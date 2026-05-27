#!/usr/bin/env python3
"""
Phase 109: Wire Phase108 opening_dynamic50_0905 → shadow universe CSV + runner check.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "kabu_native"
REPORTS = NATIVE / "results" / "reports"


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


def runner_check(universe_csv: Path) -> dict[str, Any]:
    _bootstrap()
    from storage.symbol_sources import load_symbols
    from universe.opening_dynamic50_universe import validate_universe_csv

    val = validate_universe_csv(universe_csv)
    syms = load_symbols(universe=universe_csv, native_root=NATIVE) if universe_csv.is_file() else []

    passed = (
        val.get("passed")
        and len(syms) == 50
        and val.get("total_count") == 50
    )
    return {
        "passed": passed,
        "symbol_count": len(syms),
        "universe_validation": val,
        "universe_csv_path": _rel(universe_csv),
        "load_symbols_ok": len(syms) > 0,
    }


def determine_verdict(
    *,
    opening_exists: bool,
    universe_val: dict[str, Any],
    runner: dict[str, Any],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if not opening_exists:
        return "missing_opening_dynamic50_source", ["opening_dynamic50_0905 CSV not found"]

    if universe_val.get("total_count", 0) < 50 or universe_val.get("duplicate_count", 0) > 0:
        notes.append(
            f"total={universe_val.get('total_count')} dup={universe_val.get('duplicate_count')}"
        )
        if not universe_val.get("passed"):
            notes.extend(
                c["detail"] for c in universe_val.get("checks", []) if not c.get("passed")
            )
        return "opening_dynamic50_incomplete", notes

    if not runner.get("passed"):
        notes.append(f"runner symbol_count={runner.get('symbol_count')}")
        return "runner_load_failed", notes

    notes.append("50 symbols; opening_dynamic50 bucket; runner load OK")
    return "opening_dynamic50_universe_ready", notes


def build_phase109(
    day_stamp: str,
    *,
    reports_dir: Path = REPORTS,
) -> dict[str, Any]:
    _bootstrap()
    from universe.opening_dynamic50_universe import (
        build_universe_rows,
        load_opening_dynamic50_0905,
        opening_0905_path,
        universe_output_path,
        write_universe_csv,
        validate_universe_csv,
    )

    opening_path = opening_0905_path(reports_dir, day_stamp)
    universe_path = universe_output_path(reports_dir, day_stamp)
    opening_exists = opening_path.is_file()

    universe_rows: list[dict[str, Any]] = []
    if opening_exists:
        opening_rows = load_opening_dynamic50_0905(opening_path)
        universe_rows = build_universe_rows(opening_rows)
        write_universe_csv(universe_path, universe_rows)

    universe_val = validate_universe_csv(universe_path) if universe_path.is_file() else {"passed": False}
    runner = runner_check(universe_path) if universe_path.is_file() else {"passed": False, "symbol_count": 0}
    verdict, verdict_notes = determine_verdict(
        opening_exists=opening_exists,
        universe_val=universe_val,
        runner=runner,
    )

    policy = {
        "use_0905_fixed_dynamic50": True,
        "intraday_rotation_enabled": False,
        "churn_reference": "Phase108",
        "phase108_verdict": "use_0905_fixed_dynamic50",
        "note": "09:05 opening_dynamic50 fixed for PUSH; no 5-min register rotation in shadow",
    }

    report: dict[str, Any] = {
        "phase": 109,
        "day_stamp": day_stamp,
        "verdict": verdict,
        "verdict_notes": verdict_notes,
        "verdict_options": {
            "A": "opening_dynamic50_universe_ready",
            "B": "missing_opening_dynamic50_source",
            "C": "runner_load_failed",
            "D": "opening_dynamic50_incomplete",
        },
        "policy_0905_fixed": policy,
        "static27_used": False,
        "push_limit": 50,
        "inputs": {
            "opening_dynamic50_0905_csv": _rel(opening_path),
            "opening_dynamic50_0905_exists": opening_exists,
        },
        "outputs": {
            "universe_opening_dynamic50_csv": _rel(universe_path),
            "phase109_json": _rel(reports_dir / f"phase109_opening_dynamic50_universe_{day_stamp}.json"),
            "phase109_runner_check_json": _rel(reports_dir / f"phase109_runner_check_{day_stamp}.json"),
        },
        "universe_validation": universe_val,
        "runner_check": runner,
        "constraints_confirmed": [
            "no_production_pilot_yaml_change",
            "no_overwrite_universe_intraday_full",
            "no_entry_exit_quality_vol_liq_cap_change",
            "no_symbol_hardcode",
            "no_time_of_day_filter",
            "shadow_dry_run_only",
            "no_pf_evaluation",
        ],
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 109 opening dynamic50 universe wire-up")
    parser.add_argument("--day-stamp", default=None)
    args = parser.parse_args()

    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")
    report = build_phase109(day_stamp)

    json_main = REPORTS / f"phase109_opening_dynamic50_universe_{day_stamp}.json"
    json_runner = REPORTS / f"phase109_runner_check_{day_stamp}.json"
    json_main.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    json_runner.write_text(
        json.dumps(
            {
                "phase": 109,
                "day_stamp": day_stamp,
                "runner_check": report["runner_check"],
                "universe_validation": report["universe_validation"],
                "verdict": report["verdict"],
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
                "universe_csv": report["outputs"]["universe_opening_dynamic50_csv"],
                "symbol_count": report["runner_check"].get("symbol_count"),
            },
            ensure_ascii=True,
        )
    )
    return 0 if report["verdict"] == "opening_dynamic50_universe_ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
