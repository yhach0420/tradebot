#!/usr/bin/env python3
"""
Phase249-SectorHeat-Universe-Shadow-Simulation (review only)

Shadow simulation of Sector Heat Top3 applied to Dynamic40 universe selection.
Observation only — no Runtime / Universe / Entry / YAML changes.

Output:
  kabu_native/results/reports/phase249_sector_heat_universe_shadow_*.json|csv|md
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
    parser = argparse.ArgumentParser(description="Phase249 sector heat universe shadow simulation")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    args = parser.parse_args()

    _bootstrap()
    from research.market_sector_heat_universe_shadow import MarketSectorHeatUniverseShadowSimulation

    t0 = time.monotonic()
    sim = MarketSectorHeatUniverseShadowSimulation(repo_root=REPO, reports_dir=args.reports_dir)
    result = sim.run()
    paths = sim.write_outputs(result)

    coverage = result.get("coverage") or {}
    print(
        f"phase249_sector_heat_universe_shadow wall_runtime_sec={round(time.monotonic() - t0, 1)}",
        flush=True,
    )
    print("\n=== Phase249 Sector Heat Universe Shadow ===", flush=True)
    print(f"top3 validation days: {coverage.get('top3_validation_day_count')}", flush=True)
    print(f"simulated days: {coverage.get('simulated_day_count')}", flush=True)
    print(f"trade overlap days: {coverage.get('trade_overlap_day_count')}", flush=True)
    print(f"skipped days: {coverage.get('skipped_day_count')}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
