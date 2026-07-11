"""Phase687W9 — Paper interference audit (Forward only; not strategy input).

First session: structure + immediate FAIL on drop/disconnect/registration conflict only.
No adopt/stop statistical judgment on session 1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

NO_OBSERVED_INTERFERENCE = "NO_OBSERVED_INTERFERENCE"
INTERFERENCE_DATA_INSUFFICIENT = "INTERFERENCE_DATA_INSUFFICIENT"
EVENT_DIVERGENCE = "EVENT_DIVERGENCE"
PAPER_LATENCY_DEGRADED = "PAPER_LATENCY_DEGRADED"
REGISTRATION_CONFLICT = "REGISTRATION_CONFLICT"
IMMEDIATE_FAIL_DROP = "CAPTURE_GAP_OR_DROP"
IMMEDIATE_FAIL_DISCONNECT = "CAPTURE_DISCONNECT"
IMMEDIATE_FAIL_REGISTRATION = "REGISTRATION_CONFLICT"


@dataclass
class InterferenceInputs:
    paper_push_event_count: Optional[int] = None
    capture_event_count: Optional[int] = None
    common_symbol_count: Optional[int] = None
    timestamp_overlap: Optional[bool] = None
    event_divergence: Optional[bool] = None
    paper_push_to_order_p50_ms: Optional[float] = None
    paper_push_to_order_p95_ms: Optional[float] = None
    paper_latency_baseline_p95_ms: Optional[float] = None
    sidecar_cpu_avg: Optional[float] = None
    sidecar_cpu_max: Optional[float] = None
    sidecar_memory_mb: Optional[float] = None
    sidecar_disk_mb_s: Optional[float] = None
    registration_changes: int = 0
    registration_conflict: bool = False
    reconnect_events: int = 0
    dropped_event_count: int = 0
    disconnect_count: int = 0
    session_index: int = 1  # 1 = first Forward session


@dataclass
class InterferenceResult:
    verdict: str
    immediate_fail: bool = False
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "immediate_fail": self.immediate_fail,
            "reasons": list(self.reasons),
            "metrics": dict(self.metrics),
            "strategy_use_forbidden": True,
            "first_session_no_statistical_adopt_stop": True,
        }


def evaluate_interference(inp: InterferenceInputs) -> InterferenceResult:
    """Audit only — never feeds ENTRY/EXIT/universe rules."""
    metrics = {
        "paper_push_event_count": inp.paper_push_event_count,
        "capture_event_count": inp.capture_event_count,
        "common_symbol_count": inp.common_symbol_count,
        "timestamp_overlap": inp.timestamp_overlap,
        "event_divergence": inp.event_divergence,
        "paper_push_to_order_p50_ms": inp.paper_push_to_order_p50_ms,
        "paper_push_to_order_p95_ms": inp.paper_push_to_order_p95_ms,
        "sidecar_cpu_avg": inp.sidecar_cpu_avg,
        "sidecar_cpu_max": inp.sidecar_cpu_max,
        "sidecar_memory_mb": inp.sidecar_memory_mb,
        "sidecar_disk_mb_s": inp.sidecar_disk_mb_s,
        "registration_changes": inp.registration_changes,
        "reconnect_events": inp.reconnect_events,
        "dropped_event_count": inp.dropped_event_count,
        "disconnect_count": inp.disconnect_count,
        "session_index": inp.session_index,
    }

    # Immediate FAIL only
    if inp.dropped_event_count > 0:
        return InterferenceResult(
            verdict=IMMEDIATE_FAIL_DROP,
            immediate_fail=True,
            reasons=["dropped_event_count>0"],
            metrics=metrics,
        )
    if inp.disconnect_count > 0 and (inp.capture_event_count or 0) == 0:
        return InterferenceResult(
            verdict=IMMEDIATE_FAIL_DISCONNECT,
            immediate_fail=True,
            reasons=["disconnect_with_zero_events"],
            metrics=metrics,
        )
    if inp.registration_conflict:
        return InterferenceResult(
            verdict=REGISTRATION_CONFLICT,
            immediate_fail=True,
            reasons=["registration_conflict"],
            metrics=metrics,
        )

    # Insufficient data (typical first session / weekend)
    missing = [
        k
        for k, v in {
            "paper_push_event_count": inp.paper_push_event_count,
            "capture_event_count": inp.capture_event_count,
        }.items()
        if v is None
    ]
    if missing or inp.session_index <= 1 and inp.paper_latency_baseline_p95_ms is None:
        # First session: no statistical adopt/stop; insufficient is OK unless immediate fail
        if inp.event_divergence:
            return InterferenceResult(
                verdict=EVENT_DIVERGENCE,
                immediate_fail=False,
                reasons=["event_divergence_observed"],
                metrics=metrics,
            )
        return InterferenceResult(
            verdict=INTERFERENCE_DATA_INSUFFICIENT,
            immediate_fail=False,
            reasons=["first_session_or_missing_counts"] + missing,
            metrics=metrics,
        )

    if inp.event_divergence:
        return InterferenceResult(
            verdict=EVENT_DIVERGENCE,
            immediate_fail=False,
            reasons=["event_divergence"],
            metrics=metrics,
        )

    if (
        inp.paper_push_to_order_p95_ms is not None
        and inp.paper_latency_baseline_p95_ms is not None
        and inp.paper_push_to_order_p95_ms > inp.paper_latency_baseline_p95_ms * 1.5
    ):
        return InterferenceResult(
            verdict=PAPER_LATENCY_DEGRADED,
            immediate_fail=False,
            reasons=["p95_above_1_5x_baseline"],
            metrics=metrics,
        )

    return InterferenceResult(
        verdict=NO_OBSERVED_INTERFERENCE,
        immediate_fail=False,
        reasons=[],
        metrics=metrics,
    )


def evaluate_from_capture_summary(
    summary: Mapping[str, Any],
    *,
    paper_push_event_count: Optional[int] = None,
    registration_conflict: bool = False,
    session_index: int = 1,
) -> dict[str, Any]:
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    inp = InterferenceInputs(
        paper_push_event_count=paper_push_event_count,
        capture_event_count=int(summary.get("total_events") or 0),
        dropped_event_count=int(summary.get("dropped_event_count") or 0),
        disconnect_count=int(summary.get("disconnect_count") or 0),
        reconnect_events=int(summary.get("reconnect_count") or 0),
        registration_conflict=registration_conflict,
        sidecar_cpu_avg=metrics.get("cpu_avg"),
        sidecar_cpu_max=metrics.get("cpu_max"),
        sidecar_memory_mb=metrics.get("memory_mb"),
        sidecar_disk_mb_s=metrics.get("disk_write_mb_s"),
        session_index=session_index,
    )
    return evaluate_interference(inp).as_dict()
