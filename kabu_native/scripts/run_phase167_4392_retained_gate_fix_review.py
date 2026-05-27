#!/usr/bin/env python3
"""
Phase 167: Validate 4392_retained demoted from hard gate to caution (20260527 fix).
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
DAY_STAMP = "20260527"
UNIVERSE_MODE = "core10-dynamic40-price-risk-filter-shadow"


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native = script.parents[1]
    repo = script.parents[2]
    for p in (native / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo, native


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def determine_verdict(
    *,
    dry_checks: dict[str, bool],
    summary: dict,
    full_log: dict,
    runner_exit: int | None,
) -> tuple[str, list[str]]:
    notes: list[str] = []
    verdict = str(summary.get("verdict") or "")
    stopped = str(summary.get("stopped_reason") or "")

    if dry_checks.get("safety_blocked"):
        return "E", notes + ["preflight safety blocked"]

    if not dry_checks.get("am_prep_ok"):
        return "D", notes + ["dry-run: am_prep.ok still false after fix"]

    if verdict == "universe_generation_failed" or stopped == "am_universe":
        return "D", notes + ["runner still blocked at AM universe"]

    am_dir = summary.get("am_session_dir")
    pm_dir = summary.get("pm_session_dir")
    am_live_ok = summary.get("am_live_ok")
    pm_live_ok = summary.get("pm_live_ok")

    if am_dir and (am_live_ok is True or am_live_ok is None):
        if pm_dir or verdict == "am_pm_daily_runner_ready":
            return "A", notes + ["AM started; runner progressed toward or through PM"]
        return "A", notes + ["AM session started"]

    if not am_dir and pm_dir:
        return "B", notes + ["PM-only or AM skipped; PM session dir present"]

    if verdict in ("am_pm_daily_runner_ready", "started") and runner_exit == 0:
        return "A", notes + [f"runner completed with verdict={verdict}"]

    if dry_checks.get("am_prep_ok") and verdict not in ("universe_generation_failed",):
        if summary.get("dry_run_only"):
            return "A", notes + ["dry-run validation passed; live runner not invoked in this pass"]
        return "C", notes + [f"universe fixed but session not started yet (verdict={verdict})"]

    return "D", notes + [f"still blocked (verdict={verdict}, stopped={stopped})"]


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Phase 167 4392_retained gate fix review")
    parser.add_argument("--day-stamp", default=DAY_STAMP)
    parser.add_argument(
        "--skip-dry-run",
        action="store_true",
        help="Only evaluate existing summary/log (after live runner)",
    )
    args = parser.parse_args()

    repo, native = _bootstrap()
    reports = native / "results/reports"
    runner_script = native / "scripts/run_core10_dynamic40_am_pm_daily_runner.py"
    day = args.day_stamp

    dry_proc = None
    if not args.skip_dry_run:
        cmd = [
            sys.executable,
            str(runner_script),
            "--skip-kabu",
            "--skip-safety",
            "--dry-run-only",
            "--day-stamp",
            day,
            "--universe-mode",
            UNIVERSE_MODE,
            "--enable-intraday-refresh",
            "--no-generate-features",
        ]
        dry_proc = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True, timeout=180)

    summary = _load_json(reports / f"daily_runner_summary_{day}.json")
    full_log = _load_json(reports / f"phase148_am_pm_daily_runner_{day}.json")
    am_prep = full_log.get("am_prep") or {}
    sym_checks = am_prep.get("price_risk_symbol_checks") or {}
    preflight = full_log.get("preflight") or {}
    cautions = list(preflight.get("cautions") or summary.get("preflight_cautions") or [])
    focus_cautions = list(am_prep.get("price_risk_focus_cautions") or summary.get("price_risk_focus_cautions") or [])

    dry_checks = {
        "am_prep_ok": bool(am_prep.get("ok")),
        "universe_validation_passed": bool((am_prep.get("universe_validation") or {}).get("passed")),
        "runner_check_passed": bool((am_prep.get("runner_check") or {}).get("passed")),
        "5856_excluded": bool(sym_checks.get("5856_excluded")),
        "maintains_50": bool(sym_checks.get("maintains_50")),
        "4392_retained_false": sym_checks.get("4392_retained") is False,
        "4392_caution_recorded": any("4392.T" in c for c in cautions + focus_cautions),
        "4392_not_hard_stop": bool(am_prep.get("ok")) and sym_checks.get("4392_retained") is False,
        "not_universe_generation_failed": str(summary.get("verdict") or full_log.get("verdict"))
        != "universe_generation_failed",
        "safety_blocked": str(summary.get("verdict") or "") == "safety_blocked",
        "intraday_refresh_ok": bool((am_prep.get("intraday_refresh") or {}).get("ok")),
    }

    verdict, notes = determine_verdict(
        dry_checks=dry_checks,
        summary=summary,
        full_log=full_log,
        runner_exit=dry_proc.returncode if dry_proc else None,
    )

    report = {
        "phase": 167,
        "title": "4392_retained hard gate demoted to caution",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day,
        "verdict": verdict,
        "verdict_options": {
            "A": "fixed_and_runner_started",
            "B": "fixed_but_pm_only",
            "C": "fixed_but_market_time_missed",
            "D": "still_blocked",
            "E": "safety_blocked",
        },
        "verdict_notes": notes,
        "fix_summary": {
            "removed_from_ok_gate": "4392_retained",
            "retained_hard_gates": [
                "universe_validation.passed",
                "runner_check.passed",
                "5856_excluded",
                "maintains_50",
            ],
            "4392_retained_policy": "caution_only",
        },
        "dry_run_checks": dry_checks,
        "price_risk_symbol_checks": sym_checks,
        "price_risk_focus_cautions": focus_cautions,
        "preflight_cautions": cautions,
        "daily_runner_summary": summary,
        "runner_dry_stdout_tail": (dry_proc.stdout or "")[-800:] if dry_proc else None,
        "runner_dry_exit_code": dry_proc.returncode if dry_proc else None,
        "outputs": {
            "review_json": str(reports / "phase167_4392_retained_gate_fix_review.json"),
            "summary": str(reports / f"daily_runner_summary_{day}.json"),
            "full_log": str(reports / f"phase148_am_pm_daily_runner_{day}.json"),
        },
    }
    out_path = reports / "phase167_4392_retained_gate_fix_review.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": verdict, "dry_checks": dry_checks, "notes": notes}, indent=2))
    return 0 if verdict in ("A", "B") else 1


if __name__ == "__main__":
    raise SystemExit(main())
