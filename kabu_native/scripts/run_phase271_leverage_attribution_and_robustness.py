#!/usr/bin/env python3
"""Phase271-Leverage-Attribution-and-Robustness (review only)."""

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
    parser = argparse.ArgumentParser(description="Phase271 leverage attribution and robustness")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    parser.add_argument("--period-start", type=str, default=None)
    parser.add_argument("--bootstrap-iterations", type=int, default=1000)
    args = parser.parse_args()

    _bootstrap()
    from research.equity_curve_shadow import PERIOD_START
    from research.phase271_leverage_attribution_and_robustness import (
        Phase271LeverageAttributionAndRobustness,
    )

    t0 = time.monotonic()
    job = Phase271LeverageAttributionAndRobustness(
        repo_root=REPO,
        reports_dir=args.reports_dir,
        period_start=args.period_start or PERIOD_START,
        bootstrap_iterations=args.bootstrap_iterations,
    )
    result = job.run()
    paths = job.write_outputs(result)
    answers = result.get("required_answers") or {}

    print(f"phase271 wall_runtime_sec={round(time.monotonic()-t0,1)}", flush=True)
    print("\n=== Phase271 Leverage Attribution ===", flush=True)
    print(f"lev1p5 robust: {answers.get('1_is_lev1p5_statistically_robust', {}).get('verdict')}", flush=True)
    print(f"lev1p5 economic: {answers.get('2_is_lev1p5_economically_meaningful', {}).get('verdict')}", flush=True)
    print(f"recommendation: {answers.get('5_recommended_approach', {}).get('choice')}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
