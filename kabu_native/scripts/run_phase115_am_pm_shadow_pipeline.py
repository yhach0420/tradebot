#!/usr/bin/env python3
"""Phase 115: Wire Phase114 AM/PM universes to shadow live pipeline."""

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
PHASE114 = NATIVE / "scripts" / "run_phase114_am_pm_universe_design.py"
SHADOW_PILOT_YAML = "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"
PUSH_LIMIT = 50


def _bootstrap() -> None:
    for p in (NATIVE / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def runner_load_check(universe_csv: Path) -> dict[str, Any]:
    _bootstrap()
    from storage.symbol_sources import load_symbols

    syms = load_symbols(universe=universe_csv, native_root=NATIVE) if universe_csv.is_file() else []
    return {
        "passed": len(syms) == PUSH_LIMIT,
        "symbol_count": len(syms),
        "load_symbols_ok": len(syms) > 0,
        "path": _rel(universe_csv),
    }


def run_phase114(day_stamp: str) -> dict[str, Any]:
    """Run Phase114; generate features via Phase113 only when features CSV is missing."""
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
    out: dict[str, Any] = {"exit_code": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}
    p114 = REPORTS / f"phase114_am_pm_universe_design.json"
    if p114.is_file():
        out["phase114"] = json.loads(p114.read_text(encoding="utf-8"))
    return out


def build_phase115_core10(day_stamp: str, *, generate_features: bool) -> dict[str, Any]:
    from datetime import date

    from universe.core10_dynamic40_shadow import build_pipeline_report
    from universe.daily_features import load_features_csv
    from universe.dynamic_build import load_dynamic_config, resolve_symbol_master

    trade_d = date(int(day_stamp[:4]), int(day_stamp[4:6]), int(day_stamp[6:8]))
    feat = REPORTS / f"features_{day_stamp}.csv"
    if generate_features and not feat.is_file():
        run_phase114(day_stamp)  # triggers phase113 via phase114 if needed
    features = load_features_csv(feat) if feat.is_file() else []
    push_dir = NATIVE / "data" / "push_jsonl" / trade_d.isoformat()

    cfg = load_dynamic_config(NATIVE / "configs" / "universe_dynamic_trial.yaml")
    _, entries = resolve_symbol_master(ROOT, cfg.symbol_master_paths)
    symbol_meta: dict[str, Any] = {}
    for e in entries:
        sym = f"{e.parsed.code}.T"
        symbol_meta[sym] = {
            "exchange": e.parsed.exchange,
            "symbol_key": e.parsed.symbol_key,
            "market": e.market,
        }

    core_report = build_pipeline_report(
        repo_root=ROOT,
        reports_dir=REPORTS,
        day_stamp=day_stamp,
        trade_date=trade_d,
        feature_rows=features,
        push_day_dir=push_dir,
        symbol_meta=symbol_meta,
        am_runner_fn=runner_load_check,
        pm_runner_fn=runner_load_check,
    )

    verdict = core_report["verdict"]
    if verdict == "core10_dynamic40_pipeline_ready":
        mapped_verdict = "am_pm_shadow_pipeline_ready"
    else:
        mapped_verdict = verdict

    return {
        "phase": 115,
        "universe_mode": "core10-dynamic40",
        "day_stamp": day_stamp,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": mapped_verdict,
        "phase118_verdict": verdict,
        "verdict_notes": core_report.get("verdict_notes"),
        "verdict_options": {
            "A": "am_pm_shadow_pipeline_ready (core10-dynamic40)",
            "B": "core_symbol_source_missing",
            "C": "core_limit_not_enforced",
            "D": "runner_load_failed",
        },
        "core10_diagnosis": core_report.get("core10_diagnosis"),
        "build": core_report.get("build"),
        "universe_validation": core_report.get("universe_validation"),
        "runner_check": core_report.get("runner_check"),
        "shadow_live_commands": core_report.get("shadow_live_commands"),
        "outputs": {
            "phase115_json": _rel(REPORTS / f"phase115_am_pm_shadow_pipeline_{day_stamp}.json"),
            "phase118_json": _rel(REPORTS / f"phase118_core10_dynamic40_pipeline_{day_stamp}.json"),
            "universe_am_csv": core_report["outputs"]["universe_am_csv"],
            "universe_pm_csv": core_report["outputs"]["universe_pm_csv"],
            "phase115_runner_check_json": _rel(REPORTS / f"phase115_runner_check_{day_stamp}.json"),
        },
        "phase116_session_policy": {
            "am": {"session_start": "09:03", "session_end": "11:25", "entry_stop": "11:20", "force_close": "11:25"},
            "pm": {"session_start": "12:33", "session_end": "15:23", "entry_stop": "15:18", "force_close": "15:23"},
        },
        "constraints": [
            "no_production_pilot_yaml_change",
            "no_overwrite_universe_intraday_full",
            "no_auto_order",
            "shadow_dry_run_only",
            "no_pf_evaluation",
        ],
    }


def build_phase115(day_stamp: str, *, run114_if_missing: bool) -> dict[str, Any]:
    _bootstrap()
    from universe.am_pm_shadow_universe import (
        AM_SOURCE_BUCKET,
        PM_SOURCE_BUCKET,
        build_from_phase114,
        determine_verdict,
        limit_diagnostics_path,
        load_limit_warnings,
        shadow_live_commands,
        universe_am_path,
        universe_pm_path,
        validate_runner_universe,
    )

    am_in = REPORTS / f"phase114_am_universe_dynamic50_{day_stamp}.csv"
    if run114_if_missing and (not am_in.is_file()):
        run_phase114(day_stamp)

    build = build_from_phase114(REPORTS, day_stamp)
    am_csv = universe_am_path(REPORTS, day_stamp)
    pm_csv = universe_pm_path(REPORTS, day_stamp)

    am_val = validate_runner_universe(am_csv, expected_bucket=AM_SOURCE_BUCKET, expected_session="am")
    pm_val = validate_runner_universe(pm_csv, expected_bucket=PM_SOURCE_BUCKET, expected_session="pm")

    am_runner = runner_load_check(am_csv)
    pm_runner = runner_load_check(pm_csv)

    pm_syms = set()
    if pm_csv.is_file():
        import csv

        with pm_csv.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                sym = str(row.get("symbol") or "").strip()
                if sym:
                    pm_syms.add(sym if sym.endswith(".T") else f"{sym}.T")

    limit_warnings = load_limit_warnings(limit_diagnostics_path(REPORTS, day_stamp), pm_syms)
    am_syms = set()
    if am_csv.is_file():
        import csv

        with am_csv.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                sym = str(row.get("symbol") or "").strip()
                if sym:
                    am_syms.add(sym if sym.endswith(".T") else f"{sym}.T")
    limit_warnings_am = load_limit_warnings(limit_diagnostics_path(REPORTS, day_stamp), am_syms)

    verdict, verdict_notes = determine_verdict(
        build=build,
        am_val=am_val,
        pm_val=pm_val,
        am_runner_ok=bool(am_runner.get("passed")),
        pm_runner_ok=bool(pm_runner.get("passed")),
    )

    am_rel = _rel(am_csv)
    pm_rel = _rel(pm_csv)
    commands = shadow_live_commands(day_stamp, am_csv_rel=am_rel, pm_csv_rel=pm_rel, pilot_yaml=SHADOW_PILOT_YAML)

    return {
        "phase": 115,
        "day_stamp": day_stamp,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": verdict,
        "verdict_notes": verdict_notes,
        "verdict_options": {
            "A": "am_pm_shadow_pipeline_ready",
            "B": "session_range_support_missing",
            "C": "am_pm_universe_incomplete",
            "D": "runner_load_failed",
        },
        "build": build,
        "universe_validation": {"am": am_val, "pm": pm_val},
        "runner_check": {"am": am_runner, "pm": pm_runner},
        "limit_status_warnings": {
            "pm_universe": limit_warnings,
            "am_universe": limit_warnings_am,
            "exclusion_applied": False,
            "note": "Phase114 shadow diagnostics only",
        },
        "shadow_live_commands": commands,
        "outputs": {
            "phase115_json": _rel(REPORTS / f"phase115_am_pm_shadow_pipeline_{day_stamp}.json"),
            "universe_am_csv": am_rel,
            "universe_pm_csv": pm_rel,
            "phase115_runner_check_json": _rel(REPORTS / f"phase115_runner_check_{day_stamp}.json"),
        },
        "phase116_session_policy": {
            "am": {"session_start": "09:03", "session_end": "11:25", "entry_stop": "11:20", "force_close": "11:25"},
            "pm": {"session_start": "12:33", "session_end": "15:23", "entry_stop": "15:18", "force_close": "15:23"},
        },
        "constraints": [
            "no_production_pilot_yaml_change",
            "no_overwrite_universe_intraday_full",
            "no_auto_order",
            "shadow_dry_run_only",
            "no_pf_evaluation",
        ],
    }


def main() -> int:
    _bootstrap()
    from universe.day_stamp import normalize_day_stamp

    parser = argparse.ArgumentParser(description="Phase 115 AM/PM shadow pipeline")
    parser.add_argument("--day-stamp", default=None, help="8-digit JST trade date (e.g. 20260521)")
    parser.add_argument(
        "--universe-mode",
        choices=("am-pm-dynamic50", "core10-dynamic40"),
        default="am-pm-dynamic50",
    )
    parser.add_argument("--run-phase114-if-missing", action="store_true", default=True)
    parser.add_argument("--no-run-phase114", action="store_true")
    parser.add_argument("--generate-features", action="store_true")
    args = parser.parse_args()

    day_stamp = (
        normalize_day_stamp(args.day_stamp)
        if args.day_stamp
        else datetime.now(JST).strftime("%Y%m%d")
    )
    if args.universe_mode == "core10-dynamic40":
        report = build_phase115_core10(day_stamp, generate_features=args.generate_features)
        p118 = REPORTS / f"phase118_core10_dynamic40_pipeline_{day_stamp}.json"
        p118.write_text(
            json.dumps(
                {
                    **report,
                    "phase": 118,
                    "verdict": report.get("phase118_verdict"),
                    "generated_at": report.get("generated_at"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    else:
        report = build_phase115(
            day_stamp,
            run114_if_missing=args.run_phase114_if_missing and not args.no_run_phase114,
        )

    out_main = REPORTS / f"phase115_am_pm_shadow_pipeline_{day_stamp}.json"
    out_runner = REPORTS / f"phase115_runner_check_{day_stamp}.json"
    out_main.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_runner.write_text(
        json.dumps(
            {
                "phase": 115,
                "day_stamp": day_stamp,
                "verdict": report["verdict"],
                "runner_check": report["runner_check"],
                "universe_validation": report["universe_validation"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "am_csv": report["outputs"]["universe_am_csv"],
                "pm_csv": report["outputs"]["universe_pm_csv"],
            },
            ensure_ascii=True,
        )
    )
    ok_verdicts = ("am_pm_shadow_pipeline_ready",)
    return 0 if report["verdict"] in ok_verdicts else 1


if __name__ == "__main__":
    raise SystemExit(main())
