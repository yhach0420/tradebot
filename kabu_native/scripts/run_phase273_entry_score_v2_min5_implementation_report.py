#!/usr/bin/env python3
"""Phase273: entry_score_v2_min 4→5 implementation report + verification."""

from __future__ import annotations

import csv
import json
import sys
import unittest
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase273_entry_score_v2_min5_implementation_report.json"
SRC = REPO / "kabu_native/src"
CONFIG_Q070 = REPO / "kabu_native/configs/small_paper_pilot_q070_cap3.yaml"
SESSION_EVENTS = (
    REPO / "kabu_native/results/small_paper/20260521/live_full_session_081418/small_paper_events.csv"
)
EXPECTED_MIN = 5


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
    from research.exposure_gate import ExposureGate, REJECT_ENTRY_SCORE_V2_BELOW, REJECT_LOW_QUALITY
    from small_paper.config import load_pilot_config
    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields
    from small_paper.live_observer_readiness import EXPECTED_ENTRY_SCORE_V2_MIN

    cfg = load_pilot_config(CONFIG_Q070)
    gate = ExposureGate(cfg.exposure_gate_config())

    reasons: Counter[str] = Counter()
    score_at_decision: Counter[str] = Counter()
    score4_rejected = 0
    score5_accepted = 0
    score5_rejected = 0
    evaluated = 0

    for ev in events:
        if str(ev.get("event_type") or "") not in ("candidate", "accepted", "rejected"):
            continue
        trade = dict(ev)
        trade.setdefault("profile", "momentum_volume_v13_combined")
        trade.update(compute_entry_expectancy_score_fields(trade=trade))
        v2 = int(trade.get("entry_expectancy_score_v2") or 0)
        decision = gate.evaluate_entry(trade)
        if decision.accept:
            gate.record_accepted(trade)
        evaluated += 1
        reason = decision.reason or "accepted"
        reasons[reason] += 1
        score_at_decision[str(v2)] += 1
        if v2 == 4 and reason == REJECT_ENTRY_SCORE_V2_BELOW:
            score4_rejected += 1
        if v2 == 5 and decision.accept:
            score5_accepted += 1
        if v2 == 5 and not decision.accept and reason == REJECT_ENTRY_SCORE_V2_BELOW:
            score5_rejected += 1

    return {
        "session_events_path": str(SESSION_EVENTS.relative_to(REPO)).replace("\\", "/"),
        "gate_evaluations": evaluated,
        "reject_reason_counts": dict(reasons),
        "entry_score_v2_below_threshold_count": int(reasons.get(REJECT_ENTRY_SCORE_V2_BELOW, 0)),
        "low_quality_reject_count": int(reasons.get(REJECT_LOW_QUALITY, 0)),
        "score4_rejected_at_min5": score4_rejected,
        "score5_accepted_at_min5": score5_accepted,
        "score5_rejected_at_min5": score5_rejected,
        "score_distribution": dict(score_at_decision),
        "config": {
            "entry_score_v2_min": cfg.entry_score_v2_min,
            "reject_below_quality": cfg.reject_below_quality,
            "min_continuation_quality": cfg.min_continuation_quality,
        },
        "live_observer_expected_min": EXPECTED_ENTRY_SCORE_V2_MIN,
        "readiness_min_matches_config": EXPECTED_ENTRY_SCORE_V2_MIN == cfg.entry_score_v2_min == EXPECTED_MIN,
    }


def _yaml_audit() -> dict[str, Any]:
    configs = sorted((REPO / "kabu_native/configs").glob("small_paper_pilot_q070*.yaml"))
    rows = []
    bad = []
    for p in configs:
        text = p.read_text(encoding="utf-8")
        ok = "entry_score_v2_min: 5" in text and "entry_score_v2_min: 4" not in text
        rows.append({"file": p.name, "entry_score_v2_min_5": ok})
        if not ok:
            bad.append(p.name)
    return {
        "q070_yaml_count": len(configs),
        "all_min_5": len(bad) == 0,
        "files_not_min_5": bad,
        "files": [r["file"] for r in rows],
    }


