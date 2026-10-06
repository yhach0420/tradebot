"""CalcPrice validity, Observed Trade Update, ingress. No CurrentPriceTime."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.e1_x34a_execution_policy.executable_board import (
    STATE_CONTINUOUS,
    STATE_PREOPEN_ITAYOSE,
    STATE_SPECIAL_QUOTE,
    STATE_SPECIAL_QUOTE_FIELD,
    is_executable_continuous_board,
)

JST = ZoneInfo("Asia/Tokyo")
INGRESS_KEYS = ("received_at", "received_at_jst", "persisted_at", "received_at_utc")


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _parse_iso(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        if isinstance(v, (int, float)):
            return float(v)
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST).timestamp()
    except Exception:
        return None


def ingress_epoch(rec: dict[str, Any] | None, pay: dict[str, Any] | None) -> Optional[float]:
    for obj in (rec, pay):
        if not isinstance(obj, dict):
            continue
        for k in INGRESS_KEYS:
            t = _parse_iso(obj.get(k))
            if t is not None:
                return float(t)
    return None


def calc_valid(pay: dict[str, Any]) -> Optional[float]:
    v = _f(pay.get("CalcPrice"))
    if v is None or float(v) <= 0:
        return None
    return float(v)


def lvl_price(pay: dict[str, Any], *, side: str) -> Optional[float]:
    if side == "bid":
        b1 = pay.get("Buy1") if isinstance(pay.get("Buy1"), dict) else {}
        return _f(b1.get("Price")) if b1 else _f(pay.get("BidPrice"))
    s1 = pay.get("Sell1") if isinstance(pay.get("Sell1"), dict) else {}
    return _f(s1.get("Price")) if s1 else _f(pay.get("AskPrice"))


def observed_trade_update(*, last_vol: Optional[float], vol: Optional[float], px: Optional[float]) -> bool:
    if vol is None or px is None:
        return False
    baseline = 0.0 if last_vol is None else float(last_vol)
    return float(vol) > float(baseline) and float(px) > 0


def trusted_state(pay: dict[str, Any], *, event_t: float) -> dict[str, Any]:
    gate = is_executable_continuous_board(pay, event_t=float(event_t))
    st = str(gate.get("state") or "")
    special = st in (STATE_SPECIAL_QUOTE, STATE_SPECIAL_QUOTE_FIELD)
    preopen = st == STATE_PREOPEN_ITAYOSE
    continuous = st == STATE_CONTINUOUS
    op = _f(pay.get("OpeningPrice") if pay.get("OpeningPrice") is not None else gate.get("OpeningPrice"))
    opened = op is not None and float(op) > 0 and st != "NOT_OPENED"
    return {
        "state": st,
        "continuous": bool(continuous),
        "special": bool(special),
        "preopen": bool(preopen),
        "opened": bool(opened),
        "px": _f(pay.get("CurrentPrice")),
        "vol": _f(pay.get("TradingVolume")),
        "calc": calc_valid(pay),
        "bid": lvl_price(pay, side="bid"),
        "ask": lvl_price(pay, side="ask"),
    }
