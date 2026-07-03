#!/usr/bin/env python3
"""Phase603 full-period backtest: Phase602 OFF vs Phase603 ON."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KABU = REPO / "kabu_native"


def _bootstrap() -> None:
    for p in (KABU / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _default_workers() -> int:
    n = os.cpu_count() or 4
    return max(2, min(6, n // 2))


def main() -> int:
    _bootstrap()
    parser = argparse.ArgumentParser(description="Phase603 full-period backtest")
    parser.add_argument("--poll-interval-sec", type=float, default=5.0)
    parser.add_argument("--workers", type=int, default=_default_workers(), help="Parallel day workers")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--day", action="append", dest="days", help="Limit to YYYY-MM-DD (repeatable)")
    parser.add_argument("--aggregate-only", action="store_true", help="Rebuild reports from checkpoints")
    parser.add_argument("--smoke", action="store_true", help="Run single day 2026-06-29 only")
    args = parser.parse_args()

    days = args.days
    if args.smoke:
        days = ["2026-06-29"]

    if args.aggregate_only:
        from research.phase603_full_period_backtest import Phase603FullPeriodJob, _discover_push_days

        job = Phase603FullPeriodJob(REPO, poll_interval_sec=args.poll_interval_sec)
        all_days = _discover_push_days(job.push_root)
        baseline_days = []
        candidate_days = []
        for day_iso in all_days:
            b = job._load_day_with_trades(day_iso, "phase602")
            c = job._load_day_with_trades(day_iso, "phase603")
            if b and c:
                baseline_days.append(b)
                candidate_days.append(c)
        if not baseline_days:
            print("no complete day pairs in checkpoints", file=sys.stderr)
            return 1
        result = job._aggregate(baseline_days, candidate_days, [d["day"] for d in baseline_days])
        paths = job.write_outputs(result)
        print(f"verdict={result.get('verdict')} days={len(baseline_days)}", flush=True)
        print(f"adoption={json.dumps(result.get('adoption'), ensure_ascii=False)}", flush=True)
        for k, v in paths.items():
            print(f"{k}={v}", flush=True)
        return 0

    from research.phase603_full_period_backtest import run_phase603_full_period

    print(f"phase603 starting workers={args.workers} resume={not args.no_resume}", flush=True)
    result = run_phase603_full_period(
        REPO,
        poll_interval_sec=args.poll_interval_sec,
        resume=not args.no_resume,
        days=days,
        workers=args.workers,
    )
    print(f"verdict={result.get('verdict')}", flush=True)
    print(f"adoption={json.dumps(result.get('adoption'), ensure_ascii=False)}", flush=True)
    for k, v in (result.get("output_paths") or {}).items():
        print(f"{k}={v}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
