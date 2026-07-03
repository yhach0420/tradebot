#!/usr/bin/env python3
"""Phase620: freshness semantics full-period backtest (Baseline + A–F)."""

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


def main() -> int:
    _bootstrap()
    parser = argparse.ArgumentParser(description="Phase620 freshness semantics full-period backtest")
    parser.add_argument("--poll-interval-sec", type=float, default=5.0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--day", action="append", dest="days", help="YYYY-MM-DD (repeatable)")
    parser.add_argument("--variant", action="append", dest="variants", help="baseline|A|B|C|D|E|F")
    parser.add_argument("--aggregate-only", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="Single day 2026-06-29, baseline+A only")
    parser.add_argument("--delete-checkpoints", action="store_true")
    args = parser.parse_args()

    days = args.days
    variants = args.variants
    if args.smoke:
        days = ["2026-06-29"]
        variants = ["baseline", "A"]

    if args.delete_checkpoints:
        os.environ["PHASE620_DELETE_CHECKPOINTS"] = "1"

    if args.aggregate_only:
        from research.phase620_freshness_semantics_full_period_backtest import (
            Phase620FreshnessBacktestJob,
            _discover_push_days,
        )
        from research.phase620_freshness_semantics_variant import VARIANTS

        job = Phase620FreshnessBacktestJob(REPO, poll_interval_sec=args.poll_interval_sec)
        all_days = list(days or _discover_push_days(job.push_root))
        all_variants = list(variants or list(VARIANTS.keys()))
        by_variant = {}
        for vid in all_variants:
            rows = []
            for day_iso in all_days:
                row = job._load_day_with_trades(day_iso, vid)
                if row:
                    rows.append(row)
            if rows:
                by_variant[vid] = rows
        if not by_variant:
            print("no checkpoints", file=sys.stderr)
            return 1
        result = job._aggregate(by_variant, all_days, all_variants)
        paths = job.write_outputs(result)
        print(f"verdict={result.get('verdict')}", flush=True)
        print(json.dumps(result.get("mandatory_answers"), ensure_ascii=False, indent=2), flush=True)
        for k, v in paths.items():
            print(f"{k}={v}", flush=True)
        return 0

    from research.phase620_freshness_semantics_full_period_backtest import run_phase620_freshness_backtest

    print(
        f"phase620 starting workers={args.workers} resume={not args.no_resume} "
        f"days={days or 'ALL'} variants={variants or 'ALL'}",
        flush=True,
    )
    result = run_phase620_freshness_backtest(
        REPO,
        poll_interval_sec=args.poll_interval_sec,
        resume=not args.no_resume,
        days=days,
        variants=variants,
        workers=args.workers,
    )
    print(f"verdict={result.get('verdict')}", flush=True)
    print(json.dumps(result.get("mandatory_answers"), ensure_ascii=False, indent=2), flush=True)
    for k, v in (result.get("output_paths") or {}).items():
        print(f"{k}={v}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
