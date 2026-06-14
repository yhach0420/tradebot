#!/usr/bin/env python3
"""
Phase254-SectorHeat-Negative-Filter-Robustness (review only)

Verify Phase253 negative-filter patterns are stable across trade overlap days.
Observation only — no Runtime / Universe / Entry / YAML changes.

Output:
  kabu_native/results/reports/phase254_sector_heat_negative_filter_robustness_summary.json
  kabu_native/results/reports/phase254_day_level_stability.csv
  kabu_native/results/reports/phase254_entry_count_impact.csv
  kabu_native/results/reports/phase254_exclusion_severity.csv
  kabu_native/results/reports/phase254_sector_heat_report.md
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
    parser = argparse.ArgumentParser(description="Phase254 sector heat negative filter robustness")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    parser.add_argument("--rerun-phase253", action="store_true")
    args = parser.parse_args()

    _bootstrap()
    from research.market_sector_heat_negative_filter_robustness import (
        MarketSectorHeatNegativeFilterRobustness,
    )

    t0 = time.monotonic()
    job = MarketSectorHeatNegativeFilterRobustness(
        repo_root=REPO,
        reports_dir=args.reports_dir,
        rerun_phase253=args.rerun_phase253,
    )
    result = job.run()
    paths = job.write_outputs(result)

    print(
        f"phase254_robustness wall_runtime_sec={round(time.monotonic() - t0, 1)}",
        flush=True,
    )
    print("\n=== Phase254 Sector Heat Negative Filter Robustness ===", flush=True)
    coverage = result.get("coverage") or {}
    print(
        f"trade overlap days: {coverage.get('trade_overlap_day_count')} "
        f"({', '.join(coverage.get('trade_overlap_days') or [])})",
        flush=True,
    )
    for row in result.get("robustness_verdict_by_pattern") or []:
        print(
            f"{row.get('pattern')}: {row.get('recommendation')} "
            f"(stable={row.get('stable_candidate')}, fragile={row.get('fragile_candidate')})",
            flush=True,
        )
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
