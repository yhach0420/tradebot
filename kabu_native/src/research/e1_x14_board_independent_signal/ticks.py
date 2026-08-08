"""Load slim price/volume ticks from push_jsonl (board fields ignored for features)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]


def _dash(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


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


def _ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None


def _loads(b: bytes) -> Any:
    try:
        import orjson
        return orjson.loads(b)
    except Exception:
        import json
        return json.loads(b)


def list_day_symbols(day: str) -> list[str]:
    d = NATIVE / "data" / "push_jsonl" / _dash(day)
    if not d.exists():
        return []
    out = []
    for fp in sorted(d.glob("*.jsonl")):
        name = fp.stem
        out.append(name[:-2] if name.endswith(".T") else name)
    return out


def load_symbol_ticks(day: str, symbol: str) -> list[dict[str, Any]]:
    """Chronological ticks: t_epoch, price, vol, value, vwap, price_src_age helpers."""
    fp = NATIVE / "data" / "push_jsonl" / _dash(day) / f"{symbol}.T.jsonl"
    if not fp.exists():
        fp = NATIVE / "data" / "push_jsonl" / _dash(day) / f"{symbol}.jsonl"
    if not fp.exists():
        return []
    rows: list[dict[str, Any]] = []
    prev_vol: Optional[float] = None
    for line in fp.open("rb"):
        if not line.strip():
            continue
        try:
            d = _loads(line)
        except Exception:
            continue
        recv = _ts(d.get("recorded_at"))
        if recv is None:
            continue
        p = d.get("payload") or {}
        px = _f(p.get("CurrentPrice"))
        if px is None or px <= 0:
            continue
        vol = _f(p.get("TradingVolume"))
        val = _f(p.get("TradingValue"))
        vwap = _f(p.get("VWAP"))
        px_t = _ts(p.get("CurrentPriceTime")) or recv
        vol_t = _ts(p.get("TradingVolumeTime")) or recv
        val_t = _ts(p.get("TradingValueTime")) or recv
        # VWAP often has no dedicated time — use recv
        vwap_t = recv
        vol_reset = False
        if vol is not None and prev_vol is not None and vol + 1e-9 < prev_vol:
            vol_reset = True
        if vol is not None:
            prev_vol = vol
        rows.append({
            "t": recv.timestamp(),
            "price": px,
            "vol": vol,
            "value": val,
            "vwap": vwap,
            "price_t": px_t.timestamp(),
            "vol_t": vol_t.timestamp(),
            "value_t": val_t.timestamp(),
            "vwap_t": vwap_t.timestamp(),
            "vol_reset": vol_reset,
        })
    rows.sort(key=lambda r: r["t"])
    return rows
