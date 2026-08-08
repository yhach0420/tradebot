"""Quote snapshots, mid/exec returns, spreads, latency — no interpolation."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.e1_x28_executable_joint.board import (
    BOARD_FRESHNESS_SEC,
    MIN_QTY,
    first_valid_quote,
)

from . import HORIZONS_SEC, LATENCY_SEC_PRIMARY, LATENCY_SEC_REQUESTED


def _bps(num: float, den: float) -> float:
    return (num / den - 1.0) * 10000.0


def _row_ok(board: dict[str, np.ndarray], i: int, *, side: str) -> bool:
    if board["special"][i]:
        return False
    fresh = float(board["fresh_sec"][i]) if np.isfinite(board["fresh_sec"][i]) else 0.0
    if fresh > BOARD_FRESHNESS_SEC + 1e-12:
        return False
    qty_key = "ask_qty" if side == "ask" else "bid_qty"
    qty = board[qty_key][i]
    if not np.isfinite(qty) or qty < MIN_QTY:
        return False
    px = board["ask" if side == "ask" else "bid"][i]
    return bool(np.isfinite(px) and px > 0)


def first_valid_row(
    board: dict[str, np.ndarray],
    t0: float,
    *,
    window: float = 5.0,
    require_both: bool = False,
) -> Optional[int]:
    """Index of first valid ask (and optionally bid) at/after t0 within window."""
    t = board["t"]
    if t.size == 0:
        return None
    i0 = int(np.searchsorted(t, t0, side="left"))
    lim = t0 + window
    for i in range(i0, t.size):
        if float(t[i]) > lim + 1e-12:
            break
        if float(t[i]) + 1e-12 < t0:
            continue
        if not _row_ok(board, i, side="ask"):
            # special quote blocks like first_valid_quote
            if board["special"][i]:
                return None
            continue
        if require_both and not _row_ok(board, i, side="bid"):
            continue
        return i
    return None


def last_valid_at_or_before(
    board: dict[str, np.ndarray],
    t_h: float,
    *,
    side: str,
    t_min: float,
) -> Optional[int]:
    """Last valid quote index with t_min <= t <= t_h (X33B horizon exit contract)."""
    t = board["t"]
    if t.size == 0:
        return None
    j = int(np.searchsorted(t, t_h, side="right") - 1)
    while j >= 0:
        if float(t[j]) + 1e-12 < t_min:
            break
        if _row_ok(board, j, side=side):
            return j
        j -= 1
    return None


def last_both_at_or_before(
    board: dict[str, np.ndarray],
    t_h: float,
    *,
    t_min: float,
) -> Optional[int]:
    t = board["t"]
    if t.size == 0:
        return None
    j = int(np.searchsorted(t, t_h, side="right") - 1)
    while j >= 0:
        if float(t[j]) + 1e-12 < t_min:
            break
        if _row_ok(board, j, side="ask") and _row_ok(board, j, side="bid"):
            return j
        j -= 1
    return None


def evaluate_episode(
    board: dict[str, np.ndarray],
    *,
    date: str,
    session: str,
    signal_t: float,
    entry_delay: float = 0.0,
    exit_delay: float = 0.0,
) -> dict[str, Any]:
    """
    Economics for one anchor.

    ENTRY: first valid ask at signal_t + entry_delay (5s mapping window).
    For mid_t: prefer same board row with valid bid; else separate first valid bid.

    EXIT at horizon H (X33B identity): last valid bid at or before
        entry_ask_event_t + H + exit_delay
    MID_h: last row with both ask+bid at or before same timestamp.
    No linear interpolation of missing quotes.
    """
    sess_end = session_end_epoch(date, session)
    t_entry_signal = float(signal_t) + float(entry_delay)
    if t_entry_signal > sess_end + 1e-9:
        return {"ok": False, "reason": "SESSION_CLOSED"}

    qa = first_valid_quote(board, t_entry_signal, side="ask")
    if qa["status"] != "OK":
        return {"ok": False, "reason": qa["status"]}

    ask_t = float(qa["price"])
    entry_event_t = float(qa["event_time"])

    # Same-snapshot bid if possible
    i_entry = first_valid_row(board, t_entry_signal, require_both=True)
    if i_entry is not None and abs(float(board["t"][i_entry]) - entry_event_t) < 1e-9:
        bid_t = float(board["bid"][i_entry])
        mid_t = (ask_t + bid_t) / 2.0
        same_snap = True
    else:
        qb = first_valid_quote(board, t_entry_signal, side="bid")
        if qb["status"] != "OK":
            return {"ok": False, "reason": "NO_BID_FOR_MID"}
        bid_t = float(qb["price"])
        mid_t = (ask_t + bid_t) / 2.0
        same_snap = False

    out: dict[str, Any] = {
        "ok": True,
        "signal_t": float(signal_t),
        "entry_delay": float(entry_delay),
        "exit_delay": float(exit_delay),
        "ask_t": ask_t,
        "bid_t": bid_t,
        "mid_t": mid_t,
        "entry_event_t": entry_event_t,
        "same_snapshot_entry": same_snap,
        "entry_half_spread_bps": (ask_t - mid_t) / mid_t * 10000.0,
        "entry_spread_bps": (ask_t - bid_t) / mid_t * 10000.0,
        "entry_ask_delay_from_signal": float(entry_event_t - signal_t),
        "mapping_delay_sec": float(qa.get("delay_sec") or 0.0),
    }

    for H in HORIZONS_SEC:
        t_decision = entry_event_t + float(H) + float(exit_delay)
        if t_decision > sess_end + 1e-9:
            out[f"exec_{H}"] = None
            out[f"mid_{H}"] = None
            out[f"exec_valid_{H}"] = False
            out[f"mid_valid_{H}"] = False
            continue

        j_bid = last_valid_at_or_before(
            board, t_decision, side="bid", t_min=entry_event_t
        )
        if j_bid is not None:
            bid_h = float(board["bid"][j_bid])
            out[f"exec_{H}"] = _bps(bid_h, ask_t)
            out[f"exec_valid_{H}"] = True
            out[f"bid_h_{H}"] = bid_h
        else:
            out[f"exec_{H}"] = None
            out[f"exec_valid_{H}"] = False

        j_both = last_both_at_or_before(board, t_decision, t_min=entry_event_t)
        if j_both is not None:
            ask_h = float(board["ask"][j_both])
            bid_h2 = float(board["bid"][j_both])
            mid_h = (ask_h + bid_h2) / 2.0
            out[f"mid_{H}"] = _bps(mid_h, mid_t)
            out[f"mid_valid_{H}"] = True
            out[f"ask_h_{H}"] = ask_h
            out[f"exit_half_spread_bps_{H}"] = (mid_h - bid_h2) / mid_h * 10000.0
            if out.get(f"exec_valid_{H}"):
                out[f"drag_{H}"] = float(out[f"exec_{H}"]) - float(out[f"mid_{H}"])
                # Positive cost magnitude: half-spreads in bps
                spread_mag = (
                    float(out["entry_half_spread_bps"])
                    + float(out[f"exit_half_spread_bps_{H}"])
                )
                out[f"spread_only_drag_{H}"] = spread_mag
                # Identity: EXEC-MID ≈ -(entry_half+exit_half) + residual
                # => residual = DRAG + spread_mag (quote drift / ask≠mid denoms)
                out[f"residual_drag_{H}"] = float(out[f"drag_{H}"]) + spread_mag
        else:
            out[f"mid_{H}"] = None
            out[f"mid_valid_{H}"] = False
            out[f"exit_half_spread_bps_{H}"] = None
            out[f"drag_{H}"] = None
            out[f"spread_only_drag_{H}"] = None
            out[f"residual_drag_{H}"] = None

    return out


def board_resolution_audit(board_by_key: dict) -> dict[str, Any]:
    dts = []
    for board in board_by_key.values():
        t = board.get("t")
        if t is None or t.size < 20:
            continue
        d = np.diff(t.astype(float))
        d = d[d > 1e-6]
        if d.size:
            dts.append(d[:5000])  # cap per board
    if not dts:
        return {
            "status": "LATENCY_RESOLUTION_INSUFFICIENT",
            "median_dt_sec": None,
            "primary_latency_sec": list(LATENCY_SEC_PRIMARY),
        }
    all_dt = np.concatenate(dts)
    med = float(np.median(all_dt))
    p10 = float(np.quantile(all_dt, 0.10))
    insuff = [d for d in LATENCY_SEC_REQUESTED if d > 0 and d < max(0.4, med * 0.45)]
    return {
        "status": "OK",
        "n_intervals": int(all_dt.size),
        "median_dt_sec": med,
        "p10_dt_sec": p10,
        "p50_dt_sec": med,
        "insufficient_delays_sec": insuff,
        "marginal_delays_sec": [0.5] if 0.5 not in insuff else [],
        "primary_latency_sec": list(LATENCY_SEC_PRIMARY),
        "no_interpolation": True,
        "note": "first/last valid observed quote only; no fabricated mid/bid/ask",
    }
