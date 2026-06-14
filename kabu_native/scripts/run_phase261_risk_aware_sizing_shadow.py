#!/usr/bin/env python3
"""
Phase261-Risk-Aware-Position-Sizing-Audit (review only)

Shadow evaluation of risk-based position sizing vs equity-only caps.

Output:
  kabu_native/results/reports/phase261_risk_aware_sizing_summary.json
  kabu_native/results/reports/phase261_entry_level_risk_sizing.csv
  kabu_native/results/reports/phase261_policy_by_equity.csv
  kabu_native/results/reports/phase261_volatility_bucket_analysis.csv
  kabu_native/results/reports/phase261_report.md

Example::
    python kabu_native/scripts/run_phase261_risk_aware_sizing_shadow.py
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
    parser = argparse.ArgumentParser(description="Phase261 risk-aware position sizing audit")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    args = parser.parse_args()

    _bootstrap()
    from research.risk_aware_sizing_shadow import RiskAwareSizingAudit

    t0 = time.monotonic()
    job = RiskAwareSizingAudit(repo_root=REPO, reports_dir=args.reports_dir)
    result = job.run()
    paths = job.write_outputs(result)
    verdict = result.get("verdict") or {}

    print(f"phase261_risk_aware_sizing wall_runtime_sec={round(time.monotonic() - t0, 1)}", flush=True)
    print("\n=== Phase261 Risk-Aware Position Sizing Audit ===", flush=True)
    print(f"base entries: {result.get('summary', {}).get('base_entry_count')}", flush=True)
    for key in (
        "risk_sizing_preferred_over_price_cap",
        "equity_only_sizing_overexpands_low_price",
        "hybrid_policy_candidate",
        "equity_1m_feasible",
        "equity_5m_feasible",
        "adoption_forbidden",
    ):
        print(f"{key}: {verdict.get(key)}", flush=True)
    print(f"recommendation: {verdict.get('recommendation')}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
