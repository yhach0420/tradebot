#!/usr/bin/env python3
"""Phase382: Profit driver preservation monitor for Stack C."""

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
    from research.phase382_profit_driver_preservation_monitor import load_session_profit_driver_trades

    t0 = time.monotonic()
    try:
        result = load_session_profit_driver_trades(
            job["session_meta"],
            reports_dir=Path(job["reports_dir"]),
            min_day=job["min_day"],
            max_day=job.get("max_day"),
        )
        if int(result.get("trade_count") or 0) <= 0:
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
    parser = argparse.ArgumentParser(description="Phase382 profit driver preservation monitor")
    parser.add_argument("--min-day", default=None, help="Monitor start day (default Period B start)")
    parser.add_argument("--max-day", default=None, help="Monitor end day (optional)")
    parser.add_argument("--day", default=None, help="Single day filter (YYYYMMDD)")
    parser.add_argument("--baseline-json", default="phase381_winner_profile_summary.json")
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--no-parallel", action="store_false", dest="parallel")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-tick-csv", action="store_true", default=True)
    args = parser.parse_args()

    _bootstrap()
    from research.phase376_production_daily_pnl_review import discover_session_roots, discover_sessions_for_phase376
    from research.phase377_daily_regime_breakdown import PERIOD_B_START
    from research.phase382_profit_driver_preservation_monitor import Phase382ProfitDriverPreservationMonitor

    min_day = str(args.min_day or PERIOD_B_START)
    max_day = str(args.max_day) if args.max_day else None
    sessions = discover_sessions_for_phase376(
        discover_session_roots(REPO),
        min_day=min_day,
        max_day=max_day,
        all_available=True,
    )
    if args.day:
        sessions = [s for s in sessions if str(s.get("day_key") or s.get("day")) == args.day]

    monitor = Phase382ProfitDriverPreservationMonitor(
        reports_dir=REPORTS,
        min_day=min_day,
        max_day=max_day,
        baseline_name=args.baseline_json,
    )
    jobs = [
        {
            "session_meta": meta,
            "reports_dir": str(REPORTS),
            "min_day": min_day,
            "max_day": max_day,
        }
        for meta in sessions
    ]

    print(
        f"phase382 min_day={min_day} max_day={max_day or 'open'} sessions={len(sessions)}",
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
                    monitor.ingest_session(st["result"])
                    loaded += 1
                else:
                    skipped += 1
    else:
        for job in jobs:
            st = _worker(job)
            if st.get("ok"):
                monitor.ingest_session(st["result"])
                loaded += 1
            else:
                skipped += 1

    result = monitor.analyze(
        sessions_discovered=len(sessions),
        sessions_evaluated=loaded,
        wall_runtime_sec=time.monotonic() - t0,
    )
    paths = monitor.write_outputs(result)
    summary = {k: v for k, v in result.items() if not k.startswith("_")}
    verdict = summary.get("final_verdict") or {}
    wm = summary.get("window_metrics") or {}
    print(f"wall_runtime_sec={round(time.monotonic()-t0,1)} loaded={loaded} skipped={skipped}", flush=True)
    print("\n=== Phase382 Summary ===", flush=True)
    print(f"preservation_status: {verdict.get('preservation_status')}", flush=True)
    print(f"trailing_mfe_exit_pnl: {wm.get('trailing_mfe_exit_pnl')}", flush=True)
    print(f"overlap_replaced_pnl: {wm.get('overlap_replaced_pnl')}", flush=True)
    print(f"rank_21_40_winning_pnl: {wm.get('rank_21_40_winning_pnl')}", flush=True)
    print(f"board_low_winner_count: {wm.get('board_low_winner_count')}", flush=True)
    print(f"low_mfe_stop_hit_count: {wm.get('low_mfe_stop_hit_count')}", flush=True)
    rec = str(verdict.get("recommendation") or "").replace("\u2014", "-")
    print(f"recommendation: {rec}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
