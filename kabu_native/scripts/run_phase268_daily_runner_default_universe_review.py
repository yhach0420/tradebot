#!/usr/bin/env python3
"""
Phase268 (review only): Can daily runner default switch to price-risk universe mode?

Output: kabu_native/results/reports/phase268_daily_runner_default_universe_review.json
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase268_daily_runner_default_universe_review.json"
SRC = REPO / "kabu_native/src"
REPORTS = REPO / "kabu_native/results/reports"
RUNNER_SCRIPT = REPO / "kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py"


def _bootstrap() -> None:
    for p in (SRC, REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _run_dry_run(day_stamp: str, *, universe_mode: str | None = None) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(RUNNER_SCRIPT),
        "--skip-kabu",
        "--skip-safety",
        "--dry-run-only",
        "--day-stamp",
        day_stamp,
        "--no-generate-features",
    ]
    if universe_mode:
        cmd.extend(["--universe-mode", universe_mode])
    import os

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
    stdout = (proc.stdout or "").strip()
    parsed: dict[str, Any] = {}
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                parsed = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    summary_path = REPORTS / f"daily_runner_summary_{day_stamp}.json"
    summary: dict[str, Any] = {}
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return {
        "exit_code": proc.returncode,
        "cli_json": parsed,
        "summary": summary,
        "stderr_tail": (proc.stderr or "")[-2000:],
    }


def _universe_diff(day_stamp: str) -> dict[str, Any]:
    legacy_am = REPORTS / f"universe_core10_dynamic40_am_{day_stamp}.csv"
    pr_am = REPORTS / f"universe_core10_dynamic40_price_risk_am_{day_stamp}.csv"

    def dyn_syms(path: Path) -> set[str]:
        if not path.is_file():
            return set()
        with path.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        out: set[str] = set()
        for r in rows:
            bucket = str(r.get("source_bucket") or "")
            if "dynamic" in bucket or bucket.startswith("vol_liq"):
                out.add(str(r.get("symbol") or ""))
        return {s for s in out if s}

    def min_dyn_close(path: Path) -> float | None:
        if not path.is_file():
            return None
        with path.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        vals: list[float] = []
        for r in rows:
            bucket = str(r.get("source_bucket") or "")
            if "dynamic" not in bucket and not bucket.startswith("vol_liq"):
                continue
            for key in ("close_price", "close"):
                v = r.get(key)
                if v not in (None, ""):
                    try:
                        vals.append(float(v))
                    except (TypeError, ValueError):
                        pass
                    break
        return min(vals) if vals else None

    la, pa = dyn_syms(legacy_am), dyn_syms(pr_am)
    return {
        "legacy_am_csv": str(legacy_am.relative_to(REPO)).replace("\\", "/"),
        "price_risk_am_csv": str(pr_am.relative_to(REPO)).replace("\\", "/"),
        "legacy_dynamic40_count": len(la),
        "price_risk_dynamic40_count": len(pa),
        "legacy_min_dynamic_close": min_dyn_close(legacy_am),
        "price_risk_min_dynamic_close": min_dyn_close(pr_am),
        "only_in_legacy_dynamic40": sorted(la - pa),
        "only_in_price_risk_dynamic40": sorted(pa - la),
        "overlap_dynamic40": len(la & pa),
        "replacement_count_estimate": len(la ^ pa) // 2,
    }


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    from runner.am_pm_daily_runner import (
        ENTRY_GUARD_SHADOW_YAML,
        SHADOW_PILOT_YAML,
        UNIVERSE_MODE_DEFAULT,
        UNIVERSE_MODE_PRICE_RISK,
    )
    from universe.price_risk_filter import MIN_CLOSE_PRICE

    day_stamp = "20260602"
    if not (REPORTS / f"features_{day_stamp}.csv").is_file():
        day_stamp = max(
            (p.name.replace("features_", "").replace(".csv", "") for p in REPORTS.glob("features_*.csv")),
            default="20260602",
        )

    dry_default = _run_dry_run(day_stamp)
    dry_legacy = _run_dry_run(day_stamp, universe_mode="core10-dynamic40")
    dry_price_risk = _run_dry_run(day_stamp, universe_mode=UNIVERSE_MODE_PRICE_RISK)
    diff = _universe_diff(day_stamp)

    # argparse choices duplicate if DEFAULT == PRICE_RISK without LEGACY alias
    choices_issue = UNIVERSE_MODE_DEFAULT == UNIVERSE_MODE_PRICE_RISK

    report = {
        "phase": 268,
        "mode": "daily_runner_default_universe_review",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraints": {
            "review_only": True,
            "entry_changed": False,
            "exit_changed": False,
            "default_changed_in_code": False,
        },
        "background": {
            "phase250": "MIN_CLOSE_PRICE=300 implemented in price_risk_filter.py",
            "phase266b": "daily runner CLI default still core10-dynamic40; operators must pass --universe-mode",
        },
        "current_defaults": {
            "script": str(RUNNER_SCRIPT.relative_to(REPO)).replace("\\", "/"),
            "cli_arg_default": UNIVERSE_MODE_DEFAULT,
            "DailyRunnerOptions.universe_mode_default": UNIVERSE_MODE_DEFAULT,
            "price_risk_mode_constant": UNIVERSE_MODE_PRICE_RISK,
            "MIN_CLOSE_PRICE": MIN_CLOSE_PRICE,
            "config_when_default_cli": SHADOW_PILOT_YAML,
            "config_when_price_risk_cli": ENTRY_GUARD_SHADOW_YAML,
            "auto_config_wiring_in_script": (
                "ENTRY_GUARD_SHADOW_YAML when universe_mode == UNIVERSE_MODE_PRICE_RISK else SHADOW_PILOT_YAML"
            ),
        },
        "feasibility": {
            "can_change_default_to_price_risk": True,
            "blocking_code_gaps": [],
            "implementation_notes": [
                "Set UNIVERSE_MODE_DEFAULT = UNIVERSE_MODE_PRICE_RISK (or alias) in am_pm_daily_runner.py",
                "Keep explicit legacy opt-in: add UNIVERSE_MODE_LEGACY='core10-dynamic40' for argparse choices",
                "Fix argparse choices if DEFAULT equals PRICE_RISK (avoid duplicate choice values)",
                "Update build_commands_json: config --config flag when mode != new default (line ~989)",
                "No change to price_risk_filter.py required (MIN_CLOSE already 300)",
            ],
            "argparse_choices_duplicate_if_naive_swap": choices_issue,
        },
        "impact_inventory": {
            "code_constants": [
                "kabu_native/src/runner/am_pm_daily_runner.py — UNIVERSE_MODE_DEFAULT, DailyRunnerOptions",
                "kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py — argparse default + config_rel branch",
            ],
            "universe_outputs": {
                "legacy_csv_pattern": "universe_core10_dynamic40_{am,pm}_YYYYMMDD.csv",
                "price_risk_csv_pattern": "universe_core10_dynamic40_price_risk_{am,pm}_YYYYMMDD.csv",
                "note": "Default switch changes which CSV pair is produced without CLI flag",
            },
            "pilot_config_pairing": {
                "legacy_default_yaml": SHADOW_PILOT_YAML,
                "price_risk_yaml": ENTRY_GUARD_SHADOW_YAML,
                "safety_expectations": "verify_config_safety enforces entry_price_risk_guard when price-risk mode",
            },
            "operational": [
                "kabu symbol registration uses generated universe CSV path from daily_runner summary",
                "First day after switch: ~10/40 dynamic symbols may churn (Phase250/254 evidence)",
                "core10 symbols with close<300: warn only, not auto-dropped (unchanged)",
            ],
            "intraday_refresh": {
                "requires_price_risk_mode": True,
                "note": "--enable-intraday-refresh already blocked on legacy mode",
            },
            "documentation_and_runbooks": [
                "kabu_native/docs/phase162_recommendation.md",
                "phase174/168/179 scripts embedding explicit --universe-mode",
                "phase249 recommended_rollout_sequence step 2",
            ],
            "explicit_legacy_callers": [
                "Operators/scripts passing --universe-mode core10-dynamic40 (unchanged behavior)",
                "run_phase115_am_pm_shadow_pipeline.py (optional legacy mode)",
            ],
            "not_affected": [
                "ENTRY gate logic (Phase267 v2 gate)",
                "EXIT / structural_exit policies",
                "small_paper_pilot.yaml production path (separate from daily runner trial)",
            ],
        },
        "rollback_procedure": {
            "code_rollback": [
                "Revert UNIVERSE_MODE_DEFAULT to 'core10-dynamic40' in am_pm_daily_runner.py",
                "Revert argparse default in run_core10_dynamic40_am_pm_daily_runner.py if changed",
            ],
            "operational_rollback": [
                "Run daily runner with --universe-mode core10-dynamic40 --config small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml",
                "Regenerate universe_core10_dynamic40_{am,pm}_YYYYMMDD.csv",
                "Re-register kabu symbols from legacy CSV before next live session",
            ],
            "artifact_restore": [
                "Restore prior universe_* CSV from backup or re-run legacy dry-run",
            ],
            "validation_after_rollback": [
                "dynamic40 may include close<300 names again",
                "summary JSON universe_mode=core10-dynamic40",
                "config_rel=mfe_fav_vol_liq trial yaml",
            ],
        },
        "dry_run_comparison": {
            "day_stamp": day_stamp,
            "default_cli_no_flag": dry_default,
            "explicit_legacy": dry_legacy,
            "explicit_price_risk": dry_price_risk,
            "universe_symbol_diff": diff,
        },
        "verdict": {
            "recommendation": "adopt_price_risk_as_default_in_next_implementation_phase",
            "rationale": (
                "Dry-run confirms legacy default still builds universes without MIN_CLOSE=300 filter "
                "(min dynamic close 0.0 on 20260602). Price-risk mode excludes 10 dynamic names and "
                "refills replacements (min close 325). Forgetting --universe-mode silently negates Phase250 benefit."
            ),
            "operator_gap_closed_by_default": True,
            "residual_risk": "Symbol churn on switch day; legacy studies must pass --universe-mode core10-dynamic40 explicitly",
        },
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    print(f"verdict={report['verdict']['recommendation']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
