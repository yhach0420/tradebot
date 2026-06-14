#!/usr/bin/env python3
"""Phase374: Dynamic40 universe quality review from historical paper/small_paper sessions."""

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
    from research.phase374_dynamic40_universe_quality_review import load_session_phase374

    t0 = time.monotonic()
    try:
        result = load_session_phase374(
            job["session_meta"],
            reports_dir=Path(job["reports_dir"]),
            streaming=bool(job.get("streaming", True)),
        )
        if result.get("error"):
            return {
                "ok": False,
                "session_id": job["session_meta"].get("session_id"),
                "error": result.get("error"),
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
    parser = argparse.ArgumentParser(description="Phase374 dynamic40 universe quality review")
    parser.add_argument("--min-day", default=None, help="YYYYMMDD inclusive lower bound")
    parser.add_argument("--max-day", default=None, help="YYYYMMDD inclusive upper bound")
    parser.add_argument("--recent-days", type=int, default=None, help="Limit to last N calendar days")
    parser.add_argument("--all-available-sessions", action="store_true", default=False)
    parser.add_argument("--parallel", action="store_true", default=False)
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-streaming", action="store_false", dest="streaming")
    parser.add_argument("--no-tick-csv", action="store_true", default=True)
    args = parser.parse_args()

    _bootstrap()
    from research.phase374_dynamic40_universe_quality_review import (
        Phase374Dynamic40UniverseQualityReview,
        discover_session_roots,
        discover_sessions_for_phase374,
    )

    roots = discover_session_roots(REPO)
    if not roots:
        print("ERROR: no session roots found (small_paper or paper_trade)", flush=True)
        return 1

    sessions = discover_sessions_for_phase374(
        roots,
        min_day=args.min_day,
        max_day=args.max_day,
        recent_days=args.recent_days,
        all_available=args.all_available_sessions,
    )

    audit = Phase374Dynamic40UniverseQualityReview(reports_dir=REPORTS, repo_root=REPO)
    jobs = [
        {
            "session_meta": meta,
            "reports_dir": str(REPORTS),
            "streaming": args.streaming,
        }
        for meta in sessions
    ]

    print(
        f"phase374 sessions={len(sessions)} roots={len(roots)} parallel={args.parallel} "
        f"max_workers={args.max_workers} streaming={args.streaming} "
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
                    r = st["result"]
                    print(
                        f"  ok {st.get('session_id')} "
                        f"trades={len(r.get('trades') or [])} "
                        f"dyn_monitored={len(r.get('dynamic_monitored') or {})}",
                        flush=True,
                    )
                else:
                    errors += 1
                    print(f"  SKIP {st.get('session_id')} {st.get('error')}", flush=True)
    else:
        for job in jobs:
            st = _worker(job)
            if st.get("ok"):
                audit.ingest_session(st["result"])
                evaluated += 1
                r = st["result"]
                print(
                    f"  ok {st.get('session_id')} "
                    f"trades={len(r.get('trades') or [])} "
                    f"dyn_monitored={len(r.get('dynamic_monitored') or {})}",
                    flush=True,
                )
            else:
                errors += 1
                print(f"  SKIP {st.get('session_id')} {st.get('error')}", flush=True)

    paths = audit.finalize_outputs(
        wall_runtime_sec=time.monotonic() - t0,
        sessions_discovered=len(sessions),
        min_day=args.min_day,
        max_day=args.max_day,
    )
    summary = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))
    dyn = summary.get("dynamic40_summary") or {}
    verdicts = summary.get("verdicts") or {}
    classes = summary.get("quality_class_counts") or {}
    print(f"wall_runtime_sec={round(time.monotonic()-t0,1)} sessions_skipped={errors}", flush=True)
    print("\n=== Phase374 Summary ===", flush=True)
    print(f"sessions_evaluated: {evaluated}", flush=True)
    print(f"dynamic40_monitored_symbols: {dyn.get('monitored_symbol_count')}", flush=True)
    print(f"dynamic40_entries: {dyn.get('entry_count')}", flush=True)
    print(f"dynamic40_total_pnl_yen_100: {dyn.get('total_pnl_yen_100')}", flush=True)
    print(f"quality_classes: {classes}", flush=True)
    print(f"verdicts: {verdicts}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
