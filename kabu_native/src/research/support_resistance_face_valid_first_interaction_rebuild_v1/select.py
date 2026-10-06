"""Small salient set at the session open. Structural location only. No PnL rank."""
from __future__ import annotations

from typing import Any


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _nearest_above(zones: list[dict[str, Any]], open_px: float) -> dict[str, Any] | None:
    cands = []
    for z in zones:
        if float(z["center"]) > open_px or float(z["lo"]) >= open_px or (float(z["lo"]) <= open_px <= float(z["hi"])):
            dist = max(0.0, float(z["lo"]) - open_px)
            if open_px > float(z["hi"]):
                continue
            cands.append((dist, abs(float(z["center"]) - open_px), z))
    if not cands:
        return None
    cands.sort(key=lambda t: (t[0], t[1]))
    return cands[0][2]


def _nearest_below(zones: list[dict[str, Any]], open_px: float) -> dict[str, Any] | None:
    cands = []
    for z in zones:
        if float(z["center"]) < open_px or float(z["hi"]) <= open_px or (float(z["lo"]) <= open_px <= float(z["hi"])):
            dist = max(0.0, open_px - float(z["hi"]))
            if open_px < float(z["lo"]):
                continue
            cands.append((dist, abs(open_px - float(z["center"])), z))
    if not cands:
        return None
    cands.sort(key=lambda t: (t[0], t[1]))
    return cands[0][2]


def select_salient(
    *,
    snap: dict[str, Any],
    open_px: float,
) -> dict[str, Any]:
    if not _finite(open_px):
        return {
            "resistance": None,
            "support": None,
            "broken_resistance": None,
            "broken_support": None,
            "n_selected_resistance": 0,
            "n_selected_support": 0,
        }
    res = _nearest_above(list(snap.get("resistance_active") or []), float(open_px))
    sup = _nearest_below(list(snap.get("support_active") or []), float(open_px))
    br = _nearest_below(list(snap.get("resistance_broken") or []), float(open_px))
    bs = _nearest_above(list(snap.get("support_broken") or []), float(open_px))
    return {
        "resistance": res,
        "support": sup,
        "broken_resistance": br,
        "broken_support": bs,
        "n_selected_resistance": 1 if res else 0,
        "n_selected_support": 1 if sup else 0,
        "n_selected_broken_resistance": 1 if br else 0,
        "n_selected_broken_support": 1 if bs else 0,
        "no_salient_resistance": res is None,
        "no_salient_support": sup is None,
    }


def selected_list(sel: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    mapping = (
        ("NEAREST_ACTIVE_RESISTANCE_ABOVE", "resistance", "ACTIVE_RESISTANCE"),
        ("NEAREST_ACTIVE_SUPPORT_BELOW", "support", "ACTIVE_SUPPORT"),
        ("NEAREST_BROKEN_RESISTANCE_BELOW", "broken_resistance", "BROKEN_RESISTANCE_CANDIDATE"),
        ("NEAREST_BROKEN_SUPPORT_ABOVE", "broken_support", "BROKEN_SUPPORT_CANDIDATE"),
    )
    for slot, key, label in mapping:
        z = sel.get(key)
        if z:
            out.append({**z, "selection_slot": slot, "selection_label": label})
    return out
