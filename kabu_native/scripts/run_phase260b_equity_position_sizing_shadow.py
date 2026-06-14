#!/usr/bin/env python3
"""
Phase260B-Equity-Aware-Position-Sizing-Shadow (review only)

Shadow evaluation of equity-aware position sizing vs fixed 100 shares.

Output:
  kabu_native/results/reports/phase260b_equity_position_sizing_summary.json
  kabu_native/results/reports/phase260b_entry_level_sizing.csv
  kabu_native/results/reports/phase260b_policy_by_equity.csv
  kabu_native/results/reports/phase260b_high_price_sizing_impact.csv
  kabu_native/results/reports/phase260b_report.md

Example::
    python kabu_native/scripts/run_phase260b_equity_position_sizing_shadow.py
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
    parser = argparse.ArgumentParser(description="Phase260B equity position sizing shadow")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    args = parser.parse_args()

    _bootstrap()
    from research.equity_position_sizing_shadow import EquityPositionSizingShadow

    t0 = time.monotonic()
    job = EquityPositionSizingShadow(repo_root=REPO, reports_dir=args.reports_dir)
    result = job.run()
    paths = job.write_outputs(result)
    verdict = result.get("verdict") or {}

    print(f"phase260b_equity_position_sizing_shadow wall_runtime_sec={round(time.monotonic() - t0, 1)}", flush=True)
    print("\n=== Phase260B Equity Position Sizing Shadow ===", flush=True)
    print(f"base entries: {result.get('summary', {}).get('base_entry_count')}", flush=True)
    for key in (
        "equity_1m_high_price_not_feasible",
        "equity_3m_partial_feasible",
        "equity_5m_high_price_feasible",
        "sizing_preferred_over_price_cap",
        "adoption_forbidden",
    ):
        print(f"{key}: {verdict.get(key)}", flush=True)
    print(f"recommendation: {verdict.get('recommendation')}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
