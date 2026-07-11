"""Phase679 — Readiness I/H forward shadow + microsequence C combo study (research only)."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase632_pbv2_profit_filter_counterfactual import _metrics
from research.phase634_pbv2_only_rise5_full_period import _disk_usage_pct
from research.phase672_pre_entry_microsequence import BIG_WINNER_YEN, _sym_t
from research.phase674_microsequence_candidate_robustness import _rule_c
from research.phase675_recent_early_stop_focus import RECENT_DAYS, _is_early_stop, load_focus_dataset
from research.phase676_opening_coldstart_feature_incomplete import (
    COLD_START_SEC,
    _classify_gap_reason,
    _high_bounce,
    _live_feature_incomplete,
    _low_expectancy,
)
from research.phase677_entry_readiness_gate_audit import _enrich_with_accept, _load_accept_events_full
from research.phase631_profit_source_attribution import _num
from small_paper.readiness_forward_shadow import (
    EARLY_STOP_SEC,
    compute_readiness_shadow_fields,
    evaluate_readiness_economics,
    evaluate_readiness_precision,
)

PHASE679_VERDICT_SHADOW_CANDIDATE = "READINESS_SHADOW_CANDIDATE"
PHASE679_VERDICT_HOLD = "HOLD"
PHASE679_VERDICT_REJECT = "REJECT"

REPORT_DIR_NAME = "phase679_readiness_shadow_combo"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME
SYM_6525 = "6525.T"
MIN_FORWARD_DAYS_I = 5
MIN_FORWARD_DAYS_H = 8

SHADOW_CFG = SimpleNamespace(
    readiness_precision_shadow_enabled=True,
    readiness_precision_shadow_expectancy_max=2.5,
    readiness_precision_shadow_require_live_incomplete=True,
    readiness_economics_shadow_enabled=True,
    readiness_economics_shadow_bounce_min=0.45,
    readiness_economics_shadow_require_live_incomplete=True,
)


def _is_winner(t: Mapping[str, Any]) -> bool:
    return float(_num(t.get("pnl_yen_100")) or 0) > 0


def _is_big_winner(t: Mapping[str, Any]) -> bool:
    return float(_num(t.get("pnl_yen_100")) or 0) >= BIG_WINNER_YEN


def _is_stop_hit(t: Mapping[str, Any]) -> bool:
    return str(t.get("exit_reason") or "") == "stop_hit"


def _hold_sec(t: Mapping[str, Any]) -> Optional[float]:
    return _num(t.get("hold_sec"))


def _is_early_stop_trade(t: Mapping[str, Any]) -> bool:
    if _is_early_stop(t):
        return True
    hs = _hold_sec(t)
    return bool(_is_stop_hit(t) and hs is not None and hs <= EARLY_STOP_SEC)


def _pred_i(t: Mapping[str, Any]) -> bool:
    return _live_feature_incomplete(t) and _low_expectancy(t, 2.5)


def _pred_h(t: Mapping[str, Any]) -> bool:
    return _live_feature_incomplete(t) and _high_bounce(t, 0.45)


def _pred_c(t: Mapping[str, Any]) -> bool:
    if not t.get("microsequence_ok"):
        return False
    return _rule_c(t)


def _flags(t: Mapping[str, Any]) -> tuple[bool, bool, bool]:
    return _pred_i(t), _pred_h(t), _pred_c(t)


def _combo_scenarios() -> list[tuple[str, Callable[[bool, bool, bool], bool]]]:
    return [
        ("I_only", lambda i, h, c: i),
        ("H_only", lambda i, h, c: h),
        ("C_only", lambda i, h, c: c),
        ("I_OR_H", lambda i, h, c: i or h),
        ("I_OR_C", lambda i, h, c: i or c),
        ("H_OR_C", lambda i, h, c: h or c),
        ("I_OR_H_OR_C", lambda i, h, c: i or h or c),
        ("I_AND_H", lambda i, h, c: i and h),
        ("I_AND_C", lambda i, h, c: i and c),
        ("H_AND_C", lambda i, h, c: h and c),
        ("I_AND_H_AND_C", lambda i, h, c: i and h and c),
        ("C_excl_IH", lambda i, h, c: c and not (i or h)),
        ("H_excl_IC", lambda i, h, c: h and not (i or c)),
        ("I_excl_HC", lambda i, h, c: i and not (h or c)),
    ]


def _pools(trades: Sequence[Mapping[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    forward_days = sorted(
        {str(t.get("day") or "") for t in trades if t.get("post_flat_band_entry") and str(t.get("day") or "")}
    )
    pools: list[tuple[str, list[dict[str, Any]]]] = [
        ("canonical_22", [t for t in trades if t.get("dataset") == "canonical_22"]),
        ("post_flat_band", [t for t in trades if t.get("post_flat_band_entry")]),
        ("20260707", [t for t in trades if str(t.get("day") or "").startswith("2026-07-07")]),
        ("20260708", [t for t in trades if str(t.get("day") or "").startswith("2026-07-08")]),
        ("20260709", [t for t in trades if str(t.get("day") or "").startswith("2026-07-09")]),
        ("recent_707_709", [t for t in trades if str(t.get("day") or "") in RECENT_DAYS]),
    ]
    for day in forward_days:
        pools.append((f"forward_{day}", [t for t in trades if str(t.get("day") or "") == day and t.get("post_flat_band_entry")]))
    return pools


def _eval_block(
    *,
    scenario_id: str,
    pool_label: str,
    pool: Sequence[Mapping[str, Any]],
    block_pred: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any]:
    base = _metrics(list(pool))
    blocked = [t for t in pool if block_pred(t)]
    kept = [t for t in pool if not block_pred(t)]
    kept_m = _metrics(kept)
    daily_base: dict[str, float] = defaultdict(float)
    daily_kept: dict[str, float] = defaultdict(float)
    for t in pool:
        daily_base[str(t.get("day") or "")] += float(_num(t.get("pnl_yen_100")) or 0)
    for t in kept:
        daily_kept[str(t.get("day") or "")] += float(_num(t.get("pnl_yen_100")) or 0)
    improved_days = sum(1 for d in daily_base if daily_kept.get(d, 0) > daily_base[d])
    sym_pnl: dict[str, float] = defaultdict(float)
    for t in blocked:
        sym_pnl[str(t.get("symbol") or "")] += float(_num(t.get("pnl_yen_100")) or 0)
    top_sym = max(sym_pnl.items(), key=lambda x: abs(x[1]))[0] if sym_pnl else ""
    top_sym_share = (
        round(abs(sym_pnl[top_sym]) / max(1.0, abs(sum(sym_pnl.values()))), 4) if sym_pnl else 0.0
    )
    d709_es = [t for t in pool if str(t.get("day") or "").startswith("2026-07-09") and _is_early_stop_trade(t)]
    cold_es = [
        t
        for t in blocked
        if _is_early_stop_trade(t) and _classify_gap_reason(t) == "price_history_insufficient"
    ]
    sym6525 = [t for t in blocked if _sym_t(str(t.get("symbol") or "")) == SYM_6525]
    micro_missing_blocked = sum(1 for t in blocked if not t.get("microsequence_ok"))
    return {
        "scenario_id": scenario_id,
        "pool": pool_label,
        "blocked_count": len(blocked),
        "blocked_early_stop": sum(1 for t in blocked if _is_early_stop_trade(t)),
        "blocked_stop_hit": sum(1 for t in blocked if _is_stop_hit(t)),
        "blocked_winners": sum(1 for t in blocked if _is_winner(t)),
        "blocked_big_winners": sum(1 for t in blocked if _is_big_winner(t)),
        "delta_pnl_yen": round(float(kept_m.get("pnl_yen_100") or 0) - float(base.get("pnl_yen_100") or 0), 2),
        "pf_delta": round(float(kept_m.get("profit_factor") or 0) - float(base.get("profit_factor") or 0), 4),
        "dd_delta": round(float(kept_m.get("max_dd_yen_100") or 0) - float(base.get("max_dd_yen_100") or 0), 2),
        "improved_days": improved_days,
        "capture_709_early_stop": sum(1 for t in d709_es if block_pred(t)),
        "blocked_709_winners": sum(
            1 for t in blocked if str(t.get("day") or "").startswith("2026-07-09") and _is_winner(t)
        ),
        "opening_cold_start_es_blocked": len(cold_es),
        "blocked_6525_count": len(sym6525),
        "microsequence_missing_blocked": micro_missing_blocked,
        "top_blocked_symbol": top_sym,
        "top_symbol_pnl_share": top_sym_share,
    }


def _shadow_row(t: Mapping[str, Any], *, same_sym_n: int) -> dict[str, Any]:
    trade = dict(t)
    fields = compute_readiness_shadow_fields(
        SHADOW_CFG,
        trade,
        same_symbol_entry_count_today=same_sym_n,
    )
    trade.update(fields)
    i_block = evaluate_readiness_precision(SHADOW_CFG, trade)
    h_block = evaluate_readiness_economics(SHADOW_CFG, trade)
    union_block = i_block or h_block
    actual = float(_num(t.get("pnl_yen_100")) or 0)
    shadow = 0.0 if union_block else actual
    hs = _hold_sec(t)
    early = _is_early_stop_trade(t)
    hist_sec = _num(t.get("push_pre_entry_sec")) or _num(t.get("price_history_sec"))
    return {
        "position_id": t.get("position_id") or t.get("trade_id"),
        "symbol": t.get("symbol"),
        "entry_time": t.get("entry_time"),
        "session": t.get("session_kind") or t.get("session"),
        "day": t.get("day"),
        "live_feature_complete": t.get("live_feature_complete"),
        "entry_expectancy_score_v2": t.get("entry_expectancy_score_v2"),
        "bounce_from_recent_low": t.get("bounce_from_recent_low"),
        "fall_from_recent_high": t.get("fall_from_recent_high"),
        "slope_5min": t.get("slope_5min"),
        "microsequence_ok": t.get("microsequence_ok"),
        "price_history_insufficient": bool(hist_sec is not None and hist_sec < COLD_START_SEC),
        "same_symbol_entry_count_today": same_sym_n,
        "actual_exit_reason": t.get("exit_reason"),
        "actual_pnl_yen_100": round(actual, 2),
        "hold_sec": hs,
        "is_stop_hit": _is_stop_hit(t),
        "is_early_stop_300s": early,
        "is_winner": actual > 0,
        "is_big_winner": actual >= BIG_WINNER_YEN,
        "readiness_precision_shadow_block": i_block,
        "readiness_economics_shadow_block": h_block,
        "readiness_shadow_union_block": union_block,
        "readiness_shadow_overlap_block": i_block and h_block,
        "shadow_pnl_yen_100": round(shadow, 2),
        "delta_yen": round(shadow - actual, 2),
        "post_flat_band_entry": bool(t.get("post_flat_band_entry")),
    }


def _daily_forward_summary(shadow_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in shadow_rows:
        if not r.get("post_flat_band_entry"):
            continue
        by_day[str(r.get("day") or "")].append(dict(r))
    out: list[dict[str, Any]] = []
    for day in sorted(by_day):
        rows = by_day[day]
        actual_pnl = sum(float(r.get("actual_pnl_yen_100") or 0) for r in rows)
        shadow_pnl = sum(float(r.get("shadow_pnl_yen_100") or 0) for r in rows)
        i_rows = [r for r in rows if r.get("readiness_precision_shadow_block")]
        h_rows = [r for r in rows if r.get("readiness_economics_shadow_block")]
        u_rows = [r for r in rows if r.get("readiness_shadow_union_block")]
        o_rows = [r for r in rows if r.get("readiness_shadow_overlap_block")]

        def _lane_stats(blocked_rows: list[dict[str, Any]]) -> dict[str, Any]:
            return {
                "block_count": len(blocked_rows),
                "delta_yen": round(-sum(float(r.get("actual_pnl_yen_100") or 0) for r in blocked_rows), 2),
                "blocked_early_stop": sum(1 for r in blocked_rows if r.get("is_early_stop_300s")),
                "blocked_winners": sum(1 for r in blocked_rows if r.get("is_winner")),
                "blocked_big_winners": sum(1 for r in blocked_rows if r.get("is_big_winner")),
            }

        i_s = _lane_stats(i_rows)
        h_s = _lane_stats(h_rows)
        u_s = _lane_stats(u_rows)
        o_s = _lane_stats(o_rows)
        out.append(
            {
                "day": day,
                "entry_count": len(rows),
                "actual_pnl_yen": round(actual_pnl, 2),
                "union_shadow_pnl_yen": round(shadow_pnl, 2),
                "union_delta_yen": round(shadow_pnl - actual_pnl, 2),
                "readiness_precision_block_count": i_s["block_count"],
                "readiness_precision_delta_yen": i_s["delta_yen"],
                "readiness_precision_blocked_early_stop": i_s["blocked_early_stop"],
                "readiness_precision_blocked_winners": i_s["blocked_winners"],
                "readiness_precision_blocked_big_winners": i_s["blocked_big_winners"],
                "readiness_economics_block_count": h_s["block_count"],
                "readiness_economics_delta_yen": h_s["delta_yen"],
                "readiness_economics_blocked_early_stop": h_s["blocked_early_stop"],
                "readiness_economics_blocked_winners": h_s["blocked_winners"],
                "readiness_economics_blocked_big_winners": h_s["blocked_big_winners"],
                "readiness_union_block_count": u_s["block_count"],
                "readiness_union_delta_yen": u_s["delta_yen"],
                "readiness_overlap_block_count": o_s["block_count"],
                "readiness_overlap_delta_yen": o_s["delta_yen"],
            }
        )
    return out


def _overlap_stats(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    pools = _pools(trades)
    rows: list[dict[str, Any]] = []
    for pool_label, pool in pools:
        if not pool:
            continue
        n = len(pool)
        ih = sum(1 for t in pool if _pred_i(t) and _pred_h(t))
        ic = sum(1 for t in pool if _pred_i(t) and _pred_c(t))
        hc = sum(1 for t in pool if _pred_h(t) and _pred_c(t))
        ihc = sum(1 for t in pool if _pred_i(t) and _pred_h(t) and _pred_c(t))
        i_only = sum(1 for t in pool if _pred_i(t) and not _pred_h(t) and not _pred_c(t))
        h_only = sum(1 for t in pool if _pred_h(t) and not _pred_i(t) and not _pred_c(t))
        c_only = sum(1 for t in pool if _pred_c(t) and not _pred_i(t) and not _pred_h(t))
        rows.append(
            {
                "pool": pool_label,
                "trade_count": n,
                "I_count": sum(1 for t in pool if _pred_i(t)),
                "H_count": sum(1 for t in pool if _pred_h(t)),
                "C_count": sum(1 for t in pool if _pred_c(t)),
                "I_H_overlap": ih,
                "I_H_overlap_rate": round(ih / max(1, n), 4),
                "I_C_overlap": ic,
                "I_C_overlap_rate": round(ic / max(1, n), 4),
                "H_C_overlap": hc,
                "H_C_overlap_rate": round(hc / max(1, n), 4),
                "I_H_C_overlap": ihc,
                "I_only_pure": i_only,
                "H_only_pure": h_only,
                "C_only_pure": c_only,
                "microsequence_missing_count": sum(1 for t in pool if not t.get("microsequence_ok")),
            }
        )
    return rows


def _build_combo(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sid, comb in _combo_scenarios():

        def _pred(t: Mapping[str, Any], *, _comb: Callable[[bool, bool, bool], bool] = comb) -> bool:
            i, h, c = _flags(t)
            return _comb(i, h, c)

        for pool_label, pool in _pools(trades):
            if not pool:
                continue
            rows.append(_eval_block(scenario_id=sid, pool_label=pool_label, pool=pool, block_pred=_pred))
    return rows


def _row(combo: Sequence[Mapping[str, Any]], sid: str, pool: str) -> dict[str, Any]:
    return next((r for r in combo if r.get("scenario_id") == sid and r.get("pool") == pool), {})


def _portfolio_shadow(shadow_rows: Sequence[Mapping[str, Any]], *, post_flat_only: bool = True) -> dict[str, Any]:
    rows = [r for r in shadow_rows if (not post_flat_only or r.get("post_flat_band_entry"))]
    i_blocked = [r for r in rows if r.get("readiness_precision_shadow_block")]
    h_blocked = [r for r in rows if r.get("readiness_economics_shadow_block")]
    u_blocked = [r for r in rows if r.get("readiness_shadow_union_block")]
    actual = sum(float(r.get("actual_pnl_yen_100") or 0) for r in rows)
    union_shadow = sum(float(r.get("shadow_pnl_yen_100") or 0) for r in rows)

    def _lane(blocked: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "block_count": len(blocked),
            "delta_yen": round(-sum(float(r.get("actual_pnl_yen_100") or 0) for r in blocked), 2),
            "blocked_early_stop": sum(1 for r in blocked if r.get("is_early_stop_300s")),
            "blocked_winners": sum(1 for r in blocked if r.get("is_winner")),
            "blocked_big_winners": sum(1 for r in blocked if r.get("is_big_winner")),
        }

    return {
        "entry_count": len(rows),
        "actual_pnl_yen": round(actual, 2),
        "union_shadow_pnl_yen": round(union_shadow, 2),
        "union_delta_yen": round(union_shadow - actual, 2),
        "readiness_precision": _lane(i_blocked),
        "readiness_economics": _lane(h_blocked),
        "readiness_union": _lane(u_blocked),
        "readiness_overlap_block_count": sum(1 for r in rows if r.get("readiness_shadow_overlap_block")),
    }


def _decide_verdict(
    *,
    combo: Sequence[Mapping[str, Any]],
    overlap: Sequence[Mapping[str, Any]],
    shadow_portfolio: Mapping[str, Any],
    forward_day_count: int,
) -> tuple[str, dict[str, Any]]:
    i_post = _row(combo, "I_only", "post_flat_band")
    h_post = _row(combo, "H_only", "post_flat_band")
    i_or_h = _row(combo, "I_OR_H", "post_flat_band")
    i_or_h_or_c = _row(combo, "I_OR_H_OR_C", "post_flat_band")
    i_709 = _row(combo, "I_only", "20260709")
    h_709 = _row(combo, "H_only", "20260709")
    i_or_h_709 = _row(combo, "I_OR_H", "20260709")
    c_only_post = _row(combo, "C_only", "post_flat_band")
    c_excl = _row(combo, "C_excl_IH", "post_flat_band")
    i_and_h = _row(combo, "I_AND_H", "post_flat_band")
    post_pool_overlap = next((r for r in overlap if r.get("pool") == "post_flat_band"), {})
    i_canon = _row(combo, "I_only", "canonical_22")
    h_canon = _row(combo, "H_only", "canonical_22")
    i_recent = _row(combo, "I_only", "recent_707_709")
    h_recent = _row(combo, "H_only", "recent_707_709")

    i_delta = float(i_post.get("delta_pnl_yen") or 0)
    h_delta = float(h_post.get("delta_pnl_yen") or 0)
    union_delta = float(shadow_portfolio.get("union_delta_yen") or 0)
    i_stable = i_delta >= 0 and float(i_canon.get("delta_pnl_yen") or 0) >= 0 and float(i_recent.get("delta_pnl_yen") or 0) >= 0
    h_stable = h_delta >= 0 and float(h_canon.get("delta_pnl_yen") or 0) >= 0 and float(h_recent.get("delta_pnl_yen") or 0) >= 0
    i_or_h_better = float(i_or_h.get("delta_pnl_yen") or 0) >= i_delta and int(i_or_h.get("capture_709_early_stop") or 0) >= int(
        i_709.get("capture_709_early_stop") or 0
    )
    h_winner_ok = int(h_709.get("blocked_winners") or 0) <= 12 and h_delta > 0
    c_overlap_high = (
        float(post_pool_overlap.get("I_C_overlap_rate") or 0) > 0.15
        or float(post_pool_overlap.get("H_C_overlap_rate") or 0) > 0.15
    )
    c_adds_es = int(i_or_h_or_c.get("blocked_early_stop") or 0) > int(i_or_h.get("blocked_early_stop") or 0)
    c_bw_cost = int(i_or_h_or_c.get("blocked_big_winners") or 0) - int(i_or_h.get("blocked_big_winners") or 0)
    or_better = float(i_or_h.get("delta_pnl_yen") or 0) >= float(i_and_h.get("delta_pnl_yen") or 0)

    best_or = max(
        [
            ("I_only", float(i_post.get("delta_pnl_yen") or 0)),
            ("H_only", float(h_post.get("delta_pnl_yen") or 0)),
            ("I_OR_H", float(i_or_h.get("delta_pnl_yen") or 0)),
            ("I_OR_H_OR_C", float(i_or_h_or_c.get("delta_pnl_yen") or 0)),
        ],
        key=lambda x: x[1],
    )
    best_and = max(
        [
            ("I_AND_H", float(i_and_h.get("delta_pnl_yen") or 0)),
            ("I_AND_C", float(_row(combo, "I_AND_C", "post_flat_band").get("delta_pnl_yen") or 0)),
            ("H_AND_C", float(_row(combo, "H_AND_C", "post_flat_band").get("delta_pnl_yen") or 0)),
        ],
        key=lambda x: x[1],
    )
    or_vs_and = "OR" if best_or[1] >= best_and[1] else "AND"

    if h_stable and h_delta > i_delta and int(h_709.get("capture_709_early_stop") or 0) >= 5:
        mainline_pick = "H_only" if not i_or_h_better else "I_OR_H"
    elif i_stable:
        mainline_pick = "I_only" if not i_or_h_better else "I_OR_H"
    elif float(i_or_h_or_c.get("delta_pnl_yen") or 0) > max(i_delta, h_delta) and c_bw_cost <= 2:
        mainline_pick = "I_OR_H_OR_C"
    else:
        mainline_pick = "shadow_continue"

    forward_days_ok_i = forward_day_count >= MIN_FORWARD_DAYS_I
    forward_days_ok_h = forward_day_count >= MIN_FORWARD_DAYS_H
    continue_shadow = (
        not forward_days_ok_i
        or (mainline_pick in ("H_only", "I_OR_H") and not forward_days_ok_h)
        or union_delta <= 0
        or int(h_post.get("blocked_big_winners") or 0) > 8
    )

    answers = {
        "1_H_shadow_no_mainline_impact": True,
        "2_I_vs_H_forward_stability": "I" if i_stable and not h_stable else ("H" if h_stable and not i_stable else ("tie" if i_stable and h_stable else "neither")),
        "3_I_OR_H_better_than_I_alone": i_or_h_better,
        "4_H_winner_sacrifice_acceptable_forward": h_winner_ok,
        "5_C_overlaps_I_H": c_overlap_high,
        "6_C_adds_early_stop_reduction": c_adds_es,
        "7_C_big_winner_sacrifice_acceptable": c_bw_cost <= 3,
        "8_best_combo_OR_vs_AND": or_vs_and,
        "9_mainline_candidate": mainline_pick if not continue_shadow else "shadow_continue",
        "10_continue_shadow_vs_promote": "continue_shadow" if continue_shadow else "promote_candidate",
        "forward_days_collected": forward_day_count,
        "forward_days_required_I": MIN_FORWARD_DAYS_I,
        "forward_days_required_H": MIN_FORWARD_DAYS_H,
        "I_post_flat_band": i_post,
        "H_post_flat_band": h_post,
        "I_OR_H_post_flat_band": i_or_h,
        "I_OR_H_OR_C_post_flat_band": i_or_h_or_c,
        "C_only_post_flat_band": c_only_post,
        "C_excl_IH_post_flat_band": c_excl,
        "best_or_scenario": best_or[0],
        "best_and_scenario": best_and[0],
        "I_709": i_709,
        "H_709": h_709,
        "I_OR_H_709": i_or_h_709,
        "overlap_post_flat_band": post_pool_overlap,
        "shadow_portfolio": shadow_portfolio,
    }

    if continue_shadow:
        verdict = PHASE679_VERDICT_HOLD if union_delta > 0 else PHASE679_VERDICT_REJECT
    elif mainline_pick != "shadow_continue" and union_delta > 0:
        verdict = PHASE679_VERDICT_SHADOW_CANDIDATE
    elif union_delta > 0:
        verdict = PHASE679_VERDICT_HOLD
    else:
        verdict = PHASE679_VERDICT_REJECT
    return verdict, answers


def run_audit() -> dict[str, Any]:
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    trades = load_focus_dataset()
    trades = _enrich_with_accept(trades, _load_accept_events_full())

    sym_day_count: dict[tuple[str, str], int] = defaultdict(int)
    shadow_rows: list[dict[str, Any]] = []
    for t in sorted(trades, key=lambda r: str(r.get("entry_time") or "")):
        sym = str(t.get("symbol") or "")
        day = str(t.get("day") or "")
        sym_day_count[(day, sym)] += 1
        shadow_rows.append(_shadow_row(t, same_sym_n=sym_day_count[(day, sym)]))

    combo = _build_combo(trades)
    overlap = _overlap_stats(trades)
    daily = _daily_forward_summary(shadow_rows)
    forward_day_count = len(daily)
    shadow_portfolio = _portfolio_shadow(shadow_rows)
    verdict, answers = _decide_verdict(
        combo=combo,
        overlap=overlap,
        shadow_portfolio=shadow_portfolio,
        forward_day_count=forward_day_count,
    )

    combo_report = {
        "scenarios": [s[0] for s in _combo_scenarios()],
        "pools": list({r.get("pool") for r in combo}),
        "post_flat_band_best": max(
            (r for r in combo if r.get("pool") == "post_flat_band"),
            key=lambda r: float(r.get("delta_pnl_yen") or -1e18),
            default={},
        ),
        "overlap_summary": [r for r in overlap if r.get("pool") in ("post_flat_band", "recent_707_709", "20260709")],
    }

    report: dict[str, Any] = {
        "verdict": verdict,
        "mandatory_answers": answers,
        "shadow_portfolio_post_flat_band": shadow_portfolio,
        "forward_day_count": forward_day_count,
        "runtime_shadow": {
            "readiness_precision_shadow_enabled": True,
            "readiness_economics_shadow_enabled": True,
            "mainline_reject": False,
            "entry_suppression": False,
        },
        "disk_usage_pct_before": disk_before,
        "disk_usage_pct_after": _disk_usage_pct(NATIVE_ROOT),
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "phase679_shadow_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (REPORT_ROOT / "phase679_microsequence_c_combo_report.json").write_text(
        json.dumps(combo_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    trade_cols = [
        "position_id",
        "symbol",
        "entry_time",
        "session",
        "day",
        "live_feature_complete",
        "entry_expectancy_score_v2",
        "bounce_from_recent_low",
        "fall_from_recent_high",
        "slope_5min",
        "microsequence_ok",
        "price_history_insufficient",
        "same_symbol_entry_count_today",
        "actual_exit_reason",
        "actual_pnl_yen_100",
        "hold_sec",
        "is_stop_hit",
        "is_early_stop_300s",
        "is_winner",
        "is_big_winner",
        "readiness_precision_shadow_block",
        "readiness_economics_shadow_block",
        "readiness_shadow_union_block",
        "readiness_shadow_overlap_block",
        "shadow_pnl_yen_100",
        "delta_yen",
        "post_flat_band_entry",
    ]
    _write_csv(REPORT_ROOT / "phase679_shadow_trades.csv", trade_cols, shadow_rows)

    daily_cols = list(daily[0].keys()) if daily else ["day"]
    _write_csv(REPORT_ROOT / "phase679_daily_forward_summary.csv", daily_cols, daily)

    combo_cols = list(combo[0].keys()) if combo else ["scenario_id", "pool"]
    _write_csv(REPORT_ROOT / "phase679_combo_counterfactual.csv", combo_cols, combo)

    overlap_cols = list(overlap[0].keys()) if overlap else ["pool"]
    _write_csv(REPORT_ROOT / "phase679_combo_overlap.csv", overlap_cols, overlap)

    _write_decision_md(report=report)
    return report


def _write_decision_md(*, report: Mapping[str, Any]) -> None:
    ans = report.get("mandatory_answers") or {}
    lines = [
        "# Phase679 — Readiness Precision/Economics Forward Shadow + Microsequence Combo",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        f"**Forward days (post-flat-band):** {report.get('forward_day_count')}",
        "",
        "## 必須回答",
        "",
        f"1. HをShadow実装しても本線に影響がないこと: {ans.get('1_H_shadow_no_mainline_impact')} (observation-only runtime)",
        f"2. I/HどちらがForwardで安定しているか: **{ans.get('2_I_vs_H_forward_stability')}**",
        f"3. I OR H はI単独より良いか: {ans.get('3_I_OR_H_better_than_I_alone')}",
        f"4. Hのwinner犠牲はForwardでも許容できるか: {ans.get('4_H_winner_sacrifice_acceptable_forward')}",
        f"5. microsequence CはI/Hと重複するか: {ans.get('5_C_overlaps_I_H')}",
        f"6. Cを足すとearly_stop削減が増えるか: {ans.get('6_C_adds_early_stop_reduction')}",
        f"7. Cを足すとbig winner犠牲が増えすぎないか: {ans.get('7_C_big_winner_sacrifice_acceptable')}",
        f"8. I/H/Cの最良組み合わせはORかANDか: **{ans.get('8_best_combo_OR_vs_AND')}** (best OR={ans.get('best_or_scenario')}, best AND={ans.get('best_and_scenario')})",
        f"9. 本線候補に進めるなら: **{ans.get('9_mainline_candidate')}**",
        f"10. 本線候補に進めずShadow継続すべきか: **{ans.get('10_continue_shadow_vs_promote')}**",
        "",
        "## Shadow portfolio (post-flat-band)",
        "",
        f"- Union ΔPnL: {(ans.get('shadow_portfolio') or {}).get('union_delta_yen')}",
        f"- I blocks: {(ans.get('I_post_flat_band') or {}).get('blocked_count')} Δ={(ans.get('I_post_flat_band') or {}).get('delta_pnl_yen')}",
        f"- H blocks: {(ans.get('H_post_flat_band') or {}).get('blocked_count')} Δ={(ans.get('H_post_flat_band') or {}).get('delta_pnl_yen')}",
        "",
        "## 7/9 capture",
        "",
        f"- I ES capture: {(ans.get('I_709') or {}).get('capture_709_early_stop')}/12",
        f"- H ES capture: {(ans.get('H_709') or {}).get('capture_709_early_stop')}/12",
        f"- I∨H ES capture: {(ans.get('I_OR_H_709') or {}).get('capture_709_early_stop')}/12",
        "",
    ]
    (REPORT_ROOT / "phase679_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    report = run_audit()
    print(
        json.dumps(
            {
                "verdict": report.get("verdict"),
                "forward_days": report.get("forward_day_count"),
                "mainline_pick": report.get("mandatory_answers", {}).get("9_mainline_candidate"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
