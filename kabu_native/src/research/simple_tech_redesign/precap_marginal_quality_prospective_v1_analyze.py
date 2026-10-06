"""Prospective admitted vs CAP-only blocked economics. Matching is secondary only."""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

from research.simple_tech_redesign.precap_marginal_quality_prospective_v1_spec import (
    CONCENTRATION_WARN,
    FIRST_ELIGIBLE_DATE,
    MIN_BLOCKED_HYP_N,
    OUTLIER_SYMBOL,
)
from research.simple_tech_redesign.precap_marginal_quality_timing_confounding_rca_analyze import _clock_group, _match
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_analyze import (
    EPS,
    conc_share,
    quality_pack,
    weaker,
)
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_harvest import _f

T3_FIELDS = (
    "t3_ema9",
    "t3_ema21",
    "t3_ema21_lag3",
    "t3_ema21_slope_input",
    "t3_rci9",
    "t3_rci9_prev",
    "t3_bb_lower",
    "t3_close",
    "t3_ema_gap_bps",
    "t3_close_ema9_bps",
)


def _econ(rows: list[dict[str, Any]]) -> dict[str, Any]:
    xs = [float(r["session_close_pnl"]) for r in rows if _f(r.get("session_close_pnl")) is not None]
    pack = quality_pack(xs)
    gl = pack.get("gross_loss_per_fill")
    pack["gross_loss_per_fill"] = gl
    return pack


def _disadv_conc(rows: list[dict[str, Any]], *, key: str, pnl_key: str = "pnl") -> dict[str, Any]:
    pack = conc_share(rows, key=key, pnl_key=pnl_key)
    net = pack.get("share_of_net")
    ab = pack.get("share_of_abs")
    pack["warning_gt_50pct"] = bool(
        (net is not None and float(net) > float(CONCENTRATION_WARN))
        or (ab is not None and float(ab) > float(CONCENTRATION_WARN))
    )
    return pack


