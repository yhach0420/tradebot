#!/usr/bin/env python3
"""Phase379: Period-B low-MFE stop_hit deep review."""

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
    from research.phase379_low_mfe_stophit_deep_review import load_session_period_b_trades

    t0 = time.monotonic()
    try:
        result = load_session_period_b_trades(job["session_meta"], reports_dir=Path(job["reports_dir"]))
        if int(result.get("trade_count") or 0) <= 0:
            return {
                "ok": False,
                "session_id": job["session_meta"].get("session_id"),
                "error": result.get("error") or "no_period_b_trades",
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
    parser = argparse.ArgumentParser(description="Phase379 low-MFE stop_hit deep review")
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
    from research.phase379_low_mfe_stophit_deep_review import Phase379LowMfeStophitDeepReview

    roots = discover_session_roots(REPO)
    sessions = discover_sessions_for_phase376(
        roots, min_day=args.min_day, max_day=args.max_day, all_available=True
    )
    audit = Phase379LowMfeStophitDeepReview(reports_dir=REPORTS)
    jobs = [{"session_meta": m, "reports_dir": str(REPORTS)} for m in sessions]

    print(
        f"phase379 period={args.min_day}-{args.max_day} sessions={len(sessions)} "
        f"parallel={args.parallel}",
        flush=True,
    )
    t0 = time.monotonic()
    loaded = skipped = 0
    workers = max(1, args.max_workers) if args.parallel else 1
    if args.parallel and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_worker, j): j for j in jobs}
            for fut in as_completed(futures):
                st = fut.result()
                if st.get("ok"):
                    audit.ingest_session(st["result"])
                    loaded += 1
                else:
                    skipped += 1
                    print(f"  SKIP {st.get('session_id')} {st.get('error')}", flush=True)
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
    print(f"wall_runtime_sec={round(time.monotonic()-t0,1)} loaded={loaded} skipped={skipped}", flush=True)
    print("\n=== Phase379 Summary ===", flush=True)
    print(f"low_mfe_stop_count: {summary.get('low_mfe_stop_count')}", flush=True)
    print(f"top_feature: {summary.get('top_feature_delta')}", flush=True)
    print(f"production_candidates: {summary.get('production_candidates')}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
