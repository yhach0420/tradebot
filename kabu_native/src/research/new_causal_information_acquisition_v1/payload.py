"""Futures PUSH field extract. Reuse canonical Bid1=Buy1 / Ask1=Sell1. Do not fabricate."""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional

from small_paper.canonical_board import normalize_kabu_board

FUTURES_SCALAR_KEYS = (
    "CurrentPrice",
    "CurrentPriceTime",
    "CurrentPriceStatus",
    "CalcPrice",
    "TradingVolume",
    "TradingVolumeTime",
    "TradingValue",
    "OpeningPrice",
    "OpeningPriceTime",
    "HighPrice",
    "HighPriceTime",
    "LowPrice",
    "LowPriceTime",
    "PreviousClose",
    "PreviousCloseTime",
)

LEVEL_INNER_KEYS = ("Price", "Qty", "Time", "Sign")


def _copy_if_present(src: Mapping[str, Any], key: str) -> tuple[bool, Any]:
    if key not in src:
        return False, None
    return True, src.get(key)


def _finite_or_raw(v: Any) -> Any:
    if v is None or v == "":
        return v
    try:
        f = float(v)
        if not math.isfinite(f):
            return None
        return v
    except (TypeError, ValueError):
        return v


def copy_book_level(payload: Mapping[str, Any], prefix: str, i: int) -> Optional[dict[str, Any]]:
    key = f"{prefix}{i}"
    if key not in payload:
        return None
    lv = payload.get(key)
    if not isinstance(lv, Mapping):
        return {"_raw": lv}
    out: dict[str, Any] = {}
    for inner in LEVEL_INNER_KEYS:
        if inner in lv:
            out[inner] = lv.get(inner)
    return out


def canonical_bid1_ask1(payload: Mapping[str, Any], *, received_at: str = "") -> dict[str, Any]:
    board = normalize_kabu_board(payload, received_at=received_at)
    buy = payload.get("Buy1") if isinstance(payload.get("Buy1"), Mapping) else {}
    sell = payload.get("Sell1") if isinstance(payload.get("Sell1"), Mapping) else {}
    return {
        "Bid1": {
            "price": board.canonical_best_bid,
            "qty": board.canonical_bid_qty,
            "time": buy.get("Time") if isinstance(buy, Mapping) else None,
            "sign": buy.get("Sign") if isinstance(buy, Mapping) else None,
            "source": "Buy1",
        },
        "Ask1": {
            "price": board.canonical_best_ask,
            "qty": board.canonical_ask_qty,
            "time": sell.get("Time") if isinstance(sell, Mapping) else None,
            "sign": sell.get("Sign") if isinstance(sell, Mapping) else None,
            "source": "Sell1",
        },
        "kabu_BidPrice_raw_do_not_use_as_bid": board.kabu_bid_price_raw,
        "kabu_AskPrice_raw_do_not_use_as_ask": board.kabu_ask_price_raw,
    }


def extract_futures_event(
    original_payload: Mapping[str, Any],
    *,
    received_at: str,
    future_code: str,
    resolved_symbol: str,
    exchange: int,
) -> dict[str, Any]:
    if not str(received_at or "").strip():
        raise ValueError("received_at / ingress time is required; CurrentPriceTime is not the availability clock")
    op = dict(original_payload)
    out: dict[str, Any] = {
        "received_at": str(received_at),
        "FutureCode": str(future_code),
        "resolved_symbol": str(resolved_symbol),
        "Exchange": int(exchange),
        "availability_clock": "received_at",
    }
    for key in FUTURES_SCALAR_KEYS:
        present, val = _copy_if_present(op, key)
        if present:
            out[key] = _finite_or_raw(val)
    quotes = canonical_bid1_ask1(op, received_at=str(received_at))
    out["Bid1"] = quotes["Bid1"]
    out["Ask1"] = quotes["Ask1"]
    out["kabu_BidPrice_raw_do_not_use_as_bid"] = quotes["kabu_BidPrice_raw_do_not_use_as_bid"]
    out["kabu_AskPrice_raw_do_not_use_as_ask"] = quotes["kabu_AskPrice_raw_do_not_use_as_ask"]
    for i in range(1, 11):
        buy = copy_book_level(op, "Buy", i)
        sell = copy_book_level(op, "Sell", i)
        if buy is not None:
            out[f"Buy{i}"] = buy
        if sell is not None:
            out[f"Sell{i}"] = sell
    out["original_payload"] = op
    return out


def payload_mixes_futures_into_stock(stock_record: Mapping[str, Any]) -> bool:
    keys = set(stock_record.keys())
    return bool(keys & {"FutureCode", "resolved_symbol"})
