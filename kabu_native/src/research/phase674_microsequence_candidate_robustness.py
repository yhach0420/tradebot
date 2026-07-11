"""Phase674 — Microsequence candidate robustness review (research only)."""

from __future__ import annotations

import csv
import json
import re
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase632_pbv2_profit_filter_counterfactual import _metrics
from research.phase634_pbv2_only_rise5_full_period import (
    _disk_usage_pct,
    _is_push_replay_session,
    _iter_events,
)
from research.phase663_price_age_freshness_analysis import CANONICAL_DAYS
from research.phase671_early_stop_feature_discovery import _load_trade_row_extended
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
from research.phase673_microsequence_third_condition import (
    BASE_BOUNCE_THR,
    BASE_FALL_THR,
    _base_combo,
    _is_big_winner,
    _is_loser,
)
from research.phase631_profit_source_attribution import _num, _parse_iso
from research.structural_trade_normalize import resolve_kabu_root

PHASE674_VERDICT_SHADOW_CANDIDATE = "SHADOW_CANDIDATE"
PHASE674_VERDICT_HOLD = "HOLD"
PHASE674_VERDICT_REJECT = "REJECT"
PHASE674_VERDICT_DATA_GAP = "DATA_GAP"
REPORT_DIR_NAME = "phase674_microsequence_candidate_robustness"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME
PHASE673_SWEEP = (
    NATIVE_ROOT / "results" / "reports" / "phase673_microsequence_third_condition" / "phase673_third_condition_sweep.csv"
)
SUPPLEMENTAL_DAYS = ("2026-07-08", "2026-07-09")


def _extend_price_index(
    idx: dict[tuple[str, str], list[tuple[datetime, float]]],
    day_isos: Sequence[str],
) -> dict[tuple[str, str], list[tuple[datetime, float]]]:
    out: dict[tuple[str, str], list[tuple[datetime, float]]] = defaultdict(list)
    for k, v in idx.items():
        out[k] = list(v)
    for day_iso in day_isos:
        day_key = _day_key(day_iso)
        day_dir = SMALL_PAPER_ROOT / day_key
        if not day_dir.is_dir():
            continue
        for sess_dir in sorted(day_dir.glob("live_session_*")):
            if not sess_dir.is_dir() or _is_push_replay_session(sess_dir):
                continue
            for e in _iter_events(sess_dir):
                sym = _sym_t(str(e.get("symbol") or ""))
                px = float(_num(e.get("current_price")) or 0)
                if px <= 0:
                    continue
                ts = _parse_iso(str(e.get("event_time") or e.get("entry_time") or ""))
                if ts is None:
                    continue
                out[(sym, day_key)].append((ts, px))
    for key in out:
        out[key].sort(key=lambda x: x[0])
    return dict(out)


