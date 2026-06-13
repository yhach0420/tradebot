#!/usr/bin/env python3
"""
Phase345: Board failure exit forensic review (mfe_lt_0p2_confirm5).

Replays Phase344 sessions with forensic collector; no EXIT rule changes.
Default: skip 3 sessions (Phase343 overlap), max 5 sessions (Phase344 set).
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
EVAL_MODE = "phase345_board_failure_forensic"


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

    parser = argparse.ArgumentParser(description="Phase345 board failure forensic review")
    parser.add_argument("--push-root", type=Path, default=PUSH_ROOT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--max-sessions", type=int, default=5)
    parser.add_argument("--skip-sessions", type=int, default=3)
    parser.add_argument("--max-rows-per-session", type=int, default=150000)
    parser.add_argument("--date", dest="day_key", default=None)
    parser.add_argument("--session", default=None)
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-streaming", action="store_false", dest="streaming")
    parser.add_argument("--no-tick-csv", action="store_true", default=True)

    _bootstrap()

    from research.phase336_realtime_board_full_replay import discover_push_jsonl_sessions
    from research.phase345_board_failure_forensic import Phase345ForensicReview
    from research.streaming_eval_parallel_runner import (
        add_parallel_eval_args,
        parallel_config_from_args,
        run_parallel_session_evaluation,
    )
    from small_paper.board_failure_forensic_pack import VARIANT_ID

    add_parallel_eval_args(parser)
    args = parser.parse_args()

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

    review = Phase345ForensicReview(reports_dir=REPORTS)
    par_cfg = parallel_config_from_args(args)
    review.parallel_enabled = par_cfg.parallel and par_cfg.effective_workers() > 1
    review.parallel_max_workers = par_cfg.effective_workers()

    print(
        f"phase345 forensic variant={VARIANT_ID} sessions={len(sessions)} "
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
    for result in run.session_results:
        if result.error:
            continue
        review.ingest_forensic_rows(result.trade_rows)
    review.wall_runtime_sec = time.monotonic() - t0
    review.peak_memory_mb = max(review.peak_memory_mb, run.peak_memory_mb)
    gc.collect()

    paths = review.finalize_outputs()
    summary = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))
    conclusions = summary.get("conclusions") or {}

    print("\n=== Phase345 Forensic Review ===", flush=True)
    print(f"positions: {summary.get('positions_analyzed')}", flush=True)
    print(f"profit_take_miss: {summary.get('profit_take_miss_count')} ({summary.get('profit_take_miss_total_yen')} yen)", flush=True)
    print(f"false_positive_rate: {summary.get('false_positive_rate')}", flush=True)
    print(f"correct_cut_rate: {summary.get('correct_cut_rate')}", flush=True)
    print(f"05/28 delta: {summary.get('day_20260528_total_delta_yen')}", flush=True)
    print(f"q4 continue research: {conclusions.get('q4_continue_board_failure_research')}", flush=True)
    print(f"outputs: {paths}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
