#!/usr/bin/env python3
"""
Phase262-Risk-Aware-Sizing-Forward-Shadow (review only)

Daily forward shadow logging for risk-aware position sizing policies.

Output:
  kabu_native/results/reports/phase262_risk_sizing_forward_entry_by_day.csv
  kabu_native/results/reports/phase262_risk_sizing_forward_summary_by_day.csv
  kabu_native/results/reports/phase262_risk_sizing_forward_summary.json
  kabu_native/results/reports/phase262_risk_sizing_report.md

Example::
    python kabu_native/scripts/run_phase262_risk_sizing_forward_shadow_logger.py
    python kabu_native/scripts/run_phase262_risk_sizing_forward_shadow_logger.py --day 20260525 --backfill-phase261
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
    parser = argparse.ArgumentParser(description="Phase262 risk sizing forward shadow logger")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    parser.add_argument("--day", type=str, default=None)
    parser.add_argument("--backfill-phase261", action="store_true")
    args = parser.parse_args()

    _bootstrap()
    from research.risk_sizing_forward_shadow_logger import RiskSizingForwardShadowLogger

    t0 = time.monotonic()
    job = RiskSizingForwardShadowLogger(repo_root=REPO, reports_dir=args.reports_dir)
    result = job.run(day=args.day, backfill_phase261=args.backfill_phase261)
    paths = job.write_outputs(result)
    summary = result.get("forward_summary") or {}

    print(f"phase262_risk_sizing_forward_shadow wall_runtime_sec={round(time.monotonic() - t0, 1)}", flush=True)
    print("\n=== Phase262 Risk Sizing Forward Shadow ===", flush=True)
    print(f"trade_overlap_days: {summary.get('trade_overlap_day_count')}", flush=True)
    print(f"best_policy: {summary.get('best_policy')}", flush=True)
    print(f"adopt_not_allowed: {summary.get('adopt_not_allowed')}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
