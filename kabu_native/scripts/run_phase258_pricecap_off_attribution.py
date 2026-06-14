#!/usr/bin/env python3
"""
Phase258-PriceCap-Off-Attribution (review only)

Decompose Phase257 price cap OFF improvement into low-price vs high-price effects.

Output:
  kabu_native/results/reports/phase258_pricecap_off_attribution_summary.json
  kabu_native/results/reports/phase258_price_band_attribution.csv
  kabu_native/results/reports/phase258_cap_off_added_removed.csv
  kabu_native/results/reports/phase258_low_price_risk.csv
  kabu_native/results/reports/phase258_high_price_risk.csv
  kabu_native/results/reports/phase258_report.md

Example::
    python kabu_native/scripts/run_phase258_pricecap_off_attribution.py
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
    parser = argparse.ArgumentParser(description="Phase258 price cap OFF attribution")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    args = parser.parse_args()

    _bootstrap()
    from research.pricecap_off_attribution import PriceCapOffAttribution

    t0 = time.monotonic()
    job = PriceCapOffAttribution(repo_root=REPO, reports_dir=args.reports_dir)
    result = job.run()
    paths = job.write_outputs(result)
    verdict = result.get("verdict") or {}

    print(f"phase258_pricecap_off_attribution wall_runtime_sec={round(time.monotonic() - t0, 1)}", flush=True)
    print("\n=== Phase258 Price Cap OFF Attribution ===", flush=True)
    print(f"trade overlap days: {verdict.get('trade_overlap_day_count')}", flush=True)
    print(f"total cap OFF delta: {result.get('total_cap_off_delta_pnl_yen_100')}", flush=True)
    print(f"low_price_edge_candidate: {verdict.get('low_price_edge_candidate')}", flush=True)
    print(f"high_price_risk_candidate: {verdict.get('high_price_risk_candidate')}", flush=True)
    print(f"adopt_not_allowed: {verdict.get('adopt_not_allowed')}", flush=True)
    print(f"recommendation: {verdict.get('recommendation')}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
