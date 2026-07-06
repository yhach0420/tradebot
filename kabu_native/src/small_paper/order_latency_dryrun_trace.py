"""
Phase644: Live order latency dry-run trace (measurement only, no sendorder).

Traces t0 (CurrentPriceTime) through sendorder payload dry-run (t9/t10).
"""

from __future__ import annotations

import json
import os
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.exposure_gate import REJECT_MAX_CONCURRENT
from small_paper.reject_reasons import REJECT_MAX_ENTRIES_PER_SCAN

JST = ZoneInfo("Asia/Tokyo")

TRACE_FILENAME = "order_latency_dryrun_trace.jsonl"


def order_latency_dryrun_enabled(config: Any) -> bool:
    if bool(getattr(config, "live_trading_enabled", False)):
        return False
    if bool(getattr(config, "order_enabled", False)):
        return False
    val = os.environ.get("ORDER_LATENCY_DRYRUN_TRACE_ENABLED", "").strip().lower()
    if val in ("0", "false", "no", "off"):
        return False
    if val in ("1", "true", "yes", "on"):
        return True
    return bool(getattr(config, "order_latency_dryrun_trace_enabled", True))


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="milliseconds")


def _parse_ts(val: Any) -> Optional[datetime]:
    if val is None or str(val).strip() == "":
        return None
    from storage.intraday_recorder import parse_kabu_time

    return parse_kabu_time(val, fallback=datetime.now(JST))


def _sec_between(a: Any, b: Any) -> Optional[float]:
    ta, tb = _parse_ts(a), _parse_ts(b)
    if ta is None or tb is None:
        return None
    return round((tb - ta).total_seconds(), 6)


def _ms_between(a: Any, b: Any) -> Optional[float]:
    sec = _sec_between(a, b)
    if sec is None:
        return None
    return round(sec * 1000.0, 3)


def _ms_mono(start: float, end: float) -> Optional[float]:
    if start <= 0 or end <= 0 or end < start:
        return None
    return round((end - start) * 1000.0, 3)


def _session_kind_from_ts(ts: Any) -> str:
    dt = _parse_ts(ts)
    if dt is None:
        return "UNKNOWN"
    return "AM" if dt.hour < 12 or (dt.hour == 12 and dt.minute < 25) else "PM"


def _time_bucket_from_ts(ts: Any) -> str:
    dt = _parse_ts(ts)
    if dt is None:
        return "unknown"
    return f"{dt.hour:02d}:00"


def compute_latency_stats(values: Sequence[float]) -> dict[str, Any]:
    xs = [float(v) for v in values if v is not None]
    if not xs:
        return {
            "count": 0,
            "p50": None,
            "p90": None,
            "p95": None,
            "p99": None,
            "max": None,
            "mean": None,
        }
    return {
        "count": len(xs),
        "p50": OrderLatencyDryRunSession._percentile(xs, 0.50),
        "p90": OrderLatencyDryRunSession._percentile(xs, 0.90),
        "p95": OrderLatencyDryRunSession._percentile(xs, 0.95),
        "p99": OrderLatencyDryRunSession._percentile(xs, 0.99),
        "max": round(max(xs), 6),
        "mean": round(statistics.fmean(xs), 6),
    }


