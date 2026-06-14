#!/usr/bin/env python3
"""Phase387: CAP2 production shadow validation (monitoring only)."""

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
    parser = argparse.ArgumentParser(description="Phase387 CAP2 shadow validation (no production change)")
    parser.add_argument(
        "--min-day",
        default=None,
        help="Shadow monitor start day (default: day after Phase386 period)",
    )
    parser.add_argument("--max-day", default=None, help="Optional end day YYYYMMDD")
    parser.add_argument("--day", default=None, help="Single day filter")
    parser.add_argument("--initial-equity", type=float, default=2_000_000.0)
    parser.add_argument("--equity-floor", type=float, default=None)
    parser.add_argument("--include-backtest-period", action="store_true", help="Include 20260529+ for shadow replay")
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--no-parallel", action="store_false", dest="parallel")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-tick-csv", action="store_true", default=True)
    args = parser.parse_args()

    _bootstrap()
    from research.phase376_production_daily_pnl_review import discover_session_roots, discover_sessions_for_phase376
    from research.phase387_cap2_shadow_validation import DEFAULT_SHADOW_START_DAY, Phase387Cap2ShadowValidation

    min_day = str(args.min_day or ("20260529" if args.include_backtest_period else DEFAULT_SHADOW_START_DAY))
    max_day = str(args.max_day) if args.max_day else None
    equity_floor = args.equity_floor if args.equity_floor is not None else args.initial_equity * 0.5

    sessions = discover_sessions_for_phase376(
        discover_session_roots(REPO),
        min_day=min_day,
        max_day=max_day,
        all_available=True,
    )
    if args.day:
        sessions = [s for s in sessions if str(s.get("day_key") or s.get("day")) == args.day]

    validator = Phase387Cap2ShadowValidation(
        reports_dir=REPORTS,
        min_day=min_day,
        max_day=max_day,
        initial_equity=args.initial_equity,
        equity_floor=equity_floor,
    )
    jobs = [
        {"session_meta": m, "reports_dir": str(REPORTS), "min_day": min_day, "max_day": max_day}
        for m in sessions
    ]
    print(
        f"phase387 shadow monitor min_day={min_day} max_day={max_day or 'open'} "
        f"sessions={len(sessions)} production_cap=3 shadow_cap=2",
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
                    validator.ingest_session(st["result"])
                    loaded += 1
                else:
                    skipped += 1
    else:
        for job in jobs:
            st = _worker(job)
            if st.get("ok"):
                validator.ingest_session(st["result"])
                loaded += 1
            else:
                skipped += 1

    result = validator.run(
        sessions_discovered=len(sessions),
        sessions_evaluated=loaded,
        wall_runtime_sec=time.monotonic() - t0,
    )
    paths = validator.write_outputs(result)
    ans = result.get("required_answers") or {}
    actual = result.get("actual_cap3") or {}
    shadow = result.get("shadow_cap2") or {}
    add = result.get("cap3_additional") or {}

    print(f"wall_runtime_sec={round(time.monotonic()-t0,1)} loaded={loaded} skipped={skipped}", flush=True)
    print("\n=== Phase387 Shadow Summary ===", flush=True)
    print(f"actual_cap3: accepted={result.get('acceptance', {}).get('actual_cap3_accepted')} pnl={actual.get('total_pnl_yen_100')} PF={actual.get('profit_factor')}", flush=True)
    print(f"shadow_cap2: accepted={result.get('acceptance', {}).get('shadow_cap2_accepted')} pnl={shadow.get('total_pnl_yen_100')} PF={shadow.get('profit_factor')}", flush=True)
    print(f"cap3_additional: n={result.get('acceptance', {}).get('cap3_additional_count')} pnl={add.get('total_pnl_yen_100')} PF={add.get('profit_factor')}", flush=True)
    print(f"cap2_superiority_continues={ans.get('cap2_superiority_continues')}", flush=True)
    print(f"cap3_additional_still_negative={ans.get('cap3_additional_still_negative')}", flush=True)
    for label, path in paths.items():
        if label != "state":
            print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
