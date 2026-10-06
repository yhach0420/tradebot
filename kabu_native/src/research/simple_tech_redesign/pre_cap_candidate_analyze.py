"""Pre-CAP 2-of-3 candidate diagnostics. Session-close occupancy. No threshold search."""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from research.simple_tech_redesign.branch_u_bb_analyze import _public_econ
from research.simple_tech_redesign.branch_u_causal_analyze import _tid_map, attribution
from research.simple_tech_redesign.branch_u_holdout_analyze import econ
from research.simple_tech_redesign.pre_cap_candidate_spec import (
    ADVERSE_COMPONENT_MIN,
    CANDIDATE_ID,
    CONCENTRATION_WARN,
    FWD_REVERSAL_FRAC,
    MAE_SOURCE,
    MAX_SINGLE_EFFECT_SHARE,
    MAX_TOP_SYMBOL_SHARE,
    MIN_DEV_FLAGGED_N,
    MIN_ROLE_SPLIT_N,
    MIN_SNAPSHOT_RATE,
    REJECT_REASON,
)
from research.simple_tech_redesign.v26_spec import PATH_TYPES
from research.simple_tech_redesign.v28_analyze import _delta, _pf_num

GOOD = "GOOD_CONTINUATION"
EARLY = "EARLY_FAILURE"
DIP = "DIP_THEN_RECOVERY"
PTF = "PROFIT_THEN_FAILURE"
OTHER = "OTHER"
PATHS = list(PATH_TYPES)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if x != x:
            return None
        return x
    except (TypeError, ValueError):
        return None


