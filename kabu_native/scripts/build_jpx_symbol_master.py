#!/usr/bin/env python3
"""
Build JPX symbol master CSVs (Prime / Standard / Growth tradable ordinary shares).

例::
    python kabu_native/scripts/build_jpx_symbol_master.py
    python kabu_native/scripts/build_jpx_symbol_master.py --input data/jpx/raw/listed_issues.xlsx
    python kabu_native/scripts/build_jpx_symbol_master.py --allow-sample
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

TRADABLE_PRODUCTION_MIN = 500


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    for p in (native_root / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo_root, native_root


def _rel(repo_root: Path, p: Path) -> str:
    try:
        return str(p.relative_to(repo_root))
    except ValueError:
        return str(p)


def main() -> int:
    repo_root, native_root = _bootstrap()

    from jpx.symbol_master import (
        RAW_SEARCH_HINT,
        TRADABLE_PRODUCTION_MIN,
        discover_raw_candidates,
        parse_jpx_listed_file,
        resolve_raw_input,
        write_all_outputs,
    )

    parser = argparse.ArgumentParser(description="Build JPX tradable symbol master CSVs")
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="JPX listed issues export (xlsx/xls/csv). Default: data/jpx/raw/listed_issues.xlsx",
    )
    parser.add_argument(
        "--allow-sample",
        action="store_true",
        help="Allow jpx_listed_issues_sample.csv when official file missing (dev only)",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=native_root / "results" / "reports",
    )
    parser.add_argument("--date-stamp", default=None)
    args = parser.parse_args()

    day_stamp = args.date_stamp or datetime.now(JST).strftime("%Y%m%d")
    reports_dir = args.reports_dir if args.reports_dir.is_absolute() else repo_root / args.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)

    resolution = resolve_raw_input(
        repo_root,
        explicit=args.input,
        allow_sample=args.allow_sample,
    )
    raw_dir = repo_root / "data" / "jpx" / "raw"
    candidates_info = [
        {
            "name": c.name,
            "mtime": c.mtime,
            "is_sample": c.is_sample,
            "is_official_name": c.is_official_name,
        }
        for c in resolution.candidates
    ]

    if not resolution.raw_file_found or resolution.path is None:
        payload = {
            "phase": 100,
            "verdict": "need_user_to_download_jpx_file",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "raw_file_found": False,
            "raw_file_path": None,
            "sample_only": False,
            "message": resolution.message,
            "placement_hint": RAW_SEARCH_HINT,
            "expected_files": [
                "data/jpx/raw/listed_issues.xlsx",
                "data/jpx/raw/listed_issues.xls",
                "data/jpx/raw/listed_issues.csv",
            ],
            "raw_dir_listing": [p.name for p in discover_raw_candidates(raw_dir)],
            "raw_candidates": candidates_info,
            "tradable_symbol_count": 0,
            "ready_for_dynamic_universe_build": False,
            "outputs": {},
        }
        _write_reports(reports_dir, day_stamp, payload)
        print(json.dumps(payload, ensure_ascii=True))
        return 0

    input_path = resolution.path
    result = parse_jpx_listed_file(input_path)
    output_paths: dict[str, str] = {}

    if result.verdict == "tradable_symbol_master_ready":
        output_paths = write_all_outputs(repo_root, result)

    verdict = result.verdict
    if result.verdict == "parser_needs_column_mapping":
        verdict = "parser_fix_required"
    elif resolution.sample_only or result.sample_only:
        verdict = "sample_master_only"
    elif result.tradable_symbol_count < TRADABLE_PRODUCTION_MIN:
        verdict = "sample_master_only"
        result.sample_or_incomplete_master_warning = True
    elif result.verdict == "tradable_symbol_master_ready":
        verdict = "full_jpx_master_ready"

    ready_dyn = (
        verdict == "full_jpx_master_ready"
        and result.tradable_symbol_count >= TRADABLE_PRODUCTION_MIN
        and not result.sample_only
    )

    payload = {
        "phase": 100,
        "verdict": verdict,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "raw_file_found": True,
        "raw_file_path": _rel(repo_root, input_path),
        "resolution_message": resolution.message,
        "sample_only": resolution.sample_only or result.sample_only,
        "sample_or_incomplete_master_warning": result.sample_or_incomplete_master_warning,
        "tradable_production_min": TRADABLE_PRODUCTION_MIN,
        "all_count": len(result.all_rows),
        "tradable_count": result.tradable_symbol_count,
        "prime_count": len(result.prime_rows),
        "standard_count": len(result.standard_rows),
        "growth_count": len(result.growth_rows),
        "market_distribution": result.market_distribution,
        "market_distribution_tradable": result.market_distribution_tradable,
        "excluded_reason_counts": result.excluded_market_counts,
        "optional_diagnostics": result.optional_diagnostics,
        "excel_detected_kind": result.excel_detected_kind,
        "excel_load_method": result.excel_load_method,
        "input_row_count": result.input_row_count,
        "missing_columns": result.missing_columns,
        "error": result.error,
        "outputs": output_paths,
        "output_paths": output_paths,
        "ready_for_dynamic_universe_build": ready_dyn,
        "raw_candidates": candidates_info,
    }
    _write_reports(reports_dir, day_stamp, payload)
    print(
        json.dumps(
            {
                "verdict": verdict,
                "tradable": result.tradable_symbol_count,
                "sample_only": payload["sample_only"],
                "outputs": output_paths,
            },
            ensure_ascii=True,
        )
    )
    return 0


def _write_reports(reports_dir: Path, day_stamp: str, payload: dict) -> None:
    p98 = reports_dir / f"phase98_jpx_symbol_master_build_{day_stamp}.json"
    p100 = reports_dir / f"phase100_jpx_master_setup_check_{day_stamp}.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    p100.write_text(text, encoding="utf-8")
    if payload.get("raw_file_found"):
        p98.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
