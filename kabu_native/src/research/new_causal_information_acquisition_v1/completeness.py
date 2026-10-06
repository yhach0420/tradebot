"""FULL-day gates + gap audit. No strategy. Ingress clock only."""
from __future__ import annotations

import json
import math
from datetime import datetime, time
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from research.new_causal_information_acquisition_v1.writer import day_layout

JST = ZoneInfo("Asia/Tokyo")
GAP_AUDIT_SEC = 60.0
PREOPEN = (time(8, 45), time(9, 0))
AM = (time(9, 0), time(11, 30))


def parse_received_at(value: Any) -> Optional[datetime]:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def _in_span(dt: datetime, span: tuple[time, time]) -> bool:
    t = dt.astimezone(JST).time()
    start, end = span
    return start <= t <= end


def _finite(v: Any) -> bool:
    try:
        if v is None or v == "":
            return False
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def _iter_parts(dir_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not dir_path.is_dir():
        return rows
    files = sorted(dir_path.glob("push_part_*.jsonl")) + sorted(dir_path.glob("*.jsonl"))
    seen: set[Path] = set()
    for p in files:
        if p in seen:
            continue
        seen.add(p)
        rows.extend(_iter_jsonl(p))
    return rows


def _event_clock(rec: Mapping[str, Any]) -> Optional[datetime]:
    return parse_received_at(rec.get("received_at") or rec.get("received_at_jst"))


def _gaps(times: list[datetime], *, span: tuple[time, time]) -> list[dict[str, Any]]:
    scoped = [t for t in times if _in_span(t, span)]
    scoped.sort()
    gaps = []
    for a, b in zip(scoped, scoped[1:]):
        sec = (b - a).total_seconds()
        if sec > GAP_AUDIT_SEC:
            gaps.append({"from": a.isoformat(), "to": b.isoformat(), "gap_sec": sec, "class": "UNEXPLAINED_OR_NEEDS_AUDIT"})
    return gaps


def _changes(rows: list[Mapping[str, Any]], *, span: tuple[time, time], field: str) -> int:
    prev = None
    n = 0
    for rec in rows:
        dt = _event_clock(rec)
        if dt is None or not _in_span(dt, span):
            continue
        payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), Mapping) else rec
        val = payload.get(field) if isinstance(payload, Mapping) else rec.get(field)
        if val is None or val == "":
            val = rec.get(field)
        if val is None or val == "":
            continue
        if prev is None:
            prev = val
            continue
        if val != prev:
            n += 1
            prev = val
    return n


def summarize_futures_file(path: Path) -> dict[str, Any]:
    rows = _iter_jsonl(path)
    if not rows:
        parent = path.parent / path.stem
        rows = _iter_parts(parent)
    times: list[datetime] = []
    prices: set[str] = set()
    trade_n = 0
    quote_n = 0
    finite_price_n = 0
    for rec in rows:
        dt = _event_clock(rec)
        if dt:
            times.append(dt)
        payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), Mapping) else rec
        px = rec.get("CurrentPrice")
        if px is None and isinstance(payload, Mapping):
            px = payload.get("CurrentPrice")
        if _finite(px):
            finite_price_n += 1
            prices.add(str(px))
        bid = rec.get("Bid1") if isinstance(rec.get("Bid1"), Mapping) else None
        ask = rec.get("Ask1") if isinstance(rec.get("Ask1"), Mapping) else None
        if bid or ask:
            quote_n += 1
        vol = rec.get("TradingVolume")
        if vol is None and isinstance(payload, Mapping):
            vol = payload.get("TradingVolume")
        if _finite(vol):
            trade_n += 1
    times.sort()
    return {
        "path": str(path),
        "event_n": len(rows),
        "trade_update_n": trade_n,
        "quote_update_n": quote_n,
        "finite_price_n": finite_price_n,
        "unique_price_n": len(prices),
        "change_0845_0900": _changes(rows, span=PREOPEN, field="CurrentPrice"),
        "change_0900_1130": _changes(rows, span=AM, field="CurrentPrice"),
        "continuity_0845_0900": bool([t for t in times if _in_span(t, PREOPEN)]),
        "continuity_0900_1130": bool([t for t in times if _in_span(t, AM)]),
        "gaps_gt_60s": _gaps(times, span=PREOPEN) + _gaps(times, span=AM),
        "first_received_at": times[0].isoformat() if times else None,
        "last_received_at": times[-1].isoformat() if times else None,
        "static_or_frozen": len(rows) > 0 and len(prices) <= 1 and _changes(rows, span=AM, field="CurrentPrice") == 0,
    }


def stock_symbol_n(stock_dir: Path) -> int:
    rows = _iter_parts(stock_dir)
    syms: set[str] = set()
    for rec in rows:
        payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), Mapping) else rec
        s = str((payload or {}).get("Symbol") or rec.get("symbol") or "").split("@", 1)[0]
        if s:
            syms.add(s)
    return len(syms)


def evaluate_day(day: str, *, native_root: Optional[Path] = None) -> dict[str, Any]:
    paths = day_layout(str(day), native_root=native_root)
    nk = summarize_futures_file(paths["nk225mini_jsonl"])
    tx = summarize_futures_file(paths["topix_jsonl"])
    stock_n = stock_symbol_n(paths["stock"])
    gaps = list(nk.get("gaps_gt_60s") or []) + list(tx.get("gaps_gt_60s") or [])
    full = (
        stock_n == 48
        and int(nk.get("event_n") or 0) > 0
        and int(tx.get("event_n") or 0) > 0
        and bool(nk.get("continuity_0845_0900"))
        and bool(tx.get("continuity_0845_0900"))
        and bool(nk.get("continuity_0900_1130"))
        and bool(tx.get("continuity_0900_1130"))
        and not bool(nk.get("static_or_frozen"))
        and not bool(tx.get("static_or_frozen"))
    )
    return {
        "day": str(day),
        "stock_symbol_n": stock_n,
        "nk225mini": nk,
        "topix": tx,
        "future_gaps_gt_60s": gaps,
        "FULL": bool(full),
        "PARTIAL": (not full) and (stock_n > 0 or int(nk.get("event_n") or 0) > 0 or int(tx.get("event_n") or 0) > 0),
    }


def list_full_days(*, native_root: Optional[Path] = None, context_root: Optional[Path] = None) -> list[str]:
    from research.new_causal_information_acquisition_v1.isolation import CONTEXT_ROOT, NATIVE

    root = Path(context_root) if context_root else (Path(native_root) / "data" / "market_context_capture" if native_root else CONTEXT_ROOT)
    if not root.is_dir():
        return []
    days = []
    for p in sorted(root.iterdir()):
        if p.is_dir() and len(p.name) == 8 and p.name.isdigit():
            ev = evaluate_day(p.name, native_root=native_root or NATIVE)
            if ev.get("FULL"):
                days.append(p.name)
    return days
