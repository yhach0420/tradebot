#!/usr/bin/env python3
"""
Phase250-SectorHeat-Data-Alignment-Diagnostics (review only)

Diagnose date misalignment blocking Phase249 shadow simulation.
Observation only — no Runtime / Universe / Entry / YAML changes.

Output:
  kabu_native/results/reports/phase250_sector_heat_data_alignment_*.json|csv|md
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
    parser = argparse.ArgumentParser(description="Phase250 sector heat data alignment diagnostics")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    args = parser.parse_args()

    _bootstrap()
    from research.market_sector_heat_data_alignment import MarketSectorHeatDataAlignmentDiagnostics

    t0 = time.monotonic()
    audit = MarketSectorHeatDataAlignmentDiagnostics(repo_root=REPO, reports_dir=args.reports_dir)
    result = audit.run()
    paths = audit.write_outputs(result)

    coverage = result.get("coverage") or {}
    print(
        f"phase250_data_alignment wall_runtime_sec={round(time.monotonic() - t0, 1)}",
        flush=True,
    )
    print("\n=== Phase250 Sector Heat Data Alignment ===", flush=True)
    print(f"calendar days: {coverage.get('calendar_day_count')}", flush=True)
    print(f"simulatable days: {coverage.get('simulatable_day_count')}", flush=True)
    print(f"trade-validatable days: {coverage.get('trade_validatable_day_count')}", flush=True)
    print(f"phase249_blocked: {coverage.get('phase249_blocked')}", flush=True)
    print(f"root_cause: {result.get('root_cause')}", flush=True)
    for item in result.get("next_action_suggestions") or []:
        print(f"suggestion: {item}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
