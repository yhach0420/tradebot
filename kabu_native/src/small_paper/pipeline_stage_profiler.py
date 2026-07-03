"""
Phase617: per-tick pipeline stage profiler (measurement only).
"""

from __future__ import annotations

import gzip
import csv
import json
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


def pipeline_stage_profile_enabled() -> bool:
    import os

    return os.environ.get("PIPELINE_STAGE_PROFILE", "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class PipelineStageProfiler:
    max_samples: int = 3000
    max_per_bucket: int = 1000
    _marks: dict[str, float] = field(default_factory=dict)
    _samples: list[dict[str, Any]] = field(default_factory=list)
    _extension_ms: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    _extension_calls: Counter = field(default_factory=Counter)
    _hot_path_violations: list[dict[str, Any]] = field(default_factory=list)

    def begin_tick(self) -> None:
        self._marks = {"push_received": time.monotonic()}

    def mark(self, stage: str) -> None:
        self._marks[stage] = time.monotonic()

    def record_extension(self, name: str, duration_ms: float) -> None:
        self._extension_calls[name] += 1
        self._extension_ms[name].append(float(duration_ms))

    def finish_tick(
        self,
        *,
        symbol: str,
        gate_reason: str,
        accepted: bool,
        payload_hash: str = "",
    ) -> None:
        m = self._marks
        if "push_received" not in m:
            return

        def _delta(a: str, b: str) -> Optional[float]:
            if a not in m or b not in m:
                return None
            return round((m[b] - m[a]) * 1000.0, 3)

        push_to_enrich = _delta("push_received", "enrich_done")
        extension_ms = _delta("push_received", "extension_done")
        enrich_to_fresh = _delta("enrich_done", "freshness_done")
        fresh_to_pbv2 = _delta("freshness_done", "pbv2_start")
        pbv2_ms = _delta("pbv2_start", "pbv2_end")
        pbv2_to_decision = _delta("pbv2_end", "decision_done")
        push_to_fresh = _delta("push_received", "freshness_done")
        total_ms = _delta("push_received", "decision_done")

        row = {
            "symbol": symbol,
            "gate_reason": gate_reason,
            "accepted": accepted,
            "push_to_enrich_ms": push_to_enrich,
            "extension_pre_core_ms": extension_ms,
            "enrich_to_freshness_ms": enrich_to_fresh,
            "freshness_to_pbv2_ms": fresh_to_pbv2,
            "pbv2_ms": pbv2_ms,
            "pbv2_to_decision_ms": pbv2_to_decision,
            "push_to_freshness_ms": push_to_fresh,
            "total_ms": total_ms,
            "payload_hash": payload_hash,
        }
        if len(self._samples) < self.max_samples:
            self._samples.append(row)

        for label, val in (
            ("extension_pre_core", extension_ms),
            ("post_pbv2_decision", pbv2_to_decision),
        ):
            if val is not None and val >= 5.0:
                if len(self._hot_path_violations) < self.max_per_bucket:
                    self._hot_path_violations.append({**row, "violation_stage": label, "ms": val})

        off_core = (extension_ms or 0) + (pbv2_to_decision or 0)
        if off_core >= 5.0 and len(self._hot_path_violations) < self.max_per_bucket:
            self._hot_path_violations.append({**row, "violation_stage": "off_core_hot_path", "ms": off_core})

    @staticmethod
    def _percentile(vals: Sequence[float], p: float) -> float:
        if not vals:
            return 0.0
        s = sorted(vals)
        i = int(round((len(s) - 1) * p))
        return round(s[min(max(i, 0), len(s) - 1)], 3)

    def stage_summary(self) -> list[dict[str, Any]]:
        cols = [
            ("push_to_freshness_ms", "push_to_freshness"),
            ("freshness_to_pbv2_ms", "freshness_to_pbv2"),
            ("pbv2_ms", "pbv2"),
            ("pbv2_to_decision_ms", "pbv2_to_decision"),
            ("total_ms", "total"),
        ]
        out = []
        for col, label in cols:
            vals = [float(r[col]) for r in self._samples if r.get(col) is not None]
            out.append(
                {
                    "stage": label,
                    "p50": self._percentile(vals, 0.5),
                    "p95": self._percentile(vals, 0.95),
                    "p99": self._percentile(vals, 0.99),
                    "max": round(max(vals), 3) if vals else 0,
                    "n": len(vals),
                }
            )
        return out

    def extension_summary(self) -> list[dict[str, Any]]:
        rows = []
        for name, vals in sorted(self._extension_ms.items()):
            rows.append(
                {
                    "extension": name,
                    "call_count": self._extension_calls.get(name, 0),
                    "total_ms": round(sum(vals), 3),
                    "mean_ms": round(statistics.mean(vals), 3) if vals else 0,
                    "max_ms": round(max(vals), 3) if vals else 0,
                }
            )
        return rows

    def write_samples_gz(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        cols = [
            "symbol",
            "gate_reason",
            "accepted",
            "push_to_enrich_ms",
            "extension_pre_core_ms",
            "enrich_to_freshness_ms",
            "freshness_to_pbv2_ms",
            "pbv2_ms",
            "pbv2_to_decision_ms",
            "push_to_freshness_ms",
            "total_ms",
            "payload_hash",
        ]
        with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in self._samples:
                w.writerow({k: r.get(k, "") for k in cols})

    def to_json(self) -> dict[str, Any]:
        return {
            "sample_count": len(self._samples),
            "stage_summary": self.stage_summary(),
            "extension_summary": self.extension_summary(),
            "hot_path_violations": self._hot_path_violations[:1000],
        }
