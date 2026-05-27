#!/usr/bin/env python3
"""
Phase 157: dry-run validation for intraday refresh shadow daily runner.

Example::
    python kabu_native/scripts/run_phase157_intraday_refresh_runner_review.py --day-stamp 20260525
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native = script.parents[1]
    repo = script.parents[2]
    for p in (native / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo, native


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Phase 157 intraday refresh dry-run review")
    parser.add_argument("--day-stamp", default="20260525")
    args = parser.parse_args()

    repo, native = _bootstrap()
    runner = repo / "kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py"
    cmd = [
        sys.executable,
        str(runner),
        "--skip-kabu",
        "--skip-safety",
        "--dry-run-only",
        "--day-stamp",
        args.day_stamp,
        "--universe-mode",
        "core10-dynamic40-price-risk-filter-shadow",
        "--enable-intraday-refresh",
    ]
    proc = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True)
    review_path = native / "results/reports/phase157_intraday_refresh_runner_review.json"
    summary_path = native / f"results/reports/daily_runner_summary_{args.day_stamp}.json"
    commands_path = native / f"results/reports/daily_runner_commands_{args.day_stamp}.json"

    verdict = "refresh_universe_generation_failed"
    if review_path.is_file():
        review = json.loads(review_path.read_text(encoding="utf-8"))
        verdict = str(review.get("verdict") or verdict)
    elif summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        verdict = str(summary.get("verdict") or verdict)

    out = {
        "phase": 157,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "dry_run_exit_code": proc.returncode,
        "verdict": verdict,
        "stdout_tail": (proc.stdout or "")[-800:],
        "stderr_tail": (proc.stderr or "")[-800:],
        "outputs": {
            "review": str(review_path),
            "summary": str(summary_path),
            "commands": str(commands_path),
        },
    }
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=True, indent=2))
    return 0 if verdict == "intraday_refresh_shadow_ready" and proc.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
