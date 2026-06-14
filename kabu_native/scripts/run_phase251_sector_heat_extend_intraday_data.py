#!/usr/bin/env python3
"""
Phase251-SectorHeat-Extend-Intraday-Data (review only)

Backfill intraday_1m for 20260519+ and rerun Phase246/249 so Phase249 shadow simulation
becomes evaluable. Observation only — no Runtime / Universe / Entry / YAML changes.

Output:
  kabu_native/results/reports/phase251_sector_heat_data_extension_summary.json
  kabu_native/results/reports/phase251_intraday_data_gap_report.csv
  kabu_native/results/reports/phase251_phase249_rerun_summary.json
  kabu_native/results/reports/phase251_sector_heat_report.md
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REPORTS = REPO / "kabu_native" / "results" / "reports"


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase251 sector heat intraday data extension")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    parser.add_argument("--min-day", type=str, default="20260519")
    parser.add_argument("--max-day", type=str, default="20260612")
    parser.add_argument(
        "--skip-backfill",
        action="store_true",
        help="Gap report + Phase246/249 rerun only (no Yahoo fetch)",
    )
    parser.add_argument("--backfill-delay-sec", type=float, default=0.15)
    args = parser.parse_args()

    _bootstrap()
    from research.market_sector_heat_extend_intraday import MarketSectorHeatExtendIntradayData

    t0 = time.monotonic()
    job = MarketSectorHeatExtendIntradayData(
        repo_root=REPO,
        reports_dir=args.reports_dir,
        min_day=args.min_day,
        max_day=args.max_day,
        skip_backfill=args.skip_backfill,
        backfill_delay_sec=args.backfill_delay_sec,
    )
    result = job.run()
    paths = job.write_outputs(result)

    p249 = result.get("phase249_rerun") or {}
    coverage = p249.get("coverage") or {}
    checks = p249.get("checks") or {}
    backfill = result.get("backfill") or {}

    print(
        f"phase251_sector_heat_extend wall_runtime_sec={round(time.monotonic() - t0, 1)}",
        flush=True,
    )
    print("\n=== Phase251 Sector Heat Intraday Extension ===", flush=True)
    print(f"target: {args.min_day}..{args.max_day}", flush=True)
    print(f"backfill skipped: {backfill.get('skipped')}", flush=True)
    print(f"cache_saved: {backfill.get('cache_saved', 'n/a')}", flush=True)
    gap_after = result.get("gap_summary_after") or {}
    print(
        f"intraday complete days: {gap_after.get('complete_day_count')} "
        f"({gap_after.get('first_complete_day')}..{gap_after.get('last_complete_day')})",
        flush=True,
    )
    p246 = result.get("phase246_rerun") or {}
    after = p246.get("top3_validation_range_after") or {}
    print(
        f"phase246 top3 validation: {after.get('first_day')}..{after.get('last_day')} "
        f"({after.get('day_count')} days)",
        flush=True,
    )
    print(f"phase249 simulated days: {coverage.get('simulated_day_count')}", flush=True)
    for key, ok in checks.items():
        print(f"phase249 check {key}: {ok}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
