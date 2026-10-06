"""Session occupancy. Exit and release happen before a new entry at the same time."""
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
from research.event_time_volume_confirmed_impulse_entry.execution import bid_next


def _bid_at_or_after(t: np.ndarray, bid: np.ndarray, nxt: np.ndarray, target: float, sess_end: float) -> Optional[tuple[float, float]]:
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
    price = float(bid[index])
    if not (price == price and price > 0):
        return None
    return event_t, price


def _last_bid(t: np.ndarray, bid: np.ndarray, nxt: np.ndarray, entry_t: float, sess_end: float) -> Optional[tuple[float, float]]:
    if int(t.size) == 0:
        return None
    end = int(np.searchsorted(t, float(sess_end), side="right")) - 1
    while end >= 0 and float(t[end]) > sess_end + 1e-12:
        end -= 1
    if end < 0:
        return None
    index = int(nxt[0]) if float(t[0]) >= entry_t - 1e-12 else -1
    # Walk back to the last valid bid at or after entry.
    found = None
    start = int(np.searchsorted(t, float(entry_t), side="left"))
    for index in range(start, end + 1):
        nxt_i = int(nxt[index])
        if nxt_i < 0 or nxt_i != index:
            continue
        if float(t[index]) > sess_end + 1e-12:
            break
        found = (float(t[index]), float(bid[index]))
    return found


def _excursions(t: np.ndarray, bid: np.ndarray, nxt: np.ndarray, entry_t: float, exit_t: float, entry_px: float) -> dict[str, Any]:
    empty = {"mfe": None, "mae": None, "t_mfe": None, "t_mae": None, "mfe_before": None, "mae_before": None, "peak": None}
    if not (entry_px == entry_px and entry_px > 0) or int(t.size) == 0:
        return empty
    start = int(np.searchsorted(t, float(entry_t), side="left"))
    stop = int(np.searchsorted(t, float(exit_t), side="right"))
    span = 10000.0 / float(entry_px)
    peak = None
    trough = None
    t_peak = None
    t_trough = None
    before_peak = None
    before_trough = None
    for index in range(start, stop):
        if int(nxt[index]) != index:
            continue
        price = float(bid[index])
        event_t = float(t[index])
        if event_t > exit_t + 1e-12:
            break
        if peak is None or price > peak:
            peak = price
            t_peak = event_t
        if trough is None or price < trough:
            trough = price
            t_trough = event_t
        if event_t + 1e-12 < exit_t:
            if before_peak is None or price > before_peak:
                before_peak = price
            if before_trough is None or price < before_trough:
                before_trough = price
    if peak is None:
        return empty
    return {
        "mfe": (peak - entry_px) * span,
        "mae": (trough - entry_px) * span,
        "t_mfe": None if t_peak is None else t_peak - entry_t,
        "t_mae": None if t_trough is None else t_trough - entry_t,
        "mfe_before": None if before_peak is None else (before_peak - entry_px) * span,
        "mae_before": None if before_trough is None else (before_trough - entry_px) * span,
        "peak": peak,
    }


