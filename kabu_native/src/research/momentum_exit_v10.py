"""
Phase 32: state transition EXIT v10 (Logic Lab only).
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Optional

from src.kabu_exit_engine import (
    KabuExitEvalInput,
    KabuExitEvalResult,
    KabuExitV1Config,
    evaluate_kabu_exit_v1,
)

if TYPE_CHECKING:
    from research.microstructure_runtime import MicrostructureRuntime

V10_TRANSITION_PROFILES = frozenset(
    {"momentum_volume_v10_transition_persistence", "momentum_volume_v10_combined"}
)
V10_RECOVERY_PROFILES = frozenset(
    {"momentum_volume_v10_recovery_transition", "momentum_volume_v10_combined"}
)
V10_STRUCTURE_PROFILES = frozenset(
    {"momentum_volume_v10_structure_transition", "momentum_volume_v10_combined"}
)
V10_COMBINED_PROFILE = "momentum_volume_v10_combined"

IMB_STREAK = 4
TAKE_MFE = 0.30
TAKE_GIVEBACK = 0.18


def uses_momentum_v10_exit(profile: str) -> bool:
    return profile.startswith("momentum_volume_v10_")


def _pct_change(current: float, base: float) -> float:
    if base <= 0:
        return 0.0
    return ((float(current) - float(base)) / float(base)) * 100.0


def _no_exit(base: KabuExitEvalResult, tag: str = "") -> KabuExitEvalResult:
    dbg = dict(base.exit_debug)
    if tag:
        dbg["v10_suppressed"] = tag
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


def evaluate_momentum_v10_exit(
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

    res = evaluate_kabu_exit_v1(inp, has_position=True, cfg=base_cfg)
    trans = runtime.transition_engine if runtime is not None else None

    if res.would_exit and res.exit_reason == "hard_stop":
        return res

    if runtime and mfe >= TAKE_MFE and peak > entry:
        gb = ((peak - price) / peak) * 100.0
        if gb >= TAKE_GIVEBACK and unrealized > 0:
            return _custom_exit(
                res,
                reason="structural_take_giveback",
                unrealized=unrealized,
                mfe=mfe,
                debug={"v10_take": True},
            )

    if trans is not None:
        if profile in V10_STRUCTURE_PROFILES and trans.collapse_exit_ready():
            trans.transition_exit_signals += 1
            return _custom_exit(
                res,
                reason="transition_collapse_exit",
                unrealized=unrealized,
                mfe=mfe,
                debug={
                    "collapse_score": trans.collapse_transition_score,
                    "bear_ticks": trans.collapse_bearish_ticks,
                },
            )

        if profile in V10_RECOVERY_PROFILES and trans.recovery_failure_exit_ready():
            trans.transition_exit_signals += 1
            return _custom_exit(
                res,
                reason="transition_recovery_failure_exit",
                unrealized=unrealized,
                mfe=mfe,
                debug={"recovery_phase": trans.recovery_phase},
            )

        if trans.bearish_continuation_exit_ready() and (
            profile in V10_TRANSITION_PROFILES or profile == V10_COMBINED_PROFILE
        ):
            trans.transition_exit_signals += 1
            return _custom_exit(
                res,
                reason="transition_bearish_continuation_exit",
                unrealized=unrealized,
                mfe=mfe,
                debug={
                    "bearish_locked": trans.bearish_locked,
                    "velocity": trans.bullish_to_bearish_velocity,
                },
            )

    hold = trans is not None and trans.should_hold()

    if res.would_exit and res.exit_reason == "breakout_failure":
        if hold:
            if trans:
                trans.transition_hold_events += 1
            return _no_exit(res, tag="bf_transition_hold")
        if profile in V10_STRUCTURE_PROFILES:
            return _no_exit(res, tag="bf_structure_defer")
        return res

    if res.would_exit and res.exit_reason == "board_imbalance_deterioration":
        streak = int(inp.imbalance_low_streak or 0)
        if hold:
            if trans:
                trans.transition_hold_events += 1
            return _no_exit(res, tag="imb_transition_hold")
        if streak < IMB_STREAK:
            if trans:
                trans.transition_hold_events += 1
            return _no_exit(res, tag="imb_streak")
        if profile in V10_STRUCTURE_PROFILES:
            return _no_exit(res, tag="imb_structure_only")
        if trans and trans.recovery_transition_active:
            trans.transition_hold_events += 1
            return _no_exit(res, tag="imb_recovery_transition")

    return res
