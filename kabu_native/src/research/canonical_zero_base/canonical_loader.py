"""Load PUSH streams and attach canonical boards (no raw Bid/Ask strategy use)."""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo

from research.canonical_zero_base.constants import CAPTURE_ROOT, SAMPLE_STRIDE
from small_paper.canonical_board import normalize_kabu_board

JST = ZoneInfo("Asia/Tokyo")


def parse_ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    s = str(v).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class Tick:
    day: str
    symbol: str
    ts: datetime
    px: float
    vol: Optional[float]
    board: Any  # CanonicalBoard
    event_id: str
    idx: int = 0


def iter_day_ticks(day: str, *, stride: int = SAMPLE_STRIDE) -> Iterator[Tick]:
    day_dir = CAPTURE_ROOT / day
    if not day_dir.exists():
        return
    for fp in sorted(day_dir.glob("push_part_*.jsonl")):
        with fp.open("r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if i % max(1, stride) != 0:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                op = rec.get("original_payload")
                if not isinstance(op, dict):
                    continue
                if not isinstance(op.get("Buy1"), dict) or not isinstance(op.get("Sell1"), dict):
                    continue
                board = normalize_kabu_board(op)
                if board.canonical_best_bid is None or board.canonical_best_ask is None:
                    continue
                if board.canonical_best_ask < board.canonical_best_bid:
                    continue
                px = _f(op.get("CurrentPrice") if op.get("CurrentPrice") is not None else rec.get("current_price"))
                if px is None or px <= 0:
                    px = board.canonical_mid
                if px is None or px <= 0:
                    continue
                ts = parse_ts(rec.get("received_at_jst")) or parse_ts(op.get("CurrentPriceTime"))
                if ts is None:
                    continue
                sym = str(rec.get("symbol") or op.get("Symbol") or "")
                if not sym.endswith(".T") and sym:
                    sym = f"{sym}.T"
                yield Tick(
                    day=day,
                    symbol=sym,
                    ts=ts,
                    px=float(px),
                    vol=_f(op.get("TradingVolume")),
                    board=board,
                    event_id=f"{day}:{sym}:{ts.isoformat()}:{rec.get('sequence') or i}",
                )


def load_streams(days: list[str], *, stride: int = SAMPLE_STRIDE) -> dict[str, list[Tick]]:
    streams: dict[str, list[Tick]] = defaultdict(list)
    for day in days:
        by: dict[str, list[Tick]] = defaultdict(list)
        for t in iter_day_ticks(day, stride=stride):
            by[t.symbol].append(t)
        for sym, rows in by.items():
            rows.sort(key=lambda x: x.ts)
            for i, r in enumerate(rows):
                r.idx = i
            streams[f"{day}|{sym}"] = rows
    return streams