def simulate_session(
    books: dict[str, dict[str, Any]],
    *,
    day: str,
    session: str,
    sess_end: float,
    traded_today: set[str],
) -> dict[str, Any]:
    """books[symbol] holds feature arrays, full-signal indices, and the bid board."""
    heap: list[tuple] = []
    sig_ptr = {sym: 0 for sym in books}
    for sym, book in books.items():
        signals = book["signals"]
        if int(signals.size):
            index = int(signals[0])
            heapq.heappush(heap, (float(book["t"][index]), 2, sym, "SIG", index))
    open_pos: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    counts = {
        "full": 0,
        "valid": 0,
        "unavailable": 0,
        "raw_entry": 0,
        "same_symbol": 0,
        "occupancy": 0,
        "cap": 0,
        "first_entry": 0,
        "reentry": 0,
        "ratchet": 0,
    }

    def _release(sym: str, reason: str, exit_t: float, exit_px: float) -> None:
        pos = open_pos.pop(sym)
        path = _excursions(pos["bt"], pos["bb"], pos["bn"], pos["entry_t"], exit_t, pos["entry_px"])
        realized = (float(exit_px) / float(pos["entry_px"]) - 1.0) * 10000.0
        yen = (float(exit_px) - float(pos["entry_px"])) * SHARES
        peak_bps = path["mfe"]
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
                "bps": realized,
                "hold_sec": exit_t - pos["entry_t"],
                "mfe": path["mfe"],
                "mae": path["mae"],
                "t_mfe": path["t_mfe"],
                "t_mae": path["t_mae"],
                "mfe_before": path["mfe_before"],
                "mae_before": path["mae_before"],
                "giveback": None if peak_bps is None else float(peak_bps) - realized,
                "spread_bps": pos["spread_bps"],
                "ratchets": pos["ratchets"],
                "reentry": pos["reentry"],
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
                updated = ratchet(float(book["pre_high"][index]), pos["support"])
                if updated > pos["support"]:
                    pos["support"] = updated
                    pos["ratchets"] += 1
                    counts["ratchet"] += 1
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
        # SIG
        signals = book["signals"]
        ptr = sig_ptr[sym]
        if ptr >= int(signals.size) or int(signals[ptr]) != int(index):
            continue
        sig_ptr[sym] = ptr + 1
        if ptr + 1 < int(signals.size):
            nxt = int(signals[ptr + 1])
            heapq.heappush(heap, (float(book["t"][nxt]), 2, sym, "SIG", nxt))
        counts["full"] += 1
        if not bool(book["ok"][index]):
            counts["unavailable"] += 1
            continue
        counts["valid"] += 1
        counts["raw_entry"] += 1
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
            counts["unavailable"] += 1
            counts["valid"] -= 1
            counts["raw_entry"] -= 1
            continue
        is_reentry = sym in traded_today
        if is_reentry:
            counts["reentry"] += 1
        else:
            counts["first_entry"] += 1
        traded_today.add(sym)
        spread = float(book["spread"][index]) if book["spread"][index] == book["spread"][index] else np.nan
        open_pos[sym] = {
            "entry_t": event_t,
            "entry_px": ask,
            "support": pre,
            "last_reconfirm_t": event_t,
            "ratchets": 0,
            "reentry": is_reentry,
            "spread_bps": None if not (spread == spread) else spread / ask * 10000.0,
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
            open_pos.pop(sym)
            trades.append(
                {
                    "date": day,
                    "session": session,
                    "symbol": sym,
                    "entry_t": pos["entry_t"],
                    "exit_t": None,
                    "entry_px": pos["entry_px"],
                    "exit_px": None,
                    "reason": "FAIL_CLOSE_INVALID_DATA",
                    "pnl_yen": None,
                    "bps": None,
                    "hold_sec": None,
                    "mfe": None,
                    "mae": None,
                    "t_mfe": None,
                    "t_mae": None,
                    "mfe_before": None,
                    "mae_before": None,
                    "giveback": None,
                    "spread_bps": pos["spread_bps"],
                    "ratchets": pos["ratchets"],
                    "reentry": pos["reentry"],
                }
            )
            continue
        reason = pos.get("pending_reason") or pos.get("exit_reason") or "SESSION_FLAT"
        if pos.get("invalid_pending") and not pos.get("pending_reason"):
            reason = "FAIL_CLOSE_INVALID_DATA"
        _release(sym, reason, found[0], found[1])
    return {"trades": trades, "counts": counts}


def _book(
    t: np.ndarray,
    px: np.ndarray,
    ok: np.ndarray,
    ask_px: np.ndarray,
    pre: np.ndarray,
    signals: np.ndarray,
    board_t: np.ndarray,
    board_bid: np.ndarray,
    *,
    vol10: np.ndarray | None = None,
    break_flag: np.ndarray | None = None,
) -> dict[str, Any]:
    n = int(t.size)
    zeros = np.zeros(n, dtype=bool)
    zf = np.zeros(n, dtype=float)
    board = {
        "t": board_t,
        "bid": board_bid,
        "bid_qty": np.full(board_t.size, 100.0),
        "fresh_sec": np.zeros(board_t.size),
        "executable": np.ones(board_t.size, dtype=bool),
        "special": np.zeros(board_t.size, dtype=bool),
    }
    return {
        "t": t,
        "px": px,
        "ok": ok,
        "spread": np.full(n, 1.0),
        "ask_px": ask_px,
        "vol10": zeros if vol10 is None else vol10,
        "vol30": zeros.copy(),
        "buy": zeros.copy(),
        "classified": zf,
        "ask10": zf.copy(),
        "bid10": zf.copy(),
        "tick": zeros.copy(),
        "break": zeros if break_flag is None else break_flag,
        "pre_high": pre,
        "signals": signals,
        "bt": board_t,
        "bb": board_bid,
        "bn": bid_next(board),
    }


def self_check() -> dict[str, bool]:
    t = np.array([0.0, 10.0, 40.0])
    one = _book(
        t,
        np.array([101.0, 98.0, 97.0]),
        np.array([True, True, True]),
        np.array([100.0, 99.0, 98.0]),
        np.array([99.0, 99.0, 99.0]),
        np.array([0], dtype=np.int32),
        t,
        np.array([99.0, 97.0, 96.0]),
    )
    got = simulate_session({"A": one}, day="D", session="AM", sess_end=100.0, traded_today=set())
    trade = got["trades"][0]
    # Six names at the same time. Five enter. One is blocked by the cap.
    books = {}
    for name in ("A", "B", "C", "D", "E", "F"):
        books[name] = _book(
            np.array([0.0, 50.0]),
            np.array([101.0, 90.0]),
            np.array([True, True]),
            np.array([100.0, 100.0]),
            np.array([99.0, 99.0]),
            np.array([0], dtype=np.int32),
            np.array([0.0, 50.0]),
            np.array([99.0, 89.0]),
        )
    capped = simulate_session(books, day="D", session="AM", sess_end=100.0, traded_today=set())
    # Same symbol, second signal while the first position is still open.
    held = _book(
        np.array([0.0, 5.0, 10.0]),
        np.array([101.0, 102.0, 98.0]),
        np.array([True, True, True]),
        np.array([100.0, 100.0, 99.0]),
        np.array([99.0, 100.5, 99.0]),
        np.array([0, 1], dtype=np.int32),
        np.array([0.0, 5.0, 10.0]),
        np.array([99.0, 101.0, 97.0]),
    )
    blocked = simulate_session({"A": held}, day="D", session="AM", sess_end=100.0, traded_today=set())
    # Ratchet: the second event reconfirms a higher pre-break high, so 100.2 is still above support.
    vol = np.array([False, True, False])
    brk = np.array([False, True, False])
    ratchet_book = _book(
        np.array([0.0, 10.0, 20.0]),
        np.array([101.0, 103.0, 100.2]),
        np.array([True, True, True]),
        np.array([100.0, 102.0, 101.0]),
        np.array([99.0, 101.0, 101.0]),
        np.array([0], dtype=np.int32),
        np.array([0.0, 10.0, 20.0]),
        np.array([99.0, 102.0, 100.0]),
        vol10=vol,
        break_flag=brk,
    )
    ratchet_book["vol30"] = vol.copy()
    ratchet_book["buy"] = vol.copy()
    ratchet_book["tick"] = vol.copy()
    ratchet_book["classified"] = np.array([0.0, 5.0, 0.0])
    ratchet_book["ask10"] = np.array([0.0, 4.0, 1.0])
    ratchet_book["bid10"] = np.array([0.0, 1.0, 2.0])
    ratcheted = simulate_session({"A": ratchet_book}, day="D", session="AM", sess_end=100.0, traded_today=set())
    return {
        "support_exit_uses_bid": trade["reason"] == "BREAK_SUPPORT_FAILURE" and trade["exit_px"] == 97.0,
        "cap_blocks_the_sixth": capped["counts"]["cap"] == 1 and len(capped["trades"]) == 5,
        "same_symbol_blocks_second_signal": blocked["counts"]["same_symbol"] == 1 and len(blocked["trades"]) == 1,
        "ratchet_keeps_price_above_old_level": ratcheted["counts"]["ratchet"] == 1 and ratcheted["trades"][0]["reason"] == "BREAK_SUPPORT_FAILURE",
    }


def prepare_book(feat: dict[str, Any], arrays: dict[str, np.ndarray], board: dict[str, np.ndarray], signals: np.ndarray, ask_px: np.ndarray) -> dict[str, Any]:
    return {
        "t": arrays["t"],
        "px": arrays["px"],
        "ok": arrays["ok"],
        "spread": arrays["spread"],
        "ask_px": ask_px,
        "vol10": feat["vol_accel_10"],
        "vol30": feat["vol_accel_30"],
        "buy": feat["buy"],
        "classified": feat["classified10"],
        "ask10": feat["ask10"],
        "bid10": feat["bid10"],
        "tick": feat["tick_accel_10"],
        "break": feat["price_break"],
        "pre_high": feat["pre_high"],
        "signals": signals,
        "bt": board["t"],
        "bb": board["bid"],
        "bn": bid_next(board),
    }
