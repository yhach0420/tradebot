"""FSA V4 analysis — corrected class support + continuous stability gates."""
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
from research.e1_x6_taer.failure_source.v4_precommit import ENTRY_FEATURE_COLUMNS, MIN_EFFECT_BPS


def join_rows(labels: list[dict], feats: list[dict]) -> list[dict]:
    feat_by = {r["episode_id"]: r for r in feats}
    out = []
    for lb in labels:
        f = feat_by.get(lb["episode_id"])
        if not f:
            continue
        # trade_side_quality display
        tsq = f.get("trade_side_quality")
        if tsq is None:
            tsq = "TICK_RULE_INFERRED" if f.get("trade_side_quality_code") == 1 else None
        out.append({
            **f,
            "trade_side_quality": tsq,
            "best_net_pnl_bps_300s": lb["best_net_pnl_bps_300s"],
            "net_plus_5bps": 1 if lb.get("net_plus_5bps") else 0,
            "opportunity_target_valid": lb.get("opportunity_target_valid"),
            "scenario_label_valid": lb.get("scenario_label_valid"),
            "scenario_group": lb.get("scenario_group"),
            "cluster_id": lb.get("cluster_id") or f.get("cluster_id"),
        })
    return out


def class_support_table(rows: list[dict]) -> dict[str, Any]:
    """Day class support from net_plus_5bps cluster counts — NOT median sign."""
    by_setup: dict[str, Any] = {}
    for setup in ("PULLBACK_RECLAIM", "RANGE_BREAKOUT"):
        subset = [r for r in rows if r.get("setup_type") == setup]
        days = sorted({r["day"] for r in subset})
        day_rows = []
        descriptive = []
        model_eligible = []
        for d in days:
            day = [r for r in subset if r["day"] == d]
            pos = sum(1 for r in day if int(r.get("net_plus_5bps") or 0) == 1)
            neg = sum(1 for r in day if int(r.get("net_plus_5bps") or 0) == 0)
            n = len(day)
            assert pos + neg == n
            rec = {
                "day": d,
                "cluster_n": n,
                "positive_n": pos,
                "negative_n": neg,
                "positive_rate": pos / n if n else None,
                "negative_rate": neg / n if n else None,
                # forbidden median-sign classification kept for audit contrast only
                "median_best_net_300s": _median([float(r["best_net_pnl_bps_300s"]) for r in day]),
                "descriptive_two_class": n >= 4 and pos >= 1 and neg >= 1,
                "model_confirm_eligible": n >= 6 and pos >= 2 and neg >= 2,
            }
            day_rows.append(rec)
            if rec["descriptive_two_class"]:
                descriptive.append(d)
            if rec["model_confirm_eligible"]:
                model_eligible.append(d)
        by_setup[setup] = {
            "days": day_rows,
            "descriptive_two_class_days": descriptive,
            "descriptive_two_class_day_n": len(descriptive),
            "model_eligible_days": model_eligible,
            "model_eligible_day_n": len(model_eligible),
        }
    return {
        "definition": "positive_n/negative_n from net_plus_5bps cluster labels",
        "forbidden_definition": "daily median sign of best_net_pnl_bps_300s",
        "by_setup": by_setup,
    }


def _finite_vals(rows: list[dict], fname: str) -> list[float]:
    out = []
    for r in rows:
        v = r.get(fname)
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fv):
            out.append(fv)
    return out


def _zero_variance(vals: list[float]) -> bool:
    if len(vals) < 2:
        return True
    return max(vals) - min(vals) <= 1e-15


def _median_split_effect(xs: list[float], ys: list[float]) -> Optional[float]:
    """Return None if not evaluable (too few or zero variance in x)."""
    if len(xs) < 8 or _zero_variance(xs):
        return None
    medx = _median(xs)
    hi = [y for x, y in zip(xs, ys) if x >= medx]
    lo = [y for x, y in zip(xs, ys) if x < medx]
    if not hi or not lo:
        return None
    # if all x equal after split empty — already handled
    if _zero_variance(xs):
        return None
    return (_median(hi) or 0.0) - (_median(lo) or 0.0)


