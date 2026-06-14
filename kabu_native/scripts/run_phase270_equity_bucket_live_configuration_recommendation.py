#!/usr/bin/env python3
"""
Phase270-Equity-Bucket-Live-Configuration-Recommendation (review only)

Output:
  kabu_native/results/reports/phase270_equity_bucket_recommendation_summary.json
  kabu_native/results/reports/phase270_equity_bucket_recommendation.csv
  kabu_native/results/reports/phase270_cap_by_equity.csv
  kabu_native/results/reports/phase270_stop_policy_by_equity.csv
  kabu_native/results/reports/phase270_dual_layer_comparison.csv
  kabu_native/results/reports/phase270_report.md
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
    parser = argparse.ArgumentParser(description="Phase270 equity bucket live configuration recommendation")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    parser.add_argument("--period-start", type=str, default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.equity_curve_shadow import PERIOD_START
    from research.phase270_equity_bucket_live_configuration_recommendation import (
        Phase270EquityBucketLiveConfigurationRecommendation,
    )

    t0 = time.monotonic()
    job = Phase270EquityBucketLiveConfigurationRecommendation(
        repo_root=REPO,
        reports_dir=args.reports_dir,
        period_start=args.period_start or PERIOD_START,
    )
    result = job.run()
    paths = job.write_outputs(result)
    answers = result.get("required_answers") or {}
    a1500 = answers.get("1500k_start_recommendation") or {}

    print(f"phase270 wall_runtime_sec={round(time.monotonic()-t0,1)}", flush=True)
    print("\n=== Phase270 Equity Bucket Recommendation ===", flush=True)
    print(
        f"1500k: CAP={a1500.get('recommended_cap')} lev={a1500.get('recommended_leverage')} "
        f"stop={a1500.get('recommended_stop_policy')} final={a1500.get('final_equity')}",
        flush=True,
    )
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
