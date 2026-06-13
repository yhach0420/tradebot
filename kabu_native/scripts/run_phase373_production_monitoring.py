#!/usr/bin/env python3
"""Phase373: Production monitoring pack for Phase355+364 live stack."""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
REPORTS = REPO / "kabu_native" / "results" / "reports"


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _worker(job: dict) -> dict:
    _bootstrap()
    from research.phase373_production_monitoring import load_session_production_monitoring

    t0 = time.monotonic()
    try:
        result = load_session_production_monitoring(
            job["session_meta"],
            reports_dir=Path(job["reports_dir"]),
        )
        if int((result.get("metrics") or {}).get("accepted_trade_count") or 0) <= 0:
            return {
                "ok": False,
                "session_id": job["session_meta"].get("session_id"),
                "error": result.get("error") or "no_production_trades",
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
    parser = argparse.ArgumentParser(description="Phase373 production monitoring pack")
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--no-parallel", action="store_false", dest="parallel")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--min-day", default=None, help="Override MIN_DAY (YYYYMMDD)")
    parser.add_argument("--day", default=None, help="Single day filter (YYYYMMDD)")
    args = parser.parse_args()

    _bootstrap()
    from research.phase366_stophit_reclassification import MIN_DAY
    from research.phase373_production_monitoring import Phase373ProductionMonitoring
    from small_paper.phase356_live_session_evaluation import discover_live_sessions_for_phase356

    min_day = str(args.min_day or MIN_DAY)
    sessions = discover_live_sessions_for_phase356(SMALL_PAPER, min_day=min_day)
    if args.day:
        sessions = [s for s in sessions if str(s.get("day_key") or s.get("day")) == args.day]

    audit = Phase373ProductionMonitoring(reports_dir=REPORTS)
    jobs = [{"session_meta": meta, "reports_dir": str(REPORTS)} for meta in sessions]

    print(
        f"phase373 sessions={len(sessions)} parallel={args.parallel} "
        f"max_workers={args.max_workers} min_day={min_day}",
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
                    m = st["result"].get("metrics") or {}
                    print(
                        f"  ok {st.get('session_id')} "
                        f"accepted={m.get('accepted_trade_count')} "
                        f"guard_rejects={m.get('total_guard_reject_count')} "
                        f"stop_hits={m.get('stop_hit_count')}",
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
                m = st["result"].get("metrics") or {}
                print(
                    f"  ok {st.get('session_id')} "
                    f"accepted={m.get('accepted_trade_count')} "
                    f"guard_rejects={m.get('total_guard_reject_count')} "
                    f"stop_hits={m.get('stop_hit_count')}",
                    flush=True,
                )
            else:
                errors += 1
                print(f"  SKIP {st.get('session_id')} {st.get('error')}", flush=True)

    paths = audit.finalize_outputs(
        wall_runtime_sec=time.monotonic() - t0,
        sessions_discovered=len(sessions),
        sessions_evaluated=evaluated,
    )
    summary = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))
    metrics = summary.get("metrics") or {}
    checks = summary.get("daily_checks") or {}
    print(f"wall_runtime_sec={round(time.monotonic()-t0,1)} sessions_skipped={errors}", flush=True)
    print("\n=== Phase373 Summary ===", flush=True)
    print(f"accepted_trade_count: {metrics.get('accepted_trade_count')}", flush=True)
    print(f"total_guard_reject_count: {metrics.get('total_guard_reject_count')}", flush=True)
    print(f"stop_hit_count: {metrics.get('stop_hit_count')}", flush=True)
    print(f"low_mfe_stop_hit_count: {metrics.get('low_mfe_stop_hit_count')}", flush=True)
    print(f"immediate_death_60s_count: {metrics.get('immediate_death_60s_count')}", flush=True)
    print(f"core10_guard_reject_count: {metrics.get('core10_guard_reject_count')}", flush=True)
    print(f"guards_firing: {checks.get('guards_firing')}", flush=True)
    print(f"core10_not_caught_by_guards: {checks.get('core10_not_caught_by_guards')}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
