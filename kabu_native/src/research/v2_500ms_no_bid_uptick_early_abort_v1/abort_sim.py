"""Frozen latency replay plus one early-abort overlay. V2 itself is not edited.

Boundary: PATH events with t <= signal_t+500ms run first (priority 0).
The abort check is priority 0.5 at exactly signal_t+500ms, before FILL (priority 2).
A ratchet or exit decision on an event at or before that boundary suppresses the abort.
Bid upticks use consecutive fresh bids on those events, strictly after the signal.
"""
from __future__ import annotations

import heapq
from typing import Any, Optional

import numpy as np

from research.event_time_impulse_complete_strategy_v2 import MAX_CONCURRENT, SHARES
from research.event_time_impulse_complete_strategy_v2.rules import (
    activity_lost,
    exhausted,
    participation_lost,
    ratchet,
    reconfirm,
    support_failure,
)
from research.event_time_impulse_complete_strategy_v2.simulate import _bid_at_or_after, _last_bid
from research.event_time_impulse_v2_robustness_audit.latency import _at, _defer_class, _fill
from research.event_time_volume_confirmed_impulse import FRESH_SEC

ABORT_SEC = 0.5


def fresh_bid(book: dict[str, Any], t: float) -> Optional[float]:
    times = book["board_t"]
    if int(times.size) == 0:
        return None
    j = int(np.searchsorted(times, float(t), side="right")) - 1
    if j < 0 or int(book["bid_nxt"][j]) != j:
        return None
    age = float(book["board_fresh"][j]) + (float(t) - float(times[j]))
    if not (age == age) or age > FRESH_SEC + 1e-12:
        return None
    price = float(book["board_bid"][j])
    if not (price == price and price > 0):
        return None
    return price


