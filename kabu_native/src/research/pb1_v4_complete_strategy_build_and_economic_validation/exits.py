"""Entry-aligned technical EXIT = Frozen V4 THESIS_LOST. Ops flatten 15:20. No fixed hold."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_complete_strategy_build_and_economic_validation import OPS_EXIT_ID, TECHNICAL_EXIT_ID
from research.pb1_v4_complete_strategy_build_and_economic_validation.clocks import flatten_open, next_open_after


def resolve_exit(
    rec: dict[str, Any],
    *,
    fill_t: str,
    thesis_lost: bool,
    thesis_lost_at: str | None,
) -> dict[str, Any]:
    fill = str(fill_t)[:5]
    lost_t = str(thesis_lost_at or "")[:5] if thesis_lost else ""
    if lost_t:
        if lost_t < fill:
            return {"ok": False, "reason": "lost_before_fill"}
        after = lost_t if lost_t > fill else fill
        nxt = next_open_after(rec, after_t=after, allow_pm=True)
        if nxt:
            return {
                "ok": True,
                "exit_reason": TECHNICAL_EXIT_ID,
                "thesis_death": True,
                "ops_flatten": False,
                "decision_t": lost_t,
                "exit_t": nxt["t"],
                "exit_px": nxt["px"],
                "same_bar_exit": False,
            }
    flat = flatten_open(rec, fill_t=fill_t)
    if not flat:
        return {"ok": False, "reason": "no_flatten_bar"}
    return {
        "ok": True,
        "exit_reason": OPS_EXIT_ID,
        "thesis_death": False,
        "ops_flatten": True,
        "decision_t": flat["t"],
        "exit_t": flat["t"],
        "exit_px": flat["px"],
        "same_bar_exit": False,
    }
