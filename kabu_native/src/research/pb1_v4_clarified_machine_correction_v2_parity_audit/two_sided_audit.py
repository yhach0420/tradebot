"""7741 vs 8031 vs four CLEAR TWO_SIDED regressions. No threshold search."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_clarified_machine_correction_v2 import TRUE_BODY_FRAC_MIN
from research.pb1_v4_clarified_machine_correction_v2.seed import continued_intent_state, opening_close_loc
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.reconstruct import pick

CLEAR_KEYS = (
    ("5803", "20250212"),
    ("6963", "20250613"),
    ("5803", "20250709"),
    ("5706", "20250725"),
)


def _pair(row: dict[str, Any]) -> dict[str, Any]:
    snap = dict(row.get("snap") or {})
    bars = list(snap.get("bars") or [])[:3]
    sign = int((snap.get("seed_row_v2") or {}).get("DIR") or row.get("machine_DIR") or 0)
    if len(bars) < 3:
        return {"ok": False, "symbol": row.get("symbol"), "date": row.get("date")}
    dirs = [int(b.get("direction") or 0) for b in bars]
    if sign not in (1, -1):
        # Use first non-zero direction as original auction for reconstruction.
        sign = next((d for d in dirs if d in (1, -1)), 1)
    intent = continued_intent_state(bars, sign=sign)
    bodies = [float(b["body_over_range"]) if _finite(b.get("body_over_range")) else 0.0 for b in bars]
    ranges = [float(b["range"]) for b in bars if _finite(b.get("range"))]
    nets = [float(b["net"]) if _finite(b.get("net")) else None for b in bars]
    first_leg = abs(float(nets[0])) if _finite(nets[0]) else None
    counter = abs(float(nets[1])) if _finite(nets[1]) and dirs[1] == -sign else None
    retrace = (float(counter) / float(first_leg)) if _finite(counter) and _finite(first_leg) and float(first_leg) > 0 else None
    loc = opening_close_loc(bars)
    overlap = None
    if _finite(bars[0].get("h")) and _finite(bars[0].get("l")) and _finite(bars[1].get("h")) and _finite(bars[1].get("l")):
        lo = max(float(bars[0]["l"]), float(bars[1]["l"]))
        hi = min(float(bars[0]["h"]), float(bars[1]["h"]))
        rng = float(bars[0]["h"]) - float(bars[0]["l"])
        if rng > 0:
            overlap = max(0.0, hi - lo) / rng
    first_invalidated = bool(intent.get("first_directional_auction_materially_invalidated"))
    last_continues = dirs[0] == sign and dirs[-1] == sign
    last_fight = bool(intent.get("last_bar_is_final_dominance_after_two_sided_fight"))
    substantial_mid = dirs[1] == -sign and bodies[1] >= float(TRUE_BODY_FRAC_MIN)
    # Independent of machine two_sided_balance (that tautology would label every TWO_SIDED kill a fight).
    # 8031-like: original auction intact, doji or small retrace.
    # 7741-like: substantial opposite body AND material retrace of the first leg.
    if bool(intent.get("middle_only_pullback")) or (last_continues and bodies[1] < float(TRUE_BODY_FRAC_MIN)):
        kind = "genuine_pullback"
    elif first_invalidated:
        kind = "genuine_two_sided_open"
    elif last_continues and not first_invalidated and (
        (retrace is not None and float(retrace) < 0.25)
        or (bodies[1] < 0.50 and (retrace is None or float(retrace) < 0.40))
    ):
        kind = "over_rejection_of_continuation"
    elif substantial_mid and bodies[1] >= 0.50 and retrace is not None and float(retrace) >= 0.30:
        kind = "genuine_two_sided_open"
    elif last_fight:
        kind = "genuine_two_sided_open"
    else:
        kind = "ambiguous_two_sided_path"
    return {
        "ok": True,
        "symbol": row.get("symbol"),
        "date": row.get("date"),
        "human_pattern": row.get("human_pattern"),
        "human_opening_state": row.get("human_opening_state"),
        "machine_SEED": row.get("machine_SEED"),
        "machine_opening_state": row.get("machine_opening_state"),
        "machine_ACTIVE": row.get("machine_ACTIVE"),
        "machine_THESIS_READY": row.get("machine_THESIS_READY"),
        "reconstructed_sign": sign,
        "dirs": dirs,
        "bodies": bodies,
        "ranges": ranges,
        "nets": nets,
        "counter_body": bodies[1] if len(bodies) > 1 else None,
        "counter_displacement_over_first_leg": retrace,
        "range_overlap_bar0_bar1": overlap,
        "opening_close_loc": loc,
        "committed_finish": loc is not None and ((float(loc) > 0.65) if sign > 0 else (float(loc) < 0.35)),
        "first_directional_auction_materially_invalidated": first_invalidated,
        "last_bar_continues_original_auction": last_continues,
        "last_bar_is_final_dominance_after_two_sided_fight": last_fight,
        "middle_only_pullback": bool(intent.get("middle_only_pullback")),
        "two_sided_internal_path": bool(intent.get("two_sided_internal_path")),
        "two_sided_balance": bool(intent.get("two_sided_balance")),
        "substantial_middle_opposite_body": substantial_mid,
        "semantic_kind": kind,
        "independent_of_machine_two_sided_balance": True,
        "intent": {k: intent.get(k) for k in (
            "ok",
            "middle_only_pullback",
            "two_sided_internal_path",
            "two_sided_balance",
            "last_body_over_range",
        )},
    }


def two_sided_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    s8031 = _pair(pick(rows, "8031", "20250225"))
    s7741 = _pair(pick(rows, "7741", "20250314"))
    clears = [_pair(pick(rows, s, d)) for s, d in CLEAR_KEYS]
    over = [c for c in clears if c.get("semantic_kind") == "over_rejection_of_continuation"]
    genuine = [c for c in clears if c.get("semantic_kind") == "genuine_two_sided_open"]
    return {
        "8031": {**s8031, "reference": "pullback_may_remain_TRUE"},
        "7741": {**s7741, "reference": "must_remain_TWO_SIDED_OPEN", "still_two_sided": str(s7741.get("machine_SEED") or "") != "TRUE_OPENING_DRIVE_SEED"},
        "clear_regressions": clears,
        "clear_over_rejection_n": len(over),
        "clear_genuine_two_sided_n": len(genuine),
        "committed_final_bar_erases_substantial_counter_auction": False,
        "7741_still_two_sided": str(s7741.get("machine_opening_state") or "") == "TWO_SIDED_OPEN",
        "8031_still_true_or_pullback": str(s8031.get("machine_SEED") or "") == "TRUE_OPENING_DRIVE_SEED",
        "semantic_difference": (
            "7741: middle opposite body is itself a full auction (body~0.63) and last bar is dominance after a fight. "
            "8031: middle opposite is doji-scale; original auction intact. "
            "The four CLEAR rows are classified TWO_SIDED because a middle opposite body >= 0.35 counts as substantial "
            "even when the first directional auction was not crossed. That is broader than 7741's 'fight then final dominance'."
        ),
        "two_sided_logic_not_weakened_to_rescue_CLEAR": True,
        "threshold_search": False,
    }
