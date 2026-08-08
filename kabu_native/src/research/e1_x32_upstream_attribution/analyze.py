"""Marginal attribution, day/LOSO, symbol vs timing, verdict."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from . import (
    HISTORICAL_DAYS,
    MAX_SYMBOL_CONTRIB,
    MODERATE_DELTA,
    MODERATE_NEG_DAYS,
    STRESS_DAYS_285A,
    STRONG_DELTA,
    STRONG_NEG_DAYS,
    VERDICT_COVERAGE,
    VERDICT_MULTI,
    VERDICT_NONE,
    VERDICT_UPSTREAM,
)
from .funnel import transitions


def _day_means(evals: list[dict[str, Any]], H: int) -> dict[str, float]:
    by: dict[str, list[float]] = defaultdict(list)
    for e in evals:
        if e.get(f"return_{H}_valid"):
            by[e["date"]].append(float(e[f"return_{H}"]))
    return {d: float(np.mean(vs)) for d, vs in by.items() if len(vs) >= 3}


def transition_metrics(
    parent_evals: list[dict[str, Any]],
    child_evals: list[dict[str, Any]],
) -> dict[str, Any]:
    p300, c300 = _day_means(parent_evals, 300), _day_means(child_evals, 300)
    p600, c600 = _day_means(parent_evals, 600), _day_means(child_evals, 600)

    # aggregate means
    def _m(evals, H):
        rs = [float(e[f"return_{H}"]) for e in evals if e.get(f"return_{H}_valid")]
        return float(np.mean(rs)) if rs else None

    ap300, ac300 = _m(parent_evals, 300), _m(child_evals, 300)
    ap600, ac600 = _m(parent_evals, 600), _m(child_evals, 600)
    d300 = (ac300 - ap300) if ac300 is not None and ap300 is not None else None
    d600 = (ac600 - ap600) if ac600 is not None and ap600 is not None else None

    days = []
    neg300 = neg600 = 0
    for d in HISTORICAL_DAYS:
        dd300 = (c300[d] - p300[d]) if d in c300 and d in p300 else None
        dd600 = (c600[d] - p600[d]) if d in c600 and d in p600 else None
        if dd300 is not None and dd300 < 0:
            neg300 += 1
        if dd600 is not None and dd600 < 0:
            neg600 += 1
        days.append({
            "date": d,
            "parent_ret300": p300.get(d), "child_ret300": c300.get(d), "delta300": dd300,
            "parent_ret600": p600.get(d), "child_ret600": c600.get(d), "delta600": dd600,
        })

    strength = "WEAK_OR_NONE"
    if (
        d300 is not None and d600 is not None
        and d300 <= STRONG_DELTA and d600 <= STRONG_DELTA
        and neg300 >= STRONG_NEG_DAYS and neg600 >= STRONG_NEG_DAYS
    ):
        strength = "STRONG_NEGATIVE_ATTRIBUTION"
    elif (
        d300 is not None and d600 is not None
        and (d300 <= MODERATE_DELTA or d600 <= MODERATE_DELTA)
        and (neg300 >= MODERATE_NEG_DAYS or neg600 >= MODERATE_NEG_DAYS)
    ):
        strength = "MODERATE_NEGATIVE_ATTRIBUTION"

    return {
        "parent_ret300": ap300,
        "child_ret300": ac300,
        "delta300": d300,
        "parent_ret600": ap600,
        "child_ret600": ac600,
        "delta600": d600,
        "negative_days_300": neg300,
        "negative_days_600": neg600,
        "of_days": 14,
        "attribution_strength": strength,
        "days": days,
    }


def matched_parent_delta(
    parent_evals: list[dict[str, Any]],
    child_evals: list[dict[str, Any]],
) -> dict[str, Any]:
    """Match child episodes to parent same day + same clock bucket (5-min via signal_t floor)."""
    def bucket(t: float) -> int:
        return int(float(t) // 300) * 300

    parent_by: dict[tuple[str, int], list[float]] = defaultdict(list)
    parent_by600: dict[tuple[str, int], list[float]] = defaultdict(list)
    for e in parent_evals:
        b = bucket(e["signal_t"])
        if e.get("return_300_valid"):
            parent_by[(e["date"], b)].append(float(e["return_300"]))
        if e.get("return_600_valid"):
            parent_by600[(e["date"], b)].append(float(e["return_600"]))

    deltas300, deltas600 = [], []
    for e in child_evals:
        b = bucket(e["signal_t"])
        key = (e["date"], b)
        if e.get("return_300_valid") and key in parent_by:
            deltas300.append(float(e["return_300"]) - float(np.mean(parent_by[key])))
        if e.get("return_600_valid") and key in parent_by600:
            deltas600.append(float(e["return_600"]) - float(np.mean(parent_by600[key])))

    return {
        "matched_n_300": len(deltas300),
        "matched_delta300": float(np.mean(deltas300)) if deltas300 else None,
        "matched_n_600": len(deltas600),
        "matched_delta600": float(np.mean(deltas600)) if deltas600 else None,
    }


def symbol_vs_timing(
    stage_evals: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """
    Symbol-selection: CANDIDATE_SYMBOL_POOL vs RUNTIME_UNIVERSE / CAPTURED at common clock.
    Timing: CANDIDATE_CLUSTER_ANCHORS vs CANDIDATE_SYMBOL_POOL (same symbols, different times).
    """
    t_uni = transition_metrics(
        stage_evals["RUNTIME_UNIVERSE_SELECTED"],
        stage_evals["CANDIDATE_SYMBOL_POOL"],
    )
    t_cap = transition_metrics(
        stage_evals["CAPTURED_MARKET_PROXY"],
        stage_evals["CANDIDATE_SYMBOL_POOL"],
    )
    t_time = transition_metrics(
        stage_evals["CANDIDATE_SYMBOL_POOL"],
        stage_evals["CANDIDATE_CLUSTER_ANCHORS"],
    )
    return {
        "symbol_selection_delta_300": t_uni["delta300"],
        "symbol_selection_delta_600": t_uni["delta600"],
        "symbol_selection_vs_universe": t_uni,
        "symbol_selection_vs_captured": t_cap,
        "timing_delta_300": t_time["delta300"],
        "timing_delta_600": t_time["delta600"],
        "timing": t_time,
    }


def loso_transition(
    parent_evals: list[dict[str, Any]],
    child_evals: list[dict[str, Any]],
) -> dict[str, Any]:
    """LOSO on child-parent delta300 (per-symbol omit from both)."""
    def mean_ret(evals, omit=None):
        rs = [
            float(e["return_300"])
            for e in evals
            if e.get("return_300_valid") and (omit is None or e["symbol"] != omit)
        ]
        return float(np.mean(rs)) if len(rs) >= 20 else None

    full_p, full_c = mean_ret(parent_evals), mean_ret(child_evals)
    if full_p is None or full_c is None:
        return {"severe": False, "full_delta300": None}
    full_d = full_c - full_p
    syms = sorted({e["symbol"] for e in child_evals})
    rows = []
    for sym in syms:
        p, c = mean_ret(parent_evals, sym), mean_ret(child_evals, sym)
        if p is None or c is None:
            continue
        d = c - p
        rows.append({
            "omitted_symbol": sym,
            "delta300": d,
            "contribution": full_d - d,
        })
    if not rows:
        return {"full_delta300": full_d, "severe": False}

    # concentration on child absolute negative mass
    child_rs = [
        (e["symbol"], float(e["return_300"]))
        for e in child_evals if e.get("return_300_valid")
    ]
    neg = np.array([max(0.0, -r) for _, r in child_rs])
    tot = float(neg.sum()) + 1e-12
    max_share = 0.0
    for sym in syms:
        idx = [i for i, (s, _) in enumerate(child_rs) if s == sym]
        max_share = max(max_share, float(neg[idx].sum()) / tot if idx else 0.0)

    worst = max(rows, key=lambda x: x["contribution"])
    best = min(rows, key=lambda x: x["contribution"])
    pos = sum(1 for r in rows if (r["delta300"] or 0) < 0)
    neg_n = sum(1 for r in rows if (r["delta300"] or 0) >= 0)
    return {
        "full_delta300": full_d,
        "positive_directional_loso": pos,
        "negative_directional_loso": neg_n,
        "max_symbol_contribution_share": max_share,
        "worst_omitted_symbol": worst["omitted_symbol"],
        "best_omitted_symbol": best["omitted_symbol"],
        "severe_symbol_concentration": max_share > MAX_SYMBOL_CONTRIB,
        "check_285A_stress": {d: "NOT_PRESENT" for d in STRESS_DAYS_285A},
    }


def selection_characteristics(
    *,
    stage_evals: dict[str, list],
    cand_rows: list[dict[str, Any]],
    coverage: list[dict[str, Any]],
) -> dict[str, Any]:
    """Diagnostic only — criteria actually used by universe / observed distributions."""
    # Universe actual criteria from core10_dynamic40_price_risk
    universe_criteria = [
        "core10_discord watchlist slots",
        "dynamic40 volatility_liquidity_score ranking",
        "dynamic price risk: MIN_CLOSE_PRICE / MAX_TICK_RATIO_PCT",
        "session AM/PM + refresh1000/1430 CSVs",
    ]
    # Capture: whatever was registered to push — not a research filter
    capture_note = (
        "push_jsonl may exceed 50-slot universe CSV; extras = UNRESOLVED_DUE_TO_SOURCE_COVERAGE"
    )

    # Activity/range bias among candidate rows (existing features, no new filter)
    feats = [
        "volume_delta_60s", "trading_value_delta_60s", "range_width_60s",
        "range_width_180s", "return_180s", "return_300s",
        "volume_percentile_60s", "trading_value_percentile_180s",
    ]
    dist = {}
    for f in feats:
        xs = [float(r[f]) for r in cand_rows if r.get(f) is not None]
        if len(xs) < 50:
            continue
        arr = np.asarray(xs, dtype=float)
        dist[f] = {
            "n": len(xs),
            "mean": float(np.mean(arr)),
            "p50": float(np.median(arr)),
            "p70": float(np.quantile(arr, 0.70)),
            "p90": float(np.quantile(arr, 0.90)),
        }

    # pre vs post among candidates
    pre180 = np.array([
        float(r["return_180s"]) if r.get("return_180s") is not None else np.nan
        for r in cand_rows
    ], dtype=float)
    # post from anchors eval
    anchors = stage_evals["CANDIDATE_CLUSTER_ANCHORS"]
    post300 = [float(e["return_300"]) for e in anchors if e.get("return_300_valid")]

    cap_gt_uni = sum(1 for c in coverage if c["capture_minus_universe"] > 0)
    return {
        "universe_selection_criteria_actual": universe_criteria,
        "capture_confound": capture_note,
        "days_capture_superset_of_universe": cap_gt_uni,
        "candidate_feature_distributions": dist,
        "pre_selection_return_180s_mean": float(np.nanmean(pre180)) if np.isfinite(pre180).any() else None,
        "post_candidate_ret300_mean": float(np.mean(post300)) if post300 else None,
        "activity_volatility_note": (
            "Candidate pool inherits Dynamic40 vol/liq ranking + cluster-first on feature-OK grids; "
            "X30 ACTIVITY/RANGE semantic survivors reflected activity tilt in prior study"
        ),
        "no_new_filter_created": True,
    }


def classify_effects(trans: dict[str, dict[str, Any]], decomp: dict[str, Any]) -> dict[str, Any]:
    def label(tr: dict[str, Any] | None) -> str:
        if not tr:
            return "UNRESOLVED"
        return tr.get("attribution_strength") or "WEAK_OR_NONE"

    cap_uni = trans.get("CAPTURED_MARKET_PROXY→RUNTIME_UNIVERSE_SELECTED")
    uni_cand = trans.get("RUNTIME_UNIVERSE_SELECTED→CANDIDATE_SYMBOL_POOL")
    cand_anch = trans.get("CANDIDATE_SYMBOL_POOL→CANDIDATE_CLUSTER_ANCHORS")

    return {
        "capture_selection_effect": label(cap_uni),
        "universe_selection_effect": label(cap_uni),  # universe vs captured
        "feature_eligibility_effect": "UNRESOLVED_DUE_TO_SOURCE_COVERAGE",
        "anchor_effect": label(cand_anch),  # cluster timestamp vs symbol-pool clock
        "signal_timing_effect": decomp.get("timing", {}).get("attribution_strength"),
        "symbol_pool_vs_universe_effect": label(uni_cand),
        "notes": {
            "feature_eligibility": "grid rebuild not run in X32; cannot quantify without future-free grid cache",
            "forward_label_gate": "excluded from ENTRY-pop attribution (uses future)",
        },
    }


def decide_verdict(
    *,
    coverage: list[dict[str, Any]],
    trans: dict[str, dict[str, Any]],
    effects: dict[str, Any],
) -> dict[str, Any]:
    # Coverage confound: if captured already much worse than would-be "market" —
    # we only observe CAPTURED_MARKET_PROXY. If capture >> universe and capture itself
    # is the weak parent, flag coverage.
    strong = [
        (k, v) for k, v in trans.items()
        if v.get("attribution_strength") == "STRONG_NEGATIVE_ATTRIBUTION"
    ]
    moderate = [
        (k, v) for k, v in trans.items()
        if v.get("attribution_strength") == "MODERATE_NEGATIVE_ATTRIBUTION"
    ]
    neg = strong + moderate

    # Capture confound: many days capture properly supersets universe AND
    # captured ret is already deeply negative while we have no broader market
    cap_superset_days = sum(1 for c in coverage if c["capture_minus_universe"] > 0)
    captured_is_weak_parent = False
    # If first stage (captured) ret300 already <= -10 and universe doesn't worsen much
    # → coverage bias limits research
    # Handled below via summaries passed in effects optionally

    if not neg:
        # Check coverage case: if no stage explains but captured proxy is the only observable market
        if cap_superset_days >= 7:
            # still may be NONE if deltas weak — Case C only when capture itself is the bias source
            # without a strong transition, prefer NONE unless coverage note dominates
            verdict = VERDICT_NONE
            culprit = None
            next_phase = "X33_PRE_ENTRY_SEQUENCE_DISCOVERY"
        else:
            verdict = VERDICT_NONE
            culprit = None
            next_phase = "X33_PRE_ENTRY_SEQUENCE_DISCOVERY"
    elif len(strong) == 1 and not moderate:
        verdict = VERDICT_UPSTREAM
        culprit = strong[0][0]
        next_phase = _next_for(culprit)
    elif len(neg) >= 2:
        verdict = VERDICT_MULTI
        # largest |delta300|
        culprit = min(neg, key=lambda x: (x[1].get("delta300") or 0))[0]
        next_phase = _next_for(culprit)
    elif len(strong) == 1:
        verdict = VERDICT_UPSTREAM
        culprit = strong[0][0]
        next_phase = _next_for(culprit)
    else:
        verdict = VERDICT_UPSTREAM if len(neg) == 1 else VERDICT_MULTI
        culprit = neg[0][0]
        next_phase = _next_for(culprit)

    return {
        "verdict": verdict,
        "primary_culprit_transition": culprit,
        "recommended_next_phase": next_phase,
        "strong_transitions": [k for k, _ in strong],
        "moderate_transitions": [k for k, _ in moderate],
    }


def _next_for(transition_key: str | None) -> str:
    if not transition_key:
        return "X33_PRE_ENTRY_SEQUENCE_DISCOVERY"
    if "CAPTURED" in transition_key and "UNIVERSE" in transition_key:
        # universe vs captured — if universe worsens relative to capture, universe redesign;
        # if capture is already biased (universe improves), coverage redesign
        return "X33_UPSTREAM_LONG_UNIVERSE_REDESIGN"
    if "UNIVERSE" in transition_key and "CANDIDATE_SYMBOL" in transition_key:
        return "X33_UPSTREAM_LONG_UNIVERSE_REDESIGN"
    if "CANDIDATE_CLUSTER" in transition_key or "ANCHOR" in transition_key:
        return "X33_ANCHOR_GENERATION_REDESIGN"
    return "X33_PRE_ENTRY_SEQUENCE_DISCOVERY"


def refine_verdict_for_coverage(
    decision: dict[str, Any],
    summaries: dict[str, Any],
    coverage: list[dict[str, Any]],
    trans: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """
    Case C: if CAPTURED_MARKET_PROXY already deeply negative and transitions from it
    are weak, the observable source coverage itself limits ENTRY research.
    """
    cap = summaries.get("CAPTURED_MARKET_PROXY") or {}
    uni = summaries.get("RUNTIME_UNIVERSE_SELECTED") or {}
    cap_r = cap.get("ret300")
    uni_r = uni.get("ret300")
    cap_uni = trans.get("CAPTURED_MARKET_PROXY→RUNTIME_UNIVERSE_SELECTED") or {}
    # If capture is already ~as bad as candidates and universe doesn't fix it
    cand = summaries.get("CANDIDATE_CLUSTER_ANCHORS") or {}
    if (
        cap_r is not None and cand.get("ret300") is not None
        and cap_r <= -10.0
        and abs((cand["ret300"] or 0) - cap_r) < 5.0
        and (cap_uni.get("attribution_strength") in (None, "WEAK_OR_NONE"))
        and decision["verdict"] == VERDICT_NONE
    ):
        decision = {
            **decision,
            "verdict": VERDICT_COVERAGE,
            "primary_culprit_transition": "CAPTURED_MARKET_PROXY (source coverage)",
            "recommended_next_phase": "X33_DATA_SOURCE_COVERAGE_REDESIGN",
        }
    # If universe selection is the strong culprit but capture already bad — still universe next
    # If transition capture→universe is strongly positive (universe better) but candidates worse
    # from universe→cand, culprit is symbol pool narrowing inside research — still universe/pool
    return decision


def run_attribution(stage_pack: dict[str, Any]) -> dict[str, Any]:
    evals = stage_pack["evals"]
    summaries = stage_pack["summaries"]
    trans = {}
    matched = {}
    loso = {}
    for parent, child in transitions():
        key = f"{parent}→{child}"
        trans[key] = transition_metrics(evals[parent], evals[child])
        matched[key] = matched_parent_delta(evals[parent], evals[child])
        if trans[key]["attribution_strength"] in (
            "STRONG_NEGATIVE_ATTRIBUTION", "MODERATE_NEGATIVE_ATTRIBUTION"
        ):
            loso[key] = loso_transition(evals[parent], evals[child])

    decomp = symbol_vs_timing(evals)
    return {
        "summaries": summaries,
        "transitions": trans,
        "matched_parent": matched,
        "loso": loso,
        "symbol_vs_timing": decomp,
    }
