"""
Shared Period-B shadow evaluation helpers for Phase379/380.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any, Callable, Mapping, Optional, Sequence

from research.phase377_daily_regime_breakdown import PERIOD_B_END, PERIOD_B_START

FOCUS_EXCLUDE_DAY = "20260612"
LOW_MFE_THRESHOLD_PCT = 0.3

PRODUCTION_CANDIDATE_THRESHOLDS = {
    "top_day_share_max": 0.5,
    "top_symbol_share_max": 0.3,
}


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _pf(yens: Sequence[float]) -> Optional[float]:
    gp = sum(max(y, 0.0) for y in yens)
    gl = abs(sum(min(y, 0.0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def in_period_b(day: str) -> bool:
    return PERIOD_B_START <= day <= PERIOD_B_END


def is_low_mfe_stop(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("exit_reason_canonical") or "") == "stop_hit" and (
        _float(trade.get("peak_mfe_pct")) or 0.0
    ) < LOW_MFE_THRESHOLD_PCT


def is_stop_hit(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("exit_reason_canonical") or "") == "stop_hit"


def cohort_label(trade: Mapping[str, Any]) -> str:
    if is_low_mfe_stop(trade):
        return "low_mfe_stop_hit"
    if is_stop_hit(trade):
        return "stop_hit_high_mfe"
    yen = _float(trade.get("pnl_yen_100"))
    if yen is not None and yen > 0:
        return "winning"
    if yen is not None and yen < 0:
        return "non_stop_losing"
    return "flat"


def evaluate_variant_shadow(
    trades: Sequence[Mapping[str, Any]],
    *,
    variant_id: str,
    would_block: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any]:
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for t in trades:
        if would_block(t):
            removed.append(dict(t))
        else:
            kept.append(dict(t))

    actual_yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in trades]
    kept_yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in kept]
    removed_yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in removed]

    actual_total = round(sum(actual_yens), 2)
    kept_total = round(sum(kept_yens), 2)
    skipped_pnl = round(sum(removed_yens), 2)

    baseline_by_day: dict[str, float] = defaultdict(float)
    variant_by_day: dict[str, float] = defaultdict(float)
    skipped_by_day: dict[str, float] = defaultdict(float)
    skipped_by_symbol: dict[str, float] = defaultdict(float)

    for t in trades:
        day = str(t.get("day_key") or "")
        yen = float(_float(t.get("pnl_yen_100")) or 0.0)
        baseline_by_day[day] += yen
    for t in kept:
        day = str(t.get("day_key") or "")
        variant_by_day[day] += float(_float(t.get("pnl_yen_100")) or 0.0)
    for t in removed:
        day = str(t.get("day_key") or "")
        sym = str(t.get("symbol") or "")
        yen = float(_float(t.get("pnl_yen_100")) or 0.0)
        skipped_by_day[day] += yen
        skipped_by_symbol[sym] += yen

    day_deltas = {
        day: round(variant_by_day.get(day, 0.0) - baseline_by_day.get(day, 0.0), 2)
        for day in sorted(baseline_by_day)
    }
    improved_days = sum(1 for d in day_deltas.values() if d > 0)
    worsened_days = sum(1 for d in day_deltas.values() if d < 0)
    flat_days = sum(1 for d in day_deltas.values() if d == 0)
    median_day_delta = (
        round(statistics.median(list(day_deltas.values())), 2) if day_deltas else None
    )

    total_delta = round(kept_total - actual_total, 2)
    top_day_share = None
    top_symbol_share = None
    if abs(total_delta) > 1e-6:
        if day_deltas:
            top_day_delta = max(day_deltas.items(), key=lambda kv: abs(kv[1]))
            top_day_share = round(abs(top_day_delta[1]) / abs(total_delta), 4)
        if skipped_by_symbol:
            top_sym_delta = max(skipped_by_symbol.items(), key=lambda kv: abs(kv[1]))
            top_symbol_share = round(abs(top_sym_delta[1]) / abs(total_delta), 4)

    exclude_days = [d for d in baseline_by_day if d != FOCUS_EXCLUDE_DAY]
    excl_baseline = round(sum(baseline_by_day[d] for d in exclude_days), 2)
    excl_variant = round(sum(variant_by_day.get(d, 0.0) for d in exclude_days), 2)
    delta_excl_612 = round(excl_variant - excl_baseline, 2)

    def _split_delta(subset_fn: Callable[[Mapping[str, Any]], bool]) -> Optional[float]:
        sub_kept = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in kept if subset_fn(t)]
        sub_all = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in trades if subset_fn(t)]
        return round(sum(sub_kept) - sum(sub_all), 2) if sub_all else None

    low_mfe_removed = [t for t in removed if is_low_mfe_stop(t)]
    stop_removed = [t for t in removed if is_stop_hit(t)]
    actual_pf = _pf(actual_yens)
    kept_pf = _pf(kept_yens)
    delta_pf = (
        round(float(kept_pf) - float(actual_pf), 4)
        if kept_pf is not None and actual_pf is not None
        and kept_pf != float("inf")
        and actual_pf != float("inf")
        else None
    )

    metrics = {
        "variant_id": variant_id,
        "removed_trade_count": len(removed),
        "skipped_pnl_actual": skipped_pnl,
        "delta_yen": total_delta,
        "delta_pf": delta_pf,
        "baseline_pf": actual_pf,
        "variant_pf": kept_pf,
        "baseline_pnl_yen_100": actual_total,
        "variant_pnl_yen_100": kept_total,
        "stop_hit_reduction_count": sum(1 for t in trades if is_stop_hit(t))
        - sum(1 for t in kept if is_stop_hit(t)),
        "low_mfe_stop_hit_reduction_count": sum(1 for t in trades if is_low_mfe_stop(t))
        - sum(1 for t in kept if is_low_mfe_stop(t)),
        "improved_days": improved_days,
        "worsened_days": worsened_days,
        "flat_days": flat_days,
        "median_day_delta": median_day_delta,
        "top_day_share": top_day_share,
        "top_symbol_share": top_symbol_share,
        "delta_excluding_20260612": delta_excl_612,
        "dynamic40_delta_yen": _split_delta(lambda t: str(t.get("universe_group") or "") == "dynamic40"),
        "core10_delta_yen": _split_delta(lambda t: str(t.get("universe_group") or "") == "core10"),
        "am_delta_yen": _split_delta(lambda t: str(t.get("session_kind") or "").lower() == "am"),
        "pm_delta_yen": _split_delta(lambda t: str(t.get("session_kind") or "").lower() == "pm"),
        "day_deltas": day_deltas,
    }
    metrics["production_candidate"] = production_candidate_pass(metrics)
    return metrics


def production_candidate_pass(metrics: Mapping[str, Any]) -> bool:
    checks = {
        "delta_yen_positive": (_float(metrics.get("delta_yen")) or 0.0) > 0,
        "delta_pf_positive": (_float(metrics.get("delta_pf")) or -1.0) > 0,
        "skipped_pnl_negative": (_float(metrics.get("skipped_pnl_actual")) or 0.0) < 0,
        "low_mfe_reduction": _int(metrics.get("low_mfe_stop_hit_reduction_count")) > 0,
        "improved_days_ge_worsened": _int(metrics.get("improved_days"))
        >= _int(metrics.get("worsened_days")),
        "median_day_delta_ge_zero": (_float(metrics.get("median_day_delta")) or -1.0) >= 0,
        "top_day_share_ok": (_float(metrics.get("top_day_share")) or 1.0)
        <= PRODUCTION_CANDIDATE_THRESHOLDS["top_day_share_max"],
        "top_symbol_share_ok": (_float(metrics.get("top_symbol_share")) or 1.0)
        <= PRODUCTION_CANDIDATE_THRESHOLDS["top_symbol_share_max"],
        "delta_excl_612_positive": (_float(metrics.get("delta_excluding_20260612")) or -1.0) > 0,
    }
    return all(checks.values())


def _int(val: Any) -> int:
    try:
        if val is None or val == "":
            return 0
        return int(float(val))
    except (TypeError, ValueError):
        return 0


def cohens_d(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    if len(a) < 2 or len(b) < 2:
        if a and b:
            ma, mb = statistics.mean(a), statistics.mean(b)
            return round(ma - mb, 4)
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    sa, sb = statistics.pstdev(a), statistics.pstdev(b)
    pooled = ((sa**2 + sb**2) / 2) ** 0.5
    if pooled <= 1e-9:
        return round(ma - mb, 4)
    return round((ma - mb) / pooled, 4)
