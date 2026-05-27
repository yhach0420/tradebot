#!/usr/bin/env python3
"""
Phase 106: Connect universe build to shadow / small-paper live (dry-run).

Modes:
  default (phase105): build_dynamic_universe --board-mode none
  opening-dynamic50 (phase109): opening_dynamic50_0905 → universe_opening_dynamic50
  vol-liq-dynamic50 (phase113): features_YYYYMMDD → universe_vol_liq_dynamic50
  am-pm-dynamic50 (phase115): phase114 AM/PM → universe_am/pm_dynamic50
  core10-dynamic40 (phase118): Core10 Discord + Dynamic40 → universe_core10_dynamic40_am/pm
"""

from __future__ import annotations

import argparse
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
BUILD = NATIVE / "scripts" / "build_dynamic_universe.py"
PHASE109 = NATIVE / "scripts" / "run_phase109_opening_dynamic50_universe.py"
PHASE113 = NATIVE / "scripts" / "run_phase113_vol_liq_dynamic50_universe.py"
PHASE114 = NATIVE / "scripts" / "run_phase114_am_pm_universe_design.py"
PHASE115 = NATIVE / "scripts" / "run_phase115_am_pm_shadow_pipeline.py"
PHASE118 = NATIVE / "scripts" / "run_phase118_core10_dynamic40_pipeline.py"
SHADOW_PILOT_YAML = "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def _bootstrap() -> None:
    for p in (NATIVE / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def run_build(day_stamp: str, board_mode: str) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(BUILD),
        "--board-mode",
        board_mode,
        "--date-stamp",
        day_stamp,
    ]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=180)
    out: dict[str, Any] = {
        "exit_code": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "board_mode": board_mode,
    }
    p105 = REPORTS / f"phase105_register_limit_aware_universe_{day_stamp}.json"
    if p105.is_file():
        out.update(json.loads(p105.read_text(encoding="utf-8")))
    return out


def run_phase114(day_stamp: str) -> dict[str, Any]:
    """Phase114 AM/PM universe CSVs (generates features when missing)."""
    feat = REPORTS / f"features_{day_stamp}.csv"
    cmd = [sys.executable, str(PHASE114), "--day-stamp", day_stamp]
    if feat.is_file():
        cmd.append("--no-generate")
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    out: dict[str, Any] = {
        "exit_code": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }
    p114 = REPORTS / "phase114_am_pm_universe_design.json"
    if p114.is_file():
        out["phase114"] = json.loads(p114.read_text(encoding="utf-8"))
    return out


def run_phase115(day_stamp: str, *, universe_mode: str = "am-pm-dynamic50") -> dict[str, Any]:
    cmd = [sys.executable, str(PHASE115), "--day-stamp", day_stamp, "--universe-mode", universe_mode]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    out: dict[str, Any] = {
        "exit_code": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }
    if universe_mode == "core10-dynamic40":
        p = REPORTS / f"phase118_core10_dynamic40_pipeline_{day_stamp}.json"
    else:
        p = REPORTS / f"phase115_am_pm_shadow_pipeline_{day_stamp}.json"
    if p.is_file():
        out.update(json.loads(p.read_text(encoding="utf-8")))
    return out


def run_phase118(day_stamp: str, *, generate_features: bool = False) -> dict[str, Any]:
    cmd = [sys.executable, str(PHASE118), "--day-stamp", day_stamp]
    if generate_features:
        cmd.append("--generate-features")
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    out: dict[str, Any] = {
        "exit_code": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }
    p118 = REPORTS / f"phase118_core10_dynamic40_pipeline_{day_stamp}.json"
    if p118.is_file():
        out.update(json.loads(p118.read_text(encoding="utf-8")))
    return out


