#!/usr/bin/env python3
"""Phase389: Full-regime live candidate validation."""

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
    from research.phase389_full_regime_live_candidate_validation import load_session_full_regime_capital_trades

    t0 = time.monotonic()
    try:
        result = load_session_full_regime_capital_trades(
            job["session_meta"],
            reports_dir=Path(job["reports_dir"]),
            min_day=job["min_day"],
            max_day=job.get("max_day"),
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
    parser = argparse.ArgumentParser(description="Phase389 full-regime live candidate validation")
    parser.add_argument("--min-day", default="20260518")
    parser.add_argument("--max-day", default="20260612")
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--no-parallel", action="store_false", dest="parallel")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-tick-csv", action="store_true", default=True)
    parser.add_argument(
        "--reprocess-from-cache",
        action="store_true",
        help="Skip session load; reuse phase389_full_regime_trades_cache.json",
    )
    args = parser.parse_args()

    _bootstrap()
    from research.phase376_production_daily_pnl_review import discover_session_roots, discover_sessions_for_phase376
    from research.phase389_full_regime_live_candidate_validation import Phase389FullRegimeLiveCandidateValidation

    sessions = discover_sessions_for_phase376(
        discover_session_roots(REPO),
        min_day=args.min_day,
        max_day=args.max_day,
        all_available=True,
    )
    study = Phase389FullRegimeLiveCandidateValidation(
        reports_dir=REPORTS,
        min_day=args.min_day,
        max_day=args.max_day,
    )
    cache_path = REPORTS / "phase389_full_regime_trades_cache.json"
    t0 = time.monotonic()
    loaded = skipped = 0
    if args.reprocess_from_cache and cache_path.is_file():
        import json

        study.all_trades = json.loads(cache_path.read_text(encoding="utf-8"))
        loaded = len({t.get("day_key") for t in study.all_trades})
        print(f"phase389 reprocess-from-cache trades={len(study.all_trades)} days={loaded}", flush=True)
    else:
        jobs = [
            {"session_meta": m, "reports_dir": str(REPORTS), "min_day": args.min_day, "max_day": args.max_day}
            for m in sessions
        ]
        print(
            f"phase389 period={args.min_day}-{args.max_day} candidate=1500k/cap2 sessions={len(sessions)}",
            flush=True,
        )
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
        sessions_discovered=len(sessions) if not args.reprocess_from_cache else loaded,
        sessions_evaluated=loaded,
    )
    paths = study.write_outputs(result)
    ans = result.get("required_answers") or {}
    full = result.get("full_period") or {}

    print(f"wall_runtime_sec={round(time.monotonic()-t0,1)} loaded={loaded} skipped={skipped}", flush=True)
    print("\n=== Phase389 Summary ===", flush=True)
    print(f"full_pnl={full.get('total_pnl_yen')} final={full.get('final_equity')} dd={full.get('max_drawdown_yen')}", flush=True)
    print(f"period_a={ans.get('period_a_pnl_yen')} period_b={ans.get('period_b_pnl_yen')}", flush=True)
    print(f"recommend={ans.get('recommendation')}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
