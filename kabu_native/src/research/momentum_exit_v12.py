"""
Phase 34: bullish continuation prioritization EXIT v12 (Logic Lab only).
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

V12_BULLISH_PROFILES = frozenset(
    {"momentum_volume_v12_bullish_continuation", "momentum_volume_v12_combined"}
)
V12_DECAY_PROFILES = frozenset(
    {"momentum_volume_v12_decay_exit", "momentum_volume_v12_combined"}
)
V12_BEARISH_PROFILES = frozenset(
    {"momentum_volume_v12_bearish_accumulation", "momentum_volume_v12_combined"}
)
V12_COMBINED_PROFILE = "momentum_volume_v12_combined"

IMB_STREAK = 4
TAKE_MFE = 0.30
TAKE_GIVEBACK = 0.18


def uses_momentum_v12_exit(profile: str) -> bool:
    return profile.startswith("momentum_volume_v12_")


def _pct_change(current: float, base: float) -> float:
    if base <= 0:
        return 0.0
    return ((float(current) - float(base)) / float(base)) * 100.0


def _no_exit(base: KabuExitEvalResult, tag: str = "") -> KabuExitEvalResult:
    dbg = dict(base.exit_debug)
    if tag:
        dbg["v12_suppressed"] = tag
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


def evaluate_momentum_v12_exit(
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
    bc = runtime.continuation_engine if runtime is not None else None

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
                debug={"v12_take": True},
            )

    if bc is not None:
        combined = profile == V12_COMBINED_PROFILE

        if (
            profile in V12_DECAY_PROFILES or combined
        ) and bc.decay_exit_ready():
            bc.continuation_exit_signals += 1
            return _custom_exit(
                res,
                reason="bullish_continuation_decay_exit",
                unrealized=unrealized,
                mfe=mfe,
                debug={"cont_decay": True, "cont_score": bc.bullish_continuation_score},
            )

        if (
            profile in V12_BEARISH_PROFILES or combined
        ) and bc.bearish_accumulation_exit_ready():
            bc.continuation_exit_signals += 1
            return _custom_exit(
                res,
                reason="bearish_accumulation_exit",
                unrealized=unrealized,
                mfe=mfe,
                debug={"bear_accum": bc.bearish_accumulation_score},
            )

        if combined and bc.structure_deterioration_exit_ready():
            bc.continuation_exit_signals += 1
            return _custom_exit(
                res,
                reason="structure_deterioration_persistence_exit",
                unrealized=unrealized,
                mfe=mfe,
                debug={"struct_det": bc.structure_deterioration_score},
            )

        if combined and bc.continuation_loss_exit_ready():
            bc.continuation_exit_signals += 1
            return _custom_exit(
                res,
                reason="bullish_continuation_loss_exit",
                unrealized=unrealized,
                mfe=mfe,
                debug={"cont_loss": True, "cont_dur": bc.max_continuation_duration},
            )

    hold = bc is not None and bc.should_hold_continuation()

    if res.would_exit and res.exit_reason == "breakout_failure":
        if hold:
            if bc:
                bc.continuation_hold_events += 1
            return _no_exit(res, tag="bf_continuation_hold")

    if res.would_exit and res.exit_reason == "board_imbalance_deterioration":
        streak = int(inp.imbalance_low_streak or 0)
        if hold:
            if bc:
                bc.continuation_hold_events += 1
            return _no_exit(res, tag="imb_continuation_hold")
        if bc and bc.short_bearish_noise:
            if bc:
                bc.continuation_hold_events += 1
            return _no_exit(res, tag="imb_short_bear_noise")
        if streak < IMB_STREAK:
            if bc:
                bc.continuation_hold_events += 1
            return _no_exit(res, tag="imb_streak")
        if profile in V12_BULLISH_PROFILES and bc and bc.bullish_continuation_score >= 0.48:
            bc.continuation_hold_events += 1
            return _no_exit(res, tag="imb_bullish_cont_hold")

    return res
