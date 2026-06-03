#!/usr/bin/env python3
"""Phase267: Implementation report + gate dry-run on historical session events."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import unittest
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase267_v2_gate_implementation_report.json"
SRC = REPO / "kabu_native/src"
CONFIG_Q070 = REPO / "kabu_native/configs/small_paper_pilot_q070_cap3.yaml"
SESSION_EVENTS = (
    REPO / "kabu_native/results/small_paper/20260521/live_full_session_081418/small_paper_events.csv"
)


def _bootstrap() -> None:
    for p in (SRC, REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _run_unittest() -> dict[str, Any]:
    tests_dir = REPO / "kabu_native" / "tests"
    suite = unittest.TestLoader().discover(str(tests_dir), pattern="test_phase267*.py")
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    return {
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "ok": result.wasSuccessful(),
    }


def _load_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _gate_dry_run_on_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    from research.exposure_gate import ExposureGate, ExposureGateConfig, REJECT_LOW_QUALITY
    from small_paper.config import load_pilot_config
    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

    cfg = load_pilot_config(CONFIG_Q070)
    gate = ExposureGate(cfg.exposure_gate_config())

    reasons: Counter[str] = Counter()
    v2_reject_with_high_quality = 0
    v2_pass_with_low_quality = 0
    evaluated = 0

    for ev in events:
        if str(ev.get("event_type") or "") not in ("candidate", "accepted", "rejected"):
            continue
        trade = dict(ev)
        trade.setdefault("profile", "momentum_volume_v13_combined")
        trade.update(compute_entry_expectancy_score_fields(trade=trade))
        decision = gate.evaluate_entry(trade)
        if decision.accept:
            gate.record_accepted(trade)
        evaluated += 1
        reason = decision.reason or "accepted"
        reasons[reason] += 1
        q = float(trade.get("continuation_quality_score") or 0)
        v2 = int(trade.get("entry_expectancy_score_v2") or 0)
        if reason == "entry_score_v2_below_threshold" and q >= 0.70:
            v2_reject_with_high_quality += 1
        if decision.accept and q < 0.70 and v2 >= 4:
            v2_pass_with_low_quality += 1

    low_q = int(reasons.get(REJECT_LOW_QUALITY, 0))
    v2_below = int(reasons.get("entry_score_v2_below_threshold", 0))
    top = reasons.most_common(8)

    return {
        "session_events_path": str(SESSION_EVENTS.relative_to(REPO)).replace("\\", "/"),
        "gate_evaluations": evaluated,
        "reject_reason_counts": dict(reasons),
        "entry_score_v2_below_threshold_count": v2_below,
        "low_quality_reject_count": low_q,
        "low_quality_not_dominant": low_q < v2_below,
        "v2_reject_despite_quality_ge_70": v2_reject_with_high_quality,
        "v2_pass_despite_quality_lt_70": v2_pass_with_low_quality,
        "top_reject_reasons": top,
        "config": {
            "entry_score_v2_min": cfg.entry_score_v2_min,
            "reject_below_quality": cfg.reject_below_quality,
            "min_continuation_quality": cfg.min_continuation_quality,
        },
    }


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    tests = _run_unittest()
    events = _load_events(SESSION_EVENTS)
    dry = _gate_dry_run_on_events(events)

    yaml_updated = list(
        p.name for p in sorted((REPO / "kabu_native/configs").glob("small_paper_pilot_q070*.yaml"))
    )

    report = {
        "phase": 267,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "implementation_status": "complete" if tests["ok"] else "tests_failed",
        "constraints": {
            "universe_changed": False,
            "exit_changed": False,
            "max_concurrent_changed": False,
            "daily_runner_default_unchanged": True,
        },
        "changes": {
            "exposure_gate": [
                "REJECT_ENTRY_SCORE_V2_BELOW=entry_score_v2_below_threshold",
                "entry_score_v2_min on ExposureGateConfig",
                "quality reject skipped when entry_score_v2_min>0",
                "GateDecision logs entry_expectancy_score_v2, entry_score_v2_threshold, entry_score_v2_gate_pass",
            ],
            "config": ["SmallPaperPilotConfig.entry_score_v2_min", "YAML loader"],
            "pilot_runner": [
                "EVENT_FIELDS + score precompute before evaluate_entry",
                "_event_from_gate v2 gate fields",
            ],
            "yaml_q070_updated": yaml_updated,
            "discord_notifier": ["reject label for entry_score_v2_below_threshold"],
            "live_observer_readiness": ["_q070_entry_gate_ok replaces quality-only check"],
        },
        "test_results": tests,
        "dry_run_gate_replay": dry,
        "verification": {
            "tests_passed": tests["ok"],
            "v2_reject_reason_present": dry.get("entry_score_v2_below_threshold_count", 0) > 0,
            "low_quality_not_primary_reject": dry.get("low_quality_not_dominant", False),
            "low_quality_reject_count_zero": dry.get("low_quality_reject_count", 0) == 0,
        },
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    print(f"tests_ok={tests['ok']} v2_rejects={dry.get('entry_score_v2_below_threshold_count')}", flush=True)
    return 0 if tests["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
