"""ONE_BAR_SHARE_MAX / WEAK_BAR_BODY_MAX generalizability. No 0.55 vs 0.60 search."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_clarified_machine_implementation import ONE_BAR_SHARE_MAX, WEAK_BAR_BODY_MAX
from research.pb1_v4_opening_drive_location_reaccel_spec import VALID_OPENING_STATES


def one_bar_rca(rows: list[dict[str, Any]]) -> dict[str, Any]:
    similar = []
    human_true_similar = []
    for r in rows:
        bars = list(((r.get("snap") or {}).get("bars") or [])[:3])
        if len(bars) < 3:
            continue
        ranges = [float(b["range"]) for b in bars if _finite(b.get("range"))]
        if not ranges or sum(ranges) <= 0:
            continue
        share = max(ranges) / sum(ranges)
        bodies = [float(b["body_over_range"]) if _finite(b.get("body_over_range")) else 0.0 for b in bars]
        weak_follow = min(bodies[1:]) < float(WEAK_BAR_BODY_MAX) if len(bodies) >= 2 else False
        dominated = share >= float(ONE_BAR_SHARE_MAX) and (sum(1 for x in bodies if x >= 0.35) < 2 or weak_follow)
        rec = {
            "rca_id": r.get("rca_id"),
            "symbol": r.get("symbol"),
            "date": r.get("date"),
            "human_opening_state": r.get("human_opening_state"),
            "machine_SEED": r.get("machine_SEED"),
            "one_bar_share": share,
            "bodies": bodies,
            "weak_followthrough": weak_follow,
            "matches_current_numeric_shape": dominated,
        }
        if dominated:
            similar.append(rec)
            if r.get("human_opening_state") in VALID_OPENING_STATES and not r.get("human_late"):
                human_true_similar.append(rec)
    concept = "ONE_BAR_DOMINATED_WITHOUT_FOLLOWTHROUGH"
    generalizable = len(similar) >= 2 and len(human_true_similar) == 0
    return {
        "ONE_BAR_SHARE_MAX": ONE_BAR_SHARE_MAX,
        "WEAK_BAR_BODY_MAX": WEAK_BAR_BODY_MAX,
        "exemplar_named": "7011/20241205",
        "concept": concept,
        "similar_shape_n": len(similar),
        "human_TRUE_with_similar_shape_n": len(human_true_similar),
        "similar": similar,
        "human_TRUE_similar": human_true_similar,
        "numeric_search_055_vs_060": False,
        "should_exist_as_standalone_numeric_pair": False,
        "should_be_represented_by_continued_intent_state": True,
        "generalizable_concept": bool(generalizable) or len(similar) >= 1,
        "generalizable_numeric_constants": False if len(human_true_similar) or len(similar) <= 1 else True,
        "survive_future_correction_unchanged": "no",
        "survive_reason": (
            "The concept (one large print then crawl) is reusable continued-intent language. "
            "The specific 0.55/0.20 pair is a single-exemplar boundary and should not be frozen unchanged."
        ),
    }
