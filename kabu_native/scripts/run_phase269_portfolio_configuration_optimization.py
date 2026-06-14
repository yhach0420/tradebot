#!/usr/bin/env python3
"""
Phase269-Portfolio-Configuration-Optimization (review only)

Output:
  kabu_native/results/reports/phase269_portfolio_configuration_summary.json
  kabu_native/results/reports/phase269_configuration_grid.csv
  kabu_native/results/reports/phase269_top20_configurations.csv
  kabu_native/results/reports/phase269_safe_configurations.csv
  kabu_native/results/reports/phase269_dual_layer_comparison.csv
  kabu_native/results/reports/phase269_report.md
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
    parser = argparse.ArgumentParser(description="Phase269 portfolio configuration optimization")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    parser.add_argument("--period-start", type=str, default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.equity_curve_shadow import PERIOD_START
    from research.phase269_portfolio_configuration_optimization import (
        Phase269PortfolioConfigurationOptimization,
    )

    t0 = time.monotonic()
    job = Phase269PortfolioConfigurationOptimization(
        repo_root=REPO,
        reports_dir=args.reports_dir,
        period_start=args.period_start or PERIOD_START,
    )
    result = job.run()
    paths = job.write_outputs(result)
    answers = result.get("required_answers") or {}
    a1 = answers.get("1_max_final_equity_configuration") or {}
    a6 = answers.get("6_recommended_live_configuration") or {}
    stats = result.get("grid_stats") or {}

    print(f"phase269 wall_runtime_sec={round(time.monotonic()-t0,1)}", flush=True)
    print("\n=== Phase269 Portfolio Configuration Optimization ===", flush=True)
    print(f"configurations={stats.get('configuration_count')} adoptable={stats.get('adoptable_count')} safe={stats.get('safe_count')}", flush=True)
    print(f"max_final_equity: {a1.get('config_id')} -> {a1.get('final_equity')}", flush=True)
    print(f"recommended: {a6.get('config_id')} -> {a6.get('final_equity')}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