def univariate_v4(rows: list[dict]) -> dict[str, Any]:
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
            pairs = []
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
                pairs.append((fv, float(r["best_net_pnl_bps_300s"])))
            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            applicable_n = len(subset)
            miss_rate = 1.0 - (len(xs) / applicable_n) if applicable_n else 1.0
            zv = _zero_variance(xs) if xs else True
            sp = None if zv or len(xs) < 5 else spearman(xs, ys)
            effect = None if zv else _median_split_effect(xs, ys)
            direction = None
            status = "OK"
            if zv:
                status = "NON_EVALUABLE_ZERO_VARIANCE"
                effect = None
                direction = None
                sp = None
            elif effect is None:
                status = "NON_EVALUABLE_EFFECT"
            else:
                direction = "POS" if effect > 0 else ("NEG" if effect < 0 else "ZERO")
            # plus5 median diff only if variance
            med_diff = None
            if not zv and xs:
                plus = [float(r[fname]) for r in subset
                        if r.get(fname) is not None and int(r.get("net_plus_5bps") or 0) == 1]
                nop = [float(r[fname]) for r in subset
                       if r.get(fname) is not None and int(r.get("net_plus_5bps") or 0) == 0]
                plus = [x for x in plus if math.isfinite(float(x))]
                nop = [x for x in nop if math.isfinite(float(x))]
                if plus and nop and not _zero_variance(plus + nop):
                    med_diff = (_median([float(x) for x in plus]) or 0) - (_median([float(x) for x in nop]) or 0)
            eligible = (
                status == "OK"
                and miss_rate <= 0.20
                and len(xs) >= 20
                and not zv
                and effect is not None
            )
            feats.append({
                "feature": fname,
                "applicable_n": applicable_n,
                "n_non_missing": len(xs),
                "missing_rate": miss_rate,
                "zero_variance": zv,
                "status": status,
                "spearman": sp,
                "median_split_effect_bps": effect,
                "direction": direction,
                "plus5_vs_not_median_diff": med_diff,
                "primary_candidate_eligible": eligible,
            })
        by_setup[setup] = feats
    return {"by_setup": by_setup}


def lodo_v4(rows: list[dict], uni: dict) -> dict[str, Any]:
    days = sorted({r["day"] for r in rows})
    out = {}
    for setup in ("PULLBACK_RECLAIM", "RANGE_BREAKOUT"):
        subset = [r for r in rows if r["setup_type"] == setup]
        full_map = {r["feature"]: r for r in (uni["by_setup"].get(setup) or [])}
        feat_rows = []
        for fname, full in full_map.items():
            if full.get("status") == "NON_EVALUABLE_ZERO_VARIANCE":
                feat_rows.append({
                    "feature": fname,
                    "status": "NON_EVALUABLE_ZERO_VARIANCE",
                    "stable_direction_candidate": False,
                })
                continue
            if not full.get("primary_candidate_eligible"):
                continue
            full_dir = full.get("direction")
            full_effect = full.get("median_split_effect_bps")
            day_effects = []
            day_dirs = []
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
                if len(xs) < 8 or _zero_variance(xs):
                    day_effects.append((hold, None, None, len(xs)))
                    day_dirs.append((hold, None))
                    continue
                sp = spearman(xs, ys)
                effect = _median_split_effect(xs, ys)
                d = None if effect is None else ("POS" if effect > 0 else ("NEG" if effect < 0 else "ZERO"))
                day_effects.append((hold, effect, sp, len(xs)))
                day_dirs.append((hold, d))
                if effect is not None:
                    supports.append(len(xs))
            dirs = [d for _, d in day_dirs if d in ("POS", "NEG")]
            effects_only = [e for _, e, _, _ in day_effects if e is not None]
            same = sum(1 for d in dirs if d == full_dir) if full_dir in ("POS", "NEG") else 0
            rev = sum(1 for d in dirs if full_dir in ("POS", "NEG") and d != full_dir)
            evaluable = len(dirs)
            same_rate = same / evaluable if evaluable else 0.0
            min_e = min(effects_only) if effects_only else None
            max_e = max(effects_only) if effects_only else None
            crosses = True if min_e is None or max_e is None else (min_e <= 0 <= max_e)
            min_support = min(supports) if supports else 0
            stable = (
                full.get("primary_candidate_eligible") is True
                and (full.get("missing_rate") or 1) <= 0.20
                and not full.get("zero_variance")
                and evaluable >= 7
                and same_rate >= 0.80
                and rev <= 1
                and not crosses
                and min_support >= 60
                and full_dir in ("POS", "NEG")
            )
            feat_rows.append({
                "feature": fname,
                "full_period_direction": full_dir,
                "full_period_effect": full_effect,
                "day_deletion_effects": {h: e for h, e, _, _ in day_effects},
                "day_deletion_spearman": {h: s for h, _, s, _ in day_effects},
                "same_direction_count": same,
                "same_direction_rate": same_rate,
                "direction_reversal_count": rev,
                "evaluable_day_deletions": evaluable,
                "minimum_effect": min_e,
                "maximum_effect": max_e,
                "crosses_zero": crosses,
                "minimum_support": min_support,
                "stable_direction_candidate": stable,
            })
        out[setup] = feat_rows
    return out