def _mean(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return sum(xs) / float(len(xs))


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    if n % 2:
        return float(s[n // 2])
    return 0.5 * (s[n // 2 - 1] + s[n // 2])


def _rate(n: int, d: int) -> Optional[float]:
    if d <= 0:
        return None
    return n / float(d)


def _pf_ge(treat: Any, ctrl: Any) -> bool:
    ta, tb = _pf_num(treat), _pf_num(ctrl)
    if ta is None or tb is None:
        return False
    if ta == float("inf") and tb == float("inf"):
        return True
    if ta == float("inf"):
        return True
    if tb == float("inf"):
        return False
    return float(ta) + 1e-12 >= float(tb)


def quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    filled = [r for r in rows if r.get("actual_filled")]
    pnls = [_f(r.get("control_pnl")) for r in filled]
    pnls_f = [x for x in pnls if x is not None]
    mfes = [_f(r.get("mfe_bps")) for r in filled]
    maes = [_f(r.get("mae_bps")) for r in filled]
    by = {p: 0 for p in PATHS}
    for r in filled:
        p = str(r.get("path_type") or OTHER)
        if p not in by:
            p = OTHER
        by[p] += 1
    n = len(filled)
    never_n = sum(1 for r in filled if r.get("never_break_even"))
    win_n = sum(1 for x in pnls_f if x > 1e-12)
    return {
        "candidate_n": len(rows),
        "filled_n": n,
        "NEVER_BE_n": int(never_n),
        "NEVER_BE_rate": _rate(never_n, n),
        "EARLY_FAILURE_n": by.get(EARLY, 0),
        "EARLY_FAILURE_rate": _rate(by.get(EARLY, 0), n),
        "GOOD_n": by.get(GOOD, 0),
        "GOOD_rate": _rate(by.get(GOOD, 0), n),
        "DIP_n": by.get(DIP, 0),
        "DIP_rate": _rate(by.get(DIP, 0), n),
        "PTF_n": by.get(PTF, 0),
        "PTF_rate": _rate(by.get(PTF, 0), n),
        "OTHER_n": by.get(OTHER, 0),
        "win_n": int(win_n),
        "win_rate": _rate(win_n, len(pnls_f)),
        "mean_pnl_yen_100": _mean(pnls_f),
        "median_pnl_yen_100": _median(pnls_f),
        "MAE_mean_bps": _mean([x for x in maes if x is not None]),
        "MFE_mean_bps": _mean([x for x in mfes if x is not None]),
        "MAE_median_bps": _median([x for x in maes if x is not None]),
        "MFE_median_bps": _median([x for x in mfes if x is not None]),
        "MAE_SOURCE": MAE_SOURCE,
        "by_path": by,
    }


def worse_quality(flagged: dict[str, Any], retained: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    fn, rn = int(flagged.get("filled_n") or 0), int(retained.get("filled_n") or 0)
    if fn < int(MIN_ROLE_SPLIT_N) or rn < int(MIN_ROLE_SPLIT_N):
        return {"ok": False, "underpowered": True, "reasons": ["underpowered"]}
    fm, rm = _f(flagged.get("median_pnl_yen_100")), _f(retained.get("median_pnl_yen_100"))
    if fm is not None and rm is not None and fm < rm - 1e-12:
        reasons.append("median_pnl_worse")
    fbe, rbe = _f(flagged.get("NEVER_BE_rate")), _f(retained.get("NEVER_BE_rate"))
    if fbe is not None and rbe is not None and fbe > rbe + 1e-12:
        reasons.append("never_be_higher")
    fe, re = _f(flagged.get("EARLY_FAILURE_rate")), _f(retained.get("EARLY_FAILURE_rate"))
    if fe is not None and re is not None and fe > re + 1e-12:
        reasons.append("early_higher")
    fw, rw = _f(flagged.get("win_rate")), _f(retained.get("win_rate"))
    if fw is not None and rw is not None and fw < rw - 1e-12:
        reasons.append("win_rate_worse")
    return {"ok": bool(reasons), "underpowered": False, "reasons": reasons}


def merge_arms(day_bodies: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    trades = []
    acc = {
        "signal_n": 0,
        "pre_cap_reject_n": 0,
        "accepted_entry_n": 0,
        "fill_n": 0,
        "cap_blocked": 0,
        "same_symbol_blocked": 0,
        "expired_n": 0,
        "slot_release_n": 0,
    }
    for b in day_bodies:
        pack = dict(b.get(arm) or {})
        trades.extend(list(pack.get("trades") or []))
        for k in acc:
            acc[k] = int(acc.get(k) or 0) + int(pack.get(k) or 0)
    acc["trades"] = trades
    acc["core_fill_n"] = sum(1 for t in trades if str(t.get("fill_role") or "") == "CORE")
    acc["added_fill_n"] = sum(1 for t in trades if str(t.get("fill_role") or "") == "ADDED")
    return acc


def day_table(day_bodies: list[dict[str, Any]], days: list[str]) -> list[dict[str, Any]]:
    by = {str(b.get("date") or ""): b for b in day_bodies}
    out = []
    for day in days:
        b = dict(by.get(day) or {})
        c_tr = list((b.get("control") or {}).get("trades") or [])
        t_tr = list((b.get("treatment") or {}).get("trades") or [])
        c_pnl = sum(float(_f(t.get("pnl_yen_100")) or 0.0) for t in c_tr)
        t_pnl = sum(float(_f(t.get("pnl_yen_100")) or 0.0) for t in t_tr)
        attr = attribution(c_tr, t_tr)
        cm = _tid_map(c_tr)
        tm = _tid_map(t_tr)
        incr_n = len(set(tm) - set(cm))
        out.append(
            {
                "date": day,
                "control_pnl": c_pnl,
                "treatment_pnl": t_pnl,
                "delta_pnl": t_pnl - c_pnl,
                "pre_cap_reject_n": int(b.get("pre_cap_reject_n") or 0),
                "incremental_fill_n": int(incr_n),
                "direct_removal_effect": attr.get("DISPLACED_TRADE_DELTA"),
                "downstream_occupancy_effect": attr.get("SLOT_RELEASE_DOWNSTREAM_DELTA"),
                "common_delta": attr.get("DIRECT_EXIT_DELTA"),
            }
        )
    return out


def counts_public(arm: dict[str, Any], days: list[str]) -> dict[str, Any]:
    e = _public_econ(econ(list(arm.get("trades") or []), days))
    return {
        "signal_n": arm.get("signal_n"),
        "pre_cap_reject_n": arm.get("pre_cap_reject_n"),
        "accepted_n": arm.get("accepted_entry_n"),
        "fill_n": arm.get("fill_n"),
        "CORE_fill_n": arm.get("core_fill_n"),
        "ADDED_fill_n": arm.get("added_fill_n"),
        "CAP_reject_n": arm.get("cap_blocked"),
        "same_symbol_reject_n": arm.get("same_symbol_blocked"),
        "expired_n": arm.get("expired_n"),
        **e,
    }


def cohort_pack(body: dict[str, Any], days: list[str]) -> dict[str, Any]:
    rows = list(body.get("rows") or [])
    flagged = [r for r in rows if r.get("pre_cap_reject")]
    retained = [r for r in rows if not r.get("pre_cap_reject")]
    ctrl = merge_arms(list(body.get("day_bodies") or []), "control")
    treat = merge_arms(list(body.get("day_bodies") or []), "treatment")
    attr = attribution(list(ctrl.get("trades") or []), list(treat.get("trades") or []))
    roles = {}
    for role in ("CORE", "ADDED"):
        fr = [r for r in flagged if str(r.get("fill_role") or "") == role]
        rr = [r for r in retained if str(r.get("fill_role") or "") == role]
        fq, rq = quality(fr), quality(rr)
        roles[role] = {
            "flagged": fq,
            "retained": rq,
            "flagged_rate_among_role_signals": _rate(
                sum(1 for r in rows if str(r.get("fill_role") or "") == role and r.get("pre_cap_reject")),
                sum(1 for r in rows if str(r.get("fill_role") or "") == role),
            ),
            "within_role_worse": worse_quality(fq, rq),
        }
    qf, qr = quality(flagged), quality(retained)
    c_econ = counts_public(ctrl, days)
    t_econ = counts_public(treat, days)
    days_tbl = day_table(list(body.get("day_bodies") or []), days)
    flagged_syms = Counter(str(r.get("symbol") or "") for r in flagged)
    top_flag = flagged_syms.most_common(1)[0] if flagged_syms else ("", 0)
    disp_ids = list(attr.get("control_only_ids") or [])
    disp_syms = Counter()
    cm = _tid_map(list(ctrl.get("trades") or []))
    for tid in disp_ids:
        parts = str(tid).split("|")
        if len(parts) >= 2:
            disp_syms[parts[1]] += 1
    # pnl effect of removed trades by symbol
    pnl_by = Counter()
    for k, t in cm.items():
        if k not in _tid_map(list(treat.get("trades") or [])):
            pnl_by[str(t.get("symbol") or "")] += abs(float(_f(t.get("pnl_yen_100")) or 0.0))
    top_pnl = pnl_by.most_common(1)[0] if pnl_by else ("", 0.0)
    d_pnl = float(t_econ.get("TOTAL_PNL_YEN") or 0.0) - float(c_econ.get("TOTAL_PNL_YEN") or 0.0)
    day_abs = sorted(((abs(float(r.get("delta_pnl") or 0.0)), r.get("date"), r.get("delta_pnl")) for r in days_tbl), reverse=True)
    top_day_share = None
    if day_abs and abs(d_pnl) > 1e-12:
        top_day_share = day_abs[0][0] / abs(d_pnl)
    treat_imp_by = Counter()
    tm = _tid_map(list(treat.get("trades") or []))
    for k, t in tm.items():
        if k not in cm:
            p = float(_f(t.get("pnl_yen_100")) or 0.0)
            if p > 0:
                treat_imp_by[str(t.get("symbol") or "")] += p
    top_imp = treat_imp_by.most_common(1)[0] if treat_imp_by else ("", 0.0)
    n_flag = len(flagged)
    snap_n = sum(1 for r in rows if r.get("board_snapshot_ok"))
    return {
        "QUALITY_OVERALL": {
            "flagged_n": n_flag,
            "retained_n": len(retained),
            "candidate_n": len(rows),
            "flagged_rate": _rate(n_flag, len(rows)),
            "flagged": qf,
            "retained": qr,
            "overall_worse": worse_quality(qf, qr),
        },
        "QUALITY_BY_ROLE": roles,
        "CONTROL": c_econ,
        "TREATMENT": t_econ,
        "ATTRIBUTION": {
            "DIRECT_REMOVAL_EFFECT": attr.get("DISPLACED_TRADE_DELTA"),
            "DOWNSTREAM_OCCUPANCY_EFFECT": attr.get("SLOT_RELEASE_DOWNSTREAM_DELTA"),
            "COMMON_PNL_DELTA": attr.get("DIRECT_EXIT_DELTA"),
            "TOTAL_CAUSAL_DELTA": attr.get("TOTAL_CAUSAL_DELTA"),
            "CONTROL_ONLY_N": attr.get("CONTROL_ONLY_N"),
            "INCREMENTAL_TREATMENT_N": attr.get("INCREMENTAL_TREATMENT_N"),
            "COMMON_N": attr.get("COMMON_N"),
        },
        "DAY_ROBUSTNESS": days_tbl,
        "CONCENTRATION": {
            "flagged_top_symbol": top_flag[0],
            "flagged_top_symbol_share": (top_flag[1] / float(n_flag)) if n_flag else None,
            "removed_pnl_abs_top_symbol": top_pnl[0],
            "removed_pnl_abs_top_share": (float(top_pnl[1]) / float(sum(pnl_by.values()))) if pnl_by else None,
            "downstream_pos_top_symbol": top_imp[0],
            "downstream_pos_top_share": (float(top_imp[1]) / float(sum(treat_imp_by.values()))) if treat_imp_by else None,
            "top_day_abs_delta_share": top_day_share,
            "top_day": day_abs[0][1] if day_abs else None,
            "warning": bool(
                (n_flag and top_flag[1] / float(n_flag) >= float(CONCENTRATION_WARN) - 1e-12)
                or (pnl_by and float(top_pnl[1]) / float(sum(pnl_by.values())) >= float(CONCENTRATION_WARN) - 1e-12)
                or (treat_imp_by and float(top_imp[1]) / float(sum(treat_imp_by.values())) >= float(CONCENTRATION_WARN) - 1e-12)
            ),
        },
        "occupancy_sot_ok": bool(body.get("occupancy_sot_ok")),
        "occupancy_leftover_n": int(body.get("occupancy_leftover_n") or 0),
        "board_snapshot_ok_n": int(snap_n),
        "board_snapshot_rate": _rate(snap_n, len(rows)),
    }


def _role_quality_sep(roles: dict[str, Any]) -> dict[str, Any]:
    hits = []
    proxy = True
    for role, pack in roles.items():
        w = dict(pack.get("within_role_worse") or {})
        if w.get("underpowered"):
            continue
        proxy = False
        if w.get("ok"):
            hits.append({"role": role, "reasons": w.get("reasons")})
    return {"within_role_quality_worse": hits, "any_within_role": bool(hits), "all_within_underpowered": proxy and not hits}


def decide(dev: dict[str, Any], fwd: dict[str, Any], *, leak_ok: bool, ni_ok: bool) -> dict[str, Any]:
    occ_ok = bool(dev.get("occupancy_sot_ok") and fwd.get("occupancy_sot_ok"))
    leftover_ok = int(dev.get("occupancy_leftover_n") or 0) == 0 and int(fwd.get("occupancy_leftover_n") or 0) == 0
    integrity = bool(leak_ok and ni_ok and occ_ok and leftover_ok)
    dq = dict(dev.get("QUALITY_OVERALL") or {})
    flagged_n = int(dq.get("flagged_n") or 0)
    fires = flagged_n >= int(MIN_DEV_FLAGGED_N)
    snap_rate = _f(dev.get("board_snapshot_rate"))
    snap_ok = snap_rate is not None and float(snap_rate) + 1e-12 >= float(MIN_SNAPSHOT_RATE)
    role = _role_quality_sep(dict(dev.get("QUALITY_BY_ROLE") or {}))
    ce, te = dict(dev.get("CONTROL") or {}), dict(dev.get("TREATMENT") or {})
    fe, ft = dict(fwd.get("CONTROL") or {}), dict(fwd.get("TREATMENT") or {})
    da = dict(dev.get("ATTRIBUTION") or {})
    fa = dict(fwd.get("ATTRIBUTION") or {})
    d_pnl = _delta(te, ce, "TOTAL_PNL_YEN")
    f_pnl = _delta(ft, fe, "TOTAL_PNL_YEN")
    d_dd = _delta(te, ce, "REALIZED_MAX_DD")
    f_dd = _delta(ft, fe, "REALIZED_MAX_DD")
    pnl_up = _f(d_pnl) is not None and float(d_pnl) > 1e-12
    pf_ok = _pf_ge(te.get("PF"), ce.get("PF"))
    dd_ok = _f(d_dd) is not None and float(d_dd) >= -1e-12
    fwd_rev_ok = True
    if pnl_up and _f(f_pnl) is not None:
        fwd_rev_ok = float(f_pnl) + 1e-12 >= -float(FWD_REVERSAL_FRAC) * float(d_pnl)
    fwd_pf_worse = not _pf_ge(ft.get("PF"), fe.get("PF"))
    fwd_dd_worse = _f(f_dd) is not None and float(f_dd) < -1e-12
    fwd_both_worse = bool(fwd_pf_worse and fwd_dd_worse)
    tot = _f(da.get("TOTAL_CAUSAL_DELTA")) or 0.0
    direct = abs(float(_f(da.get("DIRECT_REMOVAL_EFFECT")) or 0.0))
    down = abs(float(_f(da.get("DOWNSTREAM_OCCUPANCY_EFFECT")) or 0.0))
    single_ok = True
    if abs(tot) > 1e-12:
        single_ok = max(direct, down) / abs(tot) <= float(MAX_SINGLE_EFFECT_SHARE) + 1e-12
    conc = dict(dev.get("CONCENTRATION") or {})
    conc_ok = not bool(conc.get("warning"))
    severe_share = _f(conc.get("flagged_top_symbol_share"))
    if severe_share is not None and severe_share >= float(MAX_TOP_SYMBOL_SHARE) - 1e-12:
        conc_ok = False

    freeze = False
    if not integrity:
        case, verdict = "E", "SIMPLE_TECH_PRE_CAP_ENTRY_QUALITY_INVALID"
        nxt = "STOP. Integrity failed."
    elif not fires or not snap_ok:
        case, verdict = "D", "SIMPLE_TECH_PRE_CAP_ENTRY_QUALITY_INSUFFICIENT"
        nxt = (
            f"STOP. Flagged_n={flagged_n} (min {MIN_DEV_FLAGGED_N}) "
            f"snapshot_rate={snap_rate} (min {MIN_SNAPSHOT_RATE})."
        )
    elif role["all_within_underpowered"] and not role["any_within_role"]:
        case, verdict = "D", "SIMPLE_TECH_PRE_CAP_ENTRY_QUALITY_INSUFFICIENT"
        nxt = "STOP. Within-role quality split is underpowered (filled_n < 5 on a side)."
    elif not role["any_within_role"]:
        case, verdict = "B", "SIMPLE_TECH_PRE_CAP_BOARD_ROLE_PROXY_ONLY"
        nxt = "STOP. Board 2-of-3 separates CORE/ADDED but not trade quality inside a role. Reject as ENTRY quality filter."
    elif not (pnl_up and pf_ok and dd_ok):
        case, verdict = "C", "SIMPLE_TECH_PRE_CAP_ENTRY_QUALITY_ECONOMICS_FAILED"
        nxt = "STOP. Within-role quality difference exists but development causal economics do not qualify. Do not retune the 2-of-3 rule."
    elif not (fwd_rev_ok and not fwd_both_worse and single_ok and conc_ok):
        case, verdict = "C", "SIMPLE_TECH_PRE_CAP_ENTRY_QUALITY_ECONOMICS_FAILED"
        nxt = "STOP. Development economics qualified locally but forward-burned robustness/concentration/attribution gates failed. Do not retune."
    else:
        case, verdict = "A", "SIMPLE_TECH_PRE_CAP_ENTRY_QUALITY_DEVELOPMENT_SUPPORTED"
        nxt = (
            "Candidate freeze only. TRUE_OOS=false. CERTIFIED=false. Do not implement runtime. "
            "Do not use 20260903. Validation starts on the first complete sealed capture created after freeze. "
            "Next research after a supported forward is Technical EXIT architecture, not sizing."
        )
        freeze = True
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "CANDIDATE_FROZEN": bool(freeze),
        "ROLE_PROXY_ONLY": case == "B",
        "WITHIN_ROLE_HITS": role["within_role_quality_worse"],
        "FIRES": bool(fires),
        "DEV_PNL_UP": bool(pnl_up),
        "DEV_PF_OK": bool(pf_ok),
        "DEV_DD_OK": bool(dd_ok),
        "FWD_REVERSAL_OK": bool(fwd_rev_ok),
        "FWD_PF_AND_DD_NOT_BOTH_WORSE": (not fwd_both_worse),
        "ATTRIBUTION_NOT_SINGLE_SOURCE": bool(single_ok),
        "CONCENTRATION_OK": bool(conc_ok),
        "SNAPSHOT_OK": bool(snap_ok),
        "WITHIN_ROLE_QUALITY_SEPARATION": bool(role["any_within_role"]),
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "REJECT_REASON": REJECT_REASON,
        "ADVERSE_COMPONENT_MIN": int(ADVERSE_COMPONENT_MIN),
        "DEV_DELTA_PNL": d_pnl,
        "FWD_DELTA_PNL": f_pnl,
        "DEV_DIRECT_REMOVAL_EFFECT": da.get("DIRECT_REMOVAL_EFFECT"),
        "DEV_DOWNSTREAM_OCCUPANCY_EFFECT": da.get("DOWNSTREAM_OCCUPANCY_EFFECT"),
        "DEV_TOTAL_CAUSAL_DELTA": da.get("TOTAL_CAUSAL_DELTA"),
        "FWD_DIRECT_REMOVAL_EFFECT": fa.get("DIRECT_REMOVAL_EFFECT"),
        "FWD_DOWNSTREAM_OCCUPANCY_EFFECT": fa.get("DOWNSTREAM_OCCUPANCY_EFFECT"),
        "FWD_TOTAL_CAUSAL_DELTA": fa.get("TOTAL_CAUSAL_DELTA"),
    }


def answers(dev: dict[str, Any], fwd: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    dq = dict(dev.get("QUALITY_OVERALL") or {})
    roles = dict(dev.get("QUALITY_BY_ROLE") or {})
    core = dict(roles.get("CORE") or {})
    added = dict(roles.get("ADDED") or {})
    ce, te = dict(dev.get("CONTROL") or {}), dict(dev.get("TREATMENT") or {})
    fe, ft = dict(fwd.get("CONTROL") or {}), dict(fwd.get("TREATMENT") or {})
    conc = dict(dev.get("CONCENTRATION") or {})
    rule = (
        f"{CANDIDATE_ID}: adverse_component_count = BID_DEPLETION_5S + ASK_ADD_5S + "
        f"SPREAD_EXPANSION_5S; PRE_CAP_REJECT iff count >= {ADVERSE_COMPONENT_MIN}; else continue."
    )
    within = bool(decision.get("WITHIN_ROLE_QUALITY_SEPARATION"))
    role_proxy_only = bool(decision.get("ROLE_PROXY_ONLY"))
    core_rate = core.get("flagged_rate_among_role_signals")
    added_rate = added.get("flagged_rate_among_role_signals")
    merely_proxy = bool(role_proxy_only) or (
        not within and core_rate is not None and added_rate is not None and abs(float(core_rate) - float(added_rate)) > 1e-12
    )
    return {
        "Q1_CANDIDATE_RULE": rule,
        "Q2_THRESHOLD_SEARCH": False,
        "Q3_BOARD_WINDOW_5S_FIXED": True,
        "Q4_BOARD_OK_USED": False,
        "Q5_MERE_ROLE_PROXY": bool(merely_proxy and not within),
        "Q6_WITHIN_ROLE_QUALITY_SEPARATION": bool(within),
        "Q6_CORE_WITHIN": dict(core.get("within_role_worse") or {}),
        "Q6_ADDED_WITHIN": dict(added.get("within_role_worse") or {}),
        "Q7_DEV_CONTROL_PNL": ce.get("TOTAL_PNL_YEN"),
        "Q7_DEV_TREATMENT_PNL": te.get("TOTAL_PNL_YEN"),
        "Q8_DEV_CONTROL_PF": ce.get("PF"),
        "Q8_DEV_TREATMENT_PF": te.get("PF"),
        "Q9_DEV_CONTROL_MAXDD": ce.get("REALIZED_MAX_DD"),
        "Q9_DEV_TREATMENT_MAXDD": te.get("REALIZED_MAX_DD"),
        "Q10_FWD_CONTROL_PNL": fe.get("TOTAL_PNL_YEN"),
        "Q10_FWD_TREATMENT_PNL": ft.get("TOTAL_PNL_YEN"),
        "Q11_DIRECT_REMOVAL_EFFECT_DEV": decision.get("DEV_DIRECT_REMOVAL_EFFECT"),
        "Q11_DIRECT_REMOVAL_EFFECT_FWD": decision.get("FWD_DIRECT_REMOVAL_EFFECT"),
        "Q12_DOWNSTREAM_OCCUPANCY_EFFECT_DEV": decision.get("DEV_DOWNSTREAM_OCCUPANCY_EFFECT"),
        "Q12_DOWNSTREAM_OCCUPANCY_EFFECT_FWD": decision.get("FWD_DOWNSTREAM_OCCUPANCY_EFFECT"),
        "Q13_TOTAL_CAUSAL_DELTA_DEV": decision.get("DEV_TOTAL_CAUSAL_DELTA"),
        "Q13_TOTAL_CAUSAL_DELTA_FWD": decision.get("FWD_TOTAL_CAUSAL_DELTA"),
        "Q14_DAY_CONCENTRATION": {
            "top_day": conc.get("top_day"),
            "top_day_abs_delta_share": conc.get("top_day_abs_delta_share"),
            "warning": conc.get("warning"),
        },
        "Q15_SYMBOL_CONCENTRATION": {
            "flagged_top_symbol": conc.get("flagged_top_symbol"),
            "flagged_top_symbol_share": conc.get("flagged_top_symbol_share"),
            "removed_pnl_abs_top_symbol": conc.get("removed_pnl_abs_top_symbol"),
            "removed_pnl_abs_top_share": conc.get("removed_pnl_abs_top_share"),
            "downstream_pos_top_symbol": conc.get("downstream_pos_top_symbol"),
            "downstream_pos_top_share": conc.get("downstream_pos_top_share"),
        },
        "Q16_VERDICT": decision.get("VERDICT"),
        "Q16_CASE": decision.get("CASE"),
        "Q17_CANDIDATE_FROZEN": bool(decision.get("CANDIDATE_FROZEN")),
        "Q18_TRUE_OOS": False,
        "Q19_CERTIFIED": False,
        "Q20_NEXT": decision.get("NEXT"),
        "DEV_FLAGGED_N": dq.get("flagged_n"),
        "DEV_FLAGGED_RATE": dq.get("flagged_rate"),
        "CORE_FLAGGED_RATE": core_rate,
        "ADDED_FLAGGED_RATE": added_rate,
        "REJECT_REASON": REJECT_REASON,
    }
