"""Ranking jsonl writer. received_at is the availability clock. No 20260911 backfill."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.market_breadth_leadership_acquisition_v1 import FORBIDDEN_BACKFILL_DAYS, RANKING_TYPES
from research.market_breadth_leadership_acquisition_v1.isolation import RAW_ROOT

JST = ZoneInfo("Asia/Tokyo")


def refuse_historical_pseudosync(day: str, *, now: datetime | None = None) -> None:
    d = str(day or "").replace("-", "")
    if d in FORBIDDEN_BACKFILL_DAYS:
        raise ValueError(f"ranking tape was not captured on {d}; historical reconstruct forbidden")
    clock = now or datetime.now(JST)
    today = clock.strftime("%Y%m%d")
    if d != today:
        raise ValueError(f"historical ranking pseudo-sync forbidden: day={d} today={today}")


def day_dir(day: str) -> Path:
    return RAW_ROOT / str(day)


def ranking_path(day: str, ranking_type: int) -> Path:
    if int(ranking_type) not in RANKING_TYPES:
        raise ValueError(f"type {ranking_type} not in frozen set")
    return day_dir(day) / f"ranking_type{int(ranking_type)}.jsonl"


def append_ranking_record(day: str, rec: dict[str, Any]) -> Path:
    refuse_historical_pseudosync(day)
    typ = int(rec.get("requested_type") or 0)
    path = ranking_path(day, typ)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "received_at": rec.get("received_at"),
        "requested_type": typ,
        "ExchangeDivision": rec.get("ExchangeDivision"),
        "http_status": rec.get("http_status"),
        "raw": rec.get("raw"),
    }
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
    return path
