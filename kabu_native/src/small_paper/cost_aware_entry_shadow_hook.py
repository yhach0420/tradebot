"""Finalize-time summary lines for cost_aware_entry_shadow (fail-open)."""

from __future__ import annotations

from typing import Any, Mapping

from small_paper.cost_aware_entry_shadow import format_shadow_summary_lines


def format_cost_aware_entry_shadow_lines(summary: Mapping[str, Any]) -> list[str]:
    block = summary.get("cost_aware_entry_shadow")
    if not isinstance(block, Mapping):
        return []
    if not block.get("enabled") and block.get("selection_cycles") in (None, 0):
        return []
    return format_shadow_summary_lines(block)
