#!/usr/bin/env python3
"""
Phase 154: Validate price-risk universe + entry guard wired into AM/PM daily runner.

Runs dry-run-only daily runner for 20260525 and checks outputs.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path


DAY_STAMP = "20260525"
UNIVERSE_MODE = "core10-dynamic40-price-risk-filter-shadow"
ENTRY_GUARD_YAML = (
    "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_shadow.yaml"
)
FOCUS_5856 = "5856.T"
FOCUS_4392 = "4392.T"
CORE_WARN = "186A.T"


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


def _universe_symbols(path: Path) -> list[str]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [str(r.get("symbol") or "").strip().upper() for r in csv.DictReader(f)]


def determine_verdict(checks: dict[str, bool], runner_exit: int, runner_verdict: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    if runner_exit != 0 or runner_verdict != "am_pm_daily_runner_ready":
        return "universe_mode_wiring_failed", notes + [
            f"runner exit={runner_exit} verdict={runner_verdict}"
        ]
    if checks.get("safety_blocked"):
        return "safety_blocked", notes + ["preflight safety blocked"]
    if not checks.get("config_guard_ok"):
        return "config_guard_missing", notes + ["entry guard config not wired"]
    if not checks.get("universe_wiring_ok"):
        return "universe_mode_wiring_failed", notes + ["universe CSV/checks failed"]
    if not checks.get("core_warning_recorded"):
        return "core_warning_handling_needed", notes + ["186A core warning missing"]
    return "daily_runner_price_risk_shadow_ready", notes + ["Phase154 dry-run validation passed"]


def main() -> int:
    repo_root, native_root = _bootstrap()
    reports = native_root / "results/reports"
    runner_script = native_root / "scripts/run_core10_dynamic40_am_pm_daily_runner.py"

    cmd = [
        sys.executable,
        str(runner_script),
        "--skip-kabu",
        "--skip-safety",
        "--dry-run-only",
        "--day-stamp",
        DAY_STAMP,
        "--universe-mode",
        UNIVERSE_MODE,
        "--config",
        ENTRY_GUARD_YAML,
        "--no-generate-features",
    ]
    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, timeout=120)

    summary = _load_json(reports / f"daily_runner_summary_{DAY_STAMP}.json")
    p154_cmds = _load_json(
        reports / f"phase154_daily_runner_price_risk_shadow_commands_{DAY_STAMP}.json"
    )
    am_csv = reports / f"universe_core10_dynamic40_price_risk_am_{DAY_STAMP}.csv"
    pm_csv = reports / f"universe_core10_dynamic40_price_risk_pm_{DAY_STAMP}.csv"
    syms = _universe_symbols(am_csv)
    sym_set = set(syms)
    dup = len(syms) - len(sym_set)

    core_warnings = summary.get("core_price_risk_warnings") or []
    core_syms = {str(w.get("symbol") or "").upper() for w in core_warnings}

    checks = {
        "runner_exit_zero": proc.returncode == 0,
        "runner_verdict_ready": summary.get("verdict") == "am_pm_daily_runner_ready",
        "am_csv_exists": am_csv.is_file(),
        "pm_csv_exists": pm_csv.is_file(),
        "summary_has_price_risk_fields": bool(summary.get("price_risk_filter_enabled")),
        "entry_guard_enabled_in_summary": bool(summary.get("entry_price_risk_guard_enabled")),
        "5856_excluded": FOCUS_5856 not in sym_set,
        "4392_retained": FOCUS_4392 in sym_set,
        "maintains_50": len(syms) == 50 and dup == 0,
        "core_warning_recorded": CORE_WARN in core_syms,
        "config_points_entry_guard_yaml": ENTRY_GUARD_YAML in str(
            p154_cmds.get("config_rel") or summary.get("config_rel") or ""
        ),
        "commands_shadow_config": "entry_price_risk_guard_shadow" in str(
            p154_cmds.get("am_runner_command") or ""
        ),
        "phase154_commands_exists": (
            reports / f"phase154_daily_runner_price_risk_shadow_commands_{DAY_STAMP}.json"
        ).is_file(),
        "config_guard_ok": bool(summary.get("entry_price_risk_guard_enabled")),
        "universe_wiring_ok": (
            am_csv.is_file()
            and pm_csv.is_file()
            and FOCUS_5856 not in sym_set
            and FOCUS_4392 in sym_set
            and len(syms) == 50
            and dup == 0
        ),
        "safety_blocked": summary.get("verdict") == "safety_blocked",
    }

    verdict, notes = determine_verdict(
        checks,
        proc.returncode,
        str(summary.get("verdict") or ""),
    )

    report = {
        "phase": "154",
        "day_stamp": DAY_STAMP,
        "verdict": verdict,
        "verdict_notes": notes,
        "verdict_options": {
            "A": "daily_runner_price_risk_shadow_ready",
            "B": "config_guard_missing",
            "C": "universe_mode_wiring_failed",
            "D": "core_warning_handling_needed",
            "E": "safety_blocked",
        },
        "dry_run_command": " ".join(cmd),
        "runner_stdout": (proc.stdout or "")[-2000:],
        "runner_stderr": (proc.stderr or "")[-1000:],
        "runner_exit_code": proc.returncode,
        "validation_checks": checks,
        "daily_runner_summary": summary,
        "phase154_shadow_commands": p154_cmds,
        "outputs": {
            "review_json": str(
                reports / f"phase154_daily_runner_price_risk_shadow_review.json"
            ),
            "summary": str(reports / f"daily_runner_summary_{DAY_STAMP}.json"),
            "phase154_commands": str(
                reports / f"phase154_daily_runner_price_risk_shadow_commands_{DAY_STAMP}.json"
            ),
            "am_universe_csv": str(am_csv),
            "pm_universe_csv": str(pm_csv),
        },
    }
    out_path = reports / f"phase154_daily_runner_price_risk_shadow_review.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"verdict": verdict, "checks": checks, "outputs": report["outputs"]}, indent=2))
    return 0 if verdict == "daily_runner_price_risk_shadow_ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
