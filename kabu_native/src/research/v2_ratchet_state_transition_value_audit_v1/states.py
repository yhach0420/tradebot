"""Causal V2 ratchet states. Final ratchet_n is never used to build them."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.event_time_impulse_complete_strategy_v2.rules import (
    activity_lost,
    exhausted,
    participation_lost,
    ratchet,
    reconfirm,
    support_failure,
)
from research.event_time_impulse_complete_strategy_v2.simulate import _bid_at_or_after


def walk_position(book: dict[str, Any], signal_index: int) -> dict[str, Any]:
    """Frozen PATH order from the source signal. A same-event exit closes the new state."""
    times = book["t"]
    n = int(times.size)
    index = int(signal_index)
    support0 = float(book["pre_high"][index])
    support = support0
    last = float(times[index])
    ratchets: list[dict[str, Any]] = []
    exit_index = None
    exit_reason = None
    i = index + 1
    while i < n:
        price = float(book["px"][i])
        event_t = float(times[i])
        if not (price == price and price > 0.0):
            exit_index = i
            exit_reason = "FAIL_CLOSE_INVALID_DATA"
            break
        if reconfirm(
            bool(book["vol10"][i]),
            bool(book["vol30"][i]),
            bool(book["buy"][i]),
            float(book["classified"][i]),
            bool(book["tick"][i]),
            bool(book["break"][i]),
        ):
            last = event_t
            updated = ratchet(float(book["pre_high"][i]), support)
            if updated > support:
                support = float(updated)
                ratchets.append({"index": i, "t": event_t, "support": support, "px": price})
        if support_failure(price, support):
            exit_index = i
            exit_reason = "BREAK_SUPPORT_FAILURE"
            break
        if exhausted(
            participation_lost(float(book["classified"][i]), float(book["ask10"][i]), float(book["bid10"][i])),
            activity_lost(bool(book["vol10"][i]), bool(book["vol30"][i]), bool(book["tick"][i])),
            event_t - last,
        ):
            exit_index = i
            exit_reason = "IMPULSE_EXHAUSTED"
            break
        i += 1
    return {
        "support0": support0,
        "entry_index": index,
        "entry_t": float(times[index]),
        "entry_px_mark": float(book["px"][index]),
        "ratchets": ratchets,
        "ratchet_n": len(ratchets),
        "exit_index": exit_index,
        "exit_reason": exit_reason,
    }


def _extrema(book: dict[str, Any], t0: float, t1: float, ref: float) -> tuple[Optional[float], Optional[float]]:
    times = book["bt"]
    if int(times.size) == 0 or not (ref == ref and ref > 0):
        return None, None
    start = int(np.searchsorted(times, float(t0), side="left"))
    stop = int(np.searchsorted(times, float(t1), side="right"))
    peak = trough = float(ref)
    for index in range(start, stop):
        if int(book["bn"][index]) != index:
            continue
        price = float(book["bb"][index])
        event_t = float(times[index])
        if event_t + 1e-12 < t0 or event_t > t1 + 1e-12:
            continue
        if price == price and price > 0:
            peak = max(peak, price)
            trough = min(trough, price)
    scale = 10000.0 / float(ref)
    return (peak - ref) * scale, (trough - ref) * scale


def state_records(book: dict[str, Any], walked: dict[str, Any], trade: dict[str, Any], sess_end: float) -> list[dict[str, Any]]:
    """One row per state that is still open immediately after the transition."""
    ask_px = book["ask_px"]
    entry_ask = float(trade["entry_px"])
    final_yen = float(trade["pnl_yen"])
    final_bps = float(trade["bps"])
    ratchets = walked["ratchets"]
    arrivals = [
        {
            "k": 0,
            "index": int(walked["entry_index"]),
            "t": float(walked["entry_t"]),
            "support": float(walked["support0"]),
            "px": float(walked["entry_px_mark"]),
            "at_risk": True,
        }
    ]
    exit_index = walked["exit_index"]
    for j, item in enumerate(ratchets, start=1):
        arrivals.append(
            {
                "k": j,
                "index": int(item["index"]),
                "t": float(item["t"]),
                "support": float(item["support"]),
                "px": float(item["px"]),
                "at_risk": exit_index is None or int(exit_index) != int(item["index"]),
            }
        )
    rows: list[dict[str, Any]] = []
    for arr in arrivals:
        if not arr["at_risk"]:
            continue
        k = int(arr["k"])
        if k < len(ratchets):
            outcome = "NEXT_RATCHET"
            outcome_t = float(ratchets[k]["t"])
        elif exit_index is not None:
            outcome = "EXIT"
            outcome_t = float(book["t"][int(exit_index)])
        else:
            outcome = "SESSION_END"
            outcome_t = float(trade["exit_t"])
        bid_hit = _bid_at_or_after(book["bt"], book["bb"], book["bn"], float(arr["t"]), sess_end)
        bid = None if bid_hit is None else float(bid_hit[1])
        ask = float(ask_px[int(arr["index"])])
        if not (ask == ask and ask > 0):
            ask = None
        continue_yen = None if bid is None else final_yen - (bid - entry_ask) * 100.0
        continue_bps = None if bid is None or entry_ask <= 0 else final_bps - (bid / entry_ask - 1.0) * 10000.0
        mfe, mae = (None, None) if bid is None else _extrema(book, float(arr["t"]), outcome_t, bid)
        prev_t = None if k == 0 else float(arrivals[k - 1]["t"])
        prev_px = None if k == 0 else float(arrivals[k - 1]["px"])
        spread = None if bid is None or ask is None else ask - bid
        support = float(arr["support"])
        rows.append(
            {
                "state": k,
                "date": str(trade["date"]),
                "symbol": str(trade["symbol"]),
                "outcome": outcome,
                "dt_sec": outcome_t - float(arr["t"]),
                "continue_yen": continue_yen,
                "continue_bps": continue_bps,
                "mfe_bps": mfe,
                "mae_bps": mae,
                "px_minus_support": float(arr["px"]) - support,
                "bid_minus_support": None if bid is None else bid - support,
                "spread": spread,
                "classified": float(book["classified"][int(arr["index"])]),
                "ask10": float(book["ask10"][int(arr["index"])]),
                "bid10": float(book["bid10"][int(arr["index"])]),
                "activity": float(bool(book["vol10"][int(arr["index"])]) + bool(book["vol30"][int(arr["index"])]) + bool(book["tick"][int(arr["index"])])),
                "time_since_prev_sec": None if prev_t is None else float(arr["t"]) - prev_t,
                "price_advance": None if prev_px is None else float(arr["px"]) - prev_px,
                "support": support,
                "state_t": float(arr["t"]),
                "exit_now_yen": None if bid is None else (bid - entry_ask) * 100.0,
                "final_yen": final_yen,
            }
        )
    return rows


def self_check() -> None:
    from research.event_time_impulse_complete_strategy_v2.simulate import _book

    t = np.array([0.0, 10.0, 20.0, 30.0])
    book = _book(
        t,
        np.array([101.0, 110.0, 112.0, 90.0]),
        np.ones(4, dtype=bool),
        np.array([102.0, 111.0, 113.0, 91.0]),
        np.array([100.0, 105.0, 108.0, 108.0]),
        np.array([0]),
        t,
        np.array([100.0, 109.0, 111.0, 89.0]),
    )
    n = 4
    for key in ("vol30", "buy", "tick", "break"):
        book[key] = np.ones(n, dtype=bool)
    book["vol10"] = np.ones(n, dtype=bool)
    book["classified"] = np.ones(n)
    book["ask10"] = np.ones(n)
    book["bid10"] = np.zeros(n)
    book["pre_high"] = np.array([100.0, 105.0, 108.0, 108.0])
    walked = walk_position(book, 0)
    if walked["ratchet_n"] != 2 or walked["exit_reason"] != "BREAK_SUPPORT_FAILURE":
        raise RuntimeError(f"state_self_check_path:{walked}")
    trade = {"date": "20260722", "symbol": "1", "entry_px": 102.0, "pnl_yen": -1300.0, "bps": -100.0, "exit_t": 30.0, "entry_t": 0.0}
    rows = state_records(book, walked, trade, 100.0)
    ks = [int(r["state"]) for r in rows]
    if ks != [0, 1, 2] or rows[0]["outcome"] != "NEXT_RATCHET" or rows[2]["outcome"] != "EXIT":
        raise RuntimeError(f"state_self_check_rows:{rows}")
    book["px"] = np.array([101.0, 100.0, 112.0, 90.0])
    book["pre_high"] = np.array([100.0, 105.0, 108.0, 108.0])
    crashed = walk_position(book, 0)
    if crashed["ratchet_n"] != 1 or crashed["exit_index"] != 1:
        raise RuntimeError(f"state_self_check_same_event:{crashed}")
    closed = state_records(book, crashed, trade, 100.0)
    if [int(r["state"]) for r in closed] != [0] or closed[0]["outcome"] != "NEXT_RATCHET":
        raise RuntimeError(f"state_self_check_not_at_risk:{closed}")
