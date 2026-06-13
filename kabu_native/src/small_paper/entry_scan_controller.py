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


def _field_age_sec(payload: Mapping[str, Any], field: str) -> tuple[Optional[str], Optional[float]]:
    raw = payload.get(field)
    if raw is None or str(raw).strip() == "":
        return None, None
    now = datetime.now(JST)
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
) -> EntryFreshnessSnapshot:
    price_ts, price_age = _field_age_sec(payload, "CurrentPriceTime")
    bid_ts, bid_age = _field_age_sec(payload, "BidTime")
    ask_ts, ask_age = _field_age_sec(payload, "AskTime")
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


def check_entry_data_freshness(
    snap: EntryFreshnessSnapshot,
    *,
    max_price_age_sec: float,
    max_board_age_sec: float,
    guard_enabled: bool = True,
) -> Optional[str]:
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
        batch_enabled: bool = True,
        pipeline_source: str = "live",
        audit_writer: Optional[Callable[[Mapping[str, Any]], None]] = None,
    ) -> None:
        self.max_price_age_sec = float(max_price_age_sec)
        self.max_board_age_sec = float(max_board_age_sec)
        self.max_entries_per_scan = max(1, int(max_entries_per_scan))
        self.scan_window_sec = float(scan_window_sec)
        self.freshness_guard_enabled = bool(freshness_guard_enabled)
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


def entry_scan_controller_from_config(
    config: Any,
    *,
    pipeline_source: str,
    audit_writer: Optional[Callable[[Mapping[str, Any]], None]] = None,
) -> EntryScanController:
    return EntryScanController(
        max_price_age_sec=float(getattr(config, "entry_max_price_age_sec", 3.0) or 3.0),
        max_board_age_sec=float(getattr(config, "entry_max_board_age_sec", 3.0) or 3.0),
        max_entries_per_scan=int(getattr(config, "max_entries_per_scan", 1) or 1),
        scan_window_sec=float(getattr(config, "entry_scan_window_sec", 2.0) or 2.0),
        freshness_guard_enabled=bool(getattr(config, "entry_freshness_guard_enabled", True)),
        batch_enabled=bool(getattr(config, "entry_scan_batch_enabled", True)),
        pipeline_source=pipeline_source,
        audit_writer=audit_writer,
    )


__all__ = [
    "DATA_SOURCE_KABU_PUSH",
    "EntryFreshnessSnapshot",
    "EntryScanController",
    "PendingEntryCandidate",
    "REJECT_DATA_STALE_BOARD",
    "REJECT_DATA_STALE_PRICE",
    "REJECT_MAX_ENTRIES_PER_SCAN",
    "ScanFlushResult",
    "candidate_rank_score",
    "check_entry_data_freshness",
    "compute_entry_freshness",
    "entry_scan_controller_from_config",
]
