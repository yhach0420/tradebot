"""
Phase 42: kabu_native intraday 1m CSV recorder (separate from legacy data/intraday_1m).

Builds replay-compatible OHLCV bars from PUSH JSONL or REST board snapshots.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
CSV_COLUMNS = ("timestamp_utc", "open", "high", "low", "close", "volume")
REQUIRED_OHLCV = frozenset({"open", "high", "low", "close", "volume"})


@dataclass
class MinuteBar:
    timestamp_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_row(self) -> dict[str, Any]:
        ts = self.timestamp_utc.astimezone(timezone.utc).isoformat()
        return {
            "timestamp_utc": ts,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass
class IntradayValidation:
    ok: bool
    row_count: int
    issues: list[str] = field(default_factory=list)
    duplicate_timestamps: int = 0
    out_of_order: int = 0


def _as_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_kabu_time(value: Any, *, fallback: datetime) -> datetime:
    if value is None or value == "":
        return fallback
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=JST)
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=JST)
    except ValueError:
        return fallback


def floor_minute_utc(dt: datetime) -> datetime:
    utc = dt.astimezone(timezone.utc)
    return utc.replace(second=0, microsecond=0)


def yahoo_csv_filename(symbol: str) -> str:
    s = symbol.strip().upper()
    if not s.endswith(".T"):
        s = f"{s}.T"
    return f"{s}.csv"


class PushMinuteBarBuilder:
    """Aggregate PUSH board updates into 1-minute OHLCV bars."""

    def __init__(self) -> None:
        self._bars: dict[datetime, dict[str, Any]] = {}
        self._last_cum_volume: Optional[float] = None

    def ingest_push_payload(
        self,
        payload: Mapping[str, Any],
        *,
        recorded_at: Optional[datetime] = None,
    ) -> None:
        price = _as_float(payload.get("CurrentPrice")) or _as_float(payload.get("CalcPrice"))
        if price is None:
            return
        now = recorded_at or datetime.now(JST)
        ts = parse_kabu_time(payload.get("CurrentPriceTime"), fallback=now)
        minute = floor_minute_utc(ts)
        cum_vol = _as_float(payload.get("TradingVolume"))
        vol_delta = 0.0
        if cum_vol is not None:
            if self._last_cum_volume is not None and cum_vol >= self._last_cum_volume:
                vol_delta = cum_vol - self._last_cum_volume
            self._last_cum_volume = cum_vol

        slot = self._bars.get(minute)
        if slot is None:
            self._bars[minute] = {
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": vol_delta,
            }
        else:
            slot["high"] = max(slot["high"], price)
            slot["low"] = min(slot["low"], price)
            slot["close"] = price
            slot["volume"] = float(slot["volume"]) + vol_delta

    def snapshot_minute_volumes(self) -> list[float]:
        """Ordered minute volumes (causal snapshot, does not clear state)."""
        return [max(0.0, float(b["volume"])) for _, b in sorted(self._bars.items())]

    def finalize(self) -> list[MinuteBar]:
        out: list[MinuteBar] = []
        for minute in sorted(self._bars.keys()):
            b = self._bars[minute]
            out.append(
                MinuteBar(
                    timestamp_utc=minute,
                    open=float(b["open"]),
                    high=float(b["high"]),
                    low=float(b["low"]),
                    close=float(b["close"]),
                    volume=max(0.0, float(b["volume"])),
                )
            )
        return out


def build_minute_bars_from_push_jsonl(path: Path) -> list[MinuteBar]:
    builder = PushMinuteBarBuilder()
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = row.get("payload") if isinstance(row, dict) else None
            if not isinstance(payload, dict):
                payload = row if isinstance(row, dict) else {}
            rec_at = None
            if isinstance(row, dict) and row.get("recorded_at"):
                rec_at = parse_kabu_time(row["recorded_at"], fallback=datetime.now(JST))
            builder.ingest_push_payload(payload, recorded_at=rec_at)
    return builder.finalize()


def build_snapshot_bar_from_board(board: Mapping[str, Any]) -> Optional[MinuteBar]:
    """Single-bar snapshot when only REST board is available (not a full session)."""
    price = _as_float(board.get("CurrentPrice")) or _as_float(board.get("CalcPrice"))
    if price is None:
        return None
    ts = parse_kabu_time(board.get("CurrentPriceTime"), fallback=datetime.now(JST))
    minute = floor_minute_utc(ts)
    vol = _as_float(board.get("TradingVolume")) or 0.0
    o = _as_float(board.get("OpeningPrice")) or price
    h = _as_float(board.get("HighPrice")) or price
    l = _as_float(board.get("LowPrice")) or price
    return MinuteBar(
        timestamp_utc=minute,
        open=o,
        high=max(h, price),
        low=min(l, price),
        close=price,
        volume=vol,
    )


def validate_minute_bars(bars: Sequence[MinuteBar]) -> IntradayValidation:
    issues: list[str] = []
    if not bars:
        return IntradayValidation(ok=False, row_count=0, issues=["no_bars"])

    dup = 0
    ooo = 0
    prev: Optional[datetime] = None
    seen: set[datetime] = set()
    for b in bars:
        if b.timestamp_utc in seen:
            dup += 1
        seen.add(b.timestamp_utc)
        if prev and b.timestamp_utc < prev:
            ooo += 1
        prev = b.timestamp_utc
        if b.high < b.low:
            issues.append(f"high_lt_low:{b.timestamp_utc.isoformat()}")
        if b.open < 0 or b.close < 0:
            issues.append("negative_price")

    if dup:
        issues.append(f"duplicate_minutes:{dup}")
    if ooo:
        issues.append(f"out_of_order:{ooo}")

    return IntradayValidation(
        ok=len(issues) == 0,
        row_count=len(bars),
        issues=issues,
        duplicate_timestamps=dup,
        out_of_order=ooo,
    )


def validate_intraday_csv(path: Path) -> IntradayValidation:
    if not path.is_file():
        return IntradayValidation(ok=False, row_count=0, issues=["missing_file"])
    issues: list[str] = []
    rows: list[MinuteBar] = []
    try:
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return IntradayValidation(ok=False, row_count=0, issues=["empty_csv"])
            cols = {c.lower() for c in reader.fieldnames}
            if not REQUIRED_OHLCV.issubset(cols):
                return IntradayValidation(
                    ok=False,
                    row_count=0,
                    issues=[f"missing_columns:{sorted(REQUIRED_OHLCV - cols)}"],
                )
            ts_col = "timestamp_utc" if "timestamp_utc" in cols else "timestamp"
            for row in reader:
                ts_raw = row.get(ts_col) or row.get("timestamp_utc") or row.get("timestamp")
                if not ts_raw:
                    continue
                ts = parse_kabu_time(ts_raw, fallback=datetime.now(timezone.utc))
                rows.append(
                    MinuteBar(
                        timestamp_utc=ts,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row.get("volume") or 0),
                    )
                )
    except OSError as e:
        return IntradayValidation(ok=False, row_count=0, issues=[f"read_error:{e}"])

    if not rows:
        return IntradayValidation(ok=False, row_count=0, issues=["no_data_rows"])
    return validate_minute_bars(rows)


@dataclass
class IntradayRecorder:
    """Write validated 1m CSV under kabu_native/data/intraday_1m/."""

    native_root: Path

    @property
    def intraday_root(self) -> Path:
        return self.native_root / "data" / "intraday_1m"

    def csv_path(self, trade_date: str, symbol: str) -> Path:
        return self.intraday_root / trade_date / yahoo_csv_filename(symbol)

    def summarize_day(self, trade_date: str, symbols: Sequence[str]) -> dict[str, Any]:
        return summarize_day(self, trade_date, symbols)

    def write_bars(
        self,
        trade_date: str,
        symbol: str,
        bars: Sequence[MinuteBar],
        *,
        merge_existing: bool = True,
    ) -> tuple[Path, IntradayValidation]:
        path = self.csv_path(trade_date, symbol)
        path.parent.mkdir(parents=True, exist_ok=True)

        combined: dict[datetime, MinuteBar] = {}
        if merge_existing and path.is_file():
            for b in build_minute_bars_from_existing_csv(path):
                combined[b.timestamp_utc] = b
        for b in bars:
            combined[b.timestamp_utc] = b

        ordered = [combined[k] for k in sorted(combined.keys())]
        validation = validate_minute_bars(ordered)
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS))
            w.writeheader()
            for b in ordered:
                w.writerow(b.to_row())
        post = validate_intraday_csv(path)
        return path, post if post.row_count else validation


def build_minute_bars_from_existing_csv(path: Path) -> list[MinuteBar]:
    v = validate_intraday_csv(path)
    if not v.ok and v.row_count == 0:
        return []
    bars: list[MinuteBar] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        ts_col = "timestamp_utc"
        for row in reader:
            ts = parse_kabu_time(row.get(ts_col), fallback=datetime.now(timezone.utc))
            bars.append(
                MinuteBar(
                    timestamp_utc=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0),
                )
            )
    return bars


def build_from_push_day(
    recorder: IntradayRecorder,
    *,
    trade_date: str,
    symbol: str,
    push_jsonl_path: Path,
) -> tuple[Path | None, IntradayValidation]:
    bars = build_minute_bars_from_push_jsonl(push_jsonl_path)
    if not bars:
        return None, IntradayValidation(ok=False, row_count=0, issues=["no_bars_from_push"])
    return recorder.write_bars(trade_date, symbol, bars)


def summarize_day(
    recorder: IntradayRecorder,
    trade_date: str,
    symbols: Sequence[str],
) -> dict[str, Any]:
    day_dir = recorder.intraday_root / trade_date
    rows: list[dict[str, Any]] = []
    for sym in symbols:
        p = recorder.csv_path(trade_date, sym)
        if p.is_file():
            v = validate_intraday_csv(p)
            rows.append(
                {
                    "symbol": sym,
                    "path": str(p),
                    "bytes": p.stat().st_size,
                    "row_count": v.row_count,
                    "valid": v.ok,
                    "issues": v.issues,
                }
            )
        else:
            rows.append({"symbol": sym, "path": None, "valid": False, "issues": ["missing_csv"]})
    present = sum(1 for r in rows if r.get("path"))
    valid = sum(1 for r in rows if r.get("valid"))
    return {
        "trade_date": trade_date,
        "expected_symbols": len(symbols),
        "csv_present": present,
        "csv_valid": valid,
        "symbols": rows,
    }
