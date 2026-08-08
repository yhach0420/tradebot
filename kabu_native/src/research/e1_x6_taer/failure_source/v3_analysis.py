"""V3 stability / models — includes target-valid S7; scenario not a feature."""
from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any, Optional

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier

from research.e1_x6_taer.failure_source.analysis import _cls_metrics, _median, spearman
from research.e1_x6_taer.failure_source.precommit import (
    BOOTSTRAP_REPS,
    BOOTSTRAP_SEED,
    FEATURE_SCHEMA,
    LOGISTIC_C,
    TREE_MIN_LEAF,
)
from research.e1_x6_taer.failure_source.v3_precommit import DEPTH_UNAVAILABLE, SETUP_SPECIFIC_FEATURES


def join_target_feature_rows(
    labels: list[dict],
    feats: list[dict],
) -> list[dict]:
    """One row per target-valid cluster with features; S7 included."""
    feat_by = {r["episode_id"]: r for r in feats}
    out = []
    for lb in labels:
        if not lb.get("opportunity_target_valid"):
            continue
        f = feat_by.get(lb["episode_id"])
        if not f:
            continue
        out.append({
            **f,
            "best_net_pnl_bps_300s": lb["best_net_pnl_bps_300s"],
            "best_net_pnl_bps_60s": lb.get("best_net_pnl_bps_60s"),
            "best_net_pnl_bps_120s": lb.get("best_net_pnl_bps_120s"),
            "adverse_before_best_bps": lb.get("adverse_before_best_bps"),
            "time_to_net_positive_sec": lb.get("time_to_net_positive_sec"),
            "net_plus_5bps": 1 if lb.get("net_plus_5bps") else 0,
            "scenario_group": lb.get("scenario_group"),  # audit only
        })
    return out


def opportunity_summary_v3(labels: list[dict]) -> dict[str, Any]:
    rows = [r for r in labels if r.get("opportunity_target_valid")]
    def pack(subset):
        vals = [float(r["best_net_pnl_bps_300s"]) for r in subset]
        n = len(subset)
        return {
            "cluster_n": n,
            "best_net_pnl_bps_median": _median(vals),
            "net_positive_rate": sum(1 for v in vals if v > 0) / n if n else None,
            "net_plus_5bps_rate": sum(1 for v in vals if v >= 5.0) / n if n else None,
            "net_plus_10bps_rate": sum(1 for v in vals if v >= 10.0) / n if n else None,
            "adverse_before_best_median": _median([
                float(r["adverse_before_best_bps"]) for r in subset
                if r.get("adverse_before_best_bps") is not None
            ]),
            "time_to_positive_median": _median([
                float(r["time_to_net_positive_sec"]) for r in subset
                if r.get("time_to_net_positive_sec") is not None
            ]),
        }
    by_setup = {}
    for setup in ("PULLBACK_RECLAIM", "RANGE_BREAKOUT"):
        sub = [r for r in rows if r["setup_type"] == setup]
        by_day = {d: pack([r for r in sub if r["day"] == d]) for d in sorted({r["day"] for r in sub})}
        by_setup[setup] = {"overall": pack(sub), "by_day": by_day}
    # scenario reference only
    by_scen = {}
    for g in sorted({r.get("scenario_group") for r in rows}):
        by_scen[g] = pack([r for r in rows if r.get("scenario_group") == g])
    return {
        "unit": "target_valid_cluster_representative",
        "includes_s7": True,
        "edge_type": "oracle_edge",
        "not_realizable_exit_proof": True,
        "n": len(rows),
        "by_setup": by_setup,
        "by_scenario_group_reference_only": by_scen,
    }


def eligible_features(coverage: list[dict], setup: str) -> list[str]:
    out = []
    for c in coverage:
        fname = c["feature"]
        if fname in DEPTH_UNAVAILABLE:
            continue
        app = SETUP_SPECIFIC_FEATURES.get(fname)
        if app and app != setup:
            continue
        if app == setup or app is None:
            # recompute setup-specific missing from coverage row
            if c.get("applicable_setup") in (setup, "ALL") and c.get("primary_candidate_eligible"):
                # For ALL features, eligible flag used global missing; OK for both setups if miss<=20
                if c.get("applicable_setup") == "ALL" or c.get("applicable_setup") == setup:
                    out.append(fname)
    return out


