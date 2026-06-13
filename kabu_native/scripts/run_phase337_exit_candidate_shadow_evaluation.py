#!/usr/bin/env python3
"""
Phase337: Independent EXIT candidate shadow experiment pack (research only).

Default: --max-sessions 1 --max-rows-per-session 30000
Output: kabu_native/results/reports/phase337_exit_candidate_*
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
) -> tuple[list[dict[str, Any]], int, float, Optional[float], str]:
    from small_paper.config import load_pilot_config, resolve_output_dir
    from small_paper.pilot_runner import run_push_replay_dry_run
    from small_paper.exit_candidate_shadow import export_exit_candidate_trade_rows

    push_dir = Path(session_meta["push_dir"])
    if not push_dir.is_dir():
        return [], 0, 0.0, None, f"push_dir_missing:{push_dir}"

    day_key = str(session_meta.get("day_key") or push_dir.name.replace("-", ""))
    cfg = load_pilot_config(config_path)
    cfg = replace(cfg, discord_enabled=False, discord_observer_only=True)

    stamp = datetime.now(JST).strftime("%H%M%S")
    out_dir = resolve_output_dir(cfg, repo_root=REPO, day_key=day_key) / f"phase337_{stamp}"

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
            enable_exit_candidate_shadow=True,
            streaming_push_replay=streaming,
        )
    except Exception as exc:
        return [], 0, time.monotonic() - t0, None, str(exc)
    finally:
        _restore_discord_posts(orig_post)

    runtime = time.monotonic() - t0
    pack = result.exit_candidate_shadow
    if pack is None:
        return [], 0, runtime, None, "no_exit_candidate_shadow_pack"

    trades = export_exit_candidate_trade_rows(pack)
    push_rows = int(result.summary.get("push_rows") or result.summary.get("push_messages") or 0)
    vwap_cov: Optional[float] = None
    ticks = int(getattr(pack, "vwap_eval_ticks", 0) or 0)
    missing = int(getattr(pack, "vwap_missing_ticks", 0) or 0)
    if ticks > 0:
        vwap_cov = round(100.0 * (ticks - missing) / ticks, 2)
    return trades, push_rows, runtime, vwap_cov, ""


def main() -> int:
    try:
        import tracemalloc

        tracemalloc.start()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description="Phase337 EXIT candidate shadow evaluation")
    parser.add_argument("--push-root", type=Path, default=PUSH_ROOT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--max-sessions", type=int, default=1)
    parser.add_argument("--max-rows-per-session", type=int, default=30000)
    parser.add_argument("--date", dest="day_key", default=None, help="Filter YYYYMMDD day key")
    parser.add_argument("--session", default=None, help="Filter session_id substring")
    parser.add_argument("--streaming", action="store_true", default=True)
    parser.add_argument("--no-streaming", action="store_false", dest="streaming")
    parser.add_argument("--no-tick-csv", action="store_true", default=True, help="No tick CSV (default)")

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
    from research.phase337_exit_candidate_evaluation import Phase337Aggregator, write_phase337_outputs

    push_root = args.push_root if args.push_root.is_absolute() else REPO / args.push_root
    config_path = args.config if args.config.is_absolute() else REPO / args.config

    sessions = discover_push_jsonl_sessions(push_root)
    if args.day_key:
        sessions = [s for s in sessions if s.get("day_key") == args.day_key]
    if args.session:
        sessions = [s for s in sessions if args.session in str(s.get("session_id") or "")]
    if args.max_sessions is not None:
        sessions = sessions[: args.max_sessions]

    agg = Phase337Aggregator()
    par_cfg = parallel_config_from_args(args)
    print(
        f"phase337 sessions={len(sessions)} max_rows={args.max_rows_per_session} "
        f"streaming={args.streaming} parallel={par_cfg.parallel} "
        f"max_workers={par_cfg.effective_workers()}",
        flush=True,
    )
    run = run_parallel_session_evaluation(
        sessions=sessions,
        mode="phase337_exit_candidate",
        repo_root=REPO,
        config_path=config_path,
        max_push_rows=args.max_rows_per_session,
        streaming=args.streaming,
        parallel_config=par_cfg,
        progress=print,
    )
    ingest_session_results_to_aggregator(agg, run, ingest_method="add_session_result")
    gc.collect()

    paths = write_phase337_outputs(agg, REPORTS)
    summary = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))

    print("\n=== Phase337 Summary ===", flush=True)
    print(f"actual_total_pnl_yen_100: {summary.get('actual_total_pnl_yen_100')}", flush=True)
    print(f"best_candidate: {summary.get('best_candidate_by_delta_yen')}", flush=True)
    print(f"worst_candidate: {summary.get('worst_candidate_by_delta_yen')}", flush=True)
    print(f"extend_candidates: {summary.get('strength_hold_extend', {}).get('extend_candidate_trade_count')}", flush=True)
    print(f"peak_memory_mb: {summary.get('peak_memory_mb')}", flush=True)
    print(f"outputs: {paths}", flush=True)
    return 0 if not agg.failed_sessions else 1


if __name__ == "__main__":
    raise SystemExit(main())
