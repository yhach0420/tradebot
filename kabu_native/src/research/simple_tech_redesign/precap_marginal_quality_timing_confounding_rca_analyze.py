"""Time-adjusted comparison of CAP-only blocked vs Control admitted. No filter search."""
from __future__ import annotations

from collections import defaultdict
from math import sqrt
from statistics import median
from typing import Any, Optional

from research.simple_tech_redesign.entry_anchored_floor_break_candidate_analyze import identity_check
from research.simple_tech_redesign.entry_anchored_floor_break_candidate_spec import (
    DEV_ADDED_N,
    DEV_CORE_N,
    DEV_FILL_N,
    DEV_PNL,
    FWD_ADDED_N,
    FWD_CORE_N,
    FWD_FILL_N,
    FWD_PNL,
    YEN_PARITY_TOL,
)
from research.simple_tech_redesign.precap_marginal_quality_timing_confounding_rca_spec import (
    CLOCK_TOL_SEC,
    DEV_ADMITTED_FROM_OPEN_MEDIAN,
    DEV_BLOCKED_FROM_OPEN_MEDIAN,
    DEV_CAP_ONLY_N,
    DEV_HYP_N,
    EARLIEST_POSSIBLE_IF_CASE_A,
    FWD_ADMITTED_FROM_OPEN_MEDIAN,
    FWD_BLOCKED_FROM_OPEN_MEDIAN,
    FWD_CAP_ONLY_N,
    FWD_HYP_N,
    FWD_OUTLIER_DAY,
    FWD_OUTLIER_SYMBOL,
    T3_INVENTORY_FIELDS,
)
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_analyze import (
    EPS,
    clock_pack,
    conc_share,
    quality_pack,
    weaker,
)
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_harvest import _f, _tid

T3_NUM = T3_INVENTORY_FIELDS + (
    "p2_bar0_low",
    "p2_bar0_close",
    "p2_bar1_low",
    "p2_bar1_close",
    "p2_bar2_low",
    "p2_bar2_close",
    "mfe_bps",
    "mae_bps",
    "session_close_pnl_bps",
    "seconds_to_session_close",
    "candidate_arrival_rank",
    "active_positions",
    "free_slots",
)


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def spearman(xs: list[float], ys: list[float]) -> Optional[float]:
    pairs = [(float(a), float(b)) for a, b in zip(xs, ys) if _f(a) is not None and _f(b) is not None]
    if len(pairs) < 5:
        return None
    ax = [p[0] for p in pairs]
    ay = [p[1] for p in pairs]
    rx, ry = _ranks(ax), _ranks(ay)
    n = len(rx)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    denx = sqrt(sum((a - mx) ** 2 for a in rx))
    deny = sqrt(sum((b - my) ** 2 for b in ry))
    if denx <= EPS or deny <= EPS:
        return None
    return float(num / (denx * deny))


def _disadv_conc(rows: list[dict[str, Any]], *, key: str, pnl_key: str = "pnl") -> dict[str, Any]:
    pack = conc_share(rows, key=key, pnl_key=pnl_key)
    net = pack.get("share_of_net")
    ab = pack.get("share_of_abs")
    pack["warning_gt_50pct"] = bool(
        (net is not None and float(net) > 0.5) or (ab is not None and float(ab) > 0.5)
    )
    return pack


def _econ(rows: list[dict[str, Any]]) -> dict[str, Any]:
    xs = [float(r["session_close_pnl"]) for r in rows if _f(r.get("session_close_pnl")) is not None]
    pack = quality_pack(xs)
    bps = [_f(r.get("session_close_pnl_bps")) for r in rows]
    bps_ok = [float(x) for x in bps if x is not None]
    mfes = [float(x) for x in (_f(r.get("mfe_bps")) for r in rows) if x is not None]
    maes = [float(x) for x in (_f(r.get("mae_bps")) for r in rows) if x is not None]
    pack["session_close_pnl_bps_median"] = float(median(bps_ok)) if bps_ok else None
    pack["mfe_bps_median"] = float(median(mfes)) if mfes else None
    pack["mae_bps_median"] = float(median(maes)) if maes else None
    pack["mfe_n"] = len(mfes)
    return pack