def _day_table(admitted: list[dict[str, Any]], blocked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_a: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_b: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in admitted:
        by_a[str(r.get("date") or "")].append(r)
    for r in blocked:
        by_b[str(r.get("date") or "")].append(r)
    days = sorted(set(by_a) | set(by_b))
    out = []
    for d in days:
        ae = _econ(by_a.get(d) or [])
        be = _econ(by_b.get(d) or [])
        am = ae.get("mean_pnl")
        bm = be.get("mean_pnl")
        out.append(
            {
                "date": d,
                "admitted_n": int(ae.get("n") or 0),
                "blocked_n": int(be.get("n") or 0),
                "admitted_mean": am,
                "blocked_mean": bm,
                "difference": (float(bm) - float(am)) if am is not None and bm is not None else None,
                "admitted_PF": ae.get("PF"),
                "blocked_PF": be.get("PF"),
                "admitted_win_rate": ae.get("win_rate"),
                "blocked_win_rate": be.get("win_rate"),
            }
        )
    return out


def _thirds(cands: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for name in ("FIRST", "MIDDLE", "LAST"):
        rows = [c for c in cands if c.get("arrival_third") == name]
        adm = [c for c in rows if c.get("control_admitted") and _f(c.get("session_close_pnl")) is not None]
        blk = [c for c in rows if c.get("hypothetical_fill") and _f(c.get("session_close_pnl")) is not None]
        out[name] = {
            "admitted_n": len(adm),
            "blocked_n": len(blk),
            "admitted": _econ(adm),
            "blocked": _econ(blk),
            "all_with_pnl": _econ(adm + blk),
        }
    return out


def _t3_inventory(admitted: list[dict[str, Any]], blocked: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in T3_FIELDS:
        av = [float(x) for r in admitted if (x := _f(r.get(field))) is not None]
        bv = [float(x) for r in blocked if (x := _f(r.get(field))) is not None]
        out[field] = {
            "admitted": {"n": len(av), "median": float(median(av)) if av else None, "mean": (sum(av) / len(av)) if av else None},
            "blocked": {"n": len(bv), "median": float(median(bv)) if bv else None, "mean": (sum(bv) / len(bv)) if bv else None},
        }
    out["missing_notes"] = {
        "p2_setup_low": "MISSING_TF1_NOT_RETAINED",
        "p2_setup_high": "MISSING_TF1_NOT_RETAINED",
        "p2_exact_3_bars_beyond_signal": "MISSING_TF1_NOT_RETAINED",
    }
    return out


def evaluate(bodies: list[dict[str, Any]]) -> dict[str, Any]:
    cands: list[dict[str, Any]] = []
    identity_ok = True
    blockers = []
    for body in bodies:
        if not body.get("ok"):
            identity_ok = False
            blockers.append(body.get("blocker"))
            continue
        if not body.get("control_sot_ok") or not body.get("leftover_ok"):
            identity_ok = False
            blockers.append(f"sot:{body.get('date')}")
        cands.extend(list(body.get("candidates") or []))
    admitted = [c for c in cands if c.get("control_admitted") and _f(c.get("session_close_pnl")) is not None]
    blocked = [c for c in cands if c.get("hypothetical_fill") and _f(c.get("session_close_pnl")) is not None]
    cap_only = [c for c in cands if c.get("cap_only_blocked")]
    raw_adm = _econ(admitted)
    raw_blk = _econ(blocked)
    days = _day_table(admitted, blocked)
    roles = {}
    for role in ("CORE", "ADDED"):
        roles[role] = {
            "admitted": _econ([r for r in admitted if str(r.get("fill_role") or "") == role]),
            "blocked": _econ([r for r in blocked if str(r.get("fill_role") or "") == role]),
        }
        roles[role]["comparison"] = weaker(roles[role]["blocked"], roles[role]["admitted"])
    occ = {
        "EARLY_AND_ADMITTED": _econ([c for c in cands if c.get("first5") and c.get("control_admitted") and _f(c.get("session_close_pnl")) is not None]),
        "EARLY_NOT_ADMITTED": {
            **_econ([c for c in cands if c.get("first5") and (not c.get("control_admitted")) and _f(c.get("session_close_pnl")) is not None]),
            "group_n": sum(1 for c in cands if c.get("first5") and not c.get("control_admitted")),
        },
        "LATE_BUT_ADMITTED": _econ([c for c in cands if (not c.get("first5")) and c.get("control_admitted") and _f(c.get("session_close_pnl")) is not None]),
        "LATE_AND_BLOCKED": _econ([c for c in cands if (not c.get("first5")) and c.get("hypothetical_fill") and _f(c.get("session_close_pnl")) is not None]),
    }
    occ["EARLY_AND_ADMITTED"]["group_n"] = sum(1 for c in cands if c.get("first5") and c.get("control_admitted"))
    occ["LATE_BUT_ADMITTED"]["group_n"] = sum(1 for c in cands if (not c.get("first5")) and c.get("control_admitted"))
    occ["LATE_AND_BLOCKED"]["group_n"] = sum(1 for c in cands if (not c.get("first5")) and c.get("hypothetical_fill"))
    raw_day_contrib = []
    for d in days:
        if d.get("difference") is None:
            continue
        raw_day_contrib.append({"date": d["date"], "pnl": float(d["difference"]) * int(d["blocked_n"] or 0)})
    secondary = _match(blocked, admitted, require_role=True) if blocked and admitted else {
        "require_role": True,
        "matched_blocked_n": 0,
        "unmatched_n": len(blocked),
        "median_timestamp_gap_sec": None,
        "mean_paired_difference": None,
        "comparison": weaker({"n": 0}, {"n": 0}),
    }
    matched_day: dict[str, float] = defaultdict(float)
    for p in list(secondary.get("pairs") or []):
        if p.get("paired_difference") is None:
            continue
        matched_day[str(p.get("date") or "")] += float(p["paired_difference"])
    outlier = [c for c in blocked if str(c.get("symbol") or "") == OUTLIER_SYMBOL]
    out_pnl = float(sum(float(c["session_close_pnl"]) for c in outlier))
    blk_net = float(raw_blk.get("total_pnl") or 0.0)
    abs_den = float(sum(abs(float(c["session_close_pnl"])) for c in blocked)) if blocked else 0.0
    out_share_net = (abs(out_pnl) / abs(blk_net)) if abs(blk_net) > EPS else None
    out_share_abs = (abs(out_pnl) / abs_den) if abs_den > EPS else None
    return {
        "identity_ok": identity_ok,
        "blockers": blockers,
        "completed_days": sorted({str(c.get("date") or "") for c in cands}),
        "admitted": admitted,
        "blocked": blocked,
        "raw": {"admitted": raw_adm, "blocked": raw_blk, "comparison": weaker(raw_blk, raw_adm)},
        "day_table": days,
        "roles": roles,
        "arrival": {
            "admitted": _clock_group(admitted),
            "blocked_hyp": _clock_group(blocked),
            "cap_only_blocked": _clock_group(cap_only),
        },
        "thirds": _thirds(cands),
        "occupancy_cells": occ,
        "t3_inventory": _t3_inventory(admitted, blocked),
        "match_secondary_same_day_same_role": {k: v for k, v in secondary.items() if k != "pairs"},
        "day_concentration": _disadv_conc(raw_day_contrib, key="date"),
        "symbol_concentration": _disadv_conc(blocked, key="symbol", pnl_key="session_close_pnl"),
        "outlier_285A": {
            "n": len(outlier),
            "total_pnl": out_pnl if outlier else 0.0,
            "share_of_net": out_share_net,
            "share_of_abs": out_share_abs,
            "warning_gt_50pct": bool(
                (out_share_net is not None and float(out_share_net) > float(CONCENTRATION_WARN))
                or (out_share_abs is not None and float(out_share_abs) > float(CONCENTRATION_WARN))
            ),
            "excluded": False,
        },
        "candidates": cands,
        "admitted_n": int(raw_adm.get("n") or 0),
        "blocked_n": int(raw_blk.get("n") or 0),
        "cap_only_n": len(cap_only),
    }


def decide(pack: dict[str, Any], *, leak_ok: bool, discovery: dict[str, Any]) -> dict[str, Any]:
    integrity = bool(leak_ok and pack.get("identity_ok"))
    blocked_n = int(pack.get("blocked_n") or 0)
    raw = dict(pack.get("raw") or {})
    cmp = dict(raw.get("comparison") or {})
    clearly = bool(cmp.get("clearly_weaker"))
    mean_worse = bool(cmp.get("mean_worse"))
    days = [d for d in list(pack.get("day_table") or []) if d.get("difference") is not None]
    worse_days = [d for d in days if float(d["difference"]) < -EPS]
    better_days = [d for d in days if float(d["difference"]) > EPS]
    multi_day = len(worse_days) >= 2
    mixed = len(days) >= 3 and len(worse_days) >= 2 and len(better_days) >= 2
    day_warn = bool((pack.get("day_concentration") or {}).get("warning_gt_50pct"))
    sym_warn = bool((pack.get("symbol_concentration") or {}).get("warning_gt_50pct"))
    concentrated = bool(day_warn or sym_warn)
    late_blk = _f(((pack.get("occupancy_cells") or {}).get("LATE_AND_BLOCKED") or {}).get("mean_pnl"))
    late_adm = _f(((pack.get("occupancy_cells") or {}).get("LATE_BUT_ADMITTED") or {}).get("mean_pnl"))
    last_all = _f((((pack.get("thirds") or {}).get("LAST") or {}).get("all_with_pnl") or {}).get("mean_pnl"))
    first_all = _f((((pack.get("thirds") or {}).get("FIRST") or {}).get("all_with_pnl") or {}).get("mean_pnl"))
    late_only = False
    if late_blk is not None and late_adm is not None and abs(float(late_adm) - float(late_blk)) <= abs(float(late_blk)) * 0.5:
        late_only = True
    if first_all is not None and last_all is not None and float(last_all) < float(first_all) - EPS and not mean_worse:
        late_only = True
    roles = dict(pack.get("roles") or {})
    core_w = bool(((roles.get("CORE") or {}).get("comparison") or {}).get("mean_worse"))
    added_w = bool(((roles.get("ADDED") or {}).get("comparison") or {}).get("mean_worse"))
    role_only = bool(core_w) != bool(added_w) and (core_w or added_w)
    outl = dict(pack.get("outlier_285A") or {})
    outlier_reproduced = bool(int(outl.get("n") or 0) > 0 and outl.get("warning_gt_50pct"))
    if not integrity:
        case, verdict, nxt = (
            "E",
            "SIMPLE_TECH_PRECAP_MARGINAL_QUALITY_PROSPECTIVE_INSUFFICIENT",
            "Integrity failed. STOP. Same spec. Do not create a filter.",
        )
    elif blocked_n < int(MIN_BLOCKED_HYP_N):
        case, verdict, nxt = (
            "E",
            "SIMPLE_TECH_PRECAP_MARGINAL_QUALITY_PROSPECTIVE_INSUFFICIENT",
            "Same spec. Continue after the next sealed session. Do not change parameters.",
        )
    elif clearly and concentrated:
        case, verdict, nxt = (
            "B",
            "SIMPLE_TECH_PRECAP_MARGINAL_QUALITY_PROSPECTIVE_OUTLIER_ONLY",
            "No new ENTRY filter. Accumulate more future evidence.",
        )
    elif mixed:
        case, verdict, nxt = (
            "D",
            "SIMPLE_TECH_PRECAP_MARGINAL_QUALITY_PROSPECTIVE_MIXED",
            "Treat as regime-dependent. No new ENTRY filter. No new EXIT.",
        )
    elif clearly and multi_day and not concentrated:
        case, verdict, nxt = (
            "A",
            "SIMPLE_TECH_PRECAP_MARGINAL_QUALITY_PROSPECTIVE_REPLICATED",
            "Next: intrinsic ENTRY quality mechanism discovery only. Do not create a filter in that run until specified.",
        )
    else:
        case, verdict, nxt = (
            "C",
            "SIMPLE_TECH_PRECAP_MARGINAL_QUALITY_NOT_REPLICATED",
            "Do not treat MARGINAL_ENTRY_QUALITY as a robust bottleneck. Close the Pre-CAP ENTRY QUALITY path. No new filter.",
        )
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "OBSERVED_ECONOMIC_BOTTLENECK": "MARGINAL_ENTRY_QUALITY",
        "INTRINSIC_MECHANISM_CONFIRMED": False,
        "PRIMARY_MECHANISM_FROZEN": "OUTLIER_DOMINATED",
        "PROSPECTIVE_OBSERVATION_PROTOCOL_FROZEN": True,
        "CANDIDATE_FROZEN": False,
        "FAMILY_CLOSED": True,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "NEW_ENTRY_FILTER": False,
        "CAP_CHANGED": False,
        "NEW_EXIT_RULE": False,
        "TIME_FILTER": False,
        "first_eligible_prospective_date": FIRST_ELIGIBLE_DATE,
        "prospective_armed": True,
        "MATCHING_USED_IN_PRIMARY_VERDICT": False,
        "questions": {
            "Q1_future_blocked_weaker": bool(clearly or mean_worse) if blocked_n >= int(MIN_BLOCKED_HYP_N) else False,
            "Q2_weakness_on_multiple_days": bool(multi_day),
            "Q3_single_day_explains": bool(day_warn),
            "Q4_single_symbol_explains": bool(sym_warn),
            "Q5_one_role_only": bool(role_only),
            "Q6_late_arrival_only": bool(late_only),
            "Q7_285A_type_outlier_reproduced": bool(outlier_reproduced),
        },
        "gates": {
            "integrity": integrity,
            "blocked_n": blocked_n,
            "min_blocked_n": int(MIN_BLOCKED_HYP_N),
            "clearly_weaker": clearly,
            "mean_worse": mean_worse,
            "multi_day": multi_day,
            "mixed": mixed,
            "concentrated": concentrated,
            "worse_days": [d["date"] for d in worse_days],
            "better_days": [d["date"] for d in better_days],
            "completed_days": list(discovery.get("completed_days") or pack.get("completed_days") or []),
        },
    }
