"""Stream market_capture PUSH into slim per-symbol series (deduped, causal)."""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def parse_ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None


def fnum(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if x != x:
            return None
        return x
    except (TypeError, ValueError):
        return None


@dataclass
class PushTick:
    day: str
    symbol: str
    event_time: datetime
    current_price: float
    previous_price: Optional[float]
    cumulative_volume: Optional[float]
    volume_delta: Optional[float]
    cumulative_trading_value: Optional[float]
    trading_value_delta: Optional[float]
    bid: Optional[float]
    ask: Optional[float]
    bid_qty: Optional[float]
    ask_qty: Optional[float]
    spread_bps: Optional[float]
    tick_direction: int  # +1 / -1 / 0
    trade_side_quality: str  # DIRECT|QUOTE_INFERRED|TICK_RULE_INFERRED|NOT_EVALUABLE
    buy_aggression: Optional[float]  # 1 buy, 0 sell, None unknown
    price_age_sec: Optional[float]
    board_age_sec: Optional[float]
    dq_volume_reset: bool = False
    sequence: int = 0


@dataclass
class LoadStats:
    n_raw: int = 0
    n_kept: int = 0
    n_dup: int = 0
    n_vol_reset: int = 0
    n_missing_vol: int = 0
    n_missing_px: int = 0
    symbols: set[str] = field(default_factory=set)


def _side_quality(
    px: float,
    prev_px: Optional[float],
    bid: Optional[float],
    ask: Optional[float],
) -> tuple[str, Optional[float], int]:
    """Returns (quality, buy_aggression 0/1, tick_direction)."""
    tick = 0
    if prev_px is not None:
        if px > prev_px:
            tick = 1
        elif px < prev_px:
            tick = -1
    # Quote inference
    if ask is not None and abs(px - ask) <= 1e-9:
        return "QUOTE_INFERRED", 1.0, tick if tick else 1
    if bid is not None and abs(px - bid) <= 1e-9:
        return "QUOTE_INFERRED", 0.0, tick if tick else -1
    # Tick rule
    if tick > 0:
        return "TICK_RULE_INFERRED", 1.0, tick
    if tick < 0:
        return "TICK_RULE_INFERRED", 0.0, tick
    return "NOT_EVALUABLE", None, 0


def iter_push_day(
    day_dir: Path,
    *,
    symbol_filter: Optional[set[str]] = None,
) -> Iterator[tuple[dict[str, Any], LoadStats]]:
    """Yield raw slim dicts; caller aggregates. Stats mutated in place via final yield? 

    Better: return (ticks_by_symbol, stats) from load_push_day.
    """
    raise NotImplementedError


def load_push_day(
    native: Path,
    day: str,
    *,
    symbol_filter: Optional[set[str]] = None,
) -> tuple[dict[str, list[PushTick]], LoadStats]:
    day_dir = native / "data" / "market_capture" / day
    stats = LoadStats()
    by: dict[str, list[PushTick]] = defaultdict(list)
    last_key: dict[str, tuple] = {}
    last_px: dict[str, float] = {}
    last_vol: dict[str, float] = {}
    last_tv: dict[str, float] = {}
    last_recv: dict[str, datetime] = {}

    parts = sorted(day_dir.glob("push_part_*.jsonl"))
    for part in parts:
        with part.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                stats.n_raw += 1
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                op = o.get("original_payload") if isinstance(o.get("original_payload"), dict) else {}
                sym_raw = str(o.get("symbol") or op.get("Symbol") or "")
                if not sym_raw:
                    continue
                # normalize to XXXX.T
                sym = sym_raw if sym_raw.endswith(".T") else f"{sym_raw}.T"
                code = sym[:-2] if sym.endswith(".T") else sym
                if symbol_filter is not None and code not in symbol_filter and sym not in symbol_filter:
                    continue
                stats.symbols.add(sym)

                px = fnum(o.get("current_price") if o.get("current_price") is not None else op.get("CurrentPrice"))
                vol = fnum(o.get("trading_volume") if o.get("trading_volume") is not None else op.get("TradingVolume"))
                tv = fnum(o.get("trading_value") if o.get("trading_value") is not None else op.get("TradingValue"))
                bid = fnum(o.get("bid") if o.get("bid") is not None else op.get("BidPrice"))
                ask = fnum(o.get("ask") if o.get("ask") is not None else op.get("AskPrice"))
                bq = fnum(op.get("BidQty"))
                aq = fnum(op.get("AskQty"))
                recv = parse_ts(o.get("received_at_jst")) or parse_ts(op.get("CurrentPriceTime"))
                cpt = parse_ts(op.get("CurrentPriceTime") or o.get("current_price_time"))
                if recv is None:
                    continue
                if px is None:
                    stats.n_missing_px += 1
                    continue
                if vol is None:
                    stats.n_missing_vol += 1

                key = (sym, recv.isoformat(), px, vol, bid, ask)
                if last_key.get(sym) == key:
                    stats.n_dup += 1
                    continue
                # also skip if nothing material changed vs last kept
                prev_snap = last_key.get(sym)
                if prev_snap is not None and prev_snap[2:] == (px, vol, bid, ask):
                    stats.n_dup += 1
                    continue
                last_key[sym] = key

                prev_px = last_px.get(sym)
                vdelta: Optional[float] = None
                dq_reset = False
                if vol is not None and sym in last_vol:
                    if vol < last_vol[sym] - 1e-9:
                        dq_reset = True
                        stats.n_vol_reset += 1
                        vdelta = None
                    else:
                        vdelta = vol - last_vol[sym]
                tvdelta: Optional[float] = None
                if tv is not None and sym in last_tv and not dq_reset:
                    if tv >= last_tv[sym] - 1e-9:
                        tvdelta = tv - last_tv[sym]
                quality, buy_agg, tick = _side_quality(px, prev_px, bid, ask)
                spread = None
                if bid is not None and ask is not None and px > 0:
                    spread = (ask - bid) / px * 10000.0
                price_age = (recv - cpt).total_seconds() if cpt else None

                tick_obj = PushTick(
                    day=day,
                    symbol=sym,
                    event_time=recv,
                    current_price=px,
                    previous_price=prev_px,
                    cumulative_volume=vol,
                    volume_delta=vdelta,
                    cumulative_trading_value=tv,
                    trading_value_delta=tvdelta,
                    bid=bid,
                    ask=ask,
                    bid_qty=bq,
                    ask_qty=aq,
                    spread_bps=spread,
                    tick_direction=tick,
                    trade_side_quality=quality,
                    buy_aggression=buy_agg,
                    price_age_sec=price_age,
                    board_age_sec=price_age,
                    dq_volume_reset=dq_reset,
                    sequence=int(o.get("sequence") or 0),
                )
                by[sym].append(tick_obj)
                stats.n_kept += 1
                last_px[sym] = px
                if vol is not None and not dq_reset:
                    last_vol[sym] = vol
                elif vol is not None and dq_reset:
                    last_vol[sym] = vol
                if tv is not None:
                    last_tv[sym] = tv
                last_recv[sym] = recv

    # sort each symbol
    for sym in by:
        by[sym].sort(key=lambda t: (t.event_time, t.sequence))
    return dict(by), stats


def watch_symbols_from_events(native: Path, day: str) -> set[str]:
    """Collect symbol codes seen in that day's small_paper events (Watch50)."""
    root = native / "results" / "small_paper" / day
    codes: set[str] = set()
    if not root.is_dir():
        return codes
    import csv

    for sess in root.iterdir():
        ev = sess / "small_paper_events.csv"
        if not ev.is_file() or ev.stat().st_size < 1000:
            continue
        with ev.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for i, row in enumerate(reader):
                s = str(row.get("symbol") or "")
                if not s:
                    continue
                code = s[:-2] if s.endswith(".T") else s
                codes.add(code)
                codes.add(s if s.endswith(".T") else f"{s}.T")
                if i > 50000:
                    break
    return codes
