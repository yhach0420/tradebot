"""Daily Spearman on P2_TOUCH_AGE_BARS. Hypothesis direction is frozen. No absolute-feature reopen."""
from __future__ import annotations

from collections import Counter
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
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_harvest import in_pre_cap_pool
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_r1_analyze import (
    aggregate_daily,
    daily_spearman_table,
    day_rho_concentration,
    group_daily,
    pooled_spearman,
    symbol_pairwise_contribution,
)
from research.simple_tech_redesign.precap_t3_setup_sequence_mechanism_v1_spec import (
    ALLOWED_AGES,
    CANDIDATE_EVALUATED_THIS_RUN,
    CONCENTRATION_WARN,
    EXPECTED_SPEARMAN_SIGN,
    HYPOTHESIS_DIRECTION,
    MIN_DAY_N,
    NEXT_CANDIDATE_ACCEPT_AGES,
    NEXT_CANDIDATE_ID,
    NEXT_CANDIDATE_REJECT_AGE,
    NEXT_CANDIDATE_RULE,
    PRIMARY_FIELD,
    PRIMARY_HYPOTHESIS,
    PRIMARY_METHOD,
    TOUCH_COUNT_ROLE,
)
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_analyze import quality_pack
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_harvest import _f

BLOCK_KEYS = (
    "BLOCK_A_DISCOVERY",
    "BLOCK_B_INTERNAL_STABILITY",
    "BLOCK_C_BURNED_STRESS",
)
FEAT = PRIMARY_FIELD


