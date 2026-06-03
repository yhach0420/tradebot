#!/usr/bin/env python3
"""Phase277: Discord UX completion report with full message samples."""

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
        "entry_expectancy_score_v2": 5,
        "extended_entry_shadow_reasons": "vwap_dev;high_break_recent",
        "entry_vwap_dev_pct": 2.1,
        "entry_high_break_recent": True,
        "trading_value": 5e10,
    }
    sample_entry = preview_payload(
        event_tag="ENTRY",
        title_line="【ENTRY】 3905.T",
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
    )

    holdings = [
        {"symbol_short": "7220", "unrealized_pnl_pct": 1.2, "entry_score_v2": 5, "hold_minutes": 12},
        {"symbol_short": "6976", "unrealized_pnl_pct": -0.3, "entry_score_v2": 5, "hold_minutes": 4},
        {"symbol_short": "4062", "unrealized_pnl_pct": 0.7, "entry_score_v2": 6, "hold_minutes": 8},
    ]
    sample_deferred = preview_payload(
        event_tag="ENTRY見送り",
        title_line="【ENTRY見送り】 4062.T",
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
    )

    sample_exit = preview_payload(
        event_tag="EXIT",
        title_line="【EXIT】 3905.T",
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
    )

    watch = [f"{i:04d}.T" for i in range(7200, 7240)]
    sample_refresh = preview_payload(
        event_tag="Universe Refresh",
        title_line="【Universe Refresh】 AM 10:00",
        detail=build_universe_refresh_detail(
            session_label="AM",
            refresh_time="10:00",
            added=["3719.T", "4263.T"],
            removed=["2667.T"],
            watch_symbols=watch,
        ),
        color=0x3182CE,
    )

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
    ux_stats = {
        "score5_candidate_count": 12,
        "score5_entry_count": 4,
        "score5_deferred_total_count": 2,
        "entry_deferred_notify_count": 2,
    }
    metrics = aggregate_daily_metrics(
        mock_events,
        {"peak_open_slots": 3, "observer_entry_count": 4, "observer_exit_count": 2},
        max_concurrent_positions=3,
        monitored_symbol_count=40,
        reject_rows=mock_rejects,
        ux_stats=ux_stats,
    )
    sample_daily = preview_payload(
        event_tag="Daily Summary",
        title_line="【Daily Summary】",
        detail=build_daily_summary_detail(metrics),
        color=0x805AD5,
    )

    out = {
        "phase": 277,
        "title": "Discord notification UX completion (Phase275 100%)",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "references": [
            "kabu_native/results/reports/phase275_discord_notification_ux_design.json",
            "kabu_native/results/reports/phase276_discord_notification_ux_implementation.json",
        ],
        "verdict": "phase275_requirements_met",
        "phase277_enhancements": [
            "Universe Refresh: full watch list 10 symbols per line",
            "ENTRY: score5 candidate ordinal",
            "ENTRY見送り: holdings with pnl, score, hold minutes",
            "EXIT: entry/exit price, MFE, MAE",
            "Daily Summary: score5 stats, deferred ranking, best/worst trade",
            "Spam: entry_deferred daily_max config",
        ],
        "config": {
            "discord_entry_deferred_daily_max": 50,
            "discord_entry_deferred_cooldown_sec": 1800,
            "discord_entry_deferred_min_score_v2": 5,
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
    out_path = reports / "phase277_discord_notification_ux_completion.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
