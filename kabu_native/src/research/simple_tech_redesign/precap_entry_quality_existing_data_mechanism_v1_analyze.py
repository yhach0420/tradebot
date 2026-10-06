"""Select at most one incremental ENTRY quality feature. No threshold search. No candidate eval."""
from __future__ import annotations

from statistics import median
from typing import Any, Optional

import numpy as np

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
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_spec import (
    AVAILABILITY_MIN,
    CONCENTRATION_WARN,
    FEATURES,
    FEATURE_IDS,
    MIN_SPLIT_N,
    MISSING_POLICY_FROZEN,
    MISSING_POLICY_THIS_RUN,
    NEXT_CANDIDATE_ID_IF_CASE_A,
    NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED,
    THRESHOLD_POLICY,
    WORST_REJECT_PCT,
)
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_analyze import conc_share, quality_pack
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_harvest import _f

EPS = 1e-9
BLOCK_KEYS = (
    "BLOCK_A_DISCOVERY",
    "BLOCK_B_INTERNAL_STABILITY",
    "BLOCK_C_BURNED_STRESS",
)


def _pool(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for body in bodies:
        rows = list(body.get("pool") or body.get("candidates") or [])
        for r in rows:
            if in_pre_cap_pool(r):
                out.append(r)
    return out


def _with_pnl(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if _f(r.get("session_close_pnl")) is not None]


def _finite_feat(rows: list[dict[str, Any]], feat: str) -> list[dict[str, Any]]:
    return [r for r in rows if _f(r.get(feat)) is not None]


def _ctrl_trades(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for body in bodies:
        out.extend(list((body.get("control") or {}).get("trades") or []))
    return out


def empirical_quantile(xs: list[float], q: float) -> Optional[float]:
    if not xs:
        return None
    return float(np.percentile(np.asarray(xs, dtype=float), float(q) * 100.0))


def median_split(rows: list[dict[str, Any]], feat: str) -> dict[str, Any]:
    usable = _with_pnl(_finite_feat(rows, feat))
    empty = {
        "n": len(usable),
        "median": None,
        "direction": 0,
        "higher_is_better": None,
        "high_econ": quality_pack([]),
        "low_econ": quality_pack([]),
        "delta_mean": None,
        "high_n": 0,
        "low_n": 0,
    }
    if len(usable) < int(MIN_SPLIT_N) * 2:
        return empty
    vals = [float(r[feat]) for r in usable]
    if max(vals) - min(vals) <= EPS:
        empty["median"] = float(vals[0])
        return empty
    med = float(median(vals))
    high = [r for r in usable if float(r[feat]) >= med]
    low = [r for r in usable if float(r[feat]) < med]
    he = quality_pack([float(r["session_close_pnl"]) for r in high])
    le = quality_pack([float(r["session_close_pnl"]) for r in low])
    hm, lm = he.get("mean_pnl"), le.get("mean_pnl")
    delta = (float(hm) - float(lm)) if hm is not None and lm is not None else None
    direction = 0
    if delta is not None and abs(float(delta)) > EPS and len(high) >= int(MIN_SPLIT_N) and len(low) >= int(MIN_SPLIT_N):
        direction = 1 if float(delta) > 0 else -1
    return {
        "n": len(usable),
        "median": med,
        "direction": int(direction),
        "higher_is_better": True if direction > 0 else (False if direction < 0 else None),
        "high_econ": he,
        "low_econ": le,
        "delta_mean": delta,
        "high_n": len(high),
        "low_n": len(low),
    }


def availability(pool: list[dict[str, Any]], feat: str) -> dict[str, Any]:
    n = len(pool)
    finite = _finite_feat(pool, feat)
    miss = n - len(finite)
    avail = (len(finite) / n) if n else 0.0
    return {
        "pool_n": n,
        "finite_n": len(finite),
        "missing_n": miss,
        "availability": avail,
        "availability_ok": bool(avail >= float(AVAILABILITY_MIN)),
    }


def _dir_same(a: int, b: int) -> bool:
    return int(a) != 0 and int(a) == int(b)


def concentration_warning(rows: list[dict[str, Any]]) -> dict[str, Any]:
    day = conc_share(rows, key="date", pnl_key="session_close_pnl")
    sym = conc_share(rows, key="symbol", pnl_key="session_close_pnl")
    day["warning_gt_50pct"] = bool(float(day.get("share_of_abs") or 0.0) > float(CONCENTRATION_WARN))
    sym["warning_gt_50pct"] = bool(float(sym.get("share_of_abs") or 0.0) > float(CONCENTRATION_WARN))
    return {"day": day, "symbol": sym, "warning_gt_50pct": bool(day["warning_gt_50pct"] or sym["warning_gt_50pct"])}


def evaluate_feature(feat: str, blocks: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    pack: dict[str, Any] = {"feature": feat, "feature_id": FEATURE_IDS[feat]}
    splits: dict[str, Any] = {}
    avails: dict[str, Any] = {}
    for bk in BLOCK_KEYS:
        pool = _pool(blocks.get(bk) or [])
        avails[bk] = availability(pool, feat)
        splits[bk] = median_split(pool, feat)
    pack["availability"] = avails
    pack["splits"] = splits
    a_dir = int(splits["BLOCK_A_DISCOVERY"].get("direction") or 0)
    b_dir = int(splits["BLOCK_B_INTERNAL_STABILITY"].get("direction") or 0)
    c_dir = int(splits["BLOCK_C_BURNED_STRESS"].get("direction") or 0)
    pack["direction_A"] = a_dir
    pack["higher_is_better"] = True if a_dir > 0 else (False if a_dir < 0 else None)
    pack["block_ab_same_direction"] = _dir_same(a_dir, b_dir)
    pack["block_c_clear_reversal"] = bool(a_dir != 0 and c_dir != 0 and c_dir == -a_dir)
    a_pool = _pool(blocks.get("BLOCK_A_DISCOVERY") or [])
    a_pnl = _with_pnl(_finite_feat(a_pool, feat))
    added_split = median_split([r for r in a_pnl if str(r.get("fill_role") or "") == "ADDED"], feat)
    adm_split = median_split([r for r in a_pnl if r.get("control_admitted")], feat)
    blk_split = median_split([r for r in a_pnl if r.get("cap_only_blocked")], feat)
    pack["added"] = {k: added_split.get(k) for k in ("n", "direction", "delta_mean", "high_n", "low_n")}
    pack["admitted"] = {k: adm_split.get(k) for k in ("n", "direction", "delta_mean", "high_n", "low_n")}
    pack["cap_blocked"] = {k: blk_split.get(k) for k in ("n", "direction", "delta_mean", "high_n", "low_n")}
    pack["added_same_direction"] = _dir_same(a_dir, int(added_split.get("direction") or 0))
    pack["not_one_sided"] = _dir_same(a_dir, int(adm_split.get("direction") or 0)) and _dir_same(
        a_dir, int(blk_split.get("direction") or 0)
    )
    strata_ok = []
    strata = {}
    for name in ("FIRST", "MIDDLE", "LAST"):
        sp = median_split([r for r in a_pnl if str(r.get("arrival_third") or "") == name], feat)
        strata[name] = {k: sp.get(k) for k in ("n", "direction", "delta_mean", "high_n", "low_n")}
        if _dir_same(a_dir, int(sp.get("direction") or 0)):
            strata_ok.append(name)
    pack["arrival_strata"] = strata
    pack["arrival_same_direction_n"] = len(strata_ok)
    pack["arrival_multi_same_direction"] = len(strata_ok) >= 2
    conc = concentration_warning(a_pnl)
    pack["concentration"] = conc
    pack["availability_ok"] = bool(avails["BLOCK_A_DISCOVERY"].get("availability_ok"))
    pack["has_a_direction"] = bool(a_dir != 0 and pack["availability_ok"])
    confound = {
        "one_sided": bool(pack["has_a_direction"] and not pack["not_one_sided"]),
        "concentration": bool(pack["has_a_direction"] and conc.get("warning_gt_50pct")),
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
        and pack["arrival_multi_same_direction"]
        and pack["not_one_sided"]
        and (not conc.get("warning_gt_50pct"))
    )
    a_vals = [float(r[feat]) for r in _finite_feat(a_pool, feat)]
    pack["block_a_q30"] = empirical_quantile(a_vals, 0.30)
    pack["block_a_q70"] = empirical_quantile(a_vals, 0.70)
    pack["missing_impute_accept"] = False
    pack["missing_impute_reject"] = False
    pack["missing_policy_this_run"] = MISSING_POLICY_THIS_RUN
    return pack


def next_threshold(feat_pack: dict[str, Any]) -> dict[str, Any]:
    hib = feat_pack.get("higher_is_better")
    base = {
        "policy": THRESHOLD_POLICY,
        "search_allowed": False,
        "worst_reject_pct": float(WORST_REJECT_PCT),
        "q20_tried": False,
        "q25_tried": False,
        "q40_tried": False,
        "q50_tried": False,
    }
    if hib is True:
        base.update(
            {
                "threshold": feat_pack.get("block_a_q30"),
                "rule": "accept if feature >= BLOCK_A q30 (reject worst bottom 30%)",
                "side": "higher_is_better",
            }
        )
    elif hib is False:
        base.update(
            {
                "threshold": feat_pack.get("block_a_q70"),
                "rule": "accept if feature <= BLOCK_A q70 (reject worst top 30%)",
                "side": "lower_is_better",
            }
        )
    else:
        base["threshold"] = None
        base["rule"] = None
    return base


def evaluate(blocks: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
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
    missing = {
        feat: dict(next(f for f in features if f["feature"] == feat).get("availability") or {}) for feat in FEATURES
    }
    return {
        "features": features,
        "identity_AB_as_development": identity_ab,
        "identity_C_as_forward_burned": identity_c,
        "leftover_ok": leftover_ok,
        "control_sot_ok": sot_ok,
        "identity_ok": bool(identity_ab.get("ok") and identity_c.get("ok") and leftover_ok and sot_ok),
        "missing": missing,
        "independent_alpha_claim": False,
        "feature_discovery": False,
        "threshold_search": False,
    }


def decide(pack: dict[str, Any], *, leak_ok: bool) -> dict[str, Any]:
    integrity = bool(leak_ok and pack.get("identity_ok"))
    feats = list(pack.get("features") or [])
    qualified = [f for f in feats if f.get("qualify")]
    confounded = [f for f in feats if f.get("has_a_direction") and f.get("confounded") and not f.get("qualify")]
    selected = qualified[0] if qualified else None
    if not integrity:
        case, verdict, nxt = (
            "D",
            "SIMPLE_TECH_PRECAP_EXISTING_DATA_INTEGRITY_FAILED",
            "FAIL_CLOSED. Do not create a filter. Do not go prospective.",
        )
        selected = None
        qualified = []
    elif selected is not None:
        case, verdict, nxt = (
            "A",
            "SIMPLE_TECH_PRECAP_EXISTING_DATA_SINGLE_MECHANISM_FOUND",
            "Do not go prospective. Next: existing-data Full Causal Portfolio for PRECAP_ENTRY_QUALITY_SINGLE_FEATURE_V1 with frozen BLOCK_A worst-30pct threshold. Do not evaluate that candidate in this run.",
        )
    elif confounded:
        case, verdict, nxt = (
            "C",
            "SIMPLE_TECH_PRECAP_EXISTING_DATA_CONFOUNDED",
            "Do not create a filter. Move to a different existing-data architecture. Do not go prospective.",
        )
    else:
        case, verdict, nxt = (
            "B",
            "SIMPLE_TECH_PRECAP_EXISTING_DATA_NO_ROBUST_MECHANISM",
            "Close the 4-feature family. Do not go prospective. Next: a different existing-data causal ENTRY mechanism family.",
        )
    thr = next_threshold(selected) if selected is not None else next_threshold({})
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
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
        "integrity": integrity,
        "NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED_FLAG": bool(NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED),
    }