def _load_trades_for_days(repo_root: Path, day_isos: Sequence[str]) -> list[dict[str, Any]]:
    days = set(day_isos)
    trades: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for day_dir in sorted(SMALL_PAPER_ROOT.iterdir()):
        if not day_dir.is_dir() or len(day_dir.name) != 8:
            continue
        day_iso = _day_iso(day_dir.name)
        if day_iso not in days:
            continue
        for sess_dir in sorted(day_dir.glob("live_session_*")):
            if not sess_dir.is_dir() or _is_push_replay_session(sess_dir):
                continue
            for t in _load_trade_row_extended(sess_dir, day_iso):
                key = (day_iso, str(t.get("session") or ""), str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
                if key in seen:
                    continue
                seen.add(key)
                row = dict(t)
                row["session_dir"] = str(sess_dir)
                row["dataset"] = "supplemental"
                trades.append(row)
    return trades


def load_all_microsequence_trades() -> list[dict[str, Any]]:
    repo_root = resolve_kabu_root(NATIVE_ROOT)
    push_root = repo_root / "data" / "push_jsonl"
    canonical = _load_canonical_trades_with_session(repo_root)
    for t in canonical:
        t["dataset"] = "canonical_22"
    supplemental = _load_trades_for_days(repo_root, SUPPLEMENTAL_DAYS)
    all_raw = canonical + supplemental
    price_idx = _build_price_index_canonical(repo_root)
    price_idx = _extend_price_index(price_idx, SUPPLEMENTAL_DAYS)
    trades = _enrich_trade_labels(all_raw, repo_root=repo_root, price_idx=price_idx)
    signal_index = _load_signal_index()
    return _attach_microsequence(
        trades,
        push_root=push_root,
        signal_index=signal_index,
        price_idx=price_idx,
    )


def _parse_third_operator(op: str) -> Callable[[Mapping[str, Any]], bool]:
    if op.endswith("==1"):
        feat = op[:-3]
        return lambda t, f=feat: bool(_num(t.get(f)) or 0) >= 0.5
    if op.endswith("==0"):
        feat = op[:-3]
        return lambda t, f=feat: bool((_num(t.get(f)) or 0) < 0.5)
    m = re.match(r"^(.+?)(<=|>=)(-?\d+(?:\.\d+)?(?:e[-+]?\d+)?)$", op)
    if not m:
        return lambda t: False
    feat, sym, thr_s = m.group(1), m.group(2), m.group(3)
    thr = float(thr_s)
    if sym == "<=":
        return lambda t, f=feat, th=thr: (_num(t.get(f)) or 1e18) <= th
    return lambda t, f=feat, th=thr: (_num(t.get(f)) or -1e18) >= thr


def _liquidity_drop(t: Mapping[str, Any]) -> bool:
    q = _num(t.get("quote_update_rate"))
    sell_p = _num(t.get("sell_pressure_proxy")) or 0
    buy_p = _num(t.get("buy_pressure_proxy")) or 0
    return (q is not None and q <= 0.5) or sell_p > buy_p


def _flat_weak(t: Mapping[str, Any]) -> bool:
    return bool(t.get("flat_weak_range_shadow_block")) or str(t.get("pretrend_shape") or "") in ("C", "D")


def _rule_a(t: Mapping[str, Any]) -> bool:
    return _base_combo(t)


def _rule_b(t: Mapping[str, Any]) -> bool:
    return _base_combo(t) and (_num(t.get("high_update_failure_count")) or 999) <= 11


def _rule_c(t: Mapping[str, Any]) -> bool:
    return _base_combo(t) and (_num(t.get("slope_5min")) or 999) <= 0.1152


def _rule_d(t: Mapping[str, Any]) -> bool:
    return _base_combo(t) and _flat_weak(t)


def _rule_e(t: Mapping[str, Any]) -> bool:
    return _base_combo(t) and (_num(t.get("last_tick_direction_ratio")) or -1) >= 0.4


def _rule_f(t: Mapping[str, Any]) -> bool:
    return _base_combo(t) and _liquidity_drop(t)


def _build_candidate_rules() -> list[tuple[str, str, Callable[[Mapping[str, Any]], bool]]]:
    rules: list[tuple[str, str, Callable[[Mapping[str, Any]], bool]]] = [
        ("A", "bounce>=0.2182 AND fall<=-0.1735", _rule_a),
        ("B", "A AND high_update_failure_count<=11", _rule_b),
        ("C", "A AND slope_5min<=0.1152", _rule_c),
        ("D", "A AND flat_weak", _rule_d),
        ("E", "A AND last_tick_direction_ratio>=0.4", _rule_e),
        ("F", "A AND liquidity_drop", _rule_f),
    ]
    if PHASE673_SWEEP.is_file():
        with PHASE673_SWEEP.open(encoding="utf-8", newline="") as fh:
            for i, row in enumerate(csv.DictReader(fh), start=1):
                if i > 15:
                    break
                op = str(row.get("third_operator") or "")
                feat = str(row.get("third_feature") or "")
                if not op:
                    continue
                third = _parse_third_operator(op)

                def _combo(t: Mapping[str, Any], th=third) -> bool:
                    return _base_combo(t) and th(t)

                rules.append((f"G{i:02d}", f"A AND {feat} {op}", _combo))
    return rules


def _max_drawdown(yens: Sequence[float]) -> float:
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for y in yens:
        eq += y
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)
    return round(max_dd, 2)


