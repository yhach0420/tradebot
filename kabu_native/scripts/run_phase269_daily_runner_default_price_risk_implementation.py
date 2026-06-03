#!/usr/bin/env python3
"""Phase269: Verify daily runner default universe_mode is price-risk."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase269_daily_runner_default_price_risk_implementation.json"
SRC = REPO / "kabu_native/src"
RUNNER = REPO / "kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py"


def _bootstrap() -> None:
    for p in (SRC, REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _run_runner(day_stamp: str, extra: list[str] | None = None) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(RUNNER),
        "--skip-kabu",
        "--skip-safety",
        "--dry-run-only",
        "--day-stamp",
        day_stamp,
        "--no-generate-features",
    ]
    if extra:
        cmd.extend(extra)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    proc = subprocess.run(
        cmd,
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    cli: dict[str, Any] = {}
    for line in reversed((proc.stdout or "").strip().splitlines()):
        if line.strip().startswith("{"):
            try:
                cli = json.loads(line.strip())
                break
            except json.JSONDecodeError:
                continue
    summary_path = REPO / "kabu_native/results/reports" / f"daily_runner_summary_{day_stamp}.json"
    summary = (
        json.loads(summary_path.read_text(encoding="utf-8"))
        if summary_path.is_file()
        else {}
    )
    commands_path = REPO / "kabu_native/results/reports" / f"daily_runner_commands_{day_stamp}.json"
    commands = (
        json.loads(commands_path.read_text(encoding="utf-8"))
        if commands_path.is_file()
        else {}
    )
    return {
        "exit_code": proc.returncode,
        "cli_json": cli,
        "summary": summary,
        "daily_runner_command": (commands.get("daily_runner") or {}).get("phase148_script"),
    }


def main() -> int:
    _bootstrap()
    parser = argparse.ArgumentParser()
    parser.add_argument("--day-stamp", default="20260602")
    args = parser.parse_args()
    day = args.day_stamp

    from runner.am_pm_daily_runner import (
        ENTRY_GUARD_SHADOW_YAML,
        SHADOW_PILOT_YAML,
        UNIVERSE_MODE_DEFAULT,
        UNIVERSE_MODE_LEGACY,
        UNIVERSE_MODE_PRICE_RISK,
        build_commands_json,
        make_state,
    )

    # argparse choices uniqueness
    choices = [UNIVERSE_MODE_LEGACY, UNIVERSE_MODE_PRICE_RISK]
    choices_ok = len(choices) == len(set(choices)) and UNIVERSE_MODE_DEFAULT == UNIVERSE_MODE_PRICE_RISK

    state_default = make_state(
        REPO,
        REPO / "kabu_native",
        __import__("runner.am_pm_daily_runner", fromlist=["DailyRunnerOptions"]).DailyRunnerOptions(
            day_stamp=day,
            dry_run_only=True,
            universe_mode=UNIVERSE_MODE_DEFAULT,
            config_rel=ENTRY_GUARD_SHADOW_YAML,
        ),
    )
    cmds_default = build_commands_json(state_default)
    state_legacy = make_state(
        REPO,
        REPO / "kabu_native",
        __import__("runner.am_pm_daily_runner", fromlist=["DailyRunnerOptions"]).DailyRunnerOptions(
            day_stamp=day,
            dry_run_only=True,
            universe_mode=UNIVERSE_MODE_LEGACY,
            config_rel=SHADOW_PILOT_YAML,
        ),
    )
    cmds_legacy = build_commands_json(state_legacy)

    dry_no_flag = _run_runner(day)
    dry_legacy = _run_runner(day, ["--universe-mode", UNIVERSE_MODE_LEGACY])

    pr_am = REPO / f"kabu_native/results/reports/universe_core10_dynamic40_price_risk_am_{day}.csv"
    leg_am = REPO / f"kabu_native/results/reports/universe_core10_dynamic40_am_{day}.csv"

    report = {
        "phase": 269,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "implementation_status": "complete",
        "constraints": {
            "entry_changed": False,
            "exit_changed": False,
            "universe_filter_logic_changed": False,
            "max_concurrent_changed": False,
        },
        "constants": {
            "UNIVERSE_MODE_LEGACY": UNIVERSE_MODE_LEGACY,
            "UNIVERSE_MODE_PRICE_RISK": UNIVERSE_MODE_PRICE_RISK,
            "UNIVERSE_MODE_DEFAULT": UNIVERSE_MODE_DEFAULT,
            "argparse_choices": choices,
            "argparse_choices_unique": choices_ok,
        },
        "code_changes": [
            "kabu_native/src/runner/am_pm_daily_runner.py",
            "kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py",
        ],
        "build_commands_json": {
            "default_mode_no_config_flag": cmds_default.get("daily_runner", {}).get("phase148_script"),
            "legacy_mode_has_config_flag": "--config" in (cmds_legacy.get("daily_runner", {}).get("phase148_script") or ""),
        },
        "dry_run": {
            "day_stamp": day,
            "no_flag_default": dry_no_flag,
            "explicit_legacy": dry_legacy,
        },
        "csv_verification": {
            "price_risk_am_exists": pr_am.is_file(),
            "price_risk_am_path": str(pr_am.relative_to(REPO)).replace("\\", "/"),
            "legacy_am_exists": leg_am.is_file(),
            "legacy_am_path": str(leg_am.relative_to(REPO)).replace("\\", "/"),
        },
        "verification": {
            "default_is_price_risk": dry_no_flag["cli_json"].get("universe_mode") == UNIVERSE_MODE_PRICE_RISK,
            "default_uses_entry_guard_yaml": ENTRY_GUARD_SHADOW_YAML in str(
                dry_no_flag["cli_json"].get("config_rel", "")
            ),
            "default_generates_price_risk_csv": "price_risk" in str(
                dry_no_flag["summary"].get("am_universe_csv", "")
            ),
            "default_price_risk_filter_enabled": dry_no_flag["summary"].get("price_risk_filter_enabled") is True,
            "legacy_generates_legacy_csv": "universe_core10_dynamic40_am_" in str(
                dry_legacy["summary"].get("am_universe_csv", "")
            )
            and "price_risk" not in str(dry_legacy["summary"].get("am_universe_csv", "")),
            "legacy_no_price_risk_filter": dry_legacy["summary"].get("price_risk_filter_enabled") is False,
            "all_dry_runs_exit_zero": dry_no_flag["exit_code"] == 0 and dry_legacy["exit_code"] == 0,
        },
    }
    v = report["verification"]
    report["implementation_status"] = (
        "complete"
        if all(v.values())
        else "verification_failed"
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    print(f"status={report['implementation_status']}", flush=True)
    return 0 if report["implementation_status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