def _clock_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pack = clock_pack(rows)
    ranks = [float(r["candidate_arrival_rank"]) for r in rows if _f(r.get("candidate_arrival_rank")) is not None]
    pack["arrival_rank_median"] = float(median(ranks)) if ranks else None
    if len(ranks) >= 2:
        s = sorted(ranks)
        n = len(s)
        pack["arrival_rank_iqr"] = float(s[(3 * n) // 4] - s[n // 4])
    else:
        pack["arrival_rank_iqr"] = None
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
                "blocked_hyp_n": int(be.get("n") or 0),
                "admitted_mean_pnl": am,
                "blocked_mean_pnl": bm,
                "admitted_PF": ae.get("PF"),
                "blocked_PF": be.get("PF"),
                "difference_per_fill": (float(bm) - float(am)) if am is not None and bm is not None else None,
            }
        )
    return out


def _match(
    blocked: list[dict[str, Any]],
    admitted: list[dict[str, Any]],
    *,
    require_role: bool,
) -> dict[str, Any]:
    adm_by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    adm_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in admitted:
        day = str(a.get("date") or "")
        role = str(a.get("fill_role") or "")
        adm_by[(day, role)].append(a)
        adm_day[day].append(a)
    pairs = []
    unmatched = 0
    for b in blocked:
        day = str(b.get("date") or "")
        role = str(b.get("fill_role") or "")
        pool = list(adm_by.get((day, role)) or []) if require_role else list(adm_day.get(day) or [])
        if not pool:
            unmatched += 1
            continue
        bt = float(_f(b.get("t0")) or 0.0)
        hit = min(pool, key=lambda a: abs(float(_f(a.get("t0")) or 0.0) - bt))
        gap = abs(float(_f(hit.get("t0")) or 0.0) - bt)
        bp = _f(b.get("session_close_pnl"))
        ap = _f(hit.get("session_close_pnl"))
        pairs.append(
            {
                "blocked_id": b.get("trade_id"),
                "matched_id": hit.get("trade_id"),
                "date": day,
                "blocked_role": role,
                "matched_role": hit.get("fill_role"),
                "timestamp_gap_sec": gap,
                "blocked_pnl": bp,
                "matched_pnl": ap,
                "paired_difference": (float(bp) - float(ap)) if bp is not None and ap is not None else None,
                "blocked": b,
                "matched": hit,
            }
        )
    diffs = [float(p["paired_difference"]) for p in pairs if p.get("paired_difference") is not None]
    gaps = [float(p["timestamp_gap_sec"]) for p in pairs]
    b_pnls = [float(p["blocked_pnl"]) for p in pairs if p.get("blocked_pnl") is not None]
    a_pnls = [float(p["matched_pnl"]) for p in pairs if p.get("matched_pnl") is not None]
    return {
        "require_role": require_role,
        "matched_blocked_n": len(pairs),
        "unmatched_n": unmatched,
        "median_timestamp_gap_sec": float(median(gaps)) if gaps else None,
        "blocked": quality_pack(b_pnls),
        "matched_admitted": quality_pack(a_pnls),
        "paired_difference": quality_pack(diffs) if diffs else quality_pack([]),
        "mean_paired_difference": (sum(diffs) / len(diffs)) if diffs else None,
        "comparison": weaker(quality_pack(b_pnls), quality_pack(a_pnls)),
        "pairs": pairs,
    }


