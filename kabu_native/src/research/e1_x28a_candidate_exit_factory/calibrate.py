"""Horizon, mode, TARGET/TRAIL parameter design (fixed rules; no PnL)."""
from __future__ import annotations

from typing import Any, Optional

from research.e1_x26_exit_library.snap import snap_ceil, snap_floor

from . import (
    GIVEBACK_GRID_BPS,
    HORIZONS,
    MAX_HOLD_GRID_SEC,
    MFE_GROWTH_THRESHOLD,
    MIN_ACTIVATION_BPS,
    MIN_LOCKED_PROFIT_BPS,
    MIN_TARGET_BPS,
    MODE_MFE_RATIO,
    MODE_TERMINAL_GB_MIN,
    NO_PROGRESS_ABS_RET_BPS,
    NO_PROGRESS_GRID_SEC,
    NO_PROGRESS_MFE_BPS,
    NO_PROGRESS_SOURCE,
    STOP_GRID_BPS,
    TARGET_GRID_BPS,
    TRAIL_ACTIVATION_GRID_BPS,
)


def determine_horizon(m: dict[str, Any]) -> dict[str, Any]:
    m300 = m.get("MFE_300_q50")
    m600 = m.get("MFE_600_q50")
    m900 = m.get("MFE_900_q50")
    m1800 = m.get("MFE_1800_q50")
    g1 = None if m300 is None or m600 is None else float(m600) - float(m300)
    g2 = None if m600 is None or m900 is None else float(m900) - float(m600)
    g3 = None if m900 is None or m1800 is None else float(m1800) - float(m900)
    seq = {"gain_300_600": g1, "gain_600_900": g2, "gain_900_1800": g3}

    # default 300
    h = 300
    reason = "default_min_300"
    if g1 is not None and g1 >= MFE_GROWTH_THRESHOLD:
        h = 600
        reason = "gain_300_600>=10"
        if g2 is not None and g2 >= MFE_GROWTH_THRESHOLD:
            h = 900
            reason = "gain_600_900>=10"
            if g3 is not None and g3 >= MFE_GROWTH_THRESHOLD:
                h = 1800
                reason = "gain_900_1800>=10"
            else:
                reason = "stop_at_900_gain_900_1800<10"
        else:
            reason = "stop_at_600_gain_600_900<10"
    else:
        reason = "stop_at_300_gain_300_600<10"

    return {
        "candidate_horizon_sec": h,
        "horizon_reason": reason,
        "mfe_growth_sequence": seq,
    }


def determine_mode(m: dict[str, Any], horizon_sec: int) -> dict[str, Any]:
    mfe_h = m.get(f"MFE_{horizon_sec}_q50")
    mfe_300 = m.get("MFE_300_q50")
    term_gb = m.get("terminal_giveback_600_q50")
    eps = 1e-9
    ratio = None
    if mfe_300 is not None and mfe_h is not None:
        ratio = float(mfe_300) / max(float(mfe_h), eps)
    target_ok = (
        horizon_sec <= 600
        and ratio is not None and ratio >= MODE_MFE_RATIO
        and term_gb is not None and float(term_gb) >= MODE_TERMINAL_GB_MIN
    )
    if target_ok:
        return {
            "exit_mode": "TARGET",
            "mode_reason": f"horizon<={horizon_sec}<=600 AND mfe300/mfeH={ratio:.3f}>={MODE_MFE_RATIO} AND term_gb600={term_gb}>={MODE_TERMINAL_GB_MIN}",
            "mfe_ratio": ratio,
        }
    return {
        "exit_mode": "TRAIL",
        "mode_reason": "not_TARGET_conditions",
        "mfe_ratio": ratio,
    }


def _nearest_upside(level: float) -> int:
    ups = (20, 30, 50, 60, 80, 100)
    best = ups[0]
    best_d = abs(level - best)
    for u in ups[1:]:
        d = abs(level - u)
        if d < best_d:
            best, best_d = u, d
    return int(best)


def stop_risk_tag(stop: Optional[float]) -> Optional[str]:
    if stop is None:
        return None
    s = float(stop)
    if s <= 50:
        return "NORMAL_STOP"
    if s <= 100:
        return "WIDE_STOP"
    return "VERY_WIDE_STOP"


