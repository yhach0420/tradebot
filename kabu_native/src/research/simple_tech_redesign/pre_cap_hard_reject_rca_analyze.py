"""Hard-reject / replacement / relative-priority diagnostics. No ranking formula. No search."""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from research.simple_tech_redesign.pre_cap_hard_reject_rca_harvest import remove_diagnostic
from research.simple_tech_redesign.pre_cap_hard_reject_rca_spec import (
    DEV_COMMON_N_EXPECTED,
    DEV_CONTROL_ONLY_N_EXPECTED,
    DEV_INCREMENTAL_N_EXPECTED,
    FAILURE_PATHS,
    MIN_RELATIVE_PAIR_N_DEV,
    MIN_RELATIVE_PAIR_N_FWD,
    PROTECTED_PATHS,
)

GOOD = "GOOD_CONTINUATION"
DIP = "DIP_THEN_RECOVERY"
EARLY = "EARLY_FAILURE"
PTF = "PROFIT_THEN_FAILURE"


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


def _sign(v: Optional[float]) -> int:
    if v is None:
        return 0
    if v > 1e-12:
        return 1
    if v < -1e-12:
        return -1
    return 0


def _path_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by = {GOOD: 0, EARLY: 0, DIP: 0, PTF: 0, "OTHER": 0}
    never = 0
    pos = 0
    pnls = []
    for r in rows:
        p = str(r.get("path_type") or "OTHER")
        if p not in by:
            p = "OTHER"
        by[p] += 1
        if r.get("never_break_even"):
            never += 1
        pnl = _f(r.get("pnl_yen_100"))
        if pnl is None:
            pnl = _f(r.get("control_pnl"))
        if pnl is not None:
            pnls.append(float(pnl))
            if float(pnl) > 1e-12:
                pos += 1
    n = len(rows)
    return {
        "n": n,
        "CORE_n": sum(1 for r in rows if str(r.get("fill_role") or "") == "CORE"),
        "ADDED_n": sum(1 for r in rows if str(r.get("fill_role") or "") == "ADDED"),
        "NEVER_BE_n": int(never),
        "NEVER_BE_rate": _rate(never, n),
        "GOOD_n": by[GOOD],
        "DIP_n": by[DIP],
        "EARLY_n": by[EARLY],
        "PTF_n": by[PTF],
        "OTHER_n": by["OTHER"],
        "protected_n": by[GOOD] + by[DIP],
        "failure_path_n": by[EARLY],
        "pos_pnl_n": int(pos),
        "pos_pnl_rate": _rate(pos, len(pnls)),
        "mean_pnl": _mean(pnls),
        "median_pnl": _median(pnls),
        "LOOKED_WRONG_TO_REMOVE_n": sum(1 for r in rows if str(r.get("remove_diagnostic") or "") == "LOOKED_WRONG_TO_REMOVE"),
        "LOOKED_RIGHT_TO_REMOVE_n": sum(1 for r in rows if str(r.get("remove_diagnostic") or "") == "LOOKED_RIGHT_TO_REMOVE"),
        "AMBIGUOUS_n": sum(1 for r in rows if str(r.get("remove_diagnostic") or "") == "AMBIGUOUS"),
        "by_path": by,
    }


