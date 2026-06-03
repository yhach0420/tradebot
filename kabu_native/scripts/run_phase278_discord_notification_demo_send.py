#!/usr/bin/env python3
"""
Phase278: Send Phase276/277 Discord UX demo messages to real webhook and report results.

Usage:
  python kabu_native/scripts/run_phase278_discord_notification_demo_send.py
  python kabu_native/scripts/run_phase278_discord_notification_demo_send.py --offline-only
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
_DEFAULT_WEBHOOK_ENV = "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL"
_DISCORD_FIELD_MAX = 1024
_DISCORD_EMBED_MAX_FIELDS = 25


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


def _validate_detail(name: str, detail: str) -> dict[str, Any]:
    lines = detail.split("\n")
    return {
        "event": name,
        "char_count": len(detail),
        "line_count": len(lines),
        "within_single_field": len(detail) <= _DISCORD_FIELD_MAX,
        "overflow_chars": max(0, len(detail) - _DISCORD_FIELD_MAX),
        "has_japanese": any(ord(c) > 127 for c in detail),
    }


def _build_demo_payloads() -> dict[str, Any]:
    from small_paper.discord_message_builder import (
        aggregate_daily_metrics,
        build_daily_summary_detail,
        build_entry_deferred_detail,
        build_entry_detail,
        build_exit_detail,
        build_universe_refresh_detail,
        preview_payload,
    )
    from small_paper.discord_ux_session import DiscordUxSessionStats

    entry_data = {
        "entry_expectancy_score_v2": 5,
        "extended_entry_shadow_reasons": "vwap_dev;high_break_recent",
        "entry_vwap_dev_pct": 2.1,
        "entry_high_break_recent": True,
        "trading_value": 5e10,
        "continuation_quality_score": 0.71,
    }
    holdings = [
        {"symbol_short": "7220", "unrealized_pnl_pct": 1.2, "entry_score_v2": 5, "hold_minutes": 12},
        {"symbol_short": "6976", "unrealized_pnl_pct": -0.3, "entry_score_v2": 5, "hold_minutes": 4},
        {"symbol_short": "4062", "unrealized_pnl_pct": 0.7, "entry_score_v2": 6, "hold_minutes": 8},
    ]
    watch = [f"{i:04d}.T" for i in range(7200, 7240)]

    previews = {
        "ENTRY": preview_payload(
            event_tag="ENTRY",
            title_line="【ENTRY】 3905.T [DEMO]",
            detail=build_entry_detail(
                symbol="3905.T",
                entry_price=4520.0,
                stop_price=4465.76,
                slot_usage="2/3",
                entry_score_v2=5,
                data=entry_data,
                score5_candidate_ordinal=3,
            ),
            color=0x2F855A,
        ),
        "ENTRY見送り": preview_payload(
            event_tag="ENTRY見送り",
            title_line="【ENTRY見送り】 4062.T [DEMO]",
            detail=build_entry_deferred_detail(
                symbol="4062.T",
                current_price=3180.0,
                entry_score_v2=5,
                slot_usage="3/3",
                data=entry_data,
                open_positions=holdings,
                score5_candidate_ordinal=7,
            ),
            color=0xDD6B20,
        ),
        "EXIT": preview_payload(
            event_tag="EXIT",
            title_line="【EXIT】 3905.T [DEMO]",
            detail=build_exit_detail(
                symbol="3905.T",
                entry_price=4520.0,
                exit_price=4580.0,
                pnl_pct=1.33,
                mfe_pct=1.85,
                mae_pct=-0.42,
                hold_minutes=18.0,
                exit_reason="trailing_mfe_exit",
            ),
            color=0xC05621,
        ),
        "Universe_Refresh": preview_payload(
            event_tag="Universe Refresh",
            title_line="【Universe Refresh】 AM 10:00 [DEMO]",
            detail=build_universe_refresh_detail(
                session_label="AM",
                refresh_time="10:00",
                added=["3719.T", "4263.T"],
                removed=["2667.T"],
                watch_symbols=watch,
            ),
            color=0x3182CE,
        ),
    }

    mock_events = [
        {
            "event_type": "observer_exit",
            "symbol": "3905.T",
            "pnl_pct": 1.33,
            "exit_reason": "trailing_mfe_exit",
        },
        {
            "event_type": "observer_exit",
            "symbol": "6526.T",
            "pnl_pct": -0.85,
            "exit_reason": "stop_hit",
            "stop_hit": True,
        },
    ]
    mock_rejects = [
        {
            "symbol": "4062.T",
            "gate_reject_reason": "max_concurrent",
            "entry_expectancy_score_v2": 6,
            "continuation_quality_score": 0.72,
        },
        {
            "symbol": "3719.T",
            "gate_reject_reason": "max_concurrent",
            "entry_expectancy_score_v2": 5,
            "continuation_quality_score": 0.68,
        },
    ]
    ux = DiscordUxSessionStats(
        score5_candidate_count=12,
        score5_entry_count=4,
        score5_deferred_total_count=2,
        entry_deferred_notify_count=2,
    )
    metrics = aggregate_daily_metrics(
        mock_events,
        {"peak_open_slots": 3, "observer_entry_count": 4, "observer_exit_count": 2},
        max_concurrent_positions=3,
        monitored_symbol_count=40,
        reject_rows=mock_rejects,
        ux_stats=ux.to_summary_dict(),
    )
    previews["Daily_Summary"] = preview_payload(
        event_tag="Daily Summary",
        title_line="【Daily Summary】 [DEMO]",
        detail=build_daily_summary_detail(metrics),
        color=0x805AD5,
    )
    return {"previews": previews, "metrics": metrics, "entry_data": entry_data, "holdings": holdings, "watch": watch}


def _send_all_demo(*, repo_root: Path, native_root: Path, config_path: Path) -> list[dict[str, Any]]:
    from small_paper.config import load_pilot_config
    from small_paper.discord_notifier import SmallPaperDiscordNotifier, discord_config_from_pilot
    from small_paper.discord_ux_session import DiscordUxSessionStats

    demo = _build_demo_payloads()
    cfg = load_pilot_config(config_path)
    cfg = replace(
        cfg,
        discord_enabled=True,
        discord_observer_only=True,
        discord_send_entry_deferred_max_concurrent=True,
        discord_send_universe_refresh=True,
        discord_send_daily_summary=True,
    )
    dcfg = discord_config_from_pilot(cfg)
    dcfg = replace(dcfg, enabled=True, cooldown_sec=0.0, entry_deferred_cooldown_sec=0.0)
    stamp = datetime.now(JST).strftime("%H%M%S")
    ux = DiscordUxSessionStats()

    def _notifier() -> SmallPaperDiscordNotifier:
        n = SmallPaperDiscordNotifier(
            dcfg,
            profile=cfg.profile,
            entry_profile=cfg.entry_profile,
            policy_label=str(cfg.policy_label),
        )
        n._last_sent_mono.clear()
        return n

    entry_data = demo["entry_data"]
    holdings = demo["holdings"]
    watch = demo["watch"]
    results: list[dict[str, Any]] = []

    # ENTRY
    n = _notifier()
    ok = n.notify_entry(
        event={
            "symbol": "3905.T",
            "event_time": datetime.now(JST).isoformat(timespec="seconds"),
            "message_index": f"demo_{stamp}_entry",
            "entry_expectancy_score_v2": 5,
            **entry_data,
        },
        payload={"CurrentPrice": 4520.0, "VWAP": 4420.0},
        open_slots=2,
        session_bucket="morning",
        score5_candidate_ordinal=3,
        ux_stats=ux,
    )
    results.append({"event": "ENTRY", "sent": ok, "http": "via SmallPaperDiscordNotifier.notify_entry"})

    time.sleep(1.2)

    # ENTRY見送り
    n = _notifier()
    ok = n.notify_entry_deferred_max_concurrent(
        event={
            "symbol": "4062.T",
            "event_time": datetime.now(JST).isoformat(timespec="seconds"),
            "entry_expectancy_score_v2": 5,
            "gate_reject_reason": "max_concurrent",
        },
        payload={"CurrentPrice": 3180.0},
        trade_data=entry_data,
        open_slots=3,
        open_positions=holdings,
        score5_candidate_ordinal=7,
        ux_stats=ux,
    )
    results.append(
        {"event": "ENTRY見送り", "sent": ok, "http": "via notify_entry_deferred_max_concurrent"}
    )

    time.sleep(1.2)

    # EXIT
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
    results.append({"event": "EXIT", "sent": ok, "http": "via notify_exit"})

    time.sleep(1.2)

    # Universe Refresh
    n = _notifier()
    ok = n.notify_universe_refresh(
        session_label="AM",
        refresh_time="10:00",
        added_symbols=["3719.T", "4263.T"],
        removed_symbols=["2667.T"],
        watch_symbols=watch,
        status="completed",
    )
    results.append({"event": "Universe_Refresh", "sent": ok, "http": "via notify_universe_refresh"})

    time.sleep(1.2)

    # Daily Summary
    n = _notifier()
    mock_events = [
        {
            "event_type": "observer_exit",
            "symbol": "3905.T",
            "pnl_pct": 1.33,
            "exit_reason": "trailing_mfe_exit",
        },
        {
            "event_type": "observer_exit",
            "symbol": "6526.T",
            "pnl_pct": -0.85,
            "exit_reason": "stop_hit",
            "stop_hit": True,
        },
    ]
    mock_rejects = [
        {
            "symbol": "4062.T",
            "gate_reject_reason": "max_concurrent",
            "entry_expectancy_score_v2": 6,
            "continuation_quality_score": 0.72,
        },
        {
            "symbol": "3719.T",
            "gate_reject_reason": "max_concurrent",
            "entry_expectancy_score_v2": 5,
            "continuation_quality_score": 0.68,
        },
    ]
    ok = n.notify_daily_summary(
        events=mock_events,
        summary={
            "peak_open_slots": 3,
            "observer_entry_count": 4,
            "observer_exit_count": 2,
            "accepted_count": 4,
        },
        monitored_symbol_count=40,
        reject_rows=mock_rejects,
        ux_stats=ux,
    )
    results.append({"event": "Daily_Summary", "sent": ok, "http": "via notify_daily_summary"})

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase278 Discord UX demo send + report")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="small paper pilot yaml",
    )
    parser.add_argument(
        "--offline-only",
        action="store_true",
        help="Skip webhook POST; only validate message bodies",
    )
    parser.add_argument(
        "--webhook-env",
        default=_DEFAULT_WEBHOOK_ENV,
    )
    args = parser.parse_args()

    native_root, repo_root = _bootstrap()
    _load_env(repo_root)
    config_path = args.config or (native_root / "configs" / "small_paper_pilot_q070_cap3.yaml")
    if not config_path.is_absolute():
        config_path = repo_root / config_path

    demo = _build_demo_payloads()
    previews = demo["previews"]

    validation = [_validate_detail(k, v["detail"]) for k, v in previews.items()]
    watch_preview = previews["Universe_Refresh"]["detail"]
    watch_lines = [ln for ln in watch_preview.split("\n") if ln and not ln.startswith("セッション")]

    env_name = (args.webhook_env or _DEFAULT_WEBHOOK_ENV).strip()
    webhook_set = bool((os.getenv(env_name) or "").strip())

    send_results: list[dict[str, Any]] = []
    send_mode = "skipped"
    if args.offline_only:
        send_mode = "offline_only"
    elif not webhook_set:
        send_mode = "webhook_missing"
    else:
        send_mode = "live_webhook"
        try:
            send_results = _send_all_demo(
                repo_root=repo_root,
                native_root=native_root,
                config_path=config_path,
            )
        except Exception as e:
            send_results = [{"event": "batch", "sent": False, "error": str(e)}]

    all_sent = all(r.get("sent") for r in send_results) if send_results else False
    refresh_line_count = sum(
        1 for ln in watch_preview.split("\n") if "," in ln and any(c.isdigit() for c in ln[:4])
    )

    checklist = {
        "japanese_readable": all(v["has_japanese"] for v in validation),
        "newlines_preserved": all(v["line_count"] >= 5 for v in validation),
        "refresh_40_symbols_10_per_line": refresh_line_count == 4,
        "entry_deferred_holdings_rich": "score5" in previews["ENTRY見送り"]["detail"]
        and "分" in previews["ENTRY見送り"]["detail"],
        "summary_score5_and_ranking": "score5" in previews["Daily_Summary"]["detail"]
        and "ランキング" in previews["Daily_Summary"]["detail"],
        "single_field_overflow_handled": all(
            v["within_single_field"] or v["event"] in ("Universe_Refresh", "Daily_Summary")
            for v in validation
        ),
    }

    report = {
        "phase": 278,
        "title": "Discord notification UX demo send verification",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "send_mode": send_mode,
        "webhook_env": env_name,
        "webhook_configured": webhook_set,
        "config_path": str(config_path),
        "verdict": (
            "live_send_ok"
            if send_mode == "live_webhook" and all_sent
            else (
                "offline_validation_ok"
                if send_mode in ("offline_only", "webhook_missing")
                and all(checklist.values())
                else "needs_attention"
            )
        ),
        "operator_checklist": checklist,
        "send_results": send_results,
        "message_validation": validation,
        "discord_samples": {k: v for k, v in previews.items()},
        "notes": [
            "Universe Refresh uses split embed fields when watch list exceeds 1024 chars.",
            "Demo messages tagged [DEMO] in title for channel filtering.",
            "ENTRY見送り spam rules unchanged; demo uses fresh notifier instance per message.",
        ],
    }
    if send_mode == "webhook_missing":
        report["notes"].append(
            f"Set {env_name} in .env to run live webhook demo sends."
        )

    out_path = repo_root / "kabu_native/results/reports/phase278_discord_notification_demo_send_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    print(f"send_mode={send_mode} verdict={report['verdict']}")
    for r in send_results:
        print(f"  {r.get('event')}: sent={r.get('sent')}")
    return 0 if report["verdict"] in ("live_send_ok", "offline_validation_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