def bootstrap_v4(rows: list[dict], lodo: dict) -> dict[str, Any]:
    rng = random.Random(BOOTSTRAP_SEED)
    out = {}
    for setup in ("PULLBACK_RECLAIM", "RANGE_BREAKOUT"):
        subset = [r for r in rows if r["setup_type"] == setup]
        units: dict[str, list] = defaultdict(list)
        for r in subset:
            units[f"{r['day']}|{r['symbol']}"].append(r)
        keys = sorted(units)
        min_eff = MIN_EFFECT_BPS[setup]
        setup_out = []
        for fr in lodo.get(setup) or []:
            if not fr.get("stable_direction_candidate"):
                continue
            fname = fr["feature"]
            full_dir = fr.get("full_period_direction")
            effects = []
            spearmans = []
            for _ in range(BOOTSTRAP_REPS):
                chosen = [keys[rng.randrange(len(keys))] for _ in range(len(keys))]
                xs, ys = [], []
                for uk in chosen:
                    for r in units[uk]:
                        if r.get(fname) is None:
                            continue
                        try:
                            fv = float(r[fname])
                        except (TypeError, ValueError):
                            continue
                        if not math.isfinite(fv):
                            continue
                        xs.append(fv)
                        ys.append(float(r["best_net_pnl_bps_300s"]))
                if len(xs) < 8 or _zero_variance(xs):
                    continue
                eff = _median_split_effect(xs, ys)
                if eff is None:
                    continue
                effects.append(eff)
                sp = spearman(xs, ys)
                if sp is not None:
                    spearmans.append(sp)
            if len(effects) < 50:
                setup_out.append({
                    "feature": fname,
                    "status": "INSUFFICIENT_BOOTSTRAP",
                    "strong_stable_feature": False,
                })
                continue
            effects.sort()
            lo = effects[int(0.025 * (len(effects) - 1))]
            hi = effects[int(0.975 * (len(effects) - 1))]
            med = _median(effects)
            crosses = lo <= 0 <= hi
            pos_frac = sum(1 for e in effects if e > 0) / len(effects)
            neg_frac = sum(1 for e in effects if e < 0) / len(effects)
            sign_ok = (
                (full_dir == "POS" and med is not None and med > 0)
                or (full_dir == "NEG" and med is not None and med < 0)
            )
            abs_ok = med is not None and abs(med) >= min_eff
            strong = (not crosses) and sign_ok and abs_ok
            setup_out.append({
                "feature": fname,
                "bootstrap_median": med,
                "effect_ci95": [lo, hi],
                "crosses_zero": crosses,
                "positive_fraction": pos_frac,
                "negative_fraction": neg_frac,
                "spearman_median": _median(spearmans) if spearmans else None,
                "full_period_direction": full_dir,
                "min_effect_bps": min_eff,
                "strong_stable_feature": strong,
                "seed": BOOTSTRAP_SEED,
                "reps": BOOTSTRAP_REPS,
                "unit": "day_x_symbol",
            })
        out[setup] = setup_out
    return out


