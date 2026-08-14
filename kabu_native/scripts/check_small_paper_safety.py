#!/usr/bin/env python3
"""
Phase 44–45: Small paper pilot safety check (no live orders).

例::
    python kabu_native/scripts/check_small_paper_safety.py
    python kabu_native/scripts/check_small_paper_safety.py --live
    python kabu_native/scripts/check_small_paper_safety.py --full-session
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

    from small_paper.safety import load_config_and_check

    parser = argparse.ArgumentParser(description="Small paper pilot safety check")
    parser.add_argument(
        "--config",
        type=Path,
        default=native_root / "configs" / "small_paper_pilot.yaml",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Include live-session checks (KABU_API_PASSWORD, kabu connection)",
    )
    parser.add_argument(
        "--full-session",
        action="store_true",
        help="Full-day live dry-run checks (implies --live)",
    )
    parser.add_argument("--report-date", default=None)
    parser.add_argument(
        "--log-env",
        action="store_true",
        help="Log .env load status to stderr",
    )
    args = parser.parse_args()

    cfg_path = args.config if args.config.is_absolute() else (repo_root / args.config)
    if not cfg_path.is_file():
        print(f"config not found: {cfg_path}", file=sys.stderr)
        return 2

    live_mode = args.live or args.full_session
    day_key = args.report_date or datetime.now(JST).strftime("%Y%m%d")
    session_stamp = datetime.now(JST).strftime("%H%M%S")

    from small_paper.config import load_pilot_config
    from small_paper.pilot_env import load_pilot_environment, log_pilot_env_status

    config_pre = load_pilot_config(cfg_path)
    if live_mode or config_pre.discord_enabled:
        env_status = load_pilot_environment(
            repo_root=repo_root,
            discord_webhook_env=config_pre.discord_webhook_env,
        )
        if args.log_env or live_mode:
            log_pilot_env_status(env_status)

    config, checks = load_config_and_check(
        cfg_path,
        repo_root=repo_root,
        day_key=day_key,
        live_mode=live_mode,
        full_session=args.full_session,
        dry_run_flag=True,
        session_stamp=session_stamp if live_mode else None,
    )
    failed = [
        c for c in checks if not c.passed and c.check_id != "legacy_paper_trade_warning"
    ]
    warnings = [
        c
        for c in checks
        if c.check_id in ("legacy_paper_trade_warning", "stale_data_probe", "kabu_station_connection")
        and ("WARNING" in c.message or (c.details or {}).get("is_warning") or (c.details or {}).get("stale"))
    ]
    overall = len(failed) == 0

    report = {
        "phase": 45 if live_mode else 44,
        "component": "check_small_paper_safety",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "live_mode": live_mode,
        "full_session": args.full_session,
        "overall_pass": overall,
        "ready_for_pilot_dry_run": overall,
        "ready_for_live_session": overall if live_mode else None,
        "ready_for_full_session": overall if args.full_session else None,
        "config_path": str(cfg_path),
        "policy_label": config.policy_label,
        "policy_trial": config.policy_trial,
        "min_continuation_quality": config.min_continuation_quality,
        "checks": [
            {"check_id": c.check_id, "passed": c.passed, "message": c.message, "details": c.details}
            for c in checks
        ],
        "failed_check_ids": [c.check_id for c in failed],
        "warnings": [{"check_id": c.check_id, "message": c.message} for c in warnings],
        "human_review_required": [
            "Confirm order_enabled=false in kabu station and OS task scheduler",
            "Run check_small_paper_safety.py before each full-session dry-run",
            "Set KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL when discord_enabled=true (dedicated observer channel)",
            "Run run_small_paper_pilot.py --dry-run --source live --full-session",
            "Do not connect legacy Yahoo paper_trade or kabu_signal_shadow",
            "Review small_paper_summary.json after session for Phase40/41/43 re-evaluation",
        ],
    }
    try:
        from small_paper.ingress_run_identity import stamp_execution_scope

        stamp_execution_scope(report)
    except Exception:
        pass

    out = native_root / "results" / "reports" / f"small_paper_safety_{day_key}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nWrote {out}", file=sys.stderr)
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