def evaluate_latency_thresholds(stats: Mapping[str, Any]) -> dict[str, Any]:
    """Phase644b anomaly thresholds on dry-run reached samples."""
    push = stats.get("push_to_order_sec") or {}
    price = stats.get("price_to_order_sec") or {}
    queue = stats.get("queue_latency_ms") or {}
    decision = stats.get("decision_latency_ms") or {}

    alerts: list[str] = []
    push_p95 = push.get("p95")
    push_p99 = push.get("p99")
    price_p95 = price.get("p95")
    queue_p95 = queue.get("p95")
    decision_p95 = decision.get("p95")

    push_p95_pass = push_p95 is not None and push_p95 <= 1.5
    push_p99_ok = push_p99 is None or push_p99 <= 3.0
    price_p95_pass = price_p95 is not None and price_p95 <= 2.0

    if push_p95 is not None and push_p95 > 1.5:
        alerts.append(f"push_to_order p95={push_p95:.3f}s > 1.5s")
    if push_p99 is not None and push_p99 > 3.0:
        alerts.append(f"push_to_order p99={push_p99:.3f}s > 3.0s")
    if price_p95 is not None and price_p95 > 2.0:
        alerts.append(f"price_to_order p95={price_p95:.3f}s > 2.0s")
    if queue_p95 is not None and queue_p95 > 500:
        alerts.append(f"queue_latency p95={queue_p95:.1f}ms > 500ms")
    if decision_p95 is not None and decision_p95 > 500:
        alerts.append(f"decision_latency p95={decision_p95:.1f}ms > 500ms")

    return {
        "push_to_order_p95_pass": push_p95_pass,
        "push_to_order_p99_warning": not push_p99_ok,
        "price_to_order_p95_pass": price_p95_pass,
        "queue_latency_p95_warning": queue_p95 is not None and queue_p95 > 500,
        "decision_latency_p95_warning": decision_p95 is not None and decision_p95 > 500,
        "alerts": alerts,
        "acceptable_for_live_orders": bool(
            push_p95_pass and price_p95_pass and push_p99_ok and not alerts
        ),
    }


def detect_bottleneck(stage_stats: Mapping[str, Mapping[str, Any]]) -> str:
    best = ("unknown", 0.0)
    for name, bundle in stage_stats.items():
        p95 = bundle.get("p95")
        if p95 is not None and float(p95) > best[1]:
            best = (name, float(p95))
    return best[0]


def _sample_kind(*, accepted: bool, entry_route: str, gate_reason: str) -> str:
    if gate_reason == REJECT_MAX_CONCURRENT:
        return "cap_blocked"
    if gate_reason == REJECT_MAX_ENTRIES_PER_SCAN:
        return "max_scan_blocked"
    if not accepted:
        return "other_reject"
    if entry_route == "or":
        return "or_accepted"
    if entry_route == "pbv2":
        return "pbv2_accepted"
    return "accepted_other"


@dataclass
class _ActiveTrace:
    symbol: str
    message_index: int = 0
    current_price_time: str = ""
    t0_current_price_time: str = ""
    t1_push_received_at: str = ""
    t2_process_start_mono: float = 0.0
    t3_enrich_end_mono: float = 0.0
    t4_freshness_end_mono: float = 0.0
    t5_decision_end_mono: float = 0.0
    t5_decision_at: str = ""
    t6_queue_enqueue_mono: float = 0.0
    t7_flush_start_mono: float = 0.0
    t8_order_build_end_mono: float = 0.0
    t9_dryrun_start_at: str = ""
    t9_dryrun_start_mono: float = 0.0
    t10_dryrun_end_at: str = ""
    t10_dryrun_end_mono: float = 0.0
    entry_route: str = ""
    gate_reason: str = ""
    scan_id: str = ""
    entry_signal_mono: float = 0.0
    accepted: bool = False


