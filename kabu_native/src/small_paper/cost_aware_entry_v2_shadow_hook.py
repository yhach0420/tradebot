"""Finalize-time Discord Summary lines for cost_aware_entry_v2_shadow (fail-open)."""

from __future__ import annotations

from typing import Any, Mapping


def format_cost_aware_entry_v2_shadow_lines(summary: Mapping[str, Any]) -> list[str]:
    try:
        from small_paper.cost_aware_entry_v2_shadow import format_discord_lines

        am_pm = ""
        am_block = summary.get("am_pm_session")
        if isinstance(am_block, Mapping):
            am_pm = str(am_block.get("kind") or "")
        return format_discord_lines(summary, am_pm=am_pm)
    except Exception:
        return []