def run_phase113(day_stamp: str) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(PHASE113), "--day-stamp", day_stamp],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    out: dict[str, Any] = {
        "exit_code": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }
    p113 = REPORTS / f"phase113_vol_liq_dynamic50_universe_{day_stamp}.json"
    if p113.is_file():
        out.update(json.loads(p113.read_text(encoding="utf-8")))
    return out


def run_phase109(day_stamp: str) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(PHASE109), "--day-stamp", day_stamp],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    out: dict[str, Any] = {
        "exit_code": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }
    p109 = REPORTS / f"phase109_opening_dynamic50_universe_{day_stamp}.json"
    if p109.is_file():
        out.update(json.loads(p109.read_text(encoding="utf-8")))
    return out


def check_runner_load(trial_csv: Path) -> dict[str, Any]:
    _bootstrap()
    from storage.symbol_sources import load_symbols

    syms = load_symbols(universe=trial_csv, native_root=NATIVE)
    return {
        "passed": 0 < len(syms) <= 50,
        "symbol_count": len(syms),
        "path": _rel(trial_csv),
    }


def shadow_live_commands(day_stamp: str, universe_csv_rel: str) -> dict[str, list[str]]:
    return {
        "preflight": [
            f"python kabu_native/scripts/run_phase99_shadow_universe_preflight.py --day-stamp {day_stamp}",
        ],
        "small_paper_shadow_live_dry_run": [
            "python kabu_native/scripts/check_small_paper_safety.py",
            (
                "python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source live "
                "--full-session --wait-until-session "
                f"--universe-csv {universe_csv_rel} "
                f"--config {SHADOW_PILOT_YAML} "
                "--poll-interval-sec 5"
            ),
        ],
        "shadow_runner_universe": [
            (
                "python kabu_native/scripts/run_shadow.py --watchlist-source universe "
                f"--universe-csv {universe_csv_rel} --dry-run"
            ),
        ],
        "notes": [
            "Do not overwrite universe_intraday_full.csv or production small_paper_pilot.yaml.",
            "PUSH register limit: 50 symbols.",
            "No auto-order; dry-run / shadow only.",
        ],
    }


def determine_verdict_phase105(
    *,
    build: dict[str, Any],
    runner: dict[str, Any],
    trial_exists: bool,
) -> tuple[str, str]:
    sc = build.get("success_criteria") or {}
    if build.get("exit_code") != 0:
        return "universe_build_failed", "build_dynamic_universe exited non-zero"
    if not trial_exists:
        return "universe_build_failed", "universe_dynamic_trial CSV missing"
    if not sc.get("met"):
        return "phase105_criteria_not_met", "Phase105 success_criteria.met is false"
    if not runner.get("passed"):
        return "runner_load_failed", "load_symbols failed for trial CSV"
    return "shadow_pipeline_ready", "Phase105 + runner OK"


def determine_verdict_opening(
    *,
    phase109: dict[str, Any],
    runner: dict[str, Any],
    universe_exists: bool,
) -> tuple[str, str]:
    verdict = phase109.get("verdict", "")
    if phase109.get("exit_code", 1) != 0:
        return "runner_load_failed", "phase109 script failed"
    if verdict == "missing_opening_dynamic50_source":
        return "missing_opening_dynamic50_source", "opening_dynamic50_0905 CSV missing"
    if verdict == "opening_dynamic50_incomplete":
        return "opening_dynamic50_incomplete", "; ".join(phase109.get("verdict_notes", []))
    if not universe_exists:
        return "opening_dynamic50_incomplete", "universe_opening_dynamic50 CSV missing"
    if not runner.get("passed") or runner.get("symbol_count") != 50:
        return "runner_load_failed", f"runner symbol_count={runner.get('symbol_count')}"
    if verdict == "opening_dynamic50_universe_ready":
        return "shadow_pipeline_ready", "Phase109 opening dynamic50 wired for shadow live"
    return "runner_load_failed", f"phase109 verdict={verdict}"


