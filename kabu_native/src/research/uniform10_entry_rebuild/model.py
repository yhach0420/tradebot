"""Day-blocked / LODO ENTRY search. No symbol/day/weekday/anchor-specific weights."""
from __future__ import annotations

from itertools import product
from typing import Any, Optional

import numpy as np

from research.uniform10_entry_rebuild import (
    MIN_ABS_CORR,
    MIN_COVERAGE,
    PRIMARY_TARGET,
    SEARCH_SPACE,
)
from research.uniform10_entry_rebuild.features import CATALOG

TARGET = PRIMARY_TARGET


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def coverage_and_corr(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    inv = []
    n = len(rows)
    y = np.asarray([_f(r.get(TARGET)) for r in rows], dtype=float)
    for name, family, formula in CATALOG:
        xs = np.asarray([_f(r.get(name)) for r in rows], dtype=float)
        ok = np.isfinite(xs) & np.isfinite(y)
        cov = float(ok.mean()) if n else 0.0
        corr = None
        if int(ok.sum()) >= 30:
            a = xs[ok]
            b = y[ok]
            if float(np.std(a)) > 1e-12 and float(np.std(b)) > 1e-12:
                corr = float(np.corrcoef(a, b)[0, 1])
        miss = 1.0 - cov
        inv.append(
            {
                "feature": name,
                "family": family,
                "formula": formula,
                "coverage": cov,
                "missing": miss,
                "corr_vs_primary": corr,
                "abs_corr": abs(corr) if corr is not None else None,
                "eligible": cov >= MIN_COVERAGE and corr is not None and abs(corr) >= MIN_ABS_CORR,
            }
        )
    return inv


def redundancy(rows: list[dict[str, Any]], names: list[str]) -> list[dict[str, Any]]:
    out = []
    for i, a in enumerate(names):
        xa = np.asarray([_f(r.get(a)) for r in rows], dtype=float)
        for b in names[i + 1 :]:
            xb = np.asarray([_f(r.get(b)) for r in rows], dtype=float)
            ok = np.isfinite(xa) & np.isfinite(xb)
            if int(ok.sum()) < 30:
                continue
            if float(np.std(xa[ok])) <= 1e-12 or float(np.std(xb[ok])) <= 1e-12:
                continue
            rho = float(np.corrcoef(xa[ok], xb[ok])[0, 1])
            if abs(rho) >= 0.85:
                out.append({"a": a, "b": b, "corr": rho})
    return out


def _cohorts(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    by: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault((str(r.get("date")), str(r.get("anchor"))), []).append(r)
    return by


def _signed_score(row: dict[str, Any], *, feats: list[str], signs: dict[str, float], weights: dict[str, float]) -> Optional[float]:
    acc = 0.0
    wsum = 0.0
    for f in feats:
        v = _f(row.get(f))
        if v is None:
            return None
        w = float(weights.get(f) or 0.0)
        acc += w * float(signs.get(f) or 1.0) * v
        wsum += abs(w)
    if wsum <= 1e-12:
        return None
    return acc


def _apply_norm(cohort: list[dict[str, Any]], feats: list[str], mode: str) -> list[dict[str, Any]]:
    if mode != "cross_sectional_z":
        return [dict(r) for r in cohort]
    out = [dict(r) for r in cohort]
    for f in feats:
        xs = [_f(r.get(f)) for r in out]
        fin = [v for v in xs if v is not None]
        if len(fin) < 3:
            continue
        mu = float(np.mean(fin))
        sd = float(np.std(fin))
        for r, v in zip(out, xs):
            if v is None:
                continue
            r[f] = 0.0 if sd <= 1e-12 else (v - mu) / sd
    return out


def _topk_mean(cohort: list[dict[str, Any]], *, feats, signs, weights, topk: int, thr: Optional[float]) -> Optional[float]:
    scored = []
    for r in cohort:
        s = _signed_score(r, feats=feats, signs=signs, weights=weights)
        y = _f(r.get(TARGET))
        if s is None or y is None:
            continue
        if thr is not None and s < thr:
            continue
        scored.append((s, y, str(r.get("symbol") or "")))
    if not scored:
        return None
    scored.sort(key=lambda z: (-z[0], z[2]))
    pick = scored[: int(topk)]
    return float(np.mean([p[1] for p in pick]))


def _fit_signs_weights(train: list[dict[str, Any]], feats: list[str], weight_mode: str) -> tuple[dict[str, float], dict[str, float]]:
    y = np.asarray([_f(r.get(TARGET)) for r in train], dtype=float)
    signs: dict[str, float] = {}
    weights: dict[str, float] = {}
    for f in feats:
        xs = np.asarray([_f(r.get(f)) for r in train], dtype=float)
        ok = np.isfinite(xs) & np.isfinite(y)
        corr = 0.0
        if int(ok.sum()) >= 20 and float(np.std(xs[ok])) > 1e-12 and float(np.std(y[ok])) > 1e-12:
            corr = float(np.corrcoef(xs[ok], y[ok])[0, 1])
        signs[f] = 1.0 if corr >= 0 else -1.0
        weights[f] = 1.0 if weight_mode == "equal" else abs(corr)
    return signs, weights


def _greedy_features(train: list[dict[str, Any]], inventory: list[dict[str, Any]], max_n: int) -> list[str]:
    cands = [r["feature"] for r in inventory if r.get("eligible")]
    cands.sort(key=lambda n: -abs(next((x.get("corr_vs_primary") or 0.0) for x in inventory if x["feature"] == n)))
    picked: list[str] = []
    # drop raw partner if xs_ version of same stem is also eligible? keep both unless redundant later
    for n in cands:
        if len(picked) >= max_n:
            break
        picked.append(n)
    # drop one of a redundant pair
    red = redundancy(train, picked)
    drop = set()
    for p in red:
        a, b = p["a"], p["b"]
        if a in drop or b in drop:
            continue
        ca = abs(next((x.get("corr_vs_primary") or 0.0) for x in inventory if x["feature"] == a))
        cb = abs(next((x.get("corr_vs_primary") or 0.0) for x in inventory if x["feature"] == b))
        drop.add(a if ca < cb else b)
    return [n for n in picked if n not in drop][:max_n]


def _threshold(train: list[dict[str, Any]], feats, signs, weights, spec) -> Optional[float]:
    if spec is None:
        return None
    ss = []
    for r in train:
        s = _signed_score(r, feats=feats, signs=signs, weights=weights)
        if s is not None:
            ss.append(s)
    if not ss:
        return None
    if spec == "p60":
        return float(np.quantile(ss, 0.60))
    return None


def lodo_metric(rows: list[dict[str, Any]], *, feats, signs, weights, topk, thr, norm) -> dict[str, Any]:
    days = sorted({str(r.get("date")) for r in rows})
    per = []
    for d in days:
        test = [r for r in rows if str(r.get("date")) == d]
        cohorts = _cohorts(test)
        vals = []
        for coh in cohorts.values():
            coh_n = _apply_norm(coh, feats, norm)
            v = _topk_mean(coh_n, feats=feats, signs=signs, weights=weights, topk=topk, thr=thr)
            if v is not None:
                vals.append(v)
        per.append({"date": d, "mean_target_topk": float(np.mean(vals)) if vals else None, "anchors": len(vals)})
    xs = [p["mean_target_topk"] for p in per if p["mean_target_topk"] is not None]
    return {
        "days": per,
        "mean": float(np.mean(xs)) if xs else None,
        "median": float(np.median(xs)) if xs else None,
        "pos_days": sum(1 for x in xs if x > 0),
        "n_days": len(xs),
    }


def search_models(rows: list[dict[str, Any]]) -> dict[str, Any]:
    inv = coverage_and_corr(rows)
    days = sorted({str(r.get("date")) for r in rows})
    space = []
    for max_f, norm, wmode, topk, thr_spec in product(
        SEARCH_SPACE["max_features"],
        SEARCH_SPACE["normalization"],
        SEARCH_SPACE["weight"],
        SEARCH_SPACE["topk"],
        SEARCH_SPACE["threshold"],
    ):
        space.append(
            {
                "max_features": max_f,
                "normalization": norm,
                "weight": wmode,
                "topk": topk,
                "threshold": thr_spec,
            }
        )
    results = []
    for spec in space:
        # architecture fit uses all-but-one mean: for each left-out day, fit on rest, score left-out
        fold_scores = []
        last_arch = None
        for hold in days:
            train = [r for r in rows if str(r.get("date")) != hold]
            test = [r for r in rows if str(r.get("date")) == hold]
            if not train or not test:
                continue
            feats = _greedy_features(train, coverage_and_corr(train), int(spec["max_features"]))
            if len(feats) < 2:
                continue
            signs, weights = _fit_signs_weights(train, feats, spec["weight"])
            train_n = []
            for coh in _cohorts(train).values():
                train_n.extend(_apply_norm(coh, feats, spec["normalization"]))
            thr = _threshold(train_n, feats, signs, weights, spec["threshold"])
            test_vals = []
            for coh in _cohorts(test).values():
                coh_n = _apply_norm(coh, feats, spec["normalization"])
                v = _topk_mean(coh_n, feats=feats, signs=signs, weights=weights, topk=int(spec["topk"]), thr=thr)
                if v is not None:
                    test_vals.append(v)
            if test_vals:
                fold_scores.append(float(np.mean(test_vals)))
            last_arch = {"features": feats, "signs": signs, "weights": weights, "threshold_value": thr}
        mean = float(np.mean(fold_scores)) if fold_scores else None
        results.append({**spec, "lodo_mean_target": mean, "lodo_n": len(fold_scores), "last_arch": last_arch})
    results.sort(key=lambda r: (-(r["lodo_mean_target"] if r["lodo_mean_target"] is not None else -9e9)))
    best = results[0] if results else {}
    # final refit on all development after architecture lock
    locked = None
    if best:
        feats = _greedy_features(rows, inv, int(best["max_features"]))
        signs, weights = _fit_signs_weights(rows, feats, best["weight"])
        all_n = []
        for coh in _cohorts(rows).values():
            all_n.extend(_apply_norm(coh, feats, best["normalization"]))
        thr = _threshold(all_n, feats, signs, weights, best["threshold"])
        locked = {
            "features": feats,
            "signs": signs,
            "weights": weights,
            "normalization": best["normalization"],
            "weight_mode": best["weight"],
            "topk": int(best["topk"]),
            "threshold_spec": best["threshold"],
            "threshold_value": thr,
            "PRIMARY_TARGET": TARGET,
            "final_refit_on_all_development": True,
        }
    return {
        "SEARCH_SPACE": SEARCH_SPACE,
        "inventory": inv,
        "redundancy": redundancy(rows, [x["feature"] for x in inv if x.get("eligible")]),
        "candidates": [
            {k: v for k, v in r.items() if k != "last_arch"}
            for r in results
        ],
        "locked": locked,
        "best_lodo": {k: best.get(k) for k in ("normalization", "weight", "topk", "threshold", "max_features", "lodo_mean_target")},
    }


def score_cohort(rows: list[dict[str, Any]], model: dict[str, Any]) -> list[dict[str, Any]]:
    feats = list(model.get("features") or [])
    signs = dict(model.get("signs") or {})
    weights = dict(model.get("weights") or {})
    topk = int(model.get("topk") or 5)
    thr = model.get("threshold_value")
    normed = _apply_norm(rows, feats, str(model.get("normalization") or "none"))
    scored = []
    for r in normed:
        s = _signed_score(r, feats=feats, signs=signs, weights=weights)
        rec = dict(r)
        rec["rebuild_score"] = s
        scored.append(rec)
    ranked = sorted(
        [r for r in scored if r.get("rebuild_score") is not None],
        key=lambda r: (-float(r["rebuild_score"]), str(r.get("symbol") or "")),
    )
    for i, r in enumerate(ranked):
        r["rebuild_rank"] = i
        r["rebuild_selected"] = i < topk and (thr is None or float(r["rebuild_score"]) >= float(thr))
    by_sym = {str(r.get("symbol")): r for r in ranked}
    out = []
    for r in scored:
        extra = by_sym.get(str(r.get("symbol"))) or r
        out.append(extra)
    return out
