#!/usr/bin/env python3
"""Phase388: 1.5M live candidate validation."""

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
    parser = argparse.ArgumentParser(description="Phase388 1.5M live candidate validation")
    parser.add_argument("--min-day", default="20260529")
    parser.add_argument("--max-day", default="20260612")
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--no-parallel", action="store_false", dest="parallel")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-tick-csv", action="store_true", default=True)
    args = parser.parse_args()

    _bootstrap()
    from research.phase376_production_daily_pnl_review import discover_session_roots, discover_sessions_for_phase376
    from research.phase388_cap1500k_live_candidate_validation import Phase388Cap1500kLiveCandidateValidation

    sessions = discover_sessions_for_phase376(
        discover_session_roots(REPO),
        min_day=args.min_day,
        max_day=args.max_day,
        all_available=True,
    )
    study = Phase388Cap1500kLiveCandidateValidation(
        reports_dir=REPORTS,
        min_day=args.min_day,
        max_day=args.max_day,
    )
    jobs = [
        {"session_meta": m, "reports_dir": str(REPORTS), "min_day": args.min_day, "max_day": args.max_day}
        for m in sessions
    ]
    print(
        f"phase388 period={args.min_day}-{args.max_day} candidate=1500k/cap2 sessions={len(sessions)}",
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
                    study.ingest_session(st["result"])
                    loaded += 1
                else:
                    skipped += 1
    else:
        for job in jobs:
            st = _worker(job)
            if st.get("ok"):
                study.ingest_session(st["result"])
                loaded += 1
            else:
                skipped += 1

    result = study.run(
        wall_runtime_sec=time.monotonic() - t0,
        sessions_discovered=len(sessions),
        sessions_evaluated=loaded,
    )
    paths = study.write_outputs(result)
    ans = result.get("required_answers") or {}
    cand = result.get("candidate") or {}

    print(f"wall_runtime_sec={round(time.monotonic()-t0,1)} loaded={loaded} skipped={skipped}", flush=True)
    print("\n=== Phase388 Summary ===", flush=True)
    print(f"pnl={cand.get('total_pnl_yen')} return%={cand.get('return_pct')} PF={cand.get('profit_factor')}", flush=True)
    print(f"accepted={cand.get('accepted_trade_count')} rejected={cand.get('rejected_trade_count')}", flush=True)
    print(f"delta_vs_2m={ans.get('pnl_delta_vs_2m_cap2_yen')} recommend={ans.get('recommended_capital_label')}", flush=True)
    print(f"profitable={ans.get('is_1500k_profitable')} margin_risk={ans.get('margin_call_risk')}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
