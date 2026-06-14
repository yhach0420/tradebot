#!/usr/bin/env python3
"""
Phase257-Core12-Dynamic38-PriceCap-Shadow-Review (review only)

Shadow evaluation of Core12/Dynamic38 and price-cap removal vs actual Core10/Dynamic40.

Output:
  kabu_native/results/reports/phase257_core12_dynamic38_pricecap_shadow_summary.json
  kabu_native/results/reports/phase257_universe_diff_by_pattern.csv
  kabu_native/results/reports/phase257_trade_validation_by_pattern.csv
  kabu_native/results/reports/phase257_price_band_analysis.csv
  kabu_native/results/reports/phase257_sector_heat_impact.csv
  kabu_native/results/reports/phase257_report.md

Example::
    python kabu_native/scripts/run_phase257_core12_dynamic38_pricecap_shadow_review.py
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
    parser = argparse.ArgumentParser(description="Phase257 core12 dynamic38 pricecap shadow review")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    args = parser.parse_args()

    _bootstrap()
    from research.core12_dynamic38_pricecap_shadow_review import Core12Dynamic38PriceCapShadowReview

    t0 = time.monotonic()
    job = Core12Dynamic38PriceCapShadowReview(repo_root=REPO, reports_dir=args.reports_dir)
    result = job.run()
    paths = job.write_outputs(result)
    summary = result.get("summary") or {}

    print(f"phase257_shadow_review wall_runtime_sec={round(time.monotonic() - t0, 1)}", flush=True)
    print("\n=== Phase257 Core12 Dynamic38 PriceCap Shadow Review ===", flush=True)
    print(
        f"simulated days: {summary.get('simulated_day_count')} "
        f"trade overlap: {summary.get('trade_overlap_day_count')}",
        flush=True,
    )
    for row in result.get("aggregate_trade_by_pattern") or []:
        print(
            f"{row.get('pattern')}: pnl={row.get('total_pnl_yen_100')} "
            f"delta={row.get('delta_pnl_yen_100_vs_baseline')}",
            flush=True,
        )
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
