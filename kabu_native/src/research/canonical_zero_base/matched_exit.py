"""Strategy-specific matched EXIT arms X0–X6 (not shared unqualified exits)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence

from research.canonical_zero_base.canonical_loader import Tick
from research.canonical_zero_base.constants import COST_BPS, HARD_STOP_PCT, LOT

MAX_HOLD_SEC = 1800


def _session(ts: datetime) -> str:
    return "AM" if ts.hour < 12 else "PM"


def _pnl_yen(entry: float, exit_: float) -> float:
    raw = (exit_ - entry) * LOT
    cost = entry * LOT * COST_BPS / 10000.0 + exit_ * LOT * COST_BPS / 10000.0
    return raw - cost


def simulate_exit(
    ticks: Sequence[Tick],
    entry_idx: int,
    *,
    entry_ask: float,
    strategy_id: str,
    exit_mode: str,
    entry_features: dict[str, Any],
) -> dict[str, Any]:
    """EXIT uses future canonical best bid only."""
    if entry_ask <= 0 or entry_idx >= len(ticks) - 1:
        return {"evaluable": False}
    t0 = ticks[entry_idx]
    stop_px = entry_ask * (1.0 - HARD_STOP_PCT / 100.0)
    peak = 0.0
    activated = False
    # strategy-specific trailing activate (not legacy 0.6/1.0 board tiers)
    act = {"Z1": 0.7, "Z2": 0.5, "Z3": 0.4, "Z4": 0.6}.get(strategy_id, 0.6)
    gb = {"Z1": 0.45, "Z2": 0.50, "Z3": 0.40, "Z4": 0.50}.get(strategy_id, 0.5)
    entry_low = entry_features.get("recent_low") or entry_ask * 0.995
    break_lvl = entry_features.get("recent_high") or entry_ask
    mfe = mae = 0.0
    last_ts, last_bid = t0.ts, entry_ask
    reason = "capture_end"

    for j in range(entry_idx + 1, min(len(ticks), entry_idx + 500)):
        t = ticks[j]
        bid = t.board.canonical_best_bid
        if bid is None or bid <= 0:
            continue
        hold = (t.ts - t0.ts).total_seconds()
        if hold > MAX_HOLD_SEC:
            reason = "max_horizon"
            last_ts, last_bid = t.ts, bid
            break
        if _session(t.ts) != _session(t0.ts):
            reason = "session_close"
            last_ts, last_bid = t.ts, bid
            break
        pnl_pct = (bid - entry_ask) / entry_ask * 100.0
        mfe = max(mfe, pnl_pct)
        mae = min(mae, pnl_pct)
        if bid <= stop_px:
            return _pack(t.ts, bid, "hard_stop", mfe, mae, entry_ask, False)
        # invalidations by mode
        inv = False
        inv_reason = ""
        if exit_mode in ("X1", "X2", "X3", "X4", "X5", "X6"):
            if strategy_id == "Z1" and bid < float(entry_low):
                inv, inv_reason = True, "pullback_low_break"
            if strategy_id == "Z2" and bid < float(break_lvl) * 0.998:
                inv, inv_reason = True, "failed_breakout"
            if strategy_id == "Z3" and t.board.canonical_ask_qty and entry_features.get("canonical_ask_qty"):
                if (t.board.canonical_ask_qty or 0) > float(entry_features["canonical_ask_qty"]) * 1.2:
                    inv, inv_reason = True, "ask_wall_reform"
            if strategy_id == "Z4":
                rh = entry_features.get("recent_high")
                rl = entry_features.get("recent_low")
                if rh and rl and rl < bid < rh and hold > 15:
                    inv, inv_reason = True, "range_reentry"
        if inv and exit_mode == "X1":
            return _pack(t.ts, bid, inv_reason, mfe, mae, entry_ask, False)
        if inv and exit_mode in ("X2", "X4", "X5", "X6"):
            # flow confirm: downticks
            if (t.px < ticks[j - 1].px) or exit_mode == "X1":
                return _pack(t.ts, bid, inv_reason, mfe, mae, entry_ask, False)
        if inv and exit_mode in ("X3", "X4", "X5", "X6"):
            top = t.board.canonical_top_imbalance
            if top is not None and top < 0.45:
                return _pack(t.ts, bid, inv_reason + "+board", mfe, mae, entry_ask, False)
        # exhaustion profit
        if exit_mode in ("X5", "X6") and mfe >= act and pnl_pct <= mfe * (1.0 - gb):
            return _pack(t.ts, bid, "exhaustion_trailing", mfe, mae, entry_ask, False)
        if exit_mode == "X6":
            if pnl_pct > peak:
                peak = pnl_pct
            if not activated and peak >= act:
                activated = True
            if activated and peak > 0 and pnl_pct <= peak * (1.0 - gb):
                return _pack(t.ts, bid, "strategy_trailing", mfe, mae, entry_ask, False)
        last_ts, last_bid = t.ts, bid
    return _pack(last_ts, last_bid, reason, mfe, mae, entry_ask, reason in ("session_close", "capture_end", "max_horizon"))


def _pack(ts, bid, reason, mfe, mae, entry, operational):
    return {
        "evaluable": True,
        "exit_time": ts,
        "exit_bid": float(bid),
        "exit_reason": reason,
        "mfe": mfe,
        "mae": mae,
        "pnl_5bps": round(_pnl_yen(entry, float(bid)), 2),
        "hold_sec": None,  # filled by caller
        "operational": operational,
    }
