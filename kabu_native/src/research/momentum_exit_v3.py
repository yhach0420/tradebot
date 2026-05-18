"""
Phase 25: momentum_volume_v3 EXIT / TAKE guards (Logic Lab only).

Market-structure rules only — no per-symbol/day/time tuning.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

from src.kabu_exit_engine import (
    KabuExitEvalInput,
    KabuExitEvalResult,
    KabuExitV1Config,
    evaluate_kabu_exit_v1,
)

MOMENTUM_V3_EXIT_PROFILES = frozenset(
    {
        "momentum_volume_v3_exit_guard",
        "momentum_volume_v3_combined",
    }
)
MOMENTUM_V3_TAKE_PROFILES = frozenset(
    {
        "momentum_volume_v3_take_guard",
        "momentum_volume_v3_combined",
    }
)

# Fixed structural thresholds (global)
TAKE_MFE_MIN_PCT = 0.30
TAKE_GIVEBACK_FROM_PEAK_PCT = 0.18
IMB_STREAK_DEFAULT = 5
IMB_STREAK_WITH_MFE = 7
IMB_MFE_RELAX_THRESHOLD_PCT = 0.25
IMB_UNREALIZED_MAX_WITH_MFE = 0.10


def uses_momentum_v3_exit(profile: str) -> bool:
    return profile in MOMENTUM_V3_EXIT_PROFILES | MOMENTUM_V3_TAKE_PROFILES


def _pct_change(current: float, base: float) -> Optional[float]:
    if base <= 0:
        return None
    return ((float(current) - float(base)) / float(base)) * 100.0


def _no_exit(inp: KabuExitEvalInput, base: KabuExitEvalResult) -> KabuExitEvalResult:
    return KabuExitEvalResult(
        would_exit=False,
        exit_reason="",
        exit_priority=0,
        unrealized_pct=base.unrealized_pct,
        mfe_pct=base.mfe_pct,
        elapsed_min=base.elapsed_min,
        exit_thresholds_used=dict(base.exit_thresholds_used),
        exit_debug={**base.exit_debug, "v3_suppressed": True},
    )


def evaluate_momentum_v3_exit(
    profile: str,
    inp: KabuExitEvalInput,
    *,
    cfg: Optional[KabuExitV1Config] = None,
) -> KabuExitEvalResult:
    """Evaluate exit with v3 imbalance / structural-take overrides."""
    base_cfg = cfg or KabuExitV1Config()
    tuned = base_cfg
    if profile in MOMENTUM_V3_EXIT_PROFILES:
        tuned = replace(
            base_cfg,
            imb_low_streak_required=IMB_STREAK_DEFAULT,
            imb_exit_max_pnl_pct=max(base_cfg.imb_exit_max_pnl_pct, 0.50),
        )

    res = evaluate_kabu_exit_v1(inp, has_position=True, cfg=tuned)
    entry = float(inp.entry_price)
    price = float(inp.current_price)
    peak = float(inp.high_since_entry)
    unrealized = _pct_change(price, entry) or 0.0
    mfe = (
        float(res.mfe_pct)
        if res.mfe_pct is not None
        else (_pct_change(peak, entry) or 0.0)
    )

    if res.would_exit and res.exit_reason == "hard_stop":
        return res

    if profile in MOMENTUM_V3_TAKE_PROFILES and peak > entry:
        giveback = ((peak - price) / peak) * 100.0
        if mfe >= TAKE_MFE_MIN_PCT and giveback >= TAKE_GIVEBACK_FROM_PEAK_PCT and unrealized > 0:
            return KabuExitEvalResult(
                would_exit=True,
                exit_reason="structural_take_giveback",
                exit_priority=4,
                unrealized_pct=unrealized,
                mfe_pct=mfe,
                elapsed_min=res.elapsed_min,
                exit_thresholds_used=dict(res.exit_thresholds_used),
                exit_debug={
                    **res.exit_debug,
                    "v3_take": True,
                    "giveback_from_peak_pct": giveback,
                    "take_mfe_min_pct": TAKE_MFE_MIN_PCT,
                },
            )

    if not res.would_exit:
        return res

    if res.exit_reason == "breakout_failure":
        return res

    if res.exit_reason == "board_imbalance_deterioration" and profile in MOMENTUM_V3_EXIT_PROFILES:
        streak = int(inp.imbalance_low_streak or 0)
        if mfe >= IMB_MFE_RELAX_THRESHOLD_PCT:
            if streak < IMB_STREAK_WITH_MFE or unrealized >= IMB_UNREALIZED_MAX_WITH_MFE:
                return _no_exit(inp, res)
        elif streak < IMB_STREAK_DEFAULT:
            return _no_exit(inp, res)

    return res
