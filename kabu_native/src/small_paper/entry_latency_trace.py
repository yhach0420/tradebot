"""
Phase613: entry pipeline latency trace (measurement only).
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

PASS_SAMPLE_RATE = 0.002  # 0.2% of non-stale evals


def entry_latency_trace_enabled(config: Any) -> bool:
    if bool(getattr(config, "entry_latency_trace_enabled", False)):
        return True
    val = os.environ.get("ENTRY_LATENCY_TRACE_ENABLED", "").strip().lower()
    return val in ("1", "true", "yes", "on")


@dataclass
class _Marks:
    t0_push_received_at: str = ""
    t0_mono: float = 0.0
    t1_payload_parsed_at: str = ""
    t1_mono: float = 0.0
    t2_scan_enqueue_at: str = ""
    t2_mono: float = 0.0
    t3_freshness_check_at: str = ""
    t3_mono: float = 0.0
    t4_pbv2_eval_start_at: str = ""
    t4_mono: float = 0.0
    t5_pbv2_eval_end_at: str = ""
    t5_mono: float = 0.0
    t6_decision_recorded_at: str = ""
    t6_mono: float = 0.0


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="milliseconds")


def _ms(a_mono: float, b_mono: float) -> Optional[float]:
    if a_mono <= 0 or b_mono <= 0:
        return None
    return round((b_mono - a_mono) * 1000.0, 3)


def _parse_ts(val: Any) -> Optional[datetime]:
    if val is None or str(val).strip() == "":
        return None
    from storage.intraday_recorder import parse_kabu_time

    return parse_kabu_time(val, fallback=datetime.now(JST))


def _age_sec(at: datetime, ts_raw: Any) -> Optional[float]:
    tick = _parse_ts(ts_raw)
    if tick is None:
        return None
    return max(0.0, (at - tick).total_seconds())


class EntryLatencyTraceSession:
    """Per-session latency trace writer (jsonl)."""

    def __init__(
        self,
        output_dir: Path,
        *,
        max_price_age_sec: float = 3.0,
        sample_pass: bool = True,
    ) -> None:
        self.output_dir = output_dir
        self.max_price_age_sec = float(max_price_age_sec)
        self.sample_pass = sample_pass
        self._path = output_dir / "entry_latency_trace.jsonl"
        self._marks = _Marks()
        self._symbol = ""
        self._payload_snapshot: dict[str, Any] = {}

    def begin_push(
        self,
        *,
        symbol: str,
        payload: Mapping[str, Any],
        t0_push_received_at: Optional[str] = None,
        t0_mono: Optional[float] = None,
    ) -> None:
        import time

        self._symbol = symbol
        self._payload_snapshot = {
            "CurrentPriceTime": payload.get("CurrentPriceTime"),
            "BidTime": payload.get("BidTime"),
            "AskTime": payload.get("AskTime"),
            "CurrentPrice": payload.get("CurrentPrice"),
            "CalcPrice": payload.get("CalcPrice"),
        }
        self._marks = _Marks()
        self._marks.t0_push_received_at = t0_push_received_at or _now_iso()
        self._marks.t0_mono = float(t0_mono if t0_mono is not None else time.monotonic())

    def mark_scan_enqueue(self) -> None:
        import time

        self._marks.t2_scan_enqueue_at = _now_iso()
        self._marks.t2_mono = time.monotonic()

    def mark_payload_parsed(self) -> None:
        import time

        self._marks.t1_payload_parsed_at = _now_iso()
        self._marks.t1_mono = time.monotonic()

    def mark_freshness_check(self) -> None:
        import time

        self._marks.t3_freshness_check_at = _now_iso()
        self._marks.t3_mono = time.monotonic()

    def mark_pbv2_start(self) -> None:
        import time

        self._marks.t4_pbv2_eval_start_at = _now_iso()
        self._marks.t4_mono = time.monotonic()

    def mark_pbv2_end(self) -> None:
        import time

        self._marks.t5_pbv2_eval_end_at = _now_iso()
        self._marks.t5_mono = time.monotonic()

    def finish(
        self,
        *,
        stale_reason: Optional[str],
        gate_reason: str,
        entry_score_v2: Any = None,
    ) -> None:
        import time

        self._marks.t6_decision_recorded_at = _now_iso()
        self._marks.t6_mono = time.monotonic()
        is_stale = stale_reason == "data_stale_price" or gate_reason == "data_stale_price"
        if not is_stale and self.sample_pass and random.random() > PASS_SAMPLE_RATE:
            return
        row = self._build_row(stale_reason=stale_reason, gate_reason=gate_reason, entry_score_v2=entry_score_v2)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _build_row(
        self,
        *,
        stale_reason: Optional[str],
        gate_reason: str,
        entry_score_v2: Any,
    ) -> dict[str, Any]:
        m = self._marks
        pl = self._payload_snapshot
        t0_dt = _parse_ts(m.t0_push_received_at)
        t3_dt = _parse_ts(m.t3_freshness_check_at) or t0_dt
        d_feed = _age_sec(t0_dt, pl.get("CurrentPriceTime")) if t0_dt else None
        d_price_fresh = _age_sec(t3_dt, pl.get("CurrentPriceTime")) if t3_dt else None
        bid = pl.get("BidTime")
        ask = pl.get("AskTime")
        board_ts = bid
        if bid and ask:
            bt, at = _parse_ts(bid), _parse_ts(ask)
            if bt and at:
                board_ts = bid if bt >= at else ask
        d_board_fresh = _age_sec(t3_dt, board_ts) if t3_dt else None
        classification = classify_stale(
            current_price_time=pl.get("CurrentPriceTime"),
            d_feed_price_age_sec=d_feed,
            d_price_age_at_freshness_sec=d_price_fresh,
            max_price_age_sec=self.max_price_age_sec,
            gate_reason=gate_reason or stale_reason or "",
        )
        return {
            "symbol": self._symbol,
            "t0_push_received_at": m.t0_push_received_at,
            "t1_payload_parsed_at": m.t1_payload_parsed_at,
            "t2_scan_enqueue_at": m.t2_scan_enqueue_at,
            "t3_freshness_check_at": m.t3_freshness_check_at,
            "t4_pbv2_eval_start_at": m.t4_pbv2_eval_start_at,
            "t5_pbv2_eval_end_at": m.t5_pbv2_eval_end_at,
            "t6_decision_recorded_at": m.t6_decision_recorded_at,
            "CurrentPriceTime": pl.get("CurrentPriceTime"),
            "BidTime": pl.get("BidTime"),
            "AskTime": pl.get("AskTime"),
            "d_feed_price_age_at_push_sec": d_feed,
            "d_system_to_freshness_ms": _ms(m.t0_mono, m.t3_mono),
            "d_payload_parse_ms": _ms(m.t0_mono, m.t1_mono),
            "d_enqueue_delay_ms": _ms(m.t1_mono, m.t2_mono),
            "d_freshness_delay_ms": _ms(m.t2_mono, m.t3_mono),
            "d_pbv2_eval_ms": _ms(m.t4_mono, m.t5_mono),
            "d_record_delay_ms": _ms(m.t5_mono, m.t6_mono),
            "d_total_pipeline_ms": _ms(m.t0_mono, m.t6_mono),
            "d_price_age_at_freshness_sec": d_price_fresh,
            "d_board_age_at_freshness_sec": d_board_fresh,
            "stale_classification": classification,
            "gate_reject_reason": gate_reason,
            "entry_score_v2": entry_score_v2,
            "source": "live_trace",
        }


def classify_stale(
    *,
    current_price_time: Any,
    d_feed_price_age_sec: Optional[float],
    d_price_age_at_freshness_sec: Optional[float],
    max_price_age_sec: float,
    gate_reason: str,
) -> str:
    if gate_reason != "data_stale_price":
        return ""
    if current_price_time is None or str(current_price_time).strip() == "":
        return "C_missing_current_price_time"
    if d_feed_price_age_sec is None or d_price_age_at_freshness_sec is None:
        tick = _parse_ts(current_price_time)
        if tick is None:
            return "D_parse_or_timezone_error"
        if tick > datetime.now(JST):
            return "D_parse_or_timezone_error"
        return "E_other"
    if d_feed_price_age_sec > max_price_age_sec:
        return "A_feed_already_stale"
    if d_feed_price_age_sec <= max_price_age_sec and d_price_age_at_freshness_sec > max_price_age_sec:
        return "B_system_latency_stale"
    return "E_other"
