"""Diagnostic replay. Same heap order as frozen V2, plus the source signal index. Does not replace V2."""
from __future__ import annotations

import heapq
from typing import Any

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


def simulate_with_signal_index(
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
        yen = (float(exit_px) - float(pos["entry_px"])) * SHARES
        trades.append(
            {
                "date": day,
                "session": session,
                "symbol": sym,
                "entry_t": pos["entry_t"],
                "exit_t": exit_t,
                "entry_px": pos["entry_px"],
                "exit_px": float(exit_px),
                "reason": reason,
                "pnl_yen": yen,
                "ratchets": pos["ratchets"],
                "signal_index": int(pos["signal_index"]),
            }
        )
        _ = (path, realized)

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
        traded_today.add(sym)
        open_pos[sym] = {
            "entry_t": event_t,
            "entry_px": ask,
            "support": pre,
            "last_reconfirm_t": event_t,
            "ratchets": 0,
            "bt": book["bt"],
            "bb": book["bb"],
            "bn": book["bn"],
            "next_index": int(index) + 1,
            "exiting": False,
            "signal_index": int(index),
        }
        nxt = int(index) + 1
        if nxt < int(book["t"].size):
            heapq.heappush(heap, (float(book["t"][nxt]), 0, sym, "PATH", nxt))
    for sym, pos in list(open_pos.items()):
        found = _last_bid(pos["bt"], pos["bb"], pos["bn"], pos["entry_t"], sess_end)
        if found is None:
            open_pos.pop(sym)
            continue
        reason = pos.get("pending_reason") or pos.get("exit_reason") or "SESSION_FLAT"
        _release(sym, reason, found[0], found[1])
    return {"trades": trades}
