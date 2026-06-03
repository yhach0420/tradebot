#!/usr/bin/env python3
"""Phase276: Emit implementation report with offline Discord message samples."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def _bootstrap() -> Path:
    script = Path(__file__).resolve()
    repo_root = script.parents[2]
    native_root = script.parents[1]
    for p in (native_root / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo_root


def main() -> int:
    repo_root = _bootstrap()
    from small_paper.discord_message_builder import (
        aggregate_daily_metrics,
        build_daily_summary_detail,
        build_entry_deferred_detail,
        build_entry_detail,
        build_exit_detail,
        build_universe_refresh_detail,
        preview_payload,
    )

    entry_data = {
        "symbol": "3905.T",
        "entry_expectancy_score_v2": 5,
        "extended_entry_shadow_reasons": "vwap_dev;high_break_recent",
        "entry_vwap_dev_pct": 2.1,
        "entry_high_break_recent": True,
        "trading_value": 5e10,
        "continuation_quality_score": 0.71,
    }
    entry_detail = build_entry_detail(
        symbol="3905.T",
        entry_price=4520.0,
        stop_price=4465.76,
        slot_usage="2/3",
        entry_score_v2=5,
        data=entry_data,
    )
    sample_entry = preview_payload(
        event_tag="ENTRY",
        title_line="【ENTRY】 3905.T",
        detail=entry_detail,
        color=0x2F855A,
    )

    deferred_data = dict(entry_data)
    deferred_detail = build_entry_deferred_detail(
        symbol="4062.T",
        current_price=3180.0,
        entry_score_v2=5,
        slot_usage="3/3",
        data=deferred_data,
        open_positions=[
            {"symbol_short": "7220", "unrealized_pnl_pct": 1.2},
            {"symbol_short": "6976", "unrealized_pnl_pct": -0.3},
            {"symbol_short": "4062", "unrealized_pnl_pct": 0.7},
        ],
    )
    sample_deferred = preview_payload(
        event_tag="ENTRY見送り",
        title_line="【ENTRY見送り】 4062.T",
        detail=deferred_detail,
        color=0xDD6B20,
    )

    exit_detail = build_exit_detail(
        symbol="3905.T",
        exit_price=4580.0,
        pnl_pct=1.33,
        hold_minutes=18.0,
        exit_reason="trailing_mfe_exit",
    )
    sample_exit = preview_payload(
        event_tag="EXIT",
        title_line="【EXIT】 3905.T",
        detail=exit_detail,
        color=0xC05621,
    )

    refresh_detail = build_universe_refresh_detail(
        session_label="AM",
        refresh_time="10:00",
        added=["3719.T", "4263.T"],
        removed=["2667.T"],
        watch_count=40,
    )
    sample_refresh = preview_payload(
        event_tag="Universe Refresh",
        title_line="【Universe Refresh】 AM 10:00",
        detail=refresh_detail,
        color=0x3182CE,
    )

    mock_events = [
        {
            "event_type": "observer_exit",
            "symbol": "3905.T",
            "pnl_pct": 1.33,
            "exit_reason": "trailing_mfe_exit",
            "stop_hit": False,
        },
        {
            "event_type": "observer_exit",
            "symbol": "6526.T",
            "pnl_pct": -0.85,
            "exit_reason": "stop_hit",
            "stop_hit": True,
        },
        {
            "event_type": "observer_exit",
            "symbol": "5803.T",
            "pnl_pct": 0.42,
            "exit_reason": "momentum_fade_exit",
            "stop_hit": False,
        },
    ]
    summary = {
        "peak_open_slots": 3,
        "observer_entry_count": 4,
        "observer_exit_count": 3,
        "accepted_count": 4,
    }
    metrics = aggregate_daily_metrics(
        mock_events,
        summary,
        max_concurrent_positions=3,
        monitored_symbol_count=40,
    )
    daily_detail = build_daily_summary_detail(metrics)
    sample_daily = preview_payload(
        event_tag="Daily Summary",
        title_line="【Daily Summary】",
        detail=daily_detail,
        color=0x805AD5,
    )

    out = {
        "phase": 276,
        "title": "Discord notification UX implementation",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "design_reference": "kabu_native/results/reports/phase275_discord_notification_ux_design.json",
        "verdict": "implemented",
        "files_changed": [
            "kabu_native/src/small_paper/discord_message_builder.py",
            "kabu_native/src/small_paper/discord_notifier.py",
            "kabu_native/src/small_paper/pilot_runner.py",
            "kabu_native/src/small_paper/observer_position_tracker.py",
            "kabu_native/src/small_paper/config.py",
        ],
        "config_flags_added": [
            "discord_send_entry_deferred_max_concurrent",
            "discord_entry_deferred_cooldown_sec",
            "discord_entry_deferred_min_score_v2",
            "discord_send_universe_refresh",
            "discord_send_daily_summary",
        ],
        "constraints_respected": {
            "entry_logic_change": False,
            "exit_logic_change": False,
            "universe_logic_change": False,
            "max_concurrent_logic_change": False,
        },
        "discord_samples": {
            "ENTRY": sample_entry,
            "ENTRY見送り": sample_deferred,
            "EXIT": sample_exit,
            "Universe_Refresh": sample_refresh,
            "Daily_Summary": sample_daily,
        },
        "daily_summary_metrics": metrics,
    }

    reports = repo_root / "kabu_native" / "results" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    out_path = reports / "phase276_discord_notification_ux_implementation.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