@dataclass
class OrderLatencyDryRunSession:
    output_dir: Path
    samples: list[dict[str, Any]] = field(default_factory=list)
    _active: Optional[_ActiveTrace] = None
    _pending: dict[str, _ActiveTrace] = field(default_factory=dict)
    _wiring: dict[str, _ActiveTrace] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return self.output_dir / TRACE_FILENAME

    def begin_push(
        self,
        *,
        symbol: str,
        payload: Mapping[str, Any],
        message_index: int,
        t1_push_received_at: Optional[str],
        t2_mono: Optional[float],
    ) -> None:
        cpt = str(payload.get("CurrentPriceTime") or "")
        t0_dt = _parse_ts(cpt)
        self._active = _ActiveTrace(
            symbol=symbol,
            message_index=message_index,
            current_price_time=cpt,
            t0_current_price_time=t0_dt.isoformat(timespec="milliseconds") if t0_dt else cpt,
            t1_push_received_at=t1_push_received_at or _now_iso(),
            t2_process_start_mono=float(t2_mono if t2_mono is not None else time.monotonic()),
        )

    def mark_enrich_end(self) -> None:
        if self._active is not None:
            self._active.t3_enrich_end_mono = time.monotonic()

    def mark_freshness_end(self) -> None:
        if self._active is not None:
            self._active.t4_freshness_end_mono = time.monotonic()

    def mark_decision_end(
        self,
        *,
        accepted: bool,
        entry_route: str,
        gate_reason: str,
    ) -> None:
        if self._active is None:
            return
        self._active.t5_decision_end_mono = time.monotonic()
        self._active.t5_decision_at = _now_iso()
        self._active.accepted = accepted
        self._active.entry_route = entry_route
        self._active.gate_reason = gate_reason

    def mark_queue_enqueue(self, *, entry_signal_mono: float, scan_id: str = "") -> None:
        if self._active is None:
            return
        self._active.t6_queue_enqueue_mono = time.monotonic()
        self._active.entry_signal_mono = entry_signal_mono
        self._active.scan_id = scan_id
        key = self._pending_key(self._active.symbol, entry_signal_mono)
        self._pending[key] = self._active

    def mark_flush_start(
        self,
        *,
        entry_signal_mono: float,
        symbol: str,
        flush_start_mono: Optional[float] = None,
    ) -> Optional[_ActiveTrace]:
        key = self._pending_key(symbol, entry_signal_mono)
        tr = self._pending.pop(key, None) or self._active
        if tr is None:
            return None
        tr.t7_flush_start_mono = float(flush_start_mono if flush_start_mono is not None else time.monotonic())
        self._wiring[symbol] = tr
        return tr

    def finish_max_scan_blocked(self, *, symbol: str, entry_signal_mono: float) -> None:
        key = self._pending_key(symbol, entry_signal_mono)
        tr = self._pending.pop(key, None)
        if tr is None:
            return
        tr.accepted = False
        tr.gate_reason = REJECT_MAX_ENTRIES_PER_SCAN
        if tr.t5_decision_end_mono <= 0:
            tr.t5_decision_end_mono = time.monotonic()
        if not tr.t5_decision_at:
            tr.t5_decision_at = _now_iso()
        self._emit(tr)
        if self._active is tr:
            self._active = None

    def mark_direct_execute(self, *, entry_signal_mono: float) -> None:
        if self._active is None:
            return
        self._active.entry_signal_mono = entry_signal_mono
        if self._active.t7_flush_start_mono <= 0:
            self._active.t7_flush_start_mono = time.monotonic()
        self._wiring[self._active.symbol] = self._active

    def mark_order_build_end(self, *, symbol: str) -> None:
        tr = self._wiring.get(symbol) or self._active
        if tr is not None:
            tr.t8_order_build_end_mono = time.monotonic()

    def mark_dryrun_start(self, *, symbol: str) -> None:
        tr = self._wiring.get(symbol) or self._active
        if tr is not None:
            tr.t9_dryrun_start_at = _now_iso()
            tr.t9_dryrun_start_mono = time.monotonic()

    def mark_dryrun_end(self, *, symbol: str) -> None:
        tr = self._wiring.get(symbol) or self._active
        if tr is not None:
            tr.t10_dryrun_end_at = _now_iso()
            tr.t10_dryrun_end_mono = time.monotonic()

    def finish_reject(
        self,
        *,
        gate_reason: str,
        entry_route: str = "reject",
    ) -> None:
        if self._active is None:
            return
        tr = self._active
        if tr.t5_decision_end_mono <= 0:
            tr.t5_decision_end_mono = time.monotonic()
        if not tr.t5_decision_at:
            tr.t5_decision_at = _now_iso()
        tr.accepted = False
        tr.gate_reason = gate_reason
        tr.entry_route = entry_route
        self._emit(tr)
        self._active = None

    def finish_wiring(self, *, symbol: str) -> None:
        tr = self._wiring.pop(symbol, None) or self._active
        if tr is None:
            return
        if tr.t5_decision_end_mono <= 0:
            tr.t5_decision_end_mono = time.monotonic()
        if not tr.t5_decision_at:
            tr.t5_decision_at = _now_iso()
        self._emit(tr)
        if self._active is tr:
            self._active = None

    def _pending_key(self, symbol: str, entry_signal_mono: float) -> str:
        return f"{symbol}:{entry_signal_mono:.9f}"

    def _emit(self, tr: _ActiveTrace) -> None:
        kind = _sample_kind(
            accepted=tr.accepted,
            entry_route=tr.entry_route,
            gate_reason=tr.gate_reason,
        )
        t9_ref = tr.t9_dryrun_start_at or tr.t10_dryrun_end_at
        t5_ref = tr.t5_decision_at or t9_ref
        push_to_decision_ms = _ms_between(tr.t1_push_received_at, t5_ref)
        decision_to_order_ms = _ms_between(t5_ref, t9_ref) if t9_ref else None
        row = {
            "symbol": tr.symbol,
            "message_index": tr.message_index,
            "sample_kind": kind,
            "entry_route": tr.entry_route,
            "gate_reason": tr.gate_reason,
            "scan_id": tr.scan_id,
            "session_kind": _session_kind_from_ts(tr.t1_push_received_at),
            "time_bucket": _time_bucket_from_ts(tr.t1_push_received_at),
            "CurrentPriceTime": tr.current_price_time,
            "t0_current_price_time": tr.t0_current_price_time,
            "t1_push_received_at": tr.t1_push_received_at,
            "t5_decision_at": tr.t5_decision_at,
            "t9_dryrun_start_at": tr.t9_dryrun_start_at,
            "t10_dryrun_end_at": tr.t10_dryrun_end_at,
            "price_to_order_sec": _sec_between(tr.t0_current_price_time, t9_ref),
            "push_to_order_sec": _sec_between(tr.t1_push_received_at, t9_ref),
            "push_to_decision_ms": push_to_decision_ms,
            "decision_to_order_ms": decision_to_order_ms,
            "decision_latency_ms": _ms_mono(tr.t2_process_start_mono, tr.t5_decision_end_mono),
            "payload_to_enrich_ms": _ms_mono(tr.t2_process_start_mono, tr.t3_enrich_end_mono),
            "freshness_latency_ms": _ms_mono(tr.t3_enrich_end_mono, tr.t4_freshness_end_mono),
            "pbv2_or_latency_ms": _ms_mono(tr.t4_freshness_end_mono, tr.t5_decision_end_mono),
            "queue_latency_ms": _ms_mono(tr.t6_queue_enqueue_mono, tr.t7_flush_start_mono),
            "order_build_latency_ms": _ms_mono(tr.t7_flush_start_mono, tr.t8_order_build_end_mono),
            "order_build_ms": _ms_mono(tr.t7_flush_start_mono, tr.t8_order_build_end_mono),
            "dryrun_ms": _ms_mono(tr.t9_dryrun_start_mono, tr.t10_dryrun_end_mono),
            "dryrun_latency_ms": _ms_mono(tr.t9_dryrun_start_mono, tr.t10_dryrun_end_mono),
            "reached_dryrun": bool(tr.t9_dryrun_start_at),
        }
        self.samples.append(row)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    @staticmethod
    def _percentile(values: Sequence[float], pct: float) -> Optional[float]:
        if not values:
            return None
        xs = sorted(values)
        if len(xs) == 1:
            return round(xs[0], 6)
        k = (len(xs) - 1) * pct
        f = int(k)
        c = min(f + 1, len(xs) - 1)
        if f == c:
            return round(xs[f], 6)
        return round(xs[f] + (xs[c] - xs[f]) * (k - f), 6)

    def summary_stats(self) -> dict[str, Any]:
        dryrun = [r for r in self.samples if r.get("reached_dryrun")]
        push_vals = [float(r["push_to_order_sec"]) for r in dryrun if r.get("push_to_order_sec") is not None]
        price_vals = [float(r["price_to_order_sec"]) for r in dryrun if r.get("price_to_order_sec") is not None]
        push_stats = compute_latency_stats(push_vals)
        price_stats = compute_latency_stats(price_vals)
        stage_stats = {
            "decision_latency_ms": compute_latency_stats(
                [float(r["decision_latency_ms"]) for r in self.samples if r.get("decision_latency_ms") is not None]
            ),
            "queue_latency_ms": compute_latency_stats(
                [float(r["queue_latency_ms"]) for r in self.samples if r.get("queue_latency_ms") is not None]
            ),
            "order_build_ms": compute_latency_stats(
                [float(r["order_build_ms"]) for r in self.samples if r.get("order_build_ms") is not None]
            ),
            "pbv2_or_latency_ms": compute_latency_stats(
                [float(r["pbv2_or_latency_ms"]) for r in self.samples if r.get("pbv2_or_latency_ms") is not None]
            ),
            "payload_to_enrich_ms": compute_latency_stats(
                [float(r["payload_to_enrich_ms"]) for r in self.samples if r.get("payload_to_enrich_ms") is not None]
            ),
        }
        bundle = {
            "sample_count": len(self.samples),
            "dryrun_sample_count": len(dryrun),
            "push_to_order_sec": push_stats,
            "price_to_order_sec": price_stats,
            "stage_stats": stage_stats,
        }
        bundle["top_bottleneck"] = detect_bottleneck(stage_stats)
        bundle.update(evaluate_latency_thresholds(bundle))
        return bundle


