#!/usr/bin/env python3
"""Phase375: Dynamic40 rank quality improvement shadow validation."""

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
    parser = argparse.ArgumentParser(description="Phase375 dynamic40 rank quality shadow")
    parser.add_argument("--min-day", default=None)
    parser.add_argument("--max-day", default=None)
    parser.add_argument("--recent-days", type=int, default=None)
    parser.add_argument("--all-available-sessions", action="store_true", default=False)
    parser.add_argument("--parallel", action="store_true", default=False)
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-streaming", action="store_false", dest="streaming")
    parser.add_argument("--no-tick-csv", action="store_true", default=True)
    args = parser.parse_args()

    _bootstrap()
    from research.phase374_dynamic40_universe_quality_review import (
        discover_session_roots,
        discover_sessions_for_phase374,
    )
    from research.phase375_dynamic40_rank_quality_shadow import (
        Phase375Dynamic40RankQualityShadow,
    )

    roots = discover_session_roots(REPO)
    sessions = discover_sessions_for_phase374(
        roots,
        min_day=args.min_day,
        max_day=args.max_day,
        recent_days=args.recent_days,
        all_available=args.all_available_sessions,
    )

    audit = Phase375Dynamic40RankQualityShadow(reports_dir=REPORTS, repo_root=REPO)
    jobs = [
        {"session_meta": meta, "reports_dir": str(REPORTS), "streaming": args.streaming}
        for meta in sessions
    ]

    print(
        f"phase375 sessions={len(sessions)} parallel={args.parallel} "
        f"max_workers={args.max_workers} all_available={args.all_available_sessions}",
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
    )
    summary = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))
    verdict = summary.get("verdict") or {}
    print(f"wall_runtime_sec={round(time.monotonic()-t0,1)} sessions_skipped={errors}", flush=True)
    print("\n=== Phase375 Summary ===", flush=True)
    print(f"days_evaluated: {summary.get('population', {}).get('days_evaluated')}", flush=True)
    print(f"improvement_feasible: {verdict.get('improvement_feasible')}", flush=True)
    print(f"best_variant_production: {verdict.get('best_variant_production')}", flush=True)
    print(f"adopt_recommendation: {verdict.get('adopt_recommendation')}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
