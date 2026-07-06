#!/usr/bin/env python3
"""Phase642: daily runner verdict policy fix — report generator."""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve()
NATIVE_ROOT = SCRIPT.parents[1]
REPO_ROOT = NATIVE_ROOT.parent
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase642_daily_runner_verdict_policy"
PHASE642_VERDICT_DONE = "phase642_daily_runner_verdict_policy_done"
REAL_SUMMARY = (
    NATIVE_ROOT
    / "results"
    / "small_paper"
    / "20260701"
    / "live_session_080616"
    / "small_paper_summary.json"
)

for p in (NATIVE_ROOT / "src", REPO_ROOT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def run_phase642() -> dict:
    from runner.am_pm_daily_runner import (
        _apply_pilot_verdict_policy,
        _pilot_failed_hard,
        make_state,
        DailyRunnerOptions,
    )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    checks: list[dict] = []

    # Scenario A: 20260701 real summary (or synthetic equivalent)
    if REAL_SUMMARY.is_file():
        summary = json.loads(REAL_SUMMARY.read_text(encoding="utf-8"))
        rel = "kabu_native/results/small_paper/20260701/live_session_080616"
        live = {"exit_code": 1, "session_dir": rel, "stderr_tail": ""}
        state = make_state(REPO_ROOT, NATIVE_ROOT, DailyRunnerOptions(day_stamp="20260701"))
        _apply_pilot_verdict_policy(state, live)
        checks.append(
            {
                "scenario": "20260701_real_summary",
                "pilot_verdict": live.get("pilot_verdict"),
                "hard_failed": _pilot_failed_hard(live, repo_root=REPO_ROOT),
                "accepted_count": summary.get("accepted_count"),
                "stop_reason": summary.get("stop_reason"),
                "pass": live.get("pilot_verdict") == "completed_with_warnings"
                and not _pilot_failed_hard(live, repo_root=REPO_ROOT),
            }
        )
    else:
        checks.append({"scenario": "20260701_real_summary", "pass": False, "note": "summary missing"})

    # Scenario B: crash — exit 1, no summary
    crash_live = {"exit_code": 1, "session_dir": None}
    checks.append(
        {
            "scenario": "crash_no_summary",
            "hard_failed": _pilot_failed_hard(crash_live, repo_root=REPO_ROOT),
            "pass": _pilot_failed_hard(crash_live, repo_root=REPO_ROOT),
        }
    )

    all_pass = all(c.get("pass") for c in checks)
    report = {
        "phase": 642,
        "verdict": PHASE642_VERDICT_DONE if all_pass else "phase642_daily_runner_verdict_policy_fail",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "checks": checks,
        "policy": {
            "completed_with_warnings_requires": [
                "small_paper_summary.json exists",
                "stop_reason == completed",
                "session_started (push_messages|gate_evaluations|runtime_sec)",
                "summary_finalized (generated_at + ended_at)",
                "no fatal_error / proc_error",
                "pilot exit_code != 0",
            ],
            "hard_fail_when": [
                "no summary",
                "fatal_error",
                "stop_reason != completed",
                "proc_error",
            ],
        },
    }
    (REPORT_DIR / "phase642_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    report = run_phase642()
    return 0 if report.get("verdict") == PHASE642_VERDICT_DONE else 1


if __name__ == "__main__":
    raise SystemExit(main())
