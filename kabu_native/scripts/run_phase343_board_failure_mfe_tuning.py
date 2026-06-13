#!/usr/bin/env python3
"""
Phase343: board_failure_exit MFE filter + confirm_ticks tuning.

Default: --max-sessions 3 --max-rows-per-session 150000
Output: kabu_native/results/reports/phase343_board_failure_mfe_tuning_*
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
PUSH_ROOT = REPO / "kabu_native" / "data" / "push_jsonl"
REPORTS = REPO / "kabu_native" / "results" / "reports"
DEFAULT_CONFIG = (
    REPO
    / "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)
JST = ZoneInfo("Asia/Tokyo")


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _silence_discord_posts() -> Any:
    from small_paper.discord_notifier import SmallPaperDiscordNotifier

    orig = SmallPaperDiscordNotifier._post

    def _silent(self, **kwargs: Any) -> bool:
        return False

    SmallPaperDiscordNotifier._post = _silent  # type: ignore[method-assign]
    return orig


def _restore_discord_posts(orig: Any) -> None:
    from small_paper.discord_notifier import SmallPaperDiscordNotifier

    SmallPaperDiscordNotifier._post = orig  # type: ignore[method-assign]


def _run_session(
    *,
    session_meta: dict[str, Any],
    config_path: Path,
    max_push_rows: Optional[int],
    streaming: bool,
) -> tuple[list[dict[str, Any]], int, float, str]:
    from small_paper.board_failure_exit_tuning import export_board_failure_tuning_trade_rows
    from small_paper.config import load_pilot_config, resolve_output_dir
    from small_paper.pilot_runner import run_push_replay_dry_run

    push_dir = Path(session_meta["push_dir"])
    if not push_dir.is_dir():
        return [], 0, 0.0, f"push_dir_missing:{push_dir}"

    day_key = str(session_meta.get("day_key") or push_dir.name.replace("-", ""))
    cfg = load_pilot_config(config_path)
    cfg = replace(cfg, discord_enabled=False, discord_observer_only=True)

    stamp = datetime.now(JST).strftime("%H%M%S")
    out_dir = resolve_output_dir(cfg, repo_root=REPO, day_key=day_key) / f"phase343_{stamp}"

    orig_post = _silence_discord_posts()
    t0 = time.monotonic()
    try:
        result = run_push_replay_dry_run(
            cfg,
            push_dir=push_dir,
            output_dir=out_dir,
            repo_root=REPO,
            poll_interval_sec=0.0,
            replay_speed_sec=0.0,
            max_push_rows=max_push_rows,
            enable_discord=False,
            write_board_shadow_reports=False,
            enable_board_failure_tuning_shadow=True,
            streaming_push_replay=streaming,
        )
    except Exception as exc:
        return [], 0, time.monotonic() - t0, str(exc)
    finally:
        _restore_discord_posts(orig_post)

    runtime = time.monotonic() - t0
    pack = result.exit_candidate_shadow
    if pack is None:
        return [], 0, runtime, "no_board_failure_tuning_pack"

    trades = export_board_failure_tuning_trade_rows(pack)
    push_rows = int(result.summary.get("push_rows") or result.summary.get("push_messages") or 0)
    return trades, push_rows, runtime, ""


def main() -> int:
    try:
        import tracemalloc

        tracemalloc.start()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description="Phase343 board failure MFE filter tuning")
    parser.add_argument("--push-root", type=Path, default=PUSH_ROOT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--max-sessions", type=int, default=3)
    parser.add_argument("--skip-sessions", type=int, default=0)
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
    from research.phase343_board_failure_mfe_tuning import Phase343BoardFailureMfeAggregator

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

    agg = Phase343BoardFailureMfeAggregator(reports_dir=REPORTS)
    for path in agg.paths().values():
        if path.is_file():
            path.unlink()

    par_cfg = parallel_config_from_args(args)
    print(
        f"phase343 board_failure_mfe_tuning sessions={len(sessions)} "
        f"variants={len(agg.variants)} max_rows={args.max_rows_per_session} "
        f"parallel={par_cfg.parallel} max_workers={par_cfg.effective_workers()}",
        flush=True,
    )
    t0 = time.monotonic()
    run = run_parallel_session_evaluation(
        sessions=sessions,
        mode="phase343_board_failure_mfe",
        repo_root=REPO,
        config_path=config_path,
        max_push_rows=args.max_rows_per_session,
        streaming=args.streaming,
        parallel_config=par_cfg,
        progress=print,
    )
    ingest_session_results_to_aggregator(agg, run)
    gc.collect()

    paths = agg.finalize_outputs()
    summary = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))
    elapsed = round(time.monotonic() - t0, 1)
    baseline = summary.get("phase342_baseline") or {}
    best = summary.get("best_variant_by_tradeoff")
    adopt = summary.get("adopt_ready_variants") or []

    print("\n=== Phase343 Board Failure MFE Tuning ===", flush=True)
    print(f"elapsed_sec: {elapsed}", flush=True)
    print(f"positions: {summary.get('positions_evaluated')}", flush=True)
    print(f"phase342 profit_miss baseline: {baseline.get('profit_take_miss_yen_100')}", flush=True)
    print(f"best_variant_by_tradeoff: {best}", flush=True)
    print(f"adopt_ready_variants: {adopt}", flush=True)
    if best and best in (summary.get("variants") or {}):
        m = summary["variants"][best]
        print(
            f"best delta={m.get('delta_yen')} PF={m.get('profit_factor')} "
            f"stop_red={m.get('stop_hit_reduction_count')} "
            f"profit_miss={m.get('profit_take_miss_yen_100')}",
            flush=True,
        )
    print(f"outputs: {paths}", flush=True)
    return 0 if not adopt else 0


if __name__ == "__main__":
    raise SystemExit(main())
