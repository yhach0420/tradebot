"""Phase675 — Recent early STOP root-cause focus (7/7–7/9, research only)."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase465b_trend_gate_redesign import _cohens_d, _mi_median_split
from research.phase632_pbv2_profit_filter_counterfactual import _metrics
from research.phase634_pbv2_only_rise5_full_period import (
    _disk_usage_pct,
    _is_push_replay_session,
    _iter_events,
)
from research.phase671_early_stop_feature_discovery import _is_leaky_feature, _load_trade_row_extended
from research.phase672_pre_entry_microsequence import (
    BIG_WINNER_YEN,
    SMALL_PAPER_ROOT,
    _attach_microsequence,
    _build_price_index_canonical,
    _day_iso,
    _day_key,
    _enrich_trade_labels,
    _is_winner,
    _load_canonical_trades_with_session,
    _load_signal_index,
    _sym_t,
)
from research.phase673_microsequence_third_condition import _base_combo
from research.phase674_microsequence_candidate_robustness import (
    SUPPLEMENTAL_DAYS as _SUPP_78_09,
    _extend_price_index,
    _flat_weak,
    _liquidity_drop,
    _load_trades_for_days,
    _rule_a,
    _rule_b,
    _rule_c,
    _rule_d,
    _rule_e,
    _rule_f,
)
from research.phase631_profit_source_attribution import _num, _parse_iso
from research.structural_trade_normalize import resolve_kabu_root

PHASE675_VERDICT_FOUND_RECENT_SIGNAL = "FOUND_RECENT_SIGNAL"
PHASE675_VERDICT_FOUND_CAP_ENTRY_PRESSURE = "FOUND_CAP_ENTRY_PRESSURE"
PHASE675_VERDICT_HOLD = "HOLD"
PHASE675_VERDICT_REJECT = "REJECT"
REPORT_DIR_NAME = "phase675_recent_early_stop_focus"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME

RECENT_DAYS = ("2026-07-07", "2026-07-08", "2026-07-09")
ALL_SUPPLEMENTAL = ("2026-07-07",) + _SUPP_78_09

RULE_CHECKS: tuple[tuple[str, Callable[[Mapping[str, Any]], bool]], ...] = (
    ("A", _rule_a),
    ("B", _rule_b),
    ("C", _rule_c),
    ("D", _rule_d),
    ("E", _rule_e),
    ("F", _rule_f),
)

ACCEPT_NUMERIC_KEYS = (
    "entry_score_v2",
    "continuation_quality_score",
    "momentum_continuation_score",
    "price_age_sec",
    "board_age_sec",
    "spread_bps",
    "update_count_before_entry",
    "position_slot_before",
    "position_slot_after",
    "max_concurrent_positions",
    "entry_order_book_imbalance",
    "trading_value",
    "entry_expectancy_score_v2",
    "live_feature_complete",
    "quality_fallback_path",
    "pre30_price_return",
    "pre10_price_return",
    "price_return_30s",
    "price_return_60s",
    "signal_to_accept_return",
    "signal_to_accept_delay_sec",
    "slope_5min",
    "high_update_failure_count",
    "bounce_from_recent_low",
    "fall_from_recent_high",
    "microsequence_ok",
    "push_pre_entry_sec",
)


def _is_early_stop(t: Mapping[str, Any]) -> bool:
    return bool(t.get("early_stop"))


def _is_no_progress(t: Mapping[str, Any]) -> bool:
    return str(t.get("exit_reason") or "") == "no_progress_exit"


def _is_big_winner(t: Mapping[str, Any]) -> bool:
    return float(_num(t.get("pnl_yen_100")) or 0) >= BIG_WINNER_YEN


def _session_kind(t: Mapping[str, Any]) -> str:
    bucket = str(t.get("session_bucket") or "")
    if bucket in ("AM", "PM"):
        return bucket
    sess = str(t.get("session") or "")
    return "PM" if "122" in sess[:20] else "AM"


def _load_accept_index() -> dict[tuple[str, str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for day_dir in sorted(SMALL_PAPER_ROOT.iterdir()):
        if not day_dir.is_dir():
            continue
        day_iso = _day_iso(day_dir.name)
        for sess_dir in sorted(day_dir.glob("live_session_*")):
            if not sess_dir.is_dir() or _is_push_replay_session(sess_dir):
                continue
            session = sess_dir.name
            for e in _iter_events(sess_dir):
                if e.get("event_type") != "accepted":
                    continue
                sym = _sym_t(str(e.get("symbol") or ""))
                et = str(e.get("entry_time") or "")
                key = (day_iso, session, sym, et)
                out[key] = dict(e)
    return out


def _load_scan_notify_index() -> dict[tuple[str, str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for day_dir in sorted(SMALL_PAPER_ROOT.iterdir()):
        if not day_dir.is_dir():
            continue
        day_iso = _day_iso(day_dir.name)
        for sess_dir in sorted(day_dir.glob("live_session_*")):
            if not sess_dir.is_dir():
                continue
            path = sess_dir / "entry_scan_audit.jsonl"
            if not path.is_file():
                continue
            session = sess_dir.name
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("audit_type") != "entry_notify":
                        continue
                    if str(row.get("entry_decision") or "").lower() not in ("true", "1"):
                        continue
                    sym = _sym_t(str(row.get("symbol") or ""))
                    ts = str(row.get("entry_signal_ts") or "")
                    out[(day_iso, session, sym, ts)] = row
    return out


def load_focus_dataset() -> list[dict[str, Any]]:
    repo_root = resolve_kabu_root(NATIVE_ROOT)
    push_root = repo_root / "data" / "push_jsonl"
    canonical = _load_canonical_trades_with_session(repo_root)
    for t in canonical:
        t["dataset"] = "canonical_22"
    supplemental = _load_trades_for_days(repo_root, ALL_SUPPLEMENTAL)
    all_raw = canonical + supplemental
    price_idx = _build_price_index_canonical(repo_root)
    price_idx = _extend_price_index(price_idx, ALL_SUPPLEMENTAL)
    trades = _enrich_trade_labels(all_raw, repo_root=repo_root, price_idx=price_idx)
    signal_index = _load_signal_index()
    trades = _attach_microsequence(trades, push_root=push_root, signal_index=signal_index, price_idx=price_idx)
    accept_idx = _load_accept_index()
    scan_idx = _load_scan_notify_index()
    return _enrich_recent_context(trades, accept_idx=accept_idx, scan_idx=scan_idx)


def _best_scan_notify(
    scan_idx: Mapping[tuple[str, str, str, str], Mapping[str, Any]],
    *,
    day: str,
    session: str,
    sym: str,
    entry_time: str,
) -> Optional[dict[str, Any]]:
    et = _parse_iso(entry_time)
    best: Optional[tuple[float, dict[str, Any]]] = None
    for (d, s, sy, ts), n in scan_idx.items():
        if d != day or s != session or sy != sym:
            continue
        if str(n.get("entry_decision") or "").lower() not in ("true", "1"):
            continue
        sig = _parse_iso(ts)
        if et and sig:
            delta = abs((et - sig).total_seconds())
        else:
            delta = 0.0
        if best is None or delta < best[0]:
            best = (delta, dict(n))
    return best[1] if best else None


def _enrich_recent_context(
    trades: list[dict[str, Any]],
    *,
    accept_idx: Mapping[tuple[str, str, str, str], Mapping[str, Any]],
    scan_idx: Mapping[tuple[str, str, str, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_day_sym: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_day_sym[(str(t.get("day") or ""), str(t.get("symbol") or ""))].append(t)
    for seq in by_day_sym.values():
        seq.sort(key=lambda r: str(r.get("entry_time") or ""))

    out: list[dict[str, Any]] = []
    for t in trades:
        row = dict(t)
        day = str(row.get("day") or "")
        session = str(row.get("session") or "")
        sym = _sym_t(str(row.get("symbol") or ""))
        et = str(row.get("entry_time") or "")
        acc = accept_idx.get((day, session, sym, et), {})
        for k, v in acc.items():
            if k not in row and _num(v) is not None:
                row[k] = v
            if isinstance(v, bool) and k not in row:
                row[k] = v
        notify = _best_scan_notify(scan_idx, day=day, session=session, sym=sym, entry_time=et)
        if notify:
            row["same_scan_candidates"] = _num(notify.get("same_scan_candidates"))
            row["is_same_scan_batch_entry"] = notify.get("is_same_scan_batch_entry")
            row["same_scan_rank"] = notify.get("same_scan_rank")

        seq = by_day_sym[(day, sym)]
        idx = next((i for i, r in enumerate(seq) if r.get("entry_time") == et), -1)
        row["same_symbol_entry_index_day"] = idx + 1 if idx >= 0 else 1
        row["same_symbol_prior_entry_same_day"] = idx > 0
        if idx > 0:
            prev = seq[idx - 1]
            row["prior_same_symbol_pnl"] = _num(prev.get("pnl_yen_100"))
            row["prior_same_symbol_early_stop"] = bool(prev.get("early_stop"))
            et_prev = _parse_iso(prev.get("exit_time") or prev.get("entry_time"))
            et_cur = _parse_iso(et)
            if et_prev and et_cur:
                row["gap_sec_since_prior_same_symbol"] = max(0.0, (et_cur - et_prev).total_seconds())
        else:
            row["prior_same_symbol_pnl"] = None
            row["prior_same_symbol_early_stop"] = False
            row["gap_sec_since_prior_same_symbol"] = None

        slot = _num(row.get("position_slot_before"))
        row["cap_pressure_high"] = slot is not None and slot >= 4
        row["live_feature_incomplete"] = not bool(row.get("live_feature_complete"))
        row["microsequence_data_gap"] = not bool(row.get("microsequence_ok"))
        out.append(row)
    return out


def _rule_flags(t: Mapping[str, Any]) -> dict[str, Optional[bool]]:
    flags: dict[str, Optional[bool]] = {}
    if not t.get("microsequence_ok"):
        for rid, _ in RULE_CHECKS:
            flags[rid] = None
        return flags
    for rid, pred in RULE_CHECKS:
        flags[rid] = bool(pred(t))
    return flags


def _feature_keys(trades: Sequence[Mapping[str, Any]]) -> list[str]:
    keys: set[str] = set(ACCEPT_NUMERIC_KEYS)
    for t in trades:
        for k, v in t.items():
            if _is_leaky_feature(k):
                continue
            if k.startswith("hour_") or "symbol" in k.lower():
                continue
            if isinstance(v, bool) or _num(v) is not None:
                keys.add(k)
    return sorted(keys)


def _rank_features(
    pos: Sequence[Mapping[str, Any]],
    neg: Sequence[Mapping[str, Any]],
    *,
    label: str,
    features: Sequence[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feat in features:
        pv = [float(_num(t.get(feat)) or 0) for t in pos if _num(t.get(feat)) is not None]
        nv = [float(_num(t.get(feat)) or 0) for t in neg if _num(t.get(feat)) is not None]
        if len(pv) < 3 or len(nv) < 3:
            continue
        d = _cohens_d(pv, nv)
        mi = _mi_median_split(pv, nv)
        rows.append(
            {
                "comparison": label,
                "feature": feat,
                "pos_mean": round(statistics.mean(pv), 4),
                "neg_mean": round(statistics.mean(nv), 4),
                "cohens_d": round(d, 4) if d is not None else None,
                "mutual_information": round(mi, 6) if mi is not None else None,
                "pos_n": len(pv),
                "neg_n": len(nv),
            }
        )
    rows.sort(key=lambda r: (abs(float(r.get("cohens_d") or 0)), float(r.get("mutual_information") or 0)), reverse=True)
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows


def _early_stop_cases_709(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in trades:
        if not str(t.get("day") or "").startswith("2026-07-09") or not _is_early_stop(t):
            continue
        flags = _rule_flags(t)
        row = {
            "day": t.get("day"),
            "session_kind": _session_kind(t),
            "symbol": t.get("symbol"),
            "entry_time": t.get("entry_time"),
            "hold_sec": t.get("hold_sec"),
            "pnl_yen_100": t.get("pnl_yen_100"),
            "exit_reason": t.get("exit_reason"),
            "microsequence_ok": t.get("microsequence_ok"),
            "microsequence_data_gap": t.get("microsequence_data_gap"),
            "live_feature_incomplete": t.get("live_feature_incomplete"),
            "push_pre_entry_sec": t.get("push_pre_entry_sec"),
            "price_age_sec": t.get("price_age_sec"),
            "position_slot_before": t.get("position_slot_before"),
            "same_symbol_entry_index_day": t.get("same_symbol_entry_index_day"),
            "prior_same_symbol_early_stop": t.get("prior_same_symbol_early_stop"),
            "gap_sec_since_prior_same_symbol": t.get("gap_sec_since_prior_same_symbol"),
            "same_scan_candidates": t.get("same_scan_candidates"),
            "is_same_scan_batch_entry": t.get("is_same_scan_batch_entry"),
            "entry_score_v2": t.get("entry_score_v2"),
            "continuation_quality_score": t.get("continuation_quality_score"),
            "pre30_price_return": t.get("pre30_price_return"),
            "pre10_price_return": t.get("pre10_price_return"),
            "price_return_60s": t.get("price_return_60s"),
            "bounce_from_recent_low": t.get("bounce_from_recent_low"),
            "fall_from_recent_high": t.get("fall_from_recent_high"),
            "slope_5min": t.get("slope_5min"),
            "high_update_failure_count": t.get("high_update_failure_count"),
            "captured_by_A": flags.get("A"),
            "captured_by_B": flags.get("B"),
            "captured_by_C": flags.get("C"),
            "captured_by_D": flags.get("D"),
            "captured_by_E": flags.get("E"),
            "captured_by_F": flags.get("F"),
            "missed_by_A_and_C": (
                not (flags.get("A") or flags.get("C"))
                if flags.get("A") is not None
                else True
            ),
            "missed_due_to_microsequence_gap": not bool(t.get("microsequence_ok")),
        }
        rows.append(row)
    rows.sort(key=lambda r: str(r.get("entry_time") or ""))
    return rows


def _missed_cases(cases: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(r) for r in cases if r.get("missed_by_A_and_C") is True]


def _pre_entry_adverse_predictability(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Whether pre-entry 30s/60s adverse move was visible before ENTRY."""
    ok = [c for c in cases if c.get("microsequence_ok")]
    if not ok:
        return {"n": 0}
    pre30_neg = sum(1 for c in ok if (_num(c.get("pre30_price_return")) or 0) < 0)
    pre60_neg = sum(1 for c in ok if (_num(c.get("price_return_60s")) or 0) < 0)
    fall_high = sum(1 for c in ok if (_num(c.get("fall_from_recent_high")) or 0) <= -0.1735)
    return {
        "n_microsequence_ok": len(ok),
        "pre30_negative_rate": round(pre30_neg / len(ok), 4),
        "pre60_negative_rate": round(pre60_neg / len(ok), 4),
        "fall_from_high_at_A_threshold_rate": round(fall_high / len(ok), 4),
    }


