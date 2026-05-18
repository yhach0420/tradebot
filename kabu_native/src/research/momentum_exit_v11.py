"""
Phase 33: duration-weighted persistence EXIT v11 (Logic Lab only).
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

V11_BULLISH_PROFILES = frozenset(
    {"momentum_volume_v11_bullish_duration", "momentum_volume_v11_combined"}
)
V11_BEARISH_PROFILES = frozenset(
    {"momentum_volume_v11_bearish_duration", "momentum_volume_v11_combined"}
)
V11_DECAY_PROFILES = frozenset(
    {"momentum_volume_v11_decay_detection", "momentum_volume_v11_combined"}
)
V11_COMBINED_PROFILE = "momentum_volume_v11_combined"

IMB_STREAK = 4
TAKE_MFE = 0.30
TAKE_GIVEBACK = 0.18


def uses_momentum_v11_exit(profile: str) -> bool:
    return profile.startswith("momentum_volume_v11_")


def _pct_change(current: float, base: float) -> float:
    if base <= 0:
        return 0.0
    return ((float(current) - float(base)) / float(base)) * 100.0


def _no_exit(base: KabuExitEvalResult, tag: str = "") -> KabuExitEvalResult:
    dbg = dict(base.exit_debug)
    if tag:
        dbg["v11_suppressed"] = tag
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


def evaluate_momentum_v11_exit(
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
    dw = runtime.duration_engine if runtime is not None else None

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
                debug={"v11_take": True},
            )

    if dw is not None:
        if (
            profile in V11_BEARISH_PROFILES or profile == V11_COMBINED_PROFILE
        ) and dw.collapse_weighted_ready:
            dw.weighted_exit_signals += 1
            return _custom_exit(
                res,
                reason="weighted_collapse_continuation_exit",
                unrealized=unrealized,
                mfe=mfe,
                debug={
                    "bear_w": dw.bearish_weighted_score,
                    "collapse_w": dw.collapse_weighted,
                },
            )

        if (
            profile in V11_DECAY_PROFILES or profile == V11_COMBINED_PROFILE
        ) and dw.decay_exit_ready():
            dw.weighted_exit_signals += 1
            return _custom_exit(
                res,
                reason="weighted_bullish_decay_exit",
                unrealized=unrealized,
                mfe=mfe,
                debug={"bull_decay": True, "bear_w": dw.bearish_weighted_score},
            )

        if (
            profile in V11_BEARISH_PROFILES or profile == V11_COMBINED_PROFILE
        ) and dw.structure_break_weighted_ready:
            dw.weighted_exit_signals += 1
            return _custom_exit(
                res,
                reason="weighted_structure_break_exit",
                unrealized=unrealized,
                mfe=mfe,
                debug={"bear_w": dw.bearish_weighted_score},
            )

        if dw.bearish_weighted_exit_ready() and (
            profile in V11_BEARISH_PROFILES or profile == V11_COMBINED_PROFILE
        ):
            dw.weighted_exit_signals += 1
            return _custom_exit(
                res,
                reason="weighted_bearish_persistence_exit",
                unrealized=unrealized,
                mfe=mfe,
                debug={
                    "bull_w": dw.bullish_weighted_score,
                    "bear_w": dw.bearish_weighted_score,
                },
            )

    hold = dw is not None and dw.should_hold()

    if res.would_exit and res.exit_reason == "breakout_failure":
        if hold:
            if dw:
                dw.weighted_hold_events += 1
            return _no_exit(res, tag="bf_weighted_hold")
        return res

    if res.would_exit and res.exit_reason == "board_imbalance_deterioration":
        streak = int(inp.imbalance_low_streak or 0)
        if hold:
            if dw:
                dw.weighted_hold_events += 1
            return _no_exit(res, tag="imb_weighted_hold")
        if dw and dw.short_bearish_noise:
            dw.weighted_hold_events += 1
            return _no_exit(res, tag="imb_short_bear_noise")
        if streak < IMB_STREAK:
            if dw:
                dw.weighted_hold_events += 1
            return _no_exit(res, tag="imb_streak")
        if profile in V11_BULLISH_PROFILES and dw and dw.bullish_weighted_score >= 0.45:
            dw.weighted_hold_events += 1
            return _no_exit(res, tag="imb_bullish_weight_hold")

    return res
