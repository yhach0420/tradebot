#!/usr/bin/env python3
"""
Phase263-Equity-Position-Based-Dynamic-Stop-Shadow (review only)

Shadow evaluation of equity/position-value-derived dynamic stops vs fixed -1.2%.

Output:
  kabu_native/results/reports/phase263_entry_level_dynamic_stop.csv
  kabu_native/results/reports/phase263_summary_by_equity_risk_pct.csv
  kabu_native/results/reports/phase263_equity_dynamic_stop_summary.json
  kabu_native/results/reports/phase263_report.md

Example::
    python kabu_native/scripts/run_phase263_equity_dynamic_stop_shadow.py
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
    parser = argparse.ArgumentParser(description="Phase263 equity dynamic stop shadow")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    args = parser.parse_args()

    _bootstrap()
    from research.equity_dynamic_stop_shadow import EquityDynamicStopShadow

    t0 = time.monotonic()
    job = EquityDynamicStopShadow(repo_root=REPO, reports_dir=args.reports_dir)
    result = job.run()
    paths = job.write_outputs(result)
    verdict = result.get("verdict") or {}
    summary = result.get("summary") or {}

    print(f"phase263_equity_dynamic_stop_shadow wall_runtime_sec={round(time.monotonic() - t0, 1)}", flush=True)
    print("\n=== Phase263 Equity Dynamic Stop Shadow ===", flush=True)
    print(f"period days: {summary.get('period_days')}", flush=True)
    print(f"base entries: {summary.get('base_entry_count')}", flush=True)
    for key in (
        "dynamic_stop_candidate",
        "risk_pct_too_tight",
        "risk_pct_too_loose",
        "equity_1p5m_feasible",
        "cap2_double_stop_loss_ratio",
        "adoption_forbidden",
    ):
        print(f"{key}: {verdict.get(key)}", flush=True)
    print(f"recommendation: {verdict.get('recommendation')}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