def order_latency_dryrun_summary_fields(session: Optional[OrderLatencyDryRunSession]) -> dict[str, Any]:
    if session is None:
        return {"order_latency_dryrun_trace_enabled": False}
    stats = session.summary_stats()
    push = stats.get("push_to_order_sec") or {}
    price = stats.get("price_to_order_sec") or {}
    return {
        "order_latency_dryrun_trace_enabled": True,
        "order_latency_dryrun_sample_count": stats.get("sample_count"),
        "order_latency_dryrun_reached_count": stats.get("dryrun_sample_count"),
        "order_latency_push_to_order_p50_sec": push.get("p50"),
        "order_latency_push_to_order_p90_sec": push.get("p90"),
        "order_latency_push_to_order_p95_sec": push.get("p95"),
        "order_latency_push_to_order_p99_sec": push.get("p99"),
        "order_latency_push_to_order_max_sec": push.get("max"),
        "order_latency_price_to_order_p50_sec": price.get("p50"),
        "order_latency_price_to_order_p90_sec": price.get("p90"),
        "order_latency_price_to_order_p95_sec": price.get("p95"),
        "order_latency_price_to_order_p99_sec": price.get("p99"),
        "order_latency_price_to_order_max_sec": price.get("max"),
        "order_latency_top_bottleneck": stats.get("top_bottleneck"),
        "order_latency_alert": "; ".join(stats.get("alerts") or []) or "none",
    }