def univariate_v3(rows: list[dict], coverage: list[dict]) -> dict[str, Any]:
    by_setup = {}
    for setup in ("PULLBACK_RECLAIM", "RANGE_BREAKOUT"):
        subset = [r for r in rows if r.get("setup_type") == setup]
        feats = []
        for fname in FEATURE_SCHEMA:
            if fname in ("setup_type_code", "anchor_type_code", "trade_side_quality_code", "missing_feature_count"):
                continue
            if fname in DEPTH_UNAVAILABLE:
                continue
            app = SETUP_SPECIFIC_FEATURES.get(fname)
            if app and app != setup:
                continue
            xs, ys = [], []
            for r in subset:
                v = r.get(fname)
                if v is None:
                    continue
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(fv):
                    continue
                xs.append(fv)
                ys.append(float(r["best_net_pnl_bps_300s"]))
            applicable_n = len(subset) if not app else len(subset)
            miss_rate = 1.0 - (len(xs) / applicable_n) if applicable_n else 1.0
            sp = spearman(xs, ys) if len(xs) >= 5 else None
            effect = None
            direction = None
            if len(xs) >= 8:
                medx = _median(xs)
                hi = [y for x, y in zip(xs, ys) if x >= medx]
                lo = [y for x, y in zip(xs, ys) if x < medx]
                effect = (_median(hi) or 0) - (_median(lo) or 0)
                direction = "POS" if effect > 0 else ("NEG" if effect < 0 else "ZERO")
            plus = [float(r[fname]) for r in subset
                    if r.get(fname) is not None and int(r.get("net_plus_5bps") or 0) == 1]
            nop = [float(r[fname]) for r in subset
                   if r.get(fname) is not None and int(r.get("net_plus_5bps") or 0) == 0]
            plus = [x for x in plus if math.isfinite(x)]
            nop = [x for x in nop if math.isfinite(x)]
            med_diff = ((_median(plus) or 0) - (_median(nop) or 0)) if plus and nop else None
            cov = next((c for c in coverage if c["feature"] == fname), {})
            feats.append({
                "feature": fname,
                "applicable_n": applicable_n,
                "n_non_missing": len(xs),
                "missing_rate": miss_rate,
                "spearman": sp,
                "median_split_effect_bps": effect,
                "direction": direction,
                "plus5_vs_not_median_diff": med_diff,
                "primary_candidate_eligible": miss_rate <= 0.20 and len(xs) >= 20,
            })
        by_setup[setup] = feats
    # ALL reference
    all_ref = []
    for fname in FEATURE_SCHEMA:
        if fname in DEPTH_UNAVAILABLE or fname.endswith("_code") or fname == "missing_feature_count":
            continue
        if fname in SETUP_SPECIFIC_FEATURES:
            continue
        xs = [float(r[fname]) for r in rows if r.get(fname) is not None]
        ys = [float(r["best_net_pnl_bps_300s"]) for r in rows if r.get(fname) is not None]
        all_ref.append({"feature": fname, "spearman": spearman(xs, ys) if len(xs) >= 5 else None, "n": len(xs)})
    return {"by_setup": by_setup, "all_mixed_reference_only": all_ref}


