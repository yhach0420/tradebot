"""
Phase 42: kabu PUSH JSONL recorder — append-only per symbol per trade date.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def push_jsonl_filename(symbol: str) -> str:
    s = symbol.strip().upper()
    if not s.endswith(".T"):
        s = f"{s}.T"
    return f"{s}.jsonl"


@dataclass
class PushRecorder:
    """Append PUSH payloads to kabu_native/data/push_jsonl/YYYY-MM-DD/{symbol}.jsonl."""

    native_root: Path
    trade_date: str

    @property
    def day_dir(self) -> Path:
        return self.native_root / "data" / "push_jsonl" / self.trade_date

    def path_for_symbol(self, symbol: str) -> Path:
        return self.day_dir / push_jsonl_filename(symbol)

    def append(
        self,
        symbol: str,
        payload: Mapping[str, Any],
        *,
        recorded_at: datetime | None = None,
        source: str = "push",
    ) -> Path:
        path = self.path_for_symbol(symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = recorded_at or datetime.now(JST)
        line = {
            "recorded_at": rec.isoformat(),
            "source": source,
            "symbol": symbol.strip().upper(),
            "payload": dict(payload),
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        return path

    def line_count(self, symbol: str) -> int:
        path = self.path_for_symbol(symbol)
        if not path.is_file():
            return 0
        n = 0
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    n += 1
        return n

    def summarize(self, symbols: list[str]) -> dict[str, Any]:
        rows = []
        for sym in symbols:
            p = self.path_for_symbol(sym)
            rows.append(
                {
                    "symbol": sym,
                    "path": str(p) if p.is_file() else None,
                    "exists": p.is_file(),
                    "line_count": self.line_count(sym),
                    "bytes": p.stat().st_size if p.is_file() else 0,
                }
            )
        present = sum(1 for r in rows if r["exists"])
        return {
            "trade_date": self.trade_date,
            "expected_symbols": len(symbols),
            "jsonl_present": present,
            "symbols": rows,
        }
