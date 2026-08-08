"""Corrected TARGET design: never snap raw_target up; within-horizon reaches only."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x26_exit_library.snap import snap_ceil, snap_floor
from research.e1_x28a_candidate_exit_factory import (
    DISCOVERY,
    MAX_HOLD_GRID_SEC,
    MIN_TARGET_BPS,
    NO_PROGRESS_ABS_RET_BPS,
    NO_PROGRESS_GRID_SEC,
    NO_PROGRESS_MFE_BPS,
    NO_PROGRESS_SOURCE,
    STOP_GRID_BPS,
    TARGET_GRID_BPS,
)
from research.e1_x28a_candidate_exit_factory.calibrate import _nearest_upside, stop_risk_tag


def snap_target_floor_strict(raw_target: Optional[float]) -> tuple[Optional[float], Optional[str]]:
    """
    Snap down only. raw < 20 → unavailable (never snap up).
    """
    if raw_target is None or raw_target != raw_target:
        return None, "raw_target_missing"
    if float(raw_target) < MIN_TARGET_BPS:
        return None, "CANDIDATE_TARGET_BELOW_MINIMUM"
    target = snap_floor(float(raw_target), TARGET_GRID_BPS)
    # snap_floor returns grid[0] when below all — but we already gated >= 20
    if target is None or target < MIN_TARGET_BPS:
        return None, "CANDIDATE_TARGET_BELOW_MINIMUM"
    if target > float(raw_target) + 1e-12:
        return None, "CANDIDATE_TARGET_BELOW_MINIMUM"  # refuse upward snap
    return float(target), None


def within_horizon_target_support(
    *,
    selected: np.ndarray,
    metrics: dict[str, np.ndarray],
    dates: np.ndarray,
    path_ok: np.ndarray,
    horizon_sec: int,
    target_bps: float,
) -> dict[str, Any]:
    """Reach stats restricted to first touch within candidate_horizon_sec."""
    disc = np.isin(dates, list(DISCOVERY))
    base = disc & selected & path_ok
    up = _nearest_upside(target_bps)
    reached = metrics[f"up_{up}_reached"]
    tsec = metrics[f"up_{up}_time_sec"]
    within = base & reached & np.isfinite(tsec) & (tsec <= float(horizon_sec) + 1e-9)
    idx = np.where(within)[0]
    n = int(idx.size)
    days = int(np.unique(dates[idx]).size) if n else 0
    denom = int(base.sum())
    times = tsec[within]
    pre = metrics[f"pre_reach_MAE_{up}_bps"][within]
    pre_abs = np.abs(pre)
    q50 = float(np.quantile(times, 0.50)) if n else None
    q75 = float(np.quantile(times, 0.75)) if n else None
    pre_q75 = float(np.quantile(pre_abs, 0.75)) if n else None
    support_ok = n >= 10 and days >= 3
    return {
        "upside_level_used": up,
        "within_horizon_reached_n": n,
        "within_horizon_reached_days": days,
        "within_horizon_reach_rate": (n / denom) if denom else None,
        "reach_time_q50_within_horizon": q50,
        "reach_time_q75_within_horizon": q75,
        "pre_rise_MAE_abs_q75_within_horizon": pre_q75,
        "support_ok": support_ok,
        "status": "OK" if support_ok else "CANDIDATE_TARGET_WITHIN_HORIZON_SUPPORT_INSUFFICIENT",
    }


def design_target_v2(
    *,
    m: dict[str, Any],
    horizon_sec: int,
    selected: np.ndarray,
    metrics: dict[str, np.ndarray],
    dates: np.ndarray,
    path_ok: np.ndarray,
) -> dict[str, Any]:
    raw_target = m.get(f"MFE_{horizon_sec}_q25")
    target, err = snap_target_floor_strict(raw_target)
    if err == "CANDIDATE_TARGET_BELOW_MINIMUM":
        return {
            "ok": False,
            "reason": "CANDIDATE_TARGET_BELOW_MINIMUM",
            "raw_target": raw_target,
            "candidate_horizon_sec": horizon_sec,
        }
    if target is None:
        return {"ok": False, "reason": err or "target_unavailable", "raw_target": raw_target}

    wh = within_horizon_target_support(
        selected=selected, metrics=metrics, dates=dates, path_ok=path_ok,
        horizon_sec=horizon_sec, target_bps=target,
    )
    if not wh["support_ok"]:
        return {
            "ok": False,
            "reason": "CANDIDATE_TARGET_WITHIN_HORIZON_SUPPORT_INSUFFICIENT",
            "raw_target": raw_target,
            "target_bps": target,
            "candidate_horizon_sec": horizon_sec,
            **wh,
        }

    pre_abs_q75 = wh["pre_rise_MAE_abs_q75_within_horizon"]
    stop = snap_ceil(pre_abs_q75, STOP_GRID_BPS)
    if stop is None or stop <= 0:
        return {"ok": False, "reason": "stop_unavailable", "raw_target": raw_target, "target_bps": target, **wh}

    reach_q75 = wh["reach_time_q75_within_horizon"]
    # snap ceil, but must not exceed candidate horizon
    no_prog = snap_ceil(reach_q75, NO_PROGRESS_GRID_SEC)
    if no_prog is None:
        return {"ok": False, "reason": "no_progress_unavailable", **wh}
    if float(no_prog) > float(horizon_sec) + 1e-9:
        # prefer largest grid <= horizon; else horizon itself
        le = [g for g in NO_PROGRESS_GRID_SEC if g <= float(horizon_sec) + 1e-9]
        if le:
            # still want >= reach_q75 if possible
            ge = [g for g in le if g + 1e-12 >= float(reach_q75)]
            no_prog = float(ge[0]) if ge else float(horizon_sec)
        else:
            no_prog = float(horizon_sec)

    # max hold = candidate horizon (largest grid <= horizon); never extend past horizon
    hold_le = [g for g in MAX_HOLD_GRID_SEC if g <= float(horizon_sec) + 1e-9]
    max_hold = float(hold_le[-1]) if hold_le else float(horizon_sec)
    # if no_progress exceeds max_hold, clamp to horizon (do not extend hold)
    if float(no_prog) > float(max_hold) + 1e-9:
        no_prog = min(float(no_prog), float(horizon_sec))
        if float(no_prog) > float(max_hold) + 1e-9:
            no_prog = float(max_hold)

    return {
        "ok": True,
        "exit_mode": "TARGET",
        "stop_bps": stop,
        "target_bps": target,
        "trail_activation_bps": None,
        "giveback_bps": None,
        "giveback_mode": None,
        "no_progress_sec": float(no_prog),
        "max_hold_sec": float(max_hold),
        "raw_target": raw_target,
        "candidate_horizon_sec": horizon_sec,
        "stop_risk_tag": stop_risk_tag(stop),
        "no_progress_mfe_bps": NO_PROGRESS_MFE_BPS,
        "no_progress_abs_ret_bps": NO_PROGRESS_ABS_RET_BPS,
        "no_progress_source": NO_PROGRESS_SOURCE,
        **wh,
    }
