#!/usr/bin/env python3
"""Phase 148b: Recover 20260525 AM session after daily_runner crash; verify runner fix."""

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
SESSION_REL = "kabu_native/results/small_paper/20260525/live_session_075733"
CONFIG_REL = "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"
POLICY = "combined_structural_exit_v1"


def _bootstrap() -> None:
    for p in (NATIVE / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    _bootstrap()
    from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1
    from runner.am_pm_daily_runner import diff_new_session_dirs
    from runner.session_finalize_recovery import (
        recover_session_finalize,
        recovery_verdict,
    )

    day = "20260525"
    session_dir = ROOT / SESSION_REL.replace("/", "\\") if "\\" in str(ROOT) else ROOT / SESSION_REL
    report: dict[str, Any] = {
        "phase": "148b",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day,
        "session_dir": SESSION_REL,
    }

    # Runner set-diff regression
    list_set_ok = True
    try:
        diff_new_session_dirs(
            [session_dir],
            {session_dir, session_dir.parent / "live_session_999999"},
        )
    except TypeError as exc:
        list_set_ok = False
        report["list_set_diff_error"] = str(exc)
    report["runner_set_diff_ok"] = list_set_ok

    # Recovery
    recovery = recover_session_finalize(
        session_dir,
        repo_root=ROOT,
        config_rel=CONFIG_REL,
        structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    )
    report["recovery"] = recovery

    # review_structural_observer subprocess (same as manual command)
    review_proc = subprocess.run(
        [
            sys.executable,
            str(NATIVE / "scripts" / "review_structural_observer.py"),
            "--session-dir",
            str(session_dir),
            "--config",
            str(ROOT / CONFIG_REL),
            "--structural-exit-policy",
            POLICY_COMBINED_STRUCTURAL_EXIT_V1,
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    report["review_structural_observer"] = {
        "exit_code": review_proc.returncode,
        "stderr_tail": (review_proc.stderr or "")[-600:],
    }

    verdict = recovery_verdict(recovery)
    if review_proc.returncode != 0:
        verdict = "runner_fixed_but_recovery_partial" if list_set_ok else "recovery_failed"
    elif not list_set_ok:
        verdict = "runner_fixed_but_recovery_partial"
    report["verdict"] = verdict
    report["verdict_options"] = {
        "A": "am_session_recovered_and_runner_fixed",
        "B": "runner_fixed_but_recovery_partial",
        "C": "recovery_failed",
    }

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f"phase148b_runner_crash_recovery_{day}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"verdict": verdict, "path": str(out.relative_to(ROOT))}, ensure_ascii=True))
    return 0 if verdict == "am_session_recovered_and_runner_fixed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
