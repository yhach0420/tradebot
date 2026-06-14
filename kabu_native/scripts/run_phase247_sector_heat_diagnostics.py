#!/usr/bin/env python3
"""
Phase247-SectorHeat-Diagnostics (review only)

Diagnose Phase246 Top3 sector heat prediction quality.
Observation only — no Runtime / Universe / Entry changes.

Output:
  kabu_native/results/reports/phase247_sector_heat_*.json|csv|md
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
    parser = argparse.ArgumentParser(description="Phase247 sector heat diagnostics")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    parser.add_argument("--min-day", type=str, default=None)
    parser.add_argument("--max-day", type=str, default=None)
    parser.add_argument("--min-trusted-signal-count", type=int, default=3)
    parser.add_argument(
        "--regenerate-phase246",
        action="store_true",
        help="Re-run Phase246 before diagnostics",
    )
    args = parser.parse_args()

    _bootstrap()
    from research.market_sector_heat_diagnostics import MarketSectorHeatDiagnostics

    t0 = time.monotonic()
    audit = MarketSectorHeatDiagnostics(
        repo_root=REPO,
        reports_dir=args.reports_dir,
        min_day=args.min_day,
        max_day=args.max_day,
        min_trusted_signal_count=max(1, args.min_trusted_signal_count),
        regenerate_phase246=args.regenerate_phase246,
    )
    result = audit.run()
    paths = audit.write_outputs(result)

    baseline = result.get("baseline_comparison") or {}
    overfit = result.get("overfit_warnings") or {}
    concentration = result.get("sector_concentration") or {}

    print(
        f"phase247_sector_heat_diagnostics wall_runtime_sec={round(time.monotonic() - t0, 1)}",
        flush=True,
    )
    print("\n=== Phase247 Sector Heat Diagnostics ===", flush=True)
    print(
        f"unique sectors in top3: {concentration.get('unique_sectors_in_top3')} "
        f"top3 slot share: {concentration.get('top3_sector_slot_share')}",
        flush=True,
    )
    print(
        f"all sectors +rate: {baseline.get('all_sectors_next_day_positive_rate')} "
        f"top3 +rate: {baseline.get('top3_next_day_positive_rate')} "
        f"delta: {baseline.get('top3_vs_all_sectors_positive_rate_delta')}",
        flush=True,
    )
    print(f"overfit verdict: {overfit.get('verdict')} flags={overfit.get('flags')}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
