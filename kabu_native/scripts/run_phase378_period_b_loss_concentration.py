#!/usr/bin/env python3
"""Phase378: Period-B loss concentration review (Stack C, 20260528-20260612)."""

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
    from research.phase365_production_stack_validation import load_session_production_stack_trades
    from research.phase378_period_b_loss_concentration import kept_trades_period_b

    t0 = time.monotonic()
    try:
        result = load_session_production_stack_trades(
            job["session_meta"],
            reports_dir=Path(job["reports_dir"]),
        )
        trades = kept_trades_period_b(result)
        if not trades:
            return {
                "ok": False,
                "session_id": job["session_meta"].get("session_id"),
                "error": result.get("error") or "no_period_b_kept_trades",
                "runtime_sec": round(time.monotonic() - t0, 2),
            }
        return {
            "ok": True,
            "session_id": job["session_meta"].get("session_id"),
            "trades": trades,
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
    parser = argparse.ArgumentParser(description="Phase378 Period-B loss concentration")
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--no-parallel", action="store_false", dest="parallel")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-streaming", action="store_false", dest="streaming")
    parser.add_argument("--no-tick-csv", action="store_true", default=True)
    args = parser.parse_args()

    _bootstrap()
    from research.phase376_production_daily_pnl_review import (
        discover_session_roots,
        discover_sessions_for_phase376,
    )
    from research.phase377_daily_regime_breakdown import PERIOD_B_END, PERIOD_B_START
    from research.phase378_period_b_loss_concentration import Phase378PeriodBLossConcentration

    roots = discover_session_roots(REPO)
    sessions = discover_sessions_for_phase376(
        roots,
        min_day=PERIOD_B_START,
        max_day=PERIOD_B_END,
        all_available=True,
    )

    audit = Phase378PeriodBLossConcentration(reports_dir=REPORTS)
    jobs = [{"session_meta": meta, "reports_dir": str(REPORTS)} for meta in sessions]

    print(
        f"phase378 period={PERIOD_B_START}-{PERIOD_B_END} sessions={len(sessions)} "
        f"parallel={args.parallel} streaming={args.streaming} no_tick_csv={args.no_tick_csv}",
        flush=True,
    )
    t0 = time.monotonic()
    skipped = 0
    loaded = 0
    max_workers = max(1, args.max_workers) if args.parallel else 1

    if args.parallel and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_worker, job): job for job in jobs}
            for fut in as_completed(futures):
                st = fut.result()
                if st.get("ok"):
                    audit.ingest_trades(st["trades"])
                    loaded += 1
                else:
                    skipped += 1
                    print(f"  SKIP {st.get('session_id')} {st.get('error')}", flush=True)
    else:
        for job in jobs:
            st = _worker(job)
            if st.get("ok"):
                audit.ingest_trades(st["trades"])
                loaded += 1
            else:
                skipped += 1
                print(f"  SKIP {st.get('session_id')} {st.get('error')}", flush=True)

    result = audit.analyze()
    paths = audit.write_outputs(result)
    summary = {k: v for k, v in result.items() if not k.startswith("_")}
    judgment = summary.get("period_b_judgment") or {}

    print(f"wall_runtime_sec={round(time.monotonic() - t0, 1)} sessions_loaded={loaded} skipped={skipped}", flush=True)
    print("\n=== Phase378 Summary ===", flush=True)
    print(f"trades: {summary.get('trade_count')} losses: {summary.get('loss_trade_count')}", flush=True)
    print(f"total_pnl: {summary.get('total_pnl_yen_100')} total_loss: {summary.get('total_loss_yen_100')}", flush=True)
    print(f"loss_top20_share: {(summary.get('loss_concentration') or {}).get('loss_top20_share')}", flush=True)
    print(f"dominant_cause: {judgment.get('q4_dominant_loss_cause')}", flush=True)
    print(f"priority: {judgment.get('q5_next_improvement_priority')}", flush=True)
    print(f"core_loss: {judgment.get('core_loss_summary')}", flush=True)
    cons = summary.get("consistency_checks") or {}
    print(f"phase377_match: trades={cons.get('trade_count_matches')} pnl={cons.get('total_pnl_matches')}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