def format_order_latency_dryrun_lines(summary: Mapping[str, Any]) -> list[str]:
    if not summary.get("order_latency_dryrun_trace_enabled"):
        return []
    lines = ["[Order Latency DryRun]"]
    samples = summary.get("order_latency_dryrun_sample_count")
    if samples is not None:
        lines.append(f"samples: {int(samples)}")
    p50 = summary.get("order_latency_push_to_order_p50_sec")
    p95 = summary.get("order_latency_push_to_order_p95_sec")
    p99 = summary.get("order_latency_push_to_order_p99_sec")
    mx = summary.get("order_latency_push_to_order_max_sec")
    if p50 is not None:
        lines.append(
            f"push→order p50/p95/p99/max: {float(p50):.3f}/{float(p95 or 0):.3f}/{float(p99 or 0):.3f}/{float(mx or 0):.3f}s"
        )
    pp50 = summary.get("order_latency_price_to_order_p50_sec")
    pp95 = summary.get("order_latency_price_to_order_p95_sec")
    pp99 = summary.get("order_latency_price_to_order_p99_sec")
    pmx = summary.get("order_latency_price_to_order_max_sec")
    if pp50 is not None:
        lines.append(
            f"price→order p50/p95/p99/max: {float(pp50):.3f}/{float(pp95 or 0):.3f}/{float(pp99 or 0):.3f}/{float(pmx or 0):.3f}s"
        )
    bottleneck = summary.get("order_latency_top_bottleneck")
    if bottleneck:
        lines.append(f"top bottleneck: {bottleneck}")
    alert = summary.get("order_latency_alert") or "none"
    lines.append(f"alert: {alert}")
    return lines


