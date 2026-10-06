"""FORM B boundary correspondence. No tune. No economic optimization."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_clarified_machine_correction_v2 import ATR_SANITY_FRAC, FAIL_VISIBLE_RANGE_MIN, WIDE_REJECTION_BODY_MAX
from research.pb1_v4_clarified_machine_correction_v2.seed import bar_close_loc


def _metrics(row: dict[str, Any]) -> dict[str, Any] | None:
    snap = dict(row.get("snap") or {})
    bars = list(snap.get("bars") or [])[:3]
    clocks = list((snap.get("clock_snap") or {}).get("same_clock") or [])
    if not bars:
        return None
    first = bars[0]
    body = first.get("body_over_range")
    loc = bar_close_loc(first)
    med = (clocks[0] or {}).get("median") if clocks else None
    rn = (float(first["range"]) / float(med)) if _finite(first.get("range")) and _finite(med) and float(med) > 0 else None
    atr = snap.get("atr20")
    atr_ok = True
    atr_ratio = None
    if _finite(atr) and float(atr) > 0 and _finite(first.get("range")):
        atr_ratio = float(first["range"]) / (float(ATR_SANITY_FRAC) * float(atr))
        atr_ok = float(first["range"]) >= float(ATR_SANITY_FRAC) * float(atr)
    small = (not _finite(body)) or float(body) <= float(WIDE_REJECTION_BODY_MAX)
    interior = loc is not None and 0.35 <= float(loc) <= 0.65
    visible = _finite(rn) and float(rn) >= float(FAIL_VISIBLE_RANGE_MIN)
    fail = dict(snap.get("failed_open_v2") or {})
    return {
        "symbol": row.get("symbol"),
        "date": row.get("date"),
        "human_opening_state": row.get("human_opening_state"),
        "machine_SEED": row.get("machine_SEED"),
        "form": fail.get("form") or row.get("failed_open_form"),
        "first_body": body,
        "first_close_loc": loc,
        "range_over_clock": rn,
        "atr_sanity_ratio": atr_ratio,
        "small_body": small,
        "unresolved_interior": interior,
        "visible_range": visible,
        "atr_ok": atr_ok,
        "failed_open_ok": fail.get("ok"),
        "reason": fail.get("reason"),
    }


def form_b_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mets = [m for m in (_metrics(r) for r in rows) if m]
    form_b = [m for m in mets if m.get("form") == "WIDE_REJECTION_FAILED_ATTEMPT"]
    # candidates: small body + visible range geometry, regardless of later path
    geom = [m for m in mets if m.get("small_body") and m.get("visible_range")]
    inside = [m for m in geom if m.get("unresolved_interior") and m.get("atr_ok")]
    outside_loc = [m for m in geom if not m.get("unresolved_interior")]
    outside_atr = [m for m in geom if m.get("unresolved_interior") and not m.get("atr_ok")]

    def _edge(xs: list[dict[str, Any]], key: str, *, n: int = 3) -> list[dict[str, Any]]:
        scored = [x for x in xs if _finite(x.get(key))]
        scored.sort(key=lambda x: abs(float(x[key]) - 0.5) if key == "first_close_loc" else float(x[key]))
        return scored[:n]

    return {
        "form_b_minted_n": len(form_b),
        "form_b_rows": form_b,
        "small_body_visible_range_n": len(geom),
        "inside_interior_and_atr_n": len(inside),
        "outside_because_resolved_hammer_star_n": len(outside_loc),
        "outside_because_atr_sanity_n": len(outside_atr),
        "just_inside_close_loc": _edge(inside, "first_close_loc"),
        "just_outside_close_loc": _edge(outside_loc, "first_close_loc"),
        "just_inside_atr_ratio": _edge([m for m in inside if _finite(m.get("atr_sanity_ratio"))], "atr_sanity_ratio"),
        "just_outside_atr_ratio": _edge(outside_atr, "atr_sanity_ratio"),
        "boundaries": {
            "close_loc": [0.35, 0.65],
            "ATR_SANITY_FRAC": ATR_SANITY_FRAC,
            "FAIL_VISIBLE_RANGE_MIN": FAIL_VISIBLE_RANGE_MIN,
            "WIDE_REJECTION_BODY_MAX": WIDE_REJECTION_BODY_MAX,
        },
        "semantic_interpretation": (
            "Interior close loc reuses the existing uncommitted band so a completed hammer/star is not FORM B. "
            "ATR sanity reuses 0.40 so ordinary wide dojis (3382/20241115, 7182) stay out. "
            "Later two-bar opposite sequence is still required. Geometry alone does not mint FAILED_OPEN."
        ),
        "tuned": False,
        "economic_optimization": False,
    }
