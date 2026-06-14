#!/usr/bin/env python3
"""
Phase253-SectorHeat-Negative-Filter-Shadow (review only)

Test weak-sector exclusion patterns for Dynamic40 shadow simulation.
Observation only — no Runtime / Universe / Entry / YAML changes.

Output:
  kabu_native/results/reports/phase253_sector_heat_negative_filter_summary.json
  kabu_native/results/reports/phase253_universe_diff_by_day.csv
  kabu_native/results/reports/phase253_trade_validation_by_pattern.csv
  kabu_native/results/reports/phase253_added_removed_attribution.csv
  kabu_native/results/reports/phase253_day_level_delta.csv
  kabu_native/results/reports/phase253_sector_heat_report.md
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
    parser = argparse.ArgumentParser(description="Phase253 sector heat negative filter shadow")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    args = parser.parse_args()

    _bootstrap()
    from research.market_sector_heat_negative_filter_shadow import MarketSectorHeatNegativeFilterShadow

    t0 = time.monotonic()
    job = MarketSectorHeatNegativeFilterShadow(repo_root=REPO, reports_dir=args.reports_dir)
    result = job.run()
    paths = job.write_outputs(result)

    coverage = result.get("coverage") or {}
    print(
        f"phase253_negative_filter_shadow wall_runtime_sec={round(time.monotonic() - t0, 1)}",
        flush=True,
    )
    print("\n=== Phase253 Sector Heat Negative Filter Shadow ===", flush=True)
    print(f"simulated days: {coverage.get('simulated_day_count')}", flush=True)
    print(
        f"trade overlap days: {coverage.get('trade_overlap_day_count')} "
        f"({', '.join(coverage.get('trade_overlap_days') or [])})",
        flush=True,
    )
    for row in result.get("aggregate_trade_by_pattern") or []:
        if row.get("pattern") == "actual":
            continue
        print(
            f"{row.get('pattern')}: delta={row.get('delta_pnl_yen_100_vs_actual')} "
            f"avoidance={row.get('removed_loser_avoidance_yen_100')} "
            f"added_winners={row.get('added_winner_contribution_yen_100')}",
            flush=True,
        )
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
