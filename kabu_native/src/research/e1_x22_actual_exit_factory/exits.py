"""Phase C: Actual EXIT logic factory (event-driven, long only)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from . import ACTUAL_EXITS
from .paths import session_end_epoch


@dataclass
class ExitSpec:
    exit_id: str
    hard_stop_bps: Optional[float]
    profit_target_bps: Optional[float]
    max_hold_sec: float
    no_progress_checkpoint_sec: Optional[float] = None
    no_progress_mfe_bps: Optional[float] = None
    no_progress_abs_ret_bps: Optional[float] = None
    trail_activation_bps: Optional[float] = None
    trail_giveback_bps: Optional[float] = None
    # priority encoded in simulate()


EXIT_SPECS: dict[str, ExitSpec] = {
    "EX_TOUCH_10_10_MAX300": ExitSpec(
        "EX_TOUCH_10_10_MAX300", hard_stop_bps=-10.0, profit_target_bps=10.0, max_hold_sec=300.0,
    ),
    "EX_FAST_PROTECT_V1": ExitSpec(
        "EX_FAST_PROTECT_V1", hard_stop_bps=-10.0, profit_target_bps=10.0, max_hold_sec=180.0,
        no_progress_checkpoint_sec=60.0, no_progress_mfe_bps=5.0, no_progress_abs_ret_bps=5.0,
    ),
    "EX_CONTINUATION_TRAIL_V1": ExitSpec(
        "EX_CONTINUATION_TRAIL_V1", hard_stop_bps=-10.0, profit_target_bps=None, max_hold_sec=300.0,
        no_progress_checkpoint_sec=180.0, no_progress_mfe_bps=5.0, no_progress_abs_ret_bps=5.0,
        trail_activation_bps=10.0, trail_giveback_bps=5.0,
    ),
    "EX_ASYM_10_15_V1": ExitSpec(
        "EX_ASYM_10_15_V1", hard_stop_bps=-15.0, profit_target_bps=10.0, max_hold_sec=300.0,
    ),
    "EX_TIGHT_5_10_V1": ExitSpec(
        "EX_TIGHT_5_10_V1", hard_stop_bps=-5.0, profit_target_bps=10.0, max_hold_sec=180.0,
    ),
    "EX_TRAIL_10_5_MAX300_V1": ExitSpec(
        "EX_TRAIL_10_5_MAX300_V1", hard_stop_bps=-10.0, profit_target_bps=None, max_hold_sec=300.0,
        trail_activation_bps=10.0, trail_giveback_bps=5.0,
    ),
}


def _bps_to_ret(bps: float) -> float:
    return bps / 10000.0


def simulate_exit_on_path(
    *,
    exit_id: str,
    entry_epoch: float,
    entry_price: float,
    date: str,
    session: str,
    times: np.ndarray,
    prices: np.ndarray,
) -> Optional[dict[str, Any]]:
    """
    Event-driven long EXIT. times/prices include as-of tick at/before entry (index 0).
    Returns trade result or None if path unevaluable.
    """
    if times.size == 0 or entry_price is None or entry_price <= 0:
        return None
    spec = EXIT_SPECS[exit_id]
    sess_end = session_end_epoch(date, session)
    max_t = min(entry_epoch + spec.max_hold_sec, sess_end)

    hard = _bps_to_ret(spec.hard_stop_bps) if spec.hard_stop_bps is not None else None
    target = _bps_to_ret(spec.profit_target_bps) if spec.profit_target_bps is not None else None
    trail_act = _bps_to_ret(spec.trail_activation_bps) if spec.trail_activation_bps is not None else None
    trail_gb = _bps_to_ret(spec.trail_giveback_bps) if spec.trail_giveback_bps is not None else None
    np_mfe = _bps_to_ret(spec.no_progress_mfe_bps) if spec.no_progress_mfe_bps is not None else None
    np_abs = _bps_to_ret(spec.no_progress_abs_ret_bps) if spec.no_progress_abs_ret_bps is not None else None

    mfe = 0.0
    mae = 0.0
    trail_on = False
    np_checked = False

    # Walk events; evaluate on each price event at or after entry as-of
    last_i = None
    for i in range(times.size):
        t = float(times[i])
        if t > max_t + 1e-12:
            break
        if t > sess_end + 1e-12:
            break
        px = float(prices[i])
        ret = px / entry_price - 1.0
        mfe = max(mfe, ret)
        mae = min(mae, ret)
        last_i = i
        hold = t - entry_epoch

        # 1) hard stop
        if hard is not None and ret <= hard + 1e-15:
            return _result(exit_id, entry_epoch, entry_price, t, px, "hard_stop", hold, mfe, mae, ret)

        # 2) profit target (if configured; trailing exits skip this)
        if target is not None and ret >= target - 1e-15:
            return _result(exit_id, entry_epoch, entry_price, t, px, "profit_target", hold, mfe, mae, ret)

        # 3) trailing
        if trail_act is not None and trail_gb is not None:
            if mfe >= trail_act - 1e-15:
                trail_on = True
            if trail_on and ret <= (mfe - trail_gb) + 1e-15:
                return _result(exit_id, entry_epoch, entry_price, t, px, "trailing_exit", hold, mfe, mae, ret)

        # 4) NoProgress at checkpoint (checked once when time crosses checkpoint)
        if (
            spec.no_progress_checkpoint_sec is not None
            and not np_checked
            and hold + 1e-12 >= spec.no_progress_checkpoint_sec
        ):
            np_checked = True
            if np_mfe is not None and np_abs is not None:
                if mfe < np_mfe - 1e-15 and abs(ret) < np_abs - 1e-15:
                    return _result(
                        exit_id, entry_epoch, entry_price, t, px, "no_progress_exit", hold, mfe, mae, ret
                    )

    # End of walk: max hold or session close
    if last_i is None:
        return None
    t = float(times[last_i])
    px = float(prices[last_i])
    # If path ends before max_t solely due to no more events, use last event —
    # but classify reason by what bound hit.
    hold = t - entry_epoch
    ret = px / entry_price - 1.0
    # Prefer session_close if we hit/near session end before max hold wall
    if t >= sess_end - 1e-6 or (entry_epoch + spec.max_hold_sec) > sess_end and t >= sess_end - 1.0:
        # if max hold would exceed session, and we're at end of path near sess_end
        if abs(t - sess_end) <= 2.0 or t >= sess_end - 1e-6:
            reason = "session_close"
        elif hold + 1e-9 >= spec.max_hold_sec:
            reason = "max_hold_exit"
        else:
            # no more events before horizon — treat as max_hold if near, else session
            reason = "max_hold_exit" if hold >= spec.max_hold_sec - 1.0 else "session_close"
    elif hold + 1e-9 >= spec.max_hold_sec:
        reason = "max_hold_exit"
    else:
        # path ended early without hitting max — still force by last available (unevaluable rare)
        reason = "max_hold_exit" if (entry_epoch + spec.max_hold_sec) <= sess_end else "session_close"

    # More precise: if lim is sess_end and path reached near it
    lim = min(entry_epoch + spec.max_hold_sec, sess_end)
    if abs(lim - sess_end) < 1e-6 and t >= lim - 2.0:
        reason = "session_close"
    elif hold + 1e-9 >= spec.max_hold_sec or t >= entry_epoch + spec.max_hold_sec - 1e-6:
        reason = "max_hold_exit"
    elif lim == sess_end:
        reason = "session_close"
    else:
        reason = "max_hold_exit"

    return _result(exit_id, entry_epoch, entry_price, t, px, reason, hold, mfe, mae, ret)


def _result(exit_id, entry_epoch, entry_price, exit_t, exit_px, reason, hold, mfe, mae, ret):
    return {
        "actual_exit_id": exit_id,
        "entry_time_epoch": entry_epoch,
        "entry_price": entry_price,
        "exit_time_epoch": exit_t,
        "exit_price": exit_px,
        "exit_reason": reason,
        "hold_sec": float(hold),
        "MFE_at_exit_bps": float(mfe * 10000.0),
        "MAE_at_exit_bps": float(mae * 10000.0),
        "reference_return_bps": float(ret * 10000.0),
        "gross_reference_pnl_yen_100": float(entry_price * ret * 100.0),
    }


def unit_test_exits() -> list[dict[str, Any]]:
    """Synthetic path unit tests for priority / trailing / no-progress / max-hold / session."""
    results = []
    # Synthetic: entry at t=0, price 1000
    # Path: hard stop hits before profit
    times = np.array([0.0, 1.0, 2.0], dtype=float)
    prices = np.array([1000.0, 998.5, 1012.0])  # -15bps then +120bps — stop first on EX_TOUCH
    # For -10bps stop: 999.0
    prices = np.array([1000.0, 998.9, 1012.0])  # -11bps
    r = simulate_exit_on_path(
        exit_id="EX_TOUCH_10_10_MAX300", entry_epoch=0.0, entry_price=1000.0,
        date="20260721", session="AM", times=times, prices=prices,
    )
    results.append({"test": "hard_stop_priority", "ok": r and r["exit_reason"] == "hard_stop", "detail": r})

    times = np.array([0.0, 1.0, 2.0])
    prices = np.array([1000.0, 1001.2, 1000.0])  # +12bps profit
    r = simulate_exit_on_path(
        exit_id="EX_TOUCH_10_10_MAX300", entry_epoch=0.0, entry_price=1000.0,
        date="20260721", session="AM", times=times, prices=prices,
    )
    results.append({"test": "profit_target_priority", "ok": r and r["exit_reason"] == "profit_target", "detail": r})

    # Trailing: go +15bps then giveback 6bps from MFE
    times = np.array([0.0, 10.0, 20.0, 30.0])
    prices = np.array([1000.0, 1001.5, 1002.0, 1001.4])  # MFE +20bps, then 1001.4 = +14bps = giveback 6 from 20
    r = simulate_exit_on_path(
        exit_id="EX_TRAIL_10_5_MAX300_V1", entry_epoch=0.0, entry_price=1000.0,
        date="20260721", session="AM", times=times, prices=prices,
    )
    results.append({
        "test": "trailing_activation_giveback",
        "ok": r is not None and r["exit_reason"] == "trailing_exit",
        "detail": r,
    })

    # NoProgress at 60s: flat path
    times = np.array([0.0, 30.0, 60.0, 90.0])
    prices = np.array([1000.0, 1000.2, 1000.3, 1000.4])  # MFE <5bps, abs ret <5bps
    r = simulate_exit_on_path(
        exit_id="EX_FAST_PROTECT_V1", entry_epoch=0.0, entry_price=1000.0,
        date="20260721", session="AM", times=times, prices=prices,
    )
    results.append({"test": "no_progress_checkpoint", "ok": r and r["exit_reason"] == "no_progress_exit", "detail": r})

    # Max hold 180
    times = np.array([0.0, 60.0, 120.0, 180.0, 200.0])
    prices = np.array([1000.0, 1000.6, 1000.7, 1000.8, 1000.9])  # enough move to avoid NP? MFE 8bps >5 so no NP
    # At 60s MFE=6bps >=5 → no NP; hold to 180
    r = simulate_exit_on_path(
        exit_id="EX_FAST_PROTECT_V1", entry_epoch=0.0, entry_price=1000.0,
        date="20260721", session="AM", times=times, prices=prices,
    )
    results.append({"test": "max_hold", "ok": r and r["exit_reason"] == "max_hold_exit", "detail": r})

    # Session close: entry near AM end 11:29:50, max 300 but session ends 11:30
    # Use real epoch for 20260721 11:29:50
    from datetime import datetime
    from zoneinfo import ZoneInfo
    JST = ZoneInfo("Asia/Tokyo")
    entry = datetime(2026, 7, 21, 11, 29, 50, tzinfo=JST).timestamp()
    sess_end = datetime(2026, 7, 21, 11, 30, 0, tzinfo=JST).timestamp()
    times = np.array([entry, entry + 5.0, sess_end])
    prices = np.array([1000.0, 1000.1, 1000.2])
    r = simulate_exit_on_path(
        exit_id="EX_TOUCH_10_10_MAX300", entry_epoch=entry, entry_price=1000.0,
        date="20260721", session="AM", times=times, prices=prices,
    )
    results.append({"test": "session_close", "ok": r and r["exit_reason"] == "session_close", "detail": r})

    return results


assert set(EXIT_SPECS) == set(ACTUAL_EXITS)
