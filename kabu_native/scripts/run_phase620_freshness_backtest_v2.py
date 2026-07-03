#!/usr/bin/env python3
"""Phase620 v2: disk-safe 8-parallel freshness backtest."""

from __future__ import annotations

import argparse
import json
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
    parser = argparse.ArgumentParser(description="Phase620 v2 disk-safe freshness backtest")
    parser.add_argument("--cleanup-only", action="store_true", help="Run disk cleanup then exit")
    parser.add_argument("--aggregate-only", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--poll-interval-sec", type=float, default=5.0)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--day", action="append", dest="days")
    parser.add_argument("--variant", action="append", dest="variants")
    parser.add_argument("--smoke", action="store_true", help="2026-06-29 baseline+A only")
    args = parser.parse_args()

    if args.cleanup_only:
        from research.phase620_disk_cleanup import run_disk_cleanup

        result = run_disk_cleanup(REPO)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        if not result.get("can_resume"):
            print("ERROR: disk below 50GB", file=sys.stderr)
            return 1
        return 0

    days = args.days
    variants = args.variants
    if args.smoke:
        days = ["2026-06-29"]
        variants = ["baseline", "A"]

    from research.phase620_freshness_backtest_v2 import run_phase620_v2

    if not args.aggregate_only:
        from research.phase620_disk_cleanup import run_disk_cleanup

        cleanup = run_disk_cleanup(REPO)
        print(f"cleanup freed_gb={cleanup.get('freed_gb')} free_after={cleanup.get('free_gb_after')}", flush=True)
        if not cleanup.get("can_resume"):
            print("ERROR: disk below 50GB after cleanup", file=sys.stderr)
            return 1

    result = run_phase620_v2(
        REPO,
        poll_interval_sec=args.poll_interval_sec,
        workers=args.workers,
        days=days,
        variants=variants,
        resume=not args.no_resume,
        aggregate_only=args.aggregate_only,
    )
    if not args.aggregate_only:
        result.setdefault("mandatory_answers", {})["1_disk_freed_gb"] = cleanup.get("freed_gb")
    print(f"verdict={result.get('verdict')}", flush=True)
    print(json.dumps(result.get("mandatory_answers"), ensure_ascii=False, indent=2), flush=True)
    for k, v in (result.get("output_paths") or {}).items():
        print(f"{k}={v}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
