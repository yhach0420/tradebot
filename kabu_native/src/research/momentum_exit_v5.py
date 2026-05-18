"""
Phase 27: Recovery-based EXIT v5 (Logic Lab only).

Cut only non-recoverable early adverse moves; delay imbalance EXIT; hold recoveries.
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

MOMENTUM_V5_RECOVERY_EXIT_PROFILES = frozenset(
    {"momentum_volume_v5_recovery_exit", "momentum_volume_v5_combined"}
)
MOMENTUM_V5_DELAYED_IMB_PROFILES = frozenset(
    {"momentum_volume_v5_delayed_imbalance_exit", "momentum_volume_v5_combined"}
)
MOMENTUM_V5_RECOVERY_OR_CUT_PROFILES = frozenset(
    {"momentum_volume_v5_recovery_or_cut", "momentum_volume_v5_combined"}
)

# Global structural thresholds
DELAY_IMB_SEC = 60.0
RECOVERY_OR_CUT_MIN_SEC = 60.0
RECOVERY_OR_CUT_MAX_SEC = 90.0
EARLY_CUT_MAX_ADV = -0.12
EARLY_CUT_MAX_FAV = 0.03
HOLD_MIN_FAV = 0.05
CUT_ADV_PCT = -0.08
IMB_STREAK_AFTER_60 = 3
TAKE_MFE_MIN_PCT = 0.30
TAKE_GIVEBACK_PCT = 0.18


def uses_momentum_v5_exit(profile: str) -> bool:
    return profile.startswith("momentum_volume_v5_")


def _pct_change(current: float, base: float) -> float:
    if base <= 0:
        return 0.0
    return ((float(current) - float(base)) / float(base)) * 100.0


def _elapsed_sec(inp: KabuExitEvalInput) -> float:
    e = inp.entry_time.astimezone(timezone.utc)
    n = inp.now_time.astimezone(timezone.utc)
    return max(0.0, (n - e).total_seconds())


def _vwap_dist_now(inp: KabuExitEvalInput) -> Optional[float]:
    if inp.current_vwap is not None and float(inp.current_vwap) > 0:
        return _pct_change(float(inp.current_price), float(inp.current_vwap))
    return None


def _no_exit(base: KabuExitEvalResult, *, tag: str = "") -> KabuExitEvalResult:
    dbg = dict(base.exit_debug)
    if tag:
        dbg["v5_suppressed"] = tag
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


def _exit_result(
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


def _early_cut_conditions(runtime: "EarlyMoveRuntime", inp: KabuExitEvalInput) -> bool:
    price = float(inp.current_price)
    mom = runtime.current_momentum_pct(price)
    vwap_chg = runtime.current_vwap_change(_vwap_dist_now(inp))
    fav = runtime.max_favorable_pct
    adv = runtime.max_adverse_pct
    vwap_declining = vwap_chg is not None and vwap_chg < -0.02
    if runtime.entry_vwap_dist_pct is not None:
        vd = _vwap_dist_now(inp)
        if vd is not None and vd < float(runtime.entry_vwap_dist_pct) - 0.02:
            vwap_declining = True
    return (
        adv <= EARLY_CUT_MAX_ADV
        and mom < 0.0
        and vwap_declining
        and fav < EARLY_CUT_MAX_FAV
    )


def _recovery_or_cut_hold(runtime: "EarlyMoveRuntime", inp: KabuExitEvalInput) -> bool:
    price = float(inp.current_price)
    mom = runtime.current_momentum_pct(price)
    fav = runtime.max_favorable_pct
    vwap_chg = runtime.current_vwap_change(_vwap_dist_now(inp))
    snap60 = runtime.snapshot_60s()
    if snap60:
        fav = max(fav, float(snap60.get("max_favorable_pct") or fav))
    if fav >= HOLD_MIN_FAV:
        return True
    if mom >= 0.0:
        return True
    if vwap_chg is not None and vwap_chg > 0.0:
        return True
    if runtime.recovered_after_adverse:
        return True
    return False


def _recovery_or_cut_fail(runtime: "EarlyMoveRuntime", inp: KabuExitEvalInput) -> bool:
    price = float(inp.current_price)
    mom = runtime.current_momentum_pct(price)
    fav = runtime.max_favorable_pct
    vwap_chg = runtime.current_vwap_change(_vwap_dist_now(inp))
    adv = runtime.max_adverse_pct
    vwap_bad = vwap_chg is not None and vwap_chg < -0.03
    return adv <= CUT_ADV_PCT and fav < HOLD_MIN_FAV and mom < 0.0 and vwap_bad


def evaluate_momentum_v5_exit(
    profile: str,
    inp: KabuExitEvalInput,
    *,
    cfg: Optional[KabuExitV1Config] = None,
    runtime: Optional["EarlyMoveRuntime"] = None,
) -> KabuExitEvalResult:
    base_cfg = cfg or KabuExitV1Config()
    tuned = replace(base_cfg, imb_low_streak_required=IMB_STREAK_AFTER_60)

    entry = float(inp.entry_price)
    price = float(inp.current_price)
    peak = float(inp.high_since_entry)
    unrealized = _pct_change(price, entry)
    mfe = _pct_change(peak, entry)
    elapsed_sec = _elapsed_sec(inp)

    res = evaluate_kabu_exit_v1(inp, has_position=True, cfg=tuned)

    if res.would_exit and res.exit_reason in ("hard_stop", "breakout_failure"):
        return res

    if runtime is not None and profile in MOMENTUM_V5_DELAYED_IMB_PROFILES:
        if mfe >= TAKE_MFE_MIN_PCT and peak > entry:
            giveback = ((peak - price) / peak) * 100.0
            if giveback >= TAKE_GIVEBACK_PCT and unrealized > 0:
                return _exit_result(
                    res,
                    reason="structural_take_giveback",
                    unrealized=unrealized,
                    mfe=mfe,
                    debug={"v5_take": True, "giveback_pct": giveback},
                )

    if runtime is not None and profile in MOMENTUM_V5_RECOVERY_OR_CUT_PROFILES:
        if (
            RECOVERY_OR_CUT_MIN_SEC <= elapsed_sec <= RECOVERY_OR_CUT_MAX_SEC
            and not runtime.recovery_or_cut_evaluated
        ):
            runtime.recovery_or_cut_evaluated = True
            if _recovery_or_cut_hold(runtime, inp):
                runtime.recovery_or_cut_held = True
                runtime.recovery_hold_events += 1
            elif _recovery_or_cut_fail(runtime, inp):
                runtime.early_cut_exited = True
                return _exit_result(
                    res,
                    reason="recovery_or_cut_fail",
                    unrealized=unrealized,
                    mfe=mfe,
                    debug={"v5_recovery_or_cut": True},
                )

    if runtime is not None and profile in MOMENTUM_V5_RECOVERY_EXIT_PROFILES:
        if elapsed_sec <= DELAY_IMB_SEC and _early_cut_conditions(runtime, inp):
            runtime.early_cut_exited = True
            return _exit_result(
                res,
                reason="recovery_early_cut",
                unrealized=unrealized,
                mfe=mfe,
                debug={"v5_recovery_early_cut": True},
            )

    if not res.would_exit:
        return res

    if res.exit_reason == "board_imbalance_deterioration":
        if elapsed_sec < DELAY_IMB_SEC:
            if runtime is not None:
                runtime.recovery_hold_events += 1
            return _no_exit(res, tag="imb_before_60s")

        if profile in MOMENTUM_V5_DELAYED_IMB_PROFILES | MOMENTUM_V5_RECOVERY_EXIT_PROFILES:
            streak = int(inp.imbalance_low_streak or 0)
            if streak < IMB_STREAK_AFTER_60:
                if runtime is not None:
                    runtime.recovery_hold_events += 1
                return _no_exit(res, tag="imb_streak_insufficient")
            if mfe >= TAKE_MFE_MIN_PCT:
                if runtime is not None:
                    runtime.recovery_hold_events += 1
                return _no_exit(res, tag="imb_defer_for_mfe_take")

        if runtime is not None and runtime.recovered_after_adverse and mfe < TAKE_MFE_MIN_PCT:
            if profile in MOMENTUM_V5_RECOVERY_EXIT_PROFILES | MOMENTUM_V5_RECOVERY_OR_CUT_PROFILES:
                if unrealized > -0.15:
                    runtime.recovery_hold_events += 1
                    return _no_exit(res, tag="recovered_hold")

    return res
