"""Enter on the first frozen V2 support ratchet. V2 itself is not edited.

Support semantics:
the shadow walk starts at the FULL signal's PRE_BREAK_HIGH.
The entry event is the first later event where frozen reconfirm is true and
ratchet() raises ACTIVE_SUPPORT. That raise is the pre-entry confirmation.
The position is born with that new support already applied, post_entry ratchet
count 0, and last_reconfirm_t equal to the entry event. Later PATH events may
ratchet again. The entry event is not fed through reconfirm a second time.

The same entry event still runs the frozen exit check after the support update.
A ratchet and a support failure on one event enter and then exit.

Watching does not take a slot. CAP and same-symbol are applied only when the
ratchet actually occurs.
"""
from __future__ import annotations

import heapq
from typing import Any, Optional

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
from research.event_time_impulse_v2_robustness_audit.latency import _at, _fill


def locate_first_ratchet(book: dict[str, Any], signal_index: int) -> Optional[dict[str, Any]]:
    """First causal support raise after this FULL signal, or None if the episode ends first."""
    times = book["t"]
    n = int(times.size)
    index = int(signal_index)
    support = float(book["pre_high"][index])
    if not (support == support):
        return None
    last = float(times[index])
    i = index + 1
    while i < n:
        price = float(book["px"][i])
        event_t = float(times[i])
        if not (price == price and price > 0.0):
            return None
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
                return {
                    "index": i,
                    "t": event_t,
                    "support": float(updated),
                    "signal_index": index,
                    "signal_t": float(times[index]),
                    "signal_ask": float(book["ask_px"][index]),
                }
        if support_failure(price, support):
            return None
        if exhausted(
            participation_lost(float(book["classified"][i]), float(book["ask10"][i]), float(book["bid10"][i])),
            activity_lost(bool(book["vol10"][i]), bool(book["vol30"][i]), bool(book["tick"][i])),
            event_t - last,
        ):
            return None
        i += 1
    return None


