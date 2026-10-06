"""Frozen V2 with one change: reconfirm does not move ACTIVE_SUPPORT_LEVEL.

last_reconfirm_t still updates, so the 30s soft exit is unchanged.
A shadow support records the ratchet that would have happened. Exit checks use the entry support.
"""
from __future__ import annotations

import heapq
from typing import Any

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
from research.event_time_impulse_v2_robustness_audit.latency import _at, _defer_class, _fill


def _observe_ratchet(pos: dict[str, Any], book: dict[str, Any], index: int) -> None:
    updated = ratchet(float(book["pre_high"][index]), float(pos["shadow_support"]))
    if updated > float(pos["shadow_support"]):
        pos["shadow_support"] = float(updated)
        pos["ratchets"] += 1
        pos["max_gap"] = max(float(pos["max_gap"]), float(updated) - float(pos["fixed_support"]))


def simulate_fixed_support(
    books: dict[str, dict[str, Any]],
    *,
    day: str,
    session: str,
    sess_end: float,
    traded_today: set[str],
) -> dict[str, Any]:
    heap: list[tuple] = []
    sig_ptr = {sym: 0 for sym in books}
    for sym, book in books.items():
        signals = book["signals"]
        if int(signals.size):
            index = int(signals[0])
            heapq.heappush(heap, (float(book["t"][index]), 2, sym, "SIG", index))
    open_pos: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []

    def _release(sym: str, reason: str, exit_t: float, exit_px: float) -> None:
        pos = open_pos.pop(sym)
        path = _excursions(pos["bt"], pos["bb"], pos["bn"], pos["entry_t"], exit_t, pos["entry_px"])
        realized = (float(exit_px) / float(pos["entry_px"]) - 1.0) * 10000.0
        peak = path["mfe"]
        trades.append(
            {
                "date": day,
                "session": session,
                "symbol": sym,
                "signal_index": int(pos["signal_index"]),
                "entry_t": float(pos["entry_t"]),
                "exit_t": float(exit_t),
                "entry_px": float(pos["entry_px"]),
                "exit_px": float(exit_px),
                "reason": reason,
                "pnl_yen": (float(exit_px) - float(pos["entry_px"])) * SHARES,
                "bps": realized,
                "hold_sec": float(exit_t) - float(pos["entry_t"]),
                "mfe": path["mfe"],
                "giveback": None if peak is None else float(peak) - realized,
                "ratchets": int(pos["ratchets"]),
                "reentry": bool(pos["reentry"]),
                "max_gap": float(pos["max_gap"]),
                "fixed_support": float(pos["fixed_support"]),
            }
        )

    while heap:
        event_t, _pri, sym, kind, index = heapq.heappop(heap)
        book = books[sym]
        if kind == "FILL":
            pos = open_pos.get(sym)
            if pos is None or pos.get("fill_token") != index:
                continue
            _release(sym, pos["exit_reason"], float(pos["exit_t"]), float(pos["exit_px"]))
            continue
        if kind == "PATH":
            pos = open_pos.get(sym)
            if pos is None or pos.get("exiting") or int(pos["next_index"]) != int(index):
                continue
            price = float(book["px"][index])
            if not (price == price and price > 0.0):
                found = _bid_at_or_after(pos["bt"], pos["bb"], pos["bn"], event_t, sess_end)
                if found is None:
                    pos["invalid_pending"] = True
                else:
                    pos["exiting"] = True
                    pos["exit_reason"] = "FAIL_CLOSE_INVALID_DATA"
                    pos["exit_t"], pos["exit_px"] = found
                    pos["fill_token"] = index
                    heapq.heappush(heap, (found[0], 1, sym, "FILL", index))
                continue
            if reconfirm(
                bool(book["vol10"][index]),
                bool(book["vol30"][index]),
                bool(book["buy"][index]),
                float(book["classified"][index]),
                bool(book["tick"][index]),
                bool(book["break"][index]),
            ):
                pos["last_reconfirm_t"] = event_t
                _observe_ratchet(pos, book, int(index))
            reason = None
            if support_failure(price, pos["support"]):
                reason = "BREAK_SUPPORT_FAILURE"
            elif exhausted(
                participation_lost(float(book["classified"][index]), float(book["ask10"][index]), float(book["bid10"][index])),
                activity_lost(bool(book["vol10"][index]), bool(book["vol30"][index]), bool(book["tick"][index])),
                event_t - float(pos["last_reconfirm_t"]),
            ):
                reason = "IMPULSE_EXHAUSTED"
            if reason is not None:
                found = _bid_at_or_after(pos["bt"], pos["bb"], pos["bn"], event_t, sess_end)
                if found is None:
                    pos["pending_reason"] = reason
                    pos["exiting"] = True
                else:
                    pos["exiting"] = True
                    pos["exit_reason"] = reason
                    pos["exit_t"], pos["exit_px"] = found
                    pos["fill_token"] = index
                    heapq.heappush(heap, (found[0], 1, sym, "FILL", index))
                continue
            nxt = int(index) + 1
            if nxt < int(book["t"].size):
                pos["next_index"] = nxt
                heapq.heappush(heap, (float(book["t"][nxt]), 0, sym, "PATH", nxt))
            continue
        signals = book["signals"]
        ptr = sig_ptr[sym]
        if ptr >= int(signals.size) or int(signals[ptr]) != int(index):
            continue
        sig_ptr[sym] = ptr + 1
        if ptr + 1 < int(signals.size):
            nxt = int(signals[ptr + 1])
            heapq.heappush(heap, (float(book["t"][nxt]), 2, sym, "SIG", nxt))
        if not bool(book["ok"][index]):
            continue
        pos = open_pos.get(sym)
        if pos is not None:
            continue
        if len(open_pos) >= MAX_CONCURRENT:
            continue
        ask = float(book["ask_px"][index])
        pre = float(book["pre_high"][index])
        if not (ask == ask and ask > 0 and pre == pre):
            continue
        is_reentry = sym in traded_today
        traded_today.add(sym)
        open_pos[sym] = {
            "signal_index": int(index),
            "entry_t": event_t,
            "entry_px": ask,
            "support": pre,
            "fixed_support": pre,
            "shadow_support": pre,
            "max_gap": 0.0,
            "last_reconfirm_t": event_t,
            "ratchets": 0,
            "reentry": is_reentry,
            "bt": book["bt"],
            "bb": book["bb"],
            "bn": book["bn"],
            "next_index": int(index) + 1,
            "exiting": False,
        }
        nxt = int(index) + 1
        if nxt < int(book["t"].size):
            heapq.heappush(heap, (float(book["t"][nxt]), 0, sym, "PATH", nxt))
    for sym, pos in list(open_pos.items()):
        found = _last_bid(pos["bt"], pos["bb"], pos["bn"], pos["entry_t"], sess_end)
        if found is None:
            open_pos.pop(sym, None)
            continue
        reason = pos.get("pending_reason") or pos.get("exit_reason") or "SESSION_FLAT"
        if pos.get("invalid_pending") and not pos.get("pending_reason"):
            reason = "FAIL_CLOSE_INVALID_DATA"
        _release(sym, reason, found[0], found[1])
    return {"trades": trades}


