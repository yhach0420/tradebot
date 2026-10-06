"""First causal reconfirm is the only entry. The initial FULL signal is a watch."""
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
from research.event_time_impulse_complete_strategy_v2.simulate import _bid_at_or_after, _excursions, _last_bid
from research.event_time_impulse_v2_robustness_audit.latency import _at, quote_masks

LATENCIES_MS = (0, 50, 100, 250, 500, 1000)


def _blank() -> dict[str, int]:
    return {
        "initial_full": 0,
        "watch_started": 0,
        "watch_invalidated": 0,
        "session_expired": 0,
        "first_reconfirm": 0,
        "entry_unavailable": 0,
        "raw_entry": 0,
        "cap": 0,
        "same_symbol": 0,
        "thesis_failed_before_fill": 0,
        "post_ratchet": 0,
    }


def simulate(
    books: dict[str, dict[str, Any]],
    *,
    day: str,
    session: str,
    sess_end: float,
    latency_sec: float,
) -> dict[str, Any]:
    heap: list[tuple] = []
    sig_ptr = {sym: 0 for sym in books}
    for sym, book in books.items():
        if int(book["signals"].size):
            index = int(book["signals"][0])
            heapq.heappush(heap, (float(book["t"][index]), 3, sym, "SIG", index))
    watches: dict[str, dict[str, Any]] = {}
    positions: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    counts = _blank()

    def _close(sym: str, reason: str, exit_t: float, exit_px: float) -> None:
        pos = positions.pop(sym)
        entry_px = float(pos["entry_px"])
        path = _excursions(pos["bt"], pos["bb"], pos["bn"], float(pos["entry_t"]), exit_t, entry_px)
        realized = (float(exit_px) / entry_px - 1.0) * 10000.0
        trades.append(
            {
                "date": day,
                "session": session,
                "symbol": sym,
                "reason": reason,
                "entry_t": float(pos["entry_t"]),
                "exit_t": exit_t,
                "entry_px": entry_px,
                "exit_px": float(exit_px),
                "pnl_yen": (float(exit_px) - entry_px) * SHARES,
                "bps": realized,
                "hold_sec": exit_t - float(pos["entry_t"]),
                "mfe": path["mfe"],
                "mae": path["mae"],
                "t_mfe": path["t_mfe"],
                "t_mae": path["t_mae"],
                "post_ratchets": int(pos["post_ratchets"]),
                "reentry": bool(pos["reentry"]),
            }
        )

    while heap:
        event_t, _pri, sym, kind, index = heapq.heappop(heap)
        book = books[sym]
        if kind == "ENTRY_FILL":
            pos = positions.get(sym)
            if pos is None or pos.get("token") != index or pos.get("filled"):
                continue
            pos["filled"] = True
            pos["entry_t"] = float(pos["entry_fill_t"])
            pos["entry_px"] = float(pos["entry_fill_px"])
            continue
        if kind == "EXIT_FILL":
            pos = positions.get(sym)
            if pos is None or not pos.get("filled") or pos.get("token") != index:
                continue
            _close(sym, pos["exit_reason"], float(pos["exit_fill_t"]), float(pos["exit_px"]))
            continue
        if kind == "TRY":
            pos = positions.get(sym)
            if pos is None or pos.get("token") != index or pos.get("admitted"):
                continue
            if len(positions) > MAX_CONCURRENT:
                counts["cap"] += 1
                positions.pop(sym, None)
                continue
            pos["admitted"] = True
            nxt = int(pos["signal_index"]) + 1
            if nxt < int(book["t"].size):
                pos["next_index"] = nxt
                heapq.heappush(heap, (float(book["t"][nxt]), 0, sym, "PATH", nxt))
            if pos["filled"]:
                continue
            pos["token"] = index
            heapq.heappush(heap, (float(pos["entry_fill_t"]), 1, sym, "ENTRY_FILL", index))
            continue
        if kind == "PATH":
            watch = watches.get(sym)
            pos = positions.get(sym)
            if watch is not None and int(watch["next_index"]) == int(index):
                price = float(book["px"][index])
                higher = float(book["pre_high"][index]) > float(watch["support"])
                confirmed = higher and reconfirm(
                    bool(book["vol10"][index]),
                    bool(book["vol30"][index]),
                    bool(book["buy"][index]),
                    float(book["classified"][index]),
                    bool(book["tick"][index]),
                    bool(book["break"][index]),
                )
                if support_failure(price, float(watch["support"])):
                    counts["watch_invalidated"] += 1
                    watches.pop(sym, None)
                    continue
                if confirmed:
                    counts["first_reconfirm"] += 1
                    watches.pop(sym, None)
                    heapq.heappush(heap, (event_t, 2, sym, "ARM", index))
                    continue
                nxt = int(index) + 1
                if nxt < int(book["t"].size):
                    watch["next_index"] = nxt
                    heapq.heappush(heap, (float(book["t"][nxt]), 0, sym, "PATH", nxt))
                continue
            if pos is None or pos.get("done") or int(pos.get("next_index") or -1) != int(index):
                continue
            price = float(book["px"][index])
            if not (price == price and price > 0):
                pos["done"] = True
                pos["exit_reason"] = "FAIL_CLOSE_INVALID_DATA"
                found = _at(book["board_t"], book["bid_nxt"], book["board_bid"], max(event_t + latency_sec, float(pos["entry_fill_t"])), sess_end)
                if pos.get("filled") and found is not None:
                    pos["exit_fill_t"], pos["exit_px"] = found
                    pos["token"] = index
                    heapq.heappush(heap, (found[0], 1, sym, "EXIT_FILL", index))
                elif not pos.get("filled"):
                    counts["thesis_failed_before_fill"] += 1
                    positions.pop(sym, None)
                else:
                    positions.pop(sym, None)
                continue
            if reconfirm(bool(book["vol10"][index]), bool(book["vol30"][index]), bool(book["buy"][index]), float(book["classified"][index]), bool(book["tick"][index]), bool(book["break"][index])):
                pos["last_reconfirm_t"] = event_t
                updated = ratchet(float(book["pre_high"][index]), float(pos["support"]))
                if updated > float(pos["support"]):
                    pos["support"] = updated
                    pos["post_ratchets"] += 1
                    counts["post_ratchet"] += 1
            reason = None
            if support_failure(price, float(pos["support"])):
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
            pos["exit_reason"] = reason
            if not pos.get("filled") and event_t + 1e-12 < float(pos["entry_fill_t"]):
                counts["thesis_failed_before_fill"] += 1
                positions.pop(sym, None)
                continue
            target = max(event_t + latency_sec, float(pos["entry_fill_t"]))
            found = _bid_at_or_after(pos["bt"], pos["bb"], pos["bn"], target, sess_end) if latency_sec == 0 else _at(book["board_t"], book["bid_nxt"], book["board_bid"], target, sess_end)
            if found is None:
                positions.pop(sym, None)
                continue
            pos["exit_fill_t"], pos["exit_px"] = found
            pos["token"] = index
            heapq.heappush(heap, (found[0], 1, sym, "EXIT_FILL", index))
            continue
        if kind == "ARM":
            if sym in positions or sym in watches:
                counts["same_symbol"] += 1
                continue
            if len(positions) >= MAX_CONCURRENT:
                counts["cap"] += 1
                continue
            if latency_sec == 0:
                if not bool(book["ok"][index]):
                    counts["entry_unavailable"] += 1
                    continue
                ask = float(book["ask_px"][index])
                fill_t, fill_px = event_t, ask
            else:
                found = _at(book["board_t"], book["ask_nxt"], book["board_ask"], event_t + latency_sec, sess_end)
                if found is None:
                    counts["entry_unavailable"] += 1
                    continue
                fill_t, fill_px = found
            pre = float(book["pre_high"][index])
            if not (fill_px == fill_px and fill_px > 0 and pre == pre):
                counts["entry_unavailable"] += 1
                continue
            counts["raw_entry"] += 1
            # Reserve the slot before later entries at this timestamp.
            positions[sym] = {
                "signal_t": event_t,
                "signal_index": int(index),
                "entry_fill_t": fill_t,
                "entry_fill_px": fill_px,
                "filled": fill_t <= event_t + 1e-9,
                "entry_t": fill_t,
                "entry_px": fill_px,
                "support": pre,
                "last_reconfirm_t": event_t,
                "post_ratchets": 0,
                "reentry": False,
                "done": False,
                "admitted": False,
                "token": int(index),
                "bt": book["bt"],
                "bb": book["bb"],
                "bn": book["bn"],
                "next_index": int(index),
            }
            heapq.heappush(heap, (event_t, 2, sym, "TRY", int(index)))
            continue
        signals = book["signals"]
        ptr = sig_ptr[sym]
        if ptr >= int(signals.size) or int(signals[ptr]) != int(index):
            continue
        sig_ptr[sym] = ptr + 1
        if ptr + 1 < int(signals.size):
            nxt = int(signals[ptr + 1])
            heapq.heappush(heap, (float(book["t"][nxt]), 3, sym, "SIG", nxt))
        counts["initial_full"] += 1
        if sym in positions or sym in watches:
            counts["same_symbol"] += 1
            continue
        pre = float(book["pre_high"][index])
        if not (pre == pre):
            continue
        counts["watch_started"] += 1
        watches[sym] = {"support": pre, "next_index": int(index) + 1, "start_t": event_t}
        nxt = int(index) + 1
        if nxt < int(book["t"].size):
            heapq.heappush(heap, (float(book["t"][nxt]), 0, sym, "PATH", nxt))
    for sym in list(watches):
        counts["session_expired"] += 1
        watches.pop(sym, None)
    for sym, pos in list(positions.items()):
        if not pos.get("filled"):
            counts["thesis_failed_before_fill"] += 1
            positions.pop(sym, None)
            continue
        if latency_sec == 0:
            found = _last_bid(pos["bt"], pos["bb"], pos["bn"], float(pos["entry_t"]), sess_end)
        else:
            found = _at(books[sym]["board_t"], books[sym]["bid_nxt"], books[sym]["board_bid"], max(sess_end + latency_sec, float(pos["entry_fill_t"])), sess_end)
        if found is None:
            positions.pop(sym, None)
            continue
        _close(sym, pos.get("exit_reason") or "SESSION_FLAT", found[0], found[1])
    return {"trades": trades, "counts": counts}


