"""
ENTRY scan batching, data-freshness guard, and audit logging (live / push-replay).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from storage.intraday_recorder import parse_kabu_time

JST = ZoneInfo("Asia/Tokyo")
log = logging.getLogger("kabu_native.small_paper.entry_scan")

REJECT_DATA_STALE_PRICE = "data_stale_price"
REJECT_DATA_STALE_BOARD = "data_stale_board"
REJECT_EVENT_STALE_PRICE = "event_stale_price"
REJECT_MAX_ENTRIES_PER_SCAN = "max_entries_per_scan"

DATA_SOURCE_KABU_PUSH = "kabu_push"
DATA_SOURCE_KABU_BOARD = "kabu_board"
DATA_SOURCE_YAHOO = "yahoo"
DATA_SOURCE_MIXED = "mixed"
DATA_SOURCE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class EntryFreshnessSnapshot:
    data_source: str
    last_price_update_ts: Optional[str]
    last_board_update_ts: Optional[str]
    price_age_sec: Optional[float]
    board_age_sec: Optional[float]


PRICE_FRESHNESS_CURRENT = "current_price_time"
PRICE_FRESHNESS_BOARD_FALLBACK = "board_fallback"
PRICE_FRESHNESS_STALE_REJECT = "stale_reject"
PRICE_FRESHNESS_LIQUIDITY_STALE_TRADE = "liquidity_stale_trade"

DEFAULT_BOARD_FALLBACK_MAX_SPREAD_BPS = 50.0


@dataclass(frozen=True)
class EntryFreshnessDecision:
    reject_reason: Optional[str]
    price_freshness_source: str
    spread_bps: Optional[float]
    fallback_used: bool
    fallback_reject_reason: Optional[str]
    snapshot: EntryFreshnessSnapshot
    event_stale: bool = False
    board_stale: bool = False
    trade_stale: bool = False


@dataclass
class PendingEntryCandidate:
    symbol: str
    trade: dict[str, Any]
    decision: Any
    payload: dict[str, Any]
    enriched: dict[str, Any]
    msg_i: int
    freshness: EntryFreshnessSnapshot
    eval_start_ts: str
    eval_end_ts: str
    eval_latency_ms: float
    entry_signal_ts: str
    entry_signal_mono: float = 0.0
    rank_score: float = 0.0
    bucket: str = ""
    score5_ord: Optional[int] = None


@dataclass
class ScanFlushResult:
    scan_id: str
    scan_start_ts: str
    scan_end_ts: str
    scan_duration_sec: float
    evaluated_symbols_count: int
    entry_candidates_count: int
    entries_sent_count: int
    accepted: list[PendingEntryCandidate] = field(default_factory=list)
    rejected_max_scan: list[PendingEntryCandidate] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="milliseconds")


def _field_age_sec(
    payload: Mapping[str, Any],
    field: str,
    *,
    reference_now: Optional[datetime] = None,
) -> tuple[Optional[str], Optional[float]]:
    raw = payload.get(field)
    if raw is None or str(raw).strip() == "":
        return None, None
    now = reference_now if reference_now is not None else datetime.now(JST)
    tick = parse_kabu_time(raw, fallback=now)
    ts = tick.isoformat(timespec="milliseconds")
    age = max(0.0, (now - tick).total_seconds())
    return ts, age


def resolve_data_source(*, pipeline_source: str, payload: Mapping[str, Any]) -> str:
    src = str(pipeline_source or "").lower()
    if src in ("live", "push-replay", "push_replay"):
        has_board = payload.get("BidQty") is not None or payload.get("AskQty") is not None
        if has_board:
            return DATA_SOURCE_KABU_PUSH
        return DATA_SOURCE_KABU_PUSH
    if src == "poll":
        return DATA_SOURCE_KABU_BOARD
    if "yahoo" in src or "replay" in src:
        return DATA_SOURCE_YAHOO
    return DATA_SOURCE_UNKNOWN


def compute_entry_freshness(
    payload: Mapping[str, Any],
    *,
    pipeline_source: str,
    reference_now: Optional[datetime] = None,
) -> EntryFreshnessSnapshot:
    price_ts, price_age = _field_age_sec(
        payload, "CurrentPriceTime", reference_now=reference_now
    )
    bid_ts, bid_age = _field_age_sec(payload, "BidTime", reference_now=reference_now)
    ask_ts, ask_age = _field_age_sec(payload, "AskTime", reference_now=reference_now)
    board_candidates = [(bid_ts, bid_age), (ask_ts, ask_age)]
    board_ts = None
    board_age = None
    for ts, age in board_candidates:
        if ts is None:
            continue
        if board_age is None or (age is not None and age < board_age):
            board_ts = ts
            board_age = age
    return EntryFreshnessSnapshot(
        data_source=resolve_data_source(pipeline_source=pipeline_source, payload=payload),
        last_price_update_ts=price_ts,
        last_board_update_ts=board_ts,
        price_age_sec=price_age,
        board_age_sec=board_age,
    )


def _spread_bps_from_payload(payload: Mapping[str, Any]) -> Optional[float]:
    from universe.filters import calc_spread_bps

    return calc_spread_bps(payload)


def _price_ts_fresh(snap: EntryFreshnessSnapshot, *, max_price_age_sec: float) -> bool:
    return (
        snap.last_price_update_ts is not None
        and snap.price_age_sec is not None
        and snap.price_age_sec <= float(max_price_age_sec)
    )


def _board_fallback_eligible(
    payload: Mapping[str, Any],
    snap: EntryFreshnessSnapshot,
    *,
    max_board_age_sec: float,
    max_spread_bps: float,
) -> tuple[bool, Optional[str]]:
    reasons: list[str] = []
    if snap.board_age_sec is None or snap.board_age_sec > float(max_board_age_sec):
        reasons.append("board_stale")
    calc = payload.get("CalcPrice")
    if calc is None or (isinstance(calc, str) and not str(calc).strip()):
        reasons.append("missing_calc_price")
    bid = payload.get("BidPrice")
    ask = payload.get("AskPrice")
    if bid is None or ask is None:
        reasons.append("missing_bid_ask_price")
    spread = _spread_bps_from_payload(payload)
    if spread is None:
        reasons.append("missing_spread")
    elif spread > float(max_spread_bps):
        reasons.append("spread_above_max")
    if reasons:
        return False, ";".join(reasons)
    return True, None


def _board_stale(snap: EntryFreshnessSnapshot, *, max_board_age_sec: float) -> bool:
    return (
        snap.last_board_update_ts is None
        or snap.board_age_sec is None
        or snap.board_age_sec > float(max_board_age_sec)
    )


def _event_age_sec(
    payload: Mapping[str, Any],
    *,
    reference_now: Optional[datetime] = None,
) -> Optional[float]:
    now = reference_now if reference_now is not None else datetime.now(JST)
    raw = payload.get("recorded_at")
    if raw is None or str(raw).strip() == "":
        return None
    rec_dt = parse_kabu_time(raw, fallback=now)
    return max(0.0, (now - rec_dt).total_seconds())


def _event_stale(
    payload: Mapping[str, Any],
    *,
    reference_now: Optional[datetime],
    threshold_sec: float,
) -> bool:
    age = _event_age_sec(payload, reference_now=reference_now)
    return age is None or age > float(threshold_sec)


def _trade_stale(snap: EntryFreshnessSnapshot, *, threshold_sec: float) -> bool:
    if snap.last_price_update_ts is None or snap.price_age_sec is None:
        return True
    return snap.price_age_sec > float(threshold_sec)


def _evaluate_freshness_semantics_v2(
    snap: EntryFreshnessSnapshot,
    payload: Mapping[str, Any],
    *,
    event_stale_threshold_sec: float,
    board_stale_threshold_sec: float,
    trade_stale_threshold_sec: float,
    trade_stale_mode: str,
    guard_enabled: bool = True,
    reference_now: Optional[datetime] = None,
) -> EntryFreshnessDecision:
    spread_bps = _spread_bps_from_payload(payload)
    if not guard_enabled:
        return EntryFreshnessDecision(
            reject_reason=None,
            price_freshness_source=PRICE_FRESHNESS_CURRENT,
            spread_bps=spread_bps,
            fallback_used=False,
            fallback_reject_reason=None,
            snapshot=snap,
        )

    event_st = _event_stale(
        payload,
        reference_now=reference_now,
        threshold_sec=event_stale_threshold_sec,
    )
    board_st = _board_stale(snap, max_board_age_sec=board_stale_threshold_sec)
    trade_st = _trade_stale(snap, threshold_sec=trade_stale_threshold_sec)

    if event_st:
        return EntryFreshnessDecision(
            reject_reason=REJECT_EVENT_STALE_PRICE,
            price_freshness_source=PRICE_FRESHNESS_STALE_REJECT,
            spread_bps=spread_bps,
            fallback_used=False,
            fallback_reject_reason=None,
            snapshot=snap,
            event_stale=True,
            board_stale=board_st,
            trade_stale=trade_st,
        )

    if board_st:
        return EntryFreshnessDecision(
            reject_reason=REJECT_DATA_STALE_BOARD,
            price_freshness_source=PRICE_FRESHNESS_CURRENT,
            spread_bps=spread_bps,
            fallback_used=False,
            fallback_reject_reason=None,
            snapshot=snap,
            event_stale=False,
            board_stale=True,
            trade_stale=trade_st,
        )

    mode = str(trade_stale_mode or "tag_only").strip().lower()
    if trade_st and mode == "tag_only":
        return EntryFreshnessDecision(
            reject_reason=None,
            price_freshness_source=PRICE_FRESHNESS_LIQUIDITY_STALE_TRADE,
            spread_bps=spread_bps,
            fallback_used=False,
            fallback_reject_reason=None,
            snapshot=snap,
            event_stale=False,
            board_stale=False,
            trade_stale=True,
        )

    return EntryFreshnessDecision(
        reject_reason=None,
        price_freshness_source=PRICE_FRESHNESS_CURRENT,
        spread_bps=spread_bps,
        fallback_used=False,
        fallback_reject_reason=None,
        snapshot=snap,
        event_stale=False,
        board_stale=False,
        trade_stale=trade_st,
    )


def evaluate_entry_data_freshness(
    snap: EntryFreshnessSnapshot,
    payload: Mapping[str, Any],
    *,
    max_price_age_sec: float,
    max_board_age_sec: float,
    guard_enabled: bool = True,
    board_fallback_enabled: bool = True,
    max_fallback_spread_bps: float = DEFAULT_BOARD_FALLBACK_MAX_SPREAD_BPS,
    reference_now: Optional[datetime] = None,
    freshness_semantics_v2_enabled: bool = False,
    event_stale_threshold_sec: float = 3.0,
    board_stale_threshold_sec: float = 3.0,
    trade_stale_threshold_sec: float = 10.0,
    trade_stale_mode: str = "tag_only",
) -> EntryFreshnessDecision:
    if freshness_semantics_v2_enabled:
        return _evaluate_freshness_semantics_v2(
            snap,
            payload,
            event_stale_threshold_sec=event_stale_threshold_sec,
            board_stale_threshold_sec=board_stale_threshold_sec,
            trade_stale_threshold_sec=trade_stale_threshold_sec,
            trade_stale_mode=trade_stale_mode,
            guard_enabled=guard_enabled,
            reference_now=reference_now,
        )

    spread_bps = _spread_bps_from_payload(payload)

    if not guard_enabled:
        return EntryFreshnessDecision(
            reject_reason=None,
            price_freshness_source=PRICE_FRESHNESS_CURRENT,
            spread_bps=spread_bps,
            fallback_used=False,
            fallback_reject_reason=None,
            snapshot=snap,
        )

    if _price_ts_fresh(snap, max_price_age_sec=max_price_age_sec):
        if snap.last_board_update_ts is None:
            return EntryFreshnessDecision(
                reject_reason=REJECT_DATA_STALE_BOARD,
                price_freshness_source=PRICE_FRESHNESS_CURRENT,
                spread_bps=spread_bps,
                fallback_used=False,
                fallback_reject_reason=None,
                snapshot=snap,
            )
        if snap.board_age_sec is None or snap.board_age_sec > float(max_board_age_sec):
            return EntryFreshnessDecision(
                reject_reason=REJECT_DATA_STALE_BOARD,
                price_freshness_source=PRICE_FRESHNESS_CURRENT,
                spread_bps=spread_bps,
                fallback_used=False,
                fallback_reject_reason=None,
                snapshot=snap,
            )
        return EntryFreshnessDecision(
            reject_reason=None,
            price_freshness_source=PRICE_FRESHNESS_CURRENT,
            spread_bps=spread_bps,
            fallback_used=False,
            fallback_reject_reason=None,
            snapshot=snap,
        )

    if board_fallback_enabled:
        ok, fb_reason = _board_fallback_eligible(
            payload,
            snap,
            max_board_age_sec=max_board_age_sec,
            max_spread_bps=max_fallback_spread_bps,
        )
        if ok:
            return EntryFreshnessDecision(
                reject_reason=None,
                price_freshness_source=PRICE_FRESHNESS_BOARD_FALLBACK,
                spread_bps=spread_bps,
                fallback_used=True,
                fallback_reject_reason=None,
                snapshot=snap,
            )
        return EntryFreshnessDecision(
            reject_reason=REJECT_DATA_STALE_PRICE,
            price_freshness_source=PRICE_FRESHNESS_STALE_REJECT,
            spread_bps=spread_bps,
            fallback_used=False,
            fallback_reject_reason=fb_reason,
            snapshot=snap,
        )

    return EntryFreshnessDecision(
        reject_reason=REJECT_DATA_STALE_PRICE,
        price_freshness_source=PRICE_FRESHNESS_STALE_REJECT,
        spread_bps=spread_bps,
        fallback_used=False,
        fallback_reject_reason="board_fallback_disabled",
        snapshot=snap,
    )


def check_entry_data_freshness(
    snap: EntryFreshnessSnapshot,
    *,
    max_price_age_sec: float,
    max_board_age_sec: float,
    guard_enabled: bool = True,
    payload: Optional[Mapping[str, Any]] = None,
    board_fallback_enabled: bool = True,
    max_fallback_spread_bps: float = DEFAULT_BOARD_FALLBACK_MAX_SPREAD_BPS,
    reference_now: Optional[datetime] = None,
    freshness_semantics_v2_enabled: bool = False,
    event_stale_threshold_sec: float = 3.0,
    board_stale_threshold_sec: float = 3.0,
    trade_stale_threshold_sec: float = 10.0,
    trade_stale_mode: str = "tag_only",
) -> Optional[str]:
    if payload is None:
        if freshness_semantics_v2_enabled:
            return None
        if not guard_enabled:
            return None
        if snap.last_price_update_ts is None:
            return REJECT_DATA_STALE_PRICE
        if snap.price_age_sec is None or snap.price_age_sec > float(max_price_age_sec):
            return REJECT_DATA_STALE_PRICE
        if snap.last_board_update_ts is None:
            return REJECT_DATA_STALE_BOARD
        if snap.board_age_sec is None or snap.board_age_sec > float(max_board_age_sec):
            return REJECT_DATA_STALE_BOARD
        return None
    return evaluate_entry_data_freshness(
        snap,
        payload,
        max_price_age_sec=max_price_age_sec,
        max_board_age_sec=max_board_age_sec,
        guard_enabled=guard_enabled,
        board_fallback_enabled=board_fallback_enabled,
        max_fallback_spread_bps=max_fallback_spread_bps,
        reference_now=reference_now,
        freshness_semantics_v2_enabled=freshness_semantics_v2_enabled,
        event_stale_threshold_sec=event_stale_threshold_sec,
        board_stale_threshold_sec=board_stale_threshold_sec,
        trade_stale_threshold_sec=trade_stale_threshold_sec,
        trade_stale_mode=trade_stale_mode,
    ).reject_reason


def candidate_rank_score(trade: Mapping[str, Any], freshness: EntryFreshnessSnapshot) -> float:
    try:
        v2 = float(trade.get("entry_expectancy_score_v2") or 0)
    except (TypeError, ValueError):
        v2 = 0.0
    try:
        cq = float(trade.get("continuation_quality_score") or 0)
    except (TypeError, ValueError):
        cq = 0.0
    try:
        tv = float(trade.get("trading_value") or 0)
    except (TypeError, ValueError):
        tv = 0.0
    try:
        imb = float(trade.get("entry_order_book_imbalance") or 0.5)
    except (TypeError, ValueError):
        imb = 0.5
    try:
        vwap_dev = float(trade.get("entry_vwap_dev_pct") or 0)
    except (TypeError, ValueError):
        vwap_dev = 0.0
    try:
        mom = float(trade.get("momentum_continuation_score") or 0)
    except (TypeError, ValueError):
        mom = 0.0
    price_age = float(freshness.price_age_sec if freshness.price_age_sec is not None else 99.0)
    return (
        v2 * 1000.0
        + cq * 100.0
        + min(tv / 1e9, 20.0)
        + imb * 10.0
        + max(vwap_dev, 0.0) * 5.0
        + mom * 50.0
        - price_age * 100.0
    )


@dataclass
class _ActiveScan:
    scan_id: str
    scan_start_ts: str
    scan_start_mono: float
    evaluated_symbols: set[str] = field(default_factory=set)
    candidates: list[PendingEntryCandidate] = field(default_factory=list)


class EntryScanController:
    """Groups near-simultaneous ENTRY candidates; enforces per-scan cap after ranking."""

    def __init__(
        self,
        *,
        max_price_age_sec: float = 3.0,
        max_board_age_sec: float = 3.0,
        max_entries_per_scan: int = 1,
        scan_window_sec: float = 2.0,
        freshness_guard_enabled: bool = True,
        board_fallback_enabled: bool = True,
        board_fallback_max_spread_bps: float = DEFAULT_BOARD_FALLBACK_MAX_SPREAD_BPS,
        freshness_semantics_v2_enabled: bool = False,
        event_stale_threshold_sec: float = 3.0,
        board_stale_threshold_sec: float = 3.0,
        trade_stale_threshold_sec: float = 10.0,
        trade_stale_mode: str = "tag_only",
        batch_enabled: bool = True,
        pipeline_source: str = "live",
        audit_writer: Optional[Callable[[Mapping[str, Any]], None]] = None,
    ) -> None:
        self.max_price_age_sec = float(max_price_age_sec)
        self.max_board_age_sec = float(max_board_age_sec)
        self.max_entries_per_scan = max(1, int(max_entries_per_scan))
        self.scan_window_sec = float(scan_window_sec)
        self.freshness_guard_enabled = bool(freshness_guard_enabled)
        self.board_fallback_enabled = bool(board_fallback_enabled)
        self.board_fallback_max_spread_bps = float(board_fallback_max_spread_bps)
        self.freshness_semantics_v2_enabled = bool(freshness_semantics_v2_enabled)
        self.event_stale_threshold_sec = float(event_stale_threshold_sec)
        self.board_stale_threshold_sec = float(board_stale_threshold_sec)
        self.trade_stale_threshold_sec = float(trade_stale_threshold_sec)
        self.trade_stale_mode = str(trade_stale_mode or "tag_only")
        self.batch_enabled = bool(batch_enabled)
        self.pipeline_source = pipeline_source
        self._audit_writer = audit_writer
        self._scan: Optional[_ActiveScan] = None
        self._scan_seq = 0

    def _new_scan_id(self) -> str:
        self._scan_seq += 1
        stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
        return f"{stamp}_{self._scan_seq:03d}"

    def _start_scan(self, now_mono: float) -> _ActiveScan:
        scan = _ActiveScan(
            scan_id=self._new_scan_id(),
            scan_start_ts=_now_iso(),
            scan_start_mono=now_mono,
        )
        self._scan = scan
        return scan

    def _active_scan(self, now_mono: float) -> _ActiveScan:
        if self._scan is None:
            return self._start_scan(now_mono)
        if (
            self.batch_enabled
            and self.scan_window_sec > 0
            and (now_mono - self._scan.scan_start_mono) > self.scan_window_sec
        ):
            return self._start_scan(now_mono)
        return self._scan

    def begin_symbol_eval(self, *, now_mono: Optional[float] = None) -> tuple[str, Optional[ScanFlushResult]]:
        mono = float(now_mono if now_mono is not None else time.monotonic())
        flush_result: Optional[ScanFlushResult] = None
        if self._scan is not None and self.batch_enabled:
            if self.scan_window_sec <= 0 or (mono - self._scan.scan_start_mono) > self.scan_window_sec:
                if self._scan.candidates or self._scan.evaluated_symbols:
                    flush_result = self._flush_locked()
        scan = self._active_scan(mono)
        return scan.scan_id, flush_result

    def record_symbol_eval(
        self,
        *,
        scan_id: str,
        symbol: str,
        freshness: EntryFreshnessSnapshot,
        trade: Mapping[str, Any],
        entry_decision: bool,
        reject_reason: str = "",
        eval_start_ts: str = "",
        eval_end_ts: str = "",
        eval_latency_ms: float = 0.0,
        price_freshness_source: str = "",
        spread_bps: Optional[float] = None,
        fallback_used: bool = False,
        fallback_reject_reason: str = "",
        event_stale: bool = False,
        board_stale: bool = False,
        trade_stale: bool = False,
    ) -> None:
        if self._scan is not None:
            self._scan.evaluated_symbols.add(symbol)
        reasons = []
        raw_reasons = trade.get("entry_expectancy_score_v2_reasons") or trade.get(
            "entry_score_v2_reasons"
        )
        if raw_reasons:
            reasons = str(raw_reasons).split(";")
        row = {
            "audit_type": "entry_symbol_eval",
            "scan_id": scan_id,
            "symbol": symbol,
            "eval_start_ts": eval_start_ts,
            "eval_end_ts": eval_end_ts,
            "eval_latency_ms": round(eval_latency_ms, 1),
            "last_price_update_ts": freshness.last_price_update_ts,
            "last_board_update_ts": freshness.last_board_update_ts,
            "price_age_sec": freshness.price_age_sec,
            "board_age_sec": freshness.board_age_sec,
            "data_source": freshness.data_source,
            "entry_score_v2": trade.get("entry_expectancy_score_v2"),
            "entry_reasons": ";".join(r for r in reasons if r),
            "entry_decision": entry_decision,
            "reject_reason": reject_reason,
            "price_freshness_source": price_freshness_source,
            "current_price_age_sec": freshness.price_age_sec,
            "spread_bps": spread_bps,
            "fallback_used": fallback_used,
            "fallback_reject_reason": fallback_reject_reason,
            "event_stale": event_stale,
            "board_stale": board_stale,
            "trade_stale": trade_stale,
        }
        self._write_audit(row)

    def queue_accepted_candidate(self, candidate: PendingEntryCandidate) -> None:
        if self._scan is None:
            self._start_scan(time.monotonic())
        assert self._scan is not None
        candidate.rank_score = candidate_rank_score(candidate.trade, candidate.freshness)
        self._scan.candidates.append(candidate)

    def maybe_flush_after_eval(self) -> Optional[ScanFlushResult]:
        if not self.batch_enabled or self.scan_window_sec > 0:
            return None
        return self._flush_locked()

    def flush_pending(self) -> Optional[ScanFlushResult]:
        if self._scan is None:
            return None
        if not self._scan.candidates and not self._scan.evaluated_symbols:
            return None
        return self._flush_locked()

    def _flush_locked(self) -> ScanFlushResult:
        assert self._scan is not None
        scan = self._scan
        end_ts = _now_iso()
        duration = max(0.0, time.monotonic() - scan.scan_start_mono)
        ranked = sorted(scan.candidates, key=lambda c: c.rank_score, reverse=True)
        n_cand = len(ranked)
        cap = self.max_entries_per_scan if self.batch_enabled else max(1, n_cand)
        accepted = ranked[:cap]
        rejected = ranked[cap:]
        result = ScanFlushResult(
            scan_id=scan.scan_id,
            scan_start_ts=scan.scan_start_ts,
            scan_end_ts=end_ts,
            scan_duration_sec=round(duration, 3),
            evaluated_symbols_count=len(scan.evaluated_symbols),
            entry_candidates_count=n_cand,
            entries_sent_count=len(accepted),
            accepted=accepted,
            rejected_max_scan=rejected,
        )
        self._write_audit(
            {
                "audit_type": "entry_scan_summary",
                "scan_id": scan.scan_id,
                "scan_start_ts": scan.scan_start_ts,
                "scan_end_ts": end_ts,
                "scan_duration_sec": result.scan_duration_sec,
                "evaluated_symbols_count": result.evaluated_symbols_count,
                "entry_candidates_count": result.entry_candidates_count,
                "entries_sent_count": result.entries_sent_count,
                "same_scan_batch_entry": n_cand > 1,
            }
        )
        for i, cand in enumerate(accepted, start=1):
            self._write_audit(
                {
                    "audit_type": "entry_notify",
                    "scan_id": scan.scan_id,
                    "symbol": cand.symbol,
                    "entry_signal_ts": cand.entry_signal_ts,
                    "entry_price": cand.payload.get("CurrentPrice"),
                    "data_source": cand.freshness.data_source,
                    "price_age_sec": cand.freshness.price_age_sec,
                    "board_age_sec": cand.freshness.board_age_sec,
                    "same_scan_rank": f"{i}/{n_cand}" if n_cand else "1/1",
                    "same_scan_candidates": n_cand,
                    "is_same_scan_batch_entry": n_cand > 1,
                    "entry_decision": True,
                    "reject_reason": "",
                }
            )
        for cand in rejected:
            self._write_audit(
                {
                    "audit_type": "entry_notify",
                    "scan_id": scan.scan_id,
                    "symbol": cand.symbol,
                    "entry_signal_ts": cand.entry_signal_ts,
                    "entry_decision": False,
                    "reject_reason": REJECT_MAX_ENTRIES_PER_SCAN,
                    "same_scan_candidates": n_cand,
                    "is_same_scan_batch_entry": True,
                }
            )
        self._scan = None
        return result

    def _write_audit(self, row: Mapping[str, Any]) -> None:
        if self._audit_writer is not None:
            self._audit_writer(row)
        log.info("entry_scan_audit %s", row)


def _config_float(config: Any, name: str, default: float) -> float:
    """Read a float config value; preserve explicit 0.0 (do not treat as missing)."""
    raw = getattr(config, name, default)
    if raw is None:
        return float(default)
    return float(raw)


def entry_scan_controller_from_config(
    config: Any,
    *,
    pipeline_source: str,
    audit_writer: Optional[Callable[[Mapping[str, Any]], None]] = None,
) -> EntryScanController:
    return EntryScanController(
        max_price_age_sec=_config_float(config, "entry_max_price_age_sec", 3.0),
        max_board_age_sec=_config_float(config, "entry_max_board_age_sec", 3.0),
        max_entries_per_scan=int(getattr(config, "max_entries_per_scan", 1) or 1),
        # Phase629A: `or 2.0` treated explicit 0.0 as missing and forced a 2s wall-clock
        # batch window, so Stage call overhead changed flush boundaries and accepted counts.
        scan_window_sec=_config_float(config, "entry_scan_window_sec", 2.0),
        freshness_guard_enabled=bool(getattr(config, "entry_freshness_guard_enabled", True)),
        board_fallback_enabled=bool(getattr(config, "entry_freshness_board_fallback_enabled", False)),
        board_fallback_max_spread_bps=float(
            getattr(config, "entry_freshness_board_fallback_max_spread_bps", DEFAULT_BOARD_FALLBACK_MAX_SPREAD_BPS)
            or DEFAULT_BOARD_FALLBACK_MAX_SPREAD_BPS
        ),
        freshness_semantics_v2_enabled=bool(getattr(config, "freshness_semantics_v2_enabled", False)),
        event_stale_threshold_sec=float(getattr(config, "event_stale_threshold_sec", 3.0) or 3.0),
        board_stale_threshold_sec=float(getattr(config, "board_stale_threshold_sec", 3.0) or 3.0),
        trade_stale_threshold_sec=float(getattr(config, "trade_stale_threshold_sec", 10.0) or 10.0),
        trade_stale_mode=str(getattr(config, "trade_stale_mode", "tag_only") or "tag_only"),
        batch_enabled=bool(getattr(config, "entry_scan_batch_enabled", True)),
        pipeline_source=pipeline_source,
        audit_writer=audit_writer,
    )


__all__ = [
    "DATA_SOURCE_KABU_PUSH",
    "DEFAULT_BOARD_FALLBACK_MAX_SPREAD_BPS",
    "EntryFreshnessDecision",
    "EntryFreshnessSnapshot",
    "EntryScanController",
    "PRICE_FRESHNESS_BOARD_FALLBACK",
    "PRICE_FRESHNESS_CURRENT",
    "PRICE_FRESHNESS_LIQUIDITY_STALE_TRADE",
    "PRICE_FRESHNESS_STALE_REJECT",
    "PendingEntryCandidate",
    "REJECT_DATA_STALE_BOARD",
    "REJECT_DATA_STALE_PRICE",
    "REJECT_EVENT_STALE_PRICE",
    "REJECT_MAX_ENTRIES_PER_SCAN",
    "ScanFlushResult",
    "candidate_rank_score",
    "check_entry_data_freshness",
    "compute_entry_freshness",
    "entry_scan_controller_from_config",
    "evaluate_entry_data_freshness",
]
