"""PUSH loader — volume delta + trade side + ask depletion class (no VCIE thresholds)."""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo

from research.canonical_fcr_exact_method.constants import CAPTURE_ROOT, LOT, SAMPLE_STRIDE
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
    trade_side_confidence: float
    ask_depletion_class: str  # EXECUTED_DEPLETION|CANCELLATION_OR_UNKNOWN|NONE
    prev_ask_qty: Optional[float]
    idx: int = 0
    volume_reset: bool = False


def _session(ts: datetime) -> str:
    h = ts.hour
    if 7 <= h < 12:
        return "AM"
    if 12 <= h < 16:
        return "PM"
    return "OTHER"


def classify_trade_side(px: Optional[float], board: Any, volume_delta: Optional[float]) -> tuple[str, float]:
    if volume_delta is None or volume_delta <= 0 or px is None or px <= 0:
        return "NONE", 0.0
    ask, bid = board.canonical_best_ask, board.canonical_best_bid
    if ask is None or bid is None:
        return "NONE", 0.0
    if px >= ask:
        return "BUY", 0.85
    if px <= bid:
        return "SELL", 0.85
    return "UNKNOWN", 0.35


def classify_ask_depletion(prev_aq: Optional[float], cur_aq: Optional[float], trade_side: str, volume_delta: Optional[float]) -> str:
    if prev_aq is None or cur_aq is None:
        return "NONE"
    if cur_aq >= prev_aq:
        return "NONE"
    if volume_delta is not None and volume_delta > 0 and trade_side == "BUY":
        return "EXECUTED_DEPLETION"
    return "CANCELLATION_OR_UNKNOWN"


def iter_day_ticks(day: str, *, stride: int = SAMPLE_STRIDE) -> Iterator[Tick]:
    day_dir = CAPTURE_ROOT / day
    if not day_dir.exists():
        return
    last_vol: dict[str, float] = {}
    last_sess: dict[str, str] = {}
    last_aq: dict[str, float] = {}
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
                ts = parse_ts(rec.get("received_at_jst")) or parse_ts(op.get("CurrentPriceTime"))
                if ts is None:
                    continue
                sym = str(rec.get("symbol") or op.get("Symbol") or "")
                if not sym.endswith(".T") and sym:
                    sym = f"{sym}.T"
                sess = _session(ts)
                px = _f(op.get("CurrentPrice") if op.get("CurrentPrice") is not None else rec.get("current_price"))
                cum = _f(op.get("TradingVolume"))
                vdelta: Optional[float] = None
                reset = False
                if cum is not None:
                    prev = last_vol.get(sym)
                    prev_s = last_sess.get(sym)
                    if prev_s is not None and prev_s != sess:
                        reset, vdelta = True, None
                    elif prev is None:
                        # missing prior cum — never fake 0 volume_delta
                        vdelta = None
                    elif cum < prev:
                        reset, vdelta = True, None  # reset / cross-session: not zero
                    else:
                        vdelta = cum - prev
                    last_vol[sym] = cum
                    last_sess[sym] = sess
                side, conf = classify_trade_side(px, board, vdelta if (vdelta is not None and vdelta > 0) else None)
                aq = board.canonical_ask_qty
                paq = last_aq.get(sym)
                dep = classify_ask_depletion(paq, aq, side, vdelta)
                if aq is not None:
                    last_aq[sym] = aq
                yield Tick(
                    day=day, symbol=sym, ts=ts, px=px, cum_vol=cum, volume_delta=vdelta,
                    board=board, event_id=f"{day}:{sym}:{ts.isoformat()}:{rec.get('sequence') or i}",
                    session=sess, trade_side=side, trade_side_confidence=conf,
                    ask_depletion_class=dep, prev_ask_qty=paq, volume_reset=reset,
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
    return dict(streams)


def exec_ok(t: Tick) -> bool:
    aq = t.board.canonical_ask_qty
    return bool(
        t.board.canonical_quote_valid
        and not t.board.canonical_crossed
        and aq is not None and aq >= LOT
        and t.board.canonical_best_ask and t.board.canonical_best_ask > 0
    )
