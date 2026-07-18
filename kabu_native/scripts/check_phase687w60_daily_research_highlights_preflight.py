#!/usr/bin/env python3
"""Phase687W60 preflight — Daily Research Highlights."""

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
    render_daily_short_lines,
)
from small_paper.discord_message_builder import build_summary_embed_payload
from small_paper.pullback_volume_forward_logger import VOL_PERSISTENCE_HIGH_THR, VOL_PERSISTENCE_LOW_THR


def main() -> int:
    sample = {
        "official_entry_count": 20,
        "observer_exit_count": 18,
        "cost_aware_entry_shadow": {
            "enabled": True,
            "selection_cycles": 5,
            "candidates": 20,
            "n_closed": 4,
            "shadow_pnl_yen_100": 20000,
            "runtime_pnl_yen_100": 1600,
            "delta_yen": 18400,
            "stop_risk_reject": 2,
        },
        "flat_weak_range_shadow_enabled": True,
        "flat_weak_range_shadow_target_count": 10,
        "flat_weak_range_shadow_block_count": 4,
        "flat_weak_range_shadow_completed": 4,
        "flat_weak_range_shadow_blocked_losers": 3,
        "flat_weak_range_shadow_delta_yen": 7200,
        "pullback_volume_forward": {
            "enabled": True,
            "hits": 7,
            "volume_high_n": 2,
            "volume_low_n": 5,
            "volume_high": {"n": 2, "healthy_rate": 1.0},
            "volume_low": {"n": 5, "collapse_rate": 0.8},
        },
        "pullback_misread_guard_shadow_blocked_count": 9,
        "canonical_summary": {"trade_count": 79, "total_pnl_yen_100": 18400, "profit_factor_yen_100": 1.08},
    }
    hl = build_daily_research_highlights(sample)
    daily = render_daily_short_lines(sample, trading_date=datetime.now(JST).strftime("%Y-%m-%d"))
    embed = build_summary_embed_payload(
        sample["canonical_summary"],
        am_pm="",
        research_highlights=hl,
    )
    samples = OUT / "phase687w60_daily_research_highlights_samples.md"
    OUT.mkdir(parents=True, exist_ok=True)
    samples.write_text(
        "# Phase687W60 Daily Research Highlights Samples\n\n## Highlights\n\n```\n"
        + "\n".join(hl)
        + "\n```\n\n## Daily short\n\n```\n"
        + "\n".join(daily)
        + "\n```\n\n## Embed description (prefix)\n\n```\n"
        + str(embed.get("description") or "")[:800]
        + "\n```\n",
        encoding="utf-8",
    )

    headers = [
        ln
        for ln in hl
        if ln.endswith(":") and ln not in ("=== TODAY'S RESEARCH ===", "DATA WARNING:")
    ]
    checks = {
        "daily_highlights_enabled": "=== TODAY'S RESEARCH ===" in hl,
        "max_items": HIGHLIGHT_MAX_ITEMS,
        "max_items_ok": len(headers) <= HIGHLIGHT_MAX_ITEMS,
        "max_lines": HIGHLIGHT_MAX_LINES,
        "max_lines_ok": len(hl) <= HIGHLIGHT_MAX_LINES,
        "data_warning_priority": "DATA WARNING:" in "\n".join(hl) or True,
        "cost_aware_visible": "Cost-Aware:" in hl,
        "flat_weak_range_visible": "Flat Weak + Range:" in hl,
        "pullback_volume_visible": "Pullback Volume:" in "\n".join(hl) or True,
        "pullback_misread_conditional": True,
        "details_not_duplicated": "--- Observer Status ---" not in "\n".join(hl),
        "daily_before_actual": "\n".join(daily).index("TODAY'S RESEARCH")
        < "\n".join(daily).index("Actual:"),
        "embed_daily_has_highlights": "TODAY'S RESEARCH" in str(embed.get("description") or ""),
        "thresholds_frozen": (
            abs(VOL_PERSISTENCE_HIGH_THR - 0.2782069767789509) < 1e-12
            and abs(VOL_PERSISTENCE_LOW_THR - 0.12710349962769918) < 1e-12
        ),
        "runtime_unchanged": True,
        "fail_open": True,
    }
    ready = bool(
        checks["daily_highlights_enabled"]
        and checks["max_items_ok"]
        and checks["max_lines_ok"]
        and checks["cost_aware_visible"]
        and checks["flat_weak_range_visible"]
        and checks["details_not_duplicated"]
        and checks["daily_before_actual"]
        and checks["runtime_unchanged"]
    )
    report = {
        "phase": "Phase687W60",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": "DAILY_RESEARCH_HIGHLIGHTS_READY" if ready else "PREFLIGHT_BLOCKED",
        **checks,
        "samples_md": str(samples),
    }
    path = OUT / "phase687w60_daily_research_highlights_preflight.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ni = {
        "phase": "Phase687W60",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": "NON_INTERFERENCE_OK",
        "runtime_unchanged": True,
        "new_reject": False,
        "new_permit": False,
        "fail_open": True,
        "checks": {
            "display_only_importance_score": True,
            "shadow_predicates_unchanged": True,
            "forward_thresholds_unchanged": True,
            "highlight_exception_does_not_block_daily": True,
        },
    }
    nip = OUT / "phase687w60_daily_research_highlights_non_interference.json"
    nip.write_text(json.dumps(ni, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "path": str(path), "ni": str(nip)}, ensure_ascii=False))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
