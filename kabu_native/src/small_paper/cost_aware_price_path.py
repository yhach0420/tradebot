"""Price-path helpers for Cost-Aware Shadow (no future leak)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def parse_ts(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=JST)
    try:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=JST)
    except Exception:
        return None


def last_valid_price_at_or_before(
    path: Sequence[tuple[datetime, float]],
    *,
    asof: datetime,
    not_before: Optional[datetime] = None,
) -> Optional[tuple[datetime, float, float]]:
    """Return (price_ts, price, age_sec) with price_ts <= asof (no future leak).

    age_sec = (asof - price_ts).total_seconds().
    """
    best: Optional[tuple[datetime, float]] = None
    for ts, px in path:
        if px is None or float(px) <= 0:
            continue
        if ts > asof:
            break
        if not_before is not None and ts < not_before:
            continue
        best = (ts, float(px))
    if best is None:
        return None
    age = (asof - best[0]).total_seconds()
    return best[0], best[1], age


def build_symbol_price_paths(
    events: Sequence[Mapping[str, Any]],
) -> dict[str, list[tuple[datetime, float]]]:
    """Build ascending (ts, price) paths from candidate/accepted/exit events."""
    out: dict[str, list[tuple[datetime, float]]] = {}
    for e in events:
        et = str(e.get("event_type") or e.get("event") or "")
        if et not in ("candidate", "accepted", "observer_exit", "observer_hold", "observer_take"):
            continue
        sym = str(e.get("symbol") or "")
        if not sym:
            continue
        px = e.get("current_price")
        if px in (None, ""):
            px = e.get("exit_price") if et == "observer_exit" else e.get("entry_price")
        try:
            price = float(px) if px not in (None, "") else 0.0
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0:
            continue
        ts = parse_ts(
            e.get("event_time")
            or e.get("exit_time")
            or e.get("entry_time")
            or e.get("timestamp")
            or e.get("eval_end_ts")
        )
        if ts is None:
            continue
        out.setdefault(sym, []).append((ts, price))
    for sym, rows in out.items():
        rows.sort(key=lambda x: x[0])
        # dedupe identical consecutive stamps keeping last price
        dedup: list[tuple[datetime, float]] = []
        for ts, px in rows:
            if dedup and dedup[-1][0] == ts:
                dedup[-1] = (ts, px)
            else:
                dedup.append((ts, px))
        out[sym] = dedup
    return out
