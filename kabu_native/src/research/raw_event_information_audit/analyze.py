"""Label separation, recoverability, family support, incremental gate. No feature search."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.canonical_entry_performance_rebase.analyze import _f
from research.entry_rank_shape_audit.oof import delta_series_stats
from research.raw_event_information_audit import (
    ALIASING_SIGN,
    DAILY_SIGN_MIN,
    ELIGIBLE_DAYS,
    FAMILIES,
    FAMILY_KEYS,
    LOST_RATE_MIN,
    MIN_DAY_NEG,
    MIN_DAY_POS,
    PARITY_ABS_TOL,
    RECOVERABILITY,
    RECOVERABILITY_REASON,
    S0_EXPECTED,
    S1_EXPECTED,
    STABILITY_SIGN,
)
from research.temporal_model_probe.analyze import control_parity as _control_parity


def control_parity(s0: dict[str, Any], s1: dict[str, Any]) -> dict[str, Any]:
    return _control_parity(s0, s1)


def _lost_rate(raw_n: float, grid_n: float) -> Optional[float]:
    if raw_n <= 0:
        return 0.0
    lost = max(float(raw_n) - float(grid_n), 0.0)
    return float(lost / float(raw_n))


def coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ns = [int(r.get("n_events") or 0) for r in rows]
    arr = np.asarray(ns, dtype=float)
    def _q(p: float) -> Optional[float]:
        if arr.size == 0:
            return None
        return float(np.quantile(arr, p))
    return {
        "RAW_EVENT_ROW_N": len(rows),
        "ZERO_EVENT_ROW_N": int(sum(1 for v in ns if v == 0)),
        "EVENTS_PER_ROW_MEAN": float(np.mean(arr)) if arr.size else None,
        "EVENTS_PER_ROW_MEDIAN": float(np.median(arr)) if arr.size else None,
        "EVENTS_PER_ROW_P10": _q(0.10),
        "EVENTS_PER_ROW_P90": _q(0.90),
        "EVENTS_PER_ROW_P99": _q(0.99),
        "RAW_IMBALANCE_FLIPS_N": int(sum(int(r.get("raw_imbalance_flips_n") or 0) for r in rows)),
        "GRID_VISIBLE_IMBALANCE_FLIPS_N": int(sum(int(r.get("grid_visible_imbalance_flips_n") or 0) for r in rows)),
        "RAW_SPREAD_TRANSITIONS_N": int(sum(int(r.get("raw_spread_transitions_n") or 0) for r in rows)),
        "GRID_VISIBLE_SPREAD_TRANSITIONS_N": int(sum(int(r.get("grid_visible_spread_transitions_n") or 0) for r in rows)),
        "RAW_DEPTH_DIRECTION_CHANGES_N": int(sum(int(r.get("raw_depth_direction_changes_n") or 0) for r in rows)),
        "GRID_VISIBLE_DEPTH_DIRECTION_CHANGES_N": int(sum(int(r.get("grid_visible_depth_direction_changes_n") or 0) for r in rows)),
    }


def _vals(rows: list[dict[str, Any]], key: str) -> list[float]:
    out = []
    for r in rows:
        v = _f(r.get(key))
        if v is None:
            continue
        out.append(float(v))
    return out


def _robust_es(a: list[float], b: list[float]) -> Optional[float]:
    if not a or not b:
        return None
    med = float(np.median(a) - np.median(b))
    pooled = np.concatenate([np.asarray(a, dtype=float), np.asarray(b, dtype=float)])
    mad = float(np.median(np.abs(pooled - np.median(pooled))))
    if mad <= 1e-18:
        return 0.0 if abs(med) <= 1e-18 else None
    return med / (1.4826 * mad)


def _cohens_d(a: list[float], b: list[float]) -> Optional[float]:
    if len(a) < 2 or len(b) < 2:
        return None
    xa = np.asarray(a, dtype=float)
    xb = np.asarray(b, dtype=float)
    va = float(xa.var(ddof=1))
    vb = float(xb.var(ddof=1))
    sp = np.sqrt(((len(a) - 1) * va + (len(b) - 1) * vb) / max(len(a) + len(b) - 2, 1))
    if sp <= 1e-18:
        return 0.0
    return float((xa.mean() - xb.mean()) / sp)


def descriptor_separation(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    pos = [r for r in rows if r.get("joint_label") == 1]
    neg = [r for r in rows if r.get("joint_label") == 0]
    xp = _vals(pos, key)
    xn = _vals(neg, key)
    med_diff = None
    if xp and xn:
        med_diff = float(np.median(xp) - np.median(xn))
    daily = []
    for d in ELIGIBLE_DAYS:
        dp = _vals([r for r in pos if str(r.get("date")) == d], key)
        dn = _vals([r for r in neg if str(r.get("date")) == d], key)
        if len(dp) < int(MIN_DAY_POS) or len(dn) < int(MIN_DAY_NEG):
            continue
        daily.append(float(np.median(dp) - np.median(dn)))
    st = delta_series_stats(daily)
    global_sign = 0
    if med_diff is not None and med_diff > 0:
        global_sign = 1
    elif med_diff is not None and med_diff < 0:
        global_sign = -1
    agree = 0
    if global_sign != 0:
        agree = sum(1 for v in daily if (v > 0) == (global_sign > 0) and v != 0)
    ex_best_ok = False
    ex_top3_ok = False
    if global_sign != 0:
        if st.get("ex_best_day") is not None:
            ex_best_ok = (float(st["ex_best_day"]) > 0) == (global_sign > 0)
        if st.get("ex_top3_days") is not None:
            ex_top3_ok = (float(st["ex_top3_days"]) > 0) == (global_sign > 0)
        if len(daily) <= 1:
            ex_best_ok = False
        if len(daily) <= 3:
            ex_top3_ok = False
    rec = str(RECOVERABILITY.get(key) or "NOT_RECOVERABLE")
    robust = bool(
        global_sign != 0
        and agree >= int(DAILY_SIGN_MIN)
        and ex_best_ok
        and ex_top3_ok
    )
    return {
        "key": key,
        "family": next((f for f, ks in FAMILY_KEYS.items() if key in ks), None),
        "recoverability": rec,
        "reason": RECOVERABILITY_REASON.get(key),
        "n_joint_pos": len(xp),
        "n_joint_neg": len(xn),
        "median_joint_pos": float(np.median(xp)) if xp else None,
        "median_joint_neg": float(np.median(xn)) if xn else None,
        "median_difference": med_diff,
        "standardized_effect_robust": _robust_es(xp, xn),
        "cohens_d": _cohens_d(xp, xn),
        "daily_n": len(daily),
        "positive_direction_days": st.get("positive_days"),
        "negative_direction_days": st.get("negative_days"),
        "agree_with_global_days": agree,
        "ex_best_day": st.get("ex_best_day"),
        "ex_top3_days": st.get("ex_top3_days"),
        "global_sign": global_sign,
        "robust": robust,
    }


def _story(sign: int, key: str) -> Optional[str]:
    if sign == 0:
        return None
    st = STABILITY_SIGN.get(key)
    al = ALIASING_SIGN.get(key)
    hits = []
    if st is not None and int(st) == int(sign):
        hits.append("STABILITY")
    if al is not None and int(al) == int(sign):
        hits.append("ALIASING")
    if hits == ["STABILITY"]:
        return "STABILITY"
    if hits == ["ALIASING"]:
        return "ALIASING"
    if len(hits) == 2:
        return "BOTH"
    return None


def family_decision(seps: list[dict[str, Any]], family: str) -> dict[str, Any]:
    keys = FAMILY_KEYS[family]
    members = [s for s in seps if s.get("key") in keys]
    robust = [s for s in members if s.get("robust")]
    weak = [
        s
        for s in members
        if (not s.get("robust"))
        and s.get("global_sign") not in (0, None)
        and int(s.get("agree_with_global_days") or 0) >= 9
    ]
    not_recov_robust = [s for s in robust if s.get("recoverability") == "NOT_RECOVERABLE"]
    if robust:
        status = "SUPPORTED"
    elif weak:
        status = "WEAK"
    else:
        status = "NOT_SUPPORTED"
    stories = [_story(int(s.get("global_sign") or 0), str(s.get("key"))) for s in robust]
    stories = [x for x in stories if x in {"STABILITY", "ALIASING"}]
    story = None
    if stories:
        n_st = stories.count("STABILITY")
        n_al = stories.count("ALIASING")
        if n_st > 0 and n_al == 0:
            story = "STABILITY"
        elif n_al > 0 and n_st == 0:
            story = "ALIASING"
        else:
            story = "MIXED"
    return {
        "family": family,
        "status": status,
        "robust_keys": [s.get("key") for s in robust],
        "not_recoverable_robust_keys": [s.get("key") for s in not_recov_robust],
        "economic_story": story,
        "members": members,
    }


def incremental_gate(
    *,
    families: dict[str, dict[str, Any]],
    lost: dict[str, Optional[float]],
) -> dict[str, Any]:
    not_recov_supported = [
        f for f, b in families.items() if b.get("status") == "SUPPORTED" and b.get("not_recoverable_robust_keys")
    ]
    a = bool(not_recov_supported)
    b = False
    c = False
    if not_recov_supported:
        # SUPPORTED already requires ex-best and ex-top3 on the robust descriptor
        b = True
        c = True
    lost_ok = any(float(v) >= float(LOST_RATE_MIN) for v in lost.values() if v is not None)
    d = bool(lost_ok)
    stories = [families[f].get("economic_story") for f in not_recov_supported]
    e = bool(stories) and all(s in {"STABILITY", "ALIASING"} for s in stories) and len(set(stories)) == 1
    e_story = stories[0] if e else None
    return {
        "A_NOT_RECOVERABLE_FAMILY_ROBUST": a,
        "B_EX_BEST_DIRECTION": b,
        "C_EX_TOP3_DIRECTION": c,
        "D_LOST_TRANSITION_RATE": d,
        "E_ECONOMIC_INTERPRETATION": e,
        "economic_story": e_story,
        "not_recoverable_supported_families": not_recov_supported,
        "RAW_EVENT_INCREMENTAL_INFORMATION": bool(a and b and c and d and e),
        "lost": lost,
    }


def decide(
    *,
    parity_ok: bool,
    integ_ok: bool,
    integ_note: str,
    families: dict[str, dict[str, Any]],
    gate: dict[str, Any],
) -> dict[str, Any]:
    if not parity_ok:
        return {
            "CASE": None,
            "VERDICT": "RAW_EVENT_AUDIT_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": "S0/S1 controls did not reproduce.",
            "note": "STOP. BASE_PARITY failed.",
        }
    if not integ_ok:
        return {
            "CASE": None,
            "VERDICT": "RAW_EVENT_AUDIT_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": f"Integrity fail: {integ_note}.",
            "note": "STOP. RAW_EVENT_AUDIT_INTEGRITY_FAILED.",
        }
    statuses = {f: families[f].get("status") for f in FAMILIES}
    not_recov_sup = list(gate.get("not_recoverable_supported_families") or [])
    not_recov_signed = False
    recov_signed = False
    for f in FAMILIES:
        for m in families[f].get("members") or []:
            if m.get("global_sign") in (0, None):
                continue
            if m.get("recoverability") == "NOT_RECOVERABLE":
                not_recov_signed = True
            else:
                recov_signed = True
    if gate.get("RAW_EVENT_INCREMENTAL_INFORMATION") and not_recov_sup:
        return {
            "CASE": "A",
            "VERDICT": "RAW_EVENT_INFORMATION_CONFIRMED",
            "NEXT_RESEARCH": "EVENT_LEVEL_MODEL_ARCHITECTURE_PRECOMMIT",
            "PRIMARY_FINDING": (
                "Raw board events in t0-180s carry joint-label information that the 5s grid cannot reconstruct. "
                f"Families={not_recov_sup} story={gate.get('economic_story')}."
            ),
            "note": "CASE A. STOP. Event-level model not started this run.",
        }
    if not not_recov_signed and not recov_signed:
        return {
            "CASE": "D",
            "VERDICT": "PREENTRY_OBSERVABLE_STATE_INSUFFICIENT",
            "NEXT_RESEARCH": "ENTRY_RESEARCH_ARCHITECTURE_REASSESSMENT",
            "PRIMARY_FINDING": "Raw 180s board events do not separate Direct Joint labels.",
            "note": "CASE D. STOP. Event-level model not started this run.",
        }
    if not_recov_signed and not not_recov_sup:
        return {
            "CASE": "B",
            "VERDICT": "RAW_EVENT_SIGNAL_NOT_ROBUST",
            "NEXT_RESEARCH": "ENTRY_RESEARCH_ARCHITECTURE_REASSESSMENT",
            "PRIMARY_FINDING": (
                "Raw-event NOT_RECOVERABLE descriptors differ by joint label, but day robustness / "
                f"lost-transition / economic gate fails."
            ),
            "note": "CASE B. STOP. Event-level model not started this run.",
        }
    if not_recov_sup and not gate.get("RAW_EVENT_INCREMENTAL_INFORMATION"):
        return {
            "CASE": "B",
            "VERDICT": "RAW_EVENT_SIGNAL_NOT_ROBUST",
            "NEXT_RESEARCH": "ENTRY_RESEARCH_ARCHITECTURE_REASSESSMENT",
            "PRIMARY_FINDING": "A NOT_RECOVERABLE family is robust, but lost-transition or economic interpretation fails.",
            "note": "CASE B. STOP. Event-level model not started this run.",
        }
    return {
        "CASE": "C",
        "VERDICT": "RAW_EVENT_ADDS_NO_DISTINCT_INFORMATION",
        "NEXT_RESEARCH": "ENTRY_RESEARCH_ARCHITECTURE_REASSESSMENT",
        "PRIMARY_FINDING": "Joint-label differences that exist are approximately recoverable from the 37x7 grid.",
        "note": "CASE C. STOP. Event-level model not started this run.",
    }
