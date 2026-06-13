#!/usr/bin/env python3
"""Phase363: C03 guard robustness validation (6/12 dependency)."""

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
    from research.phase362_stack_validation import load_session_stack_trades

    t0 = time.monotonic()
    try:
        result = load_session_stack_trades(
            job["session_meta"],
            reports_dir=Path(job["reports_dir"]),
        )
        if int(result.get("trade_count_actual") or 0) <= 0:
            return {
                "ok": False,
                "session_id": job["session_meta"].get("session_id"),
                "error": result.get("error") or "no_observer_exit_trades",
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
    parser = argparse.ArgumentParser(description="Phase363 C03 robustness validation")
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--no-parallel", action="store_false", dest="parallel")
    parser.add_argument("--max-workers", type=int, default=2)
    args = parser.parse_args()

    _bootstrap()
    from research.phase363_c03_robustness_validation import MIN_DAY, Phase363C03Robustness
    from small_paper.phase356_live_session_evaluation import discover_live_sessions_for_phase356

    sessions = discover_live_sessions_for_phase356(SMALL_PAPER, min_day=MIN_DAY)

    audit = Phase363C03Robustness(reports_dir=REPORTS)
    jobs = [{"session_meta": meta, "reports_dir": str(REPORTS)} for meta in sessions]

    print(
        f"phase363 sessions={len(sessions)} parallel={args.parallel} "
        f"max_workers={args.max_workers} min_day={MIN_DAY}",
        flush=True,
    )
    t0 = time.monotonic()
    errors = 0
    if args.parallel and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=max(1, args.max_workers)) as pool:
            futures = {pool.submit(_worker, job): job for job in jobs}
            for fut in as_completed(futures):
                st = fut.result()
                if st.get("ok"):
                    audit.ingest_session(st["result"])
                    print(f"  ok {st.get('session_id')}", flush=True)
                else:
                    errors += 1
                    print(f"  SKIP {st.get('session_id')} {st.get('error')}", flush=True)
    else:
        for job in jobs:
            st = _worker(job)
            if st.get("ok"):
                audit.ingest_session(st["result"])
                print(f"  ok {st.get('session_id')}", flush=True)
            else:
                errors += 1
                print(f"  SKIP {st.get('session_id')} {st.get('error')}", flush=True)

    paths = audit.finalize_outputs(
        wall_runtime_sec=time.monotonic() - t0,
        sessions_discovered=len(sessions),
    )
    summary = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))
    ans = summary.get("answers") or {}
    verdict = summary.get("verdict") or {}
    print(f"wall_runtime_sec={round(time.monotonic()-t0,1)} skipped={errors}", flush=True)
    print("\n=== Phase363 Summary ===", flush=True)
    print(f"full delta (all): {ans.get('q6_dynamic40_comparison', {}).get('all_symbols_full_delta')}", flush=True)
    print(f"exclude 612 delta: {ans.get('q3_exclude_612_delta_yen')}", flush=True)
    print(f"exclude 612 delta>0: {ans.get('q4_exclude_612_delta_positive')}", flush=True)
    print(f"improved/worsened days: {ans.get('q5_improved_vs_worsened_days')}", flush=True)
    print(f"verdict: {verdict.get('recommendation')}", flush=True)
    print(f"outputs: {paths}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
