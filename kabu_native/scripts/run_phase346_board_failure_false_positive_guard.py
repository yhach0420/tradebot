#!/usr/bin/env python3
"""
Phase346: board_failure_exit false-positive guard evaluation.

Default: --max-sessions 8 (phase343 dev 3 + phase344 robustness 5)
Output: kabu_native/results/reports/phase346_board_failure_false_positive_guard_*
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PUSH_ROOT = REPO / "kabu_native" / "data" / "push_jsonl"
REPORTS = REPO / "kabu_native" / "results" / "reports"
DEFAULT_CONFIG = (
    REPO
    / "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)
EVAL_MODE = "phase346_board_failure_false_positive_guard"
PHASE343_SESSION_COUNT = 3


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _tag_sessions(sessions: list[dict], *, skip_sessions: int) -> list[dict]:
    tagged: list[dict] = []
    for i, meta in enumerate(sessions):
        cohort = (
            "phase343_development"
            if i < skip_sessions
            else "phase344_robustness"
        )
        tagged.append({**meta, "session_index": i, "session_cohort": cohort})
    return tagged


def main() -> int:
    try:
        import tracemalloc

        tracemalloc.start()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(
        description="Phase346 board_failure_exit false-positive guard evaluation"
    )
    parser.add_argument("--push-root", type=Path, default=PUSH_ROOT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=8,
        help="Total sessions (default 8 = phase343 3 + phase344 5)",
    )
    parser.add_argument(
        "--skip-sessions",
        type=int,
        default=PHASE343_SESSION_COUNT,
        help="First N sessions tagged phase343_development (default=3)",
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
    from research.phase346_board_failure_false_positive_guard import (
        COHORT_ROBUSTNESS,
        Phase346BoardFailureFalsePositiveGuardAggregator,
    )
    from small_paper.board_failure_false_positive_guard import default_phase346_variants

    push_root = args.push_root if args.push_root.is_absolute() else REPO / args.push_root
    config_path = args.config if args.config.is_absolute() else REPO / args.config

    sessions = discover_push_jsonl_sessions(push_root)
    if args.day_key:
        sessions = [s for s in sessions if s.get("day_key") == args.day_key]
    if args.session:
        sessions = [s for s in sessions if args.session in str(s.get("session_id") or "")]
    if args.max_sessions is not None:
        sessions = sessions[: args.max_sessions]
    sessions = _tag_sessions(sessions, skip_sessions=args.skip_sessions)

    agg = Phase346BoardFailureFalsePositiveGuardAggregator(reports_dir=REPORTS)
    for path in agg.paths().values():
        if path.is_file():
            path.unlink()

    par_cfg = parallel_config_from_args(args)
    agg.parallel_enabled = par_cfg.parallel and par_cfg.effective_workers() > 1
    agg.parallel_max_workers = par_cfg.effective_workers()

    n_variants = len(default_phase346_variants())
    robustness_n = sum(1 for s in sessions if s.get("session_cohort") == COHORT_ROBUSTNESS)
    print(
        f"phase346 variants={n_variants} sessions={len(sessions)} "
        f"(robustness={robustness_n}) max_rows={args.max_rows_per_session} "
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
    robustness = (summary.get("cohorts") or {}).get(COHORT_ROBUSTNESS) or {}
    assessments = summary.get("guard_pass_assessment") or {}
    baseline = summary.get("phase344_baseline") or {}
    base_met = robustness.get("mfe_lt_0p2_confirm5") or {}

    print("\n=== Phase346 False Positive Guard ===", flush=True)
    print(f"guard_pass_variants: {summary.get('guard_pass_variants')}", flush=True)
    print(f"best_variant: {summary.get('best_variant_by_tradeoff')}", flush=True)
    print(f"wall_runtime_sec: {summary.get('wall_runtime_sec')}", flush=True)
    print(
        f"parallel: {summary.get('parallel_enabled')} "
        f"workers: {summary.get('parallel_max_workers')}",
        flush=True,
    )
    print(
        f"baseline (phase344) delta: {baseline.get('delta_yen')} "
        f"profit_miss: {baseline.get('profit_take_miss_yen_100')}",
        flush=True,
    )
    print(
        f"base variant delta: {base_met.get('delta_yen')} "
        f"profit_miss: {base_met.get('profit_take_miss_yen_100')} "
        f"fpr: {base_met.get('false_positive_rate')}",
        flush=True,
    )
    for vid in sorted(assessments, key=lambda v: assessments[v].get("guard_pass", False), reverse=True)[:5]:
        a = assessments[vid]
        m = robustness.get(vid) or {}
        print(
            f"  {vid}: pass={a.get('guard_pass')} delta={m.get('delta_yen')} "
            f"miss={m.get('profit_take_miss_yen_100')} fpr={m.get('false_positive_rate')} "
            f"0528={m.get('session_delta_yen_20260528')}",
            flush=True,
        )
    print(f"outputs: {paths}", flush=True)
    return 0 if summary.get("guard_pass_variants") else 1


if __name__ == "__main__":
    raise SystemExit(main())
