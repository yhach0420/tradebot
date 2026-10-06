"""Ask/Bid gates. Freshness uses AskTime/BidTime then ingress. Never CurrentPriceTime."""
from __future__ import annotations

from typing import Any, Optional

from research.e1_x28_executable_joint import MIN_QTY
from research.new_entry_breakout_continuation_v1.harvest import (
    CONTINUOUS_STATES,
    ask_entry_ok,
    board_row,
)
from research.simple_full_strategy_discovery_v1 import BOARD_FRESHNESS_SEC


def payload_of(rec: dict[str, Any]) -> dict[str, Any]:
    pay = rec.get("payload")
    if isinstance(pay, dict) and pay:
        return pay
    orig = rec.get("original_payload")
    return orig if isinstance(orig, dict) else {}


def row_as_snap(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "t": row.get("t"),
        "bid": row.get("bid"),
        "ask": row.get("ask"),
        "bid_qty": row.get("bid_qty"),
        "ask_qty": row.get("ask_qty"),
        "special": bool(row.get("special")),
        "ask_fresh_sec": row.get("ask_fresh_sec"),
        "bid_fresh_sec": row.get("bid_fresh_sec"),
        "executable": bool(row.get("executable")),
        "state": str(row.get("state") or ""),
        "continuous": bool(row.get("continuous")),
        "fresh_source": str(row.get("fresh_source") or ""),
    }


def quote_snap(rec: dict[str, Any], pay: dict[str, Any], clock_t: float) -> dict[str, Any]:
    row = board_row(rec, pay, float(clock_t))
    return row_as_snap(row)


def ask_ok(snap: dict[str, Any]) -> bool:
    ok, _reason = ask_entry_ok(snap, require_qty=True)
    return bool(ok)


def _fresh_ok(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x and x <= float(BOARD_FRESHNESS_SEC) + 1e-12


def bid_ok(snap: dict[str, Any]) -> bool:
    if not snap.get("ok"):
        return False
    if not bool(snap.get("executable")) or not bool(snap.get("continuous")):
        return False
    if str(snap.get("state") or "") not in CONTINUOUS_STATES:
        return False
    if bool(snap.get("special")):
        return False
    if not _fresh_ok(snap.get("bid_fresh_sec")):
        return False
    try:
        bid = float(snap.get("bid"))
        qty = float(snap.get("bid_qty"))
    except (TypeError, ValueError):
        return False
    if not (bid == bid) or bid <= 0:
        return False
    if not (qty == qty) or qty < float(MIN_QTY) - 1e-12:
        return False
    return True


def ask_px(snap: dict[str, Any]) -> Optional[float]:
    try:
        v = float(snap.get("ask"))
    except (TypeError, ValueError):
        return None
    return v if v == v and v > 0 else None


def bid_px(snap: dict[str, Any]) -> Optional[float]:
    try:
        v = float(snap.get("bid"))
    except (TypeError, ValueError):
        return None
    return v if v == v and v > 0 else None
