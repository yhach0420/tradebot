#!/usr/bin/env python3
"""Phase382: Capital-constrained backtest for Stack C."""

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
    from research.phase382_capital_constrained_backtest import load_session_capital_backtest_trades

    t0 = time.monotonic()
    try:
        result = load_session_capital_backtest_trades(
            job["session_meta"],
            reports_dir=Path(job["reports_dir"]),
            min_day=job["min_day"],
            max_day=job.get("max_day"),
        )
        if int(result.get("valid_count") or 0) <= 0 and int(result.get("excluded_count") or 0) <= 0:
            return {
                "ok": False,
                "session_id": job["session_meta"].get("session_id"),
                "error": result.get("error") or "no_trades",
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
    parser = argparse.ArgumentParser(description="Phase382 capital-constrained backtest")
    parser.add_argument("--min-day", default="20260529")
    parser.add_argument("--max-day", default="20260612")
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--no-parallel", action="store_false", dest="parallel")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-tick-csv", action="store_true", default=True)
    args = parser.parse_args()

    _bootstrap()
    from research.phase376_production_daily_pnl_review import discover_session_roots, discover_sessions_for_phase376
    from research.phase382_capital_constrained_backtest import Phase382CapitalConstrainedBacktest

    sessions = discover_sessions_for_phase376(
        discover_session_roots(REPO),
        min_day=args.min_day,
        max_day=args.max_day,
        all_available=True,
    )
    audit = Phase382CapitalConstrainedBacktest(
        reports_dir=REPORTS,
        min_day=args.min_day,
        max_day=args.max_day,
    )
    jobs = [
        {
            "session_meta": m,
            "reports_dir": str(REPORTS),
            "min_day": args.min_day,
            "max_day": args.max_day,
        }
        for m in sessions
    ]

    print(
        f"phase382_capital period={args.min_day}-{args.max_day} sessions={len(sessions)}",
        flush=True,
    )
    t0 = time.monotonic()
    loaded = skipped = 0
    workers = max(1, args.max_workers) if args.parallel else 1
    if args.parallel and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for fut in as_completed({pool.submit(_worker, j): j for j in jobs}):
                st = fut.result()
                if st.get("ok"):
                    audit.ingest_session(st["result"])
                    loaded += 1
                else:
                    skipped += 1
    else:
        for job in jobs:
            st = _worker(job)
            if st.get("ok"):
                audit.ingest_session(st["result"])
                loaded += 1
            else:
                skipped += 1

    result = audit.run(
        parallel=args.parallel,
        max_workers=args.max_workers,
        wall_runtime_sec=time.monotonic() - t0,
        sessions_discovered=len(sessions),
        sessions_evaluated=loaded,
    )
    paths = audit.write_outputs(result)
    summary = {k: v for k, v in result.items() if not str(k).startswith("_")}
    ref = summary.get("reference_unconstrained") or {}
    best = summary.get("recommended_scenario")
    print(f"wall_runtime_sec={round(time.monotonic()-t0,1)} loaded={loaded} skipped={skipped}", flush=True)
    print("\n=== Phase382 Capital Summary ===", flush=True)
    print(f"input_trades: {summary.get('population', {}).get('input_trade_count')}", flush=True)
    print(f"excluded: {summary.get('population', {}).get('excluded_trade_count')}", flush=True)
    print(f"reference_pnl: {ref.get('realized_pnl')}", flush=True)
    print(f"recommended: {best}", flush=True)
    for s in summary.get("scenarios") or []:
        print(
            f"  {s.get('scenario_id')}: final={s.get('final_equity')} "
            f"return%={s.get('total_return_pct')} dd={s.get('max_drawdown_yen')} "
            f"accepted={s.get('accepted_trade_count')} rejected={s.get('rejected_trade_count')}",
            flush=True,
        )
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
