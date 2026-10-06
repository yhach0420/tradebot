"""V2 session engine with ACTIVE_SUPPORT fixed at entry. No order submission."""
from __future__ import annotations

import heapq
from typing import Any

import numpy as np

from research.event_time_impulse_complete_strategy_v2.rules import (
    activity_lost,
    exhausted,
    participation_lost,
    ratchet,
    reconfirm,
    support_failure,
)
from research.event_time_impulse_complete_strategy_v2.simulate import _bid_at_or_after, _last_bid
from research.event_time_impulse_fixed_entry_support_candidate_v1 import CANCEL, LIVE, MAX_CONCURRENT, SHARES, SUBMIT


def assert_no_orders() -> None:
    if SUBMIT or CANCEL or LIVE:
        raise RuntimeError("candidate_order_path_forbidden")


def simulate_session(
    books: dict[str, dict[str, Any]],
    *,
    day: str,
    session: str,
    sess_end: float,
    traded_today: set[str],
) -> dict[str, Any]:
    """Same event order as frozen V2. Reconfirm updates the soft-exit clock and not support."""
    assert_no_orders()
    heap: list[tuple] = []
    sig_ptr = {sym: 0 for sym in books}
    for sym, book in books.items():
        signals = book["signals"]
        if int(signals.size):
            index = int(signals[0])
            heapq.heappush(heap, (float(book["t"][index]), 2, sym, "SIG", index))
    open_pos: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    counts = {"cap": 0, "same_symbol": 0, "occupancy": 0, "reentry": 0, "session_flat": 0}

    def _release(sym: str, reason: str, exit_t: float, exit_px: float) -> None:
        pos = open_pos.pop(sym)
        if float(pos["support"]) != float(pos["initial_support"]):
            raise RuntimeError("active_support_moved")
        if reason == "SESSION_FLAT":
            counts["session_flat"] += 1
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
                "reconfirm_raises": int(pos["reconfirm_raises"]),
                "initial_support": float(pos["initial_support"]),
                "final_support": float(pos["support"]),
                "reentry": bool(pos["reentry"]),
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
                observed = ratchet(float(book["pre_high"][index]), float(pos["observed_support"]))
                if observed > float(pos["observed_support"]):
                    pos["observed_support"] = float(observed)
                    pos["reconfirm_raises"] += 1
            reason = None
            if support_failure(price, float(pos["support"])):
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
            if pos.get("exiting"):
                counts["occupancy"] += 1
            else:
                counts["same_symbol"] += 1
            continue
        if len(open_pos) >= MAX_CONCURRENT:
            counts["cap"] += 1
            continue
        ask = float(book["ask_px"][index])
        pre = float(book["pre_high"][index])
        if not (ask == ask and ask > 0 and pre == pre):
            continue
        is_reentry = sym in traded_today
        if is_reentry:
            counts["reentry"] += 1
        traded_today.add(sym)
        open_pos[sym] = {
            "signal_index": int(index),
            "entry_t": event_t,
            "entry_px": ask,
            "support": pre,
            "initial_support": pre,
            "observed_support": pre,
            "last_reconfirm_t": event_t,
            "reconfirm_raises": 0,
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
    return {"trades": trades, "counts": counts}


def self_check() -> None:
    from research.event_time_impulse_complete_strategy_v2.simulate import _book, simulate_session as frozen_session
    from research.v2_support_ratchet_causal_ablation_v1.ablation_sim import simulate_fixed_support

    t = np.array([0.0, 10.0, 20.0])
    flags = np.array([True, True, False])
    book = _book(
        t, np.array([101.0, 110.0, 102.0]), np.ones(3, dtype=bool),
        np.array([102.0, 111.0, 103.0]), np.array([100.0, 105.0, 105.0]), np.array([0]),
        t, np.array([100.0, 109.0, 101.0]), vol10=flags, break_flag=flags,
    )
    book["vol30"] = flags.copy()
    book["buy"] = flags.copy()
    book["tick"] = flags.copy()
    book["classified"] = np.array([1.0, 1.0, 0.0])
    book["ask10"] = np.array([1.0, 1.0, 0.0])
    book["bid10"] = np.zeros(3)
    book["pre_high"] = np.array([100.0, 105.0, 105.0])
    frozen = frozen_session({"A": book}, day="D", session="AM", sess_end=100.0, traded_today=set())
    research = simulate_fixed_support({"A": book}, day="D", session="AM", sess_end=100.0, traded_today=set())
    impl = simulate_session({"A": book}, day="D", session="AM", sess_end=100.0, traded_today=set())
    base = [r for r in frozen["trades"] if r.get("pnl_yen") is not None]
    if len(base) != 1 or base[0]["reason"] != "BREAK_SUPPORT_FAILURE":
        raise RuntimeError("candidate_self_check_frozen")
    got = impl["trades"]
    ref = research["trades"]
    if len(got) != 1 or len(ref) != 1:
        raise RuntimeError("candidate_self_check_count")
    if got[0]["reason"] == "BREAK_SUPPORT_FAILURE" or got[0]["initial_support"] != got[0]["final_support"]:
        raise RuntimeError("candidate_self_check_support")
    if int(got[0]["reconfirm_raises"]) != int(ref[0]["ratchets"]) or got[0]["reason"] != ref[0]["reason"]:
        raise RuntimeError("candidate_self_check_parity")
