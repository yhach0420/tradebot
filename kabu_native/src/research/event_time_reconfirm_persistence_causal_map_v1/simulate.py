"""Enter only when the current reconfirm ordinal equals the predeclared target."""
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
from research.event_time_impulse_complete_strategy_v2.simulate import _excursions
from research.event_time_impulse_v2_robustness_audit.latency import _at, quote_masks


def _blank() -> dict[str, int]:
    return {
        "initial_full": 0,
        "watch_started": 0,
        "watch_invalidated": 0,
        "session_expired": 0,
        "reached": 0,
        "entry_unavailable": 0,
        "raw_entry": 0,
        "cap": 0,
        "same_symbol": 0,
        "thesis_failed_before_fill": 0,
    }


def _quote(book: dict[str, Any], side: str, target: float, limit: float) -> Optional[tuple[float, float]]:
    nxt = book["ask_nxt"] if side == "ask" else book["bid_nxt"]
    prices = book["board_ask"] if side == "ask" else book["board_bid"]
    return _at(book["board_t"], nxt, prices, target, limit)


def simulate(
    books: dict[str, dict[str, Any]],
    *,
    day: str,
    session: str,
    sess_end: float,
    latency_sec: float,
    target_ordinal: int,
) -> dict[str, Any]:
    if target_ordinal not in (2, 3, 4):
        raise ValueError(target_ordinal)
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
    limit = float(sess_end) + float(latency_sec)

    def _close(sym: str, reason: str, exit_t: float, exit_px: float) -> None:
        pos = positions.pop(sym)
        entry_px = float(pos["entry_px"])
        path = _excursions(pos["bt"], pos["bb"], pos["bn"], float(pos["entry_t"]), exit_t, entry_px)
        trades.append(
            {
                "date": day,
                "session": session,
                "symbol": sym,
                "reason": reason,
                "entry_ordinal": int(target_ordinal),
                "entry_signal_t": float(pos["signal_t"]),
                "entry_signal_index": int(pos["signal_index"]),
                "entry_t": float(pos["entry_t"]),
                "exit_t": exit_t,
                "entry_px": entry_px,
                "exit_px": float(exit_px),
                "pnl_yen": (float(exit_px) - entry_px) * SHARES,
                "bps": (float(exit_px) / entry_px - 1.0) * 10000.0,
                "hold_sec": exit_t - float(pos["entry_t"]),
                "mfe": path["mfe"],
                "mae": path["mae"],
                "post_ratchets": int(pos["post_ratchets"]),
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
                pre = float(book["pre_high"][index])
                higher = pre > float(watch["support"])
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
                    watch["ordinal"] += 1
                    watch["support"] = pre
                    if int(watch["ordinal"]) == int(target_ordinal):
                        counts["reached"] += 1
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
                found = _quote(book, "bid", max(event_t + latency_sec, float(pos["entry_fill_t"])), limit)
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
            if reconfirm(
                bool(book["vol10"][index]),
                bool(book["vol30"][index]),
                bool(book["buy"][index]),
                float(book["classified"][index]),
                bool(book["tick"][index]),
                bool(book["break"][index]),
            ):
                pos["last_reconfirm_t"] = event_t
                updated = ratchet(float(book["pre_high"][index]), float(pos["support"]))
                if updated > float(pos["support"]):
                    pos["support"] = updated
                    pos["post_ratchets"] += 1
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
            found = _quote(book, "bid", target, limit)
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
            found = _quote(book, "ask", event_t + latency_sec, limit)
            if found is None:
                counts["entry_unavailable"] += 1
                continue
            fill_t, fill_px = found
            pre = float(book["pre_high"][index])
            if not (fill_px == fill_px and fill_px > 0 and pre == pre):
                counts["entry_unavailable"] += 1
                continue
            counts["raw_entry"] += 1
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
        watches[sym] = {"support": pre, "ordinal": 0, "next_index": int(index) + 1, "start_t": event_t}
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
        found = _quote(books[sym], "bid", max(float(sess_end) + latency_sec, float(pos["entry_fill_t"])), limit)
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


def _book(times, px, pre, flags, ask) -> dict[str, Any]:
    n = len(times)
    flags_a = np.asarray(flags, dtype=bool)
    quote_t = np.asarray(list(times) + [float(t) + 0.1 for t in times], dtype=float)
    order = np.argsort(quote_t, kind="mergesort")
    quote_t = quote_t[order]
    ask_px = np.asarray(list(ask) + list(ask), dtype=float)[order]
    bid_px = ask_px - 1.0
    nxt = np.arange(quote_t.size, dtype=np.int32)
    return {
        "t": np.asarray(times, dtype=float),
        "px": np.asarray(px, dtype=float),
        "pre_high": np.asarray(pre, dtype=float),
        "vol10": flags_a,
        "vol30": flags_a,
        "buy": flags_a,
        "classified": np.asarray([5.0 if flag else 0.0 for flag in flags]),
        "ask10": np.asarray([4.0 if flag else 0.0 for flag in flags]),
        "bid10": np.asarray([1.0 if flag else 0.0 for flag in flags]),
        "tick": flags_a,
        "break": flags_a,
        "signals": np.asarray([0], dtype=np.int32),
        "bt": quote_t,
        "bb": bid_px,
        "bn": nxt,
        "board_t": quote_t,
        "board_bid": bid_px,
        "board_ask": ask_px,
        "bid_nxt": nxt,
        "ask_nxt": nxt,
    }


def self_check() -> dict[str, bool]:
    invalid = _book([0, 10], [101, 98], [99, 99], [True, False], [100, 99])
    one = _book([0, 10, 20], [101, 103, 100], [99, 101, 101], [True, True, False], [100, 102, 101])
    pair = _book([0, 10, 20, 30], [101, 103, 105, 100], [99, 101, 103, 103], [True, True, True, False], [100, 102, 104, 101])
    four = _book(
        [0, 10, 20, 30, 40, 50],
        [101, 103, 105, 107, 109, 100],
        [99, 101, 103, 105, 107, 107],
        [True, True, True, True, True, False],
        [100, 102, 104, 106, 108, 101],
    )
    killed = simulate({"A": invalid}, day="D", session="AM", sess_end=100, latency_sec=0.1, target_ordinal=2)
    early = simulate({"A": one}, day="D", session="AM", sess_end=100, latency_sec=0.1, target_ordinal=2)
    pair_r2 = simulate({"A": pair}, day="D", session="AM", sess_end=100, latency_sec=0.1, target_ordinal=2)
    pair_r3 = simulate({"A": pair}, day="D", session="AM", sess_end=100, latency_sec=0.1, target_ordinal=3)
    r2 = simulate({"A": four}, day="D", session="AM", sess_end=100, latency_sec=0.1, target_ordinal=2)
    r3 = simulate({"A": four}, day="D", session="AM", sess_end=100, latency_sec=0.1, target_ordinal=3)
    r4 = simulate({"A": four}, day="D", session="AM", sess_end=100, latency_sec=0.1, target_ordinal=4)
    t2 = r2["trades"][0] if r2["trades"] else {}
    t3 = r3["trades"][0] if r3["trades"] else {}
    t4 = r4["trades"][0] if r4["trades"] else {}
    return {
        "initial_full_does_not_buy": killed["counts"]["watch_started"] == 1 and killed["counts"]["raw_entry"] == 0 and not killed["trades"],
        "watch_invalid_before_r2": killed["counts"]["watch_invalidated"] == 1 and early["counts"]["reached"] == 0 and not early["trades"],
        "r2_enters_at_second_event": t2.get("entry_signal_t") == 20.0 and t2.get("entry_px") == 104.0 and t2.get("entry_ordinal") == 2,
        "r3_enters_at_third_event": t3.get("entry_signal_t") == 30.0 and t3.get("entry_px") == 106.0,
        "r4_enters_at_fourth_event": t4.get("entry_signal_t") == 40.0 and t4.get("entry_px") == 108.0,
        "later_reconfirms_do_not_move_r2_entry": t2.get("entry_signal_index") == 2 and int(t2.get("post_ratchets") or 0) >= 1,
        "r2_does_not_require_r4": pair_r2["counts"]["reached"] == 1 and len(pair_r2["trades"]) == 1 and pair_r3["counts"]["reached"] == 0 and not pair_r3["trades"],
        "exit_uses_entry_support": t2.get("reason") == "BREAK_SUPPORT_FAILURE" and t4.get("reason") == "BREAK_SUPPORT_FAILURE",
    }
