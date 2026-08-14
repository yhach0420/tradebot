"""Low-overhead per-PUSH consumer stage telemetry (V12).

Always-on aggregated stats. Does not write a per-PUSH trace.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

STAGE_NAMES = (
    "native_ingest_us",
    "fill_check_us",
    "pbv2_schedule_us",
    "pbv2_eval_us",
    "audit_enqueue_us",
    "ack_us",
)


def _percentile(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    i = int(round((len(s) - 1) * p))
    return float(s[min(max(i, 0), len(s) - 1)])


@dataclass
class _StageAgg:
    count: int = 0
    total: float = 0.0
    max_v: float = 0.0
    samples: list[float] = field(default_factory=list)


class ConsumerPushTelemetry:
    """Reservoir-sampled microseconds per stage."""

    def __init__(self, *, max_samples: int = 4096, sample_every: int = 8) -> None:
        self.max_samples = int(max_samples)
        self.sample_every = max(1, int(sample_every))
        self._agg: dict[str, _StageAgg] = defaultdict(_StageAgg)
        self._n = 0

    def begin_push(self) -> None:
        self._n += 1

    def record_us(self, stage: str, us: float) -> None:
        a = self._agg[stage]
        v = float(us)
        a.count += 1
        a.total += v
        if v > a.max_v:
            a.max_v = v
        if a.count % self.sample_every == 0 or len(a.samples) < 32:
            if len(a.samples) < self.max_samples:
                a.samples.append(v)
            else:
                a.samples[a.count % self.max_samples] = v

    def record_sec(self, stage: str, sec: float) -> None:
        self.record_us(stage, float(sec) * 1_000_000.0)

    def span(self, stage: str) -> "_StageSpan":
        return _StageSpan(self, stage)

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {"push_count": int(self._n), "stages": {}}
        for name in STAGE_NAMES:
            a = self._agg.get(name) or _StageAgg()
            samples = a.samples
            out["stages"][name] = {
                "count": int(a.count),
                "total": round(a.total, 3),
                "mean": round(a.total / a.count, 3) if a.count else 0.0,
                "p50": round(_percentile(samples, 0.50), 3),
                "p95": round(_percentile(samples, 0.95), 3),
                "p99": round(_percentile(samples, 0.99), 3),
                "max": round(a.max_v, 3),
            }
        return out


@dataclass
class _StageSpan:
    tel: ConsumerPushTelemetry
    stage: str
    t0: float = 0.0

    def __enter__(self) -> "_StageSpan":
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.tel.record_sec(self.stage, time.perf_counter() - self.t0)