def determine_verdict_vol_liq(
    *,
    phase113: dict[str, Any],
    runner: dict[str, Any],
    universe_exists: bool,
) -> tuple[str, str]:
    verdict = phase113.get("verdict", "")
    if phase113.get("exit_code", 1) != 0:
        return "runner_load_failed", "phase113 script failed"
    if verdict == "missing_daily_features":
        return "missing_daily_features", "features_YYYYMMDD.csv missing"
    if verdict == "insufficient_valid_features":
        return "insufficient_valid_features", "; ".join(phase113.get("verdict_notes", []))
    if not universe_exists:
        return "insufficient_valid_features", "universe_vol_liq_dynamic50 CSV missing"
    if not runner.get("passed") or runner.get("symbol_count") != 50:
        return "runner_load_failed", f"runner symbol_count={runner.get('symbol_count')}"
    if verdict == "vol_liq_dynamic50_ready":
        return "shadow_pipeline_ready", "Phase113 vol_liq dynamic50 wired for shadow live"
    return "runner_load_failed", f"phase113 verdict={verdict}"


def determine_verdict_am_pm(
    *,
    phase115: dict[str, Any],
    am_runner: dict[str, Any],
    pm_runner: dict[str, Any],
    am_exists: bool,
    pm_exists: bool,
) -> tuple[str, str]:
    verdict = phase115.get("verdict", "")
    if phase115.get("exit_code", 1) != 0:
        return "runner_load_failed", "phase115 script failed"
    if verdict == "am_pm_universe_incomplete":
        return "am_pm_universe_incomplete", "; ".join(phase115.get("verdict_notes", []))
    if not am_exists or not pm_exists:
        return "am_pm_universe_incomplete", "universe_am or universe_pm CSV missing"
    if not am_runner.get("passed") or not pm_runner.get("passed"):
        return "runner_load_failed", (
            f"am={am_runner.get('symbol_count')} pm={pm_runner.get('symbol_count')}"
        )
    if verdict == "am_pm_shadow_pipeline_ready":
        return "shadow_pipeline_ready", "Phase115 AM/PM wired for shadow live"
    return "runner_load_failed", f"phase115 verdict={verdict}"