def _pool(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for body in bodies:
        for r in list(body.get("pool") or []):
            if in_pre_cap_pool(r):
                out.append(r)
    return out


def _ctrl_trades(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for body in bodies:
        out.extend(list((body.get("control") or {}).get("trades") or []))
    return out


def _age_dist(rows: list[dict[str, Any]]) -> dict[str, Any]:
    c = Counter(int(r[FEAT]) for r in rows if r.get(FEAT) in ALLOWED_AGES)
    return {"AGE0": int(c.get(0, 0)), "AGE1": int(c.get(1, 0)), "AGE2": int(c.get(2, 0)), "n": sum(c.values())}


def _econ_by_age(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    usable = [r for r in rows if _f(r.get("session_close_pnl")) is not None and r.get(FEAT) in ALLOWED_AGES]
    for age in ALLOWED_AGES:
        xs = [float(r["session_close_pnl"]) for r in usable if int(r[FEAT]) == int(age)]
        pack = quality_pack(xs)
        pack["age"] = int(age)
        out[f"AGE{age}"] = pack
    return out


def _negative(agg: dict[str, Any]) -> bool:
    med = agg.get("median_daily_rho")
    return (not agg.get("unevaluable")) and med is not None and float(med) < 0.0


def _positive(agg: dict[str, Any]) -> bool:
    med = agg.get("median_daily_rho")
    return (not agg.get("unevaluable")) and med is not None and float(med) > 0.0


def _gate6(admitted: dict[str, Any], blocked: dict[str, Any]) -> dict[str, Any]:
    adm_un = bool(admitted.get("unevaluable"))
    blk_un = bool(blocked.get("unevaluable"))
    adm_neg = _negative(admitted)
    blk_neg = _negative(blocked)
    adm_pos = _positive(admitted)
    blk_pos = _positive(blocked)
    both_neg = bool(adm_neg and blk_neg)
    one_un = bool((adm_un and (not blk_un) and blk_neg) or (blk_un and (not adm_un) and adm_neg))
    opposite = bool(adm_pos or blk_pos)
    return {
        "both_negative": both_neg,
        "one_unevaluable_other_negative": one_un,
        "opposite": opposite,
        "pass": bool(both_neg or one_un),
        "admitted_status": "UNEVALUABLE" if adm_un else ("negative" if adm_neg else ("positive" if adm_pos else "NO_DIRECTION")),
        "blocked_status": "UNEVALUABLE" if blk_un else ("negative" if blk_neg else ("positive" if blk_pos else "NO_DIRECTION")),
    }


def evaluate(blocks: dict[str, list[dict[str, Any]]], *, harvested: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    harvested = harvested or {}
    pools = {bk: _pool(blocks.get(bk) or []) for bk in BLOCK_KEYS}
    daily = {bk: aggregate_daily(daily_spearman_table(pools[bk], FEAT, min_n=MIN_DAY_N)) for bk in BLOCK_KEYS}
    pooled = {bk: pooled_spearman(pools[bk], FEAT) for bk in BLOCK_KEYS}
    a_pool = pools["BLOCK_A_DISCOVERY"]
    ages = {bk: _age_dist(pools[bk]) for bk in BLOCK_KEYS}
    no_fill = [r for bk in BLOCK_KEYS for r in pools[bk] if _f(r.get("session_close_pnl")) is None]
    econ = {bk: _econ_by_age(pools[bk]) for bk in BLOCK_KEYS}
    admitted = group_daily(a_pool, FEAT, lambda r: bool(r.get("control_admitted")))
    blocked = group_daily(a_pool, FEAT, lambda r: bool(r.get("cap_only_blocked")))
    gate6 = _gate6(admitted, blocked)
    core = group_daily(a_pool, FEAT, lambda r: str(r.get("fill_role") or "") == "CORE")
    added = group_daily(a_pool, FEAT, lambda r: str(r.get("fill_role") or "") == "ADDED")
    core["small_n_warning"] = bool(int(core.get("evaluable_day_n") or 0) < 3)
    added_neg = _negative(added)
    strata = {}
    neg_strata = []
    for name in ("FIRST", "MIDDLE", "LAST"):
        sub = [r for r in a_pool if str(r.get("arrival_third") or "") == name]
        ps = pooled_spearman(sub, FEAT)
        rho = ps.get("rho")
        is_neg = rho is not None and float(rho) < 0.0
        strata[name] = {"pooled_spearman": ps, "negative": is_neg}
        if is_neg:
            neg_strata.append(name)
    day_c = day_rho_concentration(list(daily["BLOCK_A_DISCOVERY"].get("daily") or []))
    sym_c = symbol_pairwise_contribution(a_pool, FEAT)
    touch_count = {
        "role": TOUCH_COUNT_ROLE,
        "by_block": {bk: dict(Counter(int(r["P2_TOUCH_COUNT"]) for r in pools[bk] if r.get("P2_TOUCH_COUNT") in (1, 2, 3))) for bk in BLOCK_KEYS},
        "used_for_qualify": False,
        "used_for_rule": False,
    }
    geometry = {
        "role": "SECONDARY_DIAGNOSTIC_ONLY",
        "used_for_verdict": False,
        "by_age": {},
    }
    a_usable = [r for r in a_pool if r.get(FEAT) in ALLOWED_AGES]
    for age in ALLOWED_AGES:
        chunk = [r for r in a_usable if int(r[FEAT]) == int(age)]
        locs = [_f(r.get("SIGNAL_CLOSE_LOCATION")) for r in chunk]
        locs_ok = [float(x) for x in locs if x is not None]
        rbps = [_f(r.get("SETUP_RANGE_BPS")) for r in chunk]
        rbps_ok = [float(x) for x in rbps if x is not None]
        geometry["by_age"][f"AGE{age}"] = {
            "n": len(chunk),
            "close_location_mean": (sum(locs_ok) / len(locs_ok)) if locs_ok else None,
            "range_bps_mean": (sum(rbps_ok) / len(rbps_ok)) if rbps_ok else None,
        }
    identity_ab = identity_check(
        _ctrl_trades((blocks.get("BLOCK_A_DISCOVERY") or []) + (blocks.get("BLOCK_B_INTERNAL_STABILITY") or [])),
        fill_n=DEV_FILL_N,
        core_n=DEV_CORE_N,
        added_n=DEV_ADDED_N,
        pnl=DEV_PNL,
    )
    identity_c = identity_check(
        _ctrl_trades(blocks.get("BLOCK_C_BURNED_STRESS") or []),
        fill_n=FWD_FILL_N,
        core_n=FWD_CORE_N,
        added_n=FWD_ADDED_N,
        pnl=FWD_PNL,
    )
    leftover_ok = all(bool(b.get("leftover_ok")) for bk in BLOCK_KEYS for b in list(blocks.get(bk) or []))
    sot_ok = all(bool(b.get("control_sot_ok")) for bk in BLOCK_KEYS for b in list(blocks.get(bk) or []))
    pool_id = dict(harvested.get("pool_identity") or {})
    seq = dict(harvested.get("sequence_integrity") or {})
    a_neg = _negative(daily["BLOCK_A_DISCOVERY"])
    b_neg = _negative(daily["BLOCK_B_INTERNAL_STABILITY"])
    c_pos_rev = _positive(daily["BLOCK_C_BURNED_STRESS"])
    conc_ok = (not bool(day_c.get("warning_gt_50pct"))) and (not bool(sym_c.get("warning_gt_50pct")))
    arrival_ok = len(neg_strata) >= 2
    gates = {
        "exact_reference_integrity": bool(seq.get("recovered_all")),
        "block_a_median_daily_rho_lt_0": a_neg,
        "block_b_median_daily_rho_lt_0": b_neg,
        "block_c_no_clear_positive_reversal": not c_pos_rev,
        "added_median_rho_lt_0": added_neg,
        "admitted_blocked_negative_or_one_unevaluable": bool(gate6.get("pass")),
        "arrival_at_least_2_negative": arrival_ok,
        "day_concentration_le_50": not bool(day_c.get("warning_gt_50pct")),
        "symbol_concentration_le_50": not bool(sym_c.get("warning_gt_50pct")),
    }
    qualify = all(gates.values())
    identity_ok = bool(
        identity_ab.get("ok")
        and identity_c.get("ok")
        and leftover_ok
        and sot_ok
        and pool_id.get("pool_identity_ok", True)
        and pool_id.get("outcome_n_ok", True)
        and pool_id.get("attrition_n_ok", True)
    )
    return {
        "PRIMARY_FIELD": FEAT,
        "PRIMARY_HYPOTHESIS": PRIMARY_HYPOTHESIS,
        "HYPOTHESIS_DIRECTION": HYPOTHESIS_DIRECTION,
        "EXPECTED_SPEARMAN_SIGN": EXPECTED_SPEARMAN_SIGN,
        "FLIP_DIRECTION_FROM_RESULTS": False,
        "PRIMARY_METHOD": PRIMARY_METHOD,
        "daily": daily,
        "pooled_spearman_secondary": pooled,
        "age_distribution": ages,
        "no_fill_age_distribution": _age_dist(no_fill),
        "no_fill_n": len(no_fill),
        "economics": econ,
        "admitted": admitted,
        "cap_blocked": blocked,
        "gate6": gate6,
        "core": core,
        "added": added,
        "added_negative": added_neg,
        "arrival_strata": strata,
        "arrival_negative_n": len(neg_strata),
        "concentration": {
            "day": day_c,
            "symbol": sym_c,
            "warning_gt_50pct": bool(day_c.get("warning_gt_50pct") or sym_c.get("warning_gt_50pct")),
            "excluded": False,
        },
        "touch_count_diagnostic": touch_count,
        "signal_bar_touch_diagnostic": {
            "role": "SECONDARY_DIAGNOSTIC_ONLY",
            "n_true": sum(1 for r in a_pool if r.get("SIGNAL_BAR_TOUCH") is True),
            "n_false": sum(1 for r in a_pool if r.get("SIGNAL_BAR_TOUCH") is False),
            "used_for_rule": False,
        },
        "geometry_diagnostic": geometry,
        "qualify_gates": gates,
        "qualify": bool(qualify),
        "identity_AB_as_development": identity_ab,
        "identity_C_as_forward_burned": identity_c,
        "leftover_ok": leftover_ok,
        "control_sot_ok": sot_ok,
        "identity_ok": identity_ok,
        "pool_identity": pool_id,
        "sequence_integrity": seq,
        "hypothesis_supported_A": a_neg,
        "block_ab_both_negative": bool(a_neg and b_neg),
        "c_clear_positive_reversal": c_pos_rev,
        "confound": {
            "admitted_blocked_opposite": bool(gate6.get("opposite")),
            "arrival": bool(a_neg and not arrival_ok),
            "added_reversed": bool(a_neg and _positive(added)),
        },
        "independent_alpha_claim": False,
        "closed_absolute_features_reopened": False,
        "candidate_evaluated_this_run": False,
        "threshold_search": False,
        "concentration_ok": conc_ok,
    }


def decide(pack: dict[str, Any], *, leak_ok: bool) -> dict[str, Any]:
    seq = dict(pack.get("sequence_integrity") or {})
    integrity = bool(
        leak_ok
        and pack.get("identity_ok")
        and seq.get("recovered_all")
        and int(seq.get("unknown_n") or 0) == 0
        and int(seq.get("future_bar_use_n") or 0) == 0
        and (not pack.get("closed_absolute_features_reopened"))
    )
    reasons = []
    if not leak_ok:
        reasons.append("LEAK")
    if not pack.get("identity_ok"):
        reasons.append("IDENTITY_MISMATCH")
    if not seq.get("recovered_all"):
        reasons.append("P2_REFERENCE_INCOMPLETE")
    if int(seq.get("unknown_n") or 0):
        reasons.append("SEQUENCE_UNKNOWN")
    if int(seq.get("future_bar_use_n") or 0):
        reasons.append("FUTURE_BAR_USE")
    qualify = bool(pack.get("qualify"))
    confound = dict(pack.get("confound") or {})
    confounded = bool(pack.get("hypothesis_supported_A") and any(confound.values()))
    frozen_rule = {
        "candidate_id": NEXT_CANDIDATE_ID,
        "rule": NEXT_CANDIDATE_RULE,
        "accept_ages": list(NEXT_CANDIDATE_ACCEPT_AGES),
        "reject_age": int(NEXT_CANDIDATE_REJECT_AGE),
        "evaluated_this_run": False,
        "alternative_thresholds_tried": False,
    }
    if not integrity:
        case, verdict, nxt, primary_next = (
            "D",
            "SIMPLE_TECH_PRECAP_T3_PULLBACK_FRESHNESS_INTEGRITY_FAILED",
            "FAIL_CLOSED. Do not create a filter. Do not go prospective.",
            "FAIL_CLOSED",
        )
    elif qualify:
        case, verdict, nxt, primary_next = (
            "A",
            "SIMPLE_TECH_PRECAP_T3_PULLBACK_FRESHNESS_MECHANISM_FOUND",
            "Do not go prospective. Next: existing-data Full Causal Portfolio for PRECAP_T3_PULLBACK_FRESHNESS_V1 (AGE2 reject, AGE0/1 accept). Do not evaluate that candidate in this run.",
            PRIMARY_FIELD,
        )
    elif confounded:
        case, verdict, nxt, primary_next = (
            "C",
            "SIMPLE_TECH_PRECAP_T3_PULLBACK_FRESHNESS_CONFOUNDED",
            "Architecture CLOSE. Do not create a filter. Do not go prospective. Next: a different existing-data ENTRY architecture.",
            "CLOSE_T3_PULLBACK_FRESHNESS_ARCHITECTURE",
        )
    else:
        case, verdict, nxt, primary_next = (
            "B",
            "SIMPLE_TECH_PRECAP_T3_PULLBACK_FRESHNESS_NOT_SUPPORTED",
            "Architecture CLOSE. A/B stability or hypothesized direction not supported. Do not go prospective. Next: a different existing-data ENTRY architecture.",
            "CLOSE_T3_PULLBACK_FRESHNESS_ARCHITECTURE",
        )
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "PRIMARY_NEXT_MECHANISM": primary_next,
        "qualify": bool(qualify) if integrity else False,
        "HYPOTHESIS_DIRECTION": HYPOTHESIS_DIRECTION,
        "FLIP_DIRECTION_FROM_RESULTS": False,
        "NEXT_CANDIDATE": frozen_rule,
        "candidate_evaluated_this_run": bool(CANDIDATE_EVALUATED_THIS_RUN),
        "NEW_ENTRY_FILTER": False,
        "NEW_EXIT_RULE": False,
        "CAP_CHANGED": False,
        "THRESHOLD_SEARCH": False,
        "FEATURE_DISCOVERY": False,
        "INDEPENDENT_ALPHA_FAMILY": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "CANDIDATE_FROZEN": True,
        "PROSPECTIVE_ARMED": False,
        "PROSPECTIVE_HARVEST_SUSPENDED": True,
        "FUTURE_DATA_USED": False,
        "BURNED_EXISTING_DATA_ONLY": True,
        "ABSOLUTE_FEATURE_FAMILY_CLOSED": True,
        "integrity": integrity,
        "integrity_reasons": reasons,
        "PRIMARY_FIELD": FEAT,
        "PRIMARY_METHOD": PRIMARY_METHOD,
    }
