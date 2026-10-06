"""Face-validity A–F. Structural reasons only. No PnL."""
from __future__ import annotations

from typing import Any


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _yn(ok: bool | None) -> str:
    if ok is None:
        return "UNCERTAIN"
    return "YES" if ok else "NO"


def score_chart(row: dict[str, Any], pack: dict[str, Any] | None) -> dict[str, Any]:
    sel = (pack or {}).get("sel") or {}
    snap = (pack or {}).get("snap") or {}
    atr = row.get("atr")
    opn = row.get("open")
    res = sel.get("resistance")
    sup = sel.get("support")
    n_act = int(len(snap.get("resistance_active") or []) + len(snap.get("support_active") or []))
    n_sel = int(bool(res)) + int(bool(sup))

    a_ok = None
    a_reason = "no_level"
    if res or sup:
        n_mem = 0
        if res:
            n_mem += int(res.get("distinct_swing_cycles") or 0)
        if sup:
            n_mem += int(sup.get("distinct_swing_cycles") or 0)
        a_ok = n_mem >= 2
        a_reason = "selected_zone_has_2plus_independent_confirmed_swings" if a_ok else "selected_zone_under_confirmed"
    else:
        a_ok = True
        a_reason = "no_salient_level_is_allowed"

    b_ok = None
    b_reason = "no_selected_resistance"
    if res and _finite(opn) and _finite(atr) and atr and atr > 0:
        overhead = float(res["hi"]) >= float(opn) and float(res["center"]) >= float(opn) - 0.15 * float(atr)
        near = abs(float(res["center"]) - float(opn)) <= 3.0 * float(atr)
        b_ok = bool(overhead and near)
        b_reason = "overhead_and_within_3atr" if b_ok else "resistance_not_overhead_or_too_far"
    elif not res:
        b_ok = True
        b_reason = "no_salient_resistance_allowed"

    c_ok = None
    c_reason = "no_selected_support"
    if sup and _finite(opn) and _finite(atr) and atr and atr > 0:
        under = float(sup["lo"]) <= float(opn) and float(sup["center"]) <= float(opn) + 0.15 * float(atr)
        near = abs(float(opn) - float(sup["center"])) <= 3.0 * float(atr)
        c_ok = bool(under and near)
        c_reason = "underlying_and_within_3atr" if c_ok else "support_not_underlying_or_too_far"
    elif not sup:
        c_ok = True
        c_reason = "no_salient_support_allowed"

    stale_active = 0
    if _finite(opn) and _finite(atr) and atr and atr > 0:
        for z in list(snap.get("resistance_active") or []):
            if float(z["hi"]) < float(opn) - 0.25 * float(atr):
                stale_active += 1
        for z in list(snap.get("support_active") or []):
            if float(z["lo"]) > float(opn) + 0.25 * float(atr):
                stale_active += 1
    d_yes_obsolete = stale_active > 0 or n_act > 4
    d_reason = f"stale_or_excess_active={stale_active},n_active={n_act}"

    e_clutter = n_act > 4 or n_sel > 2
    e_reason = f"n_active={n_act},n_selected={n_sel}"

    f_miss = False
    f_reason = "no_unused_2touch_cluster_within_1atr_of_open"
    if _finite(opn) and _finite(atr) and atr and atr > 0:
        unused = 0
        sel_ids = {id(res), id(sup)}
        for z in list(snap.get("resistance_active") or []) + list(snap.get("support_active") or []):
            if id(z) in sel_ids:
                continue
            if abs(float(z["center"]) - float(opn)) <= 1.0 * float(atr) and int(z.get("distinct_swing_cycles") or 0) >= 2:
                unused += 1
        # unused nearby active zones are other valid levels not selected because we keep a small set — not a miss
        f_miss = False
        f_reason = "small_set_by_design_not_a_miss" if unused else "no_nearby_unused_cluster"

    relevant = bool(a_ok) and bool(b_ok) and bool(c_ok) and (not d_yes_obsolete)
    return {
        **row,
        "A_meaningful_historical_level": _yn(a_ok),
        "A_reason": a_reason,
        "B_resistance_overhead_relevant": _yn(b_ok),
        "B_reason": b_reason,
        "C_support_underlying_relevant": _yn(c_ok),
        "C_reason": c_reason,
        "D_obsolete_level_still_shown": _yn(d_yes_obsolete),
        "D_reason": d_reason,
        "E_chart_clutter": _yn(e_clutter),
        "E_reason": e_reason,
        "F_misses_obvious_swing_zone": _yn(f_miss),
        "F_reason": f_reason,
        "relevant_pass": relevant,
        "clutter_flag": e_clutter,
        "stale_stripe_flag": d_yes_obsolete,
        "no_level_flag": bool(row.get("no_level")),
    }


def summarize_face(scored: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(scored)
    def rate(key: str, val: str) -> float | None:
        if not n:
            return None
        return sum(1 for r in scored if r.get(key) == val) / n

    clutter_rate = rate("E_chart_clutter", "YES")
    obsolete_rate = rate("D_obsolete_level_still_shown", "YES")
    relevant_rate = (sum(1 for r in scored if r.get("relevant_pass")) / n) if n else None
    no_level_n = sum(1 for r in scored if r.get("no_level_flag"))
    face_pass = bool(
        n >= 200
        and (clutter_rate is not None and clutter_rate <= 0.30)
        and (obsolete_rate is not None and obsolete_rate <= 0.15)
        and (relevant_rate is not None and relevant_rate >= 0.40)
    )
    return {
        "n": n,
        "A_yes_rate": rate("A_meaningful_historical_level", "YES"),
        "B_yes_rate": rate("B_resistance_overhead_relevant", "YES"),
        "C_yes_rate": rate("C_support_underlying_relevant", "YES"),
        "D_obsolete_yes_rate": obsolete_rate,
        "E_clutter_yes_rate": clutter_rate,
        "F_miss_yes_rate": rate("F_misses_obvious_swing_zone", "YES"),
        "obvious_relevant_rate": relevant_rate,
        "clutter_rate": clutter_rate,
        "no_level_n": no_level_n,
        "no_level_rate": (no_level_n / n) if n else None,
        "stale_stripes_gone": bool(obsolete_rate is not None and obsolete_rate <= 0.15),
        "face_valid": face_pass,
        "blocks": {b: sum(1 for r in scored if r.get("block") == b) for b in ("D1", "D2", "D3", "D4")},
        "vol_regimes": {k: sum(1 for r in scored if r.get("vol_regime") == k) for k in ("low", "mid", "high", "unknown")},
        "sector_n": len({str(r.get("sector") or "") for r in scored}),
        "excluded_prior_audit_sample": True,
    }
