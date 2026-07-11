"""Phase679B — H economics blocked winner quality audit (research only)."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase632_pbv2_profit_filter_counterfactual import _metrics
from research.phase634_pbv2_only_rise5_full_period import _disk_usage_pct
from research.phase672_pre_entry_microsequence import BIG_WINNER_YEN
from research.phase674_microsequence_candidate_robustness import _rule_c
from research.phase675_recent_early_stop_focus import RECENT_DAYS, _is_early_stop, load_focus_dataset
from research.phase676_opening_coldstart_feature_incomplete import (
    COLD_START_SEC,
    _board_not_following,
    _classify_gap_reason,
    _high_bounce,
    _live_feature_incomplete,
    _low_expectancy,
)
from research.phase677_entry_readiness_gate_audit import _enrich_with_accept, _load_accept_events_full
from research.phase631_profit_source_attribution import _num

VERDICT_H_STRONG = "H_STRONG_CANDIDATE"
VERDICT_H_REFINE = "H_NEEDS_REFINEMENT"
VERDICT_I_OR_H = "I_OR_H_CANDIDATE"
VERDICT_I_ONLY = "I_ONLY_CONTINUE"
VERDICT_HOLD = "HOLD"
VERDICT_REJECT = "REJECT"

REPORT_DIR_NAME = "phase679b_h_economics_winner_quality"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME

INTENDED_WINNER_PNL = 10000.0
SMALL_WINNER_PNL = 3000.0
MFE_INTENDED_PCT = 2.5
MFE_SMALL_PCT = 1.0
EARLY_STOP_SEC = 300.0


def _pnl(t: Mapping[str, Any]) -> float:
    return float(_num(t.get("pnl_yen_100")) or 0)


def _is_winner(t: Mapping[str, Any]) -> bool:
    return _pnl(t) > 0


def _is_loser(t: Mapping[str, Any]) -> bool:
    return _pnl(t) < 0


def _is_big_winner(t: Mapping[str, Any]) -> bool:
    return _pnl(t) >= BIG_WINNER_YEN


def _is_stop_hit(t: Mapping[str, Any]) -> bool:
    return str(t.get("exit_reason") or "") == "stop_hit"


def _hold_sec(t: Mapping[str, Any]) -> Optional[float]:
    return _num(t.get("hold_sec"))


def _is_early_stop_trade(t: Mapping[str, Any]) -> bool:
    if _is_early_stop(t):
        return True
    hs = _hold_sec(t)
    return bool(_is_stop_hit(t) and hs is not None and hs <= EARLY_STOP_SEC)


def _mfe(t: Mapping[str, Any]) -> Optional[float]:
    return _num(t.get("peak_mfe_pct")) or _num(t.get("mfe_pct")) or _num(t.get("rolling_mfe_pct"))


def _mae(t: Mapping[str, Any]) -> Optional[float]:
    return _num(t.get("peak_mae_pct")) or _num(t.get("rolling_mae_pct")) or _num(t.get("mae_pct"))


def _pred_i(t: Mapping[str, Any]) -> bool:
    return _live_feature_incomplete(t) and _low_expectancy(t, 2.5)


def _pred_h(t: Mapping[str, Any], *, bounce_min: float = 0.45) -> bool:
    return _live_feature_incomplete(t) and _high_bounce(t, bounce_min)


def _pred_c(t: Mapping[str, Any]) -> bool:
    if not t.get("microsequence_ok"):
        return False
    return _rule_c(t)


def _ih_flags(t: Mapping[str, Any]) -> tuple[bool, bool]:
    return _pred_i(t), _pred_h(t)


def _intended_winner_proxy(t: Mapping[str, Any]) -> bool:
    p = _pnl(t)
    mfe = _mfe(t)
    return p >= INTENDED_WINNER_PNL or _is_big_winner(t) or (mfe is not None and mfe >= MFE_INTENDED_PCT)


def _small_accidental_winner(t: Mapping[str, Any]) -> bool:
    p = _pnl(t)
    mfe = _mfe(t)
    if p >= SMALL_WINNER_PNL:
        return False
    if mfe is not None and mfe >= MFE_SMALL_PCT:
        return False
    return _live_feature_incomplete(t) and (_board_not_following(t) or p < 1500)


def _risky_winner(t: Mapping[str, Any]) -> bool:
    exp = _num(t.get("entry_expectancy_score_v2"))
    return (
        _pred_h(t)
        and _board_not_following(t)
        and _live_feature_incomplete(t)
        and (exp is None or exp <= 3.0)
        and _high_bounce(t, 0.45)
    )


def _classify_h_blocked_winner(t: Mapping[str, Any]) -> str:
    if _intended_winner_proxy(t):
        return "A_intended_winner"
    if _small_accidental_winner(t):
        return "B_small_accidental_winner"
    if _risky_winner(t):
        return "C_risky_winner"
    if _num(t.get("bounce_from_recent_low")) is None or _num(t.get("entry_expectancy_score_v2")) is None:
        return "D_unknown"
    return "C_risky_winner"


def _big_winner_verdict(t: Mapping[str, Any]) -> str:
    cls = _classify_h_blocked_winner(t)
    if cls == "A_intended_winner":
        return "intended_winner"
    if cls == "B_small_accidental_winner":
        return "accidental_winner"
    return "risky_winner"


def _classify_h_blocked_loser(t: Mapping[str, Any]) -> str:
    if _is_early_stop_trade(t) or (
        _is_stop_hit(t)
        and (_low_expectancy(t, 2.5) or _high_bounce(t, 0.45) or _live_feature_incomplete(t) or _board_not_following(t))
    ):
        return "A_true_bad_entry"
    push = _num(t.get("push_pre_entry_sec")) or _num(t.get("price_history_sec"))
    if (push is not None and push < COLD_START_SEC) or _classify_gap_reason(t) in (
        "price_history_insufficient",
        "opening_cold_start",
        "push_history_insufficient",
    ):
        return "C_cold_start_loss"
    if not t.get("microsequence_ok"):
        return "C_cold_start_loss"
    return "B_normal_loss"


def _top_symbol_concentration(trades: Sequence[Mapping[str, Any]]) -> tuple[str, float]:
    sym_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        sym_pnl[str(t.get("symbol") or "")] += abs(_pnl(t))
    if not sym_pnl:
        return "", 0.0
    top = max(sym_pnl.items(), key=lambda x: x[1])
    total = sum(sym_pnl.values())
    return top[0], round(top[1] / max(1.0, total), 4)


def _agg_stats(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "count": 0,
            "pnl_sum": 0.0,
            "avg_pnl": None,
            "median_pnl": None,
            "avg_mfe": None,
            "avg_mae": None,
            "avg_hold_sec": None,
            "top_symbol": "",
            "top_symbol_concentration": 0.0,
        }
    pnls = [_pnl(t) for t in trades]
    mfes = [float(v) for v in (_mfe(t) for t in trades) if v is not None]
    maes = [float(v) for v in (_mae(t) for t in trades) if v is not None]
    holds = [float(v) for v in (_hold_sec(t) for t in trades) if v is not None]
    top_sym, top_share = _top_symbol_concentration(trades)
    return {
        "count": len(trades),
        "pnl_sum": round(sum(pnls), 2),
        "avg_pnl": round(statistics.fmean(pnls), 2),
        "median_pnl": round(statistics.median(pnls), 2),
        "avg_mfe": round(statistics.fmean(mfes), 4) if mfes else None,
        "avg_mae": round(statistics.fmean(maes), 4) if maes else None,
        "avg_hold_sec": round(statistics.fmean(holds), 2) if holds else None,
        "top_symbol": top_sym,
        "top_symbol_concentration": top_share,
    }


def _pnl_decomposition(blocked: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    losers = [t for t in blocked if _is_loser(t)]
    winners = [t for t in blocked if _is_winner(t)]
    big_winners = [t for t in blocked if _is_big_winner(t)]
    loser_sum = sum(_pnl(t) for t in losers)
    winner_sum = sum(_pnl(t) for t in winners)
    bw_sum = sum(_pnl(t) for t in big_winners)
    avoided = round(-loser_sum, 2)
    lost = round(winner_sum, 2)
    winner_pnls = [_pnl(t) for t in winners]
    loser_pnls = [_pnl(t) for t in losers]
    return {
        "blocked_loser_count": len(losers),
        "blocked_loser_pnl_sum": round(loser_sum, 2),
        "avoided_loss_yen": avoided,
        "blocked_winner_count": len(winners),
        "blocked_winner_pnl_sum": round(winner_sum, 2),
        "lost_profit_yen": lost,
        "blocked_big_winner_count": len(big_winners),
        "blocked_big_winner_pnl_sum": round(bw_sum, 2),
        "net_delta_yen": round(avoided - lost, 2),
        "avg_blocked_winner_pnl": round(statistics.fmean(winner_pnls), 2) if winner_pnls else None,
        "median_blocked_winner_pnl": round(statistics.median(winner_pnls), 2) if winner_pnls else None,
        "avg_blocked_loser_pnl": round(statistics.fmean(loser_pnls), 2) if loser_pnls else None,
        "median_blocked_loser_pnl": round(statistics.median(loser_pnls), 2) if loser_pnls else None,
    }


def _counterfactual_metrics(pool: Sequence[Mapping[str, Any]], block_pred: Callable[[Mapping[str, Any]], bool]) -> dict[str, Any]:
    blocked = [t for t in pool if block_pred(t)]
    kept = [t for t in pool if not block_pred(t)]
    base = _metrics(list(pool))
    kept_m = _metrics(kept)
    decomp = _pnl_decomposition(blocked)
    daily_base: dict[str, float] = defaultdict(float)
    daily_kept: dict[str, float] = defaultdict(float)
    for t in pool:
        daily_base[str(t.get("day") or "")] += _pnl(t)
    for t in kept:
        daily_kept[str(t.get("day") or "")] += _pnl(t)
    improved_days = sum(1 for d in daily_base if daily_kept.get(d, 0) > daily_base[d])
    top_sym, top_share = _top_symbol_concentration(blocked)
    return {
        "blocked_count": len(blocked),
        **decomp,
        "blocked_early_stop": sum(1 for t in blocked if _is_early_stop_trade(t)),
        "blocked_stop_hit": sum(1 for t in blocked if _is_stop_hit(t)),
        "delta_pnl_yen": round(float(kept_m.get("pnl_yen_100") or 0) - float(base.get("pnl_yen_100") or 0), 2),
        "pf_delta": round(float(kept_m.get("profit_factor") or 0) - float(base.get("profit_factor") or 0), 4),
        "dd_delta": round(float(kept_m.get("max_dd_yen_100") or 0) - float(base.get("max_dd_yen_100") or 0), 2),
        "improved_days": improved_days,
        "top_symbol": top_sym,
        "top_symbol_concentration": top_share,
    }


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
        pools.append(
            (f"forward_{day}", [t for t in trades if str(t.get("day") or "") == day and t.get("post_flat_band_entry")])
        )
    return pools


def _h_decomposition_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pool_label, pool in _pools(trades):
        if not pool:
            continue
        blocked = [t for t in pool if _pred_h(t)]
        decomp = _pnl_decomposition(blocked)
        winners = [t for t in blocked if _is_winner(t)]
        rows.append(
            {
                "pool": pool_label,
                **decomp,
                "improvement_from_loss_avoidance": decomp["avoided_loss_yen"] > decomp["lost_profit_yen"],
                "winners_mostly_small": (
                    decomp.get("median_blocked_winner_pnl") is not None
                    and float(decomp["median_blocked_winner_pnl"]) < SMALL_WINNER_PNL
                ),
                "big_winner_impact_yen": decomp["blocked_big_winner_pnl_sum"],
                "net_positive_by_amount": decomp["net_delta_yen"] > 0,
            }
        )
        for cls in ("A_intended_winner", "B_small_accidental_winner", "C_risky_winner", "D_unknown"):
            sub = [t for t in winners if _classify_h_blocked_winner(t) == cls]
            stats = _agg_stats(sub)
            rows.append({"pool": pool_label, "winner_class": cls, **stats})
    return rows


def _h_blocked_winner_quality_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pool_label, pool in _pools(trades):
        blocked_winners = [t for t in pool if _pred_h(t) and _is_winner(t)]
        by_cls: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in blocked_winners:
            by_cls[_classify_h_blocked_winner(t)].append(dict(t))
        for cls, items in sorted(by_cls.items()):
            stats = _agg_stats(items)
            rows.append({"pool": pool_label, "winner_class": cls, **stats})
    return rows


def _big_winner_audit_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    canon = [t for t in trades if t.get("dataset") == "canonical_22" and _pred_h(t) and _is_big_winner(t)]
    canon.sort(key=lambda r: str(r.get("entry_time") or ""))
    rows: list[dict[str, Any]] = []
    for t in canon:
        i, h = _ih_flags(t)
        rows.append(
            {
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "pnl_yen_100": _pnl(t),
                "mfe": _mfe(t),
                "mae": _mae(t),
                "hold_sec": _hold_sec(t),
                "exit_reason": t.get("exit_reason"),
                "live_feature_complete": t.get("live_feature_complete"),
                "entry_expectancy_score_v2": t.get("entry_expectancy_score_v2"),
                "bounce_from_recent_low": t.get("bounce_from_recent_low"),
                "fall_from_recent_high": t.get("fall_from_recent_high"),
                "slope_5min": t.get("slope_5min"),
                "price_up_with_board_not_following": t.get("price_up_with_board_not_following"),
                "board_following_proxy": not _board_not_following(t),
                "board_improvement": t.get("board_improvement"),
                "microsequence_c_match": _pred_c(t),
                "i_precision_match": i,
                "h_reason": "incomplete_high_bounce",
                "winner_quality_class": _classify_h_blocked_winner(t),
                "big_winner_verdict": _big_winner_verdict(t),
            }
        )
    return rows


def _h_loser_quality_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pool_label, pool in _pools(trades):
        blocked_losers = [t for t in pool if _pred_h(t) and _is_loser(t)]
        by_cls: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in blocked_losers:
            by_cls[_classify_h_blocked_loser(t)].append(t)
        for cls, items in sorted(by_cls.items()):
            pnls = [_pnl(t) for t in items]
            top_sym, top_share = _top_symbol_concentration(items)
            rows.append(
                {
                    "pool": pool_label,
                    "loser_class": cls,
                    "count": len(items),
                    "pnl_sum": round(sum(pnls), 2),
                    "early_stop_count": sum(1 for t in items if _is_early_stop_trade(t)),
                    "stop_hit_count": sum(1 for t in items if _is_stop_hit(t)),
                    "avg_loss": round(statistics.fmean(pnls), 2) if pnls else None,
                    "top_symbol": top_sym,
                    "top_symbol_concentration": top_share,
                }
            )
    return rows


def _ih_decomposition_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: list[tuple[str, Callable[[bool, bool], bool]]] = [
        ("H_only", lambda i, h: h and not i),
        ("I_only", lambda i, h: i and not h),
        ("I_AND_H", lambda i, h: i and h),
        ("I_OR_H", lambda i, h: i or h),
        ("H_not_I", lambda i, h: h and not i),
        ("I_not_H", lambda i, h: i and not h),
    ]
    rows: list[dict[str, Any]] = []
    for pool_label, pool in _pools(trades):
        if not pool:
            continue
        for gid, comb in groups:
            subset = [t for t in pool if comb(*_ih_flags(t))]
            decomp = _pnl_decomposition(subset)
            rows.append({"pool": pool_label, "group_id": gid, **decomp})
        for sid, pred in (
            ("H_block", _pred_h),
            ("I_block", _pred_i),
            ("I_OR_H_block", lambda t: _pred_i(t) or _pred_h(t)),
            ("I_AND_H_block", lambda t: _pred_i(t) and _pred_h(t)),
        ):
            rows.append({"pool": pool_label, "group_id": sid, **_counterfactual_metrics(pool, pred)})
    return rows


def _combo_quality_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    scenarios: list[tuple[str, Callable[[Mapping[str, Any]], bool]]] = [
        ("C_only", _pred_c),
        ("H_only", _pred_h),
        ("I_only", _pred_i),
        ("H_OR_C", lambda t: _pred_h(t) or _pred_c(t)),
        ("I_OR_C", lambda t: _pred_i(t) or _pred_c(t)),
        ("I_OR_H_OR_C", lambda t: _pred_i(t) or _pred_h(t) or _pred_c(t)),
        ("H_AND_C", lambda t: _pred_h(t) and _pred_c(t)),
        ("I_AND_C", lambda t: _pred_i(t) and _pred_c(t)),
        ("I_AND_H_AND_C", lambda t: _pred_i(t) and _pred_h(t) and _pred_c(t)),
        ("C_excl_H", lambda t: _pred_c(t) and not _pred_h(t)),
        ("H_excl_C", lambda t: _pred_h(t) and not _pred_c(t)),
    ]
    rows: list[dict[str, Any]] = []
    for pool_label, pool in _pools(trades):
        if not pool:
            continue
        h_bw = {(t.get("entry_time"), t.get("symbol")) for t in pool if _pred_h(t) and _is_big_winner(t)}
        for sid, pred in scenarios:
            blocked = [t for t in pool if pred(t)]
            decomp = _pnl_decomposition(blocked)
            cf = _counterfactual_metrics(pool, pred)
            bw_overlap_c = sum(
                1
                for t in blocked
                if _is_big_winner(t) and (t.get("entry_time"), t.get("symbol")) in h_bw and sid.startswith("C")
            )
            rows.append(
                {
                    "pool": pool_label,
                    "scenario_id": sid,
                    **decomp,
                    "delta_pnl_yen": cf["delta_pnl_yen"],
                    "blocked_early_stop": cf["blocked_early_stop"],
                    "pf_delta": cf["pf_delta"],
                    "dd_delta": cf["dd_delta"],
                    "improved_days": cf["improved_days"],
                    "top_symbol_concentration": cf["top_symbol_concentration"],
                    "h_big_winner_overlap_with_c": bw_overlap_c,
                }
            )
    return rows


def _refined_h_variants() -> list[tuple[str, Callable[[Mapping[str, Any]], bool]]]:
    variants: list[tuple[str, Callable[[Mapping[str, Any]], bool]]] = [
        ("H_baseline", lambda t: _pred_h(t)),
        ("H_AND_exp_le_2.0", lambda t: _pred_h(t) and _low_expectancy(t, 2.0)),
        ("H_AND_exp_le_2.5", lambda t: _pred_h(t) and _low_expectancy(t, 2.5)),
        ("H_AND_exp_le_3.0", lambda t: _pred_h(t) and _low_expectancy(t, 3.0)),
        ("H_AND_board_not_following", lambda t: _pred_h(t) and _board_not_following(t)),
        ("H_AND_NOT_intended_proxy", lambda t: _pred_h(t) and not _intended_winner_proxy(t)),
        ("H_AND_MFE_pre_low", lambda t: _pred_h(t) and (_mfe(t) is None or _mfe(t) < MFE_SMALL_PCT)),
        ("H_AND_hi_update_fail_high", lambda t: _pred_h(t) and (_num(t.get("high_update_failure_count")) or 0) > 11),
        ("H_AND_NOT_microsequence_C", lambda t: _pred_h(t) and not _pred_c(t)),
    ]
    for thr in (0.45, 0.55, 0.65, 0.75):
        variants.append((f"H_bounce_ge_{thr}", lambda t, b=thr: _pred_h(t, bounce_min=b)))
    for thr in (0.0, 0.05, 0.10, 0.15):
        variants.append(
            (
                f"H_AND_slope15_le_{thr}",
                lambda t, s=thr: _pred_h(t)
                and (_num(t.get("slope_15min")) is None or (_num(t.get("slope_15min")) or 999) <= s),
            )
        )
    for thr in (0, 1, 2, 3):
        variants.append(
            (
                f"H_AND_hi_update5_le_{thr}",
                lambda t, h=thr: _pred_h(t) and (_num(t.get("high_update_5min")) is None or (_num(t.get("high_update_5min")) or 999) <= h),
            )
        )
    return variants


def _refined_h_sweep_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pool = [t for t in trades if t.get("post_flat_band_entry")]
    if not pool:
        return rows
    for vid, pred in _refined_h_variants():
        cf = _counterfactual_metrics(pool, pred)
        rows.append({"variant_id": vid, "pool": "post_flat_band", **cf})
    rows.sort(key=lambda r: (float(r.get("net_delta_yen") or -1e18), -int(r.get("blocked_big_winner_count") or 0)), reverse=True)
    return rows


def _decide_verdict(
    *,
    h_post: Mapping[str, Any],
    i_post: Mapping[str, Any],
    i_or_h_post: Mapping[str, Any],
    big_winner_rows: Sequence[Mapping[str, Any]],
    winner_quality: Sequence[Mapping[str, Any]],
    sweep: Sequence[Mapping[str, Any]],
) -> tuple[str, dict[str, Any]]:
    intended_bw = sum(1 for r in big_winner_rows if r.get("big_winner_verdict") == "intended_winner")
    accidental_bw = sum(1 for r in big_winner_rows if r.get("big_winner_verdict") == "accidental_winner")
    risky_bw = sum(1 for r in big_winner_rows if r.get("big_winner_verdict") == "risky_winner")

    post_a = next((r for r in winner_quality if r.get("pool") == "post_flat_band" and r.get("winner_class") == "A_intended_winner"), {})
    post_b = next((r for r in winner_quality if r.get("pool") == "post_flat_band" and r.get("winner_class") == "B_small_accidental_winner"), {})

    best_refined = sweep[0] if sweep else {}
    refined_saves_bw = int(h_post.get("blocked_big_winner_count") or 0) - int(
        best_refined.get("blocked_big_winner_count") or 0
    )

    answers = {
        "1_H_lost_winner_profit_yen": h_post.get("lost_profit_yen"),
        "2_H_avoided_loser_loss_yen": h_post.get("avoided_loss_yen"),
        "3_H_improvement_from_loss_avoidance": float(h_post.get("avoided_loss_yen") or 0)
        > float(h_post.get("lost_profit_yen") or 0),
        "4_H_winners_mostly_small": float(h_post.get("median_blocked_winner_pnl") or 999) < SMALL_WINNER_PNL,
        "5_H_big_winners_intended": intended_bw > accidental_bw + risky_bw,
        "6_H_only_vs_I_only": float(h_post.get("net_delta_yen") or 0) > float(i_post.get("net_delta_yen") or 0),
        "7_I_OR_H_vs_I": float(i_or_h_post.get("net_delta_yen") or 0) >= float(i_post.get("net_delta_yen") or 0),
        "9_C_adds_profit_or_sacrifice": None,
        "10_continue_H_shadow": True,
        "11_H_rescue_worthwhile": refined_saves_bw >= 2 and float(best_refined.get("net_delta_yen") or 0) > 0,
        "12_next_mainline_pick": "refined_H",
        "big_winner_intended_count": intended_bw,
        "big_winner_accidental_count": accidental_bw,
        "big_winner_risky_count": risky_bw,
        "intended_winner_pnl_post": post_a.get("pnl_sum"),
        "accidental_winner_pnl_post": post_b.get("pnl_sum"),
        "best_refined_variant": best_refined.get("variant_id"),
        "best_refined_net_delta": best_refined.get("net_delta_yen"),
        "best_refined_big_winner_count": best_refined.get("blocked_big_winner_count"),
    }

    h_net = float(h_post.get("net_delta_yen") or 0)
    i_net = float(i_post.get("net_delta_yen") or 0)
    i_or_h_net = float(i_or_h_post.get("net_delta_yen") or 0)
    h_bw = int(h_post.get("blocked_big_winner_count") or 0)
    i_bw = int(i_post.get("blocked_big_winner_count") or 0)

    if h_net <= 0:
        verdict = VERDICT_REJECT
        answers["12_next_mainline_pick"] = "I_only"
        answers["10_continue_H_shadow"] = False
    elif intended_bw >= 5 and refined_saves_bw < 3:
        verdict = VERDICT_H_REFINE
        answers["12_next_mainline_pick"] = "refined_H"
        answers["10_continue_H_shadow"] = True
        answers["11_H_rescue_worthwhile"] = True
    elif i_or_h_net > i_net and h_bw > i_bw + 5 and float(post_a.get("pnl_sum") or 0) > 50000:
        verdict = VERDICT_I_OR_H if accidental_bw >= intended_bw else VERDICT_H_REFINE
        answers["12_next_mainline_pick"] = "I_OR_H" if verdict == VERDICT_I_OR_H else "refined_H"
    elif h_net > i_net * 1.5 and accidental_bw >= intended_bw:
        verdict = VERDICT_H_STRONG
        answers["12_next_mainline_pick"] = "H"
    elif i_net >= 0 and h_bw > 8:
        verdict = VERDICT_I_ONLY
        answers["12_next_mainline_pick"] = "I_only"
        answers["10_continue_H_shadow"] = True
    else:
        verdict = VERDICT_HOLD
        answers["12_next_mainline_pick"] = "refined_H" if answers["11_H_rescue_worthwhile"] else "I_OR_H"

    return verdict, answers


def run_audit() -> dict[str, Any]:
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    trades = load_focus_dataset()
    trades = _enrich_with_accept(trades, _load_accept_events_full())

    h_decomp = _h_decomposition_rows(trades)
    winner_quality = _h_blocked_winner_quality_rows(trades)
    big_winner_audit = _big_winner_audit_rows(trades)
    loser_quality = _h_loser_quality_rows(trades)
    ih_decomp = _ih_decomposition_rows(trades)
    combo_quality = _combo_quality_rows(trades)
    refined_sweep = _refined_h_sweep_rows(trades)

    post_pool = [t for t in trades if t.get("post_flat_band_entry")]
    h_post = _pnl_decomposition([t for t in post_pool if _pred_h(t)])
    h_post_cf = _counterfactual_metrics(post_pool, _pred_h)
    h_post = {**h_post, **{k: h_post_cf[k] for k in ("delta_pnl_yen", "pf_delta", "dd_delta", "blocked_early_stop")}}
    i_post_cf = _counterfactual_metrics(post_pool, _pred_i)
    i_or_h_cf = _counterfactual_metrics(post_pool, lambda t: _pred_i(t) or _pred_h(t))
    i_and_h_cf = _counterfactual_metrics(post_pool, lambda t: _pred_i(t) and _pred_h(t))
    c_or_h_cf = _counterfactual_metrics(post_pool, lambda t: _pred_h(t) or _pred_c(t))
    i_or_h_or_c_cf = _counterfactual_metrics(post_pool, lambda t: _pred_i(t) or _pred_h(t) or _pred_c(t))

    verdict, answers = _decide_verdict(
        h_post=h_post,
        i_post=i_post_cf,
        i_or_h_post=i_or_h_cf,
        big_winner_rows=big_winner_audit,
        winner_quality=winner_quality,
        sweep=refined_sweep,
    )
    answers["8_I_AND_H_precision"] = i_and_h_cf
    answers["9_C_adds_profit_or_sacrifice"] = {
        "I_OR_H_delta": i_or_h_cf.get("delta_pnl_yen"),
        "I_OR_H_OR_C_delta": i_or_h_or_c_cf.get("delta_pnl_yen"),
        "lost_profit_increase": round(
            float(i_or_h_or_c_cf.get("lost_profit_yen") or 0) - float(i_or_h_cf.get("lost_profit_yen") or 0),
            2,
        ),
        "C_adds_early_stop": int(i_or_h_or_c_cf.get("blocked_early_stop") or 0)
        > int(i_or_h_cf.get("blocked_early_stop") or 0),
    }

    report: dict[str, Any] = {
        "verdict": verdict,
        "mandatory_answers": answers,
        "h_post_flat_band_decomposition": h_post,
        "canonical_big_winner_count": len(big_winner_audit),
        "refined_sweep_top5": refined_sweep[:5],
        "disk_usage_pct_before": disk_before,
        "disk_usage_pct_after": _disk_usage_pct(NATIVE_ROOT),
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "phase679b_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    _write_csv(
        REPORT_ROOT / "phase679b_h_blocked_winner_quality.csv",
        ["pool", "winner_class", "count", "pnl_sum", "avg_pnl", "median_pnl", "avg_mfe", "avg_mae", "avg_hold_sec", "top_symbol", "top_symbol_concentration"],
        winner_quality,
    )
    _write_csv(
        REPORT_ROOT / "phase679b_h_big_winner_audit.csv",
        list(big_winner_audit[0].keys()) if big_winner_audit else ["symbol"],
        big_winner_audit,
    )
    _write_csv(
        REPORT_ROOT / "phase679b_h_loser_quality.csv",
        ["pool", "loser_class", "count", "pnl_sum", "early_stop_count", "stop_hit_count", "avg_loss", "top_symbol", "top_symbol_concentration"],
        loser_quality,
    )
    _write_csv(
        REPORT_ROOT / "phase679b_i_h_decomposition.csv",
        [
            "pool",
            "group_id",
            "blocked_count",
            "blocked_loser_count",
            "blocked_winner_count",
            "blocked_big_winner_count",
            "avoided_loss_yen",
            "lost_profit_yen",
            "net_delta_yen",
            "delta_pnl_yen",
            "blocked_early_stop",
            "pf_delta",
            "dd_delta",
        ],
        ih_decomp,
    )
    _write_csv(
        REPORT_ROOT / "phase679b_microsequence_combo_quality.csv",
        [
            "pool",
            "scenario_id",
            "blocked_count",
            "avoided_loss_yen",
            "lost_profit_yen",
            "net_delta_yen",
            "blocked_big_winner_count",
            "blocked_big_winner_pnl_sum",
            "blocked_early_stop",
            "delta_pnl_yen",
            "pf_delta",
            "top_symbol_concentration",
        ],
        combo_quality,
    )
    _write_csv(
        REPORT_ROOT / "phase679b_refined_h_sweep.csv",
        [
            "variant_id",
            "pool",
            "blocked_count",
            "avoided_loss_yen",
            "lost_profit_yen",
            "net_delta_yen",
            "blocked_big_winner_count",
            "blocked_big_winner_pnl_sum",
            "blocked_early_stop",
            "delta_pnl_yen",
            "pf_delta",
            "dd_delta",
            "improved_days",
            "top_symbol_concentration",
        ],
        refined_sweep,
    )
    _write_decision_md(report=report, h_decomp=h_decomp, big_winner_audit=big_winner_audit)
    return report


def _write_decision_md(
    *,
    report: Mapping[str, Any],
    h_decomp: Sequence[Mapping[str, Any]],
    big_winner_audit: Sequence[Mapping[str, Any]],
) -> None:
    ans = report.get("mandatory_answers") or {}
    h_post_row = next((r for r in h_decomp if r.get("pool") == "post_flat_band" and "winner_class" not in r), {})
    lines = [
        "# Phase679B — H Economics Blocked Winner Quality Audit",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        "",
        "## 必須回答",
        "",
        f"1. Hが消したWinnerの合計利益: **{ans.get('1_H_lost_winner_profit_yen')}** 円",
        f"2. Hが避けたLoserの合計損失: **{ans.get('2_H_avoided_loser_loss_yen')}** 円",
        f"3. Hの改善は小Winner犠牲で大Loser回避か: {ans.get('3_H_improvement_from_loss_avoidance')}",
        f"4. Hが消したWinnerは小勝ち中心か: {ans.get('4_H_winners_mostly_small')} (median={h_post_row.get('median_blocked_winner_pnl')})",
        f"5. Hが消したBig Winnerは本来取りたいWinnerか: {ans.get('5_H_big_winners_intended')} (intended={ans.get('big_winner_intended_count')}, accidental={ans.get('big_winner_accidental_count')}, risky={ans.get('big_winner_risky_count')})",
        f"6. H onlyはI onlyより価値があるか: {ans.get('6_H_only_vs_I_only')}",
        f"7. I OR HはI単独より採用価値があるか: {ans.get('7_I_OR_H_vs_I')}",
        f"8. I AND Hの精度: net_delta={((ans.get('8_I_AND_H_precision') or {}).get('net_delta_yen'))}",
        f"9. C追加効果: {ans.get('9_C_adds_profit_or_sacrifice')}",
        f"10. HをShadow継続すべきか: {ans.get('10_continue_H_shadow')}",
        f"11. H救済条件を作るべきか: {ans.get('11_H_rescue_worthwhile')} (best={ans.get('best_refined_variant')})",
        f"12. 次の本線候補: **{ans.get('12_next_mainline_pick')}**",
        "",
        "## canonical Big Winner監査",
        "",
        f"- 件数: {len(big_winner_audit)}",
    ]
    for r in big_winner_audit:
        lines.append(
            f"- {r.get('symbol')} {r.get('entry_time')}: pnl={r.get('pnl_yen_100')} "
            f"verdict={r.get('big_winner_verdict')} class={r.get('winner_quality_class')}"
        )
    lines.append("")
    (REPORT_ROOT / "phase679b_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    report = run_audit()
    print(json.dumps({"verdict": report.get("verdict"), "next": report.get("mandatory_answers", {}).get("12_next_mainline_pick")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
