#!/usr/bin/env python3
"""
Phase248-SectorHeat-Debias-Validation (review only)

Verify Phase247 Top3 edge is not merely dominant-sector bias.
Observation only — no Runtime / Universe / Entry changes.

Output:
  kabu_native/results/reports/phase248_sector_heat_debias_*.json|csv|md
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
    parser = argparse.ArgumentParser(description="Phase248 sector heat debias validation")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    parser.add_argument("--min-day", type=str, default=None)
    parser.add_argument("--max-day", type=str, default=None)
    parser.add_argument(
        "--regenerate-phase246",
        action="store_true",
        help="Re-run Phase246 before debias validation",
    )
    args = parser.parse_args()

    _bootstrap()
    from research.market_sector_heat_debias_validation import MarketSectorHeatDebiasValidation

    t0 = time.monotonic()
    audit = MarketSectorHeatDebiasValidation(
        repo_root=REPO,
        reports_dir=args.reports_dir,
        min_day=args.min_day,
        max_day=args.max_day,
        regenerate_phase246=args.regenerate_phase246,
    )
    result = audit.run()
    paths = audit.write_outputs(result)

    neutral = result.get("sector_neutral_validation") or {}
    verdict = result.get("verdict") or {}

    print(
        f"phase248_sector_heat_debias wall_runtime_sec={round(time.monotonic() - t0, 1)}",
        flush=True,
    )
    print("\n=== Phase248 Sector Heat Debias Validation ===", flush=True)
    print(
        f"beat_median_rate: {neutral.get('beat_median_rate')} "
        f"beat_mean_rate: {neutral.get('beat_mean_rate')}",
        flush=True,
    )
    print(
        f"avg_excess_vs_median: {neutral.get('avg_excess_vs_median_pct')} "
        f"avg_excess_vs_mean: {neutral.get('avg_excess_vs_mean_pct')}",
        flush=True,
    )
    print(f"verdict: {verdict.get('verdict')}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