def _day_pnl(trades: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for t in trades:
        out[str(t.get("day") or "")] += float(_num(t.get("pnl_yen_100")) or 0)
    return dict(out)


def _session_kind(t: Mapping[str, Any]) -> str:
    bucket = str(t.get("session_bucket") or "")
    if bucket in ("AM", "PM"):
        return bucket
    sess = str(t.get("session") or "")
    if "122" in sess[:20]:
        return "PM"
    return "AM"


def _eval_slice(
    trades: Sequence[Mapping[str, Any]],
    *,
    rule_id: str,
    rule_label: str,
    slice_id: str,
    block_pred: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any]:
    chron = sorted(trades, key=lambda t: (str(t.get("day") or ""), str(t.get("entry_time") or ""), str(t.get("symbol") or "")))
    if not chron:
        return {
            "rule_id": rule_id,
            "rule_label": rule_label,
            "slice_id": slice_id,
            "entry_count": 0,
        }

    base_m = _metrics(list(chron))
    early_all = sum(1 for t in chron if t.get("early_stop"))
    blocked = [t for t in chron if block_pred(t)]
    kept = [t for t in chron if not block_pred(t)]
    kept_m = _metrics(kept)

    bw = sum(1 for t in blocked if _is_winner(t))
    bl = sum(1 for t in blocked if _is_loser(t))
    bbw = sum(1 for t in blocked if _is_big_winner(t))
    bes = sum(1 for t in blocked if t.get("early_stop"))

    base_day = _day_pnl(chron)
    kept_day = _day_pnl(kept)
    improved = sum(1 for d in base_day if kept_day.get(d, 0) > base_day[d])

    by_sym: dict[str, int] = defaultdict(int)
    for t in blocked:
        by_sym[str(t.get("symbol") or "")] += 1
    top_sym = max(by_sym.items(), key=lambda x: x[1]) if by_sym else ("", 0)

    overlap_fwr = sum(1 for t in blocked if t.get("flat_weak_range_shadow_block"))
    overlap_fb = sum(1 for t in blocked if t.get("flat_band_mainline_would_block"))
    pure_fwr = sum(1 for t in blocked if block_pred(t) and not t.get("flat_weak_range_shadow_block"))
    post_fb_pool = [t for t in chron if not t.get("flat_band_mainline_would_block")]
    pure_post_fb = sum(1 for t in post_fb_pool if block_pred(t))

    base_dd = _max_drawdown([float(t.get("pnl_yen_100") or 0) for t in chron])
    kept_dd = _max_drawdown([float(t.get("pnl_yen_100") or 0) for t in kept])

    return {
        "rule_id": rule_id,
        "rule_label": rule_label,
        "slice_id": slice_id,
        "entry_count": len(chron),
        "blocked_count": len(blocked),
        "blocked_winners": bw,
        "blocked_losers": bl,
        "blocked_big_winners": bbw,
        "blocked_early_stop": bes,
        "early_stop_reduction": round(bes / early_all, 4) if early_all else 0.0,
        "baseline_pnl_yen": base_m.get("pnl_yen_100"),
        "scenario_pnl_yen": kept_m.get("pnl_yen_100"),
        "delta_pnl_yen": round(float(kept_m.get("pnl_yen_100") or 0) - float(base_m.get("pnl_yen_100") or 0), 2),
        "baseline_pf": base_m.get("profit_factor"),
        "scenario_pf": kept_m.get("profit_factor"),
        "pf_delta": round(float(kept_m.get("profit_factor") or 0) - float(base_m.get("profit_factor") or 0), 4),
        "baseline_dd_yen": base_dd,
        "scenario_dd_yen": kept_dd,
        "dd_delta_yen": round(kept_dd - base_dd, 2),
        "improved_days": improved,
        "improved_days_rate": round(improved / len(base_day), 4) if base_day else 0.0,
        "top_symbol": top_sym[0],
        "top_symbol_blocked": top_sym[1],
        "top_symbol_concentration": round(top_sym[1] / len(blocked), 4) if blocked else 0.0,
        "overlap_flat_weak_range": overlap_fwr,
        "overlap_flat_band_mainline": overlap_fb,
        "pure_microsequence_blocks": pure_fwr,
        "pure_post_flat_band_blocks": pure_post_fb,
        "winner_loser_gap": bl - bw,
    }


def _slices_for_trades(trades: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    canonical = [t for t in trades if str(t.get("dataset") or "") == "canonical_22" and t.get("microsequence_ok")]
    post_fb = [t for t in canonical if t.get("post_flat_band_entry")]
    d709 = [t for t in trades if str(t.get("day") or "").startswith("2026-07-09") and t.get("microsequence_ok")]
    d708 = [t for t in trades if str(t.get("day") or "").startswith("2026-07-08") and t.get("microsequence_ok")]
    d709_am = [t for t in d709 if _session_kind(t) == "AM"]
    d709_pm = [t for t in d709 if _session_kind(t) == "PM"]
    return {
        "canonical_22_all": canonical,
        "canonical_22_post_flat_band": post_fb,
        "20260709_all": d709,
        "20260709_am": d709_am,
        "20260709_pm": d709_pm,
        "20260708_all": d708,
    }


def _overlap_rows(trades: Sequence[Mapping[str, Any]], rules: Sequence[tuple[str, str, Callable]]) -> list[dict[str, Any]]:
    ok = [t for t in trades if t.get("microsequence_ok")]
    rows: list[dict[str, Any]] = []
    for rid, label, pred in rules:
        if rid.startswith("G") and rid not in ("G01", "G02", "G03", "G04", "G05"):
            continue
        flagged = [t for t in ok if pred(t)]
        if not flagged:
            continue
        fwr = sum(1 for t in flagged if t.get("flat_weak_range_shadow_block"))
        fb = sum(1 for t in flagged if t.get("flat_band_mainline_would_block"))
        rows.append(
            {
                "rule_id": rid,
                "rule_label": label,
                "blocked_count": len(flagged),
                "overlap_flat_weak_range": fwr,
                "overlap_flat_weak_range_rate": round(fwr / len(flagged), 4),
                "overlap_flat_band_mainline": fb,
                "pure_microsequence_only": len(flagged) - fwr,
                "pure_post_flat_band": sum(1 for t in flagged if not t.get("flat_band_mainline_would_block")),
                "blocked_winners": sum(1 for t in flagged if _is_winner(t)),
                "blocked_early_stop": sum(1 for t in flagged if t.get("early_stop")),
            }
        )
    return rows


def _audit_709_rows(
    trades: Sequence[Mapping[str, Any]],
    rules: Sequence[tuple[str, str, Callable]],
) -> list[dict[str, Any]]:
    d709 = [t for t in trades if str(t.get("day") or "").startswith("2026-07-09")]
    rows: list[dict[str, Any]] = []
    for t in d709:
        row = {
            "day": t.get("day"),
            "session": t.get("session"),
            "session_kind": _session_kind(t),
            "symbol": t.get("symbol"),
            "entry_time": t.get("entry_time"),
            "pnl_yen_100": t.get("pnl_yen_100"),
            "early_stop": bool(t.get("early_stop")),
            "exit_reason": t.get("exit_reason"),
            "microsequence_ok": bool(t.get("microsequence_ok")),
            "flat_weak_range_shadow_block": bool(t.get("flat_weak_range_shadow_block")),
            "flat_band_mainline_would_block": bool(t.get("flat_band_mainline_would_block")),
            "post_flat_band_entry": bool(t.get("post_flat_band_entry")),
        }
        for rid, _, pred in rules:
            if rid in ("A", "B", "C", "D", "E", "F", "G01", "G05", "G08"):
                row[f"block_{rid}"] = bool(pred(t)) if t.get("microsequence_ok") else None
        rows.append(row)
    return rows


def _decide_verdict(
    comparison: Sequence[Mapping[str, Any]],
    *,
    audit_709: Sequence[Mapping[str, Any]],
) -> tuple[str, dict[str, Any]]:
    def _row(rid: str, sid: str) -> Optional[dict[str, Any]]:
        for r in comparison:
            if r.get("rule_id") == rid and r.get("slice_id") == sid:
                return r
        return None

    a_post = _row("A", "canonical_22_post_flat_band") or {}
    b_post = _row("B", "canonical_22_post_flat_band") or {}
    c_post = _row("C", "canonical_22_post_flat_band") or {}
    d_post = _row("D", "canonical_22_post_flat_band") or {}

    stops_709 = [t for t in audit_709 if t.get("early_stop")]
    stop_syms = {str(t.get("symbol")) for t in stops_709}
    blocked_stops_a = sum(1 for t in stops_709 if t.get("block_A"))
    blocked_stops_b = sum(1 for t in stops_709 if t.get("block_B"))
    winners_709 = [t for t in audit_709 if float(_num(t.get("pnl_yen_100")) or 0) > 0]
    blocked_wins_b = sum(1 for t in winners_709 if t.get("block_B"))

    answers = {
        "1_explains_709_stops": blocked_stops_a > 0 or blocked_stops_b > 0,
        "709_early_stop_count": len(stops_709),
        "709_blocked_early_stop_A": blocked_stops_a,
        "709_blocked_early_stop_B": blocked_stops_b,
        "709_blocked_winners_B": blocked_wins_b,
        "2_best_variant": None,
        "3_best_balance_rule": None,
        "4_overlaps_flat_weak": int(d_post.get("overlap_flat_weak_range") or 0) > 0,
        "5_pure_post_flat_band_delta_B": b_post.get("delta_pnl_yen"),
        "6_forward_shadow_worthy": False,
    }

    candidates = [
        ("A_2cond_only", a_post),
        ("B_precision", b_post),
        ("C_economics_slope", c_post),
        ("D_flat_weak", d_post),
    ]
    scored: list[tuple[str, float, Mapping[str, Any]]] = []
    for name, row in candidates:
        if not row:
            continue
        score = 0.0
        delta = float(row.get("delta_pnl_yen") or 0)
        pf = float(row.get("pf_delta") or 0)
        gap = int(row.get("winner_loser_gap") or 0)
        bbw = int(row.get("blocked_big_winners") or 0)
        esr = float(row.get("early_stop_reduction") or 0)
        pure = int(row.get("pure_post_flat_band_blocks") or 0)
        if delta > 0:
            score += min(delta / 100000, 4)
        if pf >= 0.03:
            score += 2
        score += gap * 0.05
        score += esr * 2
        score -= bbw * 0.08
        if pure > 0:
            score += 0.5
        scored.append((name, score, row))

    scored.sort(key=lambda x: x[1], reverse=True)
    if scored:
        answers["2_best_variant"] = scored[0][0]
        answers["3_best_balance_rule"] = scored[0][2].get("rule_id")

    best = scored[0][2] if scored else {}
    if (
        float(best.get("delta_pnl_yen") or 0) > 0
        and float(best.get("pf_delta") or 0) >= 0.02
        and int(best.get("pure_post_flat_band_blocks") or 0) >= 10
        and (blocked_stops_b > 0 or blocked_stops_a > 0)
    ):
        answers["6_forward_shadow_worthy"] = True
        verdict = PHASE674_VERDICT_SHADOW_CANDIDATE
    elif float(best.get("delta_pnl_yen") or 0) > 0:
        verdict = PHASE674_VERDICT_HOLD
    else:
        verdict = PHASE674_VERDICT_REJECT

    return verdict, answers


def run_audit() -> dict[str, Any]:
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    trades = load_all_microsequence_trades()
    rules = _build_candidate_rules()
    slices = _slices_for_trades(trades)

    comparison: list[dict[str, Any]] = []
    for rid, label, pred in rules:
        for sid, subset in slices.items():
            if not subset:
                continue
            comparison.append(_eval_slice(subset, rule_id=rid, rule_label=label, slice_id=sid, block_pred=pred))

    overlap = _overlap_rows(trades, rules)
    audit_709 = _audit_709_rows(trades, rules)
    verdict, answers = _decide_verdict(comparison, audit_709=audit_709)

    disk_after = _disk_usage_pct(NATIVE_ROOT)
    report: dict[str, Any] = {
        "verdict": verdict,
        "entry_count_total": len(trades),
        "canonical_count": sum(1 for t in trades if t.get("dataset") == "canonical_22"),
        "supplemental_count": sum(1 for t in trades if t.get("dataset") == "supplemental"),
        "mandatory_answers": answers,
        "candidate_rules": [{"rule_id": r[0], "rule_label": r[1]} for r in rules if not r[0].startswith("G") or r[0] <= "G05"],
        "comparison_highlights": {
            "A_post_flat_band": next((r for r in comparison if r.get("rule_id") == "A" and r.get("slice_id") == "canonical_22_post_flat_band"), {}),
            "B_post_flat_band": next((r for r in comparison if r.get("rule_id") == "B" and r.get("slice_id") == "canonical_22_post_flat_band"), {}),
            "C_post_flat_band": next((r for r in comparison if r.get("rule_id") == "C" and r.get("slice_id") == "canonical_22_post_flat_band"), {}),
            "D_post_flat_band": next((r for r in comparison if r.get("rule_id") == "D" and r.get("slice_id") == "canonical_22_post_flat_band"), {}),
            "B_20260709": next((r for r in comparison if r.get("rule_id") == "B" and r.get("slice_id") == "20260709_all"), {}),
            "A_20260709": next((r for r in comparison if r.get("rule_id") == "A" and r.get("slice_id") == "20260709_all"), {}),
        },
        "disk_usage_pct_before": disk_before,
        "disk_usage_pct_after": disk_after,
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "phase674_microsequence_candidate_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_csv(
        REPORT_ROOT / "phase674_candidate_comparison.csv",
        [
            "rule_id",
            "rule_label",
            "slice_id",
            "entry_count",
            "blocked_count",
            "blocked_winners",
            "blocked_losers",
            "blocked_big_winners",
            "blocked_early_stop",
            "early_stop_reduction",
            "delta_pnl_yen",
            "pf_delta",
            "dd_delta_yen",
            "improved_days_rate",
            "top_symbol",
            "top_symbol_concentration",
            "overlap_flat_weak_range",
            "pure_post_flat_band_blocks",
            "winner_loser_gap",
        ],
        comparison,
    )
    _write_csv(
        REPORT_ROOT / "phase674_overlap_with_flat_weak_range.csv",
        [
            "rule_id",
            "rule_label",
            "blocked_count",
            "overlap_flat_weak_range",
            "overlap_flat_weak_range_rate",
            "overlap_flat_band_mainline",
            "pure_microsequence_only",
            "pure_post_flat_band",
            "blocked_winners",
            "blocked_early_stop",
        ],
        overlap,
    )
    _write_csv(
        REPORT_ROOT / "phase674_20260709_audit.csv",
        list(audit_709[0].keys()) if audit_709 else ["day", "symbol"],
        audit_709,
    )
    _write_decision_md(report=report, comparison=comparison, audit_709=audit_709)
    return report


def _write_decision_md(
    *,
    report: Mapping[str, Any],
    comparison: Sequence[Mapping[str, Any]],
    audit_709: Sequence[Mapping[str, Any]],
) -> None:
    ans = report.get("mandatory_answers") or {}
    hi = report.get("comparison_highlights") or {}
    lines = [
        "# Phase674 — Microsequence Candidate Robustness Review",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        "",
        "## Mandatory answers",
        "",
        f"1. 7/9 STOP多発を説明できるか: {'一部可' if ans.get('1_explains_709_stops') else '不可'} "
        f"(early_stop {ans.get('709_early_stop_count')}, A捕捉 {ans.get('709_blocked_early_stop_A')}, B捕捉 {ans.get('709_blocked_early_stop_B')})",
        f"2. 最も妥当なvariant: `{ans.get('2_best_variant')}`",
        f"3. winner/early_stopバランス最良: `{ans.get('3_best_balance_rule')}`",
        f"4. Flat Weak+Rangeと重複: {'あり' if ans.get('4_overlaps_flat_weak') else '少ない'}",
        f"5. 本線後純追加効果 (B, post-FB): ΔPnL {float(hi.get('B_post_flat_band', {}).get('delta_pnl_yen') or 0):+,.0f}",
        f"6. Forward Shadow価値: {'あり' if ans.get('6_forward_shadow_worthy') else '要精査/なし'}",
        "",
        "## Post-flat-band highlights",
        "",
    ]
    for key in ("A_post_flat_band", "B_post_flat_band", "C_post_flat_band", "D_post_flat_band"):
        row = hi.get(key) or {}
        lines.append(
            f"- **{key}**: blocked={row.get('blocked_count')} W/L/BW={row.get('blocked_winners')}/"
            f"{row.get('blocked_losers')}/{row.get('blocked_big_winners')} "
            f"ΔPnL={float(row.get('delta_pnl_yen') or 0):+,.0f} pure_post_FB={row.get('pure_post_flat_band_blocks')}"
        )
    lines.extend(["", "## Constraints", "", "- Runtime/YAML/Shadow変更なし", ""])
    (REPORT_ROOT / "phase674_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    report = run_audit()
    print(json.dumps({"verdict": report.get("verdict"), "answers": report.get("mandatory_answers")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