def models_v4(rows: list[dict], boot: dict, class_support: dict) -> dict[str, Any]:
    results = {"by_setup": {}, "gates": {}}
    for setup in ("PULLBACK_RECLAIM", "RANGE_BREAKOUT"):
        subset = [r for r in rows if r["setup_type"] == setup]
        strong = [b["feature"] for b in (boot.get(setup) or []) if b.get("strong_stable_feature")]
        model_days = (class_support["by_setup"].get(setup) or {}).get("model_eligible_days") or []
        gates = {
            "target_valid_clusters": len(subset),
            "strong_stable_features": strong,
            "strong_stable_n": len(strong),
            "model_eligible_days": model_days,
            "model_eligible_day_n": len(model_days),
        }
        if setup == "RANGE_BREAKOUT":
            gates["target_valid_ge_100"] = len(subset) >= 100
            if len(subset) < 100:
                results["gates"][setup] = gates
                results["by_setup"][setup] = {
                    "skipped": True,
                    "reason": "NOT_EVALUABLE_SUPPORT_LT_100",
                    "n": len(subset),
                    "gates": gates,
                    "not_evidence_of_no_entry_signal": True,
                }
                continue

        # PULLBACK gates
        days = sorted({r["day"] for r in subset})
        build_ok = True
        build_counts = []
        for hold in days:
            build = [r for r in subset if r["day"] != hold]
            bp = sum(1 for r in build if int(r.get("net_plus_5bps") or 0) == 1)
            bn = sum(1 for r in build if int(r.get("net_plus_5bps") or 0) == 0)
            build_counts.append({"held_out": hold, "positive_n": bp, "negative_n": bn})
            if bp < 20 or bn < 20:
                build_ok = False
        gates.update({
            "target_valid_ge_100": len(subset) >= 100,
            "strong_stable_ge_2": len(strong) >= 2,
            "model_eligible_days_ge_5": len(model_days) >= 5,
            "all_lodo_build_pos_ge_20": all(c["positive_n"] >= 20 for c in build_counts),
            "all_lodo_build_neg_ge_20": all(c["negative_n"] >= 20 for c in build_counts),
            "build_counts": build_counts,
        })
        results["gates"][setup] = gates
        if not all([
            gates["target_valid_ge_100"],
            gates["strong_stable_ge_2"],
            gates["model_eligible_days_ge_5"],
            gates["all_lodo_build_pos_ge_20"],
            gates["all_lodo_build_neg_ge_20"],
            build_ok,
        ]):
            results["by_setup"][setup] = {"skipped": True, "reason": "MODEL_GATE_FAIL", "gates": gates}
            continue

        folds = []
        coef_signs: dict[str, list[str]] = defaultdict(list)
        for hold in days:
            train = [r for r in subset if r["day"] != hold]
            test = [r for r in subset if r["day"] == hold]
            tp = sum(1 for r in test if int(r.get("net_plus_5bps") or 0) == 1)
            tn = sum(1 for r in test if int(r.get("net_plus_5bps") or 0) == 0)
            Xtr, ytr = _xy(train, strong)
            Xte, yte = _xy(test, strong)
            if len(set(yte.tolist())) < 2:
                folds.append({
                    "confirm_day": hold,
                    "positive_n": tp,
                    "negative_n": tn,
                    "status": "NOT_EVALUABLE_SINGLE_CLASS",
                    "auc": None,
                })
                continue
            if len(set(ytr.tolist())) < 2 or len(Xtr) < 20:
                folds.append({
                    "confirm_day": hold,
                    "positive_n": tp,
                    "negative_n": tn,
                    "status": "NOT_EVALUABLE_BUILD",
                    "auc": None,
                })
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
            coefs = {f: float(c) for f, c in zip(strong, logit.coef_[0])}
            for f, c in coefs.items():
                coef_signs[f].append("POS" if c > 0 else ("NEG" if c < 0 else "ZERO"))
            folds.append({
                "confirm_day": hold,
                "positive_n": tp,
                "negative_n": tn,
                "status": "OK",
                "base_rate": float(np.mean(yte)),
                "logistic": _cls_metrics(yte, proba),
                "tree": _cls_metrics(yte, pred_t),
                "logistic_coefficients": coefs,
                "name": "cross_day_diagnostic",
            })
        aucs = [f["logistic"]["auc"] for f in folds
                if f.get("status") == "OK" and f.get("logistic", {}).get("auc") is not None]
        # coefficient direction consistency
        coef_consistency = {}
        for f, signs in coef_signs.items():
            if not signs:
                continue
            maj = max(set(signs), key=signs.count)
            coef_consistency[f] = signs.count(maj) / len(signs)
        mean_coef_consistency = _median(list(coef_consistency.values())) if coef_consistency else None
        results["by_setup"][setup] = {
            "skipped": False,
            "gates": gates,
            "folds": folds,
            "median_auc": _median(aucs) if aucs else None,
            "auc_gt_0_55_days": sum(1 for a in aucs if a > 0.55),
            "n_folds_evaluable": len(aucs),
            "coefficient_direction_consistency": coef_consistency,
            "coefficient_direction_consistency_median": mean_coef_consistency,
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


def audit_entry_features(rows: list[dict], labels: list[dict]) -> dict[str, Any]:
    errors = []
    if len(rows) != 399:
        errors.append(f"n={len(rows)} != 399")
    cids = [r.get("cluster_id") for r in rows]
    if len(cids) != len(set(cids)):
        errors.append("duplicate_cluster_id")
    if any(not r.get("setup_type") for r in rows):
        errors.append("setup_type_missing")
    label_cids = {r["cluster_id"] for r in labels}
    if set(cids) != label_cids:
        errors.append("identity_mismatch_vs_labels")
    future = sum(
        1 for r in rows
        if r.get("decision_time") is not None and r.get("feature_asof_time") is not None
        and float(r["feature_asof_time"]) > float(r["decision_time"]) + 1e-9
    )
    if future:
        errors.append(f"feature_asof_future={future}")
    sample = rows[0] if rows else {}
    for c in ENTRY_FEATURE_COLUMNS:
        if c == "trade_side_quality":
            if "trade_side_quality" not in sample and "trade_side_quality_code" not in sample:
                errors.append("missing_col:trade_side_quality")
            continue
        if c not in sample:
            errors.append(f"missing_col:{c}")
    # ensure setup_type populated
    if any(not str(r.get("setup_type") or "").strip() for r in rows):
        errors.append("setup_type_blank")
    status = "PASS" if not errors else "FAIL"
    return {
        "status": status,
        "errors": errors,
        "n": len(rows),
        "columns": ENTRY_FEATURE_COLUMNS,
        "verdict_if_fail": "TAER_FAILURE_ANALYSIS_AUDIT_SCHEMA_INCOMPLETE",
    }


def verdict_v4(
    *,
    audit: dict,
    boot: dict,
    models: dict,
) -> dict[str, Any]:
    if audit.get("status") != "PASS":
        return {
            "verdict": "TAER_FAILURE_ANALYSIS_AUDIT_SCHEMA_INCOMPLETE",
            "rule": "F",
            "errors": audit.get("errors"),
            "stop": True,
            "new_family_created": False,
        }

    pb_strong = [b for b in (boot.get("PULLBACK_RECLAIM") or []) if b.get("strong_stable_feature")]
    rg_strong = [b for b in (boot.get("RANGE_BREAKOUT") or []) if b.get("strong_stable_feature")]

    # RANGE component
    if len(rg_strong) == 0:
        range_v = "TAER_RANGE_NO_STABLE_ENTRY_FEATURE"
    else:
        range_v = "TAER_RANGE_STABLE_FEATURES_MODEL_SUPPORT_INSUFFICIENT"

    # PULLBACK component
    pb_model = (models.get("by_setup") or {}).get("PULLBACK_RECLAIM") or {}
    if len(pb_strong) == 0:
        pullback_v = None  # no dedicated letter when only range matters; overall may be A
    else:
        med_auc = pb_model.get("median_auc")
        auc_days = pb_model.get("auc_gt_0_55_days") or 0
        coef_c = pb_model.get("coefficient_direction_consistency_median")
        model_ok = (
            not pb_model.get("skipped")
            and len(pb_strong) >= 2
            and med_auc is not None and med_auc >= 0.60
            and auc_days >= 5
            and coef_c is not None and coef_c >= 0.80
        )
        if model_ok:
            pullback_v = "TAER_PULLBACK_NEW_FAMILY_HYPOTHESIS_SUPPORTED"
        else:
            pullback_v = "TAER_PULLBACK_ENTRY_FEATURES_STABLE_MODEL_NOT_SUPPORTED"

    if len(pb_strong) == 0 and len(rg_strong) == 0:
        return {
            "verdict": "TAER_TRIGGER_ANCHORED_FAMILY_NO_STABLE_ENTRY_SIGNAL",
            "rule": "A",
            "pullback_verdict": None,
            "range_verdict": range_v,
            "pullback_strong_features": [],
            "range_strong_features": [],
            "stop": True,
            "new_family_created": False,
            "note": "After corrected bootstrap; oracle edge not disputed",
        }

    components = []
    if pullback_v:
        components.append(pullback_v)
    components.append(range_v)
    return {
        "verdict": "|".join(components),
        "rule": "COMPOUND",
        "pullback_verdict": pullback_v,
        "range_verdict": range_v,
        "pullback_strong_features": [b["feature"] for b in pb_strong],
        "range_strong_features": [b["feature"] for b in rg_strong],
        "pullback_model": {
            "skipped": pb_model.get("skipped"),
            "reason": pb_model.get("reason"),
            "median_auc": pb_model.get("median_auc"),
            "auc_gt_0_55_days": pb_model.get("auc_gt_0_55_days"),
            "coef_consistency": pb_model.get("coefficient_direction_consistency_median"),
        },
        "stop": True,
        "new_family_created": False,
        "note": "Hypothesis support does not auto-create a new family",
    }
