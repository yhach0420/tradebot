"""Phase676 — Opening cold-start / live feature incomplete entry audit (research only)."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase632_pbv2_profit_filter_counterfactual import _metrics
from research.phase634_pbv2_only_rise5_full_period import _disk_usage_pct
from research.phase672_pre_entry_microsequence import BIG_WINNER_YEN
from research.phase674_microsequence_candidate_robustness import _rule_a, _rule_c
from research.phase675_recent_early_stop_focus import (
    RECENT_DAYS,
    _is_early_stop,
    _is_winner,
    _session_kind,
    load_focus_dataset,
)
from research.phase631_profit_source_attribution import _num, _parse_iso

PHASE676_VERDICT_DATA_COMPLETENESS = "FOUND_DATA_COMPLETENESS_SIGNAL"
PHASE676_VERDICT_SHALLOW_BOUNCE = "FOUND_SHALLOW_BOUNCE_SIGNAL"
PHASE676_VERDICT_OPENING_COLDSTART = "FOUND_OPENING_COLDSTART_SIGNAL"
PHASE676_VERDICT_HOLD = "HOLD"
PHASE676_VERDICT_REJECT = "REJECT"

REPORT_DIR_NAME = "phase676_opening_coldstart_feature_incomplete"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME

SHALLOW_FALL_MAX = -0.1735
BOUNCE_THRESHOLDS = (0.35, 0.45, 0.55, 0.65)
SLOPE5_THRESHOLDS = (0.0, 0.05, 0.1152)
SLOPE15_THRESHOLDS = (0.0, 0.05, 0.10)
EXPECTANCY_THRESHOLDS = (2.5, 2.75, 3.0)
COLD_START_SEC = 120.0
SCAN_BATCH_N = (3, 4, 5)


def _is_big_winner(t: Mapping[str, Any]) -> bool:
    return float(_num(t.get("pnl_yen_100")) or 0) >= BIG_WINNER_YEN


def _is_stop_hit(t: Mapping[str, Any]) -> bool:
    return str(t.get("exit_reason") or "") == "stop_hit"


def _live_feature_complete(t: Mapping[str, Any]) -> bool:
    return bool(t.get("live_feature_complete"))


def _live_feature_incomplete(t: Mapping[str, Any]) -> bool:
    return not _live_feature_complete(t)


def _classify_gap_reason(t: Mapping[str, Any]) -> str:
    if t.get("microsequence_ok"):
        return "ok"
    et = _parse_iso(t.get("entry_time"))
    if et is None:
        return "entry_time_mismatch"
    price_src = str(t.get("price_history_source") or "")
    board_src = str(t.get("board_history_source") or "")
    push_sec = float(_num(t.get("push_pre_entry_sec")) or 0)
    price_pts = int(_num(t.get("pre_price_points_120s")) or 0)
    board_ok = bool(t.get("board_microsequence_ok"))
    if price_src == "none" or price_pts < 3:
        return "price_history_insufficient"
    if board_src == "none" or not board_ok:
        if push_sec < 10:
            return "push_history_insufficient"
        return "board_history_insufficient"
    if push_sec < COLD_START_SEC:
        return "opening_cold_start"
    return "other_gap"


def _cold_start_history_sec(t: Mapping[str, Any]) -> Optional[float]:
    v = _num(t.get("push_pre_entry_sec"))
    return v


def _is_opening_cold_start(t: Mapping[str, Any]) -> bool:
    if t.get("microsequence_ok"):
        return False
    reason = _classify_gap_reason(t)
    return reason in ("opening_cold_start", "push_history_insufficient", "price_history_insufficient")


def _is_shallow_fall(t: Mapping[str, Any]) -> bool:
    fall = _num(t.get("fall_from_recent_high"))
    return fall is not None and fall > SHALLOW_FALL_MAX


def _high_bounce(t: Mapping[str, Any], thr: float) -> bool:
    b = _num(t.get("bounce_from_recent_low"))
    return b is not None and b >= thr


def _slope_up(t: Mapping[str, Any], *, field: str, thr: float) -> bool:
    s = _num(t.get(field))
    return s is not None and s > thr


def _board_not_following(t: Mapping[str, Any]) -> bool:
    v = _num(t.get("price_up_with_board_not_following"))
    return v is not None and v >= 0.5


def _low_expectancy(t: Mapping[str, Any], thr: float) -> bool:
    s = _num(t.get("entry_expectancy_score_v2"))
    return s is not None and s <= thr


def _missed_by_a_and_c(t: Mapping[str, Any]) -> bool:
    if not t.get("microsequence_ok"):
        return True
    return not (_rule_a(t) or _rule_c(t))


def _group_metrics(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    m = _metrics(list(trades))
    es = sum(1 for t in trades if _is_early_stop(t))
    sh = sum(1 for t in trades if _is_stop_hit(t))
    bw = sum(1 for t in trades if _is_big_winner(t))
    n = len(trades)
    return {
        **m,
        "stop_hit_count": sh,
        "early_stop_count": es,
        "early_stop_rate": round(es / n, 4) if n else None,
        "winner_count": sum(1 for t in trades if _is_winner(t)),
        "loser_count": sum(1 for t in trades if float(_num(t.get("pnl_yen_100")) or 0) < 0),
        "big_winner_count": bw,
    }


def _audit_live_feature_complete(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pools: list[tuple[str, Sequence[Mapping[str, Any]]]] = [
        ("canonical_22", [t for t in trades if t.get("dataset") == "canonical_22"]),
        ("recent_707_709", [t for t in trades if str(t.get("day") or "") in RECENT_DAYS]),
        ("20260707", [t for t in trades if str(t.get("day") or "").startswith("2026-07-07")]),
        ("20260708", [t for t in trades if str(t.get("day") or "").startswith("2026-07-08")]),
        ("20260709", [t for t in trades if str(t.get("day") or "").startswith("2026-07-09")]),
    ]
    for pool_name, pool in pools:
        for flag in (True, False):
            sub = [t for t in pool if (_live_feature_complete(t) if flag else _live_feature_incomplete(t))]
            if not sub:
                continue
            for sk in ("ALL", "AM", "PM"):
                sub2 = sub if sk == "ALL" else [t for t in sub if _session_kind(t) == sk]
                if not sub2:
                    continue
                gm = _group_metrics(sub2)
                rows.append(
                    {
                        "pool": pool_name,
                        "session_kind": sk,
                        "live_feature_complete": flag,
                        **gm,
                    }
                )
    canon = [t for t in trades if t.get("dataset") == "canonical_22"]
    day_map = {
        "20260707": "2026-07-07",
        "20260708": "2026-07-08",
        "20260709": "2026-07-09",
    }
    for pool_name, day_prefix in day_map.items():
        pool = [t for t in trades if str(t.get("day") or "").startswith(day_prefix)]
        for flag in (True, False):
            sub = [t for t in pool if (_live_feature_complete(t) if flag else _live_feature_incomplete(t))]
            if not sub:
                continue
            es_rate = _group_metrics(sub).get("early_stop_rate")
            canon_sub = [t for t in canon if _live_feature_complete(t) is flag]
            canon_rate = _group_metrics(canon_sub).get("early_stop_rate") if canon_sub else None
            rows.append(
                {
                    "pool": pool_name,
                    "session_kind": "delta_vs_canonical",
                    "live_feature_complete": flag,
                    "early_stop_rate": es_rate,
                    "canonical_early_stop_rate": canon_rate,
                    "early_stop_rate_delta": (
                        round(float(es_rate or 0) - float(canon_rate or 0), 4)
                        if es_rate is not None and canon_rate is not None
                        else None
                    ),
                }
            )
    return rows


def _audit_microsequence_gap(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    reasons = (
        "ok",
        "price_history_insufficient",
        "board_history_insufficient",
        "push_history_insufficient",
        "opening_cold_start",
        "entry_time_mismatch",
        "other_gap",
    )
    pools = [
        ("canonical_22", [t for t in trades if t.get("dataset") == "canonical_22"]),
        ("recent_707_709", [t for t in trades if str(t.get("day") or "") in RECENT_DAYS]),
        ("20260709", [t for t in trades if str(t.get("day") or "").startswith("2026-07-09")]),
    ]
    for pool_name, pool in pools:
        for reason in reasons:
            sub = [t for t in pool if _classify_gap_reason(t) == reason]
            if not sub:
                continue
            gm = _group_metrics(sub)
            rows.append({"pool": pool_name, "gap_reason": reason, **gm})
    return rows


def _709_missed_cases(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in trades:
        if not str(t.get("day") or "").startswith("2026-07-09"):
            continue
        if not _is_early_stop(t):
            continue
        if not _missed_by_a_and_c(t):
            continue
        rows.append(
            {
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "session_kind": _session_kind(t),
                "pnl_yen_100": t.get("pnl_yen_100"),
                "microsequence_ok": t.get("microsequence_ok"),
                "gap_reason": _classify_gap_reason(t),
                "live_feature_complete": t.get("live_feature_complete"),
                "bounce_from_recent_low": t.get("bounce_from_recent_low"),
                "fall_from_recent_high": t.get("fall_from_recent_high"),
                "slope_5min": t.get("slope_5min"),
                "slope_15min": t.get("slope_15min"),
                "range_5min_pct": t.get("range_5min_pct"),
                "range_10min_pct": t.get("range_10min_pct"),
                "high_update_5min": t.get("high_update_5min"),
                "volume_ratio_10min": t.get("volume_ratio_10min"),
                "price_up_with_board_not_following": t.get("price_up_with_board_not_following"),
                "entry_expectancy_score_v2": t.get("entry_expectancy_score_v2"),
                "momentum_continuation": t.get("momentum_continuation"),
                "entry_vwap_dev_pct": t.get("entry_vwap_dev_pct"),
                "pre30_price_return": t.get("pre30_price_return"),
                "price_return_60s": t.get("price_return_60s"),
                "same_scan_candidates": t.get("same_scan_candidates"),
                "same_symbol_entry_index_day": t.get("same_symbol_entry_index_day"),
                "push_pre_entry_sec": t.get("push_pre_entry_sec"),
                "shallow_fall": _is_shallow_fall(t),
                "high_bounce_045": _high_bounce(t, 0.45),
                "slope5_positive": _slope_up(t, field="slope_5min", thr=0.0),
            }
        )
    rows.sort(key=lambda r: str(r.get("entry_time") or ""))
    return rows


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
    daily_base = defaultdict(float)
    daily_kept = defaultdict(float)
    for t in pool:
        daily_base[str(t.get("day") or "")] += float(_num(t.get("pnl_yen_100")) or 0)
    for t in kept:
        daily_kept[str(t.get("day") or "")] += float(_num(t.get("pnl_yen_100")) or 0)
    improved = sum(1 for d in daily_base if daily_kept.get(d, 0) > daily_base[d])
    sym_blocked = defaultdict(float)
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
        "delta_pnl_yen": round(float(kept_m.get("pnl_yen_100") or 0) - float(base.get("pnl_yen_100") or 0), 2),
        "pf_delta": round(float(kept_m.get("profit_factor") or 0) - float(base.get("profit_factor") or 0), 4),
        "dd_delta": round(float(kept_m.get("max_dd_yen_100") or 0) - float(base.get("max_dd_yen_100") or 0), 2),
        "improved_days": improved,
        "top_blocked_symbol": top_sym,
    }


def _build_counterfactuals(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    pools = [
        ("canonical_22", [t for t in trades if t.get("dataset") == "canonical_22"]),
        ("recent_707_709", [t for t in trades if str(t.get("day") or "") in RECENT_DAYS]),
        ("20260709", [t for t in trades if str(t.get("day") or "").startswith("2026-07-09")]),
        ("post_flat_band", [t for t in trades if t.get("post_flat_band_entry")]),
    ]
    scenarios: list[tuple[str, Callable[[Mapping[str, Any]], bool]]] = [
        ("live_feature_incomplete_reject", _live_feature_incomplete),
        ("microsequence_gap_reject", lambda t: not bool(t.get("microsequence_ok"))),
        (
            "live_incomplete_AND_high_bounce_045",
            lambda t: _live_feature_incomplete(t) and _high_bounce(t, 0.45),
        ),
        (
            "live_incomplete_AND_expectancy_le_2.5",
            lambda t: _live_feature_incomplete(t) and _low_expectancy(t, 2.5),
        ),
        (
            "shallow_fall_high_bounce_045",
            lambda t: _is_shallow_fall(t) and _high_bounce(t, 0.45) and bool(t.get("microsequence_ok")),
        ),
        (
            "shallow_fall_high_bounce_AND_board_not_following",
            lambda t: _is_shallow_fall(t) and _high_bounce(t, 0.45) and _board_not_following(t),
        ),
        (
            "shallow_fall_high_bounce_AND_expectancy_le_2.5",
            lambda t: _is_shallow_fall(t) and _high_bounce(t, 0.45) and _low_expectancy(t, 2.5),
        ),
        (
            "same_scan_ge3_AND_live_incomplete",
            lambda t: (_num(t.get("same_scan_candidates")) or 0) >= 3 and _live_feature_incomplete(t),
        ),
        ("cold_start_history_lt_120", lambda t: (_cold_start_history_sec(t) or 999) < COLD_START_SEC and not bool(t.get("microsequence_ok"))),
        (
            "cold_start_lt_120_AND_expectancy_le_2.5",
            lambda t: (_cold_start_history_sec(t) or 999) < COLD_START_SEC
            and not bool(t.get("microsequence_ok"))
            and _low_expectancy(t, 2.5),
        ),
    ]
    rows: list[dict[str, Any]] = []
    for sid, pred in scenarios:
        for label, pool in pools:
            rows.append(_counterfactual_row(scenario_id=sid, pool=pool, block_pred=pred, pool_label=label))
    return rows


def _sweep_shallow_bounce(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    base_conds: list[tuple[str, Callable[[Mapping[str, Any]], bool]]] = [
        ("shallow_fall", _is_shallow_fall),
        ("high_bounce_045", lambda t: _high_bounce(t, 0.45)),
        ("high_bounce_055", lambda t: _high_bounce(t, 0.55)),
        ("slope5_gt_0", lambda t: _slope_up(t, field="slope_5min", thr=0.0)),
        ("slope15_gt_0", lambda t: _slope_up(t, field="slope_15min", thr=0.0)),
        ("board_not_following", _board_not_following),
        ("live_incomplete", _live_feature_incomplete),
        ("expectancy_le_2.5", lambda t: _low_expectancy(t, 2.5)),
        ("microsequence_gap", lambda t: not bool(t.get("microsequence_ok"))),
    ]
    pools = [
        ("20260709", [t for t in trades if str(t.get("day") or "").startswith("2026-07-09")]),
        ("recent_707_709", [t for t in trades if str(t.get("day") or "") in RECENT_DAYS]),
        ("canonical_22", [t for t in trades if t.get("dataset") == "canonical_22"]),
        ("post_flat_band", [t for t in trades if t.get("post_flat_band_entry")]),
    ]
    es_709 = [t for t in trades if str(t.get("day") or "").startswith("2026-07-09") and _is_early_stop(t)]
    for n_combo in (2, 3, 4):
        for combo in combinations(base_conds, n_combo):
            names = [c[0] for c in combo]
            preds = [c[1] for c in combo]

            def _pred(t: Mapping[str, Any], ps=preds) -> bool:
                return all(p(t) for p in ps)

            combo_id = "+".join(names)
            for label, pool in pools:
                blocked = [t for t in pool if _pred(t)]
                if not blocked:
                    continue
                kept = [t for t in pool if not _pred(t)]
                base_m = _metrics(pool)
                kept_m = _metrics(kept)
                capture_709 = sum(1 for t in es_709 if _pred(t))
                rows.append(
                    {
                        "combo_id": combo_id,
                        "n_conditions": n_combo,
                        "pool": label,
                        "blocked_count": len(blocked),
                        "blocked_early_stop": sum(1 for t in blocked if _is_early_stop(t)),
                        "blocked_winners": sum(1 for t in blocked if _is_winner(t)),
                        "blocked_big_winners": sum(1 for t in blocked if _is_big_winner(t)),
                        "capture_709_early_stop": capture_709,
                        "delta_pnl_yen": round(
                            float(kept_m.get("pnl_yen_100") or 0) - float(base_m.get("pnl_yen_100") or 0),
                            2,
                        ),
                        "pf_delta": round(
                            float(kept_m.get("profit_factor") or 0) - float(base_m.get("profit_factor") or 0),
                            4,
                        ),
                    }
                )
    rows.sort(
        key=lambda r: (
            int(r.get("capture_709_early_stop") or 0),
            float(r.get("delta_pnl_yen") or 0),
            -int(r.get("blocked_winners") or 0),
        ),
        reverse=True,
    )
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows


def _expectancy_audit(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    d709 = [t for t in trades if str(t.get("day") or "").startswith("2026-07-09")]
    es = [t for t in d709 if _is_early_stop(t)]
    win = [t for t in d709 if _is_winner(t)]
    es_scores = [float(_num(t.get("entry_expectancy_score_v2")) or 0) for t in es if _num(t.get("entry_expectancy_score_v2")) is not None]
    win_scores = [float(_num(t.get("entry_expectancy_score_v2")) or 0) for t in win if _num(t.get("entry_expectancy_score_v2")) is not None]
    return {
        "709_early_stop_mean": round(statistics.mean(es_scores), 4) if es_scores else None,
        "709_winner_mean": round(statistics.mean(win_scores), 4) if win_scores else None,
        "709_early_stop_n": len(es_scores),
        "709_winner_n": len(win_scores),
    }


def _6525_analysis(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    sym = "6525.T"
    d709 = [t for t in trades if str(t.get("day") or "").startswith("2026-07-09") and str(t.get("symbol") or "") == sym]
    incomplete = sum(1 for t in d709 if not bool(t.get("live_feature_complete")))
    gap = sum(1 for t in d709 if not bool(t.get("microsequence_ok")))
    es = sum(1 for t in d709 if _is_early_stop(t))
    return {
        "entry_count": len(d709),
        "early_stop_count": es,
        "live_feature_incomplete_count": incomplete,
        "microsequence_gap_count": gap,
        "live_incomplete_rate": round(incomplete / len(d709), 4) if d709 else None,
    }


def _decide_verdict(
    *,
    lfc_audit: Sequence[Mapping[str, Any]],
    gap_audit: Sequence[Mapping[str, Any]],
    missed: Sequence[Mapping[str, Any]],
    sweep: Sequence[Mapping[str, Any]],
    cf: Sequence[Mapping[str, Any]],
    expectancy: Mapping[str, Any],
    sym6525: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    def _rate(pool: str, flag: bool, field: str = "early_stop_rate") -> Optional[float]:
        for r in lfc_audit:
            if r.get("pool") == pool and r.get("live_feature_complete") is flag and r.get("session_kind") == "ALL":
                v = r.get(field)
                return float(v) if v is not None else None
        return None

    lfc_false_709 = _rate("20260709", False)
    lfc_true_709 = _rate("20260709", True)
    lfc_false_canon = _rate("canonical_22", False)
    gap_es_709 = next((r for r in gap_audit if r.get("pool") == "20260709" and r.get("gap_reason") != "ok"), None)
    cf_gap_709 = next((r for r in cf if r.get("scenario_id") == "microsequence_gap_reject" and r.get("pool") == "20260709"), {})
    cf_lfc_709 = next((r for r in cf if r.get("scenario_id") == "live_feature_incomplete_reject" and r.get("pool") == "20260709"), {})
    cf_shallow_709 = next(
        (r for r in cf if r.get("scenario_id") == "shallow_fall_high_bounce_045" and r.get("pool") == "20260709"),
        {},
    )
    best_sweep_709 = next((r for r in sweep if r.get("pool") == "20260709" and int(r.get("capture_709_early_stop") or 0) >= 3), {})
    missed_ok = [m for m in missed if m.get("microsequence_ok")]
    shallow_miss = sum(1 for m in missed_ok if m.get("shallow_fall") and m.get("high_bounce_045"))

    answers = {
        "1_live_feature_incomplete_main_cause": (
            lfc_false_709 is not None
            and lfc_false_canon is not None
            and lfc_false_709 > lfc_false_canon
            and lfc_false_709 >= 0.12
        ),
        "709_lfc_false_early_stop_rate": lfc_false_709,
        "709_lfc_true_early_stop_rate": lfc_true_709,
        "canonical_lfc_false_early_stop_rate": lfc_false_canon,
        "2_microsequence_gap_worth_blocking": (
            float(cf_gap_709.get("delta_pnl_yen") or 0) > 0
            and int(cf_gap_709.get("blocked_early_stop") or 0) >= 2
        ),
        "cf_gap_709": cf_gap_709,
        "3_opening_cold_start_detectable": sum(1 for m in missed if m.get("gap_reason") in ("opening_cold_start", "push_history_insufficient", "price_history_insufficient")) >= 2,
        "4_missed_ac_common_pattern": shallow_miss >= 2 or len(missed_ok) == 4,
        "missed_ok_count": len(missed_ok),
        "shallow_high_bounce_missed_ok": shallow_miss,
        "5_shallow_bounce_reject_candidate": (
            int(cf_shallow_709.get("blocked_early_stop") or 0) >= 2
            and float(cf_shallow_709.get("delta_pnl_yen") or 0) > 0
        ),
        "cf_shallow_709": cf_shallow_709,
        "best_sweep_709": best_sweep_709,
        "6_expectancy_usable_in_combo": (
            float(expectancy.get("709_early_stop_mean") or 99) < float(expectancy.get("709_winner_mean") or 0)
        ),
        "expectancy_audit": expectancy,
        "7_6525_explained_by_incomplete": sym6525.get("live_incomplete_rate", 0) >= 0.8,
        "6525_analysis": sym6525,
        "8_shadow_candidate_ready": False,
        "cf_lfc_709": cf_lfc_709,
    }

    if answers["2_microsequence_gap_worth_blocking"] and answers["3_opening_cold_start_detectable"]:
        verdict = PHASE676_VERDICT_OPENING_COLDSTART
    elif answers["5_shallow_bounce_reject_candidate"] or int(best_sweep_709.get("capture_709_early_stop") or 0) >= 4:
        verdict = PHASE676_VERDICT_SHALLOW_BOUNCE
    elif answers["1_live_feature_incomplete_main_cause"] and float(cf_lfc_709.get("delta_pnl_yen") or 0) > 50000:
        verdict = PHASE676_VERDICT_DATA_COMPLETENESS
    elif answers["1_live_feature_incomplete_main_cause"] or shallow_miss >= 2:
        verdict = PHASE676_VERDICT_HOLD
    else:
        verdict = PHASE676_VERDICT_REJECT
    return verdict, answers


def run_audit() -> dict[str, Any]:
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    trades = load_focus_dataset()

    lfc_audit = _audit_live_feature_complete(trades)
    gap_audit = _audit_microsequence_gap(trades)
    missed = _709_missed_cases(trades)
    sweep = _sweep_shallow_bounce(trades)
    cf = _build_counterfactuals(trades)
    expectancy = _expectancy_audit(trades)
    sym6525 = _6525_analysis(trades)
    verdict, answers = _decide_verdict(
        lfc_audit=lfc_audit,
        gap_audit=gap_audit,
        missed=missed,
        sweep=sweep,
        cf=cf,
        expectancy=expectancy,
        sym6525=sym6525,
    )

    blocked_winners = [
        t
        for t in trades
        if str(t.get("day") or "").startswith("2026-07-09")
        and _is_winner(t)
        and (_rule_a(t) or _rule_c(t))
        and t.get("microsequence_ok")
    ]

    report: dict[str, Any] = {
        "verdict": verdict,
        "mandatory_answers": answers,
        "709_early_stop_count": sum(
            1 for t in trades if str(t.get("day") or "").startswith("2026-07-09") and _is_early_stop(t)
        ),
        "709_missed_by_ac_count": len(missed),
        "709_blocked_winner_count": len(blocked_winners),
        "top_sweep_rows": sweep[:15],
        "top_counterfactual_709": [r for r in cf if r.get("pool") == "20260709"],
        "disk_usage_pct_before": disk_before,
        "disk_usage_pct_after": _disk_usage_pct(NATIVE_ROOT),
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "phase676_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_csv(
        REPORT_ROOT / "phase676_live_feature_complete_audit.csv",
        [
            "pool",
            "session_kind",
            "live_feature_complete",
            "entry_count",
            "stop_hit_count",
            "early_stop_count",
            "early_stop_rate",
            "pnl_yen_100",
            "profit_factor",
            "winner_count",
            "big_winner_count",
        ],
        lfc_audit,
    )
    _write_csv(
        REPORT_ROOT / "phase676_microsequence_gap_audit.csv",
        ["pool", "gap_reason", "entry_count", "early_stop_count", "early_stop_rate", "pnl_yen_100", "profit_factor", "winner_count"],
        gap_audit,
    )
    _write_csv(
        REPORT_ROOT / "phase676_709_missed_case_analysis.csv",
        list(missed[0].keys()) if missed else ["symbol"],
        missed,
    )
    _write_csv(
        REPORT_ROOT / "phase676_shallow_fall_high_bounce_sweep.csv",
        [
            "rank",
            "combo_id",
            "n_conditions",
            "pool",
            "blocked_count",
            "blocked_early_stop",
            "blocked_winners",
            "capture_709_early_stop",
            "delta_pnl_yen",
            "pf_delta",
        ],
        sweep,
    )
    _write_csv(
        REPORT_ROOT / "phase676_counterfactual.csv",
        [
            "scenario_id",
            "pool",
            "blocked_count",
            "blocked_early_stop",
            "blocked_winners",
            "blocked_big_winners",
            "delta_pnl_yen",
            "pf_delta",
            "dd_delta",
            "improved_days",
            "top_blocked_symbol",
        ],
        cf,
    )
    _write_decision_md(report=report, missed=missed)
    return report


def _write_decision_md(*, report: Mapping[str, Any], missed: Sequence[Mapping[str, Any]]) -> None:
    ans = report.get("mandatory_answers") or {}
    lines = [
        "# Phase676 — Opening Cold-Start / Live Feature Incomplete Audit",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        "",
        "## 必須回答",
        "",
        f"1. live_feature_complete=Falseはearly_stop主因か: {ans.get('1_live_feature_incomplete_main_cause')} "
        f"(7/9 rate {ans.get('709_lfc_false_early_stop_rate')} vs canonical {ans.get('canonical_lfc_false_early_stop_rate')})",
        f"2. microsequence欠損ENTRYは止める価値があるか: {ans.get('2_microsequence_gap_worth_blocking')}",
        f"3. 開場cold-startは状態条件で検出できるか: {ans.get('3_opening_cold_start_detectable')}",
        f"4. 7/9 A/C未捕捉4件の共通条件: shallow_fall+high_bounce={ans.get('shallow_high_bounce_missed_ok')}/{ans.get('missed_ok_count')}",
        f"5. 浅いfall+高bounceはreject候補か: {ans.get('5_shallow_bounce_reject_candidate')}",
        f"6. entry_expectancy_score_v2低値は複合条件として使えるか: {ans.get('6_expectancy_usable_in_combo')}",
        f"7. 6525型多回ENTRYは状態不完全で説明できるか: {ans.get('7_6525_explained_by_incomplete')}",
        f"8. Shadow候補へ進めるルール: {ans.get('8_shadow_candidate_ready')} (保留維持)",
        "",
        "## 7/9 A/C未捕捉 early_stop",
        "",
    ]
    for m in missed:
        lines.append(
            f"- {m.get('entry_time')} {m.get('symbol')} gap={m.get('gap_reason')} "
            f"bounce={m.get('bounce_from_recent_low')} fall={m.get('fall_from_recent_high')} "
            f"lfc={m.get('live_feature_complete')}"
        )
    lines.append("")
    (REPORT_ROOT / "phase676_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    report = run_audit()
    print(json.dumps({"verdict": report.get("verdict")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
