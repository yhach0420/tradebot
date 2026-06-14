#!/usr/bin/env python3
"""Phase376: Production stack daily PnL and equity curve review."""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REPORTS = REPO / "kabu_native" / "results" / "reports"


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _worker(job: dict) -> dict:
    _bootstrap()
    from research.phase365_production_stack_validation import (
        load_session_production_stack_trades,
    )

    t0 = time.monotonic()
    try:
        result = load_session_production_stack_trades(
            job["session_meta"],
            reports_dir=Path(job["reports_dir"]),
        )
        if int(result.get("trade_count_actual") or 0) <= 0:
            return {
                "ok": False,
                "session_id": job["session_meta"].get("session_id"),
                "error": result.get("error") or "no_observer_exit_trades",
                "runtime_sec": round(time.monotonic() - t0, 2),
            }
        return {
            "ok": True,
            "session_id": job["session_meta"].get("session_id"),
            "result": result,
            "runtime_sec": round(time.monotonic() - t0, 2),
        }
    except Exception as exc:
        return {
            "ok": False,
            "session_id": job.get("session_meta", {}).get("session_id"),
            "error": str(exc),
            "runtime_sec": round(time.monotonic() - t0, 2),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase376 production daily PnL review")
    parser.add_argument("--min-day", default="20260518")
    parser.add_argument("--max-day", default=None)
    parser.add_argument("--all-available-sessions", action="store_true", default=True)
    parser.add_argument("--no-all-available-sessions", action="store_false", dest="all_available_sessions")
    parser.add_argument("--compare-stacks", action="store_true", default=False)
    parser.add_argument("--parallel", action="store_true", default=False)
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-streaming", action="store_false", dest="streaming")
    parser.add_argument("--no-tick-csv", action="store_true", default=True)
    parser.add_argument("--no-png", action="store_true", default=False)
    args = parser.parse_args()

    _bootstrap()
    from research.phase376_production_daily_pnl_review import (
        PRIMARY_STACK,
        Phase376ProductionDailyPnlReview,
        discover_sessions_for_phase376,
        discover_session_roots,
    )

    roots = discover_session_roots(REPO)
    sessions = discover_sessions_for_phase376(
        roots,
        min_day=args.min_day,
        max_day=args.max_day,
        all_available=args.all_available_sessions,
    )

    audit = Phase376ProductionDailyPnlReview(reports_dir=REPORTS)
    jobs = [
        {"session_meta": meta, "reports_dir": str(REPORTS), "streaming": args.streaming}
        for meta in sessions
    ]

    print(
        f"phase376 sessions={len(sessions)} min_day={args.min_day} "
        f"compare_stacks={args.compare_stacks} parallel={args.parallel} "
        f"all_available={args.all_available_sessions}",
        flush=True,
    )
    t0 = time.monotonic()
    errors = 0
    evaluated = 0
    if args.parallel and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=max(1, args.max_workers)) as pool:
            futures = {pool.submit(_worker, job): job for job in jobs}
            for fut in as_completed(futures):
                st = fut.result()
                if st.get("ok"):
                    audit.ingest_session(st["result"])
                    evaluated += 1
                else:
                    errors += 1
                    print(f"  SKIP {st.get('session_id')} {st.get('error')}", flush=True)
    else:
        for job in jobs:
            st = _worker(job)
            if st.get("ok"):
                audit.ingest_session(st["result"])
                evaluated += 1
            else:
                errors += 1
                print(f"  SKIP {st.get('session_id')} {st.get('error')}", flush=True)

    paths = audit.finalize_outputs(
        wall_runtime_sec=time.monotonic() - t0,
        sessions_discovered=len(sessions),
        sessions_evaluated=evaluated,
        compare_stacks=args.compare_stacks,
        write_png=not args.no_png,
        min_day=args.min_day,
    )
    summary = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))
    dep = summary.get("dependency_check") or {}
    print(f"wall_runtime_sec={round(time.monotonic()-t0,1)} sessions_skipped={errors}", flush=True)
    print("\n=== Phase376 Summary (Stack C) ===", flush=True)
    print(f"total_days: {summary.get('total_days')}", flush=True)
    print(f"total_pnl_yen_100: {summary.get('total_pnl_yen_100')}", flush=True)
    print(f"profit_factor: {summary.get('profit_factor')}", flush=True)
    print(f"winning_days/losing_days: {summary.get('winning_days')}/{summary.get('losing_days')}", flush=True)
    print(f"win_day_rate: {summary.get('win_day_rate')}", flush=True)
    print(f"max_drawdown_yen_100: {summary.get('max_drawdown_yen_100')}", flush=True)
    print(f"pnl_excluding_20260612: {summary.get('pnl_excluding_20260612')}", flush=True)
    print(f"is_single_day_dependent: {dep.get('is_single_day_dependent')}", flush=True)
    print(f"primary_stack: {PRIMARY_STACK}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
