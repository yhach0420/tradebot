"""Load Capture ticks into X14 tick schema. Universe-filtered. No trade fields."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from small_paper.v1r_live_dual_lane import canonical_symbol_key

JST = ZoneInfo("Asia/Tokyo")


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if x != x:
            return None
        return x
    except (TypeError, ValueError):
        return None


def _ts(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        return x if x == x else None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST).timestamp()
    except Exception:
        return None


def _loads(b: bytes) -> Any:
    try:
        import orjson
        return orjson.loads(b)
    except Exception:
        import json
        return json.loads(b)


def _payload(rec: dict[str, Any]) -> dict[str, Any]:
    p = rec.get("original_payload") or rec.get("payload")
    return p if isinstance(p, dict) else {}


def _recv_epoch(rec: dict[str, Any], pay: dict[str, Any]) -> Optional[float]:
    for src in (rec, pay):
        for k in (
            "received_at_jst",
            "received_at",
            "event_time",
            "persisted_at",
            "received_at_utc",
        ):
            t = _ts(src.get(k))
            if t is not None:
                return t
    for k in ("CurrentPriceTime", "AskTime", "BidTime"):
        t = _ts(pay.get(k))
        if t is not None:
            return t
    return None


def load_capture_symbol_ticks(
    capture_dir: Path,
    universe: set[str],
) -> tuple[dict[str, list[dict[str, Any]]], list[float], Optional[float], int]:
    """Per-symbol X14 ticks + global event times (all market_push)."""
    ticks: dict[str, list[dict[str, Any]]] = {s: [] for s in universe}
    prev_vol: dict[str, Optional[float]] = {s: None for s in universe}
    global_t: list[float] = []
    last_t: Optional[float] = None
    n_rec = 0
    parts = sorted(p for p in capture_dir.glob("push_part_*.jsonl") if p.stat().st_size > 0)
    for part in parts:
        with part.open("rb") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    rec = _loads(line)
                except Exception:
                    continue
                if rec.get("kind") not in (None, "market_push"):
                    continue
                pay = _payload(rec)
                et = _recv_epoch(rec, pay)
                if et is None:
                    continue
                n_rec += 1
                global_t.append(et)
                last_t = et if last_t is None else max(last_t, et)
                sym = canonical_symbol_key(rec.get("symbol") or pay.get("Symbol") or rec.get("Symbol") or "")
                if not sym or sym not in universe:
                    continue
                px = _f(pay.get("CurrentPrice") if pay.get("CurrentPrice") is not None else rec.get("current_price"))
                if px is None or px <= 0:
                    continue
                vol = _f(pay.get("TradingVolume") if pay.get("TradingVolume") is not None else rec.get("trading_volume"))
                val = _f(pay.get("TradingValue") if pay.get("TradingValue") is not None else rec.get("trading_value"))
                vwap = _f(pay.get("VWAP") if pay.get("VWAP") is not None else rec.get("vwap"))
                px_t = _ts(pay.get("CurrentPriceTime") or rec.get("current_price_time")) or et
                vol_t = _ts(pay.get("TradingVolumeTime")) or et
                val_t = _ts(pay.get("TradingValueTime")) or et
                vol_reset = False
                pv = prev_vol[sym]
                if vol is not None and pv is not None and vol + 1e-9 < pv:
                    vol_reset = True
                if vol is not None:
                    prev_vol[sym] = vol
                ticks[sym].append({
                    "t": et,
                    "price": px,
                    "vol": vol,
                    "value": val,
                    "vwap": vwap,
                    "price_t": px_t,
                    "vol_t": vol_t,
                    "value_t": val_t,
                    "vwap_t": et,
                    "vol_reset": vol_reset,
                })
    for s, rows in ticks.items():
        rows.sort(key=lambda r: r["t"])
    global_t.sort()
    return ticks, global_t, last_t, n_rec
