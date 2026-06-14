#!/usr/bin/env python3
"""Phase386: Third position quality review (CAP2 vs CAP3 delta)."""

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
    from research.phase381_winner_profile_review import load_session_winner_profile_trades

    t0 = time.monotonic()
    try:
        day = str(job["session_meta"].get("day_key") or job["session_meta"].get("day") or "")
        if day < job["min_day"] or (job.get("max_day") and day > job["max_day"]):
            return {"ok": False, "error": "outside_range", "runtime_sec": round(time.monotonic() - t0, 2)}
        result = load_session_winner_profile_trades(
            job["session_meta"],
            reports_dir=Path(job["reports_dir"]),
        )
        if result.get("error") and not result.get("all_trades"):
            return {
                "ok": False,
                "session_id": job["session_meta"].get("session_id"),
                "error": result.get("error"),
                "runtime_sec": round(time.monotonic() - t0, 2),
            }
        return {"ok": True, "result": result, "runtime_sec": round(time.monotonic() - t0, 2)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "runtime_sec": round(time.monotonic() - t0, 2)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase386 third position quality review")
    parser.add_argument("--min-day", default="20260529")
    parser.add_argument("--max-day", default="20260612")
    parser.add_argument("--initial-equity", type=float, default=2_000_000.0)
    parser.add_argument("--equity-floor", type=float, default=None)
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--no-parallel", action="store_false", dest="parallel")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-tick-csv", action="store_true", default=True)
    args = parser.parse_args()

    _bootstrap()
    from research.phase376_production_daily_pnl_review import discover_session_roots, discover_sessions_for_phase376
    from research.phase386_third_position_quality_review import Phase386ThirdPositionQualityReview

    equity_floor = args.equity_floor if args.equity_floor is not None else args.initial_equity * 0.5
    sessions = discover_sessions_for_phase376(
        discover_session_roots(REPO),
        min_day=args.min_day,
        max_day=args.max_day,
        all_available=True,
    )
    review = Phase386ThirdPositionQualityReview(
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
        f"phase386 period={args.min_day}-{args.max_day} equity={args.initial_equity} sessions={len(sessions)}",
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
                    review.ingest_session(st["result"])
                    loaded += 1
                else:
                    skipped += 1
    else:
        for job in jobs:
            st = _worker(job)
            if st.get("ok"):
                review.ingest_session(st["result"])
                loaded += 1
            else:
                skipped += 1

    result = review.run(
        wall_runtime_sec=time.monotonic() - t0,
        sessions_discovered=len(sessions),
        sessions_evaluated=loaded,
    )
    paths = review.write_outputs(result)
    conc = result.get("conclusions") or {}
    cmp_ = result.get("cohort_comparison") or {}

    print(f"wall_runtime_sec={round(time.monotonic()-t0,1)} loaded={loaded} skipped={skipped}", flush=True)
    print("\n=== Phase386 Summary ===", flush=True)
    print(f"delta_trades={conc.get('delta_trade_count')} delta_pnl={conc.get('delta_total_pnl_yen_100')}", flush=True)
    print(f"third_low_quality={conc.get('third_position_is_low_quality')} recommended_cap={conc.get('recommended_cap')}", flush=True)
    cap2m = cmp_.get("cap2_accepted") or {}
    addm = cmp_.get("cap3_additional") or {}
    print(
        f"CAP2: n={cap2m.get('trade_count')} pnl={cap2m.get('total_pnl_yen_100')} PF={cap2m.get('profit_factor')}",
        flush=True,
    )
    print(
        f"CAP3_add: n={addm.get('trade_count')} pnl={addm.get('total_pnl_yen_100')} PF={addm.get('profit_factor')} "
        f"stop_rate={addm.get('stop_hit_rate')} low_mfe={addm.get('low_mfe_stop_rate')}",
        flush=True,
    )
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