def determine_verdict_core10(
    *,
    phase118: dict[str, Any],
    am_runner: dict[str, Any],
    pm_runner: dict[str, Any],
    am_exists: bool,
    pm_exists: bool,
) -> tuple[str, str]:
    verdict = phase118.get("verdict", "")
    if phase118.get("exit_code", 1) != 0:
        return "runner_load_failed", "phase118 script failed"
    if verdict == "core_symbol_source_missing":
        return "core_symbol_source_missing", "; ".join(phase118.get("verdict_notes", []))
    if verdict == "core_limit_not_enforced":
        return "core_limit_not_enforced", "; ".join(phase118.get("verdict_notes", []))
    if not am_exists or not pm_exists:
        return "core_symbol_source_missing", "universe_core10_dynamic40 AM/PM CSV missing"
    if not am_runner.get("passed") or not pm_runner.get("passed"):
        return "runner_load_failed", (
            f"am={am_runner.get('symbol_count')} pm={pm_runner.get('symbol_count')}"
        )
    if verdict == "core10_dynamic40_pipeline_ready":
        return "shadow_pipeline_ready", "Phase118 Core10+Dynamic40 wired for shadow live"
    return "runner_load_failed", f"phase118 verdict={verdict}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 106 shadow live pipeline wiring")
    parser.add_argument("--day-stamp", default=None)
    parser.add_argument(
        "--universe-mode",
        choices=(
            "phase105",
            "opening-dynamic50",
            "vol-liq-dynamic50",
            "am-pm-dynamic50",
            "core10-dynamic40",
        ),
        default="phase105",
        help=(
            "phase105 | opening-dynamic50 (109) | vol-liq-dynamic50 (113) | "
            "am-pm-dynamic50 (115) | core10-dynamic40 (118)"
        ),
    )
    parser.add_argument(
        "--board-validate",
        action="store_true",
        help="phase105 only: build with --board-mode validate",
    )
    parser.add_argument("--skip-build", action="store_true", help="Only check existing artifacts")
    args = parser.parse_args()

    _bootstrap()
    from universe.day_stamp import normalize_day_stamp

    day_stamp = (
        normalize_day_stamp(args.day_stamp)
        if args.day_stamp
        else datetime.now(JST).strftime("%Y%m%d")
    )
    mode = args.universe_mode

    if mode == "core10-dynamic40":
        am_csv = REPORTS / f"universe_core10_dynamic40_am_{day_stamp}.csv"
        pm_csv = REPORTS / f"universe_core10_dynamic40_pm_{day_stamp}.csv"
        phase118: dict[str, Any] = {}
        if not args.skip_build:
            phase118 = run_phase118(day_stamp)
        else:
            p118 = REPORTS / f"phase118_core10_dynamic40_pipeline_{day_stamp}.json"
            if p118.is_file():
                phase118 = json.loads(p118.read_text(encoding="utf-8"))
                phase118["exit_code"] = 0

        am_runner = check_runner_load(am_csv) if am_csv.is_file() else {"passed": False, "symbol_count": 0}
        pm_runner = check_runner_load(pm_csv) if pm_csv.is_file() else {"passed": False, "symbol_count": 0}
        verdict, detail = determine_verdict_core10(
            phase118=phase118,
            am_runner=am_runner,
            pm_runner=pm_runner,
            am_exists=am_csv.is_file(),
            pm_exists=pm_csv.is_file(),
        )
        report = {
            "phase": 106,
            "day_stamp": day_stamp,
            "universe_mode": mode,
            "verdict": verdict,
            "verdict_detail": detail,
            "universe_am_csv": _rel(am_csv),
            "universe_pm_csv": _rel(pm_csv),
            "phase118_summary": {
                "verdict": phase118.get("verdict"),
                "core10_diagnosis": phase118.get("core10_diagnosis"),
                "build": phase118.get("build"),
                "shadow_live_commands": phase118.get("shadow_live_commands"),
            },
            "runner_check": {"am": am_runner, "pm": pm_runner},
            "shadow_live_commands": phase118.get("shadow_live_commands") or {},
            "verdict_options": {
                "A": "shadow_pipeline_ready / core10_dynamic40_pipeline_ready",
                "B": "core_symbol_source_missing",
                "C": "core_limit_not_enforced",
                "D": "runner_load_failed",
            },
        }
        out_json = REPORTS / f"phase106_shadow_live_pipeline_{day_stamp}.json"
        out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "verdict": verdict,
                    "universe_mode": mode,
                    "am_csv": _rel(am_csv),
                    "pm_csv": _rel(pm_csv),
                    "am_symbols": am_runner.get("symbol_count"),
                    "pm_symbols": pm_runner.get("symbol_count"),
                },
                ensure_ascii=True,
            )
        )
        return 0 if verdict == "shadow_pipeline_ready" else 1

    if mode == "am-pm-dynamic50":
        am_csv = REPORTS / f"universe_am_dynamic50_{day_stamp}.csv"
        pm_csv = REPORTS / f"universe_pm_dynamic50_{day_stamp}.csv"
        am114 = REPORTS / f"phase114_am_universe_dynamic50_{day_stamp}.csv"
        phase114: dict[str, Any] = {}
        phase115: dict[str, Any] = {}
        if not args.skip_build:
            if not am114.is_file():
                phase114 = run_phase114(day_stamp)
            phase115 = run_phase115(day_stamp)
        else:
            p115 = REPORTS / f"phase115_am_pm_shadow_pipeline_{day_stamp}.json"
            if p115.is_file():
                phase115 = json.loads(p115.read_text(encoding="utf-8"))
                phase115["exit_code"] = 0

        am_runner = check_runner_load(am_csv) if am_csv.is_file() else {"passed": False, "symbol_count": 0}
        pm_runner = check_runner_load(pm_csv) if pm_csv.is_file() else {"passed": False, "symbol_count": 0}
        verdict, detail = determine_verdict_am_pm(
            phase115=phase115,
            am_runner=am_runner,
            pm_runner=pm_runner,
            am_exists=am_csv.is_file(),
            pm_exists=pm_csv.is_file(),
        )
        report = {
            "phase": 106,
            "day_stamp": day_stamp,
            "universe_mode": mode,
            "verdict": verdict,
            "verdict_detail": detail,
            "universe_am_csv": _rel(am_csv),
            "universe_pm_csv": _rel(pm_csv),
            "phase114_summary": {
                "exit_code": phase114.get("exit_code"),
                "verdict": (phase114.get("phase114") or {}).get("verdict"),
            },
            "phase115_summary": {
                "verdict": phase115.get("verdict"),
                "build": phase115.get("build"),
                "shadow_live_commands": phase115.get("shadow_live_commands"),
            },
            "runner_check": {"am": am_runner, "pm": pm_runner},
            "shadow_live_commands": phase115.get("shadow_live_commands") or {},
            "verdict_options": {
                "A": "shadow_pipeline_ready / am_pm_shadow_pipeline_ready",
                "B": "session_range_support_missing",
                "C": "am_pm_universe_incomplete",
                "D": "runner_load_failed",
            },
        }
        out_json = REPORTS / f"phase106_shadow_live_pipeline_{day_stamp}.json"
        out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "verdict": verdict,
                    "universe_mode": mode,
                    "am_csv": _rel(am_csv),
                    "pm_csv": _rel(pm_csv),
                    "am_symbols": am_runner.get("symbol_count"),
                    "pm_symbols": pm_runner.get("symbol_count"),
                },
                ensure_ascii=True,
            )
        )
        return 0 if verdict == "shadow_pipeline_ready" else 1

    if mode == "vol-liq-dynamic50":
        universe_csv = REPORTS / f"universe_vol_liq_dynamic50_{day_stamp}.csv"
        features_src = REPORTS / f"features_{day_stamp}.csv"
        phase113: dict[str, Any] = {}
        if not args.skip_build:
            phase113 = run_phase113(day_stamp)
        else:
            p113 = REPORTS / f"phase113_vol_liq_dynamic50_universe_{day_stamp}.json"
            if p113.is_file():
                phase113 = json.loads(p113.read_text(encoding="utf-8"))
                phase113["exit_code"] = 0

        runner = check_runner_load(universe_csv) if universe_csv.is_file() else {"passed": False, "symbol_count": 0}
        verdict, detail = determine_verdict_vol_liq(
            phase113=phase113,
            runner=runner,
            universe_exists=universe_csv.is_file(),
        )
        universe_rel = _rel(universe_csv)
        report = {
            "phase": 106,
            "day_stamp": day_stamp,
            "universe_mode": mode,
            "verdict": verdict,
            "verdict_detail": detail,
            "features_csv": _rel(features_src),
            "universe_csv": universe_rel,
            "phase113_summary": {
                "verdict": phase113.get("verdict"),
                "diagnostics": phase113.get("diagnostics"),
            },
            "runner_check": runner,
            "shadow_live_commands": shadow_live_commands(day_stamp, universe_rel),
            "verdict_options": {
                "A": "shadow_pipeline_ready / vol_liq_dynamic50_ready",
                "B": "missing_daily_features",
                "C": "insufficient_valid_features",
                "D": "runner_load_failed",
            },
        }
        out_json = REPORTS / f"phase106_shadow_live_pipeline_{day_stamp}.json"
        out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "verdict": verdict,
                    "universe_mode": mode,
                    "universe_csv": universe_rel,
                    "symbol_count": runner.get("symbol_count"),
                },
                ensure_ascii=True,
            )
        )
        return 0 if verdict == "shadow_pipeline_ready" else 1

    if mode == "opening-dynamic50":
        universe_csv = REPORTS / f"universe_opening_dynamic50_{day_stamp}.csv"
        opening_src = REPORTS / f"opening_dynamic50_0905_{day_stamp}.csv"
        phase109: dict[str, Any] = {}
        if not args.skip_build:
            phase109 = run_phase109(day_stamp)
        else:
            p109 = REPORTS / f"phase109_opening_dynamic50_universe_{day_stamp}.json"
            if p109.is_file():
                phase109 = json.loads(p109.read_text(encoding="utf-8"))
                phase109["exit_code"] = 0

        runner = check_runner_load(universe_csv) if universe_csv.is_file() else {"passed": False, "symbol_count": 0}
        verdict, detail = determine_verdict_opening(
            phase109=phase109,
            runner=runner,
            universe_exists=universe_csv.is_file(),
        )
        universe_rel = _rel(universe_csv)
        report: dict[str, Any] = {
            "phase": 106,
            "day_stamp": day_stamp,
            "universe_mode": mode,
            "verdict": verdict,
            "verdict_detail": detail,
            "opening_dynamic50_0905_csv": _rel(opening_src),
            "universe_csv": universe_rel,
            "phase109_summary": {
                "verdict": phase109.get("verdict"),
                "policy_0905_fixed": phase109.get("policy_0905_fixed"),
            },
            "runner_check": runner,
            "shadow_live_commands": shadow_live_commands(day_stamp, universe_rel),
            "verdict_options": {
                "A": "shadow_pipeline_ready / opening_dynamic50_universe_ready",
                "B": "missing_opening_dynamic50_source",
                "C": "runner_load_failed",
                "D": "opening_dynamic50_incomplete",
            },
        }
        out_json = REPORTS / f"phase106_shadow_live_pipeline_{day_stamp}.json"
        out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "verdict": verdict,
                    "universe_mode": mode,
                    "universe_csv": universe_rel,
                    "symbol_count": runner.get("symbol_count"),
                },
                ensure_ascii=True,
            )
        )
        return 0 if verdict == "shadow_pipeline_ready" else 1

    board_mode = "validate" if args.board_validate else "none"
    trial_csv = REPORTS / f"universe_dynamic_trial_{day_stamp}.csv"
    phase105_json = REPORTS / f"phase105_register_limit_aware_universe_{day_stamp}.json"

    build: dict[str, Any] = {}
    if not args.skip_build:
        build = run_build(day_stamp, board_mode)
    elif phase105_json.is_file():
        build = json.loads(phase105_json.read_text(encoding="utf-8"))
        build["exit_code"] = 0

    runner = check_runner_load(trial_csv) if trial_csv.is_file() else {"passed": False, "symbol_count": 0}
    verdict, detail = determine_verdict_phase105(
        build=build,
        runner=runner,
        trial_exists=trial_csv.is_file(),
    )

    universe_rel = _rel(trial_csv)
    report = {
        "phase": 106,
        "day_stamp": day_stamp,
        "universe_mode": mode,
        "verdict": verdict,
        "verdict_detail": detail,
        "board_mode_used": board_mode,
        "universe_trial_csv": universe_rel,
        "build_summary": {
            "exit_code": build.get("exit_code"),
            "verdict": build.get("verdict"),
            "success_criteria": build.get("success_criteria"),
        },
        "runner_check": runner,
        "shadow_live_commands": shadow_live_commands(day_stamp, universe_rel),
    }
    out_json = REPORTS / f"phase106_shadow_live_pipeline_{day_stamp}.json"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"verdict": verdict, "universe_mode": mode, "universe_csv": universe_rel, "symbol_count": runner.get("symbol_count")},
            ensure_ascii=True,
        )
    )
    return 0 if verdict == "shadow_pipeline_ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
