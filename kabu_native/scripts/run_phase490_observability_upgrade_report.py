#!/usr/bin/env python3
"""Phase490 observability upgrade — offline Discord mock report (no webhook)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

KABU = Path(__file__).resolve().parents[1]
REPO = KABU.parent


def _bootstrap() -> None:
    for p in (KABU / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    _bootstrap()
    from small_paper.canonical_summary import build_canonical_summary, collect_canonical_trades
    from small_paper.discord_message_builder import (
        build_daily_summary_detail,
        build_entry_detail,
        build_exit_detail,
        build_observability_embed_fields,
        format_heartbeat_runtime_health_fields,
        preview_payload,
    )

    events = [
        {
            "event_type": "observer_exit",
            "symbol": "6976.T",
            "entry_price": 20000,
            "exit_price": 20100,
            "pnl_pct": 0.5,
            "exit_reason": "trailing_mfe_exit",
            "mfe_pct": 1.0,
        },
        {
            "event_type": "observer_exit",
            "symbol": "6976.T",
            "entry_price": 20000,
            "exit_price": 19900,
            "pnl_pct": -0.5,
            "exit_reason": "stop_hit",
            "mfe_pct": 0.3,
        },
        {
            "event_type": "observer_exit",
            "symbol": "4062.T",
            "entry_price": 1000,
            "exit_price": 990,
            "pnl_pct": -1.0,
            "exit_reason": "stop_hit",
            "mfe_pct": 0.2,
        },
    ]
    canonical = build_canonical_summary(
        collect_canonical_trades(events),
        peak_open_slots=5,
        max_concurrent_positions=5,
        watch_symbols_count=120,
    )
    summary = {
        "canonical_summary": canonical,
        "api_error_count": 1,
        "stale_tick_count": 3089,
        "data_gap_count": 38,
        "live_feature_complete_rate_pct": 94.82,
        "config_sha256": "15113c9dabc3c45",
        "peak_open_slots": 5,
        "max_concurrent_positions": 5,
        "reject_reason_counts": {
            "high_drift_pullback": 4385,
            "data_stale_price": 31901,
            "late_chase_guard": 12,
            "max_concurrent": 1658,
        },
    }
    name_map = {"6976.T": "太陽誘電", "4062.T": "イビデン"}

    before_daily = preview_payload(
        event_tag="Daily Summary",
        title_line="【Daily Summary】 BEFORE",
        detail=build_daily_summary_detail(canonical),
        color=0x805AD5,
    )
    after_fields = build_observability_embed_fields(events=events, summary=summary, name_map=name_map)
    after_daily = preview_payload(
        event_tag="Daily Summary",
        title_line="【Daily Summary】 AFTER (Phase490)",
        detail=build_daily_summary_detail(canonical),
        color=0x805AD5,
        extra_fields=[{"name": f["name"], "value": f["value"]} for f in after_fields],
    )

    entry_after = preview_payload(
        event_tag="ENTRY",
        title_line="【ENTRY】 6976.T 太陽誘電",
        detail=build_entry_detail(
            symbol="6976.T",
            entry_price=19955.0,
            stop_price=19700.0,
            slot_usage="3/5",
            entry_score_v2=4,
            data={"momentum_continuation_score": 0.21},
            name_map=name_map,
            entry_time="2026-06-19T09:12:34+09:00",
        ),
        color=0x2F855A,
    )
    exit_after = preview_payload(
        event_tag="EXIT",
        title_line="【EXIT】 4062.T イビデン",
        detail=build_exit_detail(
            symbol="4062.T",
            entry_price=1000.0,
            exit_price=990.0,
            pnl_pct=-1.0,
            mfe_pct=0.2,
            mae_pct=-1.0,
            hold_minutes=8.0,
            exit_reason="stop_hit",
            pnl_yen_100=-1000.0,
            name_map=name_map,
            exit_time="2026-06-19T10:05:00+09:00",
        ),
        color=0xC53030,
    )

    hb_base = [
        {"name": "runtime_sec", "value": "14400"},
        {"name": "api_errors", "value": "1"},
        {"name": "stale_ticks", "value": "3089"},
    ]
    hb_after = hb_base + format_heartbeat_runtime_health_fields(summary)

    out_dir = KABU / "results" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "phase": 490,
        "before_daily_summary": before_daily,
        "after_daily_summary": after_daily,
        "after_entry": entry_after,
        "after_exit": exit_after,
        "before_heartbeat_fields": hb_base,
        "after_heartbeat_fields": hb_after,
    }
    out_path = out_dir / "phase490_discord_mockups.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    doc = KABU / "docs" / "operations" / "phase490_observability_upgrade.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(
        "\n".join(
            [
                "# Phase490 — Observability Upgrade",
                "",
                "Implemented: C01 Symbol Attribution, C02 Exit Breakdown, C03 Runtime Health,",
                "C05 stop_low_mfe counter/tag, C06 Reject Funnel.",
                "",
                "No Entry/Exit/Gate/Runtime logic changes — Discord formatting only.",
                "",
                "## Before / After",
                "",
                f"Mock JSON: `{out_path.relative_to(KABU).as_posix()}`",
                "",
                "### Daily Summary (before)",
                "",
                "```",
                before_daily["detail"],
                "```",
                "",
                "### Daily Summary (after)",
                "",
                "```",
                after_daily["detail"],
                "```",
                "",
                *[f"**{f['name']}**\n```\n{f['value']}\n```" for f in after_fields],
                "",
                "### HEARTBEAT (before → after)",
                "",
                "Before: runtime_sec, api_errors, stale_ticks only.",
                "",
                "After adds:",
                "",
                "```",
                "\n".join(f"{f['name']}: {f['value']}" for f in format_heartbeat_runtime_health_fields(summary)),
                "```",
                "",
                "### ENTRY (unchanged — reference mock)",
                "",
                "```",
                entry_after["detail"],
                "```",
                "",
                "### EXIT (after — stop_low_mfe tag)",
                "",
                "```",
                exit_after["detail"],
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"wrote {out_path}", flush=True)
    print(f"wrote {doc}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
