#!/usr/bin/env python3
"""
Phase 44–48: Small paper pilot dry-run (no order placement).

例::
    python kabu_native/scripts/check_small_paper_safety.py
    python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source replay
    python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source live --full-session --poll-interval-sec 5
    python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source push-replay --push-dir kabu_native/data/push_jsonl/2026-05-18 --poll-interval-sec 0
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    for p in (native_root / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo_root, native_root


def main() -> int:
    repo_root, native_root = _bootstrap()

    from small_paper.config import (
        load_pilot_config,
        resolve_live_full_session_dir,
        resolve_live_session_dir,
        resolve_output_dir,
        resolve_push_replay_dir,
    )
    from small_paper.pilot_runner import (
        run_live_dry_run,
        run_poll_dry_run,
        run_push_replay_dry_run,
        run_replay_dry_run,
    )
    from small_paper.safety import load_config_and_check
    from storage.symbol_sources import load_symbols

    parser = argparse.ArgumentParser(description="Small paper pilot dry-run")
    parser.add_argument(
        "--config",
        type=Path,
        default=native_root / "configs" / "small_paper_pilot.yaml",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        required=True,
        help="Required: dry-run only, no orders",
    )
    parser.add_argument(
        "--source",
        choices=("replay", "poll", "live", "push-replay"),
        default=None,
    )
    parser.add_argument("--trades-csv", type=Path, default=None)
    parser.add_argument("--universe", type=Path, default=None, help="Universe CSV (passed rows)")
    parser.add_argument(
        "--universe-csv",
        type=Path,
        default=None,
        help="Shadow dynamic universe CSV (overrides --universe when set)",
    )
    parser.add_argument("--max-polls", type=int, default=None)
    parser.add_argument("--duration-sec", type=float, default=None)
    parser.add_argument("--poll-interval-sec", type=float, default=None)
    parser.add_argument("--full-session", action="store_true", help="Run until session-end (09:00-15:30 JST)")
    parser.add_argument("--session-start", default=None, help="Session start HH:MM JST (default 09:00)")
    parser.add_argument("--session-end", default=None, help="Session end HH:MM JST (default 15:30)")
    parser.add_argument(
        "--auto-stop",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Stop at session-end when --full-session (default: true)",
    )
    parser.add_argument("--heartbeat-sec", type=float, default=None)
    parser.add_argument(
        "--wait-until-session",
        action="store_true",
        help="If before session start, wait until 09:00 instead of exiting",
    )
    parser.add_argument(
        "--am-pm-session",
        choices=("am", "pm"),
        default=None,
        help="Phase116: AM/PM shadow session times + entry stop + session-close exit (runtime overlay)",
    )
    parser.add_argument("--skip-safety", action="store_true")
    parser.add_argument(
        "--log-env",
        action="store_true",
        help="Log .env load status (cwd, dotenv path, KABU_API_PASSWORD, Discord webhook)",
    )
    parser.add_argument("--output-date", default=None)
    parser.add_argument(
        "--push-dir",
        type=Path,
        default=None,
        help="push_jsonl day directory (required for --source push-replay)",
    )
    parser.add_argument(
        "--replay-speed",
        type=float,
        default=None,
        help="Sleep seconds between evaluated rows (0=as fast as possible)",
    )
    parser.add_argument(
        "--max-push-rows",
        type=int,
        default=None,
        help="Cap push_jsonl rows loaded (testing)",
    )
    parser.add_argument(
        "--enable-discord",
        action="store_true",
        help="Force Discord on for push-replay",
    )
    parser.add_argument(
        "--no-discord",
        action="store_true",
        help="Disable Discord for push-replay even if config has discord_enabled",
    )
    parser.add_argument(
        "--enable-intraday-refresh",
        action="store_true",
        help="Phase157: reload universe/register at 10:00 (AM) or 14:30 (PM)",
    )
    parser.add_argument(
        "--intraday-refresh-csv",
        type=Path,
        default=None,
        help="Refresh universe CSV path (required when --enable-intraday-refresh)",
    )
    args = parser.parse_args()

    if not args.dry_run:
        print("--dry-run is required (no live order path)", file=sys.stderr)
        return 2

    cfg_path = args.config if args.config.is_absolute() else (repo_root / args.config)
    config = load_pilot_config(cfg_path)
    day_key = args.output_date or datetime.now(JST).strftime("%Y%m%d")
    session_stamp = datetime.now(JST).strftime("%H%M%S")
    source = args.source or config.default_source

    if not args.skip_safety:
        from small_paper.pilot_env import load_pilot_environment, log_pilot_env_status

        env_status = load_pilot_environment(
            repo_root=repo_root,
            discord_webhook_env=config.discord_webhook_env,
        )
        if args.log_env or source == "live":
            log_pilot_env_status(env_status)

    if source == "live":
        if args.full_session:
            out_dir = resolve_live_full_session_dir(
                config, repo_root=repo_root, day_key=day_key, session_stamp=session_stamp
            )
        else:
            out_dir = resolve_live_session_dir(
                config, repo_root=repo_root, day_key=day_key, session_stamp=session_stamp
            )
    elif source == "push-replay":
        out_dir = resolve_push_replay_dir(
            config, repo_root=repo_root, day_key=day_key, session_stamp=session_stamp
        )
    else:
        out_dir = resolve_output_dir(config, repo_root=repo_root, day_key=day_key)

    safety_report = None
    if not args.skip_safety:
        _, checks = load_config_and_check(
            cfg_path,
            repo_root=repo_root,
            day_key=day_key,
            live_mode=(source == "live"),
            full_session=bool(args.full_session and source == "live"),
            dry_run_flag=True,
            session_stamp=session_stamp if source == "live" else None,
        )
        failed = [
            c
            for c in checks
            if not c.passed and c.check_id != "legacy_paper_trade_warning"
        ]
        safety_report = {
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "overall_pass": len(failed) == 0,
            "full_session": bool(args.full_session),
            "checks": [
                {"check_id": c.check_id, "passed": c.passed, "message": c.message, "details": c.details}
                for c in checks
            ],
            "failed_check_ids": [c.check_id for c in failed],
            "warnings": [
                c.check_id
                for c in checks
                if c.check_id == "legacy_paper_trade_warning"
                and (c.details or {}).get("is_warning")
            ],
        }
        if failed and source == "live":
            print("Safety check failed:", [c.check_id for c in failed], file=sys.stderr)
            return 1

    if source == "replay":
        trades_csv = args.trades_csv
        if trades_csv is None and config.reference_trades_csv:
            trades_csv = Path(config.reference_trades_csv)
        if trades_csv and not trades_csv.is_absolute():
            trades_csv = repo_root / trades_csv
        if not trades_csv or not trades_csv.is_file():
            print("trades csv required for replay source", file=sys.stderr)
            return 2
        result = run_replay_dry_run(config, trades_csv=trades_csv, output_dir=out_dir)
    elif source == "push-replay":
        push_dir = args.push_dir
        if push_dir is None:
            print("--push-dir required for push-replay source", file=sys.stderr)
            return 2
        if not push_dir.is_absolute():
            push_dir = repo_root / push_dir
        interval = (
            args.poll_interval_sec
            if args.poll_interval_sec is not None
            else 0.0
        )
        replay_speed = args.replay_speed if args.replay_speed is not None else 0.0
        use_discord = not args.no_discord and (args.enable_discord or config.discord_enabled)
        result = run_push_replay_dry_run(
            config,
            push_dir=push_dir,
            output_dir=out_dir,
            repo_root=repo_root,
            poll_interval_sec=interval,
            replay_speed_sec=replay_speed,
            max_push_rows=args.max_push_rows,
            enable_discord=use_discord,
        )
    elif source == "live":
        universe = (
            args.universe_csv
            or args.universe
            or (native_root / "data" / "universe" / "universe_intraday_full.csv")
        )
        if not universe.is_absolute():
            universe = repo_root / universe
        if not universe.is_file():
            print(f"universe csv not found: {universe}", file=sys.stderr)
            return 2
        syms = load_symbols(universe=universe, native_root=native_root)
        tuples = [(s.symbol, s.symbol_key, s.exchange) for s in syms]

        am_pm_policy = None
        if args.am_pm_session:
            from small_paper.am_pm_session_policy import apply_am_pm_policy

            config, am_pm_policy = apply_am_pm_policy(config, args.am_pm_session)

        session_start = args.session_start or (
            am_pm_policy.session_start if am_pm_policy else config.live_session_start
        )
        session_end = args.session_end or (
            am_pm_policy.session_end if am_pm_policy else config.live_session_end
        )
        use_full_session = args.full_session or bool(am_pm_policy)

        if use_full_session:
            duration = 0.0
        else:
            duration = (
                args.duration_sec if args.duration_sec is not None else config.live_duration_sec
            )
        interval = (
            args.poll_interval_sec
            if args.poll_interval_sec is not None
            else config.live_poll_interval_sec
        )
        result = run_live_dry_run(
            config,
            symbols=tuples,
            output_dir=out_dir,
            repo_root=repo_root,
            native_root=native_root,
            config_path=cfg_path.resolve(),
            duration_sec=duration,
            poll_interval_sec=interval,
            max_polls=args.max_polls,
            safety_report=safety_report,
            full_session=use_full_session,
            session_start=session_start,
            session_end=session_end,
            auto_stop=args.auto_stop,
            heartbeat_sec=args.heartbeat_sec or config.live_heartbeat_sec,
            wait_until_session=args.wait_until_session,
            stale_tick_sec=config.live_stale_tick_sec,
            max_consecutive_api_errors=config.live_max_consecutive_api_errors,
            universe_csv_path=str(universe.resolve()),
            am_pm_policy=am_pm_policy,
            enable_intraday_refresh=bool(args.enable_intraday_refresh),
            intraday_refresh_csv_path=(
                str(args.intraday_refresh_csv.resolve())
                if args.intraday_refresh_csv
                else None
            ),
        )
    else:
        universe = (
            args.universe_csv
            or args.universe
            or (native_root / "data" / "universe" / "universe_intraday_full.csv")
        )
        if not universe.is_absolute():
            universe = repo_root / universe
        if not universe.is_file():
            print(f"universe csv not found: {universe}", file=sys.stderr)
            return 2
        syms = load_symbols(universe=universe, native_root=native_root)
        tuples = [(s.symbol, s.symbol_key, s.exchange) for s in syms]
        result = run_poll_dry_run(
            config,
            symbols=tuples,
            output_dir=out_dir,
            repo_root=repo_root,
            max_polls=args.max_polls or config.max_polls,
        )

    print(json.dumps(result.summary, ensure_ascii=False, indent=2))
    print(f"Output: {result.output_dir}")
    print(
        f"  accepted={result.summary.get('accepted_count')} "
        f"rejected={result.summary.get('rejected_count')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
