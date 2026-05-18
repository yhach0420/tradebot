"""
Phase 26: momentum_volume_v4 post-entry protection (Logic Lab only).

Market-structure guards — no per-symbol/day/time rules.
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
    from research.momentum_early_move import EarlyMoveRuntime

MOMENTUM_V4_EARLY_GUARD_PROFILES = frozenset(
    {"momentum_volume_v4_early_guard", "momentum_volume_v4_combined"}
)
MOMENTUM_V4_RECOVERY_PROFILES = frozenset(
    {"momentum_volume_v4_recovery_guard", "momentum_volume_v4_combined"}
)
MOMENTUM_V4_IMB_CONFIRM_PROFILES = frozenset(
    {"momentum_volume_v4_imbalance_confirm", "momentum_volume_v4_combined"}
)

# Global structural thresholds
EARLY_GUARD_MIN_ELAPSED_SEC = 60.0
EARLY_GUARD_ADV_PCT = -0.12
EARLY_GUARD_IMB_DROP = 0.04
EARLY_GUARD_VWAP_RECLAIM = -0.04
EARLY_GUARD_MOM_STALL_PCT = -0.06

RECOVERY_MAX_ADV_PCT = -0.20
RECOVERY_MIN_MFE_PCT = 0.12
RECOVERY_UNREALIZED_FLOOR = -0.35

IMB_CONFIRM_STREAK = 5
IMB_CONFIRM_ADV_PCT = -0.08
IMB_CONFIRM_MFE_RELAX = 0.20


def uses_momentum_v4_exit(profile: str) -> bool:
    return profile.startswith("momentum_volume_v4_")


def _pct_change(current: float, base: float) -> float:
    if base <= 0:
        return 0.0
    return ((float(current) - float(base)) / float(base)) * 100.0


def _no_exit(base: KabuExitEvalResult) -> KabuExitEvalResult:
    return KabuExitEvalResult(
        would_exit=False,
        exit_reason="",
        exit_priority=0,
        unrealized_pct=base.unrealized_pct,
        mfe_pct=base.mfe_pct,
        elapsed_min=base.elapsed_min,
        exit_thresholds_used=dict(base.exit_thresholds_used),
        exit_debug={**base.exit_debug, "v4_suppressed": True},
    )


def _early_adverse_score(runtime: "EarlyMoveRuntime", inp: KabuExitEvalInput) -> float:
    score = 0.0
    if runtime.max_adverse_pct <= EARLY_GUARD_ADV_PCT:
        score += 1.0
    imb_drop = 0.0
    if runtime.entry_imbalance is not None and inp.board_imbalance is not None:
        imb_drop = float(runtime.entry_imbalance) - float(inp.board_imbalance)
        if imb_drop >= EARLY_GUARD_IMB_DROP:
            score += 1.0
    vwap_now = None
    if inp.current_vwap is not None and float(inp.current_vwap) > 0:
        vwap_now = _pct_change(float(inp.current_price), float(inp.current_vwap))
    if vwap_now is not None and vwap_now <= EARLY_GUARD_VWAP_RECLAIM:
        score += 1.0
    if runtime.max_adverse_pct <= EARLY_GUARD_MOM_STALL_PCT:
        score += 0.5
    return score


def evaluate_momentum_v4_exit(
    profile: str,
    inp: KabuExitEvalInput,
    *,
    cfg: Optional[KabuExitV1Config] = None,
    runtime: Optional["EarlyMoveRuntime"] = None,
) -> KabuExitEvalResult:
    base_cfg = cfg or KabuExitV1Config()
    tuned = replace(base_cfg, imb_low_streak_required=IMB_CONFIRM_STREAK)

    entry = float(inp.entry_price)
    price = float(inp.current_price)
    peak = float(inp.high_since_entry)
    unrealized = _pct_change(price, entry)
    mfe = _pct_change(peak, entry)
    e = inp.entry_time.astimezone(timezone.utc)
    n = inp.now_time.astimezone(timezone.utc)
    elapsed_sec = max(0.0, (n - e).total_seconds())

    res = evaluate_kabu_exit_v1(inp, has_position=True, cfg=tuned)

    if res.would_exit and res.exit_reason == "hard_stop":
        if profile in MOMENTUM_V4_RECOVERY_PROFILES and runtime is not None:
            if (
                unrealized > RECOVERY_UNREALIZED_FLOOR
                and mfe >= RECOVERY_MIN_MFE_PCT
                and runtime.max_adverse_pct > RECOVERY_MAX_ADV_PCT
                and runtime.recovered_after_adverse
            ):
                return _no_exit(res)
        return res

    if runtime is not None and profile in MOMENTUM_V4_EARLY_GUARD_PROFILES:
        if elapsed_sec >= EARLY_GUARD_MIN_ELAPSED_SEC:
            if _early_adverse_score(runtime, inp) >= 2.5:
                return KabuExitEvalResult(
                    would_exit=True,
                    exit_reason="early_adverse_guard",
                    exit_priority=3,
                    unrealized_pct=unrealized,
                    mfe_pct=mfe,
                    elapsed_min=res.elapsed_min,
                    exit_thresholds_used=dict(res.exit_thresholds_used),
                    exit_debug={
                        "v4_early_guard": True,
                        "early_score": _early_adverse_score(runtime, inp),
                        "max_adverse_pct": runtime.max_adverse_pct,
                    },
                )

    if not res.would_exit:
        return res

    if res.exit_reason == "breakout_failure":
        return res

    if res.exit_reason == "board_imbalance_deterioration":
        streak = int(inp.imbalance_low_streak or 0)
        adv = runtime.max_adverse_pct if runtime is not None else unrealized

        if profile in MOMENTUM_V4_RECOVERY_PROFILES and mfe >= IMB_CONFIRM_MFE_RELAX:
            if runtime and runtime.recovered_after_adverse:
                return _no_exit(res)

        if profile in MOMENTUM_V4_IMB_CONFIRM_PROFILES | MOMENTUM_V4_EARLY_GUARD_PROFILES:
            if streak < IMB_CONFIRM_STREAK:
                return _no_exit(res)
            if adv > IMB_CONFIRM_ADV_PCT and mfe < IMB_CONFIRM_MFE_RELAX:
                return _no_exit(res)
            if profile in MOMENTUM_V4_IMB_CONFIRM_PROFILES and adv > IMB_CONFIRM_ADV_PCT:
                return _no_exit(res)

    return res
