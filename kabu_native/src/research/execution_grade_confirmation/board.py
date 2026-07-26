"""Event-grade atomic board from raw market_capture JSONL (Buy1/Sell1)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence
from zoneinfo import ZoneInfo

from research.execution_grade_confirmation.constants import CAPTURE_ROOT, QUOTE_FRESHNESS_MS

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


def fnum(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def sym_norm(sym: Any) -> str:
    s = str(sym or "").strip()
    if s.endswith(".T"):
        return s
    if s.isdigit() or (len(s) >= 3 and s[:-1].isdigit()):
        return f"{s}.T" if not s.endswith(".T") else s
    return s if s.endswith(".T") else f"{s}.T"


@dataclass
class AtomicQuote:
    event_id: str
    symbol: str
    day: str
    exchange_time: Optional[datetime]
    received_at: datetime
    sequence: int
    current_price: Optional[float]
    current_price_time: Optional[datetime]
    best_bid: Optional[float]
    bid_qty: Optional[float]
    best_ask: Optional[float]
    ask_qty: Optional[float]
    depth_bids: list[tuple[float, float]] = field(default_factory=list)  # (px, qty) Buy1..
    depth_asks: list[tuple[float, float]] = field(default_factory=list)  # Sell1..
    quote_age_ms: Optional[float] = None
    price_age_ms: Optional[float] = None
    ask_gt_bid: bool = False
    same_payload: bool = True
    quote_valid: bool = False
    quote_invalid_reason: str = ""
    source_file: str = ""
    source_row: int = 0
    locked: bool = False  # ask == bid
    crossed: bool = False  # ask < bid (true book)
    kabu_bid: Optional[float] = None
    kabu_ask: Optional[float] = None


def _depth(op: dict, side: str, n: int = 10) -> list[tuple[float, float]]:
    out = []
    for i in range(1, n + 1):
        lv = op.get(f"{side}{i}")
        if not isinstance(lv, dict):
            continue
        px, qty = fnum(lv.get("Price")), fnum(lv.get("Qty"))
        if px is None or qty is None:
            continue
        out.append((px, qty))
    return out


def quote_from_record(rec: dict[str, Any], *, day: str, source_file: str, source_row: int) -> Optional[AtomicQuote]:
    op = rec.get("original_payload")
    if not isinstance(op, dict) or not op:
        return None
    recv = parse_ts(rec.get("received_at_jst")) or parse_ts(op.get("CurrentPriceTime"))
    if recv is None:
        return None
    sym = sym_norm(rec.get("symbol") or op.get("Symbol"))
    depth_bids = _depth(op, "Buy")
    depth_asks = _depth(op, "Sell")
    # true English book
    best_bid = depth_bids[0][0] if depth_bids else None
    bid_qty = depth_bids[0][1] if depth_bids else None
    best_ask = depth_asks[0][0] if depth_asks else None
    ask_qty = depth_asks[0][1] if depth_asks else None
    # fallback: invert kabu names if depth missing
    kabu_bid = fnum(op.get("BidPrice") if op.get("BidPrice") is not None else rec.get("bid"))
    kabu_ask = fnum(op.get("AskPrice") if op.get("AskPrice") is not None else rec.get("ask"))
    if best_bid is None and kabu_ask is not None:
        best_bid = kabu_ask  # AskPrice = Buy1
        bid_qty = fnum(op.get("AskQty"))
    if best_ask is None and kabu_bid is not None:
        best_ask = kabu_bid  # BidPrice = Sell1
        ask_qty = fnum(op.get("BidQty"))

    cpt = parse_ts(op.get("CurrentPriceTime"))
    px = fnum(op.get("CurrentPrice") if op.get("CurrentPrice") is not None else rec.get("current_price"))
    seq = int(rec.get("sequence") or 0)
    locked = best_bid is not None and best_ask is not None and abs(best_ask - best_bid) < 1e-12
    crossed = best_bid is not None and best_ask is not None and best_ask < best_bid
    ask_gt = best_bid is not None and best_ask is not None and best_ask > best_bid

    reason = ""
    valid = True
    if best_bid is None or best_bid <= 0:
        valid, reason = False, "missing_or_nonpositive_bid"
    elif best_ask is None or best_ask <= 0:
        valid, reason = False, "missing_or_nonpositive_ask"
    elif not ask_gt:
        valid, reason = False, "locked" if locked else "crossed_true_book"
    elif bid_qty is not None and bid_qty < 0:
        valid, reason = False, "negative_bid_qty"
    elif ask_qty is not None and ask_qty < 0:
        valid, reason = False, "negative_ask_qty"

    price_age = None
    if cpt is not None:
        price_age = max(0.0, (recv - cpt).total_seconds() * 1000.0)
        if price_age > QUOTE_FRESHNESS_MS:
            # soft: mark stale but keep quote if book ok
            if valid:
                reason = "stale_price_time"

    eid = f"{day}:{sym}:{seq}:{recv.isoformat()}"
    return AtomicQuote(
        event_id=eid,
        symbol=sym,
        day=day,
        exchange_time=cpt,
        received_at=recv,
        sequence=seq,
        current_price=px,
        current_price_time=cpt,
        best_bid=best_bid,
        bid_qty=bid_qty,
        best_ask=best_ask,
        ask_qty=ask_qty,
        depth_bids=depth_bids,
        depth_asks=depth_asks,
        quote_age_ms=None,
        price_age_ms=price_age,
        ask_gt_bid=ask_gt,
        same_payload=True,
        quote_valid=valid,
        quote_invalid_reason=reason,
        source_file=source_file,
        source_row=source_row,
        locked=locked,
        crossed=crossed,
        kabu_bid=kabu_bid,
        kabu_ask=kabu_ask,
    )


def iter_day_quotes(day: str, symbols: Optional[set[str]] = None) -> Iterable[AtomicQuote]:
    d = CAPTURE_ROOT / day
    if not d.is_dir():
        return
    symset = {sym_norm(s) for s in symbols} if symbols else None
    for part in sorted(d.glob("push_part_*.jsonl")):
        if part.stat().st_size == 0:
            continue
        with part.open("r", encoding="utf-8") as f:
            for row_i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                q = quote_from_record(rec, day=day, source_file=part.name, source_row=row_i)
                if q is None:
                    continue
                if symset is not None and q.symbol not in symset:
                    continue
                yield q


def load_quotes_for_symbols(day: str, symbols: Sequence[str]) -> dict[str, list[AtomicQuote]]:
    """Load and sort atomic quotes for symbols (one pass)."""
    symset = {sym_norm(s) for s in symbols}
    by: dict[str, list[AtomicQuote]] = {s: [] for s in symset}
    for q in iter_day_quotes(day, symset):
        by.setdefault(q.symbol, []).append(q)
    for s in by:
        by[s].sort(key=lambda x: (x.received_at, x.sequence))
        # enforce monotonic: drop non-monotonic received_at
        cleaned = []
        last = None
        for q in by[s]:
            if last is not None and q.received_at < last:
                q.quote_valid = False
                q.quote_invalid_reason = "non_monotonic_timestamp"
            cleaned.append(q)
            last = q.received_at
        by[s] = cleaned
    return by


def crossed_audit(quotes_by_sym: dict[str, list[AtomicQuote]]) -> dict[str, Any]:
    total = valid = crossed = locked = miss_b = miss_a = miss_q = stale = nonmono = 0
    kabu_crossed = 0
    true_crossed = 0
    by_day_sym: list[dict] = []
    examples = []
    for sym, xs in quotes_by_sym.items():
        st = dict(symbol=sym, n=len(xs), valid=0, crossed=0, locked=0)
        for q in xs:
            total += 1
            if q.quote_valid:
                valid += 1
                st["valid"] += 1
            if q.crossed:
                crossed += 1
                true_crossed += 1
                st["crossed"] += 1
                if len(examples) < 15:
                    examples.append(
                        {
                            "symbol": sym,
                            "received_at": q.received_at.isoformat(),
                            "best_bid": q.best_bid,
                            "best_ask": q.best_ask,
                            "kabu_bid": q.kabu_bid,
                            "kabu_ask": q.kabu_ask,
                            "reason": q.quote_invalid_reason,
                            "class": "TRUE_MARKET_CROSSED" if q.crossed else "OK",
                        }
                    )
            if q.locked:
                locked += 1
                st["locked"] += 1
            if q.best_bid is None:
                miss_b += 1
            if q.best_ask is None:
                miss_a += 1
            if q.bid_qty is None or q.ask_qty is None:
                miss_q += 1
            if q.quote_invalid_reason == "stale_price_time":
                stale += 1
            if q.quote_invalid_reason == "non_monotonic_timestamp":
                nonmono += 1
            if q.kabu_bid is not None and q.kabu_ask is not None and q.kabu_ask <= q.kabu_bid:
                kabu_crossed += 1
        by_day_sym.append(st)
    n = max(1, total)
    return {
        "total_board_events": total,
        "valid_board_events": valid,
        "crossed_board_events": crossed,
        "locked_board_events": locked,
        "missing_bid": miss_b,
        "missing_ask": miss_a,
        "missing_qty": miss_q,
        "stale_board": stale,
        "non_monotonic_timestamp": nonmono,
        "kabu_named_crossed": kabu_crossed,
        "true_book_crossed": true_crossed,
        "valid_rate": round(valid / n, 4),
        "crossed_rate_true_book": round(true_crossed / n, 4),
        "kabu_named_crossed_rate": round(kabu_crossed / n, 4),
        "classification": {
            "TRUE_MARKET_CROSSED": true_crossed,
            "ASYNC_FIELD_MERGE": 0,
            "ONE_SECOND_AGGREGATION_ARTIFACT": 0,
            "FIELD_MAPPING_ERROR": kabu_crossed,
            "STALE_QUOTE_CARRY": stale,
            "UNKNOWN": 0,
        },
        "by_symbol": sorted(by_day_sym, key=lambda r: -r["n"])[:40],
        "examples": examples,
    }
