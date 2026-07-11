"""Phase677 — Entry readiness gate audit (research only, no runtime changes)."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase632_pbv2_profit_filter_counterfactual import _metrics
from research.phase634_pbv2_only_rise5_full_period import _disk_usage_pct, _is_push_replay_session, _iter_events
from research.phase672_pre_entry_microsequence import BIG_WINNER_YEN, SMALL_PAPER_ROOT, _day_iso, _sym_t
from research.phase675_recent_early_stop_focus import (
    RECENT_DAYS,
    _is_early_stop,
    _session_kind,
    load_focus_dataset,
)
from research.phase676_opening_coldstart_feature_incomplete import (
    _board_not_following,
    _classify_gap_reason,
    _high_bounce,
    _is_shallow_fall,
    _live_feature_incomplete,
    _low_expectancy,
)
from research.phase631_profit_source_attribution import _num, _parse_iso

PHASE677_VERDICT_READINESS_BUG = "FOUND_READINESS_BUG"
PHASE677_VERDICT_MINIMAL_GATE = "FOUND_MINIMAL_READINESS_GATE"
PHASE677_VERDICT_HOLD = "HOLD"
PHASE677_VERDICT_REJECT = "REJECT"

REPORT_DIR_NAME = "phase677_entry_readiness_gate_audit"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME

COLD_START_SEC = 120.0
CRITICAL_PRICE_FIELDS = ("r30_sec", "r60_sec", "r120_sec", "r300_sec", "r600_sec")
CRITICAL_SLOPE_FIELDS = ("slope_5min", "slope_15min")
CRITICAL_RANGE_FIELDS = ("range_5min_pct", "range_10min_pct")

CODE_PATH_SUMMARY = {
    "candidate_source": "pilot_runner._stage0_normalize_push → _candidate_trade_from_push",
    "feature_bridge": "live_feature_bridge.LiveFeatureBridge.update (min_ticks_for_complete=3)",
    "gate_evaluator": "research.exposure_gate.ExposureGate.evaluate_entry",
    "expectancy_score": "entry_expectancy_score_shadow.compute_entry_expectancy_score_fields (shadow/logging)",
    "freshness_guard": "entry_scan_controller.evaluate_entry_data_freshness (price/board age only)",
    "live_feature_complete_gate": "NONE — observability counter only, not a reject condition",
    "quality_fallback_gate": "NONE — increments quality_fallback_count only",
    "incomplete_pass_reason": (
        "Gate checks momentum_low + board_mid|high + entry_score_v2>=3 + PBv2 guards; "
        "does NOT require live_feature_complete or microsequence history"
    ),
}


def _is_winner(t: Mapping[str, Any]) -> bool:
    return float(_num(t.get("pnl_yen_100")) or 0) > 0


def _is_big_winner(t: Mapping[str, Any]) -> bool:
    return float(_num(t.get("pnl_yen_100")) or 0) >= BIG_WINNER_YEN


def _load_accept_events_full() -> dict[tuple[str, str, str, str], dict[str, Any]]:
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
                out[(day_iso, session, sym, et)] = dict(e)
    return out


def _missing_categories(t: Mapping[str, Any], acc: Mapping[str, Any]) -> list[str]:
    cats: list[str] = []
    gap = _classify_gap_reason(t)
    if gap == "price_history_insufficient":
        cats.append("price_history_insufficient")
    if gap in ("board_history_insufficient", "push_history_insufficient"):
        cats.append("board_history_insufficient")
    if gap == "opening_cold_start":
        cats.append("push_ring_cold_start")
    if not t.get("microsequence_ok"):
        cats.append("microsequence_gap")
    if _num(acc.get("entry_vwap_dev_pct")) is None and _num(t.get("entry_vwap_dev_pct")) is None:
        cats.append("vwap_missing")
    for fld in CRITICAL_PRICE_FIELDS:
        if _num(acc.get(fld) if fld in acc else t.get(fld)) is None:
            cats.append(f"{fld}_missing")
            break
    if any(_num(acc.get(f) if f in acc else t.get(f)) is None for f in CRITICAL_SLOPE_FIELDS):
        cats.append("slope_missing")
    if any(_num(acc.get(f) if f in acc else t.get(f)) is None for f in CRITICAL_RANGE_FIELDS):
        cats.append("range_missing")
    if acc.get("quality_fallback_path") is True or t.get("quality_fallback_path") is True:
        cats.append("quality_fallback")
    if acc.get("entry_board_mid_token_active") is False:
        cats.append("board_token_fallback")
    if _num(acc.get("update_count_before_entry")) in (None, 0):
        cats.append("volume_feature_fallback")
    if _live_feature_incomplete(t) or _live_feature_incomplete(acc):
        cats.append("live_feature_incomplete")
    if not acc.get("scan_id") and not t.get("scan_id"):
        cats.append("scan_audit_missing")
    if _num(acc.get("entry_expectancy_score_v2")) is not None and _num(acc.get("momentum_continuation_score")) is not None:
        mom = float(_num(acc.get("momentum_continuation_score")) or 0)
        if mom <= 0.2546 and acc.get("quality_fallback_path"):
            cats.append("expectancy_score_fallback_path")
    return sorted(set(cats))


def _price_history_sec(t: Mapping[str, Any]) -> Optional[float]:
    return _num(t.get("push_pre_entry_sec"))


def _board_history_sec(t: Mapping[str, Any]) -> Optional[float]:
    if not t.get("board_microsequence_ok"):
        return _num(t.get("push_pre_entry_sec"))
    return _num(t.get("push_pre_entry_sec"))


def _critical_price_complete(t: Mapping[str, Any], acc: Optional[Mapping[str, Any]] = None) -> bool:
    acc = acc or t
    for fld in ("r120_sec", "r60_sec", "r30_sec", "price_return_120s"):
        if _num(acc.get(fld) if fld in acc else t.get(fld)) is not None:
            return True
    return bool(t.get("microsequence_ok"))


def _critical_board_complete(t: Mapping[str, Any]) -> bool:
    return bool(t.get("board_microsequence_ok"))


def _expectancy_not_fallback(acc: Mapping[str, Any]) -> bool:
    return not bool(acc.get("quality_fallback_path")) and _num(acc.get("entry_expectancy_score_v2")) is not None


def _enrich_with_accept(trades: Sequence[Mapping[str, Any]], accept_idx: Mapping[tuple[str, str, str, str], Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in trades:
        day = str(t.get("day") or "")
        session = str(t.get("session") or "")
        sym = _sym_t(str(t.get("symbol") or ""))
        et = str(t.get("entry_time") or "")
        acc = accept_idx.get((day, session, sym, et), {})
        row = dict(t)
        for k, v in acc.items():
            if k not in row or row.get(k) in (None, ""):
                row[k] = v
        row["missing_categories"] = "|".join(_missing_categories(row, acc))
        row["critical_missing_count"] = len(_missing_categories(row, acc))
        row["price_history_sec"] = _price_history_sec(row)
        row["board_history_sec"] = _board_history_sec(row)
        out.append(row)
    return out


def _entry_decision_path_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in trades:
        acc_lfc = t.get("live_feature_complete")
        rows.append(
            {
                "day": t.get("day"),
                "session_kind": _session_kind(t),
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "pnl_yen_100": t.get("pnl_yen_100"),
                "early_stop": t.get("early_stop"),
                "exit_reason": t.get("exit_reason"),
                "candidate_source": CODE_PATH_SUMMARY["candidate_source"],
                "gate_evaluator": CODE_PATH_SUMMARY["gate_evaluator"],
                "pbv2_internal_gate": t.get("pbv2_internal_gate"),
                "pbv2_internal_reason": t.get("pbv2_internal_reason"),
                "gate_accept": t.get("gate_accept"),
                "gate_reject_reason": t.get("gate_reject_reason"),
                "live_feature_complete": acc_lfc,
                "incomplete_pass_reason": CODE_PATH_SUMMARY["incomplete_pass_reason"],
                "quality_fallback_path": t.get("quality_fallback_path"),
                "missing_categories": t.get("missing_categories"),
                "entry_expectancy_score_v2": t.get("entry_expectancy_score_v2"),
                "entry_board_mid_token_active": t.get("entry_board_mid_token_active"),
                "momentum_continuation_score": t.get("momentum_continuation_score"),
                "score_used_fallback_features": bool(t.get("quality_fallback_path")),
                "microsequence_ok": t.get("microsequence_ok"),
                "gap_reason": _classify_gap_reason(t),
                "price_history_sec": t.get("price_history_sec"),
                "board_history_sec": t.get("board_history_sec"),
                "push_pre_entry_sec": t.get("push_pre_entry_sec"),
                "scan_id": t.get("scan_id"),
                "same_scan_candidates": t.get("same_scan_candidates"),
                "entry_type": t.get("entry_type"),
            }
        )
    return rows


def _breakdown_by_category(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    pools = [
        ("canonical_22", [t for t in trades if t.get("dataset") == "canonical_22"]),
        ("recent_707_709", [t for t in trades if str(t.get("day") or "") in RECENT_DAYS]),
        ("20260707", [t for t in trades if str(t.get("day") or "").startswith("2026-07-07")]),
        ("20260708", [t for t in trades if str(t.get("day") or "").startswith("2026-07-08")]),
        ("20260709", [t for t in trades if str(t.get("day") or "").startswith("2026-07-09")]),
        ("post_flat_band", [t for t in trades if t.get("post_flat_band_entry")]),
    ]
    all_cats = (
        "price_history_insufficient",
        "board_history_insufficient",
        "vwap_missing",
        "r30_sec_missing",
        "slope_missing",
        "range_missing",
        "expectancy_score_fallback_path",
        "quality_fallback",
        "board_token_fallback",
        "volume_feature_fallback",
        "push_ring_cold_start",
        "microsequence_gap",
        "live_feature_incomplete",
        "scan_audit_missing",
    )
    rows: list[dict[str, Any]] = []
    for pool_name, pool in pools:
        for cat in all_cats:
            sub = [t for t in pool if cat in str(t.get("missing_categories") or "")]
            if not sub:
                continue
            m = _metrics(sub)
            es = sum(1 for t in sub if _is_early_stop(t))
            n = len(sub)
            rows.append(
                {
                    "pool": pool_name,
                    "missing_category": cat,
                    "entry_count": n,
                    "early_stop_count": es,
                    "early_stop_rate": round(es / n, 4) if n else None,
                    "winner_count": sum(1 for t in sub if _is_winner(t)),
                    "big_winner_count": sum(1 for t in sub if _is_big_winner(t)),
                    "pnl_yen_100": m.get("pnl_yen_100"),
                    "profit_factor": m.get("profit_factor"),
                }
            )
    return rows


def _readiness_predicates() -> list[tuple[str, Callable[[Mapping[str, Any]], bool]]]:
    return [
        ("A_price_history_sec_ge_120", lambda t: (_price_history_sec(t) or 0) >= COLD_START_SEC),
        ("B_board_history_sec_ge_120", lambda t: (_board_history_sec(t) or 0) >= COLD_START_SEC),
        ("C_push_pre_entry_sec_ge_120", lambda t: (_num(t.get("push_pre_entry_sec")) or 0) >= COLD_START_SEC),
        ("D_microsequence_ok", lambda t: bool(t.get("microsequence_ok"))),
        ("E_critical_price_features_complete", _critical_price_complete),
        ("F_critical_board_features_complete", _critical_board_complete),
        ("G_expectancy_score_not_fallback", lambda t: _expectancy_not_fallback(t)),
        ("H_live_incomplete_AND_high_bounce_045", lambda t: _live_feature_incomplete(t) and _high_bounce(t, 0.45)),
        ("I_live_incomplete_AND_expectancy_le_2.5", lambda t: _live_feature_incomplete(t) and _low_expectancy(t, 2.5)),
        ("J_price_history_insufficient_AND_expectancy_le_2.5", lambda t: _classify_gap_reason(t) == "price_history_insufficient" and _low_expectancy(t, 2.5)),
        ("K_cold_start_lt_120_AND_high_bounce", lambda t: (_price_history_sec(t) or 0) < COLD_START_SEC and _high_bounce(t, 0.45)),
        ("L_cold_start_lt_120_AND_expectancy_le_2.5", lambda t: (_price_history_sec(t) or 0) < COLD_START_SEC and _low_expectancy(t, 2.5)),
        ("M_high_bounce_board_not_following_incomplete", lambda t: _high_bounce(t, 0.45) and _board_not_following(t) and _live_feature_incomplete(t)),
        ("N_shallow_fall_high_bounce_incomplete", lambda t: _is_shallow_fall(t) and _high_bounce(t, 0.45) and _live_feature_incomplete(t)),
        ("O_minimal_readiness_price_or_microseq", lambda t: bool(t.get("microsequence_ok")) or (_price_history_sec(t) or 0) >= COLD_START_SEC),
    ]


def _counterfactual_row(
    *,
    scenario_id: str,
    pool: Sequence[Mapping[str, Any]],
    block_pred: Callable[[Mapping[str, Any]], bool],
    pool_label: str,
) -> dict[str, Any]:
    base = _metrics(list(pool))
    blocked = [t for t in pool if block_pred(t)]
    kept = [t for t in pool if not block_pred(t)]
    kept_m = _metrics(kept)
    d709_es = [t for t in pool if str(t.get("day") or "").startswith("2026-07-09") and _is_early_stop(t)]
    sym_blocked: dict[str, float] = defaultdict(float)
    for t in blocked:
        sym_blocked[str(t.get("symbol") or "")] += float(_num(t.get("pnl_yen_100")) or 0)
    top_sym = max(sym_blocked.items(), key=lambda x: abs(x[1]))[0] if sym_blocked else ""
    return {
        "scenario_id": scenario_id,
        "pool": pool_label,
        "blocked_count": len(blocked),
        "blocked_early_stop": sum(1 for t in blocked if _is_early_stop(t)),
        "blocked_winners": sum(1 for t in blocked if _is_winner(t)),
        "blocked_big_winners": sum(1 for t in blocked if _is_big_winner(t)),
        "capture_709_early_stop": sum(1 for t in d709_es if block_pred(t)),
        "delta_pnl_yen": round(float(kept_m.get("pnl_yen_100") or 0) - float(base.get("pnl_yen_100") or 0), 2),
        "pf_delta": round(float(kept_m.get("profit_factor") or 0) - float(base.get("profit_factor") or 0), 4),
        "dd_delta": round(float(kept_m.get("max_dd_yen_100") or 0) - float(base.get("max_dd_yen_100") or 0), 2),
        "top_blocked_symbol": top_sym,
    }


def _build_counterfactuals(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    pools = [
        ("canonical_22", [t for t in trades if t.get("dataset") == "canonical_22"]),
        ("recent_707_709", [t for t in trades if str(t.get("day") or "") in RECENT_DAYS]),
        ("20260707", [t for t in trades if str(t.get("day") or "").startswith("2026-07-07")]),
        ("20260708", [t for t in trades if str(t.get("day") or "").startswith("2026-07-08")]),
        ("20260709", [t for t in trades if str(t.get("day") or "").startswith("2026-07-09")]),
        ("post_flat_band", [t for t in trades if t.get("post_flat_band_entry")]),
    ]
    extra: list[tuple[str, Callable[[Mapping[str, Any]], bool]]] = [
        ("live_feature_incomplete_solo_FORBIDDEN", _live_feature_incomplete),
        ("price_history_sec_lt_120", lambda t: (_price_history_sec(t) or 0) < COLD_START_SEC),
        ("microsequence_gap", lambda t: not bool(t.get("microsequence_ok"))),
        ("critical_feature_missing_ge_3", lambda t: int(t.get("critical_missing_count") or 0) >= 3),
        ("critical_feature_missing_ge_4", lambda t: int(t.get("critical_missing_count") or 0) >= 4),
    ]
    scenarios = _readiness_predicates() + extra
    rows: list[dict[str, Any]] = []
    for sid, pred in scenarios:
        for label, pool in pools:
            rows.append(_counterfactual_row(scenario_id=sid, pool=pool, block_pred=pred, pool_label=label))
    return rows


def _709_winner_incomplete_analysis(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    d709 = [t for t in trades if str(t.get("day") or "").startswith("2026-07-09")]
    winners_inc = [t for t in d709 if _is_winner(t) and _live_feature_incomplete(t)]
    es_inc = [t for t in d709 if _is_early_stop(t) and _live_feature_incomplete(t)]
    rows: list[dict[str, Any]] = []
    for t in winners_inc:
        rows.append(
            {
                "cohort": "709_winner_incomplete",
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "pnl_yen_100": t.get("pnl_yen_100"),
                "big_winner": _is_big_winner(t),
                "gap_reason": _classify_gap_reason(t),
                "microsequence_ok": t.get("microsequence_ok"),
                "price_history_sec": t.get("price_history_sec"),
                "bounce_from_recent_low": t.get("bounce_from_recent_low"),
                "fall_from_recent_high": t.get("fall_from_recent_high"),
                "slope_5min": t.get("slope_5min"),
                "entry_expectancy_score_v2": t.get("entry_expectancy_score_v2"),
                "quality_fallback_path": t.get("quality_fallback_path"),
                "price_up_with_board_not_following": t.get("price_up_with_board_not_following"),
                "missing_categories": t.get("missing_categories"),
                "same_symbol_entry_index_day": t.get("same_symbol_entry_index_day"),
            }
        )
    if winners_inc and es_inc:
        w_bounce = statistics.mean(float(_num(t.get("bounce_from_recent_low")) or 0) for t in winners_inc if _num(t.get("bounce_from_recent_low")) is not None)
        e_bounce = statistics.mean(float(_num(t.get("bounce_from_recent_low")) or 0) for t in es_inc if _num(t.get("bounce_from_recent_low")) is not None)
        w_exp = statistics.mean(float(_num(t.get("entry_expectancy_score_v2")) or 0) for t in winners_inc if _num(t.get("entry_expectancy_score_v2")) is not None)
        e_exp = statistics.mean(float(_num(t.get("entry_expectancy_score_v2")) or 0) for t in es_inc if _num(t.get("entry_expectancy_score_v2")) is not None)
        rows.append(
            {
                "cohort": "summary_winner_vs_early_stop_incomplete",
                "winner_incomplete_count": len(winners_inc),
                "early_stop_incomplete_count": len(es_inc),
                "winner_mean_bounce": round(w_bounce, 4),
                "early_stop_mean_bounce": round(e_bounce, 4),
                "winner_mean_expectancy_v2": round(w_exp, 4),
                "early_stop_mean_expectancy_v2": round(e_exp, 4),
                "winner_big_count": sum(1 for t in winners_inc if _is_big_winner(t)),
            }
        )
    return rows


def _6525_sequence(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    sym = "6525.T"
    seq = [
        t
        for t in trades
        if str(t.get("day") or "").startswith("2026-07-09") and _sym_t(str(t.get("symbol") or "")) == sym
    ]
    seq.sort(key=lambda r: str(r.get("entry_time") or ""))
    rows: list[dict[str, Any]] = []
    for i, t in enumerate(seq):
        prev_gap = None
        if i > 0:
            et_prev = _parse_iso(seq[i - 1].get("exit_time") or seq[i - 1].get("entry_time"))
            et_cur = _parse_iso(t.get("entry_time"))
            if et_prev and et_cur:
                prev_gap = max(0.0, (et_cur - et_prev).total_seconds())
        rows.append(
            {
                "entry_index": i + 1,
                "entry_time": t.get("entry_time"),
                "exit_reason": t.get("exit_reason"),
                "pnl_yen_100": t.get("pnl_yen_100"),
                "early_stop": t.get("early_stop"),
                "live_feature_complete": t.get("live_feature_complete"),
                "microsequence_ok": t.get("microsequence_ok"),
                "gap_reason": _classify_gap_reason(t),
                "price_history_sec": t.get("price_history_sec"),
                "bounce_from_recent_low": t.get("bounce_from_recent_low"),
                "entry_expectancy_score_v2": t.get("entry_expectancy_score_v2"),
                "same_symbol_entry_index_day": t.get("same_symbol_entry_index_day"),
                "gap_sec_since_prior": prev_gap,
                "missing_categories": t.get("missing_categories"),
                "quality_fallback_path": t.get("quality_fallback_path"),
            }
        )
    return rows


def _pick_minimal_gate(cf: Sequence[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    candidates = [
        r
        for r in cf
        if r.get("pool") == "20260709"
        and int(r.get("capture_709_early_stop") or 0) >= 3
        and int(r.get("blocked_big_winners") or 0) <= 1
        and float(r.get("delta_pnl_yen") or 0) > 0
        and not str(r.get("scenario_id") or "").endswith("FORBIDDEN")
        and str(r.get("scenario_id") or "") not in ("live_feature_incomplete_solo_FORBIDDEN",)
    ]
    candidates.sort(
        key=lambda r: (
            -int(r.get("capture_709_early_stop") or 0),
            int(r.get("blocked_winners") or 0),
            -float(r.get("delta_pnl_yen") or 0),
        ),
    )
    return candidates[0] if candidates else None


def _decide_verdict(
    *,
    cf: Sequence[Mapping[str, Any]],
    breakdown: Sequence[Mapping[str, Any]],
    winner_rows: Sequence[Mapping[str, Any]],
    seq6525: Sequence[Mapping[str, Any]],
    minimal: Optional[Mapping[str, Any]],
) -> tuple[str, dict[str, Any]]:
    price_es = next(
        (r for r in breakdown if r.get("pool") == "20260709" and r.get("missing_category") == "price_history_insufficient"),
        {},
    )
    board_es = next(
        (r for r in breakdown if r.get("pool") == "20260709" and r.get("missing_category") == "board_history_insufficient"),
        {},
    )
    answers = {
        "1_code_reason_incomplete_accepted": CODE_PATH_SUMMARY["incomplete_pass_reason"],
        "1_no_live_feature_gate": CODE_PATH_SUMMARY["live_feature_complete_gate"],
        "2_critical_missing_features": "r30/r60/r120, slope, range, vwap at open; quality_fallback_path on bridge ticks<3",
        "3_fallback_in_score": "entry_expectancy_score_v2 uses momentum+board tokens; quality_fallback does not zero score but continuation_quality uses fallback weights",
        "4_price_vs_board_primary": (
            "price_history_insufficient"
            if float(price_es.get("early_stop_rate") or 0) >= float(board_es.get("early_stop_rate") or 0)
            else "board_history_insufficient"
        ),
        "709_price_history_es_rate": price_es.get("early_stop_rate"),
        "709_board_history_es_rate": board_es.get("early_stop_rate"),
        "5_minimal_readiness_gate": minimal,
        "6_can_protect_winners": minimal is not None and int((minimal or {}).get("blocked_winners") or 99) <= 8,
        "7_6525_readiness_failure": all(not bool(r.get("live_feature_complete")) for r in seq6525 if "entry_index" in r),
        "8_mainline_candidate_ready": False,
        "winner_incomplete_summary": next((r for r in winner_rows if r.get("cohort") == "summary_winner_vs_early_stop_incomplete"), {}),
    }
    if answers["1_no_live_feature_gate"] and answers["4_price_vs_board_primary"]:
        bug = True
    else:
        bug = False
    if minimal and answers["6_can_protect_winners"]:
        verdict = PHASE677_VERDICT_MINIMAL_GATE
    elif bug:
        verdict = PHASE677_VERDICT_READINESS_BUG
    elif minimal:
        verdict = PHASE677_VERDICT_HOLD
    else:
        verdict = PHASE677_VERDICT_REJECT
    return verdict, answers


def run_audit() -> dict[str, Any]:
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    trades = load_focus_dataset()
    accept_idx = _load_accept_events_full()
    trades = _enrich_with_accept(trades, accept_idx)

    path_rows = _entry_decision_path_rows(trades)
    breakdown = _breakdown_by_category(trades)
    cf = _build_counterfactuals(trades)
    winner_rows = _709_winner_incomplete_analysis(trades)
    seq6525 = _6525_sequence(trades)
    minimal = _pick_minimal_gate(cf)
    verdict, answers = _decide_verdict(
        cf=cf, breakdown=breakdown, winner_rows=winner_rows, seq6525=seq6525, minimal=minimal
    )

    d709_es = [t for t in trades if str(t.get("day") or "").startswith("2026-07-09") and _is_early_stop(t)]
    report: dict[str, Any] = {
        "verdict": verdict,
        "code_path_summary": CODE_PATH_SUMMARY,
        "mandatory_answers": answers,
        "709_early_stop_count": len(d709_es),
        "709_winner_incomplete_count": sum(
            1
            for t in trades
            if str(t.get("day") or "").startswith("2026-07-09") and _is_winner(t) and _live_feature_incomplete(t)
        ),
        "6525_entry_count": len([r for r in seq6525 if r.get("entry_index")]),
        "minimal_gate_candidate": minimal,
        "top_counterfactual_709": [r for r in cf if r.get("pool") == "20260709"][:15],
        "disk_usage_pct_before": disk_before,
        "disk_usage_pct_after": _disk_usage_pct(NATIVE_ROOT),
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "phase677_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_csv(
        REPORT_ROOT / "phase677_entry_decision_path.csv",
        list(path_rows[0].keys()) if path_rows else ["symbol"],
        path_rows,
    )
    _write_csv(
        REPORT_ROOT / "phase677_feature_missing_breakdown.csv",
        [
            "pool",
            "missing_category",
            "entry_count",
            "early_stop_count",
            "early_stop_rate",
            "winner_count",
            "big_winner_count",
            "pnl_yen_100",
            "profit_factor",
        ],
        breakdown,
    )
    _write_csv(
        REPORT_ROOT / "phase677_readiness_counterfactual.csv",
        [
            "scenario_id",
            "pool",
            "blocked_count",
            "blocked_early_stop",
            "blocked_winners",
            "blocked_big_winners",
            "capture_709_early_stop",
            "delta_pnl_yen",
            "pf_delta",
            "dd_delta",
            "top_blocked_symbol",
        ],
        cf,
    )
    _write_csv(
        REPORT_ROOT / "phase677_709_winner_incomplete_analysis.csv",
        list(winner_rows[0].keys()) if winner_rows else ["cohort"],
        winner_rows,
    )
    _write_csv(REPORT_ROOT / "phase677_6525_sequence_audit.csv", list(seq6525[0].keys()) if seq6525 else ["entry_index"], seq6525)
    _write_decision_md(report=report, seq6525=seq6525)
    return report


def _write_decision_md(*, report: Mapping[str, Any], seq6525: Sequence[Mapping[str, Any]]) -> None:
    ans = report.get("mandatory_answers") or {}
    code = report.get("code_path_summary") or {}
    lines = [
        "# Phase677 — Entry Readiness Gate Audit",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        "",
        "## コード上のENTRY path",
        "",
        f"- candidate: {code.get('candidate_source')}",
        f"- gate: {code.get('gate_evaluator')}",
        f"- live_feature_complete gate: {code.get('live_feature_complete_gate')}",
        f"- incompleteでも通過する理由: {code.get('incomplete_pass_reason')}",
        "",
        "## 必須回答",
        "",
        f"1. incompleteでもacceptされる理由: {ans.get('1_code_reason_incomplete_accepted')}",
        f"2. critical欠損feature: {ans.get('2_critical_missing_features')}",
        f"3. fallbackでscore成立: {ans.get('3_fallback_in_score')}",
        f"4. 価格 vs 板履歴主因: {ans.get('4_price_vs_board_primary')}",
        f"5. 最小readiness候補: {ans.get('5_minimal_readiness_gate')}",
        f"6. winner incompleteを守れるか: {ans.get('6_can_protect_winners')}",
        f"7. 6525はreadiness不備: {ans.get('7_6525_readiness_failure')}",
        f"8. 本線候補: {ans.get('8_mainline_candidate_ready')}",
        "",
        "## 6525 sequence",
        "",
    ]
    for r in seq6525:
        if r.get("entry_index"):
            lines.append(
                f"- #{r.get('entry_index')} {r.get('entry_time')} pnl={r.get('pnl_yen_100')} "
                f"es={r.get('early_stop')} gap={r.get('gap_reason')} exp={r.get('entry_expectancy_score_v2')}"
            )
    lines.append("")
    (REPORT_ROOT / "phase677_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    report = run_audit()
    print(json.dumps({"verdict": report.get("verdict")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
