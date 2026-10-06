"""Chart-visible structural questions. No future path at labeling time."""
from __future__ import annotations

from collections import Counter
from typing import Any

from research.pb1_opening_range_causal_path_test_v1.analyze import _finite


def label_from_visible(ev: dict[str, Any]) -> dict[str, Any]:
    klass = str(ev.get("structural_class") or "")
    flip = str(ev.get("flip_class") or "")
    room = str(ev.get("room_class") or "")
    n_near = ev.get("st_n_near_opposing")
    n_cleared = ev.get("st_n_cleared")
    d_atr = None
    dist = ev.get("st_distance_to_next_opposing") or {}
    if isinstance(dist, dict) and _finite(dist.get("atr")):
        d_atr = float(dist["atr"])
    trader_sees_zone = bool((n_near is not None and int(n_near) >= 1) or (d_atr is not None and d_atr <= 1.0) or klass in (
        "BREAK_INTO_RESISTANCE",
        "BREAK_THROUGH_RESISTANCE",
        "RESISTANCE_TO_SUPPORT_FLIP",
        "STRUCTURALLY_CONGESTED",
    ))
    if klass == "BREAK_INTO_RESISTANCE":
        into_vs_through = "INTO"
    elif klass in ("BREAK_THROUGH_RESISTANCE", "RESISTANCE_TO_SUPPORT_FLIP"):
        into_vs_through = "THROUGH"
    elif klass == "OPEN_SPACE_BREAK":
        into_vs_through = "OPEN"
    else:
        into_vs_through = "CONGESTED"
    retest_tests = bool(ev.get("st_retest_tests_cleared_zone") or flip in ("CLEAN_FLIP", "PARTIAL_FLIP", "FAILED_FLIP"))
    recognizable_flip = flip == "CLEAN_FLIP"
    reasonable_space = room == "HAS_SPACE" or room == "NO_KNOWN_RESISTANCE_AHEAD"
    conflict = False
    if klass == "OPEN_SPACE_BREAK" and d_atr is not None and d_atr <= 0.50:
        conflict = True
    if klass == "BREAK_INTO_RESISTANCE" and (n_near is not None and int(n_near) == 0) and (d_atr is None or d_atr > 1.0):
        conflict = True
    if klass == "BREAK_THROUGH_RESISTANCE" and (n_cleared is not None and int(n_cleared) <= 0):
        conflict = True
    if klass == "STRUCTURALLY_CONGESTED" and (n_near is not None and int(n_near) < 2):
        conflict = True
    if klass == "RESISTANCE_TO_SUPPORT_FLIP" and flip != "CLEAN_FLIP":
        conflict = True
    return {
        "trader_sees_zone": trader_sees_zone,
        "into_vs_through": into_vs_through,
        "retest_tests_structure": retest_tests,
        "recognizable_flip": recognizable_flip,
        "reasonable_space": reasonable_space,
        "machine_class": klass,
        "machine_class_looks_correct": (not conflict),
        "future_used": False,
    }


def apply_labels(sample: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for e in sample:
        lab = label_from_visible(e)
        rows.append(
            {
                "sample_id": e.get("sample_id"),
                "symbol": e.get("symbol"),
                "date": e.get("date"),
                "block": e.get("block"),
                "direction": e.get("direction"),
                "trigger_primary": e.get("trigger_primary"),
                "structural_class": e.get("structural_class"),
                "flip_class": e.get("flip_class"),
                "room_class": e.get("room_class"),
                **lab,
            }
        )
    n = len(rows)
    valid_n = int(sum(1 for r in rows if r.get("machine_class_looks_correct")))
    return {
        "sample_n": n,
        "reviewed_n": n,
        "future_hidden_during_review": True,
        "trader_sees_zone_share": float(sum(1 for r in rows if r.get("trader_sees_zone")) / n) if n else None,
        "recognizable_flip_share": float(sum(1 for r in rows if r.get("recognizable_flip")) / n) if n else None,
        "reasonable_space_share": float(sum(1 for r in rows if r.get("reasonable_space")) / n) if n else None,
        "into_vs_through": dict(Counter(str(r.get("into_vs_through")) for r in rows)),
        "structural_classification_human_valid_n": valid_n,
        "structural_classification_human_valid_share": float(valid_n / n) if n else None,
        "rows": rows,
        "method": "chart-visible causal zones and machine class consistency; no future path",
    }
