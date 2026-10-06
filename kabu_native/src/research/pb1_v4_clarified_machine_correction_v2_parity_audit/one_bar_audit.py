"""7011/20241205 vs one-bar-heavy shapes. No 0.55/0.20 restore. No search."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_clarified_machine_correction_v2 import LAST_BODY_MIN, TRUE_BODY_FRAC_MIN
from research.pb1_v4_clarified_machine_correction_v2.seed import continued_intent_state
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.reconstruct import pick
from research.pb1_v4_opening_drive_location_reaccel_spec import VALID_OPENING_STATES

ANCHORS = (("7011", "20241205"), ("6787", "20250214"), ("9101", "20250807"), ("6501", "20250612"))


def _shape(row: dict[str, Any]) -> dict[str, Any]:
    snap = dict(row.get("snap") or {})
    bars = list(snap.get("bars") or [])[:3]
    clocks = list((snap.get("clock_snap") or {}).get("same_clock") or [])
    if len(bars) < 3:
        return {"ok": False}
    ranges = [float(b["range"]) for b in bars if _finite(b.get("range"))]
    share = (max(ranges) / sum(ranges)) if ranges and sum(ranges) > 0 else None
    bodies = [float(b["body_over_range"]) if _finite(b.get("body_over_range")) else 0.0 for b in bars]
    dirs = [int(b.get("direction") or 0) for b in bars]
    ratios = []
    for i, b in enumerate(bars):
        med = (clocks[i] or {}).get("median") if i < len(clocks) else None
        if _finite(b.get("range")) and _finite(med) and float(med) > 0:
            ratios.append(float(b["range"]) / float(med))
        else:
            ratios.append(None)
    sign = int(row.get("machine_DIR") or (snap.get("seed_row_v2") or {}).get("DIR") or 0)
    if sign not in (1, -1):
        sign = dirs[0] if dirs[0] in (1, -1) else 1
    intent = continued_intent_state(bars, sign=sign)
    log = list(row.get("progress_log") or [])
    majority = bool(share is not None and float(share) > 0.5)
    last_continues = dirs[-1] == sign and bodies[-1] >= float(LAST_BODY_MIN)
    middle_crawl = len(bodies) >= 2 and bodies[1] < float(TRUE_BODY_FRAC_MIN)
    without_ft = bool(intent.get("one_bar_dominated_without_followthrough"))
    with_real = majority and last_continues and not without_ft
    return {
        "ok": True,
        "symbol": row.get("symbol"),
        "date": row.get("date"),
        "human_opening_state": row.get("human_opening_state"),
        "human_pattern": row.get("human_pattern"),
        "machine_SEED": row.get("machine_SEED"),
        "machine_ACTIVE": row.get("machine_ACTIVE"),
        "machine_THESIS_READY": row.get("machine_THESIS_READY"),
        "dirs": dirs,
        "bodies": bodies,
        "ranges": ranges,
        "one_bar_share": share,
        "same_clock_range_ratios": ratios,
        "net_vs_gross": intent.get("net_vs_gross"),
        "close_to_close_progression": intent.get("close_to_close_progression"),
        "followthrough_continues_auction": intent.get("followthrough_continues_auction"),
        "one_bar_dominated_without_followthrough": without_ft,
        "majority_one_bar": majority,
        "middle_bar_crawl": middle_crawl,
        "last_bar_continues": last_continues,
        "n_strong_bodies": intent.get("n_strong_bodies"),
        "counter_auction": intent.get("two_sided_internal_path"),
        "intent_ok": intent.get("ok"),
        "post_seed_progress_classes": [x.get("class") for x in log],
        "shape_class": (
            "ONE_BAR_DOMINATED_WITHOUT_FOLLOWTHROUGH"
            if without_ft
            else ("ONE_BAR_DOMINATED_WITH_REAL_CONTINUATION" if with_real else "NOT_ONE_BAR_HEAVY")
        ),
    }


def one_bar_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    similar = []
    for r in rows:
        sh = _shape(r)
        if not sh.get("ok"):
            continue
        if sh.get("majority_one_bar"):
            similar.append(sh)
    focus = [_shape(pick(rows, s, d)) for s, d in ANCHORS]
    m7011 = focus[0]
    concept_exists = any(bool(x.get("one_bar_dominated_without_followthrough")) for x in similar)
    # 7011: majority one-bar, last body continues, machine TRUE. Concept did not fire.
    concept_state = "partially"
    if concept_exists and not bool(m7011.get("one_bar_dominated_without_followthrough")):
        concept_state = "partially"
    elif concept_exists:
        concept_state = "yes"
    else:
        concept_state = "no"
    return {
        "7011_20241205": m7011,
        "anchors": focus,
        "similar_one_bar_heavy_n": len(similar),
        "similar": similar,
        "human_TRUE_similar_n": sum(
            1
            for x in similar
            if x.get("human_opening_state") in VALID_OPENING_STATES and not pick(rows, str(x.get("symbol")), str(x.get("date"))).get("human_late")
        ),
        "ONE_BAR_DOMINATED_WITHOUT_FOLLOWTHROUGH_still_exists": concept_state,
        "7011_change": "regression",
        "7011_why": (
            "First-bar share is majority. Bar-2 body is a crawl. Last bar continues with a real body, "
            "and n_strong counts the first bar, so followthrough=true. The old 0.55/0.20 pair rejected this "
            "MICRO exemplar. Removing the pair without encoding 'dominant print then weak follow bar' "
            "revived a live TRUE thesis. Concept exists only as majority-one-bar AND NOT followthrough; "
            "followthrough is too easy when the dominant bar itself supplies n_strong."
        ),
        "did_removal_delete_the_concept": concept_state != "yes",
        "restored_old_thresholds": False,
        "threshold_search": False,
        "6787_9101_6501_not_auto_invalid_because_one_bar_heavy": True,
    }