def simulate_fixed_support_latency(
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

    def _emit(sym: str, pos: dict[str, Any], exit_t: float, exit_px: float) -> None:
        entry_px = float(pos["entry_px"])
        trades.append(
            {
                "date": day,
                "session": session,
                "symbol": sym,
                "signal_index": int(pos["signal_index"]),
                "reason": pos["exit_reason"],
                "pnl_yen": (float(exit_px) - entry_px) * SHARES,
                "bps": (float(exit_px) / entry_px - 1.0) * 10000.0,
                "hold_sec": float(exit_t) - float(pos["entry_t"]),
                "entry_px": entry_px,
                "exit_px": float(exit_px),
                "entry_t": float(pos["entry_t"]),
                "exit_t": float(exit_t),
                "ratchets": int(pos["ratchets"]),
                "reentry": bool(pos["reentry"]),
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
            _emit(sym, pos, float(pos["exit_fill_t"]), float(pos["exit_px"]))
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
                _observe_ratchet(pos, book, int(index))
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
            pos["exit_reason"] = reason
            if not pos.get("filled") and event_t + 1e-12 < float(pos["entry_fill_t"]):
                open_pos.pop(sym, None)
                continue
            target = max(event_t + latency_sec, float(pos["entry_fill_t"]))
            found = _bid_at_or_after(book["bt"], book["bb"], book["bn"], target, sess_end) if latency_sec == 0.0 else _fill(book, "bid", target, sess_end, fill_model)
            if found is None:
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
        if pos is not None:
            continue
        if len(open_pos) >= MAX_CONCURRENT:
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
        open_pos[sym] = {
            "signal_t": event_t,
            "signal_index": int(index),
            "entry_fill_t": found[0],
            "entry_fill_px": found[1],
            "filled": found[0] <= event_t + 1e-9,
            "entry_t": found[0],
            "entry_px": found[1],
            "support": pre,
            "fixed_support": pre,
            "shadow_support": pre,
            "max_gap": 0.0,
            "last_reconfirm_t": event_t,
            "ratchets": 0,
            "reentry": is_reentry,
            "done": False,
            "next_index": int(index) + 1,
        }
        if not open_pos[sym]["filled"]:
            open_pos[sym]["token"] = int(index)
            heapq.heappush(heap, (found[0], 1, sym, "ENTRY", int(index)))
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
        _emit(sym, pos, float(found[0]), float(found[1]))
        open_pos.pop(sym, None)
    return {"trades": trades}


def self_check() -> None:
    from research.event_time_impulse_complete_strategy_v2.simulate import _book, simulate_session

    t = np.array([0.0, 10.0, 20.0])
    flags = np.array([True, True, False])
    book = _book(
        t,
        np.array([101.0, 110.0, 102.0]),
        np.ones(3, dtype=bool),
        np.array([102.0, 111.0, 103.0]),
        np.array([100.0, 105.0, 105.0]),
        np.array([0]),
        t,
        np.array([100.0, 109.0, 101.0]),
        vol10=flags,
        break_flag=flags,
    )
    book["vol30"] = flags.copy()
    book["buy"] = flags.copy()
    book["tick"] = flags.copy()
    book["classified"] = np.array([1.0, 1.0, 0.0])
    book["ask10"] = np.array([1.0, 1.0, 0.0])
    book["bid10"] = np.zeros(3)
    book["pre_high"] = np.array([100.0, 105.0, 105.0])
    frozen = simulate_session({"A": book}, day="D", session="AM", sess_end=100.0, traded_today=set())
    ablated = simulate_fixed_support({"A": book}, day="D", session="AM", sess_end=100.0, traded_today=set())
    base = [r for r in frozen["trades"] if r.get("pnl_yen") is not None]
    cand = ablated["trades"]
    if len(base) != 1 or base[0]["reason"] != "BREAK_SUPPORT_FAILURE":
        raise RuntimeError(f"ablation_self_check_frozen:{base}")
    if len(cand) != 1 or cand[0]["reason"] == "BREAK_SUPPORT_FAILURE" or int(cand[0]["ratchets"]) < 1:
        raise RuntimeError(f"ablation_self_check_candidate:{cand}")
    if abs(float(cand[0]["fixed_support"]) - 100.0) > 1e-9:
        raise RuntimeError("ablation_self_check_support_moved")
