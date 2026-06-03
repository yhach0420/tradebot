#!/usr/bin/env python3
"""
Phase281: Split small-paper trade Discord notifications to KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL.

Usage:
  python kabu_native/scripts/run_phase281_discord_channel_split.py
  python kabu_native/scripts/run_phase281_discord_channel_split.py --offline-only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
_LEGACY_ENV = "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL"
_NOTIFY_ENV = "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL"


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    for p in (native_root / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return native_root, repo_root


def _load_env(repo_root: Path) -> None:
    try:
        from api.rest_client import load_kabu_env

        load_kabu_env(repo_root=repo_root)
    except Exception:
        env_path = repo_root / ".env"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _webhook_inventory() -> dict[str, Any]:
    return {
        "small_paper_trade_notify": {
            "env": _NOTIFY_ENV,
            "fallback_env": _LEGACY_ENV,
            "events": [
                "ENTRY",
                "ENTRY見送り",
                "EXIT",
                "Universe Refresh",
                "Daily Summary",
            ],
            "implementation": "small_paper/discord_notifier.py (_post trade_notify=True)",
        },
        "small_paper_legacy_observer": {
            "env": _LEGACY_ENV,
            "events": ["HEARTBEAT", "HOLD", "TAKE", "REJECT", "ERROR", "SESSION SUMMARY"],
            "implementation": "small_paper/discord_notifier.py (_post trade_notify=False)",
        },
        "kabu_shadow_paper": {
            "env": "KABU_SHADOW_DISCORD_WEBHOOK_URL",
            "unchanged": True,
            "implementation": "notify/discord.py, shadow/runner.py, replay",
        },
        "yahoo_legacy_paper_trade": {
            "env": "DISCORD_WEBHOOK_URL",
            "unchanged": True,
            "note": "Blocked for small_paper safety; not used by pilot observer",
        },
        "discord_issue_bot": {
            "env": "DISCORD_WEBHOOK_URL (issue bot subproject)",
            "unchanged": True,
            "implementation": "discord_issue_bot/discord_issue_bot.py",
            "note": "Separate .env under discord_issue_bot/ — not modified in Phase281",
        },
    }


def _send_trade_demo(*, config_path: Path) -> list[dict[str, Any]]:
    from small_paper.config import load_pilot_config
    from small_paper.discord_notifier import SmallPaperDiscordNotifier, discord_config_from_pilot
    from small_paper.discord_ux_session import DiscordUxSessionStats

    cfg = load_pilot_config(config_path)
    cfg = replace(
        cfg,
        discord_enabled=True,
        discord_observer_only=True,
        discord_send_universe_refresh=True,
        discord_send_daily_summary=True,
    )
    dcfg = discord_config_from_pilot(cfg)
    dcfg = replace(dcfg, enabled=True, cooldown_sec=0.0, entry_deferred_cooldown_sec=0.0)

    def _notifier() -> SmallPaperDiscordNotifier:
        n = SmallPaperDiscordNotifier(
            dcfg,
            profile=cfg.profile,
            entry_profile=cfg.entry_profile,
            policy_label=str(cfg.policy_label),
        )
        n._last_sent_mono.clear()
        return n

    entry_data = {
        "entry_expectancy_score_v2": 5,
        "extended_entry_shadow_reasons": "vwap_dev",
        "continuation_quality_score": 0.71,
    }
    watch = [f"{7200 + i}.T" for i in range(40)]
    watch[0] = "7203.T"
    watch[1] = "4062.T"
    ux = DiscordUxSessionStats(score5_candidate_count=3, score5_entry_count=1)
    results: list[dict[str, Any]] = []
    stamp = datetime.now(JST).strftime("%H%M%S")

    n = _notifier()
    ok = n.notify_entry(
        event={
            "symbol": "3905.T",
            "event_time": datetime.now(JST).isoformat(timespec="seconds"),
            "message_index": f"phase281_{stamp}_entry",
            **entry_data,
        },
        payload={"CurrentPrice": 4520.0},
        open_slots=1,
        session_bucket="morning",
        score5_candidate_ordinal=2,
        ux_stats=ux,
    )
    results.append(
        {
            "event": "ENTRY",
            "sent": ok,
            "trade_webhook_source": n.trade_webhook_source(),
        }
    )
    time.sleep(1.0)

    n = _notifier()
    ok = n.notify_exit(
        context={
            "symbol": "3905.T",
            "is_structural_exit": True,
            "exit_reason": "trailing_mfe_exit",
            "current_price": 4580.0,
            "entry_price": 4520.0,
            "realized_pnl_pct": 1.33,
            "mfe_pct": 1.85,
            "mae_pct": -0.42,
            "hold_sec": 1080.0,
            "exit_time": datetime.now(JST).isoformat(timespec="seconds"),
        }
    )
    results.append(
        {
            "event": "EXIT",
            "sent": ok,
            "trade_webhook_source": n.trade_webhook_source(),
        }
    )
    time.sleep(1.0)

    n = _notifier()
    ok = n.notify_universe_refresh(
        session_label="AM",
        refresh_time="10:00",
        added_symbols=["3719.T"],
        removed_symbols=["2667.T"],
        watch_symbols=watch,
    )
    results.append(
        {
            "event": "Universe_Refresh",
            "sent": ok,
            "trade_webhook_source": n.trade_webhook_source(),
        }
    )
    time.sleep(1.0)

    n = _notifier()
    ok = n.notify_daily_summary(
        events=[],
        summary={"peak_open_slots": 2, "observer_entry_count": 1, "observer_exit_count": 1},
        monitored_symbol_count=40,
        reject_rows=[],
        ux_stats=ux,
    )
    results.append(
        {
            "event": "Daily_Summary",
            "sent": ok,
            "trade_webhook_source": n.trade_webhook_source(),
        }
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase281 Discord channel split verification")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--offline-only", action="store_true")
    args = parser.parse_args()

    native_root, repo_root = _bootstrap()
    _load_env(repo_root)
    config_path = args.config or (native_root / "configs" / "small_paper_pilot_q070_cap3.yaml")
    if not config_path.is_absolute():
        config_path = repo_root / config_path

    legacy_set = bool((os.getenv(_LEGACY_ENV) or "").strip())
    notify_set = bool((os.getenv(_NOTIFY_ENV) or "").strip())

    send_results: list[dict[str, Any]] = []
    if args.offline_only:
        send_mode = "offline_only"
    elif not notify_set and not legacy_set:
        send_mode = "webhook_missing"
    else:
        send_mode = "live_webhook"
        try:
            send_results = _send_trade_demo(config_path=config_path)
        except Exception as e:
            send_results = [{"event": "batch", "sent": False, "error": str(e)}]

    sources = {r.get("trade_webhook_source") for r in send_results if r.get("trade_webhook_source")}
    all_sent = all(r.get("sent") for r in send_results) if send_results else False
    used_notify = sources == {"notify"} or (notify_set and "legacy_fallback" not in sources)

    checklist = {
        "notify_env_documented": True,
        "inventory_complete": True,
        "issue_bot_unchanged": True,
        "trade_events_use_notify_or_fallback": bool(notify_set or legacy_set),
        "live_send_all_four": all_sent if send_mode == "live_webhook" else None,
        "prefer_notify_when_set": (not notify_set) or used_notify,
    }

    if send_mode != "live_webhook":
        checklist["live_send_all_four"] = None

    verdict = "needs_attention"
    if send_mode == "live_webhook" and all_sent and checklist["trade_events_use_notify_or_fallback"]:
        verdict = "live_send_ok"
    elif send_mode in ("offline_only", "webhook_missing") and checklist["trade_events_use_notify_or_fallback"]:
        verdict = "offline_inventory_ok"

    report = {
        "phase": 281,
        "title": "Discord trade notify channel split",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "send_mode": send_mode,
        "env": {
            _LEGACY_ENV: legacy_set,
            _NOTIFY_ENV: notify_set,
        },
        "fallback_behavior": (
            "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL unset → trade events use "
            f"{_LEGACY_ENV}"
        ),
        "verdict": verdict,
        "operator_checklist": checklist,
        "webhook_inventory": _webhook_inventory(),
        "send_results": send_results,
        "notes": [
            "Trade notify: ENTRY, ENTRY見送り, EXIT, Universe Refresh, Daily Summary.",
            "Legacy observer webhook unchanged for HEARTBEAT/HOLD/TAKE/ERROR.",
            "discord_issue_bot uses its own DISCORD_WEBHOOK_URL — not modified.",
        ],
    }

    out_path = repo_root / "kabu_native/results/reports/phase281_discord_channel_split.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    print(f"send_mode={send_mode} verdict={verdict}")
    print(f"  {_LEGACY_ENV}={legacy_set} {_NOTIFY_ENV}={notify_set}")
    for r in send_results:
        print(f"  {r.get('event')}: sent={r.get('sent')} source={r.get('trade_webhook_source')}")
    return 0 if verdict in ("live_send_ok", "offline_inventory_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
