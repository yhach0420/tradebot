#!/usr/bin/env python3
"""Phase272: Apply Phase271 leverage robustness to equity bucket recommendations."""

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
    parser = argparse.ArgumentParser(description="Phase272 lev2-fixed equity bucket recommendation")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    parser.add_argument("--period-start", type=str, default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.equity_curve_shadow import PERIOD_START
    from research.phase272_apply_leverage_robustness_to_equity_bucket_recommendation import (
        Phase272ApplyLeverageRobustnessToEquityBucketRecommendation,
    )

    t0 = time.monotonic()
    job = Phase272ApplyLeverageRobustnessToEquityBucketRecommendation(
        repo_root=REPO,
        reports_dir=args.reports_dir,
        period_start=args.period_start or PERIOD_START,
    )
    result = job.run()
    paths = job.write_outputs(result)
    a1 = (result.get("required_answers") or {}).get("1_cap_for_1500k_lev2_fixed") or {}
    a5 = (result.get("required_answers") or {}).get("5_live_start_recommendation") or {}

    print(f"phase272 wall_runtime_sec={round(time.monotonic()-t0,1)}", flush=True)
    print("\n=== Phase272 Lev2 Fixed Recommendations ===", flush=True)
    print(
        f"1500k: CAP={a1.get('recommended_cap')} stop={a1.get('recommended_stop_policy')} final={a1.get('final_equity')}",
        flush=True,
    )
    print(f"live_start: {a5.get('config_id')}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
