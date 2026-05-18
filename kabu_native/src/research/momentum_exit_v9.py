"""
Phase 31: state-based persistence EXIT v9 (Logic Lab only).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timezone
from typing import TYPE_CHECKING, Optional

from src.kabu_exit_engine import (
    KabuExitEvalInput,
    KabuExitEvalResult,
    KabuExitV1Config,
    evaluate_kabu_exit_v1,
)

if TYPE_CHECKING:
    from research.microstructure_runtime import MicrostructureRuntime

V9_STATE_PROFILES = frozenset(
    {"momentum_volume_v9_state_persistence", "momentum_volume_v9_combined"}
)
V9_STRUCTURE_PROFILES = frozenset(
    {"momentum_volume_v9_structure_break_state", "momentum_volume_v9_combined"}
)
V9_RECOVERY_PROFILES = frozenset(
    {"momentum_volume_v9_recovery_state", "momentum_volume_v9_combined"}
)
V9_COMBINED_PROFILE = "momentum_volume_v9_combined"

IMB_STREAK = 4
TAKE_MFE = 0.30
TAKE_GIVEBACK = 0.18


def uses_momentum_v9_exit(profile: str) -> bool:
    return profile.startswith("momentum_volume_v9_")


def _pct_change(current: float, base: float) -> float:
    if base <= 0:
        return 0.0
    return ((float(current) - float(base)) / float(base)) * 100.0


def _vwap_dist(inp: KabuExitEvalInput) -> Optional[float]:
    if inp.current_vwap and float(inp.current_vwap) > 0:
        return _pct_change(float(inp.current_price), float(inp.current_vwap))
    return None


def _no_exit(base: KabuExitEvalResult, tag: str = "") -> KabuExitEvalResult:
    dbg = dict(base.exit_debug)
    if tag:
        dbg["v9_suppressed"] = tag
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


def evaluate_momentum_v9_exit(
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
    eng = runtime.state_engine if runtime is not None else None

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
                debug={"v9_take": True},
            )

    if eng is not None:
        if profile in V9_STRUCTURE_PROFILES and eng.structure_break_ready():
            eng.state_exit_signals += 1
            return _custom_exit(
                res,
                reason="state_structure_break_exit",
                unrealized=unrealized,
                mfe=mfe,
                debug={
                    "bearish": eng.bearish_instant,
                    "structure_ticks": eng.structure_break_persist_ticks,
                },
            )

        if profile in V9_RECOVERY_PROFILES and eng.recovery_failed_exit_ready():
            eng.state_exit_signals += 1
            return _custom_exit(
                res,
                reason="state_recovery_fail_exit",
                unrealized=unrealized,
                mfe=mfe,
                debug={"recovery_ticks": eng.recovery_persist_ticks},
            )

        if eng.bearish_exit_ready() and (
            profile in V9_STATE_PROFILES or profile == V9_COMBINED_PROFILE
        ):
            eng.state_exit_signals += 1
            return _custom_exit(
                res,
                reason="state_bearish_persistence_exit",
                unrealized=unrealized,
                mfe=mfe,
                debug={
                    "bullish": eng.bullish_instant,
                    "bearish": eng.bearish_instant,
                    "bear_ticks": eng.bearish_persist_ticks,
                },
            )

    hold = eng is not None and eng.should_hold()

    if res.would_exit and res.exit_reason == "breakout_failure":
        if hold:
            if eng:
                eng.state_hold_events += 1
            return _no_exit(res, tag="bf_state_hold")
        if profile in V9_STRUCTURE_PROFILES:
            return _no_exit(res, tag="bf_structure_defer")
        return res

    if res.would_exit and res.exit_reason == "board_imbalance_deterioration":
        streak = int(inp.imbalance_low_streak or 0)
        if hold:
            if eng:
                eng.state_hold_events += 1
            return _no_exit(res, tag="imb_state_hold")
        if streak < IMB_STREAK:
            if eng:
                eng.state_hold_events += 1
            return _no_exit(res, tag="imb_streak")
        if profile in V9_STRUCTURE_PROFILES:
            return _no_exit(res, tag="imb_structure_only")

    return res
