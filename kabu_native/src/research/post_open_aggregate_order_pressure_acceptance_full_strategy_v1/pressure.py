"""BUY_PRESSURE and Observed Trade Update. Ingress payload only."""
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
from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1 import PRESSURE_FIELDS

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


def buy_pressure(pay: dict[str, Any]) -> dict[str, Any]:
    ordered = [_f(pay.get(k)) for k in PRESSURE_FIELDS]
    if any(v is None for v in ordered):
        return {"ok": False, "BUY_PRESSURE": False, "established": False, "reason": "NAN"}
    if any(float(v) < 0 for v in ordered):
        return {"ok": False, "BUY_PRESSURE": False, "established": False, "reason": "NEGATIVE"}
    mo_buy, mo_sell, under, over = (float(v) for v in ordered)
    pos = mo_buy > mo_sell and under > over
    return {
        "ok": True,
        "BUY_PRESSURE": bool(pos),
        "established": True,
        "reason": "TRUE" if pos else "FALSE",
        "MarketOrderBuyQty": mo_buy,
        "MarketOrderSellQty": mo_sell,
        "UnderBuyQty": under,
        "OverSellQty": over,
    }


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
    }