def plan_session(books: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    planned: list[dict[str, Any]] = []
    for sym, book in books.items():
        for source in book["signals"]:
            index = int(source)
            found = locate_first_ratchet(book, index)
            planned.append(
                {
                    "symbol": sym,
                    "signal_index": index,
                    "signal_t": float(book["t"][index]),
                    "signal_ask": float(book["ask_px"][index]),
                    "found": found,
                }
            )
    return planned


def simulate_reconfirm_entry(
    books: dict[str, dict[str, Any]],
    planned: list[dict[str, Any]],
    *,
    day: str,
    session: str,
    sess_end: float,
    traded_today: set[str],
    latency_sec: float,
    fill_model: str = "asof",
) -> dict[str, Any]:
    fill_pri = 1 if latency_sec == 0.0 else 2
    arm_pri = 2 if latency_sec == 0.0 else 3
    heap: list[tuple] = []
    open_pos: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    counts = {"cap": 0, "same_symbol": 0, "quote_miss": 0, "with_reconfirm": 0, "without_reconfirm": 0, "first_entry": 0, "reentry": 0}
    arms = {(item["symbol"], int(item["signal_index"])): item["found"] for item in planned}
    for item in planned:
        if item["found"] is None:
            counts["without_reconfirm"] += 1
            continue
        counts["with_reconfirm"] += 1
        found = item["found"]
        heapq.heappush(heap, (float(found["t"]), arm_pri, item["symbol"], "ARM", int(found["index"]), int(item["signal_index"])))

    def _emit(sym: str, pos: dict[str, Any], reason: str, exit_t: float, exit_px: float) -> None:
        entry_px = float(pos["entry_px"])
        trades.append(
            {
                "date": day,
                "session": session,
                "symbol": sym,
                "reason": reason,
                "pnl_yen": (float(exit_px) - entry_px) * SHARES,
                "bps": (float(exit_px) / entry_px - 1.0) * 10000.0,
                "entry_px": entry_px,
                "exit_px": float(exit_px),
                "entry_t": float(pos["entry_t"]),
                "exit_t": float(exit_t),
                "ratchets": int(pos["ratchets"]),
                "reentry": bool(pos["reentry"]),
                "signal_index": int(pos["signal_index"]),
                "signal_t": float(pos["signal_t"]),
                "signal_ask": float(pos["signal_ask"]),
                "reconfirm_t": float(pos["reconfirm_t"]),
                "delay_ms": (float(pos["reconfirm_t"]) - float(pos["signal_t"])) * 1000.0,
            }
        )

    def _schedule_exit(sym: str, pos: dict[str, Any], book: dict[str, Any], reason: str, event_t: float, index: int) -> None:
        if not pos.get("filled") and event_t + 1e-12 < float(pos["entry_fill_t"]):
            open_pos.pop(sym, None)
            return
        pos["exiting"] = True
        pos["exit_reason"] = reason
        target = max(event_t + latency_sec, float(pos["entry_fill_t"]))
        if latency_sec == 0.0:
            found = _bid_at_or_after(book["bt"], book["bb"], book["bn"], target, sess_end)
        else:
            found = _fill(book, "bid", target, sess_end, fill_model)
        if found is None:
            pos["pending_reason"] = reason
            return
        pos["exit_t"], pos["exit_px"] = found
        pos["fill_token"] = index
        heapq.heappush(heap, (float(found[0]), fill_pri, sym, "FILL", index, int(pos["signal_index"])))

    while heap:
        event_t, _pri, sym, kind, index, token = heapq.heappop(heap)
        book = books[sym]
        pos = open_pos.get(sym)
        if kind == "FILL":
            if pos is None or int(pos.get("fill_token", -2)) != int(index) or int(pos["signal_index"]) != int(token):
                continue
            _emit(sym, pos, str(pos["exit_reason"]), float(pos["exit_t"]), float(pos["exit_px"]))
            open_pos.pop(sym, None)
            continue
        if kind == "ENTRY":
            if pos is None or int(pos["signal_index"]) != int(token) or pos.get("filled"):
                continue
            pos["filled"] = True
            pos["entry_t"] = float(pos["entry_fill_t"])
            pos["entry_px"] = float(pos["entry_fill_px"])
            continue
        if kind == "ARM":
            if pos is not None:
                counts["same_symbol"] += 1
                continue
            if len(open_pos) >= MAX_CONCURRENT:
                counts["cap"] += 1
                continue
            found = arms.get((sym, int(token)))
            if found is None:
                continue
            if latency_sec == 0.0:
                ask = float(book["ask_px"][index])
                if not (ask == ask and ask > 0.0):
                    counts["quote_miss"] += 1
                    continue
                entry_fill_t, entry_fill_px, filled = event_t, ask, True
            else:
                hit = _fill(book, "ask", event_t + latency_sec, sess_end, fill_model)
                if hit is None:
                    counts["quote_miss"] += 1
                    continue
                entry_fill_t, entry_fill_px = hit
                filled = entry_fill_t <= event_t + 1e-9
            is_reentry = sym in traded_today
            traded_today.add(sym)
            if is_reentry:
                counts["reentry"] += 1
            else:
                counts["first_entry"] += 1
            open_pos[sym] = {
                "signal_index": int(token),
                "signal_t": float(found["signal_t"]),
                "signal_ask": float(found["signal_ask"]),
                "reconfirm_t": event_t,
                "entry_fill_t": float(entry_fill_t),
                "entry_fill_px": float(entry_fill_px),
                "filled": filled,
                "entry_t": float(entry_fill_t),
                "entry_px": float(entry_fill_px),
                "support": float(found["support"]),
                "last_reconfirm_t": event_t,
                "ratchets": 0,
                "reentry": is_reentry,
                "exiting": False,
                "next_index": int(index) + 1,
            }
            pos = open_pos[sym]
            if not filled:
                heapq.heappush(heap, (float(entry_fill_t), 1, sym, "ENTRY", int(index), int(token)))
            price = float(book["px"][index])
            reason = None
            if support_failure(price, pos["support"]):
                reason = "BREAK_SUPPORT_FAILURE"
            elif exhausted(
                participation_lost(float(book["classified"][index]), float(book["ask10"][index]), float(book["bid10"][index])),
                activity_lost(bool(book["vol10"][index]), bool(book["vol30"][index]), bool(book["tick"][index])),
                0.0,
            ):
                reason = "IMPULSE_EXHAUSTED"
            if reason is not None:
                _schedule_exit(sym, pos, book, reason, event_t, int(index))
                continue
            nxt = int(index) + 1
            if nxt < int(book["t"].size):
                heapq.heappush(heap, (float(book["t"][nxt]), 0, sym, "PATH", nxt, int(token)))
            continue
        if pos is None or pos.get("exiting") or int(pos["next_index"]) != int(index) or int(pos["signal_index"]) != int(token):
            continue
        price = float(book["px"][index])
        if not (price == price and price > 0.0):
            _schedule_exit(sym, pos, book, "FAIL_CLOSE_INVALID_DATA", event_t, int(index))
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
            _schedule_exit(sym, pos, book, reason, event_t, int(index))
            continue
        nxt = int(index) + 1
        if nxt < int(book["t"].size):
            pos["next_index"] = nxt
            heapq.heappush(heap, (float(book["t"][nxt]), 0, sym, "PATH", nxt, int(token)))
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
        reason = pos.get("pending_reason") or pos.get("exit_reason") or "SESSION_FLAT"
        _emit(sym, pos, reason, float(found[0]), float(found[1]))
        open_pos.pop(sym, None)
    return {"trades": trades, "counts": counts}
