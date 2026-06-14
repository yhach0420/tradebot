#!/usr/bin/env python3
"""Phase383: Realistic credit position sizing backtest."""

from __future__ import annotations

import argparse
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
        return {"ok": True, "result": result, "runtime_sec": round(time.monotonic() - t0, 2)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "runtime_sec": round(time.monotonic() - t0, 2)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase383 realistic credit sizing backtest")
    parser.add_argument("--min-day", default="20260529")
    parser.add_argument("--max-day", default="20260612")
    parser.add_argument("--initial-equity", type=float, default=500_000.0)
    parser.add_argument("--equity-floor", type=float, default=None)
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--no-parallel", action="store_false", dest="parallel")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-tick-csv", action="store_true", default=True)
    args = parser.parse_args()

    _bootstrap()
    from research.phase376_production_daily_pnl_review import discover_session_roots, discover_sessions_for_phase376
    from research.phase383_realistic_credit_sizing_backtest import Phase383RealisticCreditSizingBacktest

    equity_floor = args.equity_floor if args.equity_floor is not None else args.initial_equity * 0.5
    sessions = discover_sessions_for_phase376(
        discover_session_roots(REPO),
        min_day=args.min_day,
        max_day=args.max_day,
        all_available=True,
    )
    audit = Phase383RealisticCreditSizingBacktest(
        reports_dir=REPORTS,
        min_day=args.min_day,
        max_day=args.max_day,
        initial_equity=args.initial_equity,
        equity_floor=equity_floor,
    )
    jobs = [
        {"session_meta": m, "reports_dir": str(REPORTS), "min_day": args.min_day, "max_day": args.max_day}
        for m in sessions
    ]
    print(
        f"phase383 period={args.min_day}-{args.max_day} equity={args.initial_equity} "
        f"sessions={len(sessions)}",
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
    print(f"wall_runtime_sec={round(time.monotonic()-t0,1)} loaded={loaded} skipped={skipped}", flush=True)
    print("\n=== Phase383 Summary ===", flush=True)
    print(f"recommended: {summary.get('recommended_scenario')}", flush=True)
    for s in summary.get("scenarios") or []:
        print(
            f"  {s.get('scenario_id')}: final={s.get('final_equity')} return%={s.get('total_return_pct')} "
            f"dd={s.get('max_drawdown_yen')} accepted={s.get('accepted_trade_count')} "
            f"reject_rate={round(float(s.get('reject_rate') or 0)*100,1)}% "
            f"min_maint={s.get('min_maintenance_ratio')} reinvest={s.get('reinvestment_effective')}",
            flush=True,
        )
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
