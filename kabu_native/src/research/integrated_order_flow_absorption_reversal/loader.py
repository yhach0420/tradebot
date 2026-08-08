"""PUSH loader — stride=1, canonical Buy1/Sell1 only."""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo

from research.integrated_order_flow_absorption_reversal.constants import CAPTURE_ROOT, LOT, STRIDE
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
    px: Optional[float]
    cum_vol: Optional[float]
    volume_delta: Optional[float]
    board: Any
    event_id: str
    session: str
    trade_side: str
    event_seq: int
    prev_ask_qty: Optional[float]
    prev_bid_qty: Optional[float]
    prev_bid_px: Optional[float]
    idx: int = 0


def _session(ts: datetime) -> str:
    h = ts.hour
    if 7 <= h < 12:
        return "AM"
    if 12 <= h < 16:
        return "PM"
    return "OTHER"


def classify_trade_side(px: Optional[float], board: Any, volume_delta: Optional[float]) -> str:
    if volume_delta is None or volume_delta <= 0 or px is None or px <= 0:
        return "NONE"
    ask, bid = board.canonical_best_ask, board.canonical_best_bid
    if ask is None or bid is None:
        return "NONE"
    if px >= ask:
        return "BUY"
    if px <= bid:
        return "SELL"
    return "UNKNOWN"


def discover_days() -> list[str]:
    if not CAPTURE_ROOT.exists():
        return []
    out: list[str] = []
    for p in CAPTURE_ROOT.iterdir():
        if not (p.is_dir() and p.name.isdigit() and len(p.name) == 8):
            continue
        if any(p.glob("push_part_*.jsonl")) or any(p.glob("session_*/push_part_*.jsonl")):
            out.append(p.name)
    return sorted(out)


def iter_day_ticks(day: str) -> Iterator[Tick]:
    day_dir = CAPTURE_ROOT / day
    if not day_dir.exists():
        return
    last_vol: dict[str, float] = {}
    last_sess: dict[str, str] = {}
    last_aq: dict[str, float] = {}
    last_bq: dict[str, float] = {}
    last_bp: dict[str, float] = {}
    # Chronological merge across flat + session_* layouts (never file-name order alone).
    try:
        from small_paper.replay_session_normalizer import normalize_day_capture

        events, _rep = normalize_day_capture(day_dir, day=day)
        records = []
        for e in events:
            # NormalizedEvent.payload is the capture envelope; board lives in original_payload.
            env = e.payload if isinstance(e.payload, dict) else {}
            op = env.get("original_payload") if isinstance(env.get("original_payload"), dict) else env
            records.append(
                {
                    "original_payload": op,
                    "symbol": e.symbol,
                    "received_at_jst": e.received_at or e.event_time,
                    "sequence": e.sequence,
                    "capture_session_id": e.session_id,
                }
            )
    except Exception:
        records = []
        for fp in sorted(day_dir.glob("push_part_*.jsonl")):
            with fp.open("r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        continue

    for i, rec in enumerate(records):
        if i % STRIDE != 0:
            continue
        if not isinstance(rec, dict):
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
        ts = parse_ts(rec.get("received_at_jst")) or parse_ts(op.get("CurrentPriceTime"))
        if ts is None:
            continue
        sym = str(rec.get("symbol") or op.get("Symbol") or "")
        if not sym.endswith(".T") and sym:
            sym = f"{sym}.T"
        sess = _session(ts)
        if sess == "OTHER":
            continue
        px = _f(op.get("CurrentPrice") if op.get("CurrentPrice") is not None else rec.get("current_price"))
        cum = _f(op.get("TradingVolume"))
        vdelta: Optional[float] = None
        if cum is not None:
            prev = last_vol.get(sym)
            prev_s = last_sess.get(sym)
            if prev_s is not None and prev_s != sess:
                vdelta = None
            elif prev is None or cum < prev:
                vdelta = None
            else:
                vdelta = cum - prev
            last_vol[sym] = cum
            last_sess[sym] = sess
        side = classify_trade_side(px, board, vdelta if (vdelta is not None and vdelta > 0) else None)
        aq = board.canonical_ask_qty
        bq = board.canonical_bid_qty
        bp = board.canonical_best_bid
        paq, pbq, pbp = last_aq.get(sym), last_bq.get(sym), last_bp.get(sym)
        if aq is not None:
            last_aq[sym] = aq
        if bq is not None:
            last_bq[sym] = bq
        if bp is not None:
            last_bp[sym] = bp
        try:
            seq = int(rec.get("sequence")) if rec.get("sequence") is not None else i
        except (TypeError, ValueError):
            seq = i
        yield Tick(
            day=day, symbol=sym, ts=ts, px=px, cum_vol=cum, volume_delta=vdelta,
            board=board, event_id=f"{day}:{sym}:{ts.isoformat()}:{seq}",
            session=sess, trade_side=side, event_seq=seq,
            prev_ask_qty=paq, prev_bid_qty=pbq, prev_bid_px=pbp,
        )


def load_streams(days: list[str]) -> dict[str, list[Tick]]:
    streams: dict[str, list[Tick]] = defaultdict(list)
    for day in days:
        by: dict[str, list[Tick]] = defaultdict(list)
        for t in iter_day_ticks(day):
            by[t.symbol].append(t)
        for sym, rows in by.items():
            rows.sort(key=lambda x: (x.ts, x.event_seq))
            for i, r in enumerate(rows):
                r.idx = i
            streams[f"{day}|{sym}"] = rows
    return dict(streams)


def exec_entry_ok(t: Tick) -> bool:
    aq = t.board.canonical_ask_qty
    return bool(
        t.board.canonical_quote_valid and not t.board.canonical_crossed
        and aq is not None and aq >= LOT
        and t.board.canonical_best_ask and t.board.canonical_best_ask > 0
    )


def first_valid_ask(ticks: list[Tick], i: int) -> Optional[tuple[int, float]]:
    for j in range(i, min(len(ticks), i + 50)):
        if exec_entry_ok(ticks[j]):
            return j, float(ticks[j].board.canonical_best_ask)
    return None


def bid_at(ticks: list[Tick], i: int, fallback: float) -> float:
    for j in range(i, max(-1, i - 3), -1):
        if j < 0:
            break
        b = ticks[j].board.canonical_best_bid
        if b and b > 0:
            return float(b)
    for j in range(i, min(len(ticks), i + 5)):
        b = ticks[j].board.canonical_best_bid
        if b and b > 0:
            return float(b)
    return fallback
