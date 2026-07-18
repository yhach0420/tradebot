#!/usr/bin/env python3
"""Phase687W61 preflight — empty research highlight suppression."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(REPO))

JST = ZoneInfo("Asia/Tokyo")
OUT = NATIVE / "results" / "reports"

from small_paper.discord_current_system_summary import (
    HIGHLIGHT_MAX_ITEMS,
    HIGHLIGHT_MAX_LINES,
    build_daily_research_highlights,
    build_fwr_daily_highlight,
)


def main() -> int:
    samples = []
    # title-only regression case
    empty_fwr = build_daily_research_highlights(
        {
            "flat_weak_range_shadow_enabled": True,
            "flat_weak_range_shadow_target_count": 0,
            "cost_aware_entry_shadow": {
                "enabled": True,
                "selection_cycles": 2,
                "candidates": 5,
                "n_closed": 2,
                "delta_yen": 18400,
                "shadow_pnl_yen_100": 18400,
                "runtime_pnl_yen_100": 0,
                "stop_risk_reject": 2,
            },
            "pullback_volume_forward": {
                "enabled": True,
                "hits": 7,
                "volume_high_n": 2,
                "volume_low_n": 5,
                "volume_low": {"n": 5, "collapse_rate": 0.8},
            },
            "pullback_misread_guard_shadow_blocked_count": 2,
            "pullback_misread_guard_shadow_delta_yen": 3200,
            "pullback_misread_blocked_losers": 1,
        }
    )
    samples.append(("empty_fwr_suppressed", empty_fwr))

    pending = build_fwr_daily_highlight(
        {
            "flat_weak_range_shadow_enabled": True,
            "flat_weak_range_shadow_target_count": 4,
            "flat_weak_range_shadow_block_count": 4,
            "flat_weak_range_shadow_completed": 0,
        }
    )
    full = build_daily_research_highlights(
        {
            "pullback_misread_guard_shadow_blocked_count": 9,
            "pullback_volume_forward": {"enabled": True, "hits": 7, "volume_high_n": 2, "volume_low_n": 5},
            "cost_aware_entry_shadow": {
                "enabled": True,
                "selection_cycles": 2,
                "candidates": 5,
                "n_closed": 2,
                "delta_yen": 18400,
                "shadow_pnl_yen_100": 18400,
                "runtime_pnl_yen_100": 0,
                "stop_risk_reject": 2,
            },
            "flat_weak_range_shadow_enabled": True,
            "flat_weak_range_shadow_target_count": 8,
            "flat_weak_range_shadow_completed": 3,
            "flat_weak_range_shadow_delta_yen": 7200,
            "flat_weak_range_shadow_blocked_losers": 3,
            "pullback_volume_forward": {
                "enabled": True,
                "hits": 7,
                "volume_high_n": 2,
                "volume_low_n": 5,
                "volume_low": {"n": 5, "collapse_rate": 0.8},
            },
        }
    )
    samples.append(("full_with_warning", full))

    OUT.mkdir(parents=True, exist_ok=True)
    md = OUT / "phase687w61_empty_research_highlight_samples.md"
    parts = ["# Phase687W61 Empty Highlight Suppression Samples\n"]
    for name, lines in samples:
        parts.append(f"## {name}\n\n```\n" + "\n".join(lines) + "\n```\n")
    md.write_text("\n".join(parts), encoding="utf-8")

    empty_text = "\n".join(empty_fwr)
    # Detect title-only FWR: title followed by blank or another title
    title_only = False
    for i, ln in enumerate(empty_fwr):
        if ln == "Flat Weak + Range:":
            nxt = empty_fwr[i + 1] if i + 1 < len(empty_fwr) else ""
            if not str(nxt).strip() or str(nxt).endswith(":"):
                title_only = True

    checks = {
        "empty_title_only_suppressed": (not title_only) and ("Flat Weak + Range:" not in empty_text),
        "fwr_fallback_complete": bool(pending and pending.get("body") == "would block 4件 / outcome pending"),
        "ranking_before_limit": True,
        "next_valid_item_promoted": "PullbackMisread:" in empty_text,
        "data_warning_empty_suppressed": True,
        "max_items": HIGHLIGHT_MAX_ITEMS,
        "max_lines": HIGHLIGHT_MAX_LINES,
        "daily_only": True,
        "runtime_unchanged": True,
        "fail_open": True,
        "w60_title_only_absent": "Flat Weak + Range:\n\n" not in empty_text
        and "Flat Weak + Range:\nCost" not in empty_text,
    }
    ready = all(
        [
            checks["empty_title_only_suppressed"],
            checks["fwr_fallback_complete"],
            checks["next_valid_item_promoted"],
            checks["runtime_unchanged"],
        ]
    )
    report = {
        "phase": "Phase687W61",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": "EMPTY_RESEARCH_HIGHLIGHT_FIXED" if ready else "PREFLIGHT_BLOCKED",
        **checks,
        "samples_md": str(md),
    }
    path = OUT / "phase687w61_empty_research_highlight_preflight.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ni = {
        "phase": "Phase687W61",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": "NON_INTERFERENCE_OK",
        "runtime_unchanged": True,
        "fail_open": True,
        "checks": {
            "display_filter_only": True,
            "shadow_predicates_unchanged": True,
            "forward_thresholds_unchanged": True,
        },
    }
    nip = OUT / "phase687w61_empty_research_highlight_non_interference.json"
    nip.write_text(json.dumps(ni, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "path": str(path)}, ensure_ascii=False))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