def _field_dist(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    xs = [float(v) for r in rows if (v := _f(r.get(field))) is not None]
    if not xs:
        return {"n": 0, "median": None, "mean": None}
    return {"n": len(xs), "median": float(median(xs)), "mean": float(sum(xs) / len(xs))}


def _t3_inventory(admitted: list[dict[str, Any]], blocked: list[dict[str, Any]], matched: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for field in T3_NUM:
        adm = _field_dist(admitted, field)
        blk = _field_dist(blocked, field)
        diffs = []
        for p in list(matched.get("pairs") or []):
            bv = _f((p.get("blocked") or {}).get(field))
            av = _f((p.get("matched") or {}).get(field))
            if bv is None or av is None:
                continue
            diffs.append(float(bv) - float(av))
        out[field] = {
            "admitted": adm,
            "blocked": blk,
            "matched_paired_diff_median": float(median(diffs)) if diffs else None,
            "matched_paired_n": len(diffs),
            "blocked_minus_admitted_median": (
                float(blk["median"]) - float(adm["median"]) if adm.get("median") is not None and blk.get("median") is not None else None
            ),
        }
    out["missing_not_computed"] = {
        "t3_rci9_prev": "MISSING_NOT_IN_FROZEN_CACHE",
        "t3_ema21_slope_input": "MISSING_NOT_IN_FROZEN_CACHE",
    }
    return out


def _cell(rows: list[dict[str, Any]], *, early: Optional[bool], admitted: Optional[bool], blocked_hyp: Optional[bool]) -> dict[str, Any]:
    xs = []
    for r in rows:
        if early is True and not r.get("first5"):
            continue
        if early is False and r.get("first5"):
            continue
        if admitted is True and not r.get("control_admitted"):
            continue
        if admitted is False and r.get("control_admitted"):
            continue
        if blocked_hyp is True and not r.get("hypothetical_fill"):
            continue
        if blocked_hyp is False and r.get("hypothetical_fill"):
            continue
        xs.append(r)
    econ_rows = [r for r in xs if _f(r.get("session_close_pnl")) is not None]
    pack = _econ(econ_rows)
    pack["group_n"] = len(xs)
    return pack


def _is_outlier(r: dict[str, Any]) -> bool:
    return str(r.get("date") or "") == FWD_OUTLIER_DAY and str(r.get("symbol") or "") == FWD_OUTLIER_SYMBOL


def evaluate_cohort(bodies: list[dict[str, Any]], *, cohort: str, enforce_identity: bool = True) -> dict[str, Any]:
    cands: list[dict[str, Any]] = []
    ctrl_tr: list[dict[str, Any]] = []
    leftover_ok = True
    sot_ok = True
    cap_only_n = 0
    multi_n = 0
    for body in bodies:
        leftover_ok = leftover_ok and bool(body.get("leftover_ok"))
        sot_ok = sot_ok and bool(body.get("control_sot_ok"))
        cands.extend(list(body.get("candidates") or []))
        ctrl_tr.extend(list((body.get("control") or {}).get("trades") or []))
        inv = dict(body.get("cap_inventory") or {})
        cap_only_n += len(list(inv.get("cap_only") or []))
        multi_n += len(list(inv.get("multi_reason") or []))
    if cohort == "DEVELOPMENT":
        ident = identity_check(ctrl_tr, fill_n=DEV_FILL_N, core_n=DEV_CORE_N, added_n=DEV_ADDED_N, pnl=DEV_PNL)
        exp_hyp, exp_cap, exp_adm_med, exp_blk_med = DEV_HYP_N, DEV_CAP_ONLY_N, DEV_ADMITTED_FROM_OPEN_MEDIAN, DEV_BLOCKED_FROM_OPEN_MEDIAN
    else:
        ident = identity_check(ctrl_tr, fill_n=FWD_FILL_N, core_n=FWD_CORE_N, added_n=FWD_ADDED_N, pnl=FWD_PNL)
        exp_hyp, exp_cap, exp_adm_med, exp_blk_med = FWD_HYP_N, FWD_CAP_ONLY_N, FWD_ADMITTED_FROM_OPEN_MEDIAN, FWD_BLOCKED_FROM_OPEN_MEDIAN
    admitted = [c for c in cands if bool(c.get("control_admitted"))]
    blocked = [c for c in cands if bool(c.get("hypothetical_fill"))]
    cap_only_rows = [c for c in cands if bool(c.get("cap_only_blocked"))]
    adm_clock = _clock_group(admitted)
    blk_clock = _clock_group(blocked)
    cap_clock = _clock_group(cap_only_rows)
    clock_ok = (
        adm_clock.get("from_open_median") is not None
        and cap_clock.get("from_open_median") is not None
        and abs(float(adm_clock["from_open_median"]) - float(exp_adm_med)) <= CLOCK_TOL_SEC
        and abs(float(cap_clock["from_open_median"]) - float(exp_blk_med)) <= CLOCK_TOL_SEC
    )
    ident["cap_only_blocked_n"] = cap_only_n
    ident["hypothetical_fill_n"] = len(blocked)
    ident["expected_cap_only_n"] = exp_cap
    ident["expected_hyp_n"] = exp_hyp
    ident["clock_identity_ok"] = bool(clock_ok)
    ident["leftover_ok"] = leftover_ok
    ident["control_sot_ok"] = sot_ok
    ident["ok"] = bool(
        ident.get("ok")
        and leftover_ok
        and sot_ok
        and (
            not enforce_identity
            or (
                cap_only_n == int(exp_cap)
                and len(blocked) == int(exp_hyp)
                and clock_ok
            )
        )
    )
    raw_adm = _econ(admitted)
    raw_blk = _econ(blocked)
    primary = _match(blocked, admitted, require_role=True)
    secondary = _match(blocked, admitted, require_role=False)
    thirds = {}
    for name in ("FIRST", "MIDDLE", "LAST"):
        rows = [c for c in cands if c.get("arrival_third") == name]
        thirds[name] = {
            "admitted_n": sum(1 for r in rows if r.get("control_admitted")),
            "blocked_hyp_n": sum(1 for r in rows if r.get("hypothetical_fill")),
            "blocked": _econ([r for r in rows if r.get("hypothetical_fill")]),
            "admitted": _econ([r for r in rows if r.get("control_admitted")]),
            "all_with_pnl": _econ([r for r in rows if _f(r.get("session_close_pnl")) is not None]),
        }
    first5 = [c for c in cands if c.get("first5")]
    later = [c for c in cands if not c.get("first5")]
    occupancy_cells = {
        "EARLY_AND_ADMITTED": _cell(cands, early=True, admitted=True, blocked_hyp=None),
        "EARLY_NOT_ADMITTED": _cell(cands, early=True, admitted=False, blocked_hyp=None),
        "LATE_BUT_ADMITTED": _cell(cands, early=False, admitted=True, blocked_hyp=None),
        "LATE_AND_BLOCKED": _cell(cands, early=False, admitted=None, blocked_hyp=True),
    }
    horizon = {
        "admitted_spearman_seconds_to_close_vs_pnl": spearman(
            [float(r.get("seconds_to_session_close") or 0.0) for r in admitted],
            [float(r.get("session_close_pnl") or 0.0) for r in admitted],
        ),
        "blocked_spearman_seconds_to_close_vs_pnl": spearman(
            [float(r.get("seconds_to_session_close") or 0.0) for r in blocked],
            [float(r.get("session_close_pnl") or 0.0) for r in blocked],
        ),
    }
    roles = {}
    for role in ("CORE", "ADDED"):
        roles[role] = {
            "admitted": _econ([r for r in admitted if str(r.get("fill_role") or "") == role]),
            "blocked": _econ([r for r in blocked if str(r.get("fill_role") or "") == role]),
        }
        roles[role]["comparison"] = weaker(roles[role]["blocked"], roles[role]["admitted"])
    t3 = _t3_inventory(admitted, blocked, primary)
    day_rows = _day_table(admitted, blocked)
    raw_day_contrib = []
    for d in day_rows:
        if d.get("difference_per_fill") is None:
            continue
        raw_day_contrib.append({"date": d["date"], "pnl": float(d["difference_per_fill"]) * int(d["blocked_hyp_n"] or 0)})
    matched_day: dict[str, float] = defaultdict(float)
    for p in list(primary.get("pairs") or []):
        if p.get("paired_difference") is None:
            continue
        matched_day[str(p.get("date") or "")] += float(p["paired_difference"])
    matched_day_rows = [{"date": k, "pnl": v} for k, v in matched_day.items()]
    raw_sym = _disadv_conc(blocked, key="symbol", pnl_key="session_close_pnl")
    by_sym: dict[str, float] = defaultdict(float)
    for p in list(primary.get("pairs") or []):
        if p.get("paired_difference") is None:
            continue
        by_sym[str((p.get("blocked") or {}).get("symbol") or "")] += float(p["paired_difference"])
    matched_sym_rows = [{"symbol": k, "pnl": v} for k, v in by_sym.items()]
    return {
        "cohort": cohort,
        "identity": ident,
        "cap_only_blocked_n": cap_only_n,
        "multi_reason_n": multi_n,
        "hypothetical_fill_n": len(blocked),
        "admitted_n": len(admitted),
        "raw": {"admitted": raw_adm, "blocked": raw_blk, "comparison": weaker(raw_blk, raw_adm)},
        "arrival": {"admitted": adm_clock, "blocked_hyp": blk_clock, "cap_only_blocked": cap_clock},
        "day_table": day_rows,
        "match_primary_same_day_same_role": {k: v for k, v in primary.items() if k != "pairs"},
        "match_primary_pairs_n": len(list(primary.get("pairs") or [])),
        "match_secondary_same_day": {k: v for k, v in secondary.items() if k != "pairs"},
        "thirds": thirds,
        "first5": _econ([r for r in first5 if _f(r.get("session_close_pnl")) is not None]),
        "later": _econ([r for r in later if _f(r.get("session_close_pnl")) is not None]),
        "first5_admitted": _econ([r for r in first5 if r.get("control_admitted")]),
        "later_admitted": _econ([r for r in later if r.get("control_admitted")]),
        "occupancy_cells": occupancy_cells,
        "horizon": horizon,
        "roles": roles,
        "t3_inventory": t3,
        "v22_join_n": sum(1 for c in cands if c.get("v22_joined")),
        "day_concentration": {
            "raw_blocked_disadvantage": _disadv_conc(raw_day_contrib, key="date"),
            "time_matched_blocked_disadvantage": _disadv_conc(matched_day_rows, key="date"),
        },
        "symbol_concentration": {
            "raw_blocked": raw_sym,
            "time_matched": _disadv_conc(matched_sym_rows, key="symbol"),
        },
        "candidates": cands,
        "_match_primary": primary,
    }


def _shrink(raw_cmp: dict[str, Any], matched: dict[str, Any]) -> dict[str, Any]:
    raw_gap = None
    ra = (raw_cmp.get("admitted") or {}).get("mean_pnl")
    rb = (raw_cmp.get("blocked") or {}).get("mean_pnl")
    if ra is not None and rb is not None:
        raw_gap = float(rb) - float(ra)
    matched_gap = matched.get("mean_paired_difference")
    explained = None
    if raw_gap is not None and matched_gap is not None and abs(raw_gap) > EPS:
        explained = 1.0 - (float(matched_gap) / float(raw_gap))
    return {"raw_mean_gap": raw_gap, "matched_mean_paired_diff": matched_gap, "fraction_explained_by_timing": explained}


def decide(dev: dict[str, Any], fwd: dict[str, Any], fwd_ex: dict[str, Any], *, leak_ok: bool) -> dict[str, Any]:
    ident_ok = bool(dev.get("identity", {}).get("ok")) and bool(fwd.get("identity", {}).get("ok"))
    integrity = bool(leak_ok and ident_ok)
    d_match_n = int((dev.get("match_primary_same_day_same_role") or {}).get("matched_blocked_n") or 0)
    f_match_n = int((fwd.get("match_primary_same_day_same_role") or {}).get("matched_blocked_n") or 0)
    insufficient = d_match_n < 20 or f_match_n < 5
    d_raw = bool(((dev.get("raw") or {}).get("comparison") or {}).get("clearly_weaker"))
    f_raw = bool(((fwd.get("raw") or {}).get("comparison") or {}).get("clearly_weaker"))
    d_mean = bool(((dev.get("raw") or {}).get("comparison") or {}).get("mean_worse"))
    f_mean = bool(((fwd.get("raw") or {}).get("comparison") or {}).get("mean_worse"))
    d_m = bool(((dev.get("match_primary_same_day_same_role") or {}).get("comparison") or {}).get("clearly_weaker"))
    f_m = bool(((fwd.get("match_primary_same_day_same_role") or {}).get("comparison") or {}).get("clearly_weaker"))
    d_m_mean = bool(((dev.get("match_primary_same_day_same_role") or {}).get("comparison") or {}).get("mean_worse"))
    f_m_mean = bool(((fwd.get("match_primary_same_day_same_role") or {}).get("comparison") or {}).get("mean_worse"))
    d_sh = _shrink(dev.get("raw") or {}, dev.get("match_primary_same_day_same_role") or {})
    f_sh = _shrink(fwd.get("raw") or {}, fwd.get("match_primary_same_day_same_role") or {})
    timing_explains_dev = (d_sh.get("fraction_explained_by_timing") or 0.0) >= 0.5 or not d_m
    timing_explains_fwd = (f_sh.get("fraction_explained_by_timing") or 0.0) >= 0.5 or not f_m
    last_dev = ((dev.get("thirds") or {}).get("LAST") or {}).get("all_with_pnl") or {}
    first_dev = ((dev.get("thirds") or {}).get("FIRST") or {}).get("all_with_pnl") or {}
    late_weaker_dev = (
        _f(last_dev.get("mean_pnl")) is not None
        and _f(first_dev.get("mean_pnl")) is not None
        and float(last_dev["mean_pnl"]) < float(first_dev["mean_pnl"]) - EPS
    )
    last_fwd = ((fwd.get("thirds") or {}).get("LAST") or {}).get("all_with_pnl") or {}
    first_fwd = ((fwd.get("thirds") or {}).get("FIRST") or {}).get("all_with_pnl") or {}
    late_weaker_fwd = (
        _f(last_fwd.get("mean_pnl")) is not None
        and _f(first_fwd.get("mean_pnl")) is not None
        and float(last_fwd["mean_pnl"]) < float(first_fwd["mean_pnl"]) - EPS
    )
    late_weaker = bool(late_weaker_dev and late_weaker_fwd)
    first5_mean = _f((dev.get("first5") or {}).get("mean_pnl"))
    later_mean = _f((dev.get("later") or {}).get("mean_pnl"))
    first5_better = first5_mean is not None and later_mean is not None and float(first5_mean) > float(later_mean) + EPS
    late_adm = _f(((dev.get("occupancy_cells") or {}).get("LATE_BUT_ADMITTED") or {}).get("mean_pnl"))
    late_blk = _f(((dev.get("occupancy_cells") or {}).get("LATE_AND_BLOCKED") or {}).get("mean_pnl"))
    late_admitted_also_weak = late_adm is not None and late_blk is not None and abs(float(late_adm) - float(late_blk)) <= abs(float(late_blk)) * 0.5
    rho_b = (dev.get("horizon") or {}).get("blocked_spearman_seconds_to_close_vs_pnl")
    horizon_only = rho_b is not None and float(rho_b) > 0.3 and not d_m
    f_ex_mean = bool(((fwd_ex.get("raw") or {}).get("comparison") or {}).get("mean_worse"))
    f_ex_m_mean = bool(((fwd_ex.get("match_primary_same_day_same_role") or {}).get("comparison") or {}).get("mean_worse"))
    outlier_dom = bool(d_mean) and ((not f_mean) or (f_mean and not f_ex_mean) or (d_m_mean and not f_ex_m_mean))
    same_dir_raw = bool(d_mean) == bool(f_mean)
    same_dir_match = bool(d_m_mean) == bool(f_m_mean)
    secondary: list[str] = []
    if late_weaker_dev or timing_explains_dev:
        secondary.append("SESSION_TIMING_DECAY")
    if first5_better:
        secondary.append("IMPLICIT_FIRST_ARRIVAL_PRIORITY")
    if d_m:
        secondary.append("INTRINSIC_ENTRY_STATE_QUALITY")
    if late_admitted_also_weak:
        secondary.append("OCCUPANCY_SELECTION_EFFECT")
    if not integrity:
        case, verdict, nxt, mech = (
            "G",
            "SIMPLE_TECH_PRECAP_TIMING_RCA_INTEGRITY_FAILED",
            "FAIL_CLOSED.",
            "INSUFFICIENT",
        )
    elif insufficient:
        case, verdict, nxt, mech = (
            "F",
            "SIMPLE_TECH_PRECAP_TIMING_RCA_INSUFFICIENT",
            "STOP. Matching / sample insufficient.",
            "INSUFFICIENT",
        )
    elif outlier_dom and not (d_m and f_ex_m_mean):
        case, verdict, nxt, mech = (
            "E",
            "SIMPLE_TECH_PRECAP_FWD_OUTLIER_DOMINATED",
            "No new filter. Additional future evidence first.",
            "OUTLIER_DOMINATED",
        )
    elif d_m and (f_m or f_m_mean) and not (timing_explains_dev and timing_explains_fwd) and same_dir_match:
        case, verdict, nxt, mech = (
            "A",
            "SIMPLE_TECH_PRECAP_INTRINSIC_ENTRY_QUALITY_SUPPORTED",
            "Next: mechanism-only discovery of existing T3 ENTRY state. Do not create a filter yet.",
            "INTRINSIC_ENTRY_STATE_QUALITY",
        )
    elif (timing_explains_dev and timing_explains_fwd) and not (d_m and f_m):
        if first5_better and late_admitted_also_weak and not d_m:
            case, verdict, nxt, mech = (
                "C",
                "SIMPLE_TECH_PRECAP_IMPLICIT_PRIORITY_SUPPORTED",
                "Study occupancy as implicit priority architecture. Do not change CAP. Do not call CAP a quality filter.",
                "IMPLICIT_FIRST_ARRIVAL_PRIORITY",
            )
        else:
            case, verdict, nxt, mech = (
                "B",
                "SIMPLE_TECH_PRECAP_SESSION_TIMING_DECAY_SUPPORTED",
                "Do not search new ENTRY features. Study session-timing architecture in a separate RCA. Do not create a time filter yet.",
                "SESSION_TIMING_DECAY",
            )
    elif d_m or d_m_mean:
        case, verdict, nxt, mech = (
            "D",
            "SIMPLE_TECH_PRECAP_MIXED_TIMING_INTRINSIC",
            "Do not jump to feature search. Decompose the remaining intrinsic component once.",
            "MIXED_TIMING_AND_INTRINSIC",
        )
    else:
        case, verdict, nxt, mech = (
            "B",
            "SIMPLE_TECH_PRECAP_SESSION_TIMING_DECAY_SUPPORTED",
            "Do not search new ENTRY features. Study session-timing architecture in a separate RCA. Do not create a time filter yet.",
            "SESSION_TIMING_DECAY",
        )
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "PRIMARY_MECHANISM": mech,
        "SECONDARY_DRIVERS": secondary,
        "OBSERVED_ECONOMIC_BOTTLENECK": "MARGINAL_ENTRY_QUALITY",
        "INTRINSIC_MECHANISM_CONFIRMED": bool(case == "A"),
        "CANDIDATE_FROZEN": False,
        "FAMILY_CLOSED": True,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "NEW_ENTRY_FILTER": False,
        "CAP_CHANGED": False,
        "NEW_EXIT_RULE": False,
        "TIME_FILTER": False,
        "first_eligible_prospective_date": None,
        "earliest_possible_if_case_A": EARLIEST_POSSIBLE_IF_CASE_A,
        "prospective_armed": False,
        "shrinkage": {"DEV": d_sh, "FWD": f_sh},
        "questions": {
            "Q1_raw_blocked_weak": bool(d_raw or d_mean),
            "Q2_matched_disadvantage_remains": bool(d_m or d_m_mean),
            "Q3_late_arrival_strata_weak": bool(late_weaker),
            "Q4_FIRST5_vs_later_quality_gap": bool(first5_better),
            "Q5_late_admitted_also_weak_time_effect": bool(late_admitted_also_weak),
            "Q6_matched_blocked_weaker_intrinsic": bool(d_m),
            "Q7_remaining_horizon_alone": bool(horizon_only),
            "Q8_DEV_FWD_same_direction": bool(same_dir_raw),
            "Q9_FWD_ex_285A_same_architecture": bool(f_ex_mean) == bool(f_mean),
        },
        "gates": {
            "integrity": integrity,
            "insufficient": insufficient,
            "dev_raw_weaker": d_raw,
            "fwd_raw_weaker": f_raw,
            "dev_matched_weaker": d_m,
            "fwd_matched_weaker": f_m,
            "timing_explains_dev": timing_explains_dev,
            "timing_explains_fwd": timing_explains_fwd,
            "outlier_dominated": outlier_dom,
            "same_dir_raw": same_dir_raw,
            "same_dir_match": same_dir_match,
        },
    }
