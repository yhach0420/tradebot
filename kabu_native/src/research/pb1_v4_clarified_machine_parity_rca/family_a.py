"""Family A causal-clear audit. 09:14 identification is not automatically invalid."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite

FOCUS = (("9432", "20250402"), ("8058", "20250814"))


def _pick(rows: list[dict[str, Any]], symbol: str, date: str) -> dict[str, Any]:
    return next((x for x in rows if str(x.get("symbol")) == symbol and str(x.get("date")) == date), {}) or {}


def _cleared(*, sign: int, px: Any, z: dict[str, Any]) -> bool:
    if not (_finite(px) and _finite(z.get("zone_high")) and _finite(z.get("zone_low"))):
        return False
    if sign > 0:
        return float(px) > float(z["zone_high"])
    return float(px) < float(z["zone_low"])


def classify_family_a(row: dict[str, Any]) -> dict[str, Any]:
    snap = dict(row.get("snap") or {})
    bars = list(snap.get("bars") or [])
    seed_bar = bars[2] if len(bars) >= 3 else None
    sign = int((snap.get("seed_row") or {}).get("DIR") or row.get("machine_DIR") or 0)
    loc_raw = snap.get("location_at_0914")
    loc0914 = dict(loc_raw) if isinstance(loc_raw, dict) else {}
    zones = list(snap.get("zones") or [])
    open_px = snap.get("open")
    pdc = snap.get("pdc")
    loc_t = row.get("location_t")
    family = str(row.get("location_family") or loc0914.get("family") or "")
    kind = loc0914.get("level_kind") or (str(row.get("location_id") or "").split("|")[-2] if row.get("location_id") else None)
    matched = None
    for z in zones:
        if str(z.get("name") or "") == str(kind or "") or str(z.get("kind") or "") == str(kind or ""):
            matched = z
            break
    if matched is None and loc0914.get("level") is not None:
        for z in zones:
            if _finite(z.get("zone_high")) and abs(float(z["zone_high"]) - float(loc0914["level"])) < 1e-6:
                matched = z
                break
            if _finite(z.get("zone_low")) and abs(float(z["zone_low"]) - float(loc0914["level"])) < 1e-6:
                matched = z
                break
    a_class = None
    clear_t = None
    if family.startswith("A") and sign in (1, -1) and matched:
        already_at_open = _cleared(sign=sign, px=open_px, z=matched)
        seed_clears = seed_bar is not None and _cleared(
            sign=sign, px=(seed_bar.get("h") if sign > 0 else seed_bar.get("l")), z=matched
        )
        open_inside = False
        if _finite(matched.get("zone_low")) and _finite(matched.get("zone_high")) and _finite(open_px):
            open_inside = float(matched["zone_low"]) <= float(open_px) <= float(matched["zone_high"])
        if seed_clears and not already_at_open:
            a_class = "A1_PREEXISTING_LEVEL_CLEARED_BY_OPENING_DRIVE"
            clear_t = str(seed_bar.get("t1") or "09:14")[:5]
        elif already_at_open and not open_inside:
            a_class = "A2_LEVEL_ALREADY_CLEARED_BEFORE_SEED"
            clear_t = "before_09:00"
        else:
            a_class = "A3_NO_REAL_CLEAR_BUT_CLASSIFIER_MATCHED_ZONE"
            clear_t = str(loc_t or "09:14")
    elif family.startswith("A"):
        a_class = "A3_NO_REAL_CLEAR_BUT_CLASSIFIER_MATCHED_ZONE"
    dist = None
    if matched and seed_bar and sign in (1, -1):
        level = float(matched["zone_high"]) if sign > 0 else float(matched["zone_low"])
        px = seed_bar.get("h") if sign > 0 else seed_bar.get("l")
        if _finite(px):
            dist = (float(px) - level) * float(sign)
    same_bar = bool(loc_t and str(loc_t) <= "09:14")
    kind_name = str(kind or (matched or {}).get("name") or (matched or {}).get("source") or "")
    if family.startswith("A") and kind_name in ("VWAP", "SMA25", "SMA75"):
        a_class = "A3_NO_REAL_CLEAR_BUT_CLASSIFIER_MATCHED_ZONE"
        clear_t = str(loc_t or "09:14")
    return {
        "symbol": row.get("symbol"),
        "date": row.get("date"),
        "human_opening_state": row.get("human_opening_state"),
        "human_location": row.get("human_location"),
        "family": family,
        "level_kind": kind,
        "level_value": (matched or {}).get("zone_high") if sign > 0 else (matched or {}).get("zone_low"),
        "level_known_at": "prior_session_or_causal_zone_book",
        "clear_event_t": clear_t,
        "location_identified_t": loc_t,
        "open": open_px,
        "pdc": pdc,
        "zone": matched,
        "distance_beyond_level_at_identification": dist,
        "same_bar_created_SEED_ACTIVE_CLEAR_LOCATION": same_bar and family.startswith("A"),
        "A_class": a_class,
        "automatically_invalid_because_0914": False,
        "semantically_weak_reason": (
            "Family A matched VWAP/MA, not a preexisting structural level. The 09:14 SEED+CLEAR is a classifier zone, not a causal clear event."
            if a_class == "A3_NO_REAL_CLEAR_BUT_CLASSIFIER_MATCHED_ZONE" and kind_name in ("VWAP", "SMA25", "SMA75")
            else (
                "already-cleared prior zone used as today's location without a same-session clear event"
                if a_class == "A2_LEVEL_ALREADY_CLEARED_BEFORE_SEED"
                else (
                    "classifier matched a zone without a genuine far-side clear"
                    if a_class == "A3_NO_REAL_CLEAR_BUT_CLASSIFIER_MATCHED_ZONE"
                    else (
                        "same 09:14 bar minted SEED+ACTIVE+CLEAR+LOCATION; valid only if the opening drive actually crossed a preexisting level"
                        if same_bar
                        else None
                    )
                )
            )
        ),
    }


def _loc0914_family(row: dict[str, Any]) -> str:
    loc = ((row.get("snap") or {}).get("location_at_0914"))
    if isinstance(loc, dict):
        return str(loc.get("family") or "")
    return ""


def family_a_rca(rows: list[dict[str, Any]]) -> dict[str, Any]:
    a_rows = [
        classify_family_a(r)
        for r in rows
        if str(r.get("location_family") or "").startswith("A") or _loc0914_family(r).startswith("A")
    ]
    counts = {}
    for x in a_rows:
        k = str(x.get("A_class") or "unclassified")
        counts[k] = int(counts.get(k) or 0) + 1
    focus = [classify_family_a(_pick(rows, s, d)) for s, d in FOCUS]
    a3_n = int(counts.get("A3_NO_REAL_CLEAR_BUT_CLASSIFIER_MATCHED_ZONE") or 0)
    return {
        "family_A_0914_automatically_invalid": False,
        "A1_and_A2_may_be_valid": True,
        "A3_is_not_valid": True,
        "n": len(a_rows),
        "by_class": counts,
        "lack_real_causal_clear_n": a3_n,
        "rows": a_rows,
        "9432_20250402": focus[0],
        "8058_20250814": focus[1],
        "no_extra_future_hold_required": True,
    }
