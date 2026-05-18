"""
Phase 30: recovery persistence EXIT v8 (Logic Lab only).

Evaluates sustained recovery (reclaim / favorable / imbalance) vs one-shot bounces.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timezone
from typing import TYPE_CHECKING, Optional

from research.microstructure_runtime import FAV_PERSISTENCE_MIN, VWAP_RECLAIM_FAIL

from src.kabu_exit_engine import (
    KabuExitEvalInput,
    KabuExitEvalResult,
    KabuExitV1Config,
    evaluate_kabu_exit_v1,
)

if TYPE_CHECKING:
    from research.microstructure_runtime import MicrostructureRuntime

V8_RECLAIM_PROFILES = frozenset(
    {"momentum_volume_v8_reclaim_persistence", "momentum_volume_v8_combined"}
)
V8_FAVORABLE_PROFILES = frozenset(
    {"momentum_volume_v8_favorable_persistence", "momentum_volume_v8_combined"}
)
V8_DELAYED_IMB_PROFILES = frozenset(
    {"momentum_volume_v8_delayed_imb_refined", "momentum_volume_v8_combined"}
)
V8_STRUCTURE_PROFILES = frozenset(
    {"momentum_volume_v8_structure_break_refined", "momentum_volume_v8_combined"}
)

DELAY_IMB_SEC = 60.0
IMB_STREAK = 4
IMB_SUSTAINED_TICKS = 5
STRUCTURE_MOM_STREAK = 4
STRUCTURE_IMB_STREAK = 3
VWAP_COLLAPSE_PCT = -0.14
TAKE_MFE = 0.30
TAKE_GIVEBACK = 0.18


def uses_momentum_v8_exit(profile: str) -> bool:
    return profile.startswith("momentum_volume_v8_")


def _pct_change(current: float, base: float) -> float:
    if base <= 0:
        return 0.0
    return ((float(current) - float(base)) / float(base)) * 100.0


def _elapsed_sec(inp: KabuExitEvalInput) -> float:
    e = inp.entry_time.astimezone(timezone.utc)
    n = inp.now_time.astimezone(timezone.utc)
    return max(0.0, (n - e).total_seconds())


def _vwap_dist(inp: KabuExitEvalInput) -> Optional[float]:
    if inp.current_vwap and float(inp.current_vwap) > 0:
        return _pct_change(float(inp.current_price), float(inp.current_vwap))
    return None


def _no_exit(base: KabuExitEvalResult, tag: str = "") -> KabuExitEvalResult:
    dbg = dict(base.exit_debug)
    if tag:
        dbg["v8_suppressed"] = tag
    return KabuExitEvalResult(
        would_exit=False,
        exit_reason="",
        exit_priority=0,
        unrealized_pct=base.unrealized_pct,
        mfe_pct=base.mfe_pct,
        elapsed_min=base.elapsed_min,
        exit_thresholds_used=dict(base.exit_thresholds_used),
        exit_debug=dbg,
    )


def _custom_exit(
    base: KabuExitEvalResult,
    *,
    reason: str,
    unrealized: float,
    mfe: float,
    debug: dict,
) -> KabuExitEvalResult:
    return KabuExitEvalResult(
        would_exit=True,
        exit_reason=reason,
        exit_priority=3,
        unrealized_pct=unrealized,
        mfe_pct=mfe,
        elapsed_min=base.elapsed_min,
        exit_thresholds_used=dict(base.exit_thresholds_used),
        exit_debug={**base.exit_debug, **debug},
    )


def _structure_break_v8(runtime: "MicrostructureRuntime", inp: KabuExitEvalInput) -> bool:
    vd = _vwap_dist(inp)
    vwap_fail_persist = (
        runtime.below_vwap_seen
        and not runtime.reclaim_persistent()
        and (vd is None or vd < VWAP_RECLAIM_FAIL)
    )
    mom_neg = runtime.momentum_negative_streak >= STRUCTURE_MOM_STREAK
    imb_persist = runtime.max_imbalance_collapse_streak >= STRUCTURE_IMB_STREAK
    fav_gone = not runtime.favorable_persistent() and runtime.max_favorable_pct < FAV_PERSISTENCE_MIN
    adv_persist = runtime.adverse_persistence_count >= 5
    return vwap_fail_persist and mom_neg and imb_persist and fav_gone and adv_persist


def _imb_sustained_with_adverse(runtime: "MicrostructureRuntime") -> bool:
    return (
        runtime.imb_weak_sustained_ticks >= IMB_SUSTAINED_TICKS
        and runtime.adverse_persistence_count >= 4
    )


def evaluate_momentum_v8_exit(
    profile: str,
    inp: KabuExitEvalInput,
    *,
    cfg: Optional[KabuExitV1Config] = None,
    runtime: Optional["MicrostructureRuntime"] = None,
) -> KabuExitEvalResult:
    base_cfg = replace(cfg or KabuExitV1Config(), imb_low_streak_required=IMB_STREAK)
    entry = float(inp.entry_price)
    price = float(inp.current_price)
    peak = float(inp.high_since_entry)
    unrealized = _pct_change(price, entry)
    mfe = _pct_change(peak, entry)
    elapsed = _elapsed_sec(inp)

    res = evaluate_kabu_exit_v1(inp, has_position=True, cfg=base_cfg)

    if res.would_exit and res.exit_reason == "hard_stop":
        return res

    if runtime and elapsed >= DELAY_IMB_SEC:
        vd = _vwap_dist(inp)
        if vd is not None and vd <= VWAP_COLLAPSE_PCT and not runtime.reclaim_persistent():
            return _custom_exit(
                res,
                reason="vwap_collapse_exit",
                unrealized=unrealized,
                mfe=mfe,
                debug={"v8_vwap_collapse": True},
            )

    if runtime and mfe >= TAKE_MFE and peak > entry:
        gb = ((peak - price) / peak) * 100.0
        if gb >= TAKE_GIVEBACK and unrealized > 0:
            return _custom_exit(
                res,
                reason="structural_take_giveback",
                unrealized=unrealized,
                mfe=mfe,
                debug={"v8_take": True},
            )

    if runtime and profile in V8_RECLAIM_PROFILES and elapsed >= DELAY_IMB_SEC:
        if runtime.reclaim_failure_ticks >= 3 and not runtime.reclaim_persistent():
            return _custom_exit(
                res,
                reason="reclaim_failure_exit",
                unrealized=unrealized,
                mfe=mfe,
                debug={
                    "reclaim_persist": runtime.reclaim_persist_ticks,
                    "reclaim_fail": runtime.reclaim_failure_ticks,
                },
            )

    if runtime and profile in V8_FAVORABLE_PROFILES and elapsed >= DELAY_IMB_SEC:
        if runtime.favorable_faded():
            return _custom_exit(
                res,
                reason="favorable_fade_exit",
                unrealized=unrealized,
                mfe=mfe,
                debug={"fade_ticks": runtime.favorable_fade_ticks},
            )

    if (
        runtime
        and profile in V8_STRUCTURE_PROFILES
        and elapsed >= DELAY_IMB_SEC
        and _structure_break_v8(runtime, inp)
    ):
        return _custom_exit(
            res,
            reason="structure_break_v8",
            unrealized=unrealized,
            mfe=mfe,
            debug={"v8_structure": True},
        )

    if res.would_exit and res.exit_reason == "breakout_failure":
        if runtime and (
            runtime.reclaim_persistent()
            or runtime.favorable_persistent()
            or runtime.recovery_then_trend()
        ):
            runtime.recovery_hold_events += 1
            return _no_exit(res, tag="bf_v8_persistence_hold")
        if profile in V8_STRUCTURE_PROFILES:
            return _no_exit(res, tag="bf_defer_structure")
        return res

    if res.would_exit and res.exit_reason == "board_imbalance_deterioration":
        streak = int(inp.imbalance_low_streak or 0)

        if profile in V8_DELAYED_IMB_PROFILES | V8_STRUCTURE_PROFILES:
            if elapsed < DELAY_IMB_SEC:
                if runtime:
                    runtime.recovery_hold_events += 1
                return _no_exit(res, tag="imb_before_60s")

            if streak < IMB_STREAK:
                if runtime:
                    runtime.recovery_hold_events += 1
                return _no_exit(res, tag="imb_streak")

            if runtime and profile in V8_DELAYED_IMB_PROFILES:
                if not _imb_sustained_with_adverse(runtime):
                    runtime.recovery_hold_events += 1
                    return _no_exit(res, tag="imb_transient")

        if runtime and profile in V8_RECLAIM_PROFILES and runtime.reclaim_persistent():
            runtime.recovery_hold_events += 1
            return _no_exit(res, tag="imb_reclaim_hold")

        if runtime and profile in V8_FAVORABLE_PROFILES and runtime.favorable_persistent():
            runtime.recovery_hold_events += 1
            return _no_exit(res, tag="imb_fav_hold")

        if runtime and runtime.recovery_then_trend():
            runtime.recovery_hold_events += 1
            return _no_exit(res, tag="imb_recovery_trend")

        if profile in V8_STRUCTURE_PROFILES:
            return _no_exit(res, tag="imb_structure_only")

    return res