def _role_split(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for role in ("CORE", "ADDED"):
        grp = [r for r in rows if str(r.get("fill_role") or "") == role]
        out[role] = _path_counts(grp)
    return out


def hard_reject_semantic(body: dict[str, Any]) -> dict[str, Any]:
    rows = list(body.get("rows") or [])
    flagged = []
    retained = []
    for r in rows:
        rec = dict(r)
        rec["remove_diagnostic"] = remove_diagnostic(rec)
        rec["pnl_yen_100"] = rec.get("control_pnl")
        if rec.get("pre_cap_reject") and rec.get("actual_filled"):
            flagged.append(rec)
        elif (not rec.get("pre_cap_reject")) and rec.get("actual_filled"):
            retained.append(rec)
    control_only = list(body.get("control_only") or [])
    direct = [t for t in control_only if t.get("removal_class") == "DIRECT_PRECAP" or t.get("pre_cap_reject")]
    cascade = [t for t in control_only if not (t.get("removal_class") == "DIRECT_PRECAP" or t.get("pre_cap_reject"))]
    combo = Counter()
    for r in flagged:
        bits = tuple(name for name in ("BID_DEPLETION_5S", "ASK_ADD_5S", "SPREAD_EXPANSION_5S") if r.get(name) or any(str(c.get("name")) == name and c.get("active") for c in list(r.get("components") or [])))
        combo[str(bits)] += 1
    # occupancy control-only uses enriched fields
    combo_occ = Counter()
    for t in direct:
        bits = tuple(name for name in ("BID_DEPLETION_5S", "ASK_ADD_5S", "SPREAD_EXPANSION_5S") if t.get(name))
        combo_occ[str(bits)] += 1
    return {
        "UNCONSTRAINED_FLAGGED": _path_counts(flagged),
        "UNCONSTRAINED_FLAGGED_BY_ROLE": _role_split(flagged),
        "UNCONSTRAINED_RETAINED": _path_counts(retained),
        "UNCONSTRAINED_RETAINED_BY_ROLE": _role_split(retained),
        "CONTROL_ONLY": _path_counts(control_only),
        "CONTROL_ONLY_BY_ROLE": _role_split(control_only),
        "DIRECT_PRECAP_REMOVED": _path_counts(direct),
        "DIRECT_PRECAP_REMOVED_BY_ROLE": _role_split(direct),
        "OCCUPANCY_CASCADE_REMOVED": _path_counts(cascade),
        "component_combo_unconstrained_flagged": dict(combo),
        "component_combo_direct_removed": dict(combo_occ),
        "note": (
            "LOOKED_WRONG_TO_REMOVE = GOOD_CONTINUATION or DIP_THEN_RECOVERY. "
            "LOOKED_RIGHT_TO_REMOVE = EARLY_FAILURE or NEVER_BE. PnL is overlay only, not a rule."
        ),
        "PROTECTED_PATHS": list(PROTECTED_PATHS),
        "FAILURE_PATHS": list(FAILURE_PATHS),
    }


def pair_economics(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    deltas = [float(_f(p.get("replacement_minus_removed")) or 0.0) for p in pairs]
    better = sum(1 for x in deltas if x > 1e-12)
    worse = sum(1 for x in deltas if x < -1e-12)
    ties = len(deltas) - better - worse
    return {
        "pair_n": len(pairs),
        "replacement_better_n": int(better),
        "replacement_worse_n": int(worse),
        "replacement_tie_n": int(ties),
        "median_pair_delta": _median(deltas),
        "mean_pair_delta": _mean(deltas),
        "total_pair_delta": sum(deltas) if deltas else 0.0,
    }


def order_diagnostic(pairs: list[dict[str, Any]], incremental: list[dict[str, Any]], control_only: list[dict[str, Any]]) -> dict[str, Any]:
    elapsed = [float(x) for x in (_f(p.get("elapsed_sec_from_removed")) for p in pairs) if x is not None]
    rem_min = [float(x) for x in (_f(p.get("removed_minutes_from_open")) for p in pairs) if x is not None]
    inc_min = [float(x) for x in (_f(p.get("replacement_minutes_from_open")) for p in pairs) if x is not None]
    dens_r = [float(x) for x in (_f((p.get("removed") or {}).get("candidate_density_60s")) for p in pairs) if x is not None]
    dens_i = [float(x) for x in (_f(p.get("candidate_density_60s")) for p in pairs) if x is not None]
    later = sum(1 for p in pairs if (_f(p.get("elapsed_sec_from_removed")) or 0.0) > 1e-12)
    rem_core = sum(1 for t in control_only if str(t.get("fill_role") or "") == "CORE")
    inc_core = sum(1 for t in incremental if str(t.get("fill_role") or "") == "CORE")
    return {
        "median_elapsed_sec_removed_to_replacement": _median(elapsed),
        "mean_elapsed_sec_removed_to_replacement": _mean(elapsed),
        "replacement_later_than_removed_n": int(later),
        "median_removed_minutes_from_open": _median(rem_min),
        "median_replacement_minutes_from_open": _median(inc_min),
        "median_removed_density_60s": _median(dens_r),
        "median_replacement_density_60s": _median(dens_i),
        "CONTROL_ONLY_CORE_n": int(rem_core),
        "CONTROL_ONLY_ADDED_n": len(control_only) - rem_core,
        "INCREMENTAL_CORE_n": int(inc_core),
        "INCREMENTAL_ADDED_n": len(incremental) - inc_core,
        "later_and_more_added": bool(
            (_median(elapsed) or 0.0) > 0
            and inc_core < rem_core
        ),
    }


def relative_board(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    agree = 0
    usable = 0
    adv_deltas = []
    pnl_deltas = []
    by_comp = {
        "BID_DEPLETION_5S": {"agree": 0, "usable": 0},
        "ASK_ADD_5S": {"agree": 0, "usable": 0},
        "SPREAD_EXPANSION_5S": {"agree": 0, "usable": 0},
    }
    keys = {
        "BID_DEPLETION_5S": "bid_depletion_event_n",
        "ASK_ADD_5S": "ask_add_event_n",
        "SPREAD_EXPANSION_5S": "spread_expand_event_n",
    }
    board_pref = Counter()
    econ_pref = Counter()
    for p in pairs:
        rem = dict(p.get("removed") or {})
        inc = dict(p.get("replacement") or {})
        ra = int(p.get("removed_adverse_component_count") or rem.get("adverse_component_count") or 0)
        ia = int(p.get("replacement_adverse_component_count") or inc.get("adverse_component_count") or 0)
        dp = float(_f(p.get("replacement_minus_removed")) or 0.0)
        adv_deltas.append(ia - ra)
        pnl_deltas.append(dp)
        if ia < ra:
            bpref = "replacement"
        elif ra < ia:
            bpref = "removed"
        else:
            bpref = "tie"
        if dp > 1e-12:
            epref = "replacement"
        elif dp < -1e-12:
            epref = "removed"
        else:
            epref = "tie"
        board_pref[bpref] += 1
        econ_pref[epref] += 1
        if bpref != "tie" and epref != "tie":
            usable += 1
            if bpref == epref:
                agree += 1
        for name, key in keys.items():
            rv = _f(rem.get(key) if rem.get(key) is not None else rem.get(name))
            iv = _f(inc.get(key) if inc.get(key) is not None else inc.get(name))
            if rv is None or iv is None or abs(float(iv) - float(rv)) <= 1e-12:
                continue
            by_comp[name]["usable"] += 1
            board_wants_inc = float(iv) < float(rv)
            econ_wants_inc = dp > 1e-12
            if board_wants_inc == econ_wants_inc and abs(dp) > 1e-12:
                by_comp[name]["agree"] += 1
    for name, pack in by_comp.items():
        pack["agree_rate"] = _rate(int(pack["agree"]), int(pack["usable"]))
    med_adv = _median(adv_deltas)
    med_pnl = _median(pnl_deltas)
    aligned = bool(_sign(med_adv) != 0 and _sign(med_pnl) != 0 and _sign(med_adv) == -_sign(med_pnl))
    return {
        "pair_n": len(pairs),
        "usable_preference_n": int(usable),
        "preference_agree_n": int(agree),
        "preference_agree_rate": _rate(agree, usable),
        "board_prefers": dict(board_pref),
        "econ_prefers": dict(econ_pref),
        "median_replacement_minus_removed_adverse": med_adv,
        "median_replacement_minus_removed_pnl": med_pnl,
        "lower_adverse_aligns_with_higher_pnl": bool(aligned),
        "by_component": by_comp,
        "note": (
            "Relative direction only: fewer Bid depletion / Ask add / spread-expand events. "
            "No score, no ranking rule."
        ),
    }


def top_share(rows: list[dict[str, Any]], key: str = "symbol") -> dict[str, Any]:
    c = Counter(str(r.get(key) or "") for r in rows)
    if not c:
        return {"top": "", "share": None, "n": 0}
    top, n = c.most_common(1)[0]
    return {"top": top, "share": n / float(len(rows)), "n": n}


def cohort_pack(body: dict[str, Any]) -> dict[str, Any]:
    pairs = list(body.get("pairs") or [])
    control_only = list(body.get("control_only") or [])
    incremental = list(body.get("incremental") or [])
    rel = relative_board(pairs)
    return {
        "identity": dict(body.get("identity") or {}),
        "attribution": dict(body.get("attribution") or {}),
        "HARD_REJECT_SEMANTIC": hard_reject_semantic(body),
        "PAIR_ECONOMICS": pair_economics(pairs),
        "ORDER": order_diagnostic(pairs, incremental, control_only),
        "RELATIVE_BOARD": rel,
        "CONCENTRATION": {
            "control_only_symbol": top_share(control_only),
            "incremental_symbol": top_share(incremental),
            "pair_removed_symbol": top_share([p.get("removed") or {} for p in pairs]),
        },
        "occupancy_sot_ok": bool(body.get("occupancy_sot_ok")),
        "occupancy_leftover_n": int(body.get("occupancy_leftover_n") or 0),
        "pairs": pairs,
        "control_only": control_only,
        "incremental": incremental,
        "common": list(body.get("common") or []),
    }


def decide(dev: dict[str, Any], fwd: dict[str, Any], *, leak_ok: bool, ni_ok: bool, identity_ok: bool) -> dict[str, Any]:
    occ_ok = bool(dev.get("occupancy_sot_ok") and fwd.get("occupancy_sot_ok"))
    leftover_ok = int(dev.get("occupancy_leftover_n") or 0) == 0 and int(fwd.get("occupancy_leftover_n") or 0) == 0
    integrity = bool(leak_ok and ni_ok and occ_ok and leftover_ok and identity_ok)
    d_rel = dict(dev.get("RELATIVE_BOARD") or {})
    f_rel = dict(fwd.get("RELATIVE_BOARD") or {})
    d_pair = dict(dev.get("PAIR_ECONOMICS") or {})
    f_pair = dict(fwd.get("PAIR_ECONOMICS") or {})
    d_ord = dict(dev.get("ORDER") or {})
    f_ord = dict(fwd.get("ORDER") or {})
    d_sem = dict(dev.get("HARD_REJECT_SEMANTIC") or {})
    direct = dict(d_sem.get("DIRECT_PRECAP_REMOVED") or {})
    hard_fail = True
    if int(direct.get("LOOKED_WRONG_TO_REMOVE_n") or 0) == 0 and int(direct.get("pos_pnl_n") or 0) == 0:
        hard_fail = False
    rel_dev = bool(d_rel.get("lower_adverse_aligns_with_higher_pnl")) and int(d_rel.get("pair_n") or 0) >= int(MIN_RELATIVE_PAIR_N_DEV)
    rel_fwd = bool(f_rel.get("lower_adverse_aligns_with_higher_pnl")) and int(f_rel.get("pair_n") or 0) >= int(MIN_RELATIVE_PAIR_N_FWD)
    rel_both = bool(rel_dev and rel_fwd)
    pair_worse_dev = (_f(d_pair.get("median_pair_delta")) or 0.0) < -1e-12
    pair_worse_fwd = (_f(f_pair.get("median_pair_delta")) or 0.0) < -1e-12
    later_dev = (_f(d_ord.get("median_elapsed_sec_removed_to_replacement")) or 0.0) > 1e-12
    later_fwd = (_f(f_ord.get("median_elapsed_sec_removed_to_replacement")) or 0.0) > 1e-12
    order_dev = bool(pair_worse_dev and later_dev)
    order_fwd = bool(pair_worse_fwd and later_fwd)
    order_main = bool(order_dev and (order_fwd or int(f_pair.get("pair_n") or 0) < int(MIN_RELATIVE_PAIR_N_FWD)))

    if not integrity:
        case, verdict = "E", "SIMPLE_TECH_PRE_CAP_HARD_REJECT_FAILURE_INVALID"
        nxt = "STOP. Integrity or identity reproduction failed."
    elif rel_both and not order_main:
        case, verdict = "A", "SIMPLE_TECH_PRE_CAP_RELATIVE_PRIORITY_MECHANISM_SUPPORTED"
        nxt = "STOP this RCA. Absolute veto stays failed. A later run may precommit one relative-priority candidate. Do not implement it here."
    elif order_main and not rel_both:
        case, verdict = "B", "SIMPLE_TECH_PRE_CAP_REPLACEMENT_ORDER_MECHANISM_FOUND"
        nxt = "STOP. Next research is candidate ordering / occupancy architecture, not a new board veto. Do not create a ranking formula in this family yet."
    elif not rel_both and not order_main:
        case, verdict = "D", "SIMPLE_TECH_PRE_CAP_BOARD_PATH_CLOSED"
        nxt = "STOP. Absolute veto failed and relative board priority is not supported. Close the Pre-CAP board family."
    else:
        case, verdict = "C", "SIMPLE_TECH_PRE_CAP_HARD_REJECT_FAILURE_MIXED"
        nxt = "STOP. Multiple mechanisms present; do not isolate a single next candidate."
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "HARD_REJECT_SEMANTIC_FAILURE": bool(hard_fail),
        "RELATIVE_ALIGNED_DEV": bool(rel_dev),
        "RELATIVE_ALIGNED_FWD": bool(rel_fwd),
        "RELATIVE_BOTH": bool(rel_both),
        "ORDER_MAIN": bool(order_main),
        "ORDER_DEV": bool(order_dev),
        "ORDER_FWD": bool(order_fwd),
        "IDENTITY_OK": bool(identity_ok),
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "RANKING_RULE_CREATED": False,
        "NEW_ENTRY_RULE": False,
        "DEV_PAIR_N": d_pair.get("pair_n"),
        "FWD_PAIR_N": f_pair.get("pair_n"),
        "DEV_MEDIAN_PAIR_DELTA": d_pair.get("median_pair_delta"),
        "FWD_MEDIAN_PAIR_DELTA": f_pair.get("median_pair_delta"),
        "DEV_TOTAL_PAIR_DELTA": d_pair.get("total_pair_delta"),
        "FWD_TOTAL_PAIR_DELTA": f_pair.get("total_pair_delta"),
        "DEV_DIRECT_REMOVAL_EFFECT": (dev.get("attribution") or {}).get("DIRECT_REMOVAL_EFFECT"),
        "DEV_DOWNSTREAM_OCCUPANCY_EFFECT": (dev.get("attribution") or {}).get("DOWNSTREAM_OCCUPANCY_EFFECT"),
        "FWD_DIRECT_REMOVAL_EFFECT": (fwd.get("attribution") or {}).get("DIRECT_REMOVAL_EFFECT"),
        "FWD_DOWNSTREAM_OCCUPANCY_EFFECT": (fwd.get("attribution") or {}).get("DOWNSTREAM_OCCUPANCY_EFFECT"),
    }


def identity_ok(dev: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    ident = dict(dev.get("identity") or {})
    got = {
        "COMMON_N": int(ident.get("COMMON_N") or 0),
        "CONTROL_ONLY_N": int(ident.get("CONTROL_ONLY_N") or 0),
        "INCREMENTAL_TREATMENT_N": int(ident.get("INCREMENTAL_TREATMENT_N") or 0),
    }
    exp = {
        "COMMON_N": int(DEV_COMMON_N_EXPECTED),
        "CONTROL_ONLY_N": int(DEV_CONTROL_ONLY_N_EXPECTED),
        "INCREMENTAL_TREATMENT_N": int(DEV_INCREMENTAL_N_EXPECTED),
    }
    ok = got == exp
    return ok, {"got": got, "expected": exp}
