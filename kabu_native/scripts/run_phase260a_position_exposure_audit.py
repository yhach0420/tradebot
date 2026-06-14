#!/usr/bin/env python3
"""
Phase260A-Position-Exposure-Audit (review only)

Audit whether high-price profits are price-cap or position-sizing constrained.

Output:
  kabu_native/results/reports/phase260a_position_exposure_audit_summary.json
  kabu_native/results/reports/phase260a_exposure_distribution.csv
  kabu_native/results/reports/phase260a_price_band_exposure.csv
  kabu_native/results/reports/phase260a_feasibility_by_equity.csv
  kabu_native/results/reports/phase260a_report.md

Example::
    python kabu_native/scripts/run_phase260a_position_exposure_audit.py
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
    parser = argparse.ArgumentParser(description="Phase260A position exposure audit")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    args = parser.parse_args()

    _bootstrap()
    from research.position_exposure_audit import PositionExposureAudit

    t0 = time.monotonic()
    job = PositionExposureAudit(repo_root=REPO, reports_dir=args.reports_dir)
    result = job.run()
    paths = job.write_outputs(result)
    verdict = result.get("verdict") or {}

    print(f"phase260a_position_exposure_audit wall_runtime_sec={round(time.monotonic() - t0, 1)}", flush=True)
    print("\n=== Phase260A Position Exposure Audit ===", flush=True)
    print(f"entries: {result.get('summary', {}).get('entry_count')}", flush=True)
    print(f"price_cap_is_proxy_for_position_sizing: {verdict.get('price_cap_is_proxy_for_position_sizing')}", flush=True)
    print(f"high_price_edge_but_low_equity_problem: {verdict.get('high_price_edge_but_low_equity_problem')}", flush=True)
    print(f"high_price_edge_and_large_equity_safe: {verdict.get('high_price_edge_and_large_equity_safe')}", flush=True)
    print(f"recommendation: {verdict.get('recommendation')}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
