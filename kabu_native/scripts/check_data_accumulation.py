#!/usr/bin/env python3
"""
Phase 42: Daily check — intraday CSV / PUSH JSONL coverage for pilot sample growth.

例::
    python kabu_native/scripts/check_data_accumulation.py \\
        --universe kabu_native/data/universe/universe_intraday_full.csv
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    for p in (native_root / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo_root, native_root


def main() -> int:
    repo_root, native_root = _bootstrap()

    from storage.data_accumulation_report import (
        build_data_accumulation_status,
        write_accumulation_reports,
    )
    from storage.symbol_sources import load_symbols

    parser = argparse.ArgumentParser(description="Check kabu_native data accumulation")
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--universe", type=Path, default=None)
    parser.add_argument("--morning-screen", type=Path, default=None)
    parser.add_argument("--symbols", default=None)
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=native_root / "results" / "reports",
    )
    args = parser.parse_args()

    trade_date = args.trade_date or datetime.now(JST).date().isoformat()
    sym_list = load_symbols(
        universe=args.universe,
        morning_screen=args.morning_screen,
        symbols=args.symbols.split(",") if args.symbols else None,
        native_root=native_root,
    )

    log = logging.getLogger("check_data_accumulation")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    report = build_data_accumulation_status(
        native_root=native_root,
        repo_root=repo_root,
        trade_date=trade_date,
        expected_symbols=sym_list,
    )
    reports_dir = args.reports_dir
    if not reports_dir.is_absolute():
        reports_dir = native_root / reports_dir
    json_path, csv_path = write_accumulation_reports(report, reports_dir=reports_dir)

    log.info(
        "coverage=%.1f%% intraday=%s/%s push=%s/%s native_days=%s",
        (report.get("symbol_coverage_ratio") or 0) * 100,
        report.get("intraday_csv_present"),
        report.get("expected_symbol_count"),
        report.get("push_jsonl_present"),
        report.get("expected_symbol_count"),
        report.get("kabu_native_trading_day_count"),
    )
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")
    print(
        f"trade_date={trade_date} "
        f"intraday_valid={report.get('intraday_csv_valid')}/{report.get('expected_symbol_count')} "
        f"native_oos_days={report.get('kabu_native_trading_day_count')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