def attach(book: dict[str, Any], board: dict[str, np.ndarray]) -> None:
    masks = quote_masks(board)
    book["board_t"] = board["t"]
    book["board_bid"] = board["bid"]
    book["board_ask"] = board["ask"]
    book["bid_nxt"] = masks["bid"]
    book["ask_nxt"] = masks["two"]


def self_check() -> dict[str, bool]:
    def _one(px, pre, flags, ok, ask, signals):
        n = len(px)
        book = {
            "t": np.arange(n, dtype=float),
            "px": np.asarray(px, dtype=float),
            "ok": np.asarray(ok, dtype=bool),
            "ask_px": np.asarray(ask, dtype=float),
            "pre_high": np.asarray(pre, dtype=float),
            "vol10": np.asarray(flags, dtype=bool),
            "vol30": np.asarray(flags, dtype=bool),
            "buy": np.asarray(flags, dtype=bool),
            "classified": np.asarray([5.0 if flag else 0.0 for flag in flags]),
            "ask10": np.asarray([4.0 if flag else 0.0 for flag in flags]),
            "bid10": np.asarray([1.0 if flag else 0.0 for flag in flags]),
            "tick": np.asarray(flags, dtype=bool),
            "break": np.asarray(flags, dtype=bool),
            "signals": np.asarray(signals, dtype=np.int32),
            "bt": np.arange(n, dtype=float),
            "bb": np.asarray(px, dtype=float) - 1.0,
            "bn": np.arange(n, dtype=np.int32),
            "board_t": np.arange(n, dtype=float),
            "board_bid": np.asarray(px, dtype=float) - 1.0,
            "board_ask": np.asarray(ask, dtype=float),
            "bid_nxt": np.arange(n, dtype=np.int32),
            "ask_nxt": np.arange(n, dtype=np.int32),
        }
        return book

    # Price falls through the initial support before any higher reconfirm.
    invalidated = simulate(
        {"A": _one([101, 98], [99, 99], [True, False], [True, True], [100, 99], [0])},
        day="D",
        session="AM",
        sess_end=100,
        latency_sec=0,
    )
    # The second event reconfirms a higher pre-break high. The third event loses that new support.
    entered = simulate(
        {"A": _one([101, 103, 100], [99, 101, 101], [True, True, False], [True, True, True], [100, 102, 101], [0])},
        day="D",
        session="AM",
        sess_end=100,
        latency_sec=0,
    )
    trade = entered["trades"][0] if entered["trades"] else {}
    return {
        "initial_full_does_not_buy": invalidated["counts"]["watch_started"] == 1 and invalidated["counts"]["raw_entry"] == 0 and not invalidated["trades"],
        "watch_invalid_before_reconfirm": invalidated["counts"]["watch_invalidated"] == 1,
        "first_reconfirm_enters": entered["counts"]["first_reconfirm"] == 1 and len(entered["trades"]) == 1 and trade.get("entry_px") == 102.0,
        "exit_is_new_support": trade.get("reason") == "BREAK_SUPPORT_FAILURE",
    }
