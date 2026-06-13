#!/usr/bin/env python3
"""Phase372: Low-MFE stop_hit immediate-death forensic."""

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
    from research.phase372_low_mfe_immediate_death_forensic import (
        load_session_low_mfe_immediate_death,
    )

    t0 = time.monotonic()
    try:
        result = load_session_low_mfe_immediate_death(
            job["session_meta"],
            reports_dir=Path(job["reports_dir"]),
        )
        if not (result.get("all_production_enriched") or []):
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
    parser = argparse.ArgumentParser(description="Phase372 low-MFE immediate death forensic")
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--no-parallel", action="store_false", dest="parallel")
    parser.add_argument("--max-workers", type=int, default=2)
    args = parser.parse_args()

    _bootstrap()
    from research.phase366_stophit_reclassification import MIN_DAY
    from research.phase372_low_mfe_immediate_death_forensic import (
        Phase372LowMfeImmediateDeathForensic,
    )
    from small_paper.phase356_live_session_evaluation import discover_live_sessions_for_phase356

    sessions = discover_live_sessions_for_phase356(SMALL_PAPER, min_day=MIN_DAY)

    audit = Phase372LowMfeImmediateDeathForensic(reports_dir=REPORTS)
    jobs = [{"session_meta": meta, "reports_dir": str(REPORTS)} for meta in sessions]

    print(
        f"phase372 sessions={len(sessions)} parallel={args.parallel} "
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
                    n = int(st["result"].get("residual_count") or 0)
                    print(f"  ok {st.get('session_id')} low_mfe={n}", flush=True)
                else:
                    errors += 1
                    print(f"  SKIP {st.get('session_id')} {st.get('error')}", flush=True)
    else:
        for job in jobs:
            st = _worker(job)
            if st.get("ok"):
                audit.ingest_session(st["result"])
                n = int(st["result"].get("residual_count") or 0)
                print(f"  ok {st.get('session_id')} low_mfe={n}", flush=True)
            else:
                errors += 1
                print(f"  SKIP {st.get('session_id')} {st.get('error')}", flush=True)

    paths = audit.finalize_outputs(
        wall_runtime_sec=time.monotonic() - t0,
        sessions_discovered=len(sessions),
    )
    summary = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))
    conc = summary.get("conclusion") or {}
    print(f"wall_runtime_sec={round(time.monotonic()-t0,1)} sessions_skipped={errors}", flush=True)
    print("\n=== Phase372 Summary ===", flush=True)
    print(f"low_mfe_count: {summary.get('low_mfe_stop_count')}", flush=True)
    print(f"low_mfe_loss: {summary.get('total_low_mfe_loss_yen_100')}", flush=True)
    print(f"largest_loss_cluster: {conc.get('largest_loss_cluster')}", flush=True)
    print(f"positive_clusters: {conc.get('positive_counterfactual_clusters')}", flush=True)
    print(f"best_positive: {conc.get('best_positive_cluster')} delta={conc.get('best_positive_delta_yen')}", flush=True)
    print(f"entry_guard_candidate: {conc.get('entry_guard_candidate')}", flush=True)
    print(f"best_entry_proxy: {conc.get('best_entry_proxy_cluster')} delta={conc.get('best_entry_proxy_delta_yen')}", flush=True)
    print(f"shadow_candidate: {conc.get('shadow_validation_candidate')}", flush=True)
    print(f"recommendation: {conc.get('recommendation')}", flush=True)
    print(f"outputs: {paths}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
