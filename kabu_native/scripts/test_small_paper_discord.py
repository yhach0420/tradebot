#!/usr/bin/env python3
"""Send a test [SMALL PAPER DRY RUN] Discord observer message (no orders)."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
_DEFAULT_WEBHOOK_ENV = "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL"


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    for p in (native_root / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return native_root, repo_root


def main() -> int:
    native_root, repo_root = _bootstrap()
    from api.rest_client import load_kabu_env
    from small_paper.config import load_pilot_config
    from small_paper.discord_notifier import SmallPaperDiscordNotifier, discord_config_from_pilot

    parser = argparse.ArgumentParser(
        description="Test small paper observer Discord (KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL)"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=native_root / "configs" / "small_paper_pilot.yaml",
    )
    parser.add_argument(
        "--webhook-env",
        default=None,
        help=f"Override webhook env var (default: config or {_DEFAULT_WEBHOOK_ENV})",
    )
    args = parser.parse_args()

    load_kabu_env(repo_root=repo_root)
    cfg_path = args.config if args.config.is_absolute() else (repo_root / args.config)
    cfg = load_pilot_config(cfg_path)
    env_name = (args.webhook_env or cfg.discord_webhook_env or _DEFAULT_WEBHOOK_ENV).strip()
    if env_name != _DEFAULT_WEBHOOK_ENV:
        print(
            f"warning: expected {_DEFAULT_WEBHOOK_ENV}, using {env_name}",
            file=sys.stderr,
        )
    webhook = (os.getenv(env_name) or "").strip()
    print(f"webhook_env: {env_name}")
    if not webhook:
        print(f"error: {env_name} is not set in environment", file=sys.stderr)
        return 2

    dcfg = discord_config_from_pilot(cfg)
    if args.webhook_env:
        from dataclasses import replace

        dcfg = replace(dcfg, webhook_env=env_name)
    notifier = SmallPaperDiscordNotifier(
        dcfg,
        profile=cfg.profile,
        entry_profile=cfg.entry_profile,
    )
    if not notifier.active:
        print(
            "Discord notifier inactive (check discord_enabled, observer_only, webhook env)",
            file=sys.stderr,
        )
        return 1
    ok = notifier.notify_heartbeat(
        summary={
            "runtime_sec": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "observer_entry_count": 0,
            "observer_holding_count": 0,
            "observer_exit_count": 0,
            "peak_open_slots": 0,
            "api_error_count": 0,
            "stale_tick_count": 0,
            "quality_distribution": {},
            "session_bucket_summary": {},
            "top_symbols": "TEST",
            "event_time": datetime.now(JST).isoformat(timespec="seconds"),
        }
    )
    print("sent" if ok else "failed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
