"""Pullback vs two-sided fight. No body/retrace grid search."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_clarified_machine_correction_v2.seed import continued_intent_state, opening_close_loc
from research.pb1_v4_clarified_machine_correction_v2_rca.reconstruct import pick

CLEAR4 = (
    ("5803", "20250212"),
    ("6963", "20250613"),
    ("5803", "20250709"),
    ("5706", "20250725"),
)
ANCHORS = (("8031", "20250225"), ("7741", "20250314"), ("6963", "20250613"))


def _sandwich(row: dict[str, Any]) -> dict[str, Any] | None:
    snap = dict(row.get("snap") or {})
    bars = list(snap.get("bars") or [])[:3]
    if len(bars) < 3:
        return None
    dirs = [int(b.get("direction") or 0) for b in bars]
    if not (
        dirs[0] in (1, -1)
        and dirs[1] == -dirs[0]
        and dirs[2] == dirs[0]
    ):
        return None
    sign = dirs[0]
    intent = continued_intent_state(bars, sign=sign)
    bodies = [float(b["body_over_range"]) if _finite(b.get("body_over_range")) else 0.0 for b in bars]
    ranges = [float(b["range"]) if _finite(b.get("range")) else None for b in bars]
    nets = [float(b["net"]) if _finite(b.get("net")) else None for b in bars]
    first_leg = abs(float(nets[0])) if _finite(nets[0]) else None
    first_exc = abs(float(ranges[0])) if _finite(ranges[0]) else None
    mid_net = abs(float(nets[1])) if _finite(nets[1]) else None
    mid_rng = ranges[1]
    retrace_net = (float(mid_net) / float(first_leg)) if _finite(mid_net) and _finite(first_leg) and float(first_leg) > 0 else None
    retrace_exc = (float(mid_rng) / float(first_exc)) if _finite(mid_rng) and _finite(first_exc) and float(first_exc) > 0 else None
    overlap = None
    if _finite(bars[0].get("h")) and _finite(bars[0].get("l")) and _finite(bars[1].get("h")) and _finite(bars[1].get("l")):
        lo = max(float(bars[0]["l"]), float(bars[1]["l"]))
        hi = min(float(bars[0]["h"]), float(bars[1]["h"]))
        rng0 = float(bars[0]["h"]) - float(bars[0]["l"])
        if rng0 > 0:
            overlap = max(0.0, hi - lo) / rng0
    first_c = bars[0].get("c")
    mid_c = bars[1].get("c")
    first_o = bars[0].get("o")
    undo = None
    if _finite(first_c) and _finite(first_o) and _finite(mid_c):
        first_prog = (float(first_c) - float(first_o)) * float(sign)
        undo_amt = (float(first_c) - float(mid_c)) * float(sign)
        undo = (float(undo_amt) / float(first_prog)) if abs(float(first_prog)) > 1e-12 else None
    # Middle close materially undoes first close if it gives back a majority of first-bar close progress.
    undoes = undo is not None and float(undo) >= 0.5
    first_invalid = bool(intent.get("first_directional_auction_materially_invalidated"))
    last_net = abs(float(nets[2])) if _finite(nets[2]) else None
    last_rng = ranges[2]
    last_vs_first_net = (float(last_net) / float(first_leg)) if _finite(last_net) and _finite(first_leg) and float(first_leg) > 0 else None
    last_vs_mid_rng = (float(last_rng) / float(mid_rng)) if _finite(last_rng) and _finite(mid_rng) and float(mid_rng) > 0 else None
    intact = (not first_invalid) and (not undoes)
    # Missing dimension: is the middle itself a committed opposite auction that rewrites
    # first-leg close progress, and is the third bar leftover dominance after that fight
    # rather than a new continuation of an intact first auction?
    middle_is_comparable_auction = (
        _finite(retrace_exc)
        and float(retrace_exc) >= 0.35
        and bodies[1] >= 0.50
    )
    third_is_new_drive = last_vs_first_net is not None and float(last_vs_first_net) >= 0.70 and bodies[2] >= 0.50
    if intact and not middle_is_comparable_auction:
        sem = "PULLBACK_ORIGINAL_AUCTION_INTACT"
        third_role = "CONTINUATION_AFTER_PULLBACK"
    elif middle_is_comparable_auction and (undoes or first_invalid):
        sem = "TWO_SIDED_COMMITTED_FIGHT"
        third_role = "FINAL_DOMINANCE_AFTER_TWO_SIDED_FIGHT"
    elif middle_is_comparable_auction and intact:
        sem = "MEANINGFUL_COUNTER_AUCTION"
        third_role = "CONTINUATION_AFTER_PULLBACK" if third_is_new_drive else "FINAL_DOMINANCE_AFTER_TWO_SIDED_FIGHT"
    else:
        sem = "AMBIGUOUS"
        third_role = "CONTINUATION_AFTER_PULLBACK" if intact else "FINAL_DOMINANCE_AFTER_TWO_SIDED_FIGHT"
    return {
        "ok": True,
        "symbol": row.get("symbol"),
        "date": row.get("date"),
        "human_pattern": row.get("human_pattern"),
        "human_opening_state": row.get("human_opening_state"),
        "machine_SEED": row.get("machine_SEED"),
        "machine_opening_state": row.get("machine_opening_state"),
        "machine_THESIS_READY": row.get("machine_THESIS_READY"),
        "dirs": dirs,
        "first_leg_net": first_leg,
        "first_leg_range": ranges[0],
        "first_leg_body": bodies[0],
        "middle_counter_net": mid_net,
        "middle_range": mid_rng,
        "middle_body_over_range": bodies[1],
        "counter_disp_over_first_leg_net": retrace_net,
        "counter_excursion_over_first_leg_excursion": retrace_exc,
        "overlap": overlap,
        "middle_undo_of_first_close_progress": undo,
        "middle_close_materially_undoes_first_close": undoes,
        "first_directional_auction_invalidated": first_invalid,
        "first_directional_auction_remains_structurally_intact": intact,
        "third_bar_net": last_net,
        "third_bar_range": last_rng,
        "third_bar_body": bodies[2],
        "third_bar_net_over_first_leg": last_vs_first_net,
        "third_bar_range_over_middle": last_vs_mid_rng,
        "opening_close_loc": opening_close_loc(bars),
        "middle_is_comparable_opposite_auction": middle_is_comparable_auction,
        "third_is_new_continuation_drive": third_is_new_drive,
        "third_bar_role": third_role,
        "semantic_class": sem,
        "machine_two_sided_balance": bool(intent.get("two_sided_balance")),
        "machine_middle_only_pullback": bool(intent.get("middle_only_pullback")),
    }


def two_sided_rca(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sandwiches = []
    for r in rows:
        rec = _sandwich(r)
        if rec:
            sandwiches.append(rec)
    anchors = {f"{s}_{d}": _sandwich(pick(rows, s, d)) for s, d in ANCHORS}
    clears = [_sandwich(pick(rows, s, d)) for s, d in CLEAR4]
    a8031 = anchors.get("8031_20250225") or {}
    a7741 = anchors.get("7741_20250314") or {}
    a6963 = anchors.get("6963_20250613") or {}
    c5803a = clears[0] or {}
    c6963 = clears[1] or {}
    c5803b = clears[2] or {}
    c5706 = clears[3] or {}
    return {
        "sandwich_n": len(sandwiches),
        "sandwiches": sandwiches,
        "anchors": anchors,
        "clear4": clears,
        "6963_vs_7741": (
            "6963 middle retrace of first-leg net is ~0.13 with middle range much smaller than the first dump; "
            "the first auction remains intact and bar 3 continues that dump. "
            "7741 middle is a committed opposite body (~0.63) that is itself an auction, then a smaller final bar "
            "after the fight. Body 0.39 vs 0.63 is not the separator; first-auction integrity and middle-as-auction are."
        ),
        "5803_0212_vs_7741": (
            "Both have ~0.34 retrace. 5803/0212 middle body is 0.38 and does not undo first-close progress; "
            "bar 3 is a large same-dir continuation (net ~135 vs first ~99). 7741 middle body 0.63 is a committed "
            "opposite auction and bar 3 is smaller leftover dominance. Retracement alone cannot separate them. "
            "The missing dimension is whether the middle bar rewrites first-leg close progress / is a comparable "
            "opposite auction, and whether bar 3 is continuation of an intact first auction or leftover after a fight."
        ),
        "5803_20250709_class": c5803b.get("semantic_class"),
        "5706_20250725_class": c5706.get("semantic_class"),
        "still_genuine_two_sided_5803_0709": c5803b.get("semantic_class") in ("TWO_SIDED_COMMITTED_FIGHT", "MEANINGFUL_COUNTER_AUCTION"),
        "still_genuine_two_sided_5706": c5706.get("semantic_class") in ("TWO_SIDED_COMMITTED_FIGHT", "MEANINGFUL_COUNTER_AUCTION"),
        "missing_semantic_dimension": (
            "Whether the middle bar is a committed opposite auction that undoes first-leg close progress, "
            "and whether the third bar continues an intact first auction or is leftover dominance after a fight."
        ),
        "body_035_sufficient": False,
        "retrace_alone_sufficient": False,
        "primary_cause": (
            "substantial_opp uses opposite body>=0.35 as a proxy for SUBSTANTIAL_COUNTER_AUCTION. "
            "That proxy is too broad: it treats a pullback with a real body (6963, 5803/0212) like 7741's fight."
        ),
        "8031": a8031,
        "7741": a7741,
        "6963_20250613": a6963,
        "5803_20250212": c5803a,
        "no_grid_search": True,
        "v2_already_has_limited_counter_auction": True,
    }
