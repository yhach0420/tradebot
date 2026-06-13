#!/usr/bin/env python3
"""
Phase336: Full push-replay evaluation of Phase335 realtime board shadow vs actual Phase332.

Output: kabu_native/results/reports/phase336_realtime_board_full_replay_*.json/csv
"""

from __future__ import annotations

import argparse
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
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
REPORTS = REPO / "kabu_native" / "results" / "reports"
OUT_SUMMARY = REPORTS / "phase336_realtime_board_full_replay_summary.json"
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


def _run_session_replay(
    *,
    session_meta: dict[str, Any],
    config_path: Path,
    max_push_rows: Optional[int],
) -> tuple[list[dict[str, Any]], int, float, str]:
    from small_paper.config import load_pilot_config, resolve_output_dir
    from small_paper.pilot_runner import run_push_replay_dry_run
    from small_paper.realtime_board_exit_shadow import export_trade_rows

    push_dir = Path(session_meta["push_dir"])
    if not push_dir.is_dir():
        return [], 0, 0.0, f"push_dir_missing:{push_dir}"

    day_key = str(session_meta.get("day_key") or push_dir.name.replace("-", ""))
    cfg = load_pilot_config(config_path)
    cfg = replace(cfg, discord_enabled=True, discord_observer_only=True)

    stamp = datetime.now(JST).strftime("%H%M%S")
    out_dir = resolve_output_dir(cfg, repo_root=REPO, day_key=day_key) / f"phase336_{stamp}"

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
            enable_discord=True,
            write_board_shadow_reports=False,
        )
    except Exception as exc:
        return [], 0, time.monotonic() - t0, str(exc)
    finally:
        _restore_discord_posts(orig_post)

    runtime = time.monotonic() - t0
    logger = result.realtime_board_shadow
    if logger is None:
        return [], int(result.summary.get("push_rows") or 0), runtime, "no_board_shadow_logger"

    trades = export_trade_rows(logger)
    push_rows = int(result.summary.get("push_rows") or result.summary.get("push_messages") or 0)
    return trades, push_rows, runtime, ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase336 realtime board full push-replay evaluation")
    parser.add_argument("--push-root", type=Path, default=PUSH_ROOT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--max-push-rows", type=int, default=None)
    parser.add_argument("--max-sessions", type=int, default=None)
    parser.add_argument("--day-key", default=None, help="Filter to one YYYYMMDD day key")
    parser.add_argument("--include-small-paper-sessions", action="store_true")
    parser.add_argument(
        "--start-index",
        type=int,
        default=1,
        help="1-based session index to start from (resume)",
    )
    parser.add_argument(
        "--resume-from-checkpoint",
        action="store_true",
        help="Load trades/sessions from checkpoint and continue after last_completed_index",
    )
    args = parser.parse_args()

    _bootstrap()

    from research.phase336_realtime_board_full_replay import (
        Phase336Aggregator,
        discover_push_jsonl_sessions,
        discover_small_paper_push_replay_sessions,
        write_phase336_outputs,
    )

    push_root = args.push_root if args.push_root.is_absolute() else REPO / args.push_root
    config_path = args.config if args.config.is_absolute() else REPO / args.config

    sessions = discover_push_jsonl_sessions(push_root)
    if args.include_small_paper_sessions:
        sp = discover_small_paper_push_replay_sessions(SMALL_PAPER)
        seen_push = {s["push_dir"] for s in sessions}
        for s in sp:
            if s["push_dir"] not in seen_push:
                sessions.append(s)

    if args.day_key:
        sessions = [s for s in sessions if s.get("day_key") == args.day_key]

    if args.max_sessions is not None:
        sessions = sessions[: args.max_sessions]

    agg = Phase336Aggregator()
    ck_path = REPORTS / "phase336_realtime_board_full_replay.checkpoint.json"
    start_idx = max(1, int(args.start_index))

    if args.resume_from_checkpoint and ck_path.is_file():
        try:
            ck = json.loads(ck_path.read_text(encoding="utf-8"))
            last = int(ck.get("last_completed_index") or 0)
            start_idx = max(start_idx, last + 1)
            trades_path = REPORTS / "phase336_realtime_board_full_replay_trades.csv"
            if trades_path.is_file():
                import csv

                with trades_path.open(encoding="utf-8", newline="") as f:
                    for row in csv.DictReader(f):
                        sid = str(row.get("session_id") or "")
                        day_key = str(row.get("day_key") or "")
                        meta = next((s for s in sessions if s.get("session_id") == sid), None)
                        if meta is None:
                            meta = {"session_id": sid, "day_key": day_key, "source": "checkpoint"}
                        agg.trades.append(dict(row))
            sessions_path = REPORTS / "phase336_realtime_board_full_replay_sessions.csv"
            if sessions_path.is_file():
                import csv

                with sessions_path.open(encoding="utf-8", newline="") as f:
                    for row in csv.DictReader(f):
                        agg.sessions.append(dict(row))
            print(f"phase336 resumed from checkpoint last={last} start_index={start_idx}", flush=True)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"phase336 checkpoint resume failed: {exc}", flush=True)

    print(f"phase336 sessions_to_run={len(sessions)} start_index={start_idx}", flush=True)

    for i, meta in enumerate(sessions, start=1):
        if i < start_idx:
            continue
        sid = meta.get("session_id")
        print(f"[{i}/{len(sessions)}] replay {sid} ...", flush=True)
        trades, push_rows, runtime, err = _run_session_replay(
            session_meta=meta,
            config_path=config_path,
            max_push_rows=args.max_push_rows,
        )
        if err:
            print(f"  FAILED: {err}", flush=True)
            agg.add_session_result(session_meta=meta, trade_rows=[], push_rows=0, runtime_sec=runtime, error=err)
        else:
            print(f"  ok trades={len(trades)} push_rows={push_rows} runtime_sec={runtime:.1f}", flush=True)
            agg.add_session_result(
                session_meta=meta,
                trade_rows=trades,
                push_rows=push_rows,
                runtime_sec=runtime,
            )
        paths_partial = write_phase336_outputs(agg, REPORTS)
        ck_path.write_text(
            json.dumps(
                {
                    "last_completed_index": i,
                    "sessions_total": len(sessions),
                    "partial_summary": agg.build_summary(),
                    "output_paths": paths_partial,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        sys.stdout.flush()

    paths = write_phase336_outputs(agg, REPORTS)
    summary = agg.build_summary()
    summary["max_push_rows_per_session"] = args.max_push_rows
    summary["push_root"] = str(push_root)
    paths["summary"].write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"wrote {paths['summary']}", flush=True)
    return 0 if summary.get("sessions_failed", 0) == 0 or summary.get("sessions_evaluated", 0) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
