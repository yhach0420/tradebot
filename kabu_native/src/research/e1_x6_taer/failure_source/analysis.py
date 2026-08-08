"""Stats, S7 audit, LODO, bootstrap, models, verdict for FSA V2."""
from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from typing import Any, Optional

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier

from .precommit import (
    ADVERSE_THRESHOLDS_BPS,
    BOOTSTRAP_REPS,
    BOOTSTRAP_SEED,
    FEATURE_SCHEMA,
    FORBIDDEN_FEATURES,
    LABEL_QUALITY_MIN_CLUSTER_FRAC,
    LOGISTIC_C,
    OPP_THRESHOLDS_BPS,
    TREE_MIN_LEAF,
)

S7_LABELS = {
    "S7_CENSORED_OR_OTHER",
}


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    ys = sorted(xs)
    n = len(ys)
    mid = n // 2
    return ys[mid] if n % 2 else 0.5 * (ys[mid - 1] + ys[mid])


def _quantile(xs: list[float], q: float) -> Optional[float]:
    if not xs:
        return None
    ys = sorted(xs)
    if len(ys) == 1:
        return ys[0]
    pos = q * (len(ys) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ys[lo]
    return ys[lo] * (hi - pos) + ys[hi] * (pos - lo)


def primary_rows(opp: list[dict], feats: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """Cluster representatives, non-S7, evaluable — primary supervised set."""
    feat_by = {r["episode_id"]: r for r in feats}
    primary_opp = []
    primary_feat = []
    excluded = Counter()
    for r in opp:
        if not r.get("is_cluster_representative"):
            excluded["not_representative"] += 1
            continue
        if r.get("scenario_id_prior") in S7_LABELS or str(r.get("scenario_id_prior") or "").startswith("S7_"):
            excluded["s7"] += 1
            continue
        if not r.get("evaluable"):
            excluded["not_evaluable"] += 1
            continue
        if r.get("best_net_pnl_bps_300s") is None:
            excluded["missing_primary_outcome"] += 1
            continue
        primary_opp.append(r)
        if r["episode_id"] in feat_by:
            primary_feat.append(feat_by[r["episode_id"]])
        else:
            excluded["missing_features"] += 1
    meta = {
        "primary_cluster_n": len(primary_opp),
        "excluded_counts": dict(excluded),
        "s7_excluded_from_primary_supervised": True,
    }
    return primary_opp, primary_feat, meta


def opportunity_summary(opp: list[dict], *, representatives_only: bool = True) -> dict[str, Any]:
    rows = [r for r in opp if r.get("evaluable") and r.get("best_net_pnl_bps_300s") is not None]
    if representatives_only:
        rows = [r for r in rows if r.get("is_cluster_representative")]

    def pack(subset: list[dict]) -> dict:
        vals = [float(r["best_net_pnl_bps_300s"]) for r in subset]
        adv = [float(r["adverse_before_best_bps"]) for r in subset if r.get("adverse_before_best_bps") is not None]
        ttp = [float(r["time_to_net_positive_sec"]) for r in subset if r.get("time_to_net_positive_sec") is not None]
        n = len(subset)
        return {
            "episode_n": n,
            "cluster_n": n,  # already reps
            "best_net_pnl_bps_median": _median(vals),
            "best_net_pnl_bps_q25": _quantile(vals, 0.25),
            "best_net_pnl_bps_q75": _quantile(vals, 0.75),
            "net_positive_rate": sum(1 for v in vals if v > 0) / n if n else None,
            "net_plus_5bps_rate": sum(1 for v in vals if v >= 5.0) / n if n else None,
            "net_plus_10bps_rate": sum(1 for v in vals if v >= 10.0) / n if n else None,
            "adverse_before_best_median": _median(adv),
            "time_to_positive_median": _median(ttp),
        }

    by_setup = {}
    for setup in ("PULLBACK_RECLAIM", "RANGE_BREAKOUT"):
        sub = [r for r in rows if r["setup_type"] == setup]
        by_day = {}
        for d in sorted({r["day"] for r in sub}):
            by_day[d] = pack([r for r in sub if r["day"] == d])
        by_setup[setup] = {"overall": pack(sub), "by_day": by_day}

    # secondary label grid counts (sensitivity only)
    grid = []
    for thr in OPP_THRESHOLDS_BPS:
        for adv_thr in ADVERSE_THRESHOLDS_BPS:
            n = sum(
                1 for r in rows
                if float(r["best_net_pnl_bps_300s"]) >= thr
                and float(r.get("adverse_before_best_bps") or 0) >= adv_thr
            )
            grid.append({"opp_thr_bps": thr, "adverse_thr_bps": adv_thr, "n": n, "rate": n / len(rows) if rows else None})

    return {
        "unit": "cluster_representative",
        "n_rows": len(rows),
        "by_setup": by_setup,
        "secondary_label_grid_counts": grid,
        "note": "Do not cherry-pick best grid cell",
    }


def judge_opportunity_exists(summary: dict) -> dict[str, Any]:
    """Rule A: opportunity nearly absent across horizons/days."""
    details = {}
    any_setup_has_opp = False
    for setup, blk in (summary.get("by_setup") or {}).items():
        overall = blk["overall"]
        med = overall.get("best_net_pnl_bps_median")
        rate5 = overall.get("net_plus_5bps_rate") or 0.0
        day_rates = []
        for d, p in (blk.get("by_day") or {}).items():
            day_rates.append((d, p.get("net_plus_5bps_rate"), p.get("best_net_pnl_bps_median")))
        low_days = sum(1 for _, r, m in day_rates if (r or 0) < 0.25 and (m or 0) <= 0)
        # multi-horizon: check 60/120 via overall only on 300 primary; treat med<=0 and low +5 rate
        exists = (med is not None and med > 0) or rate5 >= 0.30
        # multiple days with positive median
        pos_days = sum(1 for _, _, m in day_rates if m is not None and m > 0)
        details[setup] = {
            "median_300": med,
            "plus5_rate": rate5,
            "positive_median_days": pos_days,
            "low_opp_days": low_days,
            "opportunity_exists": bool(exists and pos_days >= 2),
        }
        if details[setup]["opportunity_exists"]:
            any_setup_has_opp = True
    return {
        "any_setup_opportunity_exists": any_setup_has_opp,
        "by_setup": details,
        "verdict_if_none": "TAER_NO_EXECUTABLE_ENTRY_OPPORTUNITY",
    }


def audit_s7(opp: list[dict], episodes: list[dict]) -> dict[str, Any]:
    ep_by = {e["episode_id"]: e for e in episodes}
    rows = []
    for r in opp:
        scen = r.get("scenario_id_prior") or ""
        if scen not in S7_LABELS and not str(scen).startswith("S7_"):
            continue
        e = ep_by.get(r["episode_id"]) or {}
        path_n = int(e.get("path_n_prior") or r.get("path_event_count") or 0)
        cr = e.get("censor_reason_prior") or ""
        best = r.get("best_net_pnl_bps_300s")
        reason = "OTHER"
        if cr in ("SESSION_GAP",) or "SESSION" in str(cr).upper():
            reason = "SESSION_BOUNDARY"
        elif path_n < 5 or (not r.get("path_complete") and path_n < 10):
            reason = "INSUFFICIENT_HORIZON"
        elif r.get("first_touch_plus_5_or_minus_10") == "NONE" and r.get("first_touch_plus_10_or_minus_15") == "NONE":
            reason = "NO_CLEAR_FIRST_TOUCH"
        elif not r.get("evaluable") or best is None:
            reason = "STALE_OR_MISSING_PATH"
        elif (e.get("mfe_prior") or 0) > 0 and (e.get("mae_prior") or 0) < 0:
            # both sides touched — conflicting for S7 bucket
            reason = "CONFLICTING_SCENARIO"
        rows.append({
            "episode_id": r["episode_id"],
            "setup": r["setup_type"],
            "day": r["day"],
            "symbol": r["symbol"],
            "path_seconds": None if r.get("best_exit_time") is None else (
                float(r["best_exit_time"]) - float(r["entry_time"]) if r.get("entry_time") else None
            ),
            "path_n": path_n,
            "best_net_pnl_bps": best,
            "reason": reason,
            "censor_reason_prior": cr,
        })
    by_reason = Counter(x["reason"] for x in rows)
    return {
        "n": len(rows),
        "by_reason": dict(by_reason),
        "rows_sample": rows[:200],
        "rows_all_n": len(rows),
    }


def label_quality(cluster_n: int, primary_cluster_n: int) -> dict[str, Any]:
    frac = primary_cluster_n / cluster_n if cluster_n else 0.0
    return {
        "overlap_cluster_n": cluster_n,
        "primary_usable_cluster_n": primary_cluster_n,
        "usable_fraction": frac,
        "threshold": LABEL_QUALITY_MIN_CLUSTER_FRAC,
        "insufficient": frac < LABEL_QUALITY_MIN_CLUSTER_FRAC,
        "verdict_if_insufficient": "TAER_FAILURE_ANALYSIS_INSUFFICIENT_LABEL_QUALITY",
    }


def spearman(x: list[float], y: list[float]) -> Optional[float]:
    n = len(x)
    if n < 5:
        return None
    def rank(a):
        order = sorted(range(n), key=lambda i: a[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and a[order[j + 1]] == a[order[i]]:
                j += 1
            avg = 0.5 * (i + j) + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = rank(x), rank(y)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    denx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    deny = math.sqrt(sum((b - my) ** 2 for b in ry))
    if denx <= 0 or deny <= 0:
        return None
    return num / (denx * deny)


def univariate_analysis(primary_opp: list[dict], primary_feat: list[dict]) -> dict[str, Any]:
    opp_by = {r["episode_id"]: r for r in primary_opp}
    by_setup: dict[str, Any] = {}
    for setup in ("PULLBACK_RECLAIM", "RANGE_BREAKOUT"):
        rows = []
        for f in primary_feat:
            if f.get("setup_type") != setup:
                continue
            o = opp_by.get(f["episode_id"])
            if not o:
                continue
            rows.append((f, o))
        feat_stats = []
        for fname in FEATURE_SCHEMA:
            if fname == "missing_feature_count":
                continue
            xs, ys, w = [], [], []
            for f, o in rows:
                v = f.get(fname)
                if v is None:
                    continue
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(fv):
                    continue
                xs.append(fv)
                ys.append(float(o["best_net_pnl_bps_300s"]))
                w.append(float(f.get("cluster_weight") or 1.0))
            miss_rate = 1.0 - (len(xs) / len(rows)) if rows else 1.0
            sp = spearman(xs, ys) if len(xs) >= 5 else None
            # median split effect
            if len(xs) >= 8:
                medx = _median(xs)
                hi = [y for x, y in zip(xs, ys) if x >= medx]
                lo = [y for x, y in zip(xs, ys) if x < medx]
                effect = (_median(hi) or 0) - (_median(lo) or 0)
            else:
                effect = None
            # +5bps group median diff
            plus = [float(f.get(fname)) for f, o in rows
                    if f.get(fname) is not None and float(o["best_net_pnl_bps_300s"]) >= 5.0]
            nop = [float(f.get(fname)) for f, o in rows
                   if f.get(fname) is not None and float(o["best_net_pnl_bps_300s"]) < 5.0]
            plus = [x for x in plus if math.isfinite(x)]
            nop = [x for x in nop if math.isfinite(x)]
            med_diff_p5 = None
            if plus and nop:
                med_diff_p5 = (_median(plus) or 0) - (_median(nop) or 0)
            direction = None
            if effect is not None:
                direction = "POS" if effect > 0 else ("NEG" if effect < 0 else "ZERO")
            feat_stats.append({
                "feature": fname,
                "n": len(xs),
                "missing_rate": miss_rate,
                "spearman": sp,
                "median_split_effect_bps": effect,
                "direction": direction,
                "plus5_vs_not_median_diff": med_diff_p5,
            })
        by_setup[setup] = feat_stats
    return {"by_setup": by_setup, "forbidden_features_checked": FORBIDDEN_FEATURES}


def lodo_stability(primary_opp: list[dict], primary_feat: list[dict], uni: dict) -> dict[str, Any]:
    days = sorted({r["day"] for r in primary_opp})
    opp_by = {r["episode_id"]: r for r in primary_opp}
    out = {}
    for setup in ("PULLBACK_RECLAIM", "RANGE_BREAKOUT"):
        full_map = {r["feature"]: r for r in (uni["by_setup"].get(setup) or [])}
        feat_rows = []
        for fname in FEATURE_SCHEMA:
            if fname == "missing_feature_count":
                continue
            full = full_map.get(fname) or {}
            full_dir = full.get("direction")
            day_dirs = []
            effects = []
            supports = []
            for hold in days:
                xs, ys = [], []
                for f in primary_feat:
                    if f.get("setup_type") != setup or f.get("day") == hold:
                        continue
                    o = opp_by.get(f["episode_id"])
                    if not o or f.get(fname) is None:
                        continue
                    try:
                        fv = float(f[fname])
                    except (TypeError, ValueError):
                        continue
                    if not math.isfinite(fv):
                        continue
                    xs.append(fv)
                    ys.append(float(o["best_net_pnl_bps_300s"]))
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
            rev = sum(1 for d in dirs if full_dir in ("POS", "NEG") and d != full_dir and d != "ZERO")
            evaluable = len(dirs)
            same_rate = same / evaluable if evaluable else 0.0
            # opportunity days
            pos_days = neg_days = 0
            for d in days:
                day_vals = [float(o["best_net_pnl_bps_300s"]) for o in primary_opp
                            if o["setup_type"] == setup and o["day"] == d]
                if not day_vals:
                    continue
                m = _median(day_vals)
                if m is not None and m > 0:
                    pos_days += 1
                elif m is not None and m <= 0:
                    neg_days += 1
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
                "day_deletion_directions": {d: dd for d, dd in day_dirs},
                "same_direction_count": same,
                "direction_reversal_count": rev,
                "same_direction_rate": same_rate,
                "minimum_effect_size": min(effects) if effects else None,
                "maximum_effect_size": max(effects) if effects else None,
                "minimum_cluster_support": min(supports) if supports else None,
                "evaluable_days": evaluable,
                "positive_opportunity_days": pos_days,
                "non_opportunity_days": neg_days,
                "missing_rate": full.get("missing_rate"),
                "stable_candidate": stable,
            })
        out[setup] = feat_rows
    return out


def bootstrap_ci(primary_opp: list[dict], primary_feat: list[dict], lodo: dict) -> dict[str, Any]:
    rng = random.Random(BOOTSTRAP_SEED)
    opp_by = {r["episode_id"]: r for r in primary_opp}
    out = {}
    for setup in ("PULLBACK_RECLAIM", "RANGE_BREAKOUT"):
        # units = day|symbol
        units: dict[str, list] = defaultdict(list)
        for f in primary_feat:
            if f.get("setup_type") != setup:
                continue
            o = opp_by.get(f["episode_id"])
            if not o:
                continue
            units[f"{f['day']}|{f['symbol']}"].append((f, o))
        unit_keys = sorted(units)
        setup_out = []
        stable_feats = [r["feature"] for r in lodo.get(setup) or [] if r.get("stable_candidate")]
        for fname in stable_feats or []:
            effects = []
            for _ in range(BOOTSTRAP_REPS):
                # resample units with replacement
                chosen = [unit_keys[rng.randrange(len(unit_keys))] for _ in range(len(unit_keys))]
                xs, ys = [], []
                for uk in chosen:
                    for f, o in units[uk]:
                        if f.get(fname) is None:
                            continue
                        try:
                            fv = float(f[fname])
                        except (TypeError, ValueError):
                            continue
                        if not math.isfinite(fv):
                            continue
                        xs.append(fv)
                        ys.append(float(o["best_net_pnl_bps_300s"]))
                if len(xs) < 8:
                    continue
                medx = _median(xs)
                hi = [y for x, y in zip(xs, ys) if x >= medx]
                lo = [y for x, y in zip(xs, ys) if x < medx]
                effects.append((_median(hi) or 0) - (_median(lo) or 0))
            if len(effects) < 50:
                ci = None
                crosses = True
            else:
                effects.sort()
                lo = effects[int(0.025 * (len(effects) - 1))]
                hi = effects[int(0.975 * (len(effects) - 1))]
                ci = [lo, hi]
                crosses = lo <= 0 <= hi
            setup_out.append({
                "feature": fname,
                "bootstrap_reps": BOOTSTRAP_REPS,
                "seed": BOOTSTRAP_SEED,
                "effect_ci95": ci,
                "ci_crosses_0": crosses,
                "strong_stable": (not crosses) and ci is not None,
            })
        out[setup] = setup_out
    return out


def model_diagnostics(
    primary_opp: list[dict],
    primary_feat: list[dict],
    lodo: dict,
    boot: dict,
    opp_judge: dict,
) -> dict[str, Any]:
    results = {"ran": False, "by_setup": {}, "gates": {}}
    for setup in ("PULLBACK_RECLAIM", "RANGE_BREAKOUT"):
        rows_f = [f for f in primary_feat if f.get("setup_type") == setup]
        rows_o = [o for o in primary_opp if o.get("setup_type") == setup]
        opp_by = {r["episode_id"]: r for r in rows_o}
        stable = []
        for r in lodo.get(setup) or []:
            if not r.get("stable_candidate"):
                continue
            b = next((x for x in (boot.get(setup) or []) if x["feature"] == r["feature"]), None)
            if b and b.get("strong_stable"):
                stable.append(r["feature"])
        pos_days = (lodo.get(setup) or [{}])[0].get("positive_opportunity_days") if lodo.get(setup) else 0
        # recompute days
        day_med = {}
        for o in rows_o:
            day_med.setdefault(o["day"], []).append(float(o["best_net_pnl_bps_300s"]))
        pos_d = sum(1 for vs in day_med.values() if (_median(vs) or 0) > 0)
        neg_d = sum(1 for vs in day_med.values() if (_median(vs) or 0) <= 0)
        gates = {
            "usable_clusters": len(rows_o),
            "usable_clusters_ge_100": len(rows_o) >= 100,
            "stable_univariate_ge_2": len(stable) >= 2,
            "positive_opp_days_ge_4": pos_d >= 4,
            "negative_opp_days_ge_4": neg_d >= 4,
            "stable_features": stable,
        }
        results["gates"][setup] = gates
        if not all([
            gates["usable_clusters_ge_100"],
            gates["stable_univariate_ge_2"],
            gates["positive_opp_days_ge_4"],
            gates["negative_opp_days_ge_4"],
        ]):
            results["by_setup"][setup] = {"skipped": True, "gates": gates}
            continue
        results["ran"] = True
        # LODO logistic + tree
        days = sorted(day_med)
        fold_rows = []
        for hold in days:
            train_f = [f for f in rows_f if f["day"] != hold and f["episode_id"] in opp_by]
            test_f = [f for f in rows_f if f["day"] == hold and f["episode_id"] in opp_by]
            if len(train_f) < 30 or len(test_f) < 5:
                fold_rows.append({"confirm_day": hold, "skipped": True, "reason": "insufficient_n"})
                continue
            Xtr, ytr = _xy(train_f, opp_by, stable)
            Xte, yte = _xy(test_f, opp_by, stable)
            if len(set(ytr)) < 2 or len(Xtr) < 20:
                fold_rows.append({"confirm_day": hold, "skipped": True, "reason": "single_class_or_small"})
                continue
            scaler = StandardScaler()
            Xtr_s = scaler.fit_transform(Xtr)
            Xte_s = scaler.transform(Xte)
            logit = LogisticRegression(penalty="l2", C=LOGISTIC_C, max_iter=2000, random_state=BOOTSTRAP_SEED)
            logit.fit(Xtr_s, ytr)
            proba = logit.predict_proba(Xte_s)[:, 1]
            tree = DecisionTreeClassifier(
                max_depth=2, min_samples_leaf=TREE_MIN_LEAF, random_state=BOOTSTRAP_SEED
            )
            tree.fit(Xtr, ytr)
            pred_t = tree.predict_proba(Xte)[:, 1] if hasattr(tree, "predict_proba") else tree.predict(Xte)
            base = float(np.mean(yte))
            fold_rows.append({
                "confirm_day": hold,
                "skipped": False,
                "confirm_cluster_n": len(yte),
                "base_rate": base,
                "logistic": _cls_metrics(yte, proba),
                "tree": _cls_metrics(yte, pred_t),
                "logistic_coefficients": {f: float(c) for f, c in zip(stable, logit.coef_[0])},
                "preprocess_fit_days_only": True,
                "name": "cross_day_diagnostic",
            })
        aucs = [f["logistic"]["auc"] for f in fold_rows if not f.get("skipped") and f["logistic"]["auc"] is not None]
        results["by_setup"][setup] = {
            "skipped": False,
            "gates": gates,
            "folds": fold_rows,
            "median_auc": _median(aucs) if aucs else None,
            "auc_gt_0_55_days": sum(1 for a in aucs if a > 0.55),
            "n_folds_eval": len(aucs),
        }
    return results


def _xy(feats, opp_by, columns):
    X, y = [], []
    for f in feats:
        o = opp_by[f["episode_id"]]
        row = []
        ok = True
        for c in columns:
            v = f.get(c)
            if v is None or not math.isfinite(float(v)):
                ok = False
                break
            row.append(float(v))
        if not ok:
            continue
        X.append(row)
        y.append(1 if float(o["best_net_pnl_bps_300s"]) >= 5.0 else 0)
    return np.asarray(X, dtype=float), np.asarray(y, dtype=int)


def _cls_metrics(y_true, scores):
    y_true = np.asarray(y_true)
    scores = np.asarray(scores, dtype=float)
    out = {"auc": None, "pr_auc": None, "precision": None, "recall": None}
    if len(set(y_true.tolist())) < 2:
        return out
    try:
        out["auc"] = float(roc_auc_score(y_true, scores))
    except Exception:
        pass
    try:
        out["pr_auc"] = float(average_precision_score(y_true, scores))
    except Exception:
        pass
    pred = (scores >= 0.5).astype(int)
    tp = int(((pred == 1) & (y_true == 1)).sum())
    fp = int(((pred == 1) & (y_true == 0)).sum())
    fn = int(((pred == 0) & (y_true == 1)).sum())
    out["precision"] = tp / (tp + fp) if (tp + fp) else None
    out["recall"] = tp / (tp + fn) if (tp + fn) else None
    return out


def final_verdict(
    *,
    label_q: dict,
    opp_judge: dict,
    lodo: dict,
    boot: dict,
    models: dict,
) -> dict[str, Any]:
    if label_q.get("insufficient"):
        return {
            "verdict": "TAER_FAILURE_ANALYSIS_INSUFFICIENT_LABEL_QUALITY",
            "rule": "D",
            "family_action": "STOP_NO_SPECULATION",
        }
    if not opp_judge.get("any_setup_opportunity_exists"):
        return {
            "verdict": "TAER_NO_EXECUTABLE_ENTRY_OPPORTUNITY",
            "rule": "A",
            "family_action": "CLOSE_TAER_FAMILY",
        }

    supported = []
    for setup in ("PULLBACK_RECLAIM", "RANGE_BREAKOUT"):
        strong = [b for b in (boot.get(setup) or []) if b.get("strong_stable")]
        stable_n = len([r for r in (lodo.get(setup) or []) if r.get("stable_candidate")])
        # require strong stable >= 2
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
        # also B path: no stable signal
        supported.append((setup, ok, stable_n, len(strong), med_auc, auc_days))

    ok_setups = [s for s, ok, *_ in supported if ok]
    if not ok_setups:
        # no stable entry signal
        return {
            "verdict": "TAER_TRIGGER_ANCHORED_FAMILY_NO_STABLE_ENTRY_SIGNAL",
            "rule": "B",
            "family_action": "CLOSE_TAER_FAMILY",
            "setup_diagnostics": [
                {"setup": s, "supported": ok, "stable_n": sn, "strong_n": st, "median_auc": ma, "auc_gt_055_days": ad}
                for s, ok, sn, st, ma, ad in supported
            ],
        }
    if len(ok_setups) == 2:
        v = "TAER_SETUP_SPECIFIC_NEW_FAMILY_HYPOTHESES_SUPPORTED"
    elif ok_setups[0] == "PULLBACK_RECLAIM":
        v = "TAER_PULLBACK_NEW_FAMILY_HYPOTHESIS_SUPPORTED"
    else:
        v = "TAER_RANGE_NEW_FAMILY_HYPOTHESIS_SUPPORTED"
    return {
        "verdict": v,
        "rule": "C",
        "family_action": "STOP_BEFORE_NEW_FAMILY_DOC_PLAN_PRECOMMIT",
        "supported_setups": ok_setups,
        "setup_diagnostics": [
            {"setup": s, "supported": ok, "stable_n": sn, "strong_n": st, "median_auc": ma, "auc_gt_055_days": ad}
            for s, ok, sn, st, ma, ad in supported
        ],
        "new_candidate_created": False,
    }
