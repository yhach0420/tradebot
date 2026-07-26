"""Prospective execution-grade capture (observer-only; no orders, no mainline hook)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.execution_grade_confirmation.board import quote_from_record, sym_norm
from research.execution_grade_confirmation.constants import NATIVE

JST = ZoneInfo("Asia/Tokyo")

CAPTURE_SCHEMA_VERSION = "EGC_1.0"
DEFAULT_OUT = NATIVE / "results" / "research" / "execution_grade_capture_live"


def payload_hash(op: dict[str, Any], symbol: str, recv: str) -> str:
    parts = [symbol, recv, str(op.get("CurrentPrice")), str(op.get("TradingVolume"))]
    for side in ("Buy", "Sell"):
        for i in range(1, 11):
            lv = op.get(f"{side}{i}") or {}
            if isinstance(lv, dict):
                parts.append(f"{lv.get('Price')}:{lv.get('Qty')}")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


@dataclass
class ProspectiveCaptureSpec:
    """Spec for future capture-only observer (not wired into Paper/mainline)."""

    schema_version: str = CAPTURE_SCHEMA_VERSION
    submit: int = 0
    cancel: int = 0
    live_order: int = 0
    paper_orders: bool = False
    affects_mainline_entry_exit: bool = False
    universe_changed: bool = False
    fields: list[str] = field(
        default_factory=lambda: [
            "raw_board_push",
            "raw_price_push",
            "payload_hash",
            "sequence",
            "exchange_time",
            "received_at",
            "bid_ask_levels_1_to_10",
            "current_price",
            "trading_volume",
            "confirmation_candidate",
            "strict_confirmation",
            "decision_timestamp",
            "same_event_ask",
            "ask_after_100ms_250ms_500ms_1s",
            "exit_decision",
            "same_event_bid",
            "bid_after_100ms_250ms_500ms_1s",
            "virtual_100share_fill",
        ]
    )
    forward_ready_gates: dict[str, Any] = field(
        default_factory=lambda: {
            "true_oos_days_min": 10,
            "am_days_min": 5,
            "pm_days_min": 5,
            "strict_confirmation_min": 300,
            "confirmation_ask_coverage_min": 0.90,
            "exit_bid_coverage_min": 0.90,
            "crossed_quote_rate_max": 0.01,
            "fill_100_evaluable_min": 0.80,
        }
    )


@dataclass
class DayQuality:
    day: str
    total_push: int = 0
    board_push: int = 0
    valid_atomic_quote: int = 0
    valid_ask: int = 0
    valid_bid: int = 0
    crossed: int = 0
    locked: int = 0
    missing_qty: int = 0
    stale: int = 0
    push_gaps_ms: list = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        gaps = sorted(self.push_gaps_ms)
        def pct(p):
            if not gaps:
                return None
            return gaps[int(p * (len(gaps) - 1))]
        return {
            "day": self.day,
            "total_PUSH": self.total_push,
            "board_PUSH": self.board_push,
            "valid_atomic_quote": self.valid_atomic_quote,
            "valid_ask": self.valid_ask,
            "valid_bid": self.valid_bid,
            "crossed": self.crossed,
            "locked": self.locked,
            "missing_qty": self.missing_qty,
            "stale": self.stale,
            "median_PUSH_interval_ms": pct(0.5),
            "p90_PUSH_interval_ms": pct(0.9),
            "valid_quote_rate": round(self.valid_atomic_quote / max(1, self.board_push), 4),
            "crossed_rate": round(self.crossed / max(1, self.board_push), 4),
        }


class ExecutionGradeObserver:
    """Capture-only observer. Does not submit/cancel/live order. Not auto-wired."""

    def __init__(self, out_dir: Optional[Path] = None):
        self.out_dir = Path(out_dir or DEFAULT_OUT)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._last_recv: dict[str, datetime] = {}
        self.quality: dict[str, DayQuality] = {}
        self.submit = 0
        self.cancel = 0
        self.live_order = 0

    def on_push(self, envelope: dict[str, Any], *, day: str) -> Optional[dict[str, Any]]:
        """Process one capture envelope; append research JSONL. No trading side effects."""
        q = quote_from_record(envelope, day=day, source_file="live", source_row=0)
        dq = self.quality.setdefault(day, DayQuality(day=day))
        dq.total_push += 1
        if q is None:
            return None
        dq.board_push += 1
        sym = q.symbol
        prev = self._last_recv.get(sym)
        if prev is not None:
            dq.push_gaps_ms.append((q.received_at - prev).total_seconds() * 1000.0)
        self._last_recv[sym] = q.received_at
        if q.quote_valid:
            dq.valid_atomic_quote += 1
            dq.valid_ask += 1
            dq.valid_bid += 1
        if q.crossed:
            dq.crossed += 1
        if q.locked:
            dq.locked += 1
        if q.bid_qty is None or q.ask_qty is None:
            dq.missing_qty += 1
        if q.quote_invalid_reason == "stale_price_time":
            dq.stale += 1

        op = envelope.get("original_payload") or {}
        row = {
            "schema": CAPTURE_SCHEMA_VERSION,
            "day": day,
            "symbol": sym,
            "sequence": q.sequence,
            "received_at": q.received_at.isoformat(),
            "exchange_time": q.exchange_time.isoformat() if q.exchange_time else None,
            "payload_hash": payload_hash(op, sym, q.received_at.isoformat()),
            "current_price": q.current_price,
            "best_bid": q.best_bid,
            "bid_qty": q.bid_qty,
            "best_ask": q.best_ask,
            "ask_qty": q.ask_qty,
            "depth_bids": q.depth_bids,
            "depth_asks": q.depth_asks,
            "quote_valid": q.quote_valid,
            "quote_invalid_reason": q.quote_invalid_reason,
            "submit": 0,
            "cancel": 0,
            "live_order": 0,
        }
        path = self.out_dir / day / "execution_grade_quotes.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def day_quality_rows(self) -> list[dict[str, Any]]:
        return [q.summary() for q in self.quality.values()]


def prospective_spec_dict() -> dict[str, Any]:
    spec = ProspectiveCaptureSpec()
    return {
        **asdict(spec),
        "implementation": "research.execution_grade_confirmation.prospective.ExecutionGradeObserver",
        "wiring": "NOT_WIRED_TO_MAINLINE",
        "verdict": "PROSPECTIVE_CAPTURE_READY",
        "note": "Observer saves Buy1/Sell1 atomic quotes; does not alter Paper ENTRY/EXIT or notifications.",
    }