def lodo_v3(rows: list[dict], uni: dict) -> dict[str, Any]:
    days = sorted({r["day"] for r in rows})
    out = {}
    for setup in ("PULLBACK_RECLAIM", "RANGE_BREAKOUT"):
        subset = [r for r in rows if r["setup_type"] == setup]
        full_map = {r["feature"]: r for r in (uni["by_setup"].get(setup) or [])}
        feat_rows = []
        # opportunity days
        day_med = {}
        for r in subset:
            day_med.setdefault(r["day"], []).append(float(r["best_net_pnl_bps_300s"]))
        pos_days = sum(1 for vs in day_med.values() if (_median(vs) or 0) > 0)
        neg_days = sum(1 for vs in day_med.values() if (_median(vs) or 0) <= 0)

        for fname, full in full_map.items():
            if not full.get("primary_candidate_eligible"):
                continue
            full_dir = full.get("direction")
            day_dirs = []
            effects = []
            supports = []
            for hold in days:
                xs, ys = [], []
                for r in subset:
                    if r["day"] == hold or r.get(fname) is None:
                        continue
                    try:
                        fv = float(r[fname])
                    except (TypeError, ValueError):
                        continue
                    if not math.isfinite(fv):
                        continue
                    xs.append(fv)
                    ys.append(float(r["best_net_pnl_bps_300s"]))
                if len(xs) < 8:
                    day_dirs.append((hold, None))
                    continue
                medx = _median(xs)
                hi = [y for x, y in zip(xs, ys) if x >= medx]
                lo = [y for x, y in zip(xs, ys) if x < medx]
                effect = (_median(hi) or 0) - (_median(lo) or 0)
                d = "POS" if effect > 0 else ("NEG" if effect < 0 else "ZERO")
                day_dirs.append((hold, d))
                effects.append(effect)
                supports.append(len(xs))
            dirs = [d for _, d in day_dirs if d in ("POS", "NEG")]
            same = sum(1 for d in dirs if d == full_dir) if full_dir in ("POS", "NEG") else 0
            rev = sum(1 for d in dirs if full_dir in ("POS", "NEG") and d != full_dir)
            evaluable = len(dirs)
            same_rate = same / evaluable if evaluable else 0.0
            stable = (
                evaluable >= 7
                and same_rate >= 0.80
                and rev <= 1
                and (full.get("missing_rate") or 1) <= 0.20
                and pos_days >= 4
                and neg_days >= 4
                and full_dir in ("POS", "NEG")
            )
            feat_rows.append({
                "feature": fname,
                "full_period_direction": full_dir,
                "same_direction_count": same,
                "direction_reversal_count": rev,
                "same_direction_rate": same_rate,
                "evaluable_deletions": evaluable,
                "minimum_effect_size": min(effects) if effects else None,
                "maximum_effect_size": max(effects) if effects else None,
                "minimum_support": min(supports) if supports else None,
                "positive_opportunity_days": pos_days,
                "non_opportunity_days": neg_days,
                "stable_candidate": stable,
            })
        out[setup] = feat_rows
    return out


def bootstrap_v3(rows: list[dict], lodo: dict) -> dict[str, Any]:
    rng = random.Random(BOOTSTRAP_SEED)
    out = {}
    for setup in ("PULLBACK_RECLAIM", "RANGE_BREAKOUT"):
        subset = [r for r in rows if r["setup_type"] == setup]
        units: dict[str, list] = defaultdict(list)
        for r in subset:
            units[f"{r['day']}|{r['symbol']}"].append(r)
        keys = sorted(units)
        setup_out = []
        for fr in lodo.get(setup) or []:
            if not fr.get("stable_candidate"):
                continue
            fname = fr["feature"]
            effects = []
            for _ in range(BOOTSTRAP_REPS):
                chosen = [keys[rng.randrange(len(keys))] for _ in range(len(keys))]
                xs, ys = [], []
                for uk in chosen:
                    for r in units[uk]:
                        if r.get(fname) is None:
                            continue
                        xs.append(float(r[fname]))
                        ys.append(float(r["best_net_pnl_bps_300s"]))
                if len(xs) < 8:
                    continue
                medx = _median(xs)
                hi = [y for x, y in zip(xs, ys) if x >= medx]
                lo = [y for x, y in zip(xs, ys) if x < medx]
                effects.append((_median(hi) or 0) - (_median(lo) or 0))
            if len(effects) < 50:
                ci, crosses = None, True
            else:
                effects.sort()
                lo = effects[int(0.025 * (len(effects) - 1))]
                hi = effects[int(0.975 * (len(effects) - 1))]
                ci = [lo, hi]
                crosses = lo <= 0 <= hi
            setup_out.append({
                "feature": fname,
                "effect_ci95": ci,
                "ci_crosses_0": crosses,
                "strong_stable": (not crosses) and ci is not None,
                "seed": BOOTSTRAP_SEED,
                "reps": BOOTSTRAP_REPS,
            })
        out[setup] = setup_out
    return out


