"""
Phase 28: microstructure-adaptive EXIT v6 (Logic Lab only).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timezone
from typing import TYPE_CHECKING, Optional

from research.microstructure_runtime import (
    FAV_PERSISTENCE_MIN,
    IMB_COLLAPSE_DELTA,
    IMB_QUEUE_WEAK,
    NOISE_ADV_TOLERANCE,
    SPREAD_EXPANSION_SEVERE,
    VWAP_RECLAIM_FAIL,
)
from src.kabu_exit_engine import (
    KabuExitEvalInput,
    KabuExitEvalResult,
    KabuExitV1Config,
    evaluate_kabu_exit_v1,
)

if TYPE_CHECKING:
    from research.microstructure_runtime import MicrostructureRuntime

V6_NOISE_PROFILES = frozenset(
    {"momentum_volume_v6_noise_tolerant", "momentum_volume_v6_combined"}
)
V6_STRUCTURE_PROFILES = frozenset(
    {"momentum_volume_v6_structure_break", "momentum_volume_v6_combined"}
)
V6_RECOVERY_PROFILES = frozenset(
    {"momentum_volume_v6_recovery_bias", "momentum_volume_v6_combined"}
)

DELAY_IMB_SEC = 60.0
IMB_STREAK = 4
STRUCTURE_BREAK_SCORE_MIN = 0.55
FAKE_BREAKOUT_SCORE_MIN = 0.55
TAKE_MFE = 0.30
TAKE_GIVEBACK = 0.18


def uses_momentum_v6_exit(profile: str) -> bool:
    return profile.startswith("momentum_volume_v6_")


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
        dbg["v6_suppressed"] = tag
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


def _recovery_bias_hold(runtime: "MicrostructureRuntime", inp: KabuExitEvalInput) -> bool:
    price = float(inp.current_price)
    mom = runtime.current_momentum_pct(price)
    vd = _vwap_dist(inp)
    if runtime.recovered_after_adverse or runtime.vwap_reclaim_achieved:
        return True
    if mom >= 0 and runtime.max_favorable_pct >= FAV_PERSISTENCE_MIN * 0.5:
        return True
    if vd is not None and vd > 0.02:
        return True
    if runtime.max_adverse_pct > NOISE_ADV_TOLERANCE:
        return True
    return False


def _noise_tolerant_exit(runtime: "MicrostructureRuntime", inp: KabuExitEvalInput) -> bool:
    spread_bad = runtime.spread_expansion_ratio >= SPREAD_EXPANSION_SEVERE
    imb_bad = (
        runtime.imbalance_collapse_streak >= 3
        or (
            inp.board_imbalance is not None
            and float(inp.board_imbalance) < IMB_QUEUE_WEAK
        )
    )
    mom_bad = runtime.momentum_negative_streak >= 4
    adv_bad = runtime.max_adverse_pct <= NOISE_ADV_TOLERANCE - 0.04
    return spread_bad and imb_bad and mom_bad and adv_bad


def evaluate_momentum_v6_exit(
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
    vd = _vwap_dist(inp)

    res = evaluate_kabu_exit_v1(inp, has_position=True, cfg=base_cfg)

    if res.would_exit and res.exit_reason == "hard_stop":
        return res
    if res.would_exit and res.exit_reason == "breakout_failure":
        if runtime and runtime.vwap_reclaim_achieved and mfe >= 0.08:
            return _no_exit(res, tag="bf_recovery_hold")
        return res

    if runtime and mfe >= TAKE_MFE and peak > entry:
        gb = ((peak - price) / peak) * 100.0
        if gb >= TAKE_GIVEBACK and unrealized > 0:
            return _custom_exit(
                res,
                reason="structural_take_giveback",
                unrealized=unrealized,
                mfe=mfe,
                debug={"v6_take": True},
            )

    if runtime and profile in V6_RECOVERY_PROFILES and _recovery_bias_hold(runtime, inp):
        if res.would_exit and res.exit_reason == "board_imbalance_deterioration":
            runtime.recovery_hold_events += 1
            return _no_exit(res, tag="recovery_bias_hold")

    if runtime and elapsed < DELAY_IMB_SEC:
        if res.would_exit and res.exit_reason == "board_imbalance_deterioration":
            runtime.recovery_hold_events += 1
            return _no_exit(res, tag="imb_noise_window")

    if runtime and profile in V6_NOISE_PROFILES | V6_STRUCTURE_PROFILES:
        runtime.compute_scores()
        if runtime.fake_breakout_score >= FAKE_BREAKOUT_SCORE_MIN and elapsed <= 120:
            return _custom_exit(
                res,
                reason="fake_breakout_exit",
                unrealized=unrealized,
                mfe=mfe,
                debug={"fake_score": runtime.fake_breakout_score},
            )
        if profile in V6_STRUCTURE_PROFILES and runtime.structure_break_score >= STRUCTURE_BREAK_SCORE_MIN:
            return _custom_exit(
                res,
                reason="structure_break_exit",
                unrealized=unrealized,
                mfe=mfe,
                debug={"structure_score": runtime.structure_break_score},
            )
        if profile in V6_NOISE_PROFILES and _noise_tolerant_exit(runtime, inp):
            return _custom_exit(
                res,
                reason="microstructure_noise_exit",
                unrealized=unrealized,
                mfe=mfe,
                debug={"spread_ratio": runtime.spread_expansion_ratio},
            )

    if res.would_exit and res.exit_reason == "board_imbalance_deterioration":
        streak = int(inp.imbalance_low_streak or 0)
        if elapsed < DELAY_IMB_SEC:
            if runtime:
                runtime.recovery_hold_events += 1
            return _no_exit(res, tag="imb_delayed")
        if streak < IMB_STREAK:
            if runtime:
                runtime.recovery_hold_events += 1
            return _no_exit(res, tag="imb_streak")
        if runtime and _recovery_bias_hold(runtime, inp) and mfe < TAKE_MFE:
            runtime.recovery_hold_events += 1
            return _no_exit(res, tag="imb_recovery_hold")
        if vd is not None and vd > VWAP_RECLAIM_FAIL and runtime and runtime.favorable_persistence_count > 2:
            runtime.recovery_hold_events += 1
            return _no_exit(res, tag="imb_vwap_hold")

    return res