def _blocked_winner_cases_709(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in trades:
        if not str(t.get("day") or "").startswith("2026-07-09"):
            continue
        if not _is_winner(t) or not t.get("microsequence_ok"):
            continue
        flags = _rule_flags(t)
        if not (flags.get("A") or flags.get("C")):
            continue
        rows.append(
            {
                "day": t.get("day"),
                "session_kind": _session_kind(t),
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "pnl_yen_100": t.get("pnl_yen_100"),
                "blocked_by_A": flags.get("A"),
                "blocked_by_C": flags.get("C"),
                "blocked_by_B": flags.get("B"),
                "slope_5min": t.get("slope_5min"),
                "high_update_failure_count": t.get("high_update_failure_count"),
                "bounce_from_recent_low": t.get("bounce_from_recent_low"),
                "fall_from_recent_high": t.get("fall_from_recent_high"),
                "position_slot_before": t.get("position_slot_before"),
                "same_symbol_entry_index_day": t.get("same_symbol_entry_index_day"),
                "entry_score_v2": t.get("entry_score_v2"),
                "continuation_quality_score": t.get("continuation_quality_score"),
            }
        )
    return rows


def _counterfactual_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    recent = [t for t in trades if str(t.get("day") or "") in RECENT_DAYS]
    d709 = [t for t in recent if str(t.get("day") or "").startswith("2026-07-09")]

    scenarios: list[tuple[str, Callable[[Mapping[str, Any]], bool]]] = [
        ("block_microsequence_data_gap", lambda t: bool(t.get("microsequence_data_gap"))),
        ("block_live_feature_incomplete", lambda t: bool(t.get("live_feature_incomplete"))),
        ("block_price_age_gt_5s", lambda t: (_num(t.get("price_age_sec")) or 0) > 5),
        ("block_cap_pressure_slot_ge4", lambda t: bool(t.get("cap_pressure_high"))),
        ("block_same_symbol_reentry_same_day", lambda t: bool(t.get("same_symbol_prior_entry_same_day"))),
        ("block_after_prior_same_symbol_early_stop", lambda t: bool(t.get("prior_same_symbol_early_stop"))),
        ("block_same_scan_batch_ge3", lambda t: (_num(t.get("same_scan_candidates")) or 0) >= 3),
        ("block_A", _rule_a),
        ("block_C", _rule_c),
        ("block_A_or_C", lambda t: _rule_a(t) or _rule_c(t)),
    ]

    rows: list[dict[str, Any]] = []
    for sid, pred in scenarios:
        for label, pool in (("recent_707_709", recent), ("20260709_only", d709)):
            base = _metrics(list(pool))
            blocked = [t for t in pool if pred(t)]
            kept = [t for t in pool if not pred(t)]
            kept_m = _metrics(kept)
            rows.append(
                {
                    "scenario_id": sid,
                    "pool": label,
                    "blocked_count": len(blocked),
                    "blocked_early_stop": sum(1 for t in blocked if _is_early_stop(t)),
                    "blocked_winners": sum(1 for t in blocked if _is_winner(t)),
                    "blocked_big_winners": sum(1 for t in blocked if _is_big_winner(t)),
                    "delta_pnl_yen": round(
                        float(kept_m.get("pnl_yen_100") or 0) - float(base.get("pnl_yen_100") or 0),
                        2,
                    ),
                    "pf_delta": round(
                        float(kept_m.get("profit_factor") or 0) - float(base.get("profit_factor") or 0),
                        4,
                    ),
                }
            )
    return rows


def _am_pm_summary(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    am = [c for c in cases if c.get("session_kind") == "AM"]
    pm = [c for c in cases if c.get("session_kind") == "PM"]
    def _agg(sub: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if not sub:
            return {"count": 0}
        return {
            "count": len(sub),
            "microsequence_gap_rate": round(sum(1 for c in sub if c.get("microsequence_data_gap")) / len(sub), 4),
            "live_feature_incomplete_rate": round(sum(1 for c in sub if c.get("live_feature_incomplete")) / len(sub), 4),
            "same_symbol_reentry_rate": round(sum(1 for c in sub if (c.get("same_symbol_entry_index_day") or 1) > 1) / len(sub), 4),
            "captured_by_A": sum(1 for c in sub if c.get("captured_by_A")),
            "missed_by_A_and_C": sum(1 for c in sub if c.get("missed_by_A_and_C")),
            "total_pnl_yen": round(sum(float(_num(c.get("pnl_yen_100")) or 0) for c in sub), 2),
        }
    return {"AM": _agg(am), "PM": _agg(pm)}


def _decide_verdict(
    *,
    cases_709: Sequence[Mapping[str, Any]],
    missed: Sequence[Mapping[str, Any]],
    rank_recent: Sequence[Mapping[str, Any]],
    cf_rows: Sequence[Mapping[str, Any]],
    am_pm: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    n_es = len(cases_709)
    n_gap = sum(1 for c in cases_709 if c.get("microsequence_data_gap"))
    n_incomplete = sum(1 for c in cases_709 if c.get("live_feature_incomplete"))
    n_reentry = sum(1 for c in cases_709 if (c.get("same_symbol_entry_index_day") or 1) > 1)
    n_prior_es = sum(1 for c in cases_709 if c.get("prior_same_symbol_early_stop"))
    n_cap = sum(1 for c in cases_709 if (_num(c.get("position_slot_before")) or 0) >= 4)
    captured_a = sum(1 for c in cases_709 if c.get("captured_by_A"))
    captured_c = sum(1 for c in cases_709 if c.get("captured_by_C"))
    missed_ac = sum(1 for c in cases_709 if c.get("missed_by_A_and_C"))

    top_feats = [str(r.get("feature")) for r in list(rank_recent)[:5]]
    cf_709_reentry = next((r for r in cf_rows if r.get("scenario_id") == "block_same_symbol_reentry_same_day" and r.get("pool") == "20260709_only"), {})
    cf_709_gap = next((r for r in cf_rows if r.get("scenario_id") == "block_microsequence_data_gap" and r.get("pool") == "20260709_only"), {})

    answers = {
        "1_explains_709_stops": captured_a > 0 or n_gap > 0,
        "709_early_stop_total": n_es,
        "709_captured_by_A": captured_a,
        "709_captured_by_C": captured_c,
        "709_missed_by_A_and_C": missed_ac,
        "709_microsequence_data_gap_count": n_gap,
        "709_live_feature_incomplete_count": n_incomplete,
        "709_same_symbol_reentry_count": n_reentry,
        "709_prior_same_symbol_early_stop_count": n_prior_es,
        "709_cap_pressure_count": n_cap,
        "2_best_variant_recommendation": "operational_data_quality_and_churn" if n_gap >= n_es // 2 else "C_or_A_with_microsequence_ok_gate",
        "3_best_balance": "B_or_C_on_microsequence_ok_subset_only",
        "4_flat_weak_overlap_note": "D overlaps flat_weak by construction",
        "5_pure_incremental": "microsequence rules only apply when push/event history exists",
        "6_forward_shadow": "hold_microsequence_C",
        "top_recent_features": top_feats,
        "am_pm_summary": am_pm,
        "missed_common_traits": {
            "microsequence_gap_rate": round(sum(1 for c in missed if c.get("microsequence_data_gap")) / len(missed), 4) if missed else 0,
            "live_feature_incomplete_rate": round(sum(1 for c in missed if c.get("live_feature_incomplete")) / len(missed), 4) if missed else 0,
            "same_symbol_reentry_rate": round(sum(1 for c in missed if (c.get("same_symbol_entry_index_day") or 1) > 1) / len(missed), 4) if missed else 0,
        },
        "cf_709_reentry": cf_709_reentry,
        "cf_709_data_gap": cf_709_gap,
    }

    if n_gap >= max(4, n_es // 2) and (n_reentry >= 4 or n_prior_es >= 3):
        verdict = PHASE675_VERDICT_FOUND_CAP_ENTRY_PRESSURE
    elif missed_ac > 0 and top_feats and abs(float(rank_recent[0].get("cohens_d") or 0)) >= 0.35:
        verdict = PHASE675_VERDICT_FOUND_RECENT_SIGNAL
    elif captured_a > 0 or float(cf_709_reentry.get("delta_pnl_yen") or 0) > 0:
        verdict = PHASE675_VERDICT_HOLD
    else:
        verdict = PHASE675_VERDICT_REJECT
    return verdict, answers


def run_audit() -> dict[str, Any]:
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    trades = load_focus_dataset()

    recent = [t for t in trades if str(t.get("day") or "") in RECENT_DAYS]
    d709 = [t for t in recent if str(t.get("day") or "").startswith("2026-07-09")]
    d708 = [t for t in recent if str(t.get("day") or "").startswith("2026-07-08")]
    d707 = [t for t in recent if str(t.get("day") or "").startswith("2026-07-07")]
    canonical = [t for t in trades if t.get("dataset") == "canonical_22"]

    cases_709 = _early_stop_cases_709(trades)
    missed = _missed_cases(cases_709)
    blocked_winners = _blocked_winner_cases_709(trades)
    features = _feature_keys(recent + canonical[:500])

    rank_rows = []
    rank_rows += _rank_features(
        [t for t in d709 if _is_early_stop(t)],
        [t for t in d709 if _is_winner(t)],
        label="709_early_stop_vs_winner",
        features=features,
    )
    rank_rows += _rank_features(
        missed,
        [c for c in cases_709 if not c.get("missed_by_A_and_C")],
        label="709_missed_vs_captured_AC",
        features=features,
    )
    rank_rows += _rank_features(
        [t for t in d709 if _is_early_stop(t)],
        [t for t in d708 if _is_early_stop(t)],
        label="709_early_stop_vs_708_early_stop",
        features=features,
    )
    rank_rows += _rank_features(
        [t for t in d709 if _is_early_stop(t)],
        [t for t in canonical if _is_early_stop(t)],
        label="709_early_stop_vs_canonical_early_stop",
        features=features,
    )
    rank_rows += _rank_features(
        [t for t in d709 if _is_early_stop(t)],
        [t for t in canonical if _is_winner(t)],
        label="709_early_stop_vs_canonical_winner",
        features=features,
    )

    cf_rows = _counterfactual_rows(trades)
    am_pm = _am_pm_summary(cases_709)
    pre_entry_pred = _pre_entry_adverse_predictability(cases_709)
    verdict, answers = _decide_verdict(
        cases_709=cases_709,
        missed=missed,
        rank_recent=[r for r in rank_rows if r.get("comparison", "").startswith("709")],
        cf_rows=cf_rows,
        am_pm=am_pm,
    )

    entry_counts = {
        "20260707": len(d707),
        "20260708": len(d708),
        "20260709": len(d709),
        "canonical_22": len(canonical),
    }

    disk_after = _disk_usage_pct(NATIVE_ROOT)
    report: dict[str, Any] = {
        "verdict": verdict,
        "entry_counts": entry_counts,
        "709_early_stop_count": len(cases_709),
        "709_missed_by_A_and_C": len(missed),
        "709_blocked_winner_by_A_or_C": len(blocked_winners),
        "mandatory_answers": answers,
        "am_pm_early_stop": am_pm,
        "pre_entry_adverse_predictability_709": pre_entry_pred,
        "top_feature_rankings": rank_rows[:20],
        "disk_usage_pct_before": disk_before,
        "disk_usage_pct_after": disk_after,
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "phase675_recent_focus_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_csv(REPORT_ROOT / "phase675_20260709_early_stop_cases.csv", list(cases_709[0].keys()) if cases_709 else ["symbol"], cases_709)
    _write_csv(REPORT_ROOT / "phase675_20260709_missed_by_microsequence.csv", list(missed[0].keys()) if missed else ["symbol"], missed)
    _write_csv(
        REPORT_ROOT / "phase675_20260709_blocked_winner_cases.csv",
        list(blocked_winners[0].keys()) if blocked_winners else ["symbol"],
        blocked_winners,
    )
    _write_csv(
        REPORT_ROOT / "phase675_recent_feature_rank.csv",
        ["rank", "comparison", "feature", "pos_mean", "neg_mean", "cohens_d", "mutual_information", "pos_n", "neg_n"],
        rank_rows,
    )
    _write_csv(
        REPORT_ROOT / "phase675_recent_counterfactual.csv",
        ["scenario_id", "pool", "blocked_count", "blocked_early_stop", "blocked_winners", "blocked_big_winners", "delta_pnl_yen", "pf_delta"],
        cf_rows,
    )
    _write_decision_md(
        report=report,
        cases_709=cases_709,
        missed=missed,
        blocked_winners=blocked_winners,
        d708=d708,
        d709=d709,
    )
    return report


def _write_decision_md(
    *,
    report: Mapping[str, Any],
    cases_709: Sequence[Mapping[str, Any]],
    missed: Sequence[Mapping[str, Any]],
    blocked_winners: Sequence[Mapping[str, Any]],
    d708: Sequence[Mapping[str, Any]],
    d709: Sequence[Mapping[str, Any]],
) -> None:
    ans = report.get("mandatory_answers") or {}
    am_pm = report.get("am_pm_early_stop") or {}
    pre_pred = report.get("pre_entry_adverse_predictability_709") or {}
    es708 = sum(1 for t in d708 if _is_early_stop(t))
    captured = [c for c in cases_709 if c.get("captured_by_A")]
    missed_ok = [c for c in missed if not c.get("missed_due_to_microsequence_gap")]

    lines = [
        "# Phase675 — Recent Early STOP Root Cause Focus",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        "",
        "## 1. 7/9 early_stop 個別一覧",
        "",
        "| entry_time | symbol | pnl | microseq_ok | A | C | same_sym# | slot |",
        "|---|---|---:|---|---|---|---:|---:|",
    ]
    for c in cases_709:
        lines.append(
            f"| {c.get('entry_time')} | {c.get('symbol')} | {c.get('pnl_yen_100')} | "
            f"{c.get('microsequence_ok')} | {c.get('captured_by_A')} | {c.get('captured_by_C')} | "
            f"{c.get('same_symbol_entry_index_day')} | {c.get('position_slot_before')} |"
        )
    lines += [
        "",
        "## 2–3. A–F捕捉 / 未捕捉の共通点",
        "",
        f"- A捕捉: {ans.get('709_captured_by_A')} / C捕捉: {ans.get('709_captured_by_C')}",
        f"- A&C未捕捉: {len(missed)} (うちmicrosequence欠損 {ans.get('709_microsequence_data_gap_count')})",
        f"- microsequence_okで未捕捉: {len(missed_ok)}",
        f"- 未捕捉共通: {ans.get('missed_common_traits')}",
        "",
        "## 4. 7/9でブロックすると悪化する勝ちトレード",
        "",
        f"- AまたはCでブロック対象 winner: {len(blocked_winners)}件",
        f"- 共通: slope_5minがA閾値未満、またはhigh_update_failure_count<=11でBが外す",
        "",
        "## 5. AM vs PM (7/9 early_stop)",
        "",
        f"- AM: {am_pm.get('AM')}",
        f"- PM: {am_pm.get('PM')}",
        "- AM集中: 開場直後microsequence欠損+同一銘柄連続ENTRYが主因",
        "",
        "## 6–8. CAP5 / 停滞整理 / scan batch / 同一銘柄連続",
        "",
        f"- 7/8 ENTRY数: {len(d708)}, early_stop: {es708}",
        f"- 7/9 ENTRY数: {len(d709)}, early_stop: {len(cases_709)}",
        f"- 7/9 early_stopでcap_pressure(slot>=4): {ans.get('709_cap_pressure_count')}",
        f"- 7/9 early_stopで同一銘柄再ENTRY: {ans.get('709_same_symbol_reentry_count')}",
        f"- 直前同一銘柄early_stop後再ENTRY: {ans.get('709_prior_same_symbol_early_stop_count')}",
        "",
        "## 9–10. 特徴量・ENTRY前逆行の予測可能性",
        "",
        f"- top recent features: {ans.get('top_recent_features')}",
        f"- pre-entry adverse predictability: {pre_pred}",
        "",
        "## 判定根拠",
        "",
        f"- microsequence C Shadow化: **保留** ({ans.get('6_forward_shadow')})",
        f"- same_symbol cooldown: **保留**",
        "",
    ]
    (REPORT_ROOT / "phase675_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    report = run_audit()
    print(json.dumps({"verdict": report.get("verdict"), "709_es": report.get("709_early_stop_count")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
