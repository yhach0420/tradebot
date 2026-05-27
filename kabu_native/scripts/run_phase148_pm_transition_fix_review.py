#!/usr/bin/env python3
"""Phase 148 bugfix review: PM transition after AM session dir diff fix."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "kabu_native"
REPORTS = NATIVE / "results" / "reports"


def _bootstrap() -> None:
    for p in (NATIVE / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _run_unit_tests() -> dict[str, Any]:
    import importlib
    import inspect
    import tempfile

    names = [
        "test_diff_before_list_after_set",
        "test_diff_before_set_after_list",
        "test_diff_no_new_dirs",
        "test_diff_multiple_new_dirs",
        "test_list_session_dirs_includes_live_session_prefix",
        "test_discover_live_session_dir_with_summary",
        "test_pilot_ok_without_summary_allows_pm_transition",
        "test_run_pilot_session_does_not_raise_on_list_set",
        "test_am_detection_warning_pm_prep_reached",
    ]
    passed = 0
    failures: list[str] = []
    sys.path.insert(0, str(NATIVE / "tests"))
    mod = importlib.import_module("test_am_pm_daily_runner_session_dirs")
    for name in names:
        fn = getattr(mod, name)
        try:
            sig = inspect.signature(fn)
            if "tmp_path" in sig.parameters:
                with tempfile.TemporaryDirectory() as td:
                    fn(Path(td))
            else:
                fn()
            passed += 1
        except Exception as exc:
            failures.append(f"{name}: {exc}")
    return {"passed": passed, "total": len(names), "failures": failures}


def main() -> int:
    _bootstrap()
    from runner.am_pm_daily_runner import diff_new_session_dirs

    # Regression: list - set must not raise
    list_set_ok = True
    list_set_error = ""
    try:
        before = [Path("a/live_session_1")]
        after = {Path("a/live_session_1"), Path("a/live_session_2")}
        diff_new_session_dirs(before, after)
    except TypeError as exc:
        list_set_ok = False
        list_set_error = str(exc)

    unit = _run_unit_tests()
    dry = subprocess.run(
        [
            sys.executable,
            str(NATIVE / "scripts" / "run_core10_dynamic40_am_pm_daily_runner.py"),
            "--skip-kabu",
            "--skip-safety",
            "--dry-run-only",
            "--day-stamp",
            "20260521",
            "--no-generate-features",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )

    verdict = "phase148_runner_pm_transition_fixed"
    notes: list[str] = []
    if not list_set_ok:
        verdict = "session_dir_detection_still_broken"
        notes.append(f"list-set diff still fails: {list_set_error}")
    if unit["failures"]:
        verdict = "session_dir_detection_still_broken"
        notes.extend(unit["failures"])
    if dry.returncode != 0:
        verdict = "session_dir_detection_still_broken"
        notes.append(f"dry_run_only runner exit={dry.returncode}")

    report: dict[str, Any] = {
        "phase": "148_bugfix",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": verdict,
        "verdict_options": {
            "A": "phase148_runner_pm_transition_fixed",
            "B": "session_dir_detection_still_broken",
        },
        "verdict_notes": notes or ["list/set diff fixed; live_session_* detected; PM transition on pilot_ok"],
        "list_set_diff_ok": list_set_ok,
        "unit_tests": unit,
        "dry_run_runner": {
            "exit_code": dry.returncode,
            "stdout_tail": (dry.stdout or "")[-400:],
            "stderr_tail": (dry.stderr or "")[-400:],
        },
        "fixes": [
            "diff_new_session_dirs normalizes list/set",
            "SESSION_DIR_PREFIXES includes live_session_ and live_full_session_",
            "AM session_dir miss -> warning, PM continues if pilot exit 0",
            "run_daily_runner wraps exceptions with stopped_reason",
        ],
    }

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / "phase148_pm_transition_fix_review.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": verdict, "path": str(out.relative_to(ROOT))}, ensure_ascii=True))
    return 0 if verdict == "phase148_runner_pm_transition_fixed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
