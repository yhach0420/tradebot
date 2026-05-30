#!/usr/bin/env python3
"""
Phase176: Verification for minimal intraday refresh degraded-mode patch.

Writes:
 - kabu_native/results/reports/phase176_intraday_refresh_patch_verification.json
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


OUT = Path("kabu_native/results/reports/phase176_intraday_refresh_patch_verification.json")
PILOT_RUNNER = Path("kabu_native/src/small_paper/pilot_runner.py")


def _run(cmd: list[str]) -> dict[str, Any]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "command": " ".join(cmd),
        "exit_code": p.returncode,
        "stdout": p.stdout[-4000:],
        "stderr": p.stderr[-4000:],
        "ok": p.returncode == 0,
    }


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    src = PILOT_RUNNER.read_text(encoding="utf-8")

    checks = {
        "1_no_request_stop_on_open_symbols_exceed_cap": '_request_stop("open_symbols_exceed_cap")' not in src,
        "2_intraday_refresh_done_true_on_failure": "state.intraday_refresh_done = True" in src,
        "3_specs_empty_guard": "if not specs:" in src,
        "4_failure_log_has_action_and_will_stop": (
            "action\": \"continue_keep_previous_subscription" in src and "will_stop\": False" in src
        ),
    }

    compile_res = _run([sys.executable, "-m", "py_compile", str(PILOT_RUNNER)])
    t1 = _run([sys.executable, "-m", "unittest", "-q", "kabu_native.tests.test_intraday_refresh"])
    t2 = _run(
        [
            sys.executable,
            "-m",
            "unittest",
            "-q",
            "kabu_native.tests.test_phase176_intraday_refresh_degraded_behavior",
        ]
    )

    verdict = "pass"
    if not all(checks.values()):
        verdict = "fail_code_invariant"
    elif not compile_res.get("ok"):
        verdict = "fail_py_compile"
    elif not (t1.get("ok") and t2.get("ok")):
        verdict = "fail_tests"

    report = {
        "phase": 176,
        "verdict": verdict,
        "checks": checks,
        "py_compile": compile_res,
        "tests": {
            "test_intraday_refresh": t1,
            "test_phase176_intraday_refresh_degraded_behavior": t2,
        },
        "notes": {
            "expected_runtime_behavior": [
                "At refresh time, if open_symbols_exceed_cap occurs, emit intraday_refresh failed with action=continue_keep_previous_subscription, will_stop=false.",
                "Do not call _request_stop; keep previous subscription; continue main loop.",
                "Mark intraday_refresh_done=True to avoid repeated attempts.",
            ]
        },
        "files": {
            "pilot_runner": str(PILOT_RUNNER).replace("\\", "/"),
        },
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

