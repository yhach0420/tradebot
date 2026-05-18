"""
Phase 29: noise-tolerant EXIT v7 (Logic Lab only).

Three pillars: delayed_imbalance, recovery_check (15–60s), structure_break_only.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timezone
from typing import TYPE_CHECKING, Optional

from research.microstructure_runtime import (
    FAV_PERSISTENCE_MIN,
    IMB_COLLAPSE_DELTA,
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

V7_DELAYED_PROFILES = frozenset(
    {"momentum_volume_v7_delayed_imb", "momentum_volume_v7_combined"}
)
V7_RECOVERY_PROFILES = frozenset(
    {"momentum_volume_v7_recovery_check", "momentum_volume_v7_combined"}
)
V7_STRUCTURE_PROFILES = frozenset(
    {"momentum_volume_v7_structure_break", "momentum_volume_v7_combined"}
)

DELAY_IMB_SEC = 60.0
IMB_STREAK = 4
RECOVERY_CHECK_SEC = 60.0
VWAP_COLLAPSE_PCT = -0.12
FAV_HOLD_PCT = 0.05
FAV_CUT_MAX_PCT = 0.03
ADV_CUT_PCT = -0.12
STRUCTURE_MOM_STREAK = 4
STRUCTURE_IMB_STREAK = 3
TAKE_MFE = 0.30
TAKE_GIVEBACK = 0.18


def uses_momentum_v7_exit(profile: str) -> bool:
    return profile.startswith("momentum_volume_v7_")


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
        dbg["v7_suppressed"] = tag
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


def _vwap_collapse(inp: KabuExitEvalInput, runtime: Optional["MicrostructureRuntime"]) -> bool:
    vd = _vwap_dist(inp)
    if vd is not None and vd <= VWAP_COLLAPSE_PCT:
        return True
    if runtime and runtime.entry_vwap_dist_pct is not None and vd is not None:
        if vd < float(runtime.entry_vwap_dist_pct) - 0.10:
            return True
    return False


def _recovery_path_at_60(runtime: "MicrostructureRuntime") -> tuple[Optional[bool], dict]:
    snap = runtime.snapshot_60s()
    if not snap:
        return None, {}
    mom_chg = float(snap.get("momentum_pct_from_entry") or snap.get("price_pct_from_entry") or 0.0)
    vwap_chg = snap.get("vwap_distance_change")
    fav = float(snap.get("max_favorable_pct") or runtime.max_favorable_pct)
    adv = float(snap.get("max_adverse_pct") or runtime.max_adverse_pct)
    vwap_ok = vwap_chg is not None and float(vwap_chg) >= 0.0
    hold = mom_chg >= 0.0 or vwap_ok or fav >= FAV_HOLD_PCT
    cut = (
        mom_chg < 0.0
        and vwap_chg is not None
        and float(vwap_chg) < 0.0
        and fav < FAV_CUT_MAX_PCT
        and adv <= ADV_CUT_PCT
    )
    meta = {
        "momentum_change_60s": mom_chg,
        "vwap_distance_change_60s": vwap_chg,
        "favorable_move_60s": fav,
        "adverse_move_60s": adv,
        "recovery_hold": hold,
        "recovery_cut": cut,
    }
    if cut:
        return False, meta
    if hold:
        return True, meta
    return None, meta


def _structure_break_v7(runtime: "MicrostructureRuntime", inp: KabuExitEvalInput) -> bool:
    price = float(inp.current_price)
    mom = runtime.current_momentum_pct(price)
    vd = _vwap_dist(inp)
    vwap_fail = runtime.below_vwap_seen and not runtime.vwap_reclaim_achieved
    if vd is not None and vd < VWAP_RECLAIM_FAIL:
        vwap_fail = True
    mom_neg = runtime.momentum_negative_streak >= STRUCTURE_MOM_STREAK or mom < -0.05
    adv_persist = runtime.max_adverse_pct <= ADV_CUT_PCT + 0.02
    imb_persist = runtime.max_imbalance_collapse_streak >= STRUCTURE_IMB_STREAK
    no_fav = runtime.favorable_persistence_count < 1 and runtime.max_favorable_pct < FAV_PERSISTENCE_MIN
    return vwap_fail and mom_neg and adv_persist and imb_persist and no_fav


def evaluate_momentum_v7_exit(
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

    if runtime and _vwap_collapse(inp, runtime):
        return _custom_exit(
            res,
            reason="vwap_collapse_exit",
            unrealized=unrealized,
            mfe=mfe,
            debug={"v7_vwap_collapse": True},
        )

    if runtime and mfe >= TAKE_MFE and peak > entry:
        gb = ((peak - price) / peak) * 100.0
        if gb >= TAKE_GIVEBACK and unrealized > 0:
            return _custom_exit(
                res,
                reason="structural_take_giveback",
                unrealized=unrealized,
                mfe=mfe,
                debug={"v7_take": True},
            )

    if (
        runtime
        and profile in V7_RECOVERY_PROFILES
        and elapsed >= RECOVERY_CHECK_SEC
    ):
        if not runtime.v7_recovery_check_done:
            runtime.v7_recovery_check_done = True
            hold, meta = _recovery_path_at_60(runtime)
            runtime.v7_recovery_meta_60s = meta
            if hold is True:
                runtime.v7_recovery_hold_at_60 = True
                runtime.v7_judgment = "recovery_hold"
                runtime.recovery_hold_events += 1
            elif hold is False:
                runtime.v7_judgment = "adverse_cut"
                runtime.early_cut_exited = True
                return _custom_exit(
                    res,
                    reason="v7_adverse_cut",
                    unrealized=unrealized,
                    mfe=mfe,
                    debug={"v7_recovery_check": meta},
                )
            else:
                runtime.v7_judgment = "neutral_60s"

    if (
        runtime
        and profile in V7_STRUCTURE_PROFILES
        and elapsed >= DELAY_IMB_SEC
        and _structure_break_v7(runtime, inp)
    ):
        return _custom_exit(
            res,
            reason="structure_break_v7",
            unrealized=unrealized,
            mfe=mfe,
            debug={
                "imb_streak": runtime.max_imbalance_collapse_streak,
                "mom_streak": runtime.momentum_negative_streak,
            },
        )

    if res.would_exit and res.exit_reason == "breakout_failure":
        if runtime and profile in V7_RECOVERY_PROFILES and (
            runtime.v7_recovery_hold_at_60 or runtime.vwap_reclaim_achieved or mfe >= 0.08
        ):
            runtime.recovery_hold_events += 1
            return _no_exit(res, tag="bf_v7_hold")
        if profile in V7_STRUCTURE_PROFILES:
            return _no_exit(res, tag="bf_defer_structure")
        if profile in V7_DELAYED_PROFILES and elapsed < DELAY_IMB_SEC:
            return _no_exit(res, tag="bf_delayed_window")
        return res

    if res.would_exit and res.exit_reason == "board_imbalance_deterioration":
        streak = int(inp.imbalance_low_streak or 0)

        if profile in V7_DELAYED_PROFILES | V7_STRUCTURE_PROFILES:
            if elapsed < DELAY_IMB_SEC:
                if runtime:
                    runtime.recovery_hold_events += 1
                    runtime.v7_delayed_imb_suppressed = True
                return _no_exit(res, tag="imb_before_60s")
            if streak < IMB_STREAK:
                if runtime:
                    runtime.recovery_hold_events += 1
                return _no_exit(res, tag="imb_streak")

        if (
            runtime
            and runtime.v7_recovery_hold_at_60
            and profile in V7_RECOVERY_PROFILES
        ):
            runtime.recovery_hold_events += 1
            return _no_exit(res, tag="imb_recovery_hold_60")

        if profile in V7_STRUCTURE_PROFILES:
            return _no_exit(res, tag="imb_structure_only")

    return res