def design_target(m: dict[str, Any], horizon_sec: int) -> dict[str, Any]:
    raw_target = m.get(f"MFE_{horizon_sec}_q25")
    target = snap_floor(raw_target, TARGET_GRID_BPS)
    if target is None or target < MIN_TARGET_BPS:
        return {"ok": False, "reason": "target_below_20_or_missing", "raw_target": raw_target}

    up = _nearest_upside(target)
    if not m.get(f"up_{up}_metric_support_ok"):
        return {"ok": False, "reason": f"target_reach_support_insufficient_up_{up}", "raw_target": raw_target, "target_bps": target}

    pre_abs_q75 = m.get(f"pre_rise_MAE_abs_{up}_q75")
    stop = snap_ceil(pre_abs_q75, STOP_GRID_BPS)
    if stop is None or stop <= 0:
        return {"ok": False, "reason": "stop_unavailable", "raw_target": raw_target, "target_bps": target}

    reach_t_q75 = m.get(f"up_{up}_time_q75")
    no_prog = snap_ceil(reach_t_q75, NO_PROGRESS_GRID_SEC)
    if no_prog is None:
        return {"ok": False, "reason": "no_progress_unavailable"}

    hold_raw = max(float(horizon_sec), float(no_prog))
    max_hold = snap_ceil(hold_raw, MAX_HOLD_GRID_SEC)
    if max_hold is None or no_prog > max_hold:
        # bump max hold
        max_hold = snap_ceil(float(no_prog), MAX_HOLD_GRID_SEC)
    if max_hold is None or no_prog > max_hold:
        return {"ok": False, "reason": "max_hold_lt_no_progress"}

    return {
        "ok": True,
        "exit_mode": "TARGET",
        "stop_bps": stop,
        "target_bps": target,
        "trail_activation_bps": None,
        "giveback_bps": None,
        "giveback_mode": None,
        "no_progress_sec": no_prog,
        "max_hold_sec": max_hold,
        "raw_target": raw_target,
        "pre_rise_MAE_abs_q75": pre_abs_q75,
        "reach_time_q75": reach_t_q75,
        "upside_level_used": up,
        "stop_risk_tag": stop_risk_tag(stop),
        "no_progress_mfe_bps": NO_PROGRESS_MFE_BPS,
        "no_progress_abs_ret_bps": NO_PROGRESS_ABS_RET_BPS,
        "no_progress_source": NO_PROGRESS_SOURCE,
    }


def design_trail(m: dict[str, Any], horizon_sec: int) -> dict[str, Any]:
    raw_act = m.get(f"MFE_{horizon_sec}_q25")
    act = snap_floor(raw_act, TRAIL_ACTIVATION_GRID_BPS)
    if act is None or act < MIN_ACTIVATION_BPS:
        return {"ok": False, "reason": "activation_below_20_or_missing", "raw_activation": raw_act}

    raw_gb = m.get(f"max_giveback_{horizon_sec}_q25")
    # PROTECT invariant: activation - giveback >= 10
    max_gb_allowed = float(act) - MIN_LOCKED_PROFIT_BPS
    if max_gb_allowed < min(GIVEBACK_GRID_BPS):
        return {"ok": False, "reason": "cannot_lock_10bps", "raw_activation": raw_act, "activation_bps": act}

    # observed giveback snapped ceil, then capped by lock
    gb_obs = snap_ceil(raw_gb, GIVEBACK_GRID_BPS) if raw_gb is not None else None
    # largest giveback grid <= max_gb_allowed and ideally reflecting observed
    candidates = [g for g in GIVEBACK_GRID_BPS if g <= max_gb_allowed + 1e-12]
    if not candidates:
        return {"ok": False, "reason": "giveback_grid_empty_under_lock"}
    if gb_obs is not None:
        # min(observed grid, activation-10) as max allowed → take largest <= that
        cap = min(float(gb_obs), max_gb_allowed)
        giveback = max([g for g in candidates if g <= cap + 1e-12], default=None)
    else:
        giveback = candidates[-1]  # max under lock if no observation
    if giveback is None:
        return {"ok": False, "reason": "giveback_unavailable"}
    if float(act) - float(giveback) < MIN_LOCKED_PROFIT_BPS - 1e-9:
        return {"ok": False, "reason": "locked_profit_lt_10"}

    up = _nearest_upside(act)
    if not m.get(f"up_{up}_metric_support_ok"):
        return {"ok": False, "reason": f"activation_reach_support_insufficient_up_{up}",
                "activation_bps": act, "giveback_bps": giveback}

    pre_abs_q75 = m.get(f"pre_rise_MAE_abs_{up}_q75")
    stop = snap_ceil(pre_abs_q75, STOP_GRID_BPS)
    if stop is None or stop <= 0:
        return {"ok": False, "reason": "stop_unavailable"}

    reach_t_q75 = m.get(f"up_{up}_time_q75")
    no_prog = snap_ceil(reach_t_q75, NO_PROGRESS_GRID_SEC)
    if no_prog is None:
        return {"ok": False, "reason": "no_progress_unavailable"}

    max_hold = snap_ceil(float(horizon_sec), MAX_HOLD_GRID_SEC)
    if max_hold is None or float(no_prog) > float(max_hold):
        max_hold = snap_ceil(float(no_prog), MAX_HOLD_GRID_SEC)
    if max_hold is None or float(no_prog) > float(max_hold):
        return {"ok": False, "reason": "max_hold_lt_no_progress"}

    return {
        "ok": True,
        "exit_mode": "TRAIL",
        "stop_bps": stop,
        "target_bps": None,
        "trail_activation_bps": act,
        "giveback_bps": giveback,
        "giveback_mode": "from_MFE",
        "no_progress_sec": no_prog,
        "max_hold_sec": max_hold,
        "raw_activation": raw_act,
        "raw_giveback": raw_gb,
        "pre_rise_MAE_abs_q75": pre_abs_q75,
        "reach_time_q75": reach_t_q75,
        "upside_level_used": up,
        "locked_profit_bps": float(act) - float(giveback),
        "stop_risk_tag": stop_risk_tag(stop),
        "no_progress_mfe_bps": NO_PROGRESS_MFE_BPS,
        "no_progress_abs_ret_bps": NO_PROGRESS_ABS_RET_BPS,
        "no_progress_source": NO_PROGRESS_SOURCE,
    }
