#!/usr/bin/env python3
"""
Phase274-Live-Config-Auto-Transition-Shadow (review only)

Output:
  kabu_native/results/reports/phase274_live_config_transition_equity_curve.csv
  kabu_native/results/reports/phase274_live_config_transition_daily_equity.csv
  kabu_native/results/reports/phase274_live_config_transition_summary.json
  kabu_native/results/reports/phase274_live_config_transition_report.md
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
    parser = argparse.ArgumentParser(description="Phase274 live config auto transition shadow")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    parser.add_argument("--day", type=str, default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.phase274_live_config_auto_transition_shadow import LiveConfigAutoTransitionShadow

    t0 = time.monotonic()
    job = LiveConfigAutoTransitionShadow(repo_root=REPO, reports_dir=args.reports_dir)
    result = job.run(day=args.day)
    paths = job.write_outputs(result)
    summary = result.get("transition_summary") or {}

    print(f"phase274_live_config_transition wall_runtime_sec={round(time.monotonic() - t0, 1)}", flush=True)
    print("\n=== Phase274 Live Config Transition Shadow ===", flush=True)
    print(f"current_equity: {summary.get('current_equity')}", flush=True)
    print(f"band: {summary.get('active_policy_band')}", flush=True)
    print(f"transition_day_to_2000k: {summary.get('transition_day_to_2000k')}", flush=True)
    print(f"adoption_verdict: {(summary.get('adoption_verdict') or {}).get('adoption_verdict')}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
