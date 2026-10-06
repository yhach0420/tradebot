"""Robustness stresses. The frozen V2 simulator is not edited."""
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
from research.event_time_volume_confirmed_impulse import FRESH_SEC, MIN_QTY
from research.event_time_impulse_complete_strategy_v2.simulate import _bid_at_or_after, _last_bid
from research.event_time_volume_confirmed_impulse_entry.execution import bid_next

LATENCIES_MS = (0, 50, 100, 250, 500, 1000, 2000)


def _next_ok(mask: np.ndarray) -> np.ndarray:
    nxt = np.full(int(mask.size), -1, dtype=np.int32)
    last = -1
    for index in range(int(mask.size) - 1, -1, -1):
        if bool(mask[index]):
            last = index
        nxt[index] = last
    return nxt


def quote_masks(board: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    fresh = board["fresh_sec"]
    bid = board["bid"]
    ask = board["ask"]
    bid_qty = board["bid_qty"]
    ask_qty = board["ask_qty"]
    base = (
        board["executable"]
        & ~board["special"]
        & np.isfinite(fresh)
        & (fresh <= FRESH_SEC + 1e-12)
    )
    bid_ok = base & np.isfinite(bid) & (bid > 0) & np.isfinite(bid_qty) & (bid_qty >= MIN_QTY - 1e-12)
    two = (
        bid_ok
        & np.isfinite(ask)
        & (ask > 0)
        & (ask >= bid)
        & np.isfinite(ask_qty)
        & (ask_qty >= MIN_QTY - 1e-12)
    )
    return {"bid": _next_ok(bid_ok), "two": _next_ok(two)}


def _at(t: np.ndarray, nxt: np.ndarray, prices: np.ndarray, target: float, sess_end: float) -> Optional[tuple[float, float]]:
    if int(t.size) == 0 or target > sess_end + 1e-12:
        return None
    start = int(np.searchsorted(t, float(target), side="left"))
    if start >= int(t.size):
        return None
    index = int(nxt[start])
    if index < 0:
        return None
    event_t = float(t[index])
    if event_t + 1e-12 < target or event_t > sess_end + 1e-12:
        return None
    price = float(prices[index])
    if not (price == price and price > 0):
        return None
    return event_t, price


def _asof(t: np.ndarray, nxt: np.ndarray, prices: np.ndarray, fresh: np.ndarray, target: float, sess_end: float) -> Optional[tuple[float, float]]:
    """Latest valid quote already known at target. Age it with the existing 5s freshness rule."""
    if int(t.size) == 0 or target > sess_end + 1e-12:
        return None
    end = int(np.searchsorted(t, float(target), side="right")) - 1
    while end >= 0 and float(t[end]) > sess_end + 1e-12:
        end -= 1
    while end >= 0 and int(nxt[end]) != end:
        end -= 1
    if end < 0:
        return None
    age = float(fresh[end]) + (float(target) - float(t[end]))
    if not (age == age) or age > FRESH_SEC + 1e-12:
        return None
    price = float(prices[end])
    if not (price == price and price > 0):
        return None
    return float(target), price


def _fill(book: dict[str, Any], side: str, target: float, sess_end: float, fill_model: str) -> Optional[tuple[float, float]]:
    nxt = book["ask_nxt"] if side == "ask" else book["bid_nxt"]
    prices = book["board_ask"] if side == "ask" else book["board_bid"]
    if fill_model == "asof":
        hit = _asof(book["board_t"], nxt, prices, book["board_fresh"], target, sess_end)
        if hit is not None:
            return hit
    return _at(book["board_t"], nxt, prices, target, sess_end)


def _defer_class(book: dict[str, Any], signal_t: float, fill_t: float, sess_end: float) -> str:
    if fill_t > sess_end + 1e-9:
        return "SESSION_BOUNDARY"
    times = book["board_t"]
    start = int(np.searchsorted(times, float(signal_t), side="left"))
    if start >= int(times.size) or abs(float(times[start]) - float(signal_t)) > 1e-3:
        return "EXPECTED_NO_EXECUTABLE_BID"
    if int(book["bid_nxt"][start]) != start:
        return "EXPECTED_NO_EXECUTABLE_BID"
    return "OTHER"


def simulate_latency(
    books: dict[str, dict[str, Any]],
    *,
    day: str,
    session: str,
    sess_end: float,
    traded_today: set[str],
    latency_sec: float,
    fill_model: str = "next",
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
    counts = {"thesis_failed_before_fill": 0, "entry_pending_collision": 0, "exit_before_entry_fill": 0, "signal_while_entry_pending": 0, "entry_quote_miss": 0, "cap": 0, "same_symbol": 0}

    def _pending_open() -> bool:
        return any(not pos.get("filled") for pos in open_pos.values())

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
            entry_px = float(pos["entry_px"])
            exit_px = float(pos["exit_px"])
            exit_t = float(pos["exit_fill_t"])
            realized = (exit_px / entry_px - 1.0) * 10000.0
            initial = float(pos["initial_support"])
            final = float(pos["support"])
            trades.append(
                {
                    "date": day,
                    "session": session,
                    "symbol": sym,
                    "reason": pos["exit_reason"],
                    "pnl_yen": (exit_px - entry_px) * SHARES,
                    "bps": realized,
                    "hold_sec": exit_t - float(pos["entry_t"]),
                    "reentry": pos["reentry"],
                    "entry_px": entry_px,
                    "exit_px": exit_px,
                    "ratchets": pos["ratchets"],
                    "initial_support": initial,
                    "final_support": final,
                    "support_distance_bps": (entry_px / initial - 1.0) * 10000.0 if initial > 0 else None,
                    "exit_distance_bps": (exit_px / final - 1.0) * 10000.0 if final > 0 else None,
                    "time_to_first_ratchet": pos["time_to_first_ratchet"],
                    "entry_signal_t": float(pos["signal_t"]),
                    "entry_fill_t": float(pos["entry_t"]),
                    "exit_signal_t": float(pos["exit_signal_t"]) if pos.get("exit_signal_t") is not None else None,
                    "exit_fill_t": exit_t,
                    "entry_t": float(pos["entry_t"]),
                    "exit_t": exit_t,
                    "deferral_class": pos.get("deferral_class"),
                }
            )
            open_pos.pop(sym, None)
            continue
        if kind == "PATH":
            if pos is None or pos.get("done") or int(pos["next_index"]) != int(index):
                continue
            price = float(book["px"][index])
            if not (price == price and price > 0.0):
                pos["done"] = True
                pos["exit_reason"] = "FAIL_CLOSE_INVALID_DATA"
                continue
            if reconfirm(bool(book["vol10"][index]), bool(book["vol30"][index]), bool(book["buy"][index]), float(book["classified"][index]), bool(book["tick"][index]), bool(book["break"][index])):
                pos["last_reconfirm_t"] = event_t
                updated = ratchet(float(book["pre_high"][index]), pos["support"])
                if updated > pos["support"]:
                    if pos["first_ratchet_t"] is None:
                        pos["first_ratchet_t"] = event_t
                        pos["time_to_first_ratchet"] = event_t - float(pos["signal_t"])
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
                counts["thesis_failed_before_fill"] += 1
                counts["exit_before_entry_fill"] += 1
                open_pos.pop(sym, None)
                continue
            target = max(event_t + latency_sec, float(pos["entry_fill_t"]))
            if latency_sec == 0.0:
                found = _bid_at_or_after(book["bt"], book["bb"], book["bn"], target, sess_end)
            else:
                found = _fill(book, "bid", target, sess_end, fill_model)
            if found is None:
                # Frozen V2 keeps the thesis reason and closes with the last in-session bid.
                pos["done"] = True
                pos["pending_reason"] = reason
                continue
            pos["exit_fill_t"], pos["exit_px"] = found
            if found[0] > event_t + 1e-6:
                pos["deferral_class"] = _defer_class(book, event_t, found[0], sess_end)
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
        if _pending_open():
            counts["signal_while_entry_pending"] += 1
        if pos is not None and not pos.get("filled"):
            counts["entry_pending_collision"] += 1
            continue
        if pos is not None:
            counts["same_symbol"] += 1
            continue
        if len(open_pos) >= MAX_CONCURRENT:
            counts["cap"] += 1
            continue
        if not bool(book["ok"][index]):
            continue
        found = _fill(book, "ask", event_t + latency_sec, sess_end, fill_model)
        if found is None:
            counts["entry_quote_miss"] += 1
            continue
        pre = float(book["pre_high"][index])
        if not (pre == pre):
            continue
        is_reentry = sym in traded_today
        traded_today.add(sym)
        open_pos[sym] = {
            "signal_t": event_t,
            "signal_index": int(index),
            "entry_fill_t": found[0],
            "entry_fill_px": found[1],
            "filled": found[0] <= event_t + 1e-9,
            "entry_t": found[0],
            "entry_px": found[1],
            "initial_support": pre,
            "support": pre,
            "last_reconfirm_t": event_t,
            "ratchets": 0,
            "first_ratchet_t": None,
            "time_to_first_ratchet": None,
            "reentry": is_reentry,
            "done": False,
            "next_index": int(index) + 1,
        }
        if open_pos[sym]["filled"]:
            pass
        else:
            open_pos[sym]["token"] = int(index)
            heapq.heappush(heap, (found[0], 1, sym, "ENTRY", int(index)))
        nxt = int(index) + 1
        if nxt < int(book["t"].size):
            heapq.heappush(heap, (float(book["t"][nxt]), 0, sym, "PATH", nxt))
    for sym, pos in list(open_pos.items()):
        if not pos.get("filled"):
            counts["thesis_failed_before_fill"] += 1
            open_pos.pop(sym, None)
            continue
        book = books[sym]
        if latency_sec == 0.0:
            # Forced close uses the last causal executable bid inside the session.
            found = _last_bid(book["bt"], book["bb"], book["bn"], float(pos["entry_t"]), sess_end)
        else:
            # A delay past the bell is not filled outside the cash session.
            target = max(float(sess_end) + latency_sec, float(pos["entry_fill_t"]))
            found = _at(book["board_t"], book["bid_nxt"], book["board_bid"], target, sess_end)
        if found is None:
            open_pos.pop(sym, None)
            continue
        pos["exit_reason"] = pos.get("pending_reason") or pos.get("exit_reason") or "SESSION_FLAT"
        if pos.get("exit_signal_t") is None:
            pos["exit_signal_t"] = float(sess_end)
        pos["exit_fill_t"], pos["exit_px"] = found
        pos["token"] = -1
        entry_px = float(pos["entry_px"])
        exit_px = float(found[1])
        trades.append(
            {
                "date": day,
                "session": session,
                "symbol": sym,
                "reason": pos["exit_reason"],
                "pnl_yen": (exit_px - entry_px) * SHARES,
                "bps": (exit_px / entry_px - 1.0) * 10000.0,
                "hold_sec": found[0] - float(pos["entry_t"]),
                "reentry": pos["reentry"],
                "entry_px": entry_px,
                "exit_px": exit_px,
                "ratchets": pos["ratchets"],
                "initial_support": pos["initial_support"],
                "final_support": pos["support"],
                "support_distance_bps": (entry_px / float(pos["initial_support"]) - 1.0) * 10000.0,
                "exit_distance_bps": (exit_px / float(pos["support"]) - 1.0) * 10000.0,
                "time_to_first_ratchet": pos["time_to_first_ratchet"],
                "entry_signal_t": float(pos["signal_t"]),
                "entry_fill_t": float(pos["entry_t"]),
                "exit_signal_t": float(pos["exit_signal_t"]) if pos.get("exit_signal_t") is not None else float(sess_end),
                "exit_fill_t": float(found[0]),
                "entry_t": float(pos["entry_t"]),
                "exit_t": float(found[0]),
                "deferral_class": pos.get("deferral_class") or "SESSION_BOUNDARY",
            }
        )
        open_pos.pop(sym, None)
    return {"trades": trades, "counts": counts}


def attach_quote_index(book: dict[str, Any], board: dict[str, np.ndarray]) -> None:
    masks = quote_masks(board)
    book["board_t"] = board["t"]
    book["board_bid"] = board["bid"]
    book["board_ask"] = board["ask"]
    book["bid_nxt"] = masks["bid"]
    book["ask_nxt"] = masks["two"]
    book["board_fresh"] = board["fresh_sec"]
    book["bn"] = bid_next(board)
