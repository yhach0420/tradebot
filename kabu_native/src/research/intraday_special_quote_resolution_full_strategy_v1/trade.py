"""Observed-trade-update and ISQ 1m trade bars. Ingress clock. No CurrentPrice carry."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.e1_x34a_execution_policy.executable_board import is_executable_continuous_board
from research.intraday_special_quote_resolution_full_strategy_v1.semantics import trusted_market_state

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


def minute_epoch(et: float) -> float:
    dt = datetime.fromtimestamp(float(et), JST)
    return float(dt.replace(second=0, microsecond=0).timestamp())


def opening_valid(pay: dict[str, Any], *, event_t: float) -> bool:
    gate = is_executable_continuous_board(pay, event_t=float(event_t))
    op = _f(gate.get("OpeningPrice"))
    if op is None or op <= 0:
        return False
    if str(gate.get("state") or "") == "NOT_OPENED":
        return False
    return True


def classify_payload(pay: dict[str, Any], *, event_t: float) -> dict[str, Any]:
    gate = is_executable_continuous_board(pay, event_t=float(event_t))
    return {
        "gate": gate,
        "trusted": trusted_market_state(gate),
        "opened": opening_valid(pay, event_t=event_t),
        "px": _f(pay.get("CurrentPrice") if pay.get("CurrentPrice") is not None else gate.get("CurrentPrice")),
        "vol": _f(pay.get("TradingVolume") if pay.get("TradingVolume") is not None else gate.get("TradingVolume")),
        "current_price_time": pay.get("CurrentPriceTime"),
        "trading_volume_time": pay.get("TradingVolumeTime"),
    }


def observed_trade_update(*, last_vol: Optional[float], vol: Optional[float], px: Optional[float]) -> bool:
    if vol is None or px is None:
        return False
    if last_vol is None:
        return False
    if not (px == px) or float(px) <= 0:
        return False
    if not (vol == vol):
        return False
    return float(vol) > float(last_vol)


def volume_regression(*, last_vol: Optional[float], vol: Optional[float]) -> bool:
    if vol is None or last_vol is None:
        return False
    return float(vol) < float(last_vol)


def new_trade_bar(*, minute: float, px: float, ingress_t: float) -> dict[str, Any]:
    p = float(px)
    return {
        "minute_epoch": float(minute),
        "open": p,
        "high": p,
        "low": p,
        "close": p,
        "TRADE_UPDATE_N": 1,
        "first_t": float(ingress_t),
        "last_t": float(ingress_t),
        "finalize_t": None,
    }


def add_trade_to_bar(bar: dict[str, Any], *, px: float, ingress_t: float) -> None:
    p = float(px)
    bar["high"] = max(float(bar["high"]), p)
    bar["low"] = min(float(bar["low"]), p)
    bar["close"] = p
    bar["TRADE_UPDATE_N"] = int(bar["TRADE_UPDATE_N"]) + 1
    bar["last_t"] = float(ingress_t)


def finish_bar(bar: dict[str, Any], *, finalize_t: float) -> dict[str, Any]:
    out = dict(bar)
    out["finalize_t"] = float(finalize_t)
    return out
