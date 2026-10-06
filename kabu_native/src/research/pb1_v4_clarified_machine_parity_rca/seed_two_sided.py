"""Pullback vs two-sided. No threshold search."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_clarified_machine_implementation.seed import continued_intent_state, opening_close_loc


def _pair(bars: list[dict[str, Any]], *, sign: int) -> dict[str, Any]:
    if len(bars) < 3 or sign not in (1, -1):
        return {"ok": False}
    intent = continued_intent_state(bars[:3], sign=sign)
    dirs = [int(b.get("direction") or 0) for b in bars[:3]]
    bodies = [b.get("body_over_range") for b in bars[:3]]
    ranges = [float(b["range"]) for b in bars[:3] if _finite(b.get("range"))]
    nets = [float(b["net"]) if _finite(b.get("net")) else None for b in bars[:3]]
    first_leg = abs(float(nets[0])) if _finite(nets[0]) else None
    counter = abs(float(nets[1])) if _finite(nets[1]) and dirs[1] == -sign else None
    retrace = (float(counter) / float(first_leg)) if _finite(counter) and _finite(first_leg) and float(first_leg) > 0 else None
    loc = opening_close_loc(bars[:3])
    first_invalidated = False
    if sign > 0 and _finite(bars[0].get("l")) and _finite(bars[1].get("c")):
        first_invalidated = float(bars[1]["c"]) < float(bars[0]["l"])
    if sign < 0 and _finite(bars[0].get("h")) and _finite(bars[1].get("c")):
        first_invalidated = float(bars[1]["c"]) > float(bars[0]["h"])
    last_continues_original = dirs[0] == sign and dirs[2] == sign
    last_after_fight = dirs[1] == -sign and _finite(bodies[1]) and float(bodies[1]) >= 0.35
    return {
        "ok": True,
        "sign": sign,
        "dirs": dirs,
        "bodies": bodies,
        "ranges": ranges,
        "nets": nets,
        "counter_body": bodies[1] if len(bodies) > 1 else None,
        "counter_displacement_over_first_leg": retrace,
        "counter_bar_range": ranges[1] if len(ranges) > 1 else None,
        "intent": intent,
        "opening_close_loc": loc,
        "committed_finish": loc is not None and (float(loc) > 0.65 if sign > 0 else float(loc) < 0.35),
        "first_directional_auction_materially_invalidated": first_invalidated,
        "last_bar_continues_original_auction": last_continues_original and not last_after_fight,
        "last_bar_is_final_dominance_after_two_sided_fight": last_after_fight and last_continues_original,
        "middle_only_pullback": bool(intent.get("middle_only_pullback")),
        "two_sided_internal_path": bool(intent.get("two_sided_internal_path")),
        "two_sided_balance_machine": bool(intent.get("two_sided_balance")),
        "machine_requires_uncommitted_close_to_call_two_sided": True,
    }


def compare_8031_7741(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def pick(sym: str, date: str) -> dict[str, Any]:
        return next((x for x in rows if str(x.get("symbol")) == sym and str(x.get("date")) == date), {}) or {}

    a = pick("8031", "20250225")
    b = pick("7741", "20250314")
    sa = _pair(list(((a.get("snap") or {}).get("bars") or [])[:3]), sign=int(((a.get("snap") or {}).get("seed_row") or {}).get("DIR") or a.get("machine_DIR") or 0))
    sb = _pair(list(((b.get("snap") or {}).get("bars") or [])[:3]), sign=int(((b.get("snap") or {}).get("seed_row") or {}).get("DIR") or b.get("machine_DIR") or 0))
    return {
        "8031": {
            **sa,
            "human": a.get("human_opening_state"),
            "machine_SEED": a.get("machine_SEED"),
            "verdict": "legitimate_pullback",
            "why": (
                "Middle opposite body is a doji-scale nick inside an otherwise one-sided +/−/+ path. "
                "First directional auction is not materially invalidated. Last bar continues the original auction. "
                "V2 limited-counter concept is satisfied."
            ),
        },
        "7741": {
            **sb,
            "human": b.get("human_opening_state"),
            "machine_SEED": b.get("machine_SEED"),
            "verdict": "two_sided_bug",
            "why": (
                "Middle bar is a substantial opposite auction (body comparable to the other two). "
                "That is a two-sided fight, not a pullback. A committed final close must not erase it. "
                "Machine two_sided_balance also requires uncommitted loc, so TRUE still mints."
            ),
        },
        "semantic_distinction": (
            "A weak middle pullback keeps the original auction intact. "
            "A substantial opposite bar invalidates 'limited counter-directional auction' even if the last print dominates."
        ),
        "committed_final_bar_erases_substantial_counter_auction": False,
        "v2_faithful": False,
        "threshold_search": False,
    }
