"""Operational pipeline with full timestamp fields + latency metrics."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np

from research.e1_x36r_freeze_integrity.serialize import score_fn_from_serialized

from . import FEATURE_ORDER, POSITION_CAP, WAIT_SEC
from .notify_queue import NonBlockingNotifyQueue, format_entry_prefix


@dataclass
class TimingRecord:
    fields: dict[str, Optional[float]] = field(default_factory=dict)
    latencies_ms: dict[str, Optional[float]] = field(default_factory=dict)


def _ms(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return float((a - b) * 1000.0)


def process_candidate_operational(
    *,
    ser: dict,
    features: dict[str, float],
    symbol: str,
    anchor_signal_time: float,
    market_event_time: float,
    local_receive_time: float,
    notify: NonBlockingNotifyQueue,
    available_slots: int,
    cohort_rank: int,
    score: float,
    limit_price: float,
    inject_extra_decision_delay_ms: float = 0.0,
) -> dict[str, Any]:
    """
    Simulate operational path for one candidate at known score/rank.
    Does not mutate strategy; records RESEARCH vs OPERATIONAL fill window.
    """
    tr = TimingRecord()
    tr.fields["market_event_time"] = market_event_time
    tr.fields["local_receive_time"] = local_receive_time
    tr.fields["anchor_signal_time"] = anchor_signal_time

    t_snap = time.time()
    tr.fields["snapshot_ready_time"] = t_snap
    # features assumed from rolling state (already computed) — stamp only
    t_feat = time.time()
    tr.fields["feature_ready_time"] = t_feat

    sfn = score_fn_from_serialized(ser)
    # score already provided for cohort; recompute for identity
    t_score0 = time.time()
    recomputed = float(sfn(features))
    t_score1 = time.time()
    tr.fields["score_ready_time"] = t_score1

    if inject_extra_decision_delay_ms > 0:
        time.sleep(inject_extra_decision_delay_ms / 1000.0)

    t_adm = time.time()
    tr.fields["admission_decision_time"] = t_adm

    expiry = anchor_signal_time + WAIT_SEC
    late = t_adm >= expiry - 1e-12
    # RESEARCH: order active = t0
    research_active = anchor_signal_time
    # OPERATIONAL: cannot be active before decision
    operational_active = t_adm
    operational_fill_ok = (not late) and (operational_active < expiry)

    tr.fields["simulated_order_active_time"] = (
        None if late else research_active  # research semantic preserved in field for RESEARCH ledger
    )

    admitted = (available_slots > 0) and (not late)
    block_reason = None
    if late:
        block_reason = "LATE_DECISION_BLOCKED"
        admitted = False
    elif available_slots <= 0:
        block_reason = "NO_AVAILABLE_SLOT"

    # notification after state fixed
    nq = notify.enqueue(
        "ENTRY" if admitted else "LATENCY_WARNING" if late else "ENTRY_BLOCKED",
        {
            "symbol": symbol,
            "signal_time": anchor_signal_time,
            "decision_time": t_adm,
            "decision_latency_ms": _ms(t_adm, anchor_signal_time),
            "score": score,
            "cohort_rank": cohort_rank,
            "limit_price": limit_price,
            "qty": 100,
            "admitted": admitted,
            "expiry_time": expiry,
            "block_reason": block_reason,
        },
        prefix=format_entry_prefix() if admitted else ("[V1R LATENCY WARNING]" if late else "[V1R BLOCKED]"),
    )
    tr.fields["notification_enqueue_time"] = time.time()
    # sent_time filled asynchronously — approximate flush later
    tr.fields["notification_sent_time"] = None

    tr.latencies_ms = {
        "ingest_latency_ms": _ms(local_receive_time, market_event_time),
        "snapshot_latency_ms": _ms(t_snap, anchor_signal_time),
        "feature_latency_ms": _ms(t_feat, t_snap),
        "model_latency_ms": _ms(t_score1, t_score0),
        "allocator_latency_ms": _ms(t_adm, t_score1),
        "decision_latency_ms": _ms(t_adm, anchor_signal_time),
        "notification_enqueue_latency_ms": nq.get("enqueue_latency_ms"),
    }

    return {
        "symbol": symbol,
        "features": features,
        "score": score,
        "score_recomputed": recomputed,
        "score_identity": abs(score - recomputed) < 1e-12,
        "cohort_rank": cohort_rank,
        "admitted": admitted,
        "block_reason": block_reason,
        "late_decision": late,
        "research_order_active": research_active,
        "operational_order_active": operational_active,
        "expiry": expiry,
        "operational_fill_window": [operational_active, expiry] if operational_fill_ok else None,
        "operational_fill_ok": operational_fill_ok,
        "limit_price": limit_price,
        "timing": tr.fields,
        "latencies_ms": tr.latencies_ms,
        "notify": nq,
        "ledgers": {
            "RESEARCH_PROSPECTIVE": {
                "order_active": research_active,
                "expiry": expiry,
                "limit_price_rule": "Buy1 @ t0",
            },
            "OPERATIONAL_REALIZABLE": {
                "order_active": operational_active,
                "expiry": expiry,  # NOT decision+1s
                "fill_ok": operational_fill_ok,
                "late": late,
            },
        },
    }


def latency_benchmark(
    ser: dict,
    *,
    n: int = 200,
    notify: Optional[NonBlockingNotifyQueue] = None,
) -> dict[str, Any]:
    """Synthetic stream latency bench — no market files, no 20260810."""
    own_notify = notify is None
    q = notify or NonBlockingNotifyQueue()
    means = ser["preprocessing"]["mean"]
    scales = ser["preprocessing"]["scale"]
    sfn = score_fn_from_serialized(ser)
    decision_ms = []
    records = []
    dropped_events = 0
    out_of_order = 0
    backlog_max = 0
    for i in range(n):
        # Wall-clock relative anchors: measure wiring latency, not synthetic clock skew.
        # market_event <= local_receive <= anchor just before processing.
        now = time.time()
        market_t = now - 0.003
        local_t = now - 0.001
        if i % 23 == 0:
            # intentional out-of-order receive stamp for counting (still ignored for features)
            local_t = market_t - 0.0001
            out_of_order += 1
        anchor = now
        feats = {f: float(means[j]) + (i % 5) * 0.01 * float(scales[j]) for j, f in enumerate(FEATURE_ORDER)}
        # future-free: only use feats at/before anchor (synthetic already)
        score = float(sfn(feats))
        rec = process_candidate_operational(
            ser=ser,
            features=feats,
            symbol=f"S{i%20:04d}",
            anchor_signal_time=anchor,
            market_event_time=market_t,
            local_receive_time=local_t,
            notify=q,
            available_slots=POSITION_CAP,
            cohort_rank=1,
            score=score,
            limit_price=1000.0 + i,
        )
        dms = rec["latencies_ms"].get("decision_latency_ms")
        if dms is not None:
            decision_ms.append(float(dms))
        records.append(rec)
        st = q.stats()
        backlog_max = max(backlog_max, st["max_backlog"])

    q.flush(timeout_sec=3.0)
    st = q.stats()
    if own_notify:
        q.stop()

    a = np.asarray(decision_ms, dtype=float) if decision_ms else np.asarray([0.0])
    return {
        "n": n,
        "decision_latency_ms": {
            "p50": float(np.quantile(a, 0.50)),
            "p90": float(np.quantile(a, 0.90)),
            "p95": float(np.quantile(a, 0.95)),
            "p99": float(np.quantile(a, 0.99)),
            "max": float(np.max(a)),
            "mean": float(np.mean(a)),
        },
        "notification": st,
        "dropped_events": dropped_events + st["dropped"],
        "out_of_order_events": out_of_order,
        "backlog_max": backlog_max,
        "sample_record_keys": list(records[0]["timing"].keys()) if records else [],
        "records_n": len(records),
    }