def models_v3(rows: list[dict], lodo: dict, boot: dict) -> dict[str, Any]:
    results = {"ran": False, "by_setup": {}, "gates": {}}
    for setup in ("PULLBACK_RECLAIM", "RANGE_BREAKOUT"):
        subset = [r for r in rows if r["setup_type"] == setup]
        strong = [b["feature"] for b in (boot.get(setup) or []) if b.get("strong_stable")]
        stable_n = len([r for r in (lodo.get(setup) or []) if r.get("stable_candidate")])
        day_med = {}
        for r in subset:
            day_med.setdefault(r["day"], []).append(float(r["best_net_pnl_bps_300s"]))
        pos_d = sum(1 for vs in day_med.values() if (_median(vs) or 0) > 0)
        # negative days: median best_net <= 0 OR net_plus_5bps rate context — use day where median < 5 for "neg" of plus5?
        # Spec: net_plus_5bps positive days >= 4 and negative days >= 4
        plus_day = {}
        for r in subset:
            plus_day.setdefault(r["day"], []).append(int(r.get("net_plus_5bps") or 0))
        pos5_days = sum(1 for vs in plus_day.values() if (sum(vs) / len(vs)) >= 0.5)
        neg5_days = sum(1 for vs in plus_day.values() if (sum(vs) / len(vs)) < 0.5)
        # also require some days with median <=0 for non-opportunity
        neg_med_days = sum(1 for vs in day_med.values() if (_median(vs) or 0) <= 0)
        gates = {
            "target_valid_clusters": len(subset),
            "target_valid_ge_100": len(subset) >= 100,
            "stable_univariate_ge_2": len(strong) >= 2,
            "plus5_pos_days_ge_4": pos5_days >= 4,
            "plus5_neg_days_ge_4": neg5_days >= 4,
            "stable_features": strong,
            "pos_median_days": pos_d,
            "neg_median_days": neg_med_days,
        }
        results["gates"][setup] = gates
        if not all([
            gates["target_valid_ge_100"],
            gates["stable_univariate_ge_2"],
            gates["plus5_pos_days_ge_4"],
            gates["plus5_neg_days_ge_4"],
        ]):
            results["by_setup"][setup] = {"skipped": True, "gates": gates}
            continue
        results["ran"] = True
        days = sorted(day_med)
        folds = []
        for hold in days:
            train = [r for r in subset if r["day"] != hold]
            test = [r for r in subset if r["day"] == hold]
            Xtr, ytr = _xy(train, strong)
            Xte, yte = _xy(test, strong)
            if len(set(ytr.tolist())) < 2 or len(Xtr) < 20 or len(Xte) < 3:
                folds.append({"confirm_day": hold, "skipped": True})
                continue
            scaler = StandardScaler()
            Xtr_s = scaler.fit_transform(Xtr)
            Xte_s = scaler.transform(Xte)
            logit = LogisticRegression(penalty="l2", C=LOGISTIC_C, max_iter=2000, random_state=BOOTSTRAP_SEED)
            logit.fit(Xtr_s, ytr)
            proba = logit.predict_proba(Xte_s)[:, 1]
            tree = DecisionTreeClassifier(max_depth=2, min_samples_leaf=TREE_MIN_LEAF, random_state=BOOTSTRAP_SEED)
            tree.fit(Xtr, ytr)
            pred_t = tree.predict_proba(Xte)[:, 1]
            folds.append({
                "confirm_day": hold,
                "skipped": False,
                "confirm_cluster_n": int(len(yte)),
                "base_rate": float(np.mean(yte)),
                "logistic": _cls_metrics(yte, proba),
                "tree": _cls_metrics(yte, pred_t),
                "logistic_coefficients": {f: float(c) for f, c in zip(strong, logit.coef_[0])},
                "name": "cross_day_diagnostic",
            })
        aucs = [f["logistic"]["auc"] for f in folds if not f.get("skipped") and f.get("logistic", {}).get("auc") is not None]
        results["by_setup"][setup] = {
            "skipped": False,
            "gates": gates,
            "folds": folds,
            "median_auc": _median(aucs) if aucs else None,
            "auc_gt_0_55_days": sum(1 for a in aucs if a > 0.55),
            "n_folds_eval": len(aucs),
        }
    return results


