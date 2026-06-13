#!/usr/bin/env python3
"""
Phase343-pre: benchmark sequential vs parallel session evaluation.

Default: 3 sessions, 150k rows, max_workers=2 for parallel leg.
Output: phase343_parallel_eval_benchmark.json
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
PUSH_ROOT = REPO / "kabu_native" / "data" / "push_jsonl"
REPORTS = REPO / "kabu_native" / "results" / "reports"
DEFAULT_CONFIG = (
    REPO
    / "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)
JST = ZoneInfo("Asia/Tokyo")
EVAL_MODE = "phase343_board_failure_mfe"


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _discover_sessions(args: argparse.Namespace) -> list[dict]:
    from research.phase336_realtime_board_full_replay import discover_push_jsonl_sessions

    push_root = args.push_root if args.push_root.is_absolute() else REPO / args.push_root
    sessions = discover_push_jsonl_sessions(push_root)
    if args.skip_sessions:
        sessions = sessions[args.skip_sessions :]
    if args.day_key:
        sessions = [s for s in sessions if s.get("day_key") == args.day_key]
    if args.session:
        sessions = [s for s in sessions if args.session in str(s.get("session_id") or "")]
    if args.max_sessions is not None:
        sessions = sessions[: args.max_sessions]
    return sessions


def _run_leg(
    *,
    sessions: list[dict],
    config_path: Path,
    max_push_rows: int,
    streaming: bool,
    parallel: bool,
    max_workers: int,
    worker_temp_dir: Path | None,
    keep_worker_temp: bool,
) -> tuple[float, float, int, list[dict]]:
    from research.phase343_board_failure_mfe_tuning import Phase343BoardFailureMfeAggregator
    from research.streaming_eval_parallel_runner import (
        ParallelEvalConfig,
        ingest_session_results_to_aggregator,
        output_paths_size_mb,
        run_parallel_session_evaluation,
    )

    agg = Phase343BoardFailureMfeAggregator(reports_dir=REPORTS)
    for path in agg.paths().values():
        if path.is_file():
            path.unlink()

    par_cfg = ParallelEvalConfig(
        parallel=parallel,
        max_workers=max_workers,
        worker_temp_dir=worker_temp_dir,
        cleanup_temp=not keep_worker_temp,
    )
    print(
        f"  leg parallel={parallel} max_workers={par_cfg.effective_workers()} "
        f"sessions={len(sessions)}",
        flush=True,
    )
    run = run_parallel_session_evaluation(
        sessions=sessions,
        mode=EVAL_MODE,
        repo_root=REPO,
        config_path=config_path,
        max_push_rows=max_push_rows,
        streaming=streaming,
        parallel_config=par_cfg,
        progress=lambda msg: print(f"    {msg}", flush=True),
    )
    ingest_session_results_to_aggregator(agg, run)
    paths = agg.finalize_outputs()
    out_mb = output_paths_size_mb(paths)
    peak_mb = max(run.peak_memory_mb, getattr(agg, "peak_memory_mb", 0.0))
    sessions_failed = len(run.failed_sessions)
    gc.collect()
    return run.wall_runtime_sec, peak_mb, out_mb, run.failed_sessions


def main() -> int:
    try:
        import tracemalloc

        tracemalloc.start()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description="Phase343-pre parallel eval benchmark")
    parser.add_argument("--push-root", type=Path, default=PUSH_ROOT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--max-sessions", type=int, default=3)
    parser.add_argument("--skip-sessions", type=int, default=0)
    parser.add_argument("--max-rows-per-session", type=int, default=150000)
    parser.add_argument("--date", dest="day_key", default=None)
    parser.add_argument("--session", default=None)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-streaming", action="store_false", dest="streaming")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--worker-temp-dir", type=Path, default=None)
    parser.add_argument("--keep-worker-temp", action="store_true", default=False)
    parser.add_argument("--skip-sequential", action="store_true", default=False)
    args = parser.parse_args()

    _bootstrap()

    from research.streaming_eval_parallel_runner import write_parallel_eval_benchmark

    sessions = _discover_sessions(args)
    config_path = args.config if args.config.is_absolute() else REPO / args.config
    benchmark_path = REPORTS / "phase343_parallel_eval_benchmark.json"

    print(f"phase343-pre benchmark sessions={len(sessions)}", flush=True)

    seq_runtime = 0.0
    seq_peak = 0.0
    seq_out_mb = 0.0
    seq_failed: list[dict] = []
    if not args.skip_sequential:
        print("\n[1/2] Sequential leg (max_workers=1)...", flush=True)
        seq_runtime, seq_peak, seq_out_mb, seq_failed = _run_leg(
            sessions=sessions,
            config_path=config_path,
            max_push_rows=args.max_rows_per_session,
            streaming=args.streaming,
            parallel=False,
            max_workers=1,
            worker_temp_dir=args.worker_temp_dir,
            keep_worker_temp=args.keep_worker_temp,
        )

    print("\n[2/2] Parallel leg...", flush=True)
    par_runtime, par_peak, par_out_mb, par_failed = _run_leg(
        sessions=sessions,
        config_path=config_path,
        max_push_rows=args.max_rows_per_session,
        streaming=args.streaming,
        parallel=True,
        max_workers=args.max_workers,
        worker_temp_dir=args.worker_temp_dir,
        keep_worker_temp=args.keep_worker_temp,
    )

    payload = write_parallel_eval_benchmark(
        benchmark_path,
        sequential_runtime_sec=seq_runtime,
        parallel_runtime_sec=par_runtime,
        max_workers=args.max_workers,
        sessions_evaluated=len(sessions),
        sessions_failed=max(len(seq_failed), len(par_failed)),
        peak_memory_mb=max(seq_peak, par_peak),
        output_size_mb=par_out_mb,
        extra={
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "eval_mode": EVAL_MODE,
            "sequential_peak_memory_mb": seq_peak,
            "parallel_peak_memory_mb": par_peak,
            "sequential_output_size_mb": seq_out_mb,
            "parallel_output_size_mb": par_out_mb,
            "sequential_failed_sessions": seq_failed,
            "parallel_failed_sessions": par_failed,
            "platform": sys.platform,
        },
    )

    print("\n=== Phase343-pre Benchmark ===", flush=True)
    print(f"sequential_runtime_sec: {payload['sequential_runtime_sec']}", flush=True)
    print(f"parallel_runtime_sec: {payload['parallel_runtime_sec']}", flush=True)
    print(f"speedup_ratio: {payload['speedup_ratio']}", flush=True)
    print(f"peak_memory_mb: {payload['peak_memory_mb']}", flush=True)
    print(f"output_size_mb: {payload['output_size_mb']}", flush=True)
    print(f"sessions_failed: {payload['sessions_failed']}", flush=True)
    print(f"benchmark: {benchmark_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
