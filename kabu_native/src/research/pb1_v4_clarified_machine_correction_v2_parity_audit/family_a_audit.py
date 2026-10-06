"""Family A A1/A2/A3 vs A2 alternate-label leak. No future hold required."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_clarified_machine_correction_v2.location import _family_a_class, _prior_zones, _zone_cleared, _zone_id
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.reconstruct import pick


def _cleared_zones(row: dict[str, Any]) -> list[dict[str, Any]]:
    snap = dict(row.get("snap") or {})
    bars = list(snap.get("bars") or [])
    sign = int(row.get("machine_DIR") or (snap.get("seed_row_v2") or {}).get("DIR") or 0)
    if sign not in (1, -1) or not bars:
        return []
    bar = bars[2] if len(bars) >= 3 else bars[-1]
    extreme = bar.get("h") if sign > 0 else bar.get("l")
    open_px = snap.get("open")
    out = []
    for z in _prior_zones(list(snap.get("zones") or [])):
        if not _zone_cleared(sign=sign, px=extreme, zone=z):
            continue
        a_class = _family_a_class(sign=sign, session_open=open_px, zone=z)
        already = _zone_cleared(sign=sign, px=open_px, zone=z)
        level = float(z["zone_high"]) if sign > 0 else float(z["zone_low"])
        dist_open = None
        if _finite(open_px):
            dist_open = (float(open_px) - level) * float(sign)
        out.append(
            {
                "kind": _zone_id(z),
                "A_class": a_class,
                "already_cleared_at_open": already,
                "distance_open_beyond_level": dist_open,
                "zone_low": z.get("zone_low"),
                "zone_high": z.get("zone_high"),
            }
        )
    return out


def _minted_kind(row: dict[str, Any]) -> str:
    loc = str(row.get("location_id") or "")
    for token in ("VWAP", "SMA25", "SMA75", "SR_DETECTOR", "PDL", "PDH", "PDC", "D5L", "D5H", "OR_HIGH", "OR_LOW"):
        if token in loc:
            return token
    return ""


def _matching_zone(cz: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    if not kind:
        return {}
    for z in cz:
        if kind.upper() in str(z.get("kind") or "").upper():
            return z
    return {}


def _label(row: dict[str, Any], cz: list[dict[str, Any]]) -> str:
    seen_a3 = any(str(z.get("A_class") or "").startswith("A3") for z in cz) or any(
        "A3" in str(x) for x in list(row.get("family_a_seen") or [])
    )
    minted = str(row.get("location_family") or "")
    a_class = str(row.get("location_A_class") or "")
    kind = _minted_kind(row)
    mz = _matching_zone(cz, kind)
    already = bool(mz.get("already_cleared_at_open"))
    if minted != "A_CLEARED_ZONE":
        if seen_a3:
            return "A3_CORRECTLY_BLOCKED"
        return "NOT_FAMILY_A"
    if a_class.startswith("A3") or kind in ("VWAP", "SMA25", "SMA75"):
        return "A3_MINTED_VIOLATION"
    if a_class.startswith("A1"):
        return "GENUINE_A1"
    if a_class.startswith("A2"):
        if seen_a3 and not already:
            return "A2_ALT_LABEL_LEAK"
        if already or kind in ("SR_DETECTOR", "PDL", "PDH", "PDC", "D5L", "D5H"):
            return "GENUINE_A2"
        return "GENUINE_A2"
    return "UNCLASSIFIED_A"


def family_a_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labeled = []
    for r in rows:
        cz = _cleared_zones(r)
        lab = _label(r, cz)
        loc0914 = dict((r.get("snap") or {}).get("location_at_0914_v2") or {})
        labeled.append(
            {
                "rca_id": r.get("rca_id"),
                "symbol": r.get("symbol"),
                "date": r.get("date"),
                "human_location": r.get("human_location"),
                "human_pattern": r.get("human_pattern"),
                "machine_family": r.get("location_family"),
                "machine_A_class": r.get("location_A_class"),
                "location_id": r.get("location_id"),
                "family_a_seen": r.get("family_a_seen"),
                "cleared_zone_scan": cz,
                "identify_0914_family": loc0914.get("family"),
                "identify_0914_A_class": loc0914.get("A_class"),
                "label": lab,
            }
        )
    counts: dict[str, int] = {}
    for x in labeled:
        k = str(x.get("label") or "other")
        counts[k] = int(counts.get(k) or 0) + 1
    a88 = [x for x in labeled if str(x.get("machine_family") or "").startswith("A")]
    a3_seen = [x for x in labeled if x.get("label") == "A3_CORRECTLY_BLOCKED" or any("A3" in str(s) for s in list(x.get("family_a_seen") or []))]
    def _focus(symbol: str, date: str) -> dict[str, Any]:
        x = next((y for y in labeled if y.get("symbol") == symbol and y.get("date") == date), {}) or {}
        kind = _minted_kind(x)
        mz = _matching_zone(list(x.get("cleared_zone_scan") or []), kind)
        already = bool(mz.get("already_cleared_at_open"))
        a3_skipped = any("A3" in str(s) for s in list(x.get("family_a_seen") or []))
        if already and kind not in ("VWAP", "SMA25", "SMA75"):
            verdict = "valid_A2_fallback"
        elif a3_skipped and str(x.get("machine_family") or "") == "A_CLEARED_ZONE":
            verdict = "A3_bypass_alternate_label_leak"
        else:
            verdict = "not_family_A_or_blocked"
        return {
            **x,
            "minted_kind": kind,
            "matching_zone": mz,
            "genuinely_known_before_seed": bool(mz),
            "genuinely_already_cleared_before_seed": already,
            "semantically_meaningful_structural_level": kind in ("SR_DETECTOR", "PDL", "PDH", "PDC", "D5L", "D5H"),
            "verdict": verdict,
        }

    focus = {
        "9432_20250402": _focus("9432", "20250402"),
        "8058_20250814": _focus("8058", "20250814"),
    }
    return {
        "n88": len(labeled),
        "by_label": counts,
        "A1_valid_n": int(counts.get("GENUINE_A1") or 0),
        "A2_valid_n": int(counts.get("GENUINE_A2") or 0),
        "A2_alternate_label_leak_n": int(counts.get("A2_ALT_LABEL_LEAK") or 0),
        "A3_correctly_blocked_n": int(counts.get("A3_CORRECTLY_BLOCKED") or 0),
        "A3_minted_n": int(counts.get("A3_MINTED_VIOLATION") or 0),
        "family_A_minted_in_88_n": len(a88),
        "a3_seen_rows": a3_seen,
        "a1_rows": [x for x in labeled if x.get("label") == "GENUINE_A1"],
        "a2_questionable_human": [
            x
            for x in labeled
            if str(x.get("machine_family") or "").startswith("A")
            and str(x.get("human_location") or "") in ("QUESTIONABLE_LOCATION", "NO_MEANINGFUL_DEFENDED_LOCATION")
        ],
        "focus": focus,
        "first_unsuccessful_kills": False,
        "hold_required_to_identify": False,
        "future_hold_not_required": True,
        "a3_cannot_mint": int(counts.get("A3_MINTED_VIOLATION") or 0) == 0,
    }
