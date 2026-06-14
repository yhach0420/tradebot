#!/usr/bin/env python3
"""
Phase255-SectorHeat-Forward-Shadow-Logger (review only)

Daily forward shadow logging for weak-sector exclusion universe patterns.
Observation only — no Runtime / Universe / Entry / YAML changes.

Output:
  kabu_native/results/reports/phase255_sector_heat_forward_shadow_universe_by_day.csv
  kabu_native/results/reports/phase255_sector_heat_forward_shadow_trade_by_day.csv
  kabu_native/results/reports/phase255_sector_heat_forward_shadow_summary.json
  kabu_native/results/reports/phase255_sector_heat_report.md

Example (after universe build + paper session)::
    python kabu_native/scripts/run_phase255_sector_heat_forward_shadow_logger.py --day 20260525
    python kabu_native/scripts/run_phase255_sector_heat_forward_shadow_logger.py --backfill-phase253
    python kabu_native/scripts/run_phase255_sector_heat_forward_shadow_logger.py --summary-only
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
REPO = Path(__file__).resolve().parents[2]
REPORTS = REPO / "kabu_native" / "results" / "reports"


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase255 sector heat forward shadow logger")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    parser.add_argument("--day", type=str, default=None, help="Validation day YYYYMMDD (default: today JST)")
    parser.add_argument("--backfill-phase253", action="store_true", help="Seed log from Phase253 overlap days")
    parser.add_argument("--universe-only", action="store_true")
    parser.add_argument("--trades-only", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    day = args.day or datetime.now(JST).strftime("%Y%m%d")
    log_universe = not args.trades_only and not args.summary_only
    log_trades = not args.universe_only and not args.summary_only
    update_summary = not args.universe_only and not args.trades_only

    _bootstrap()
    from research.market_sector_heat_forward_shadow_logger import MarketSectorHeatForwardShadowLogger

    t0 = time.monotonic()
    job = MarketSectorHeatForwardShadowLogger(repo_root=REPO, reports_dir=args.reports_dir)
    result = job.run(
        day=day,
        log_universe=log_universe,
        log_trades=log_trades,
        update_summary=update_summary,
        backfill_phase253=args.backfill_phase253,
    )
    paths = job.write_outputs(result)

    summary = result.get("forward_summary") or {}
    print(
        f"phase255_forward_shadow_logger wall_runtime_sec={round(time.monotonic() - t0, 1)}",
        flush=True,
    )
    print("\n=== Phase255 Sector Heat Forward Shadow Logger ===", flush=True)
    print(f"day: {day}", flush=True)
    print(f"last_run: {result.get('last_run')}", flush=True)
    print(
        f"trade overlap days: {summary.get('trade_overlap_day_count')} "
        f"({', '.join(summary.get('trade_overlap_days') or [])})",
        flush=True,
    )
    print(f"adopt_not_allowed_global: {summary.get('adopt_not_allowed_global')}", flush=True)
    for row in summary.get("adoption_verdict_by_pattern") or []:
        if row.get("pattern") == "actual":
            continue
        print(
            f"{row.get('pattern')}: adopt_not_allowed={row.get('adopt_not_allowed')} "
            f"stable={row.get('stable_candidate')} fragile={row.get('fragile_candidate')}",
            flush=True,
        )
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