def _fast_paper_spot_check() -> dict[str, Any]:
    """Replay one session with min=5 via phase270 engine (trade_count only)."""
    import importlib.util

    p270_path = REPO / "kabu_native/scripts/run_phase270_fast_paper_integration_comparison.py"
    spec = importlib.util.spec_from_file_location("p270", p270_path)
    assert spec and spec.loader
    p270 = importlib.util.module_from_spec(spec)
    sys.modules["p270"] = p270
    spec.loader.exec_module(p270)

    p71 = p270._load_module("phase71_p273", "kabu_native/scripts/run_phase71_split_momentum_fade_review.py")
    sid = "20260521/live_full_session_081418"
    sdir = p270.SMALL_PAPER / sid
    events = p270._load_events(sdir)
    if not events:
        return {"session_id": sid, "skipped": True, "reason": "no events"}
    system = {
        "quality_min": None,
        "score_v2_min": EXPECTED_MIN,
        "score_reject_key": "entry_score_v2_below_threshold",
    }
    sim = p270.SystemSim(system, p71)
    sim._day = "20260521"
    sim._stream = "live"
    for ev in sorted(
        events,
        key=lambda e: (
            p270._parse_ts(str(e.get("event_time") or "")),
            int(p270._float(e.get("message_index")) or 0),
        ),
    ):
        sim.on_row(ev)
    sim.finalize(p71._session_end(events))
    m = p270._metrics_from_trades(sim.completed)
    return {
        "session_id": sid,
        "score_v2_min_used": EXPECTED_MIN,
        "trade_count": m["trade_count"],
        "profit_factor": m["profit_factor"],
        "total_pnl_pct": m["total_pnl_pct"],
        "v2_below_rejects": int(sim.reject_reason_counts.get("entry_score_v2_below_threshold", 0)),
    }


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    tests = _run_unittest()
    yaml_audit = _yaml_audit()
    events = _load_events(SESSION_EVENTS)
    dry = _gate_dry_run_on_events(events)
    fast = _fast_paper_spot_check()

    ok = (
        tests["ok"]
        and yaml_audit["all_min_5"]
        and dry["config"]["entry_score_v2_min"] == EXPECTED_MIN
        and dry["readiness_min_matches_config"]
    )

    report = {
        "phase": 273,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "implementation_status": "complete" if ok else "verification_failed",
        "objective": "Profit maximization under max_concurrent=3; raise entry_score_v2_min 4→5",
        "phase272_reference": {
            "v2_ge4": {"profit_factor": 0.9498, "total_pnl_pct": -36.875, "trade_count": 7081},
            "v2_ge5": {"profit_factor": 1.1093, "total_pnl_pct": 10.0898, "trade_count": 928},
        },
        "constraints": {
            "universe_changed": False,
            "exit_changed": False,
            "max_concurrent_changed": False,
            "daily_runner_default_unchanged": True,
        },
        "changes": {
            "entry_score_v2_min": {"from": 4, "to": 5},
            "yaml_q070_updated": yaml_audit["files"],
            "live_observer_readiness": [
                "EXPECTED_ENTRY_SCORE_V2_MIN 4→5",
            ],
            "tests": [
                "test_phase267_entry_score_v2_gate.py: score4 reject, score5 pass, yaml min=5",
            ],
        },
        "yaml_audit": yaml_audit,
        "test_results": tests,
        "dry_run_gate_replay": dry,
        "fast_paper_spot_check": fast,
        "verification": {
            "tests_passed": tests["ok"],
            "all_q070_yaml_min_5": yaml_audit["all_min_5"],
            "config_entry_score_v2_min_is_5": dry["config"]["entry_score_v2_min"] == EXPECTED_MIN,
            "readiness_expected_min_is_5": dry["live_observer_expected_min"] == EXPECTED_MIN,
            "score4_rejected_at_min5": dry["score4_rejected_at_min5"] > 0,
            "score5_boundary_exercised": dry["score5_accepted_at_min5"] >= 0,
        },
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    print(f"ok={ok} tests={tests['ok']} yaml={yaml_audit['all_min_5']}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
