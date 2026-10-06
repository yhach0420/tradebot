"""Nested leave-one-day-out ranking. Inner selection on TRAIN days only. No PnL."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from research.uniform10_entry_rebuild_v2 import (
    FEATURE_COVERAGE_MIN,
    MAX_SELECTED_FEATURES,
    MIN_COHORT_N,
    NORMS,
    REDUNDANCY_ABS_CORR,
    RF_HP,
    RIDGE_ALPHAS,
)
from research.uniform10_entry_rebuild_v2.features import PREDICTIVE_FEATURES

TARGET = "EXECUTABLE_FORWARD_MID_RETURN_600S"


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _rank(x: np.ndarray) -> np.ndarray:
    o = np.argsort(x, kind="mergesort")
    r = np.empty(x.size, dtype=float)
    r[o] = np.arange(x.size, dtype=float)
    return r


def spearman(a: list[float], b: list[float]) -> Optional[float]:
    if len(a) < int(MIN_COHORT_N):
        return None
    aa = np.asarray(a, dtype=float)
    bb = np.asarray(b, dtype=float)
    if float(np.std(aa)) <= 1e-12 or float(np.std(bb)) <= 1e-12:
        return 0.0
    return float(np.corrcoef(_rank(aa), _rank(bb))[0, 1])


def eligible(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("modeling_bucket") == "PRIMARY_MODELING_ELIGIBLE" and _f(r.get(TARGET)) is not None]


def cohorts(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    by: dict[tuple[str, str], list] = defaultdict(list)
    for r in rows:
        by[(str(r.get("date")), str(r.get("anchor")))].append(r)
    return by


def coverage_inventory(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    xs = eligible(rows)
    n = len(xs)
    out = []
    y = [_f(r.get(TARGET)) for r in xs]
    for name in PREDICTIVE_FEATURES:
        vals = [_f(r.get(name)) for r in xs]
        ok = [v is not None for v in vals]
        cov = (sum(ok) / n) if n else 0.0
        aa = [vals[i] for i in range(n) if ok[i] and y[i] is not None]
        bb = [y[i] for i in range(n) if ok[i] and y[i] is not None]
        sp = spearman(aa, bb) if len(aa) >= 30 else None
        out.append(
            {
                "feature": name,
                "coverage": cov,
                "missing_rate": 1.0 - cov if n else None,
                "train_spearman_diagnostic_only": sp,
                "eligible": cov >= float(FEATURE_COVERAGE_MIN),
            }
        )
    return out


def _pair_corr(rows: list[dict[str, Any]], a: str, b: str) -> Optional[float]:
    xa = np.asarray([_f(r.get(a)) for r in rows], dtype=float)
    xb = np.asarray([_f(r.get(b)) for r in rows], dtype=float)
    ok = np.isfinite(xa) & np.isfinite(xb)
    if int(ok.sum()) < 30:
        return None
    if float(np.std(xa[ok])) <= 1e-12 or float(np.std(xb[ok])) <= 1e-12:
        return None
    return float(np.corrcoef(xa[ok], xb[ok])[0, 1])


def greedy_features(train: list[dict[str, Any]], max_n: int) -> list[str]:
    xs = eligible(train)
    inv = coverage_inventory(xs)
    cands = [r["feature"] for r in inv if r.get("eligible")]
    scored = []
    for f in cands:
        by_day: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for r in xs:
            v, y = _f(r.get(f)), _f(r.get(TARGET))
            if v is None or y is None:
                continue
            by_day[str(r.get("date"))].append((v, y))
        day_sp = []
        for pairs in by_day.values():
            sp = spearman([p[0] for p in pairs], [p[1] for p in pairs])
            if sp is not None:
                day_sp.append(abs(sp))
        scored.append((float(np.mean(day_sp)) if day_sp else -1.0, f))
    scored.sort(key=lambda z: -z[0])
    picked: list[str] = []
    for _s, f in scored:
        if len(picked) >= int(max_n):
            break
        red = False
        for g in picked:
            rho = _pair_corr(xs, f, g)
            if rho is not None and abs(rho) >= float(REDUNDANCY_ABS_CORR):
                red = True
                break
        if not red:
            picked.append(f)
    return picked


def apply_norm(cohort: list[dict[str, Any]], feats: list[str], mode: str) -> list[dict[str, Any]]:
    out = [dict(r) for r in cohort]
    if mode == "none" or not feats:
        return out
    n = len(out)
    for f in feats:
        xs = [_f(r.get(f)) for r in out]
        fin_idx = [i for i, v in enumerate(xs) if v is not None]
        if len(fin_idx) < 3:
            continue
        if mode == "cross_sectional_z":
            fin = [xs[i] for i in fin_idx]
            mu = float(np.mean(fin))
            sd = float(np.std(fin))
            for i in fin_idx:
                out[i][f] = 0.0 if sd <= 1e-12 else (xs[i] - mu) / sd
        elif mode == "robust_cross_sectional_rank":
            order = sorted(fin_idx, key=lambda i: (xs[i], str(out[i].get("symbol") or "")))
            for rank, i in enumerate(order):
                out[i][f] = (rank + 0.5) / max(len(order), 1)
    return out


def _matrix(rows: list[dict[str, Any]], feats: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    X = []
    y = []
    ok = []
    for r in rows:
        vec = []
        good = True
        for f in feats:
            v = _f(r.get(f))
            if v is None:
                good = False
                break
            vec.append(float(v))
        yt = _f(r.get(TARGET))
        if yt is None:
            good = False
        ok.append(good)
        X.append(vec if good else [0.0] * len(feats))
        y.append(float(yt) if yt is not None else 0.0)
    return np.asarray(X, dtype=float), np.asarray(y, dtype=float), np.asarray(ok, dtype=bool)


def fit_model(train: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    family = str(spec["family"])
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    if family == "BASE_CURRENT_SCORE":
        return {"family": family, "features": ["current_score"], "normalization": "none", "kind": "current"}
    train_n: list[dict[str, Any]] = []
    for coh in cohorts(train).values():
        train_n.extend(apply_norm(coh, feats, norm))
    X, y, ok = _matrix(eligible(train_n), feats)
    if int(ok.sum()) < 40:
        return {"family": family, "features": feats, "normalization": norm, "kind": "fail"}
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X[ok])
    if family == "LINEAR_REGULARIZED":
        m = Ridge(alpha=float(spec.get("alpha") or 1.0))
        m.fit(Xs, y[ok])
        return {
            "family": family,
            "features": feats,
            "normalization": norm,
            "alpha": spec.get("alpha"),
            "kind": "ridge",
            "scaler_mean": scaler.mean_.tolist(),
            "scaler_scale": scaler.scale_.tolist(),
            "coef": m.coef_.tolist(),
            "intercept": float(m.intercept_),
        }
    if family == "RF_EXISTING":
        m = RandomForestRegressor(
            n_estimators=int(RF_HP["n_estimators"]),
            max_depth=int(RF_HP["max_depth"]),
            min_samples_leaf=int(RF_HP["min_samples_leaf"]),
            random_state=int(RF_HP["random_state"]),
            n_jobs=1,
        )
        m.fit(Xs, y[ok])
        return {
            "family": family,
            "features": feats,
            "normalization": norm,
            "kind": "rf",
            "scaler_mean": scaler.mean_.tolist(),
            "scaler_scale": scaler.scale_.tolist(),
            "model": m,
        }
    return {"family": family, "kind": "fail", "features": feats, "normalization": norm}


def _predict_row(fit: dict[str, Any], row: dict[str, Any]) -> Optional[float]:
    kind = fit.get("kind")
    if kind == "current":
        return _f(row.get("current_score"))
    if kind == "fail":
        return None
    feats = list(fit.get("features") or [])
    vec = []
    for f in feats:
        v = _f(row.get(f))
        if v is None:
            return None
        vec.append(float(v))
    mean = np.asarray(fit.get("scaler_mean"), dtype=float)
    scale = np.asarray(fit.get("scaler_scale"), dtype=float)
    x = (np.asarray(vec, dtype=float) - mean) / np.where(scale == 0, 1.0, scale)
    if kind == "ridge":
        return float(np.dot(fit["coef"], x) + float(fit["intercept"]))
    if kind == "rf":
        return float(fit["model"].predict(x.reshape(1, -1))[0])
    return None


def score_rows(rows: list[dict[str, Any]], fit: dict[str, Any]) -> list[dict[str, Any]]:
    feats = list(fit.get("features") or [])
    norm = str(fit.get("normalization") or "none")
    out = []
    for coh in cohorts(rows).values():
        normed = apply_norm(coh, feats, norm) if fit.get("kind") != "current" else [dict(r) for r in coh]
        for r in normed:
            rec = dict(r)
            rec["c2_score"] = _predict_row(fit, rec)
            out.append(rec)
    return out


def mean_daily_spearman(rows: list[dict[str, Any]], score_key: str = "c2_score") -> dict[str, Any]:
    xs = eligible(rows)
    by_day: dict[str, list[float]] = defaultdict(list)
    n_coh = 0
    for (day, _an), grp in cohorts(xs).items():
        aa, bb = [], []
        for r in grp:
            s, y = _f(r.get(score_key)), _f(r.get(TARGET))
            if s is None or y is None:
                continue
            aa.append(s)
            bb.append(y)
        sp = spearman(aa, bb)
        if sp is None:
            continue
        n_coh += 1
        by_day[day].append(sp)
    daily = []
    for d, vs in sorted(by_day.items()):
        daily.append({"date": d, "spearman": float(np.mean(vs)), "n_cohorts": len(vs)})
    vals = [x["spearman"] for x in daily]
    return {
        "n_days": len(vals),
        "n_cohorts": n_coh,
        "MEAN_DAILY_SPEARMAN": float(np.mean(vals)) if vals else None,
        "MEDIAN_DAILY_SPEARMAN": float(np.median(vals)) if vals else None,
        "positive_day_count": sum(1 for v in vals if v > 0),
        "negative_day_count": sum(1 for v in vals if v < 0),
        "days": daily,
    }


def ranking_block(rows: list[dict[str, Any]], score_key: str = "c2_score") -> dict[str, Any]:
    xs = eligible(rows)
    by = cohorts(xs)

    def mean_topk(k: Optional[int]) -> Optional[float]:
        vals = []
        for grp in by.values():
            scored = [( _f(r.get(score_key)), _f(r.get(TARGET)), str(r.get("symbol"))) for r in grp]
            scored = [t for t in scored if t[0] is not None and t[1] is not None]
            if not scored:
                continue
            scored.sort(key=lambda z: (-z[0], z[2]))
            pick = scored if k is None else scored[:k]
            vals.append(float(np.mean([p[1] for p in pick])))
        return float(np.mean(vals)) if vals else None

    all_m = mean_topk(None)
    t10, t5, t3, t1 = mean_topk(10), mean_topk(5), mean_topk(3), mean_topk(1)
    winner_ranks = []
    large_hits = large_tot = top10_large = top10_n = pos_n = pos_tot = 0
    for grp in by.values():
        scored = [r for r in grp if _f(r.get(score_key)) is not None and _f(r.get(TARGET)) is not None]
        if len(scored) < 3:
            continue
        ys = [_f(r.get(TARGET)) for r in scored]
        thr = float(np.quantile(ys, 0.90))
        ordered = sorted(scored, key=lambda r: (-float(_f(r.get(score_key))), str(r.get("symbol"))))
        best = max(scored, key=lambda r: (float(_f(r.get(TARGET))), str(r.get("symbol"))))
        winner_ranks.append(1 + next(i for i, r in enumerate(ordered) if str(r.get("symbol")) == str(best.get("symbol"))))
        large = [r for r in scored if float(_f(r.get(TARGET))) >= thr]
        large_tot += len(large)
        top10 = set(id(r) for r in ordered[:10])
        large_hits += sum(1 for r in large if id(r) in top10)
        top10_n += min(10, len(ordered))
        top10_large += sum(1 for r in ordered[:10] if float(_f(r.get(TARGET))) >= thr)
        pos_tot += len(scored)
        pos_n += sum(1 for r in scored if float(_f(r.get(TARGET))) > 0)
    inversions = 0
    seq = [t1, t3, t5, t10, all_m]
    labs = ["Top1", "Top3", "Top5", "Top10", "ALL"]
    for a, b in zip(seq, seq[1:]):
        if a is not None and b is not None and a + 1e-15 < b:
            inversions += 1
    monotonic_ok = inversions <= 2 and (t1 is None or all_m is None or t1 >= all_m - 1e-12)
    return {
        "ALL_TARGET": all_m,
        "TOP10_TARGET": t10,
        "TOP5_TARGET": t5,
        "TOP3_TARGET": t3,
        "TOP1_TARGET": t1,
        "TOP10_uplift": (t10 - all_m) if t10 is not None and all_m is not None else None,
        "TOP5_uplift": (t5 - all_m) if t5 is not None and all_m is not None else None,
        "TOP3_uplift": (t3 - all_m) if t3 is not None and all_m is not None else None,
        "TOP1_uplift": (t1 - all_m) if t1 is not None and all_m is not None else None,
        "mean_winner_rank": float(np.mean(winner_ranks)) if winner_ranks else None,
        "large_up_recall": (large_hits / large_tot) if large_tot else None,
        "large_up_precision": (top10_large / top10_n) if top10_n else None,
        "positive_target_rate": (pos_n / pos_tot) if pos_tot else None,
        "rank_inversions": inversions,
        "monotonic_ok": monotonic_ok,
        "labels_checked": labs,
    }


def spec_grid(feat_by_k: dict[int, list[str]]) -> list[dict[str, Any]]:
    out = [{"family": "BASE_CURRENT_SCORE", "features": ["current_score"], "normalization": "none", "max_features": 1}]
    for k in MAX_SELECTED_FEATURES:
        feats = feat_by_k.get(int(k)) or []
        if len(feats) < 2:
            continue
        for norm in NORMS:
            for a in RIDGE_ALPHAS:
                out.append(
                    {
                        "family": "LINEAR_REGULARIZED",
                        "features": feats,
                        "normalization": norm,
                        "alpha": a,
                        "max_features": int(k),
                    }
                )
            out.append(
                {
                    "family": "RF_EXISTING",
                    "features": feats,
                    "normalization": norm,
                    "max_features": int(k),
                }
            )
    return out


def inner_select(train: list[dict[str, Any]]) -> dict[str, Any]:
    days = sorted({str(r.get("date")) for r in train})
    feat_by_k = {int(k): greedy_features(train, int(k)) for k in MAX_SELECTED_FEATURES}
    grid = spec_grid(feat_by_k)
    ranked = []
    for i, spec in enumerate(grid, start=1):
        fold = []
        family = str(spec.get("family") or "")
        for hold in days:
            tr = [r for r in train if str(r.get("date")) != hold]
            te = [r for r in train if str(r.get("date")) == hold]
            if not eligible(tr) or not eligible(te):
                continue
            if family == "BASE_CURRENT_SCORE":
                scored = [{**dict(r), "c2_score": _f(r.get("current_score"))} for r in te]
            else:
                fit = fit_model(tr, spec)
                scored = score_rows(te, fit)
            m = mean_daily_spearman(scored, "c2_score")
            if m.get("MEAN_DAILY_SPEARMAN") is not None:
                fold.append(float(m["MEAN_DAILY_SPEARMAN"]))
        mean = float(np.mean(fold)) if fold else None
        ranked.append({**{k: v for k, v in spec.items() if k != "features"}, "features": spec.get("features"), "inner_mean_daily_spearman": mean, "inner_n": len(fold)})
        print(
            f"    inner {i}/{len(grid)} {family} k={spec.get('max_features')} "
            f"norm={spec.get('normalization')} alpha={spec.get('alpha')} sp={mean}",
            flush=True,
        )
    ranked.sort(key=lambda r: (-(r["inner_mean_daily_spearman"] if r["inner_mean_daily_spearman"] is not None else -9.0), str(r.get("family"))))
    best = ranked[0] if ranked else {}
    return {"feat_by_k": feat_by_k, "inner_leaderboard": ranked[:12], "selected": best}


def _lock_spec(selected_specs: list[dict[str, Any]]) -> dict[str, Any]:
    keys = []
    for s in selected_specs:
        keys.append(
            (
                s.get("family"),
                s.get("normalization"),
                s.get("max_features"),
                s.get("alpha"),
                tuple(s.get("features") or ()),
            )
        )
    mode = Counter(keys).most_common(1)[0][0] if keys else None
    locked = None
    for s in selected_specs:
        if (
            s.get("family"),
            s.get("normalization"),
            s.get("max_features"),
            s.get("alpha"),
            tuple(s.get("features") or ()),
        ) == mode:
            locked = s
            break
    return {k: v for k, v in (locked or {}).items() if k != "inner_n"}


def _finish_nested(rows: list[dict[str, Any]], oof_rows: list[dict[str, Any]], fold_recs: list[dict[str, Any]], selected_specs: list[dict[str, Any]]) -> dict[str, Any]:
    locked = _lock_spec(selected_specs)
    cur = score_rows(rows, {"family": "BASE_CURRENT_SCORE", "kind": "current", "features": ["current_score"], "normalization": "none"})
    cur_m = mean_daily_spearman(cur, "c2_score")
    new_m = mean_daily_spearman(oof_rows, "c2_score")
    rank_new = ranking_block(oof_rows, "c2_score")
    rank_cur = ranking_block(cur, "c2_score")
    return {
        "folds": fold_recs,
        "locked_spec": locked,
        "oof_rows": oof_rows,
        "current_eval": cur_m,
        "new_eval": new_m,
        "ranking_new": rank_new,
        "ranking_current": rank_cur,
        "feat_inventory_all_train_diagnostic": coverage_inventory(eligible(rows)),
    }


def spec_from_winner(train: list[dict[str, Any]], winner: dict[str, Any]) -> dict[str, Any]:
    family = str(winner.get("family") or "")
    if family == "BASE_CURRENT_SCORE":
        return {
            "family": family,
            "features": ["current_score"],
            "normalization": "none",
            "max_features": 1,
            "inner_mean_daily_spearman": winner.get("inner_mean_daily_spearman"),
        }
    k = int(winner.get("max_features") or 6)
    feats = greedy_features(train, k)
    spec = {
        "family": family,
        "features": feats,
        "normalization": winner.get("normalization") or "none",
        "max_features": k,
        "inner_mean_daily_spearman": winner.get("inner_mean_daily_spearman"),
    }
    if winner.get("alpha") is not None:
        spec["alpha"] = winner.get("alpha")
    return spec


def nested_oof_from_winners(rows: list[dict[str, Any]], winners: list[dict[str, Any]]) -> dict[str, Any]:
    """Replay outer holdout scoring using already-chosen inner winners. No inner re-search."""
    by = {str(w.get("holdout_day")): w for w in winners}
    oof_rows: list[dict[str, Any]] = []
    fold_recs = []
    selected_specs = []
    days = sorted(by)
    for hold in days:
        print(f"  reconstruct holdout {hold}", flush=True)
        train = eligible([r for r in rows if str(r.get("date")) != hold])
        test = [r for r in rows if str(r.get("date")) == hold]
        spec = spec_from_winner(train, by[hold])
        selected_specs.append(spec)
        fit = fit_model(train, spec)
        scored = score_rows(test, fit)
        oof_rows.extend(scored)
        m = mean_daily_spearman(scored, "c2_score")
        fold_recs.append(
            {
                "holdout_day": hold,
                "family": spec.get("family"),
                "normalization": spec.get("normalization"),
                "max_features": spec.get("max_features"),
                "alpha": spec.get("alpha"),
                "features": spec.get("features"),
                "inner_mean_daily_spearman": spec.get("inner_mean_daily_spearman"),
                "holdout_mean_daily_spearman": m.get("MEAN_DAILY_SPEARMAN"),
                "reconstructed_from_inner_log": True,
            }
        )
    return _finish_nested(rows, oof_rows, fold_recs, selected_specs)


def parse_inner_winners(log_text: str) -> list[dict[str, Any]]:
    import re

    hold_re = re.compile(r"outer holdout (\d{8})")
    inner_re = re.compile(
        r"inner\s+(\d+)/37\s+(\S+)\s+k=(\S+)\s+norm=(\S+)\s+alpha=(\S+)\s+sp=(\S+)"
    )
    winners: list[dict[str, Any]] = []
    current: Optional[str] = None
    grid: list[dict[str, Any]] = []

    def _flush() -> None:
        if current is None or not grid:
            return
        ranked = sorted(
            grid,
            key=lambda r: (
                -(r["inner_mean_daily_spearman"] if r["inner_mean_daily_spearman"] is not None else -9.0),
                str(r.get("family")),
            ),
        )
        best = dict(ranked[0])
        best["holdout_day"] = current
        winners.append(best)

    for line in log_text.splitlines():
        hm = hold_re.search(line)
        if hm:
            _flush()
            current = hm.group(1)
            grid = []
            continue
        im = inner_re.search(line)
        if im and current is not None:
            alpha_s = im.group(5)
            sp_s = im.group(6)
            k_s = im.group(3)
            grid.append(
                {
                    "family": im.group(2),
                    "max_features": int(k_s) if k_s not in {"None", "none"} else 1,
                    "normalization": im.group(4),
                    "alpha": None if alpha_s in {"None", "none"} else float(alpha_s),
                    "inner_mean_daily_spearman": None if sp_s in {"None", "none"} else float(sp_s),
                }
            )
    _flush()
    return winners


def nested_oof(rows: list[dict[str, Any]]) -> dict[str, Any]:
    days = sorted({str(r.get("date")) for r in rows})
    oof_rows: list[dict[str, Any]] = []
    fold_recs = []
    selected_specs = []
    fits_by_day: dict[str, dict[str, Any]] = {}
    for hold in days:
        print(f"  outer holdout {hold}", flush=True)
        train = [r for r in rows if str(r.get("date")) != hold]
        test = [r for r in rows if str(r.get("date")) == hold]
        inner = inner_select(eligible(train))
        spec = inner.get("selected") or {}
        selected_specs.append(spec)
        fit = fit_model(eligible(train), spec)
        # drop unpicklable RF from fold rec; keep in fits_by_day for scoring this run
        scored = score_rows(test, fit)
        oof_rows.extend(scored)
        m = mean_daily_spearman(scored, "c2_score")
        fold_recs.append(
            {
                "holdout_day": hold,
                "family": spec.get("family"),
                "normalization": spec.get("normalization"),
                "max_features": spec.get("max_features"),
                "alpha": spec.get("alpha"),
                "features": spec.get("features"),
                "inner_mean_daily_spearman": spec.get("inner_mean_daily_spearman"),
                "holdout_mean_daily_spearman": m.get("MEAN_DAILY_SPEARMAN"),
            }
        )
        fits_by_day[hold] = fit
    out = _finish_nested(rows, oof_rows, fold_recs, selected_specs)
    out["fits_by_day"] = fits_by_day
    return out


def robustness(oof_rows: list[dict[str, Any]]) -> dict[str, Any]:
    base = mean_daily_spearman(oof_rows, "c2_score")
    days = list(base.get("days") or [])
    days_sorted = sorted(days, key=lambda r: -float(r.get("spearman") or -9))
    def drop_days(n: int) -> Optional[float]:
        drop = {d["date"] for d in days_sorted[:n]}
        keep = [r for r in oof_rows if str(r.get("date")) not in drop]
        return mean_daily_spearman(keep, "c2_score").get("MEAN_DAILY_SPEARMAN")

    # symbol contribution: mean target of OOF Top1 picks
    top1_y: dict[str, list[float]] = defaultdict(list)
    for grp in cohorts(eligible(oof_rows)).values():
        scored = [r for r in grp if _f(r.get("c2_score")) is not None and _f(r.get(TARGET)) is not None]
        if not scored:
            continue
        best = max(scored, key=lambda r: (float(_f(r.get("c2_score"))), str(r.get("symbol"))))
        top1_y[str(best.get("symbol"))].append(float(_f(best.get(TARGET))))
    contrib = sorted(((float(np.mean(vs)), sym) for sym, vs in top1_y.items() if vs), reverse=True)
    top_syms = [c[1] for c in contrib[:3]]

    def drop_sym(syms: list[str]) -> Optional[float]:
        keep = [r for r in oof_rows if str(r.get("symbol")) not in set(syms)]
        return mean_daily_spearman(keep, "c2_score").get("MEAN_DAILY_SPEARMAN")

    sp0 = base.get("MEAN_DAILY_SPEARMAN")
    sp_d1 = drop_days(1)
    sp_d3 = drop_days(3)
    sp_s1 = drop_sym(top_syms[:1])
    sp_s3 = drop_sym(top_syms[:3])
    not_robust = bool(
        sp0 is not None
        and (
            (sp_d3 is not None and float(sp_d3) <= 0)
            or (sp_s3 is not None and float(sp_s3) <= 0)
            or (base.get("positive_day_count") or 0) <= 3
        )
    )
    return {
        "mean_daily_spearman": sp0,
        "ex_top_day": sp_d1,
        "ex_top3_days": sp_d3,
        "ex_top_symbol": sp_s1,
        "ex_top3_symbols": sp_s3,
        "top_symbols_by_n": top_syms,
        "positive_day_count": base.get("positive_day_count"),
        "negative_day_count": base.get("negative_day_count"),
        "RANKING_NOT_ROBUST": not_robust,
    }


def cohorts_by_symbol(rows: list[dict[str, Any]]) -> dict[str, list]:
    g: dict[str, list] = defaultdict(list)
    for r in eligible(rows):
        g[str(r.get("symbol"))].append(r)
    return g


def tod_bucket(anchor: str) -> str:
    a = str(anchor)
    if a in {"09:05", "09:15"}:
        return "OPEN_EARLY"
    if a.startswith("09") or a.startswith("10") or a.startswith("11"):
        return "AM_NORMAL"
    if a in {"15:00", "15:10", "15:20"}:
        return "SESSION_TAIL"
    return "PM_NORMAL"


def tod_split(oof_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for name in ("OPEN_EARLY", "AM_NORMAL", "PM_NORMAL", "SESSION_TAIL"):
        xs = [r for r in oof_rows if tod_bucket(str(r.get("anchor"))) == name]
        m = mean_daily_spearman(xs, "c2_score")
        rk = ranking_block(xs, "c2_score")
        out.append({"bucket": name, **m, "TOP1_TARGET": rk.get("TOP1_TARGET"), "ALL_TARGET": rk.get("ALL_TARGET")})
    return out