def simulate_abort(
    books: dict[str, dict[str, Any]],
    *,
    day: str,
    session: str,
    sess_end: float,
    traded_today: set[str],
    latency_sec: float,
    fill_model: str = "asof",
) -> dict[str, Any]:
    heap: list[tuple] = []
    sig_ptr = {sym: 0 for sym in books}
    for sym, book in books.items():
        signals = book["signals"]
        if int(signals.size):
            index = int(signals[0])
            heapq.heappush(heap, (float(book["t"][index]), 3, sym, "SIG", index))
    open_pos: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    counts = {"early_abort": 0, "cap": 0, "same_symbol": 0}

    def _emit(sym: str, pos: dict[str, Any]) -> None:
        entry_px = float(pos["entry_px"])
        exit_px = float(pos["exit_px"])
        exit_t = float(pos["exit_fill_t"])
        trades.append(
            {
                "date": day,
                "session": session,
                "symbol": sym,
                "reason": pos["exit_reason"],
                "pnl_yen": (exit_px - entry_px) * SHARES,
                "bps": (exit_px / entry_px - 1.0) * 10000.0,
                "entry_px": entry_px,
                "exit_px": exit_px,
                "entry_t": float(pos["entry_t"]),
                "exit_t": exit_t,
                "ratchets": int(pos["ratchets"]),
                "reentry": pos["reentry"],
                "signal_index": int(pos["signal_index"]),
                "signal_t": float(pos["signal_t"]),
            }
        )

    while heap:
        event_t, _pri, sym, kind, index = heapq.heappop(heap)
        book = books[sym]
        pos = open_pos.get(sym)
        if kind == "ENTRY":
            if pos is None or pos.get("token") != index or pos.get("filled"):
                continue
            pos["filled"] = True
            pos["entry_t"] = float(pos["entry_fill_t"])
            pos["entry_px"] = float(pos["entry_fill_px"])
            continue
        if kind == "EXIT":
            if pos is None or pos.get("token") != index or not pos.get("filled"):
                continue
            _emit(sym, pos)
            open_pos.pop(sym, None)
            continue
        if kind == "ABORT":
            if pos is None or pos.get("done") or int(pos["signal_index"]) != int(index) or not pos.get("filled"):
                continue
            if int(pos["ratchets"]) > 0 or pos.get("exit_reason"):
                continue
            if int(pos["bid_upticks"]) > 0:
                continue
            pos["done"] = True
            pos["exit_reason"] = "EARLY_ABORT"
            pos["exit_signal_t"] = float(pos["signal_t"]) + ABORT_SEC
            target = max(float(pos["exit_signal_t"]) + latency_sec, float(pos["entry_fill_t"]))
            if latency_sec == 0.0:
                found = _bid_at_or_after(book["bt"], book["bb"], book["bn"], target, sess_end)
            else:
                found = _fill(book, "bid", target, sess_end, fill_model)
            counts["early_abort"] += 1
            if found is None:
                pos["pending_reason"] = "EARLY_ABORT"
                continue
            pos["exit_fill_t"], pos["exit_px"] = found
            pos["token"] = index
            heapq.heappush(heap, (found[0], 2, sym, "EXIT", index))
            continue
        if kind == "PATH":
            if pos is None or pos.get("done") or int(pos["next_index"]) != int(index):
                continue
            price = float(book["px"][index])
            if float(pos["signal_t"]) < event_t <= float(pos["signal_t"]) + ABORT_SEC + 1e-12:
                bid = fresh_bid(book, event_t)
                if bid is not None:
                    prev = pos.get("prev_fresh_bid")
                    if prev is not None and bid > prev:
                        pos["bid_upticks"] += 1
                    pos["prev_fresh_bid"] = bid
            if not (price == price and price > 0.0):
                pos["done"] = True
                pos["exit_reason"] = "FAIL_CLOSE_INVALID_DATA"
                continue
            if reconfirm(bool(book["vol10"][index]), bool(book["vol30"][index]), bool(book["buy"][index]), float(book["classified"][index]), bool(book["tick"][index]), bool(book["break"][index])):
                pos["last_reconfirm_t"] = event_t
                updated = ratchet(float(book["pre_high"][index]), pos["support"])
                if updated > pos["support"]:
                    pos["support"] = updated
                    pos["ratchets"] += 1
            reason = None
            if support_failure(price, pos["support"]):
                reason = "BREAK_SUPPORT_FAILURE"
            elif exhausted(
                participation_lost(float(book["classified"][index]), float(book["ask10"][index]), float(book["bid10"][index])),
                activity_lost(bool(book["vol10"][index]), bool(book["vol30"][index]), bool(book["tick"][index])),
                event_t - float(pos["last_reconfirm_t"]),
            ):
                reason = "IMPULSE_EXHAUSTED"
            if reason is None:
                nxt = int(index) + 1
                if nxt < int(book["t"].size):
                    pos["next_index"] = nxt
                    heapq.heappush(heap, (float(book["t"][nxt]), 0, sym, "PATH", nxt))
                continue
            pos["done"] = True
            pos["exit_signal_t"] = event_t
            pos["exit_reason"] = reason
            if not pos.get("filled") and event_t + 1e-12 < float(pos["entry_fill_t"]):
                open_pos.pop(sym, None)
                continue
            target = max(event_t + latency_sec, float(pos["entry_fill_t"]))
            if latency_sec == 0.0:
                found = _bid_at_or_after(book["bt"], book["bb"], book["bn"], target, sess_end)
            else:
                found = _fill(book, "bid", target, sess_end, fill_model)
            if found is None:
                pos["pending_reason"] = reason
                continue
            pos["exit_fill_t"], pos["exit_px"] = found
            pos["token"] = index
            heapq.heappush(heap, (found[0], 2, sym, "EXIT", index))
            continue
        signals = book["signals"]
        ptr = sig_ptr[sym]
        if ptr >= int(signals.size) or int(signals[ptr]) != int(index):
            continue
        sig_ptr[sym] = ptr + 1
        if ptr + 1 < int(signals.size):
            nxt_i = int(signals[ptr + 1])
            heapq.heappush(heap, (float(book["t"][nxt_i]), 3, sym, "SIG", nxt_i))
        if pos is not None:
            counts["same_symbol"] += 1
            continue
        if len(open_pos) >= MAX_CONCURRENT:
            counts["cap"] += 1
            continue
        if not bool(book["ok"][index]):
            continue
        found = _fill(book, "ask", event_t + latency_sec, sess_end, fill_model if latency_sec else "asof")
        if found is None:
            continue
        pre = float(book["pre_high"][index])
        if not (pre == pre):
            continue
        is_reentry = sym in traded_today
        traded_today.add(sym)
        bid0 = fresh_bid(book, event_t)
        open_pos[sym] = {
            "signal_t": event_t,
            "signal_index": int(index),
            "entry_fill_t": found[0],
            "entry_fill_px": found[1],
            "filled": found[0] <= event_t + 1e-9,
            "entry_t": found[0],
            "entry_px": found[1],
            "support": pre,
            "last_reconfirm_t": event_t,
            "ratchets": 0,
            "reentry": is_reentry,
            "done": False,
            "next_index": int(index) + 1,
            "bid_upticks": 0,
            "prev_fresh_bid": bid0,
            "exit_reason": None,
        }
        if not open_pos[sym]["filled"]:
            open_pos[sym]["token"] = int(index)
            heapq.heappush(heap, (found[0], 1, sym, "ENTRY", int(index)))
        heapq.heappush(heap, (event_t + ABORT_SEC, 0.5, sym, "ABORT", int(index)))
        nxt = int(index) + 1
        if nxt < int(book["t"].size):
            heapq.heappush(heap, (float(book["t"][nxt]), 0, sym, "PATH", nxt))
    for sym, pos in list(open_pos.items()):
        if not pos.get("filled"):
            open_pos.pop(sym, None)
            continue
        book = books[sym]
        if latency_sec == 0.0:
            found = _last_bid(book["bt"], book["bb"], book["bn"], float(pos["entry_t"]), sess_end)
        else:
            target = max(float(sess_end) + latency_sec, float(pos["entry_fill_t"]))
            found = _at(book["board_t"], book["bid_nxt"], book["board_bid"], target, sess_end)
        if found is None:
            open_pos.pop(sym, None)
            continue
        pos["exit_reason"] = pos.get("pending_reason") or pos.get("exit_reason") or "SESSION_FLAT"
        pos["exit_fill_t"], pos["exit_px"] = found
        _emit(sym, pos)
        open_pos.pop(sym, None)
    return {"trades": trades, "counts": counts}
