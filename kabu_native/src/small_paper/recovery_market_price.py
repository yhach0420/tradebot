"""Recovery EXIT market-price selection (Paper only; no orders).

Priority for long close:
1. LAST_VALID_BID
2. LAST_VALID_CURRENT_PRICE
3. LAST_VALID_BOARD_MID
4. LAST_VALID_ASK (warning)
5. ENTRY_PRICE_FALLBACK (warning)
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

SOURCE_BID = "LAST_VALID_BID"
SOURCE_CURRENT = "LAST_VALID_CURRENT_PRICE"
SOURCE_MID = "LAST_VALID_BOARD_MID"
SOURCE_ASK = "LAST_VALID_ASK"
SOURCE_ENTRY = "ENTRY_PRICE_FALLBACK"

STALE_WARN_SEC = 60.0


def parse_ts(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        t = value
    else:
        s = str(value).replace("Z", "+00:00")
        try:
            t = datetime.fromisoformat(s)
        except ValueError:
            return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=JST)
    return t


def _f(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x) or x <= 0:
        return None
    return x


def round_to_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return round(price, 4)
    return round(round(price / tick) * tick, 10)


def tick_size_for(price: float) -> float:
    try:
        from research.low_price_risk_review import jpx_tick_size_yen

        return float(jpx_tick_size_yen(price))
    except Exception:
        return 1.0


@dataclass
class PriceCandidate:
    timestamp: datetime
    symbol: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    current_price: Optional[float] = None
    board_mid: Optional[float] = None
    source_file: str = ""
    source_line: int = -1
    source_record_id: str = ""
    event_type: str = ""

    def age_at(self, force_close: datetime) -> float:
        return (force_close - self.timestamp).total_seconds()


@dataclass
class RecoveryPriceDecision:
    recovery_price: float
    recovery_price_source: str
    selected_market_timestamp: Optional[str]
    price_age_at_force_close_sec: Optional[float]
    bid: Optional[float] = None
    ask: Optional[float] = None
    board_mid: Optional[float] = None
    current_price: Optional[float] = None
    tick_size: Optional[float] = None
    fallback_used: bool = False
    future_leak_check: str = "PASS"
    source_file: str = ""
    source_line: int = -1
    source_record_id: str = ""
    confidence: str = "HIGH"
    warning: str = ""
    candidates_considered: int = 0
    pnl_pct: float = 0.0
    pnl_yen_100: float = 0.0
    pnl_yen_100_cost5bps: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_candidate_from_mapping(
    row: Mapping[str, Any],
    *,
    symbol: str,
    source_file: str,
    source_line: int,
) -> Optional[PriceCandidate]:
    row_sym = str(row.get("symbol") or row.get("Symbol") or "")
    if row_sym and row_sym != symbol and row_sym.replace(".T", "") != symbol.replace(".T", ""):
        return None
    ts = parse_ts(
        row.get("current_price_time")
        or row.get("CurrentPriceTime")
        or row.get("board_time")
        or row.get("event_time")
        or row.get("timestamp")
        or row.get("ts")
    )
    if ts is None:
        return None
    bid = _f(
        row.get("canonical_best_bid")
        or row.get("best_bid")
        or row.get("board_bid")
        or row.get("bid")
        or row.get("BestBid")
        or row.get("BidPrice")  # raw kabu last; prefer canonical above
    )
    ask = _f(
        row.get("canonical_best_ask")
        or row.get("best_ask")
        or row.get("board_ask")
        or row.get("ask")
        or row.get("BestAsk")
        or row.get("AskPrice")
    )
    # If only raw kabu labels present (no canonical), reconstruct from Buy1/Sell1 when available
    if (row.get("canonical_best_bid") is None and isinstance(row.get("Buy1"), dict)) or (
        row.get("canonical_best_ask") is None and isinstance(row.get("Sell1"), dict)
    ):
        buy1 = row.get("Buy1") if isinstance(row.get("Buy1"), dict) else {}
        sell1 = row.get("Sell1") if isinstance(row.get("Sell1"), dict) else {}
        bid = _f(buy1.get("Price")) if buy1.get("Price") is not None else bid
        ask = _f(sell1.get("Price")) if sell1.get("Price") is not None else ask
    cur = _f(row.get("current_price") or row.get("CurrentPrice") or row.get("validated_current_price"))
    mid = None
    if bid is not None and ask is not None:
        mid = round_to_tick((bid + ask) / 2.0, tick_size_for((bid + ask) / 2.0))
    elif _f(row.get("board_mid")) is not None:
        mid = _f(row.get("board_mid"))
    if bid is None and ask is None and cur is None and mid is None:
        return None
    return PriceCandidate(
        timestamp=ts,
        symbol=symbol,
        bid=bid,
        ask=ask,
        current_price=cur,
        board_mid=mid,
        source_file=source_file,
        source_line=source_line,
        source_record_id=str(row.get("position_id") or row.get("message_index") or row.get("decision_id") or ""),
        event_type=str(row.get("event_type") or row.get("sample_kind") or ""),
    )


def iter_event_candidates(
    events_path: Path,
    *,
    symbol: str,
) -> list[PriceCandidate]:
    out: list[PriceCandidate] = []
    if not events_path.is_file():
        return out
    with events_path.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            c = extract_candidate_from_mapping(
                row, symbol=symbol, source_file=str(events_path), source_line=i
            )
            if c is not None:
                out.append(c)
    return out


def filter_candidates(
    candidates: Sequence[PriceCandidate],
    *,
    entry_time: datetime,
    force_close: datetime,
) -> tuple[list[PriceCandidate], list[str]]:
    """Keep entry_time <= ts <= force_close; reject future/past leaks."""
    kept: list[PriceCandidate] = []
    notes: list[str] = []
    for c in candidates:
        if c.timestamp > force_close:
            notes.append("rejected_future")
            continue
        if c.timestamp < entry_time:
            notes.append("rejected_before_entry")
            continue
        kept.append(c)
    kept.sort(key=lambda x: x.timestamp)
    return kept, notes


def select_recovery_price(
    *,
    symbol: str,
    entry_price: float,
    entry_time: datetime,
    force_close: datetime,
    candidates: Sequence[PriceCandidate],
    stale_warn_sec: float = STALE_WARN_SEC,
) -> RecoveryPriceDecision:
    valid, _notes = filter_candidates(candidates, entry_time=entry_time, force_close=force_close)
    tick = tick_size_for(entry_price)
    warnings: list[str] = []

    def _finish(
        price: float,
        source: str,
        cand: Optional[PriceCandidate],
        *,
        fallback: bool = False,
        extra_warning: str = "",
        confidence: str = "HIGH",
    ) -> RecoveryPriceDecision:
        pnl_pct = round((price / entry_price - 1.0) * 100.0, 4) if entry_price else 0.0
        pnl_yen = round((price - entry_price) * 100.0, 2)
        # reference 5bps round-trip cost on notional (not formal)
        notional = entry_price * 100.0
        cost = notional * 0.0005
        pnl_cost = round(pnl_yen - cost, 2)
        age = cand.age_at(force_close) if cand else None
        warn = extra_warning
        if age is not None and age > stale_warn_sec and source != SOURCE_ENTRY:
            warn = "STALE_LAST_MARKET_PRICE" if not warn else f"{warn};STALE_LAST_MARKET_PRICE"
        if fallback:
            warn = "ENTRY_PRICE_FALLBACK" if not warn else f"{warn};ENTRY_PRICE_FALLBACK"
        return RecoveryPriceDecision(
            recovery_price=price,
            recovery_price_source=source,
            selected_market_timestamp=cand.timestamp.isoformat() if cand else None,
            price_age_at_force_close_sec=round(age, 3) if age is not None else None,
            bid=cand.bid if cand else None,
            ask=cand.ask if cand else None,
            board_mid=cand.board_mid if cand else None,
            current_price=cand.current_price if cand else None,
            tick_size=tick,
            fallback_used=fallback,
            future_leak_check="PASS",
            source_file=cand.source_file if cand else "",
            source_line=cand.source_line if cand else -1,
            source_record_id=cand.source_record_id if cand else "",
            confidence=confidence,
            warning=warn,
            candidates_considered=len(valid),
            pnl_pct=pnl_pct,
            pnl_yen_100=pnl_yen,
            pnl_yen_100_cost5bps=pnl_cost,
        )

    # Prefer latest timestamp; within same timestamp prefer richer quote for chosen source.
    if not valid:
        return _finish(
            float(entry_price),
            SOURCE_ENTRY,
            None,
            fallback=True,
            confidence="LOW",
        )

    # Walk from newest to oldest and pick first available by priority.
    newest_first = list(reversed(valid))

    for c in newest_first:
        if c.bid is not None:
            return _finish(c.bid, SOURCE_BID, c)
    for c in newest_first:
        if c.current_price is not None:
            return _finish(c.current_price, SOURCE_CURRENT, c)
    for c in newest_first:
        if c.board_mid is not None:
            return _finish(c.board_mid, SOURCE_MID, c)
    for c in newest_first:
        if c.ask is not None:
            return _finish(
                c.ask,
                SOURCE_ASK,
                c,
                extra_warning="ASK_USED_FOR_LONG_CLOSE_OPTIMISTIC",
                confidence="MEDIUM",
            )
    return _finish(float(entry_price), SOURCE_ENTRY, None, fallback=True, confidence="LOW")


def resolve_recovery_price_for_position(
    *,
    symbol: str,
    entry_price: float,
    entry_time: datetime | str,
    force_close: datetime | str,
    events_path: Path,
    extra_candidates: Sequence[PriceCandidate] = (),
) -> RecoveryPriceDecision:
    et = parse_ts(entry_time)
    fc = parse_ts(force_close)
    if et is None or fc is None:
        raise ValueError("entry_time and force_close required")
    cands = list(iter_event_candidates(events_path, symbol=symbol))
    cands.extend(extra_candidates)
    return select_recovery_price(
        symbol=symbol,
        entry_price=float(entry_price),
        entry_time=et,
        force_close=fc,
        candidates=cands,
    )


def apply_decision_to_exit_event(
    exit_ev: dict[str, Any],
    decision: RecoveryPriceDecision,
    *,
    previous_recovery_price: Any = None,
    previous_pnl_yen_100: Any = None,
) -> dict[str, Any]:
    out = dict(exit_ev)
    out.update(
        {
            "exit_price": decision.recovery_price,
            "current_price": decision.current_price
            if decision.current_price is not None
            else decision.recovery_price,
            "pnl_pct": decision.pnl_pct,
            "pnl_yen_100": decision.pnl_yen_100,
            "actual_pnl_yen_100": decision.pnl_yen_100,
            "recovery_price": decision.recovery_price,
            "recovery_price_source": decision.recovery_price_source,
            "selected_market_timestamp": decision.selected_market_timestamp,
            "price_age_at_force_close_sec": decision.price_age_at_force_close_sec,
            "recovery_bid": decision.bid,
            "recovery_ask": decision.ask,
            "recovery_board_mid": decision.board_mid,
            "recovery_tick_size": decision.tick_size,
            "recovery_fallback_used": decision.fallback_used,
            "recovery_future_leak_check": decision.future_leak_check,
            "recovery_price_source_file": decision.source_file,
            "recovery_price_source_line": decision.source_line,
            "recovery_price_confidence": decision.confidence,
            "recovery_price_warning": decision.warning,
            "previous_recovery_price": previous_recovery_price,
            "previous_pnl_yen_100": previous_pnl_yen_100,
            "previous_price_source": "ENTRY_PRICE_FORCED_ZERO",
            "phase676_market_price_recovery": True,
        }
    )
    return out