def aggregate_samples(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build mandatory-answer style summary from trace rows."""

    def _vals(key: str, *, kind: Optional[str] = None, dryrun_only: bool = False) -> list[float]:
        out: list[float] = []
        for r in samples:
            if kind and str(r.get("sample_kind")) != kind:
                continue
            if dryrun_only and not r.get("reached_dryrun"):
                continue
            v = r.get(key)
            if v is None:
                continue
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                continue
        return out

    push_all = _vals("push_to_order_sec", dryrun_only=True)
    price_all = _vals("price_to_order_sec", dryrun_only=True)

    def _stage_means(prefix: str) -> dict[str, Optional[float]]:
        keys = {
            "payload_processing_ms": "payload_to_enrich_ms",
            "decision_ms": "decision_latency_ms",
            "freshness_ms": "freshness_latency_ms",
            "pbv2_or_ms": "pbv2_or_latency_ms",
            "queue_ms": "queue_latency_ms",
            "order_build_ms": "order_build_latency_ms",
        }
        out: dict[str, Optional[float]] = {}
        for name, k in keys.items():
            xs = _vals(k)
            out[name] = round(statistics.fmean(xs), 3) if xs else None
        return out

    stages = _stage_means("")
    dominant = max(stages.items(), key=lambda kv: kv[1] or 0.0)[0] if any(stages.values()) else "unknown"

    pbv2_push = _vals("push_to_order_sec", kind="pbv2_accepted", dryrun_only=True)
    or_push = _vals("push_to_order_sec", kind="or_accepted", dryrun_only=True)
    cap_push = _vals("decision_latency_ms", kind="cap_blocked")
    acc_push = _vals("decision_latency_ms", kind="pbv2_accepted") + _vals(
        "decision_latency_ms", kind="or_accepted"
    )

    p50 = lambda xs: OrderLatencyDryRunSession._percentile(xs, 0.5)
    p95 = lambda xs: OrderLatencyDryRunSession._percentile(xs, 0.95)

    acceptable = (p95(push_all) or 999) <= 1.5 and (p95(price_all) or 999) <= 2.0

    return {
        "1_push_to_order_p50_sec": p50(push_all),
        "1_push_to_order_p95_sec": p95(push_all),
        "1_push_to_order_max_sec": round(max(push_all), 6) if push_all else None,
        "2_price_to_order_p50_sec": p50(price_all),
        "2_price_to_order_p95_sec": p95(price_all),
        "2_price_to_order_max_sec": round(max(price_all), 6) if price_all else None,
        "3_dominant_delay_stage": dominant,
        "3_stage_means_ms": stages,
        "4_pbv2_or_delay_diff_sec": (
            round(statistics.fmean(pbv2_push) - statistics.fmean(or_push), 6)
            if pbv2_push and or_push
            else None
        ),
        "4_pbv2_push_to_order_mean_sec": round(statistics.fmean(pbv2_push), 6) if pbv2_push else None,
        "4_or_push_to_order_mean_sec": round(statistics.fmean(or_push), 6) if or_push else None,
        "5_cap_blocked_slower_than_accept_ms": (
            round(statistics.fmean(cap_push) - statistics.fmean(acc_push), 3)
            if cap_push and acc_push
            else None
        ),
        "6_acceptable_for_live_orders": acceptable,
        "7_improvement_recommendations": _recommendations(stages, push_all, price_all),
        "sample_count": len(samples),
    }


def _recommendations(
    stages: Mapping[str, Optional[float]],
    push_vals: Sequence[float],
    price_vals: Sequence[float],
) -> list[str]:
    recs: list[str] = []
    if (stages.get("queue_ms") or 0) > 50:
        recs.append("Reduce entry_scan batch window or flush accepted queue sooner")
    if (stages.get("order_build_ms") or 0) > 20:
        recs.append("Cache sendorder payload templates / precompute limit price fields")
    if (stages.get("pbv2_or_ms") or 0) > 100:
        recs.append("Profile PBv2/OR gate evaluation hot path")
    if (stages.get("payload_processing_ms") or 0) > 80:
        recs.append("Optimize enrich_payload / feature bridge update")
    if push_vals and max(push_vals) > 1.5:
        recs.append("Investigate tail latency outliers (>1.5s push→order)")
    if price_vals and max(price_vals) > 2.0:
        recs.append("CurrentPriceTime age at order build may exceed tolerance — tighten pipeline")
    if not recs:
        recs.append("Latency within targets; continue dry-run monitoring on live sessions")
    return recs
