#!/usr/bin/env python3
"""
Phase356: Post-Phase355 EXIT rebaseline (shadow only).

Evaluates EXIT candidates on live sessions with Phase355 ENTRY guard population.
Default: live sessions from 20260518+, --parallel --max-workers 2.
Optional: --push-replay for push_jsonl replay (slow; poll_interval_sec=0 required).
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
PUSH_ROOT = REPO / "kabu_native" / "data" / "push_jsonl"
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
REPORTS = REPO / "kabu_native" / "results" / "reports"
DEFAULT_CONFIG = (
    REPO
    / "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)
MIN_DAY = "20260518"


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _live_worker(job: dict[str, Any]) -> dict[str, Any]:
    _bootstrap()
    from small_paper.phase356_live_session_evaluation import evaluate_live_session_phase356

    t0 = time.monotonic()
    try:
        result = evaluate_live_session_phase356(job["session_meta"])
        err = str(result.get("error") or "")
        if err and err != "no_kept_observer_exit_trades" and not result.get("trade_rows"):
            return {
                "ok": False,
                "session_id": job["session_meta"].get("session_id"),
                "error": err,
                "runtime_sec": round(time.monotonic() - t0, 2),
            }
        out_path = Path(job["output_path"])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return {
            "ok": True,
            "output_path": str(out_path),
            "session_id": job["session_meta"].get("session_id"),
            "trade_rows": len(result.get("trade_rows") or []),
            "positions": int(result.get("positions_evaluated") or 0),
            "skipped_pullback": int(result.get("skipped_pullback_dynamic40") or 0),
            "runtime_sec": round(time.monotonic() - t0, 2),
        }
    except Exception as exc:
        return {
            "ok": False,
            "session_id": job.get("session_meta", {}).get("session_id"),
            "error": str(exc),
            "runtime_sec": round(time.monotonic() - t0, 2),
        }


def _run_live_parallel(
    *,
    sessions: list[dict[str, Any]],
    parallel: bool,
    max_workers: int,
    temp_dir: Path,
) -> list[dict[str, Any]]:
    jobs = []
    for i, meta in enumerate(sessions):
        jobs.append(
            {
                "session_meta": meta,
                "output_path": str(temp_dir / f"phase356_live_{i:04d}.json"),
            }
        )

    results: list[dict[str, Any]] = []
    if parallel and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_live_worker, job): job for job in jobs}
            for fut in as_completed(futures):
                results.append(fut.result())
    else:
        for job in jobs:
            results.append(_live_worker(job))
    return results


def _ingest_live_results(agg: Any, temp_dir: Path, jobs: list[dict[str, Any]]) -> None:
    for i, job in enumerate(jobs):
        path = Path(job["output_path"])
        if not path.is_file():
            agg.ingest_session(
                session_meta=job["session_meta"],
                trade_rows=[],
                push_rows=0,
                runtime_sec=0.0,
                error="missing_worker_output",
            )
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        agg.ingest_session(
            session_meta=data.get("session_meta") or job["session_meta"],
            trade_rows=list(data.get("trade_rows") or []),
            push_rows=0,
            runtime_sec=0.0,
            error=str(data.get("error") or ""),
        )
        if int(data.get("skipped_pullback_dynamic40") or 0):
            agg.pullback_guard_reject_count_total += int(data["skipped_pullback_dynamic40"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase356 post-Phase355 EXIT rebaseline")
    parser.add_argument("--push-root", type=Path, default=PUSH_ROOT)
    parser.add_argument("--small-paper-root", type=Path, default=SMALL_PAPER)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--min-day", default=MIN_DAY)
    parser.add_argument("--max-sessions", type=int, default=None)
    parser.add_argument("--max-rows-per-session", type=int, default=None)
    parser.add_argument("--date", dest="day_key", default=None)
    parser.add_argument("--session", default=None)
    parser.add_argument("--poll-interval-sec", type=float, default=0.0)
    parser.add_argument("--push-replay", action="store_true", default=False)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-streaming", action="store_false", dest="streaming")
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--no-parallel", action="store_false", dest="parallel")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--worker-temp-dir", type=Path, default=None)

    _bootstrap()
    args = parser.parse_args()

    from datetime import datetime
    from zoneinfo import ZoneInfo

    from research.phase356_post_phase355_exit_rebaseline import Phase356IncrementalAggregator

    agg = Phase356IncrementalAggregator(reports_dir=REPORTS, min_day_key=args.min_day)
    for path in agg.paths().values():
        if path.is_file():
            path.unlink()

    if args.push_replay:
        from research.phase336_realtime_board_full_replay import discover_push_jsonl_sessions
        from research.streaming_eval_parallel_runner import (
            ingest_session_results_to_aggregator,
            parallel_config_from_args,
            run_parallel_session_evaluation,
        )
        from research.streaming_eval_parallel_runner import ParallelEvalConfig

        push_root = args.push_root if args.push_root.is_absolute() else REPO / args.push_root
        config_path = args.config if args.config.is_absolute() else REPO / args.config
        sessions = discover_push_jsonl_sessions(push_root)
        sessions = [s for s in sessions if str(s.get("day_key") or "") >= args.min_day]
        if args.day_key:
            sessions = [s for s in sessions if s.get("day_key") == args.day_key]
        if args.session:
            sessions = [s for s in sessions if args.session in str(s.get("session_id") or "")]
        if args.max_sessions is not None:
            sessions = sessions[: args.max_sessions]
        par_cfg = ParallelEvalConfig(
            parallel=args.parallel,
            max_workers=args.max_workers,
            worker_temp_dir=args.worker_temp_dir,
        )
        print(
            f"phase356 PUSH-REPLAY sessions={len(sessions)} poll_interval={args.poll_interval_sec}",
            flush=True,
        )
        run = run_parallel_session_evaluation(
            sessions=sessions,
            mode="phase356_exit_rebaseline",
            repo_root=REPO,
            config_path=config_path,
            max_push_rows=args.max_rows_per_session,
            streaming=args.streaming,
            parallel_config=par_cfg,
            extra={"poll_interval_sec": args.poll_interval_sec},
            progress=print,
        )
        ingest_session_results_to_aggregator(agg, run)
    else:
        from small_paper.phase356_live_session_evaluation import discover_live_sessions_for_phase356

        sp_root = (
            args.small_paper_root
            if args.small_paper_root.is_absolute()
            else REPO / args.small_paper_root
        )
        sessions = discover_live_sessions_for_phase356(sp_root, min_day=args.min_day)
        if args.day_key:
            sessions = [s for s in sessions if s.get("day_key") == args.day_key]
        if args.session:
            sessions = [s for s in sessions if args.session in str(s.get("session_id") or "")]
        if args.max_sessions is not None:
            sessions = sessions[: args.max_sessions]

        stamp = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d_%H%M%S")
        temp_dir = args.worker_temp_dir or (REPORTS / f"_phase356_live_temp_{stamp}")
        temp_dir.mkdir(parents=True, exist_ok=True)

        print(
            f"phase356 LIVE sessions={len(sessions)} parallel={args.parallel} "
            f"max_workers={args.max_workers}",
            flush=True,
        )
        t0 = time.monotonic()
        jobs = [
            {
                "session_meta": meta,
                "output_path": str(temp_dir / f"phase356_live_{i:04d}.json"),
            }
            for i, meta in enumerate(sessions)
        ]
        statuses = _run_live_parallel(
            sessions=sessions,
            parallel=args.parallel,
            max_workers=max(1, args.max_workers),
            temp_dir=temp_dir,
        )
        for st in statuses:
            sid = st.get("session_id")
            if st.get("ok"):
                print(
                    f"  ok {sid} positions={st.get('positions')} "
                    f"trade_rows={st.get('trade_rows')} skipped_pb={st.get('skipped_pullback')} "
                    f"runtime={st.get('runtime_sec')}s",
                    flush=True,
                )
            else:
                print(f"  FAIL {sid} {st.get('error')}", flush=True)
        _ingest_live_results(agg, temp_dir, jobs)
        print(f"wall_runtime_sec={round(time.monotonic()-t0,1)}", flush=True)

    gc.collect()
    paths = agg.finalize_outputs()
    summary = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))

    print("\n=== Phase356 Summary ===", flush=True)
    print(f"evaluation_mode: {'push_replay' if args.push_replay else 'live_sessions'}", flush=True)
    print(f"sessions_evaluated: {summary.get('sessions_evaluated')}", flush=True)
    print(f"sessions_failed: {summary.get('sessions_failed')}", flush=True)
    print(f"positions_evaluated: {summary.get('positions_evaluated')}", flush=True)
    print(f"pullback_excluded_trades: {summary.get('pullback_guard_reject_count_total')}", flush=True)
    print(f"actual_total_pnl_yen_100: {summary.get('actual_total_pnl_yen_100')}", flush=True)
    print(f"actual_pf: {summary.get('actual_pf')}", flush=True)
    print(f"best_candidate: {summary.get('best_candidate_by_delta_yen')}", flush=True)
    print(f"best_bd_tuning: {summary.get('best_board_dynamic_tuning')}", flush=True)
    print(f"adopt_shortlist: {summary.get('adopt_candidate_shortlist')}", flush=True)
    for cid, met in (summary.get("candidates") or {}).items():
        if cid == "current_board_dynamic":
            continue
        print(
            f"  {cid}: delta={met.get('delta_yen')} pf={met.get('profit_factor')} "
            f"sessions+{met.get('improved_session_count')}/-{met.get('worsened_session_count')} "
            f"stop_red={met.get('stop_hit_reduction_count')}",
            flush=True,
        )
    focus = summary.get("focus_20260612_am") or {}
    if focus:
        print("focus 20260612 AM:", flush=True)
        for cid, fm in focus.items():
            if cid == "current_board_dynamic":
                print(f"  actual={fm.get('actual_total_pnl_yen_100')} positions={fm.get('positions')}", flush=True)
            else:
                print(f"  {cid}: delta={fm.get('delta_yen')}", flush=True)
    print(f"outputs: {paths}", flush=True)
    return 0 if summary.get("positions_evaluated", 0) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
