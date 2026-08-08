"""EXIT evaluation after OPEN (Phase B executable contract).

Priority at same grid (frozen):
  SESSION_CLOSE -> INVALIDATION -> STOP -> NO_PROGRESS -> TRAILING -> MAX_HOLD
ENTRY ask / EXIT bid / STOP on bid / invalidation state on mid / fill at bid.
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from .economics import yen_roundtrip_cost
from .exits import MAX_STRUCTURAL_RISK_BPS
from .tick_resolver import tick_size

EXIT_A = {
    "no_progress_sec": 180.0,
    "max_hold_sec": 600.0,
    "trailing": False,
}
EXIT_B = {
    "no_progress_sec": 120.0,
    "max_hold_sec": 420.0,
    "trailing": True,
}


def exit_spec(exit_id: str) -> dict[str, Any]:
    return EXIT_B if "EXIT_B" in exit_id else EXIT_A


def structural_entry_ok(entry_ask: float, stop_level: float) -> tuple[bool, float, str]:
    """Reject OPEN if R<=0 or structural risk > 60bps."""
    r = float(entry_ask) - float(stop_level)
    if not np.isfinite(r) or r <= 0:
        return False, r, "STRUCTURAL_RISK_NONPOSITIVE_R"
    if entry_ask <= 0:
        return False, r, "INVALID_ENTRY_ASK"
    risk_bps = r / entry_ask * 10000.0
    if risk_bps > MAX_STRUCTURAL_RISK_BPS + 1e-12:
        return False, r, "STRUCTURAL_RISK_GT_60BPS"
    return True, r, ""


def _invalidation(
    setup: str,
    g: int,
    open_g: int,
    trigger_g: int,
    feats: dict[str, np.ndarray],
    frozen: dict[str, Any],
    tick: float,
    *,
    vwap_in_use: bool,
    ret30_neg_streak: list[int],
    high_stall: list[int],
    below_range_streak: list[int],
) -> Optional[str]:
    mid = feats["mid"][g]
    if not np.isfinite(mid):
        return None
    tl = float(frozen["trigger_level"])
    if setup == "CONT":
        if mid < tl - tick + 1e-12:
            return "INVALIDATION_BREAKOUT_LOST"
        r30 = feats["ret_30s_bps"][g]
        if np.isfinite(r30) and r30 < 0:
            ret30_neg_streak[0] += 1
        else:
            ret30_neg_streak[0] = 0
        if ret30_neg_streak[0] >= 2:
            return "INVALIDATION_DIR_REVERSED"
        low60 = feats["low_60s"][g]
        high60 = feats["high_60s"][g]
        prev_h = feats["high_60s"][g - 1] if g > 0 else np.nan
        if np.isfinite(high60) and np.isfinite(prev_h) and high60 > prev_h + 1e-12:
            high_stall[0] = 0
        else:
            high_stall[0] += 1
        if (np.isfinite(low60) and mid < low60 - 1e-12 and high_stall[0] >= 6):
            return "INVALIDATION_SUPPORT_LOST"
    elif setup == "PULL":
        if mid < tl - tick + 1e-12:
            return "INVALIDATION_RECLAIM_LOST"
        pl = float(frozen.get("pullback_low", float("nan")))
        if np.isfinite(pl) and mid < pl - 1e-12:
            return "INVALIDATION_PULLBACK_LOW_BROKEN"
        if vwap_in_use:
            # inactive for R3 registry (optional_features_in_use=[]); kept for contract
            vd = feats.get("vwap_dev_bps", np.full_like(mid, np.nan))[g]
            # track via below_range_streak reuse: mid below VWAP => vwap_dev < 0
            if np.isfinite(vd) and vd < 0:
                below_range_streak[0] += 1
            else:
                below_range_streak[0] = 0
            if below_range_streak[0] >= 3:
                return "INVALIDATION_VWAP_LOST"
    else:  # BREAK
        ch = float(frozen.get("compression_high", float("nan")))
        # 1) lost breakout level by >=1 tick
        if mid < tl - tick + 1e-12:
            return "INVALIDATION_BACK_INSIDE_RANGE"
        # 2) cannot hold above range high for 2 consecutive grids
        if np.isfinite(ch):
            if mid < ch - 1e-12:
                below_range_streak[0] += 1
            else:
                below_range_streak[0] = 0
            if below_range_streak[0] >= 2:
                return "INVALIDATION_FAILED_HOLD_ABOVE_RANGE"
        # 3) breakout failure: vol_ratio_60_300<1.0 within 60s (12 grids) after trigger
        if (g - trigger_g) <= 12:
            vr = feats["vol_ratio_60_300"][g]
            if np.isfinite(vr) and vr < 1.0 - 1e-12:
                return "INVALIDATION_BREAKOUT_FAILURE_VOL"
    return None


def evaluate_from_open(
    *,
    setup: str,
    exit_id: str,
    open_g: int,
    trigger_g: int,
    entry_ask: float,
    stop_level: float,
    frozen: dict[str, Any],
    feats: dict[str, np.ndarray],
    grid: np.ndarray,
    symbol_class: str,
    vwap_in_use: bool = False,
) -> dict[str, Any]:
    """Walk grids after OPEN; return completed exit row fields (no CAP/day meta)."""
    spec = exit_spec(exit_id)
    n = grid.shape[0]
    tick = tick_size(symbol_class, entry_ask)
    cost_per_share = yen_roundtrip_cost(entry_ask) / 100.0  # yen per share equivalent of roundtrip
    # NO_PROGRESS uses: gain < cost_yen_per_share + 1*tick
    np_edge = cost_per_share + tick

    mfe = 0.0
    mae = 0.0
    max_fav_r = 0.0
    armed = False
    r_init = entry_ask - stop_level
    ret30_neg = [0]
    high_stall = [0]
    below_range = [0]
    t_open = float(grid[open_g])

    # evaluate from the next grid after OPEN (OPEN grid is the fill grid)
    for g in range(open_g + 1, n):
        bid = feats["bid"][g]
        mid = feats["mid"][g]
        t = float(grid[g])
        elapsed = t - t_open

        # 1. update rolling structures
        if np.isfinite(bid):
            gain = float(bid) - entry_ask
            mfe = max(mfe, gain)
            mae = min(mae, gain)
            if r_init > 0:
                max_fav_r = max(max_fav_r, gain / r_init)
                if gain >= r_init - 1e-12:
                    armed = True

        # 2. SESSION_CLOSE / window end (last grid)
        if g == n - 1:
            if not np.isfinite(bid):
                return {"status": "CENSORED", "exit_reason": "CENSORED_NO_BID_AT_CLOSE",
                        "exit_g": g, "exit_bid": None, "mfe_yen": mfe * 100, "mae_yen": mae * 100}
            return {
                "status": "COMPLETED", "exit_reason": "SESSION_CLOSE",
                "exit_g": g, "exit_bid": float(bid),
                "mfe_yen": mfe * 100, "mae_yen": mae * 100,
                "elapsed_sec": elapsed, "trailing_armed": armed,
            }

        if not np.isfinite(bid) or not np.isfinite(mid):
            continue  # wait for quote; do not fire exits without bid/mid

        gain = float(bid) - entry_ask

        # 3. INVALIDATION
        inv = _invalidation(
            setup, g, open_g, trigger_g, feats, frozen, tick,
            vwap_in_use=vwap_in_use,
            ret30_neg_streak=ret30_neg, high_stall=high_stall,
            below_range_streak=below_range,
        )
        if inv:
            return {
                "status": "COMPLETED", "exit_reason": inv,
                "exit_g": g, "exit_bid": float(bid),
                "mfe_yen": mfe * 100, "mae_yen": mae * 100,
                "elapsed_sec": elapsed, "trailing_armed": armed,
            }

        # 4. STOP (bid <= stop)
        if float(bid) <= stop_level + 1e-12:
            return {
                "status": "COMPLETED", "exit_reason": "STOP",
                "exit_g": g, "exit_bid": float(bid),
                "mfe_yen": mfe * 100, "mae_yen": mae * 100,
                "elapsed_sec": elapsed, "trailing_armed": armed,
            }

        # 5. NO_PROGRESS
        if elapsed + 1e-9 >= spec["no_progress_sec"]:
            # fire once at/after threshold on each subsequent grid while still failing
            if gain < np_edge - 1e-12:
                return {
                    "status": "COMPLETED", "exit_reason": "NO_PROGRESS",
                    "exit_g": g, "exit_bid": float(bid),
                    "mfe_yen": mfe * 100, "mae_yen": mae * 100,
                    "elapsed_sec": elapsed, "trailing_armed": armed,
                }

        # 6. TRAILING (EXIT_B)
        if spec["trailing"] and armed and r_init > 0:
            floor = entry_ask + 0.5 * max_fav_r * r_init
            if float(bid) <= floor + 1e-12:
                return {
                    "status": "COMPLETED", "exit_reason": "TRAILING",
                    "exit_g": g, "exit_bid": float(bid),
                    "mfe_yen": mfe * 100, "mae_yen": mae * 100,
                    "elapsed_sec": elapsed, "trailing_armed": True,
                    "trail_floor": floor,
                }

        # 7. MAX_HOLD
        if elapsed + 1e-9 >= spec["max_hold_sec"]:
            return {
                "status": "COMPLETED", "exit_reason": "MAX_HOLD",
                "exit_g": g, "exit_bid": float(bid),
                "mfe_yen": mfe * 100, "mae_yen": mae * 100,
                "elapsed_sec": elapsed, "trailing_armed": armed,
            }

    # fell off end without last-grid handling (empty grid after open)
    return {
        "status": "CENSORED", "exit_reason": "CENSORED_WINDOW_END",
        "exit_g": None, "exit_bid": None,
        "mfe_yen": mfe * 100, "mae_yen": mae * 100,
    }
