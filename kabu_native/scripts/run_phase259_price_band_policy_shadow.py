#!/usr/bin/env python3
"""
Phase259-PriceBand-Policy-Shadow (review only)

Shadow evaluation of decomposed price-band policies on Core10/Dynamic40.

Output:
  kabu_native/results/reports/phase259_price_band_policy_shadow_summary.json
  kabu_native/results/reports/phase259_trade_validation_by_policy.csv
  kabu_native/results/reports/phase259_price_band_composition.csv
  kabu_native/results/reports/phase259_added_removed_by_policy.csv
  kabu_native/results/reports/phase259_risk_metrics.csv
  kabu_native/results/reports/phase259_report.md

Example::
    python kabu_native/scripts/run_phase259_price_band_policy_shadow.py
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
    parser = argparse.ArgumentParser(description="Phase259 price band policy shadow")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    args = parser.parse_args()

    _bootstrap()
    from research.price_band_policy_shadow import PriceBandPolicyShadow

    t0 = time.monotonic()
    job = PriceBandPolicyShadow(repo_root=REPO, reports_dir=args.reports_dir)
    result = job.run()
    paths = job.write_outputs(result)
    verdict = result.get("verdict") or {}
    summary = result.get("summary") or {}

    print(f"phase259_price_band_policy_shadow wall_runtime_sec={round(time.monotonic() - t0, 1)}", flush=True)
    print("\n=== Phase259 Price Band Policy Shadow ===", flush=True)
    print(
        f"simulated days: {summary.get('simulated_day_count')} "
        f"trade overlap: {summary.get('trade_overlap_day_count')}",
        flush=True,
    )
    print(f"adopt_not_allowed: {verdict.get('adopt_not_allowed')}", flush=True)
    print(f"high_price_edge_candidate: {verdict.get('high_price_edge_candidate')}", flush=True)
    print(f"recommendation: {verdict.get('recommendation')}", flush=True)
    for row in result.get("risk_metrics") or []:
        print(
            f"{row.get('policy')}: delta={row.get('delta_vs_actual')} "
            f"high_delta={row.get('high_price_delta')}",
            flush=True,
        )
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
