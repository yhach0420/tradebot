#!/usr/bin/env python3
"""Phase641b: pilot subprocess logging enhancement — report generator."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve()
NATIVE_ROOT = SCRIPT.parents[1]
REPO_ROOT = NATIVE_ROOT.parent
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase641b_subprocess_logging"
PHASE641B_VERDICT_DONE = "phase641b_subprocess_logging_done"

for p in (NATIVE_ROOT / "src", REPO_ROOT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def _run_pytest() -> dict:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_phase641b_subprocess_logging.py",
            "tests/test_phase642_daily_runner_verdict_policy.py",
            "tests/test_am_pm_daily_runner_session_dirs.py",
            "-q",
        ],
        cwd=str(NATIVE_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "exit_code": proc.returncode,
        "stdout_tail": proc.stdout.splitlines()[-5:],
        "stderr_tail": proc.stderr.splitlines()[-5:],
        "pass": proc.returncode == 0,
    }


def run_phase641b() -> dict:
    from runner.pilot_subprocess_logging import (
        PILOT_STDERR_LOG,
        PILOT_STDOUT_LOG,
        format_pilot_exit_display,
        persist_pilot_subprocess_logs,
    )
    from small_paper.discord_message_builder import format_runtime_health_lines

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    checks: list[dict] = []

    # Module smoke: persist + display
    smoke_dir = REPORT_DIR / "_smoke_session"
    if smoke_dir.is_dir():
        import shutil

        shutil.rmtree(smoke_dir, ignore_errors=True)
    smoke_dir.mkdir(parents=True)
    meta = persist_pilot_subprocess_logs(
        smoke_dir,
        stdout="ok\n",
        stderr="warn\n",
    )
    checks.append(
        {
            "scenario": "persist_logs_smoke",
            "stdout_log": PILOT_STDOUT_LOG,
            "stderr_log": PILOT_STDERR_LOG,
            "tail_count": len(meta["stderr_last_20_lines"]),
            "pass": (smoke_dir / PILOT_STDOUT_LOG).is_file()
            and (smoke_dir / PILOT_STDERR_LOG).is_file(),
        }
    )

    checks.append(
        {
            "scenario": "pilot_exit_display",
            "warning": format_pilot_exit_display(exit_code=1, pilot_verdict="completed_with_warnings"),
            "failed": format_pilot_exit_display(exit_code=1, pilot_verdict="failed"),
            "pass": format_pilot_exit_display(exit_code=1, pilot_verdict="completed_with_warnings")
            == "1 (warning)"
            and format_pilot_exit_display(exit_code=0, pilot_verdict="success") == "0",
        }
    )

    discord_lines = format_runtime_health_lines(
        {"pilot_exit_code": 1, "pilot_subprocess_verdict": "completed_with_warnings"}
    )
    checks.append(
        {
            "scenario": "discord_runtime_health",
            "pilot_exit_line": next((l for l in discord_lines if l.startswith("Pilot Exit:")), ""),
            "pass": any(l == "Pilot Exit: 1 (warning)" for l in discord_lines),
        }
    )

    pytest_result = _run_pytest()
    checks.append({"scenario": "pytest", **pytest_result})

    all_pass = all(c.get("pass") for c in checks)
    report = {
        "phase": "641b",
        "verdict": PHASE641B_VERDICT_DONE if all_pass else "phase641b_subprocess_logging_fail",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "checks": checks,
        "artifacts": {
            "stdout_log_name": PILOT_STDOUT_LOG,
            "stderr_log_name": PILOT_STDERR_LOG,
            "session_dir_pattern": "results/small_paper/<date>/live_session_xxxxxx/",
            "daily_summary_fields": [
                "am_pilot_exit_code",
                "am_pilot_stdout_path",
                "am_pilot_stderr_path",
                "am_stdout_last_20_lines",
                "am_stderr_last_20_lines",
                "pm_* (same pattern)",
            ],
        },
    }
    (REPORT_DIR / "phase641b_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    report = run_phase641b()
    return 0 if report.get("verdict") == PHASE641B_VERDICT_DONE else 1


if __name__ == "__main__":
    raise SystemExit(main())
