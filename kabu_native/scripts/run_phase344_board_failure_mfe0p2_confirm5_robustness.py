#!/usr/bin/env python3
"""
Phase344: mfe_lt_0p2_confirm5 robustness on additional sessions.

Default: --max-sessions 5 --skip-sessions 3 --max-rows-per-session 150000
Output: kabu_native/results/reports/phase344_board_failure_mfe0p2_confirm5_robustness_*
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
import time
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
EVAL_MODE = "phase344_board_failure_mfe0p2_confirm5"


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    try:
        import tracemalloc

        tracemalloc.start()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(
        description="Phase344 mfe_lt_0p2_confirm5 robustness validation"
    )
    parser.add_argument("--push-root", type=Path, default=PUSH_ROOT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--max-sessions", type=int, default=5)
    parser.add_argument(
        "--skip-sessions",
        type=int,
        default=3,
        help="Skip first N sessions (Phase343 overlap default=3)",
    )
    parser.add_argument("--max-rows-per-session", type=int, default=150000)
    parser.add_argument("--date", dest="day_key", default=None)
    parser.add_argument("--session", default=None)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-streaming", action="store_false", dest="streaming")
    parser.add_argument("--no-tick-csv", action="store_true", default=True)

    _bootstrap()

    from research.streaming_eval_parallel_runner import (
        add_parallel_eval_args,
        ingest_session_results_to_aggregator,
        parallel_config_from_args,
        run_parallel_session_evaluation,
    )

    add_parallel_eval_args(parser)
    args = parser.parse_args()

    from research.phase336_realtime_board_full_replay import discover_push_jsonl_sessions
    from research.phase344_board_failure_mfe0p2_robustness import (
        Phase344BoardFailureMfe0p2RobustnessAggregator,
    )
    from small_paper.board_failure_exit_tuning import VARIANT_MFE_LT_0P2_CONFIRM5

    push_root = args.push_root if args.push_root.is_absolute() else REPO / args.push_root
    config_path = args.config if args.config.is_absolute() else REPO / args.config

    sessions = discover_push_jsonl_sessions(push_root)
    if args.skip_sessions:
        sessions = sessions[args.skip_sessions :]
    if args.day_key:
        sessions = [s for s in sessions if s.get("day_key") == args.day_key]
    if args.session:
        sessions = [s for s in sessions if args.session in str(s.get("session_id") or "")]
    if args.max_sessions is not None:
        sessions = sessions[: args.max_sessions]

    agg = Phase344BoardFailureMfe0p2RobustnessAggregator(reports_dir=REPORTS)
    for path in agg.paths().values():
        if path.is_file():
            path.unlink()

    par_cfg = parallel_config_from_args(args)
    agg.parallel_enabled = par_cfg.parallel and par_cfg.effective_workers() > 1
    agg.parallel_max_workers = par_cfg.effective_workers()

    print(
        f"phase344 variant={VARIANT_MFE_LT_0P2_CONFIRM5} sessions={len(sessions)} "
        f"skip={args.skip_sessions} max_rows={args.max_rows_per_session} "
        f"parallel={par_cfg.parallel} max_workers={par_cfg.effective_workers()}",
        flush=True,
    )

    t0 = time.monotonic()
    run = run_parallel_session_evaluation(
        sessions=sessions,
        mode=EVAL_MODE,
        repo_root=REPO,
        config_path=config_path,
        max_push_rows=args.max_rows_per_session,
        streaming=args.streaming,
        parallel_config=par_cfg,
        progress=print,
    )
    ingest_session_results_to_aggregator(agg, run)
    agg.wall_runtime_sec = time.monotonic() - t0
    agg.peak_memory_mb = max(agg.peak_memory_mb, run.peak_memory_mb)
    gc.collect()

    paths = agg.finalize_outputs()
    summary = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))
    verdict = summary.get("robustness_verdict") or {}
    metrics = summary.get("variant_metrics") or {}
    baseline = summary.get("phase343_baseline") or {}

    print("\n=== Phase344 Robustness ===", flush=True)
    print(f"robustness_pass: {verdict.get('robustness_pass')}", flush=True)
    print(f"fail_reasons: {verdict.get('fail_reasons')}", flush=True)
    print(f"wall_runtime_sec: {summary.get('wall_runtime_sec')}", flush=True)
    print(f"parallel: {summary.get('parallel_enabled')} workers: {summary.get('parallel_max_workers')}", flush=True)
    print(f"actual_pnl: {summary.get('actual_total_pnl_yen_100')} delta: {metrics.get('delta_yen')}", flush=True)
    print(f"PF shadow/actual: {metrics.get('profit_factor')} / {summary.get('actual_pf')}", flush=True)
    print(
        f"stop_red: {metrics.get('stop_hit_reduction_count')} "
        f"profit_miss: {metrics.get('profit_take_miss_yen_100')} "
        f"(phase343: {baseline.get('profit_take_miss_yen_100')})",
        flush=True,
    )
    print(f"sessions +/−: {metrics.get('improved_session_count')}/{metrics.get('worsened_session_count')}", flush=True)
    print(f"top_symbol_share: {metrics.get('top_symbol_delta_share')}", flush=True)
    print(f"outputs: {paths}", flush=True)
    return 0 if verdict.get("robustness_pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