def _xy(rows, columns):
    X, y = [], []
    for r in rows:
        row = []
        ok = True
        for c in columns:
            v = r.get(c)
            if v is None or not math.isfinite(float(v)):
                ok = False
                break
            row.append(float(v))
        if not ok:
            continue
        X.append(row)
        y.append(int(r.get("net_plus_5bps") or 0))
    return np.asarray(X, dtype=float), np.asarray(y, dtype=int)


def verdict_v3(
    *,
    target_summary: dict,
    schema: dict,
    opp_summary: dict,
    lodo: dict,
    boot: dict,
    models: dict,
) -> dict[str, Any]:
    rate = target_summary.get("opportunity_target_valid_rate") or 0.0
    if rate < 0.70:
        return {
            "verdict": "TAER_FAILURE_ANALYSIS_INSUFFICIENT_OPPORTUNITY_TARGET_QUALITY",
            "rule": "TARGET_QUALITY",
            "stop": True,
        }
    if schema.get("status") != "PASS":
        return {
            "verdict": "TAER_FAILURE_ANALYSIS_INSUFFICIENT_FEATURE_SCHEMA",
            "rule": "FEATURE_SCHEMA",
            "errors": schema.get("errors"),
            "stop": True,
        }

    supported = []
    for setup in ("PULLBACK_RECLAIM", "RANGE_BREAKOUT"):
        strong = [b for b in (boot.get(setup) or []) if b.get("strong_stable")]
        stable_n = len([r for r in (lodo.get(setup) or []) if r.get("stable_candidate")])
        m = (models.get("by_setup") or {}).get(setup) or {}
        med_auc = m.get("median_auc")
        auc_days = m.get("auc_gt_0_55_days") or 0
        ok = (
            len(strong) >= 2
            and stable_n >= 2
            and med_auc is not None
            and med_auc >= 0.60
            and auc_days >= 5
        )
        supported.append((setup, ok, stable_n, len(strong), med_auc, auc_days))

    ok_setups = [s for s, ok, *_ in supported if ok]
    if not ok_setups:
        return {
            "verdict": "TAER_TRIGGER_ANCHORED_FAMILY_NO_STABLE_ENTRY_SIGNAL",
            "rule": "NO_STABLE_ENTRY",
            "oracle_edge_present": True,
            "realizable_exit_not_proven": True,
            "family_action": "NO_NEW_FAMILY_STOP",
            "setup_diagnostics": [
                {"setup": s, "supported": ok, "stable_n": sn, "strong_n": st, "median_auc": ma, "auc_gt_055_days": ad}
                for s, ok, sn, st, ma, ad in supported
            ],
            "stop": True,
            "new_family_created": False,
        }
    if len(ok_setups) == 2:
        v = "TAER_SETUP_SPECIFIC_NEW_FAMILY_HYPOTHESES_SUPPORTED"
    elif ok_setups[0] == "PULLBACK_RECLAIM":
        v = "TAER_PULLBACK_NEW_FAMILY_HYPOTHESIS_SUPPORTED"
    else:
        v = "TAER_RANGE_NEW_FAMILY_HYPOTHESIS_SUPPORTED"
    return {
        "verdict": v,
        "rule": "HYPOTHESIS_SUPPORTED",
        "supported_setups": ok_setups,
        "setup_diagnostics": [
            {"setup": s, "supported": ok, "stable_n": sn, "strong_n": st, "median_auc": ma, "auc_gt_055_days": ad}
            for s, ok, sn, st, ma, ad in supported
        ],
        "stop": True,
        "new_family_created": False,
        "note": "Hypothesis only — no new family Document/Plan/Precommit in this analysis",
    }
