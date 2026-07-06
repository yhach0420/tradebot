"""
Phase629: ENTRY pipeline stage dataclasses + DEBUG-only stage trace logger.

Structure-only refactoring support for pilot_runner._process_push_payload.
These dataclasses carry values between stages; they perform NO logic.
Behavior of the pipeline is defined entirely by the stage functions in
pilot_runner.py, which contain the original code moved verbatim.

Stage map (see docs/operations/phase629_stage_refactoring.md):
    Stage0  Payload Normalize   -> Stage0NormalizedPayload
    Stage1  Freshness           -> Stage1FreshnessResult
    Stage2  PBv2                -> Stage2PBv2Result   (GateDecision is never mutated)
    Stage3  Cluster Guard       -> Stage3ClusterDecision (classification of the
                                   cluster-guard outcome computed inside the
                                   ExposureGate chain during Stage2; the guard
                                   itself is NOT moved — moving it would change
                                   behavior, which Phase629 forbids)
    Stage4  OR Overlay          -> Stage4FinalEntryDecision
    Stage5  Entry Execute       (queue/flush/register_entry — writes via ctx)
    Stage6  Post Entry          (writer/audit/ExtensionBus/Discord — writes via ctx)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stage dataclasses (data carriers only — no logic)
# ---------------------------------------------------------------------------


@dataclass
class Stage0NormalizedPayload:
    """Stage0 output: normalized push payload + candidate trade.

    Responsibility: enrich_payload / candidate generation / price ring.
    """

    symbol: str
    msg_i: int
    payload: Mapping[str, Any]
    enriched: dict[str, Any]
    trade: dict[str, Any]
    snapshot: Any
    bucket: str
    scan_id: str
    eval_start_ts: str
    eval_start_mono: float
    t0_push_received_at: Optional[str] = None
    t0_mono: Optional[float] = None


@dataclass
class Stage1FreshnessResult:
    """Stage1 output: freshness v1/v2 evaluation (event/board/trade/tag/reject_reason).

    pre_gate_reason: "" normally; "am_pm_entry_stop" / "outside_refresh_universe"
    when the pre-freshness short-circuit branch fired (freshness is then skipped,
    exactly as before Phase629).
    short_circuit_decision: the GateDecision produced by a pre-gate block or a
    stale reject; None when the candidate proceeds to Stage2 (PBv2).
    ref_now_unbound: legacy Phase629 flag; Phase640 assigns ref_now on pre-gate
    branches so audit/reject writers no longer fail.
    """

    ref_now: Optional[datetime] = None
    ref_now_unbound: bool = False
    freshness: Any = None
    freshness_decision: Any = None
    stale_reason: Optional[str] = None
    pre_gate_reason: str = ""
    short_circuit_decision: Any = None


@dataclass
class Stage2PBv2Result:
    """Stage2 output: PBv2 GateDecision (accept/reason/score fields) + internal reason.

    The GateDecision instance is treated as immutable from here on: no stage
    mutates it (any reason remapping in _evaluate_gate_entry uses
    dataclasses.replace, producing a new instance).
    """

    decision: Any = None
    internal_reason: str = ""
    internal_gate: str = ""


# Stage3 cluster decision statuses (values mirror entry_cluster_guard constants).
CLUSTER_STAGE_PASS = "PASS"
CLUSTER_STAGE_REJECT = "REJECT"
CLUSTER_STAGE_FEATURE_INCOMPLETE = "FEATURE_INCOMPLETE"
CLUSTER_STAGE_EXCEPTION = "EXCEPTION"
CLUSTER_STAGE_NOT_EVALUATED = "NOT_EVALUATED"


@dataclass(frozen=True)
class Stage3ClusterDecision:
    """Stage3 output: cluster guard outcome classification.

    The cluster guard executes inside ExposureGate.evaluate_entry (Stage2) and
    must stay there (Phase629 forbids logic moves). Stage3 formalizes its
    outcome as a dataclass for trace/observability. Derived read-only from the
    Stage2 GateDecision + trade fields; never feeds back into any decision.
    """

    status: str = CLUSTER_STAGE_NOT_EVALUATED
    cluster_guard_status: str = ""
    cluster_id: Any = None
    new_subcluster_id: Any = None
    liquidity_burst: Any = None
    via_exception: bool = False


@dataclass
class Stage4FinalEntryDecision:
    """Stage4 output: final ENTRY decision (PBv2 / OR / Reject).

    pbv2 reason is never modified here: pbv2_internal_reason / pbv2_internal_gate
    were persisted in Stage2 (Phase627) and are only read afterwards.
    entry_route: "pbv2" | "or" | "reject" | "pre_gate_reject" | "stale_reject".
    """

    decision: Any = None
    entry_route: str = "reject"
    or_overlay_reason: str = ""
    final_reject_reason: str = ""
    stale_reason: Optional[str] = None


@dataclass
class Stage6CandidateRecord:
    """Stage6 (candidate recording) output consumed by Stage5/Stage6-reject."""

    score5_ord: Optional[int] = None
    eval_end_ts: str = ""
    eval_latency_ms: float = 0.0


# ---------------------------------------------------------------------------
# Stage trace logger (DEBUG only; no-op otherwise)
# ---------------------------------------------------------------------------

STAGE_TRACE_ENV = "ENTRY_PIPELINE_STAGE_TRACE"

_STAGE_TRACE_LOGGER = logging.getLogger("small_paper.entry_pipeline_stages.trace")


def stage_trace_enabled() -> bool:
    """DEBUG-only gate: env flag or DEBUG-level logger. Off in production."""
    if os.environ.get(STAGE_TRACE_ENV, "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return _STAGE_TRACE_LOGGER.isEnabledFor(logging.DEBUG)


class StageTraceLogger:
    """Records stage start/end (Stage0..Stage6). Never touches pipeline data."""

    __slots__ = ("enabled", "symbol", "msg_i", "records")

    def __init__(self, *, symbol: str = "", msg_i: int = 0) -> None:
        self.enabled = stage_trace_enabled()
        self.symbol = symbol
        self.msg_i = msg_i
        self.records: list[dict[str, Any]] = []

    def start(self, stage: str) -> None:
        if not self.enabled:
            return
        self._emit(stage, "start")

    def end(self, stage: str, *, note: str = "") -> None:
        if not self.enabled:
            return
        self._emit(stage, "end", note=note)

    def _emit(self, stage: str, phase: str, *, note: str = "") -> None:
        import time

        rec = {
            "stage": stage,
            "phase": phase,
            "symbol": self.symbol,
            "msg_i": self.msg_i,
            "mono": time.monotonic(),
        }
        if note:
            rec["note"] = note
        self.records.append(rec)
        _STAGE_TRACE_LOGGER.debug(
            "[stage_trace] stage=%s phase=%s symbol=%s msg_i=%s%s",
            stage,
            phase,
            self.symbol,
            self.msg_i,
            f" note={note}" if note else "",
        )


def classify_cluster_stage(decision: Any, trade: Mapping[str, Any]) -> Stage3ClusterDecision:
    """Derive Stage3ClusterDecision from Stage2 outputs (read-only, no side effects)."""
    from small_paper.entry_cluster_guard import (
        CLUSTER_GUARD_EXCEPTION,
        CLUSTER_GUARD_FEATURE_INCOMPLETE,
        CLUSTER_GUARD_PASSED,
        CLUSTER_GUARD_REJECTED,
        REJECT_ENTRY_CLUSTER_GUARD,
    )

    status_raw = str(
        getattr(decision, "cluster_guard_status", "") or trade.get("cluster_guard_status") or ""
    )
    cluster_id = getattr(decision, "cluster_id", None)
    if cluster_id is None:
        cluster_id = trade.get("cluster_id")
    new_sub = getattr(decision, "new_subcluster_id", None)
    if new_sub is None:
        new_sub = trade.get("new_subcluster_id")
    burst = getattr(decision, "liquidity_burst", None)
    if burst is None:
        burst = trade.get("liquidity_burst")
    via_exc = bool(getattr(decision, "entry_cluster_guard_via_exception", False))

    if not decision.accept and str(getattr(decision, "reason", "")) == REJECT_ENTRY_CLUSTER_GUARD:
        status = CLUSTER_STAGE_REJECT
    elif status_raw == CLUSTER_GUARD_FEATURE_INCOMPLETE:
        status = CLUSTER_STAGE_FEATURE_INCOMPLETE
    elif status_raw == CLUSTER_GUARD_EXCEPTION:
        status = CLUSTER_STAGE_EXCEPTION
    elif status_raw in (CLUSTER_GUARD_PASSED, CLUSTER_GUARD_REJECTED):
        status = CLUSTER_STAGE_PASS if status_raw == CLUSTER_GUARD_PASSED else CLUSTER_STAGE_REJECT
    elif status_raw:
        status = status_raw
    else:
        status = CLUSTER_STAGE_NOT_EVALUATED
    return Stage3ClusterDecision(
        status=status,
        cluster_guard_status=status_raw,
        cluster_id=cluster_id,
        new_subcluster_id=new_sub,
        liquidity_burst=burst,
        via_exception=via_exc,
    )
