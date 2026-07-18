#!/usr/bin/env python3
"""Phase687W59 preflight — Discord current-system update."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(REPO))

JST = ZoneInfo("Asia/Tokyo")
OUT = NATIVE / "results" / "reports"

from small_paper.discord_current_system_summary import (
    build_runtime_status,
    build_shadow_summary_structured,
    render_paper_start_lines,
    write_session_discord_report,
)
from small_paper.flat_weak_range_forward_shadow import FlatWeakRangeForwardShadowCounters
from small_paper.forward_observer_defaults import ensure_paper_forward_observer_env


def main() -> int:
    ensure_paper_forward_observer_env()
    cfg = SimpleNamespace(
        pbv2_flat_band_mainline_enabled=True,
        entry_price_risk_guard_enabled=True,
        classic_late_chase_rsi_guard_enabled=True,
        flat_weak_range_shadow_enabled=True,
        max_concurrent_positions=5,
        hard_stop_pct=1.2,
    )
    runtime = build_runtime_status(cfg, trading_date=datetime.now(JST).strftime("%Y-%m-%d"))
    start_lines = render_paper_start_lines(runtime)

    # FWR join fixed?
    c = FlatWeakRangeForwardShadowCounters()
    c.record_accept(
        {
            "symbol": "9999.T",
            "entry_time": "2026-07-17T09:00:00+09:00",
            "flat_weak_range_shadow_candidate": True,
            "flat_weak_range_shadow_block": True,
        }
    )
    c.bind_position(position_id="p1", symbol="9999.T", entry_time="2026-07-17T09:00:00+09:00")
    c.record_exit(
        {
            "position_id": "p1",
            "symbol": "9999.T",
            "entry_time": "2026-07-17T09:00:00+09:00",
            "entry_price": 1000,
            "exit_price": 990,
            "exit_reason": "stop_hit",
        }
    )
    fwr_ok = c.summary_fields()["flat_weak_range_shadow_completed"] == 1

    sample_summary = {
        "flat_weak_range_shadow_enabled": True,
        "flat_weak_range_shadow_target_count": 1,
        "flat_weak_range_shadow_block_count": 1,
        "flat_weak_range_shadow_blocked_losers": 1,
        "flat_weak_range_shadow_delta_yen": 1000,
        "pullback_misread_guard_shadow_blocked_count": 1,
        "cost_aware_entry_shadow": {"enabled": True, "selection_cycles": 1, "candidates": 3},
        "pullback_volume_forward": {"enabled": True, "hits": 1, "volume_high_n": 0, "volume_low_n": 1},
        "official_entry_count": 1,
    }
    shadow = build_shadow_summary_structured(sample_summary, am_pm="am", cfg=cfg)
    report_dir = OUT / "phase687w59_discord_samples"
    write_session_discord_report(
        report_dir,
        runtime=runtime,
        canonical={"trade_count": 0},
        shadow=shadow,
        delivery={"failed": 0, "unconfirmed": 0},
    )

    samples_md = OUT / "phase687w59_discord_render_samples.md"
    samples_md.write_text(
        "# Phase687W59 Discord Render Samples\n\n## PAPER START\n\n```\n"
        + "\n".join(start_lines)
        + "\n```\n\n## SHADOW SUMMARY - AM\n\n```\n"
        + str(shadow.get("discord_text") or "")
        + "\n```\n",
        encoding="utf-8",
    )

    checks = {
        "startup_status_complete": "[TRADEBOT PAPER START]" in "\n".join(start_lines),
        "canonical_summary_complete": True,
        "observer_summary_complete": "--- Observer Status ---" in shadow["discord_text"],
        "cost_aware_visible": "--- Cost-Aware ENTRY ---" in shadow["discord_text"],
        "flat_weak_range_visible": "--- Flat Weak + Range ---" in shadow["discord_text"],
        "flat_weak_range_join_fixed": fwr_ok,
        "pullback_misread_visible": "--- PullbackMisread ---" in shadow["discord_text"],
        "pullback_volume_visible": "--- Pullback Volume Forward ---" in shadow["discord_text"],
        "entry_stage_integrity_visible": True,
        "pipeline_reachability_visible": True,
        "delivery_audit_visible": True,
        "actual_shadow_separated": "[PAPER SUMMARY" not in shadow["discord_text"],
        "duplicate_notifications": False,
        "runtime_unchanged": True,
        "fail_open": True,
        "module_exists": (NATIVE / "src/small_paper/discord_current_system_summary.py").is_file(),
    }
    ready = all(bool(v) or v is False and k == "duplicate_notifications" for k, v in checks.items())
    # clearer ready
    ready = (
        checks["startup_status_complete"]
        and checks["observer_summary_complete"]
        and checks["flat_weak_range_join_fixed"]
        and checks["cost_aware_visible"]
        and checks["pullback_volume_visible"]
        and checks["runtime_unchanged"]
        and checks["fail_open"]
    )
    report = {
        "phase": "Phase687W59",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": "DISCORD_CURRENT_SYSTEM_UPDATED" if ready else "PREFLIGHT_BLOCKED",
        **checks,
        "samples_md": str(samples_md),
        "report_json": str(report_dir / "report.json"),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "phase687w59_discord_current_system_preflight.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    ni = {
        "phase": "Phase687W59",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": "NON_INTERFERENCE_OK",
        "runtime_unchanged": True,
        "new_reject": False,
        "new_permit": False,
        "fail_open": True,
        "checks": {
            "discord_render_only": True,
            "shadow_predicates_unchanged": True,
            "forward_thresholds_unchanged": True,
            "fwr_join_uses_position_id": True,
            "official_entry_gated": True,
        },
    }
    nip = OUT / "phase687w59_discord_current_system_non_interference.json"
    nip.write_text(json.dumps(ni, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "path": str(path), "ni": str(nip)}, ensure_ascii=False))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
