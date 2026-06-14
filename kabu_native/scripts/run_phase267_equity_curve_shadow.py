#!/usr/bin/env python3
"""
Phase267-Equity-Curve-Shadow (review only)

Simulate 1.5M / credit 2x / 100 shares / CAP=2 equity curves.

Output:
  kabu_native/results/reports/phase267_equity_curve.csv
  kabu_native/results/reports/phase267_daily_equity.csv
  kabu_native/results/reports/phase267_drawdown.csv
  kabu_native/results/reports/phase267_equity_curve_summary.json
  kabu_native/results/reports/phase267_report.md

Example::
    python kabu_native/scripts/run_phase267_equity_curve_shadow.py
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
    parser = argparse.ArgumentParser(description="Phase267 equity curve shadow")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    parser.add_argument("--period-start", type=str, default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.equity_curve_shadow import PERIOD_START, EquityCurveShadow

    t0 = time.monotonic()
    job = EquityCurveShadow(
        repo_root=REPO,
        reports_dir=args.reports_dir,
        period_start=args.period_start or PERIOD_START,
    )
    result = job.run()
    paths = job.write_outputs(result)
    actual = (result.get("scenarios") or {}).get("actual_fixed_stop") or {}
    dynamic = (result.get("scenarios") or {}).get("dynamic_stop_risk_1p0") or {}

    print(f"phase267_equity_curve_shadow wall_runtime_sec={round(time.monotonic() - t0, 1)}", flush=True)
    print("\n=== Phase267 Equity Curve Shadow ===", flush=True)
    print(f"trades: {(result.get('population') or {}).get('input_trade_count')}", flush=True)
    for label, row in (("actual_fixed_stop", actual), ("dynamic_stop_risk_1p0", dynamic)):
        print(
            f"{label}: final={row.get('final_equity')} return={row.get('total_return_pct')}% "
            f"max_dd={row.get('max_drawdown_pct')}% calmar={row.get('calmar_ratio')}",
            flush=True,
        )
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
