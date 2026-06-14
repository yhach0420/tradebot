#!/usr/bin/env python3
"""
Phase273-Forward-Live-Configuration-Shadow-Logger (review only)

Daily forward shadow equity curves for Phase272 provisional live configurations.

Output:
  kabu_native/results/reports/phase273_live_config_shadow_daily_equity.csv
  kabu_native/results/reports/phase273_live_config_shadow_trade_events.csv
  kabu_native/results/reports/phase273_live_config_shadow_summary.json
  kabu_native/results/reports/phase273_live_config_shadow_report.md

Example::
    python kabu_native/scripts/run_phase273_live_config_forward_shadow_logger.py
    python kabu_native/scripts/run_phase273_live_config_forward_shadow_logger.py --day 20260612
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
    parser = argparse.ArgumentParser(description="Phase273 live config forward shadow logger")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    parser.add_argument("--day", type=str, default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.phase273_live_config_forward_shadow_logger import LiveConfigForwardShadowLogger

    t0 = time.monotonic()
    job = LiveConfigForwardShadowLogger(repo_root=REPO, reports_dir=args.reports_dir)
    result = job.run(day=args.day)
    paths = job.write_outputs(result)
    summary = result.get("forward_summary") or {}

    print(f"phase273_live_config_forward_shadow wall_runtime_sec={round(time.monotonic() - t0, 1)}", flush=True)
    print("\n=== Phase273 Live Config Forward Shadow ===", flush=True)
    print(f"day_count: {summary.get('day_count')}", flush=True)
    print(f"current_recommendation: {summary.get('current_recommendation')}", flush=True)
    print(f"adopt_not_allowed: {summary.get('adopt_not_allowed')}", flush=True)
    for row in summary.get("candidates") or []:
        print(
            f"{row.get('candidate_key')}: final={row.get('final_equity')} "
            f"DD={row.get('max_drawdown_pct')} verdict={row.get('verdict')}",
            flush=True,
        )
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
