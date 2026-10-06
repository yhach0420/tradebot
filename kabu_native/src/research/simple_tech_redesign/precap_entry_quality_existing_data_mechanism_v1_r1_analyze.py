"""Daily Spearman PRIMARY. Median split is diagnostic only. No threshold search. No candidate eval."""
from __future__ import annotations

from collections import defaultdict
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
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_analyze import (
    availability,
    empirical_quantile,
    median_split,
    next_threshold,
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_harvest import in_pre_cap_pool
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_r1_spec import (
    AVAILABILITY_MIN,
    CONCENTRATION_WARN,
    FEATURES,
    FEATURE_IDS,
    MEDIAN_SPLIT_ROLE,
    MIN_DAY_N,
    MISSING_POLICY_FROZEN,
    MISSING_POLICY_THIS_RUN,
    NEXT_CANDIDATE_ID_IF_CASE_A,
    NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED,
    PRIMARY_METHOD,
    THRESHOLD_POLICY,
)
from research.simple_tech_redesign.precap_marginal_quality_timing_confounding_rca_analyze import _ranks, spearman
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_analyze import _iqr
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_harvest import _f

EPS = 1e-12
BLOCK_KEYS = (
    "BLOCK_A_DISCOVERY",
    "BLOCK_B_INTERNAL_STABILITY",
    "BLOCK_C_BURNED_STRESS",
)
CAPTURE_STATES = ("ACTIVE_SAME_PID", "INACTIVE_EXPECTED", "UNKNOWN")


def _pool(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for body in bodies:
        for r in list(body.get("pool") or body.get("candidates") or []):
            if in_pre_cap_pool(r):
                out.append(r)
    return out


def _with_pnl(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if _f(r.get("session_close_pnl")) is not None]


def _finite_feat(rows: list[dict[str, Any]], feat: str) -> list[dict[str, Any]]:
    return [r for r in rows if _f(r.get(feat)) is not None]


def _usable(rows: list[dict[str, Any]], feat: str) -> list[dict[str, Any]]:
    return _finite_feat(_with_pnl(rows), feat)


def _ctrl_trades(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for body in bodies:
        out.extend(list((body.get("control") or {}).get("trades") or []))
    return out


def _sign(x: Optional[float]) -> Optional[int]:
    if x is None:
        return None
    if float(x) > 0:
        return 1
    if float(x) < 0:
        return -1
    return 0


def direction_label(median_rho: Optional[float]) -> str:
    s = _sign(median_rho)
    if s is None or s == 0:
        return "NO_DIRECTION"
    return "higher_is_better" if s > 0 else "lower_is_better"


def capture_reporting_state(pre: dict[str, Any], post: dict[str, Any]) -> dict[str, Any]:
    pre_pid = pre.get("CAPTURE_PID")
    post_pid = post.get("CAPTURE_PID")
    both_null = pre_pid is None and post_pid is None
    if both_null:
        state = "INACTIVE_EXPECTED"
    elif pre_pid is not None and post_pid is not None and pre_pid == post_pid:
        state = "ACTIVE_SAME_PID"
    else:
        state = "UNKNOWN"
    return {
        "CAPTURE_STATE": state,
        "PRE_CAPTURE_PID": pre_pid,
        "POST_CAPTURE_PID": post_pid,
        "CAPTURE_PID_UNCHANGED": pre_pid == post_pid,
        "CAPTURE_ALIVE_INFERRED_FROM_NULL_UNCHANGED": False,
        "null_equals_null_means_alive": False,
        "note": "null==null is not Capture alive. Session-end inactive is INACTIVE_EXPECTED.",
        "allowed_states": list(CAPTURE_STATES),
    }


def daily_spearman_table(rows: list[dict[str, Any]], feat: str, *, min_n: int = MIN_DAY_N) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in _usable(rows, feat):
        by[str(r.get("date") or "")].append(r)
    dates = sorted(by)
    out: list[dict[str, Any]] = []
    for day in dates:
        chunk = by[day]
        n = len(chunk)
        rec: dict[str, Any] = {"date": day, "n": n, "rho": None, "evaluable": False, "status": "UNEVALUABLE_SMALL_N"}
        if n < int(min_n):
            rec["status"] = "UNEVALUABLE_SMALL_N"
            out.append(rec)
            continue
        rho = spearman([float(r[feat]) for r in chunk], [float(r["session_close_pnl"]) for r in chunk])
        if rho is None:
            rec["status"] = "UNEVALUABLE_ZERO_VARIANCE"
            out.append(rec)
            continue
        rec["rho"] = float(rho)
        rec["evaluable"] = True
        rec["status"] = "OK"
        out.append(rec)
    return out


def aggregate_daily(table: list[dict[str, Any]]) -> dict[str, Any]:
    rhos = [float(r["rho"]) for r in table if r.get("evaluable") and r.get("rho") is not None]
    n_ev = len(rhos)
    med = float(median(rhos)) if rhos else None
    return {
        "evaluable_day_n": n_ev,
        "unevaluable_small_n_day_n": sum(1 for r in table if r.get("status") == "UNEVALUABLE_SMALL_N"),
        "unevaluable_zero_variance_day_n": sum(1 for r in table if r.get("status") == "UNEVALUABLE_ZERO_VARIANCE"),
        "positive_rho_day_n": sum(1 for x in rhos if x > 0),
        "negative_rho_day_n": sum(1 for x in rhos if x < 0),
        "zero_rho_day_n": sum(1 for x in rhos if x == 0),
        "median_daily_rho": med,
        "IQR_daily_rho": _iqr(rhos) if len(rhos) >= 2 else (0.0 if len(rhos) == 1 else None),
        "direction": direction_label(med),
        "sign": _sign(med),
        "unevaluable": bool(n_ev == 0),
        "daily": table,
    }


def pooled_spearman(rows: list[dict[str, Any]], feat: str) -> dict[str, Any]:
    usable = _usable(rows, feat)
    n = len(usable)
    rho = spearman([float(r[feat]) for r in usable], [float(r["session_close_pnl"]) for r in usable]) if n else None
    return {
        "n": n,
        "rho": rho,
        "evaluable": rho is not None,
        "role": "SECONDARY_ONLY",
        "used_for_primary_direction": False,
        "used_for_qualify": False,
    }


def _same_sign(a: Optional[int], b: Optional[int]) -> bool:
    return a is not None and b is not None and int(a) != 0 and int(a) == int(b)


def group_daily(rows: list[dict[str, Any]], feat: str, pred) -> dict[str, Any]:
    sub = [r for r in rows if pred(r)]
    agg = aggregate_daily(daily_spearman_table(sub, feat))
    agg["subset_n"] = len(sub)
    agg["subset_outcome_n"] = len(_usable(sub, feat))
    return agg


def day_rho_concentration(table: list[dict[str, Any]]) -> dict[str, Any]:
    ev = [r for r in table if r.get("evaluable") and r.get("rho") is not None]
    by_rho: dict[str, float] = {str(r["date"]): abs(float(r["rho"])) for r in ev}
    by_ev: dict[str, float] = {str(r["date"]): abs(float(r["rho"])) * float(r.get("n") or 0) for r in ev}

    def _top(by: dict[str, float]) -> dict[str, Any]:
        if not by:
            return {"top": None, "top_value": None, "share_of_abs": None, "warning_gt_50pct": False, "by": {}}
        den = float(sum(by.values()))
        top_k = max(by, key=lambda k: by[k])
        share = (by[top_k] / den) if den > EPS else None
        return {
            "top": top_k,
            "top_value": by[top_k],
            "share_of_abs": share,
            "warning_gt_50pct": bool(share is not None and share > float(CONCENTRATION_WARN)),
            "by": dict(by),
        }

    rho_c = _top(by_rho)
    ev_c = _top(by_ev)
    warn = bool(rho_c["warning_gt_50pct"] or ev_c["warning_gt_50pct"])
    return {
        "metric": "feature_daily_rho_abs_share",
        "rho_abs": rho_c,
        "rho_times_n": ev_c,
        "warning_gt_50pct": warn,
        "excluded": False,
    }


def symbol_pairwise_contribution(rows: list[dict[str, Any]], feat: str) -> dict[str, Any]:
    usable = _usable(rows, feat)
    empty = {
        "metric": "spearman_numerator_by_symbol",
        "n": len(usable),
        "top": None,
        "top_value": None,
        "share_of_abs": None,
        "warning_gt_50pct": False,
        "by": {},
        "excluded": False,
        "unevaluable": True,
    }
    if len(usable) < int(MIN_DAY_N):
        return empty
    xs = [float(r[feat]) for r in usable]
    ys = [float(r["session_close_pnl"]) for r in usable]
    rx, ry = _ranks(xs), _ranks(ys)
    n = len(rx)
    mx = sum(rx) / n
    my = sum(ry) / n
    by: dict[str, float] = defaultdict(float)
    denx = sum((a - mx) ** 2 for a in rx) ** 0.5
    deny = sum((b - my) ** 2 for b in ry) ** 0.5
    if denx <= EPS or deny <= EPS:
        empty["unevaluable"] = True
        empty["status"] = "UNEVALUABLE_ZERO_VARIANCE"
        return empty
    for r, a, b in zip(usable, rx, ry):
        by[str(r.get("symbol") or "")] += (a - mx) * (b - my)
    abs_den = float(sum(abs(v) for v in by.values()))
    if abs_den <= EPS:
        empty["unevaluable"] = False
        empty["by"] = dict(by)
        return empty
    top_k = max(by, key=lambda k: abs(by[k]))
    share = abs(by[top_k]) / abs_den
    return {
        "metric": "spearman_numerator_by_symbol",
        "n": len(usable),
        "top": top_k,
        "top_value": by[top_k],
        "share_of_abs": float(share),
        "warning_gt_50pct": bool(share > float(CONCENTRATION_WARN)),
        "by": dict(by),
        "excluded": False,
        "unevaluable": False,
    }


def evaluate_feature(feat: str, blocks: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    pack: dict[str, Any] = {
        "feature": feat,
        "feature_id": FEATURE_IDS[feat],
        "PRIMARY_METHOD": PRIMARY_METHOD,
        "SECONDARY_DIAGNOSTIC_ONLY": True,
        "median_split_used_for_qualify": False,
        "median_split_used_for_direction": False,
        "pooled_rho_used_for_qualify": False,
        "pooled_rho_used_for_primary_direction": False,
    }
    avails: dict[str, Any] = {}
    daily_blocks: dict[str, Any] = {}
    pooled_blocks: dict[str, Any] = {}
    split_diag: dict[str, Any] = {}
    for bk in BLOCK_KEYS:
        pool = _pool(blocks.get(bk) or [])
        avails[bk] = availability(pool, feat)
        daily_blocks[bk] = aggregate_daily(daily_spearman_table(pool, feat))
        pooled_blocks[bk] = pooled_spearman(pool, feat)
        ms = median_split(pool, feat)
        ms["SECONDARY_DIAGNOSTIC_ONLY"] = True
        split_diag[bk] = ms
    pack["availability"] = avails
    pack["daily"] = daily_blocks
    pack["pooled_spearman_secondary"] = pooled_blocks
    pack["median_split_diagnostic"] = split_diag
    a = daily_blocks["BLOCK_A_DISCOVERY"]
    b = daily_blocks["BLOCK_B_INTERNAL_STABILITY"]
    c = daily_blocks["BLOCK_C_BURNED_STRESS"]
    a_sign = a.get("sign")
    b_sign = b.get("sign")
    c_sign = c.get("sign")
    pack["direction_A"] = a.get("direction")
    pack["median_daily_rho_A"] = a.get("median_daily_rho")
    pack["higher_is_better"] = True if a_sign == 1 else (False if a_sign == -1 else None)
    pack["has_a_direction"] = bool(avails["BLOCK_A_DISCOVERY"].get("availability_ok") and a_sign not in (None, 0))
    pack["block_ab_same_direction"] = _same_sign(a_sign, b_sign)
    pack["block_c_clear_reversal"] = bool(a_sign not in (None, 0) and c_sign not in (None, 0) and c_sign == -a_sign)
    a_pool = _pool(blocks.get("BLOCK_A_DISCOVERY") or [])
    admitted = group_daily(a_pool, feat, lambda r: bool(r.get("control_admitted")))
    blocked = group_daily(a_pool, feat, lambda r: bool(r.get("cap_only_blocked")))
    for g in (admitted, blocked):
        if g.get("unevaluable"):
            g["status"] = "UNEVALUABLE"
            g["treated_as_opposite"] = False
        g["same_sign_as_A"] = _same_sign(a_sign, g.get("sign"))
    pack["admitted"] = admitted
    pack["cap_blocked"] = blocked
    pack["not_one_sided"] = bool(
        (not admitted.get("unevaluable"))
        and (not blocked.get("unevaluable"))
        and _same_sign(a_sign, admitted.get("sign"))
        and _same_sign(a_sign, blocked.get("sign"))
    )
    core = group_daily(a_pool, feat, lambda r: str(r.get("fill_role") or "") == "CORE")
    added = group_daily(a_pool, feat, lambda r: str(r.get("fill_role") or "") == "ADDED")
    core["small_n_warning"] = bool(int(core.get("evaluable_day_n") or 0) < 3)
    if added.get("unevaluable"):
        added["status"] = "UNEVALUABLE"
        added["treated_as_opposite"] = False
    added["same_sign_as_A"] = _same_sign(a_sign, added.get("sign"))
    pack["core"] = core
    pack["added"] = added
    pack["added_same_direction"] = bool(added.get("same_sign_as_A"))
    strata = {}
    strata_ok = []
    for name in ("FIRST", "MIDDLE", "LAST"):
        sub = [r for r in a_pool if str(r.get("arrival_third") or "") == name]
        pooled = pooled_spearman(sub, feat)
        daily = aggregate_daily(daily_spearman_table(sub, feat))
        pooled_sign = _sign(pooled.get("rho"))
        same = _same_sign(a_sign, pooled_sign)
        strata[name] = {
            "pooled_spearman": pooled,
            "daily_spearman_secondary": daily,
            "same_sign_as_A": same,
        }
        if same:
            strata_ok.append(name)
    pack["arrival_strata"] = strata
    pack["arrival_same_direction_n"] = len(strata_ok)
    pack["arrival_multi_same_direction"] = len(strata_ok) >= 2
    day_c = day_rho_concentration(list(a.get("daily") or []))
    sym_c = symbol_pairwise_contribution(a_pool, feat)
    pack["concentration"] = {
        "day": day_c,
        "symbol": sym_c,
        "warning_gt_50pct": bool(day_c.get("warning_gt_50pct") or sym_c.get("warning_gt_50pct")),
        "population_pnl_copied": False,
        "excluded": False,
    }
    pack["availability_ok"] = bool(avails["BLOCK_A_DISCOVERY"].get("availability_ok"))
    confound = {
        "one_sided": bool(pack["has_a_direction"] and not pack["not_one_sided"]),
        "concentration": bool(pack["has_a_direction"] and pack["concentration"]["warning_gt_50pct"]),
        "arrival_only": bool(pack["has_a_direction"] and not pack["arrival_multi_same_direction"]),
        "added_opposite_or_unevaluable": bool(pack["has_a_direction"] and not pack["added_same_direction"]),
    }
    pack["confound"] = confound
    pack["confounded"] = any(confound.values())
    pack["robust_fail"] = {
        "ab_direction_mismatch": bool(pack["has_a_direction"] and not pack["block_ab_same_direction"]),
        "c_clear_reversal": bool(pack["has_a_direction"] and pack["block_c_clear_reversal"]),
    }
    pack["qualify"] = bool(
        pack["availability_ok"]
        and pack["has_a_direction"]
        and pack["block_ab_same_direction"]
        and (not pack["block_c_clear_reversal"])
        and pack["added_same_direction"]
        and pack["not_one_sided"]
        and pack["arrival_multi_same_direction"]
        and (not pack["concentration"]["warning_gt_50pct"])
    )
    a_vals = [float(r[feat]) for r in _finite_feat(a_pool, feat)]
    pack["block_a_q30"] = empirical_quantile(a_vals, 0.30)
    pack["block_a_q70"] = empirical_quantile(a_vals, 0.70)
    pack["q30_q70_used_for_qualify"] = False
    pack["missing_impute_accept"] = False
    pack["missing_impute_reject"] = False
    pack["missing_policy_this_run"] = MISSING_POLICY_THIS_RUN
    pack["qualify_gates"] = {
        "availability_ge_80": pack["availability_ok"],
        "a_has_direction": pack["has_a_direction"],
        "ab_same_direction": pack["block_ab_same_direction"],
        "c_clear_reversal_false": not pack["block_c_clear_reversal"],
        "added_same_direction": pack["added_same_direction"],
        "admitted_and_cap_blocked_same_direction": pack["not_one_sided"],
        "arrival_at_least_2_strata": pack["arrival_multi_same_direction"],
        "no_feature_concentration_gt_50": not pack["concentration"]["warning_gt_50pct"],
    }
    return pack


def _concentration_fingerprint(feat_pack: dict[str, Any]) -> tuple:
    conc = dict(feat_pack.get("concentration") or {})
    day = dict((conc.get("day") or {}).get("rho_abs") or {})
    sym = dict(conc.get("symbol") or {})
    return (
        day.get("top"),
        round(float(day.get("share_of_abs") or 0.0), 12) if day.get("share_of_abs") is not None else None,
        sym.get("top"),
        round(float(sym.get("share_of_abs") or 0.0), 12) if sym.get("share_of_abs") is not None else None,
    )


def evaluate(blocks: dict[str, list[dict[str, Any]]], *, harvested: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    features = [evaluate_feature(feat, blocks) for feat in FEATURES]
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
    fps = [_concentration_fingerprint(f) for f in features]
    identical_conc = bool(len(set(fps)) == 1 and features)
    harvested = harvested or {}
    pool_id = dict(harvested.get("pool_identity") or {})
    attrition = dict(harvested.get("attrition") or {})
    unexplained = int(attrition.get("unexplained_n") or 0)
    identity_ok = bool(identity_ab.get("ok") and identity_c.get("ok") and leftover_ok and sot_ok and pool_id.get("pool_identity_ok", True))
    methodology_ok = all(
        (not f.get("median_split_used_for_qualify"))
        and (not f.get("median_split_used_for_direction"))
        and (not f.get("pooled_rho_used_for_qualify"))
        and f.get("PRIMARY_METHOD") == PRIMARY_METHOD
        for f in features
    )
    missing = {feat: dict(next(f for f in features if f["feature"] == feat).get("availability") or {}) for feat in FEATURES}
    avail_all_100 = all(
        abs(float(((missing[feat].get(bk) or {}).get("availability") or 0.0)) - 1.0) < 1e-12
        and int((missing[feat].get(bk) or {}).get("missing_n") or 0) == 0
        for feat in FEATURES
        for bk in BLOCK_KEYS
    )
    return {
        "features": features,
        "identity_AB_as_development": identity_ab,
        "identity_C_as_forward_burned": identity_c,
        "leftover_ok": leftover_ok,
        "control_sot_ok": sot_ok,
        "identity_ok": identity_ok,
        "pool_identity": pool_id,
        "attrition": attrition,
        "missing": missing,
        "availability_all_100pct": avail_all_100,
        "concentration_identical_across_features": identical_conc,
        "concentration_fingerprints": fps,
        "methodology_ok": methodology_ok,
        "attrition_unexplained_n": unexplained,
        "independent_alpha_claim": False,
        "feature_discovery": False,
        "threshold_search": False,
        "PRIMARY_METHOD": PRIMARY_METHOD,
        "MEDIAN_SPLIT_ROLE": MEDIAN_SPLIT_ROLE,
        "capture_restreamed": bool(harvested.get("capture_restreamed")),
    }


def decide(pack: dict[str, Any], *, leak_ok: bool) -> dict[str, Any]:
    unexplained = int(pack.get("attrition_unexplained_n") or 0)
    pool_ok = bool((pack.get("pool_identity") or {}).get("pool_identity_ok", True))
    methodology_ok = bool(pack.get("methodology_ok", True))
    identical_conc = bool(pack.get("concentration_identical_across_features"))
    integrity = bool(
        leak_ok
        and pack.get("identity_ok")
        and pool_ok
        and methodology_ok
        and unexplained == 0
        and (not identical_conc)
        and (not pack.get("capture_restreamed"))
    )
    integrity_reasons = []
    if not leak_ok:
        integrity_reasons.append("LEAK")
    if not pack.get("identity_ok"):
        integrity_reasons.append("IDENTITY_MISMATCH")
    if not pool_ok:
        integrity_reasons.append("POOL_IDENTITY_MISMATCH")
    if not methodology_ok:
        integrity_reasons.append("METHODOLOGY_MISMATCH")
    if unexplained:
        integrity_reasons.append("ATTRITION_UNEXPLAINED")
    if identical_conc:
        integrity_reasons.append("CONCENTRATION_IDENTICAL_ACROSS_FEATURES")
    if pack.get("capture_restreamed"):
        integrity_reasons.append("CAPTURE_RESTREAM")
    feats = list(pack.get("features") or [])
    qualified = [f for f in feats if f.get("qualify")]
    confounded = [f for f in feats if f.get("has_a_direction") and f.get("confounded") and not f.get("qualify")]
    selected = qualified[0] if qualified else None
    if not integrity:
        case, verdict, nxt, primary_next = (
            "D",
            "SIMPLE_TECH_PRECAP_EXISTING_DATA_INTEGRITY_FAILED",
            "FAIL_CLOSED. Do not create a filter. Do not go prospective.",
            "FAIL_CLOSED",
        )
        selected = None
        qualified = []
    elif selected is not None:
        case, verdict, nxt, primary_next = (
            "A",
            "SIMPLE_TECH_PRECAP_EXISTING_DATA_SINGLE_MECHANISM_FOUND",
            "Do not go prospective. Next: existing-data Full Causal Portfolio for PRECAP_ENTRY_QUALITY_SINGLE_FEATURE_V1 with frozen BLOCK_A worst-30pct threshold. Do not evaluate that candidate in this run.",
            "PRECAP_ENTRY_QUALITY_SINGLE_FEATURE_V1",
        )
    elif confounded:
        case, verdict, nxt, primary_next = (
            "C",
            "SIMPLE_TECH_PRECAP_EXISTING_DATA_CONFOUNDED",
            "Single-feature absolute filter family CLOSE. Do not create a filter. Move to a different existing-data architecture. Do not go prospective.",
            "CLOSE_SINGLE_FEATURE_ABSOLUTE_FILTER_FAMILY",
        )
    else:
        case, verdict, nxt, primary_next = (
            "B",
            "SIMPLE_TECH_PRECAP_EXISTING_DATA_NO_ROBUST_MECHANISM",
            "Single-feature absolute filter family CLOSE. Close the 4-feature family. Do not go prospective. Next: a different existing-data causal ENTRY mechanism family.",
            "CLOSE_SINGLE_FEATURE_ABSOLUTE_FILTER_FAMILY",
        )
    thr = next_threshold(selected) if selected is not None else next_threshold({})
    thr["used_for_qualify"] = False
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "PRIMARY_NEXT_MECHANISM": primary_next,
        "selected_feature": None if selected is None else selected.get("feature"),
        "selected_feature_id": None if selected is None else selected.get("feature_id"),
        "higher_is_better": None if selected is None else selected.get("higher_is_better"),
        "qualified_features": [f.get("feature") for f in qualified],
        "confounded_features": [f.get("feature") for f in confounded],
        "NEXT_THRESHOLD": thr,
        "MISSING_POLICY_FROZEN": MISSING_POLICY_FROZEN,
        "MISSING_POLICY_THIS_RUN": MISSING_POLICY_THIS_RUN,
        "NEXT_CANDIDATE_ID_IF_CASE_A": NEXT_CANDIDATE_ID_IF_CASE_A if case == "A" else None,
        "NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED": False,
        "candidate_evaluated_this_run": False,
        "INDEPENDENT_ALPHA_FAMILY": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "CANDIDATE_FROZEN": False,
        "PROSPECTIVE_ARMED": False,
        "PROSPECTIVE_HARVEST_SUSPENDED": True,
        "FUTURE_DATA_USED": False,
        "BURNED_EXISTING_DATA_ONLY": True,
        "NEW_ENTRY_FILTER": False,
        "NEW_EXIT_RULE": False,
        "CAP_CHANGED": False,
        "THRESHOLD_SEARCH": False,
        "FEATURE_DISCOVERY": False,
        "OBSERVED_ECONOMIC_BOTTLENECK": "MARGINAL_ENTRY_QUALITY",
        "INTRINSIC_MECHANISM_CONFIRMED": False,
        "PRIMARY_MECHANISM_FROZEN": "OUTLIER_DOMINATED",
        "PRIMARY_METHOD": PRIMARY_METHOD,
        "SECONDARY_DIAGNOSTIC_ONLY": True,
        "integrity": integrity,
        "integrity_reasons": integrity_reasons,
        "NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED_FLAG": bool(NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED),
        "SUPERSEDED_FOR_DECISION_PRIOR": True,
        "THRESHOLD_POLICY": THRESHOLD_POLICY,
        "AVAILABILITY_MIN": AVAILABILITY_MIN,
    }
