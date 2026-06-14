#!/usr/bin/env python3
"""Phase381: Winner profile review for Period B."""

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
        result = load_session_winner_profile_trades(job["session_meta"], reports_dir=Path(job["reports_dir"]))
        if int(result.get("trade_count") or 0) <= 0:
            return {"ok": False, "session_id": job["session_meta"].get("session_id"), "error": result.get("error") or "no_trades"}
        return {"ok": True, "result": result, "runtime_sec": round(time.monotonic() - t0, 2)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "runtime_sec": round(time.monotonic() - t0, 2)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase381 winner profile review")
    parser.add_argument("--min-day", default="20260528")
    parser.add_argument("--max-day", default="20260612")
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--no-parallel", action="store_false", dest="parallel")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-tick-csv", action="store_true", default=True)
    args = parser.parse_args()

    _bootstrap()
    from research.phase376_production_daily_pnl_review import discover_session_roots, discover_sessions_for_phase376
    from research.phase381_winner_profile_review import Phase381WinnerProfileReview

    sessions = discover_sessions_for_phase376(
        discover_session_roots(REPO), min_day=args.min_day, max_day=args.max_day, all_available=True
    )
    audit = Phase381WinnerProfileReview(reports_dir=REPORTS)
    jobs = [{"session_meta": m, "reports_dir": str(REPORTS)} for m in sessions]
    print(f"phase381 period={args.min_day}-{args.max_day} sessions={len(sessions)}", flush=True)
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

    result = audit.analyze()
    paths = audit.write_outputs(result)
    summary = {k: v for k, v in result.items() if not k.startswith("_")}
    fj = summary.get("final_judgment") or {}
    print(f"wall_runtime_sec={round(time.monotonic()-t0,1)} loaded={loaded} skipped={skipped}", flush=True)
    print("\n=== Phase381 Summary ===", flush=True)
    print(f"winning_count: {summary.get('winning_count')}", flush=True)
    print(f"trailing_pnl: {fj.get('trailing_mfe_pnl')}", flush=True)
    print(f"overlap_pnl: {fj.get('overlap_pnl')}", flush=True)
    priority = str(fj.get("priority_recommendation") or "").replace("\u2014", "-")
    print(f"priority: {priority}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
