"""Stream push_jsonl quotes for risk metrics — no PnL / alpha fields."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import parse_ts

from . import FRESHNESS_MAX_SEC, LOT

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]

# Quantity unit contract ( empirically shares, not number-of-lots )
QTY_UNIT = "shares"


def _day_dash(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def raw_day_dir(day: str) -> Path:
    return NATIVE / "data" / "push_jsonl" / _day_dash(day)


def _norm_sym(sym: str) -> str:
    s = str(sym)
    return s[:-2] if s.endswith(".T") else s


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _level_qty(payload: dict[str, Any], side: str, n: int) -> Optional[float]:
    total = 0.0
    any_hit = False
    for i in range(1, n + 1):
        lvl = payload.get(f"{side}{i}") or {}
        q = _f(lvl.get("Qty"))
        if q is not None:
            any_hit = True
            total += q
    return total if any_hit else None


def _age_sec(ts_raw: Any, recv: datetime) -> Optional[float]:
    ts = parse_ts(ts_raw)
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=JST)
    return (recv - ts).total_seconds()


def iter_symbol_day_rows(day: str, symbol: str) -> Iterator[dict[str, Any]]:
    """Yield slim quote rows for one symbol-day. No PnL fields."""
    fp = raw_day_dir(day) / f"{symbol}.T.jsonl"
    if not fp.exists():
        fp2 = raw_day_dir(day) / f"{symbol}.jsonl"
        fp = fp2 if fp2.exists() else fp
    if not fp.exists():
        return
    try:
        import orjson as _json  # type: ignore

        def _loads(b: bytes):
            return _json.loads(b)
    except Exception:
        import json as _json

        def _loads(b: bytes):
            return _json.loads(b)

    with fp.open("rb") as f:
        for lineb in f:
            try:
                d = _loads(lineb)
            except Exception:
                continue
            recv = parse_ts(d.get("recorded_at"))
            if recv is None:
                continue
            if recv.tzinfo is None:
                recv = recv.replace(tzinfo=JST)
            p = d.get("payload") or {}
            buy1 = p.get("Buy1") or {}
            sell1 = p.get("Sell1") or {}
            bid = _f(buy1.get("Price"))
            ask = _f(sell1.get("Price"))
            if bid is None or ask is None or bid <= 0 or ask <= 0:
                continue
            bid_qty = _f(buy1.get("Qty"))
            ask_qty = _f(sell1.get("Qty"))
            price_age = _age_sec(p.get("CurrentPriceTime"), recv)
            board_age_bid = _age_sec(p.get("BidTime"), recv)
            board_age_ask = _age_sec(p.get("AskTime"), recv)
            board_ages = [a for a in (board_age_bid, board_age_ask) if a is not None]
            board_age = min(board_ages) if board_ages else None
            yield {
                "t": recv.timestamp(),
                "bid": bid,
                "ask": ask,
                "bid_qty": bid_qty,
                "ask_qty": ask_qty,
                "bid_depth_3": _level_qty(p, "Buy", 3),
                "ask_depth_3": _level_qty(p, "Sell", 3),
                "bid_depth_5": _level_qty(p, "Buy", 5),
                "ask_depth_5": _level_qty(p, "Sell", 5),
                "price_age_sec": price_age,
                "board_age_sec": board_age,
                "previous_close": _f(p.get("PreviousClose")),
                "previous_close_time": p.get("PreviousCloseTime"),
                "current_price_status": p.get("CurrentPriceStatus"),
            }


def reference_price_from_rows(day: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Prefer previous session official close; never same-day entry price."""
    for r in rows:
        pc = r.get("previous_close")
        if pc is not None and pc > 0:
            pct = r.get("previous_close_time")
            # asof: previous close time date must be before trading day
            asof_valid = True
            pts = parse_ts(pct) if pct else None
            if pts is not None:
                day_dt = datetime(int(day[:4]), int(day[4:6]), int(day[6:]), tzinfo=JST)
                asof_valid = pts.astimezone(JST).date() < day_dt.date()
            return {
                "day": day,
                "reference_price": float(pc),
                "reference_price_source": "previous_session_official_close",
                "reference_price_time": pct,
                "asof_valid": asof_valid,
            }
    # fallback: first valid mid of day is NOT allowed for static — mark not evaluable
    return {
        "day": day,
        "reference_price": None,
        "reference_price_source": "NOT_EVALUABLE_REFERENCE_PRICE",
        "reference_price_time": None,
        "asof_valid": False,
    }


def is_board_fresh(board_age: Optional[float], max_age: float = FRESHNESS_MAX_SEC) -> bool:
    return board_age is not None and board_age <= max_age


def is_price_fresh(price_age: Optional[float], max_age: float = FRESHNESS_MAX_SEC) -> bool:
    # When CurrentPriceTime missing, price freshness not evaluable → not fresh
    return price_age is not None and price_age <= max_age


def qty_unit_contract() -> dict[str, Any]:
    return {
        "unit": QTY_UNIT,
        "lot_size_shares": LOT,
        "note": "Buy1.Qty / Sell1.Qty treated as shares (not number of round lots)",
        "evidence": "observed top-of-book qty multiples of 100; runtime LOT_SIZE=100",
    }
