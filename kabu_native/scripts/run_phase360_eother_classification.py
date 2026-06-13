#!/usr/bin/env python3
"""Phase360: E_other low-MFE stop_hit deep classification."""

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
    from research.phase360_eother_classification import load_session_eother_trades

    t0 = time.monotonic()
    try:
        result = load_session_eother_trades(
            job["session_meta"],
            reports_dir=Path(job["reports_dir"]),
        )
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
    parser = argparse.ArgumentParser(description="Phase360 E_other classification")
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--no-parallel", action="store_false", dest="parallel")
    parser.add_argument("--max-workers", type=int, default=2)
    args = parser.parse_args()

    _bootstrap()
    from research.phase357_actual_exit_audit import MAX_DAY, MIN_DAY
    from research.phase360_eother_classification import Phase360EotherClassification
    from small_paper.phase356_live_session_evaluation import discover_live_sessions_for_phase356

    sessions = discover_live_sessions_for_phase356(SMALL_PAPER, min_day=MIN_DAY)
    sessions = [s for s in sessions if str(s.get("day_key") or "") <= MAX_DAY]

    audit = Phase360EotherClassification(reports_dir=REPORTS)
    jobs = [{"session_meta": meta, "reports_dir": str(REPORTS)} for meta in sessions]

    print(
        f"phase360 sessions={len(sessions)} parallel={args.parallel} "
        f"max_workers={args.max_workers}",
        flush=True,
    )
    t0 = time.monotonic()
    if args.parallel and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=max(1, args.max_workers)) as pool:
            futures = {pool.submit(_worker, job): job for job in jobs}
            for fut in as_completed(futures):
                st = fut.result()
                if st.get("ok"):
                    audit.ingest_session(st["result"])
                    n = len(st["result"].get("eother_trades") or [])
                    print(f"  ok {st.get('session_id')} eother={n}", flush=True)
                else:
                    print(f"  FAIL {st.get('session_id')} {st.get('error')}", flush=True)
    else:
        for job in jobs:
            st = _worker(job)
            if st.get("ok"):
                audit.ingest_session(st["result"])
                n = len(st["result"].get("eother_trades") or [])
                print(f"  ok {st.get('session_id')} eother={n}", flush=True)

    paths = audit.finalize_outputs()
    summary = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))
    print(f"wall_runtime_sec={round(time.monotonic()-t0,1)}", flush=True)
    print("\n=== Phase360 Summary ===", flush=True)
    print(f"eother count: {summary.get('eother_low_mfe_count')}", flush=True)
    print(f"eother loss: {summary.get('eother_low_mfe_total_pnl_yen_100')}", flush=True)
    best = summary.get("best_entry_guard_candidate") or {}
    print(f"best guard: {best.get('cluster_id')} - {best.get('guard_rule')}", flush=True)
    print(f"delta_yen: {best.get('delta_yen')} delta_pf: {best.get('delta_pf')}", flush=True)
    print(f"outputs: {paths}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
