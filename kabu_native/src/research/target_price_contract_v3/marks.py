"""V3 price-mark contracts. Causal as-of. No fill qty. No OPENS_WITHIN_1S."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x28_executable_joint.board import BOARD_FRESHNESS_SEC, MIN_QTY
from research.e1_x34a_execution_policy.executable_board import (
    EXECUTABLE_PRICE_STATUS,
    PREOPEN_ITAYOSE_SIGNS,
    SPECIAL_QUOTE_SIGNS,
    STATE_NOT_OPENED,
    STATE_PREOPEN_ITAYOSE,
    STATE_SPECIAL_QUOTE,
    STATE_SPECIAL_QUOTE_FIELD,
)
from research.executable_target_v2_b_threshold.contract import last_executable_mid

ITAYOSE_STATES = frozenset({STATE_NOT_OPENED, STATE_PREOPEN_ITAYOSE})
SPECIAL_STATES = frozenset({STATE_SPECIAL_QUOTE, STATE_SPECIAL_QUOTE_FIELD})
BLOCK_MARK_STATES = ITAYOSE_STATES | SPECIAL_STATES


def last_idx(t: np.ndarray, t_at: float) -> int:
    if t is None or t.size == 0:
        return -1
    return int(np.searchsorted(t, float(t_at), side="right") - 1)


def _sign_class(ask_sign: str, bid_sign: str) -> Optional[str]:
    for s in (str(ask_sign or ""), str(bid_sign or "")):
        if s in PREOPEN_ITAYOSE_SIGNS:
            return "ITAYOSE"
        if s in SPECIAL_QUOTE_SIGNS:
            return "SPECIAL"
    return None


def _state_at(board: dict[str, np.ndarray], j: int) -> str:
    st = board.get("board_execution_state")
    if st is None or j >= int(getattr(st, "size", 0) or 0):
        return ""
    return str(st[j] or "")


def _blocked_itayose_special(board: dict[str, np.ndarray], j: int) -> Optional[str]:
    spec = board.get("special")
    if spec is not None and j < int(spec.size) and bool(spec[j]):
        return "SPECIAL"
    state = _state_at(board, j)
    if state in ITAYOSE_STATES:
        return "ITAYOSE"
    if state in SPECIAL_STATES:
        return "SPECIAL"
    ask_s = ""
    bid_s = ""
    if board.get("ask_sign") is not None and j < int(board["ask_sign"].size):
        ask_s = str(board["ask_sign"][j] or "")
    if board.get("bid_sign") is not None and j < int(board["bid_sign"].size):
        bid_s = str(board["bid_sign"][j] or "")
    return _sign_class(ask_s, bid_s)


def last_m1_quote(
    board: dict[str, np.ndarray],
    t_at: float,
    *,
    t_lo: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """Last continuous two-sided quote mid at/before t_at. No qty. No fill gate."""
    t = board.get("t")
    if t is None or t.size == 0:
        return None
    i = last_idx(t, t_at)
    if i < 0:
        return None
    for j in range(i, -1, -1):
        tj = float(t[j])
        if t_lo is not None and tj + 1e-12 < float(t_lo):
            break
        if tj > float(t_at) + 1e-12:
            continue
        blocked = _blocked_itayose_special(board, j)
        if blocked:
            continue
        bid = float(board["bid"][j]) if np.isfinite(board["bid"][j]) else float("nan")
        ask = float(board["ask"][j]) if np.isfinite(board["ask"][j]) else float("nan")
        if not (np.isfinite(bid) and np.isfinite(ask) and bid > 0 and ask > bid + 1e-12):
            continue
        mid = (bid + ask) / 2.0
        return {
            "price": float(mid),
            "source": "M1",
            "event_t": tj,
            "age_sec": float(t_at) - tj,
            "bid": float(bid),
            "ask": float(ask),
            "state": _state_at(board, j),
            "future_event": False,
        }
    return None


def last_m2_trade(
    board: dict[str, np.ndarray],
    t_at: float,
    *,
    t_lo: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """Last continuous CurrentPrice at/before t_at. Status 1 or 2. No qty."""
    t = board.get("t")
    px = board.get("last_px")
    if t is None or t.size == 0 or px is None or px.size == 0:
        return None
    i = last_idx(t, t_at)
    if i < 0:
        return None
    status = board.get("last_status")
    px_t = board.get("last_px_t")
    for j in range(i, -1, -1):
        tj = float(t[j])
        if t_lo is not None and tj + 1e-12 < float(t_lo):
            break
        if tj > float(t_at) + 1e-12:
            continue
        blocked = _blocked_itayose_special(board, j)
        if blocked:
            continue
        price = float(px[j]) if j < int(px.size) and np.isfinite(px[j]) else float("nan")
        if not (np.isfinite(price) and price > 0):
            continue
        stv = float(status[j]) if status is not None and j < int(status.size) and np.isfinite(status[j]) else float("nan")
        if not (np.isfinite(stv) and int(stv) in EXECUTABLE_PRICE_STATUS):
            continue
        print_t = float(px_t[j]) if px_t is not None and j < int(px_t.size) and np.isfinite(px_t[j]) else None
        if print_t is not None and print_t > float(t_at) + 1e-12:
            continue
        print_age = (float(t_at) - print_t) if print_t is not None else None
        return {
            "price": float(price),
            "source": "M2",
            "event_t": tj,
            "age_sec": float(t_at) - tj,
            "print_age_sec": print_age,
            "status": int(stv),
            "state": _state_at(board, j),
            "future_event": False,
        }
    return None


def last_m3(
    board: dict[str, np.ndarray],
    t_at: float,
    *,
    t_lo: Optional[float] = None,
    max_age: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """Fixed fallback: M1 within max_age else M2 within max_age. Same rule both sides."""
    m1 = last_m1_quote(board, t_at, t_lo=t_lo)
    if m1 is not None and (max_age is None or float(m1["age_sec"]) <= float(max_age) + 1e-12):
        return m1
    m2 = last_m2_trade(board, t_at, t_lo=t_lo)
    if m2 is not None and (max_age is None or float(m2["age_sec"]) <= float(max_age) + 1e-12):
        return m2
    return None


def last_m0_ref(board: dict[str, np.ndarray], t_at: float, t_lo: Optional[float] = None) -> Optional[dict[str, Any]]:
    """V2 last_executable_mid: qty/fill-style + 5s. Reference only."""
    hit = last_executable_mid(board, t_at, t_lo=t_lo)
    if not hit:
        return None
    return {
        "price": float(hit["mid"]),
        "source": "M0",
        "event_t": hit.get("event_t"),
        "age_sec": hit.get("lag_sec"),
        "state": hit.get("state"),
        "qty_min": float(MIN_QTY),
        "freshness": float(BOARD_FRESHNESS_SEC),
        "future_event": False,
    }


def pack_mark(hit: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not hit:
        return {"px": None, "age": None, "src": None, "print_age": None}
    return {
        "px": hit.get("price"),
        "age": hit.get("age_sec"),
        "src": hit.get("source"),
        "print_age": hit.get("print_age_sec"),
    }
