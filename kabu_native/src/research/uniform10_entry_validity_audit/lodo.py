"""Leak-corrected nested LODO on the frozen existing search space only."""
from __future__ import annotations

from itertools import product
from typing import Any, Optional

import numpy as np

from research.uniform10_entry_rebuild import MIN_ABS_CORR, MIN_COVERAGE, SEARCH_SPACE
from research.uniform10_entry_rebuild.features import CATALOG
from research.uniform10_entry_rebuild.model import TARGET

FEATURE_NAMES = [n for n, _f, _fo in CATALOG]


def _nan(v: Any) -> float:
    try:
        if v is None or v == "":
            return float("nan")
        x = float(v)
        return x if x == x else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def tables(rows: list[dict[str, Any]]) -> dict[str, Any]:
    dates = np.asarray([str(r.get("date") or "") for r in rows])
    anchors = np.asarray([str(r.get("anchor") or "") for r in rows])
    symbols = np.asarray([str(r.get("symbol") or "") for r in rows])
    y = np.asarray([_nan(r.get(TARGET)) for r in rows], dtype=float)
    cols = [np.asarray([_nan(r.get(n)) for r in rows], dtype=float) for n in FEATURE_NAMES]
    x = np.column_stack(cols) if cols else np.zeros((len(rows), 0), dtype=float)
    return {"dates": dates, "anchors": anchors, "symbols": symbols, "y": y, "x": x}


def _corr_cov(x: np.ndarray, y: np.ndarray, idx: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n_f = x.shape[1]
    cov = np.zeros(n_f, dtype=float)
    corr = np.full(n_f, np.nan, dtype=float)
    if idx.size == 0:
        return cov, corr
    yv = y[idx]
    n = float(idx.size)
    for j in range(n_f):
        xs = x[idx, j]
        ok = np.isfinite(xs) & np.isfinite(yv)
        cov[j] = float(ok.mean()) if n else 0.0
        if int(ok.sum()) < 30:
            continue
        a = xs[ok]
        b = yv[ok]
        if float(a.std()) <= 1e-12 or float(b.std()) <= 1e-12:
            continue
        corr[j] = float(np.corrcoef(a, b)[0, 1])
    return cov, corr


def _eligible(cov: np.ndarray, corr: np.ndarray) -> list[int]:
    out = []
    for j, (c, r) in enumerate(zip(cov, corr)):
        if c >= MIN_COVERAGE and np.isfinite(r) and abs(float(r)) >= MIN_ABS_CORR:
            out.append(j)
    out.sort(key=lambda j: -abs(float(corr[j])))
    return out


def _redundancy_drop(x: np.ndarray, idx: np.ndarray, picked: list[int], corr: np.ndarray) -> list[int]:
    drop: set[int] = set()
    for a_i, ja in enumerate(picked):
        xa = x[idx, ja]
        for jb in picked[a_i + 1 :]:
            if ja in drop or jb in drop:
                continue
            xb = x[idx, jb]
            ok = np.isfinite(xa) & np.isfinite(xb)
            if int(ok.sum()) < 30:
                continue
            if float(xa[ok].std()) <= 1e-12 or float(xb[ok].std()) <= 1e-12:
                continue
            rho = float(np.corrcoef(xa[ok], xb[ok])[0, 1])
            if abs(rho) >= 0.85:
                ca = abs(float(corr[ja])) if np.isfinite(corr[ja]) else 0.0
                cb = abs(float(corr[jb])) if np.isfinite(corr[jb]) else 0.0
                drop.add(ja if ca < cb else jb)
    return [j for j in picked if j not in drop]


def _greedy(x: np.ndarray, y: np.ndarray, idx: np.ndarray, max_n: int) -> tuple[list[int], np.ndarray, np.ndarray]:
    cov, corr = _corr_cov(x, y, idx)
    elig = _eligible(cov, corr)[: max(max_n * 3, max_n)]
    picked = elig[:max_n]
    picked = _redundancy_drop(x, idx, picked, corr)[:max_n]
    return picked, cov, corr


def _signs_weights(
    x: np.ndarray, y: np.ndarray, idx: np.ndarray, feats: list[int], weight_mode: str
) -> tuple[np.ndarray, np.ndarray]:
    signs = np.ones(len(feats), dtype=float)
    weights = np.ones(len(feats), dtype=float)
    yv = y[idx]
    for k, j in enumerate(feats):
        xs = x[idx, j]
        ok = np.isfinite(xs) & np.isfinite(yv)
        c = 0.0
        if int(ok.sum()) >= 20 and float(xs[ok].std()) > 1e-12 and float(yv[ok].std()) > 1e-12:
            c = float(np.corrcoef(xs[ok], yv[ok])[0, 1])
        signs[k] = 1.0 if c >= 0 else -1.0
        weights[k] = 1.0 if weight_mode == "equal" else abs(c)
    return signs, weights


def _cohort_slices(dates: np.ndarray, anchors: np.ndarray, idx: np.ndarray) -> list[np.ndarray]:
    if idx.size == 0:
        return []
    keys = dates[idx] + "|" + anchors[idx]
    out = []
    for k in np.unique(keys):
        out.append(idx[keys == k])
    return out


def _apply_norm_block(block: np.ndarray, mode: str) -> np.ndarray:
    if mode != "cross_sectional_z":
        return block
    z = np.array(block, copy=True)
    for j in range(z.shape[1]):
        col = z[:, j]
        ok = np.isfinite(col)
        if int(ok.sum()) < 3:
            continue
        mu = float(np.mean(col[ok]))
        sd = float(np.std(col[ok]))
        if sd <= 1e-12:
            z[ok, j] = 0.0
        else:
            z[ok, j] = (col[ok] - mu) / sd
    return z


def _threshold_train(
    x: np.ndarray,
    idx: np.ndarray,
    dates: np.ndarray,
    anchors: np.ndarray,
    feats: list[int],
    signs: np.ndarray,
    weights: np.ndarray,
    norm: str,
    thr_spec: Optional[str],
) -> Optional[float]:
    if thr_spec is None:
        return None
    ss: list[float] = []
    for sl in _cohort_slices(dates, anchors, idx):
        block = _apply_norm_block(x[sl][:, feats], norm)
        sc = block * signs.reshape(1, -1) * weights.reshape(1, -1)
        good = np.all(np.isfinite(block), axis=1)
        vals = np.sum(sc, axis=1)
        ss.extend(float(v) for v, g in zip(vals, good) if g)
    if not ss:
        return None
    if thr_spec == "p60":
        return float(np.quantile(ss, 0.60))
    return None


def _topk_mean(
    x: np.ndarray,
    y: np.ndarray,
    sl: np.ndarray,
    feats: list[int],
    signs: np.ndarray,
    weights: np.ndarray,
    symbols: np.ndarray,
    norm: str,
    topk: int,
    thr: Optional[float],
) -> Optional[float]:
    block = _apply_norm_block(x[sl][:, feats], norm)
    if block.size == 0 or not feats:
        return None
    sc = np.sum(block * signs.reshape(1, -1) * weights.reshape(1, -1), axis=1)
    yy = y[sl]
    good = np.all(np.isfinite(block), axis=1) & np.isfinite(sc) & np.isfinite(yy)
    if thr is not None:
        good = good & (sc >= float(thr))
    if not bool(good.any()):
        return None
    order = np.lexsort((symbols[sl], -sc))
    picked = []
    for i in order:
        if not good[i]:
            continue
        picked.append(float(yy[i]))
        if len(picked) >= int(topk):
            break
    if not picked:
        return None
    return float(np.mean(picked))


def _eval_spec(
    tbl: dict[str, Any],
    train_idx: np.ndarray,
    test_idx: np.ndarray,
    spec: dict[str, Any],
) -> Optional[float]:
    x, y = tbl["x"], tbl["y"]
    feats, _cov, _corr = _greedy(x, y, train_idx, int(spec["max_features"]))
    if len(feats) < 2:
        return None
    signs, weights = _signs_weights(x, y, train_idx, feats, spec["weight"])
    thr = _threshold_train(
        x, train_idx, tbl["dates"], tbl["anchors"], feats, signs, weights, spec["normalization"], spec["threshold"]
    )
    vals = []
    for sl in _cohort_slices(tbl["dates"], tbl["anchors"], test_idx):
        v = _topk_mean(
            x, y, sl, feats, signs, weights, tbl["symbols"], spec["normalization"], int(spec["topk"]), thr
        )
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    return float(np.mean(vals))


def _space() -> list[dict[str, Any]]:
    out = []
    for max_f, norm, wmode, topk, thr_spec in product(
        SEARCH_SPACE["max_features"],
        SEARCH_SPACE["normalization"],
        SEARCH_SPACE["weight"],
        SEARCH_SPACE["topk"],
        SEARCH_SPACE["threshold"],
    ):
        out.append(
            {
                "max_features": max_f,
                "normalization": norm,
                "weight": wmode,
                "topk": topk,
                "threshold": thr_spec,
            }
        )
    return out


def nested_lodo(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Outer day held out of architecture selection. Existing search space only."""
    tbl = tables(rows)
    days = sorted({str(d) for d in tbl["dates"]})
    space = _space()
    outer = []
    for hold in days:
        print(f"nested LODO outer hold={hold}", flush=True)
        train_idx = np.flatnonzero(tbl["dates"] != hold)
        test_idx = np.flatnonzero(tbl["dates"] == hold)
        train_days = sorted({str(d) for d in tbl["dates"][train_idx]})
        inner_best = None
        inner_best_mean = None
        inner_rows = []
        for spec in space:
            inner_vals = []
            for inner_hold in train_days:
                inner_train = np.flatnonzero((tbl["dates"] != hold) & (tbl["dates"] != inner_hold))
                inner_test = np.flatnonzero((tbl["dates"] != hold) & (tbl["dates"] == inner_hold))
                v = _eval_spec(tbl, inner_train, inner_test, spec)
                if v is not None:
                    inner_vals.append(v)
            mean_i = float(np.mean(inner_vals)) if inner_vals else None
            inner_rows.append({**spec, "inner_mean": mean_i, "inner_n": len(inner_vals)})
            if mean_i is None:
                continue
            if inner_best_mean is None or mean_i > inner_best_mean:
                inner_best_mean = mean_i
                inner_best = spec
        outer_v = None
        frozen = None
        if inner_best is not None:
            x, y = tbl["x"], tbl["y"]
            feats, _c, corr = _greedy(x, y, train_idx, int(inner_best["max_features"]))
            signs, weights = _signs_weights(x, y, train_idx, feats, inner_best["weight"])
            thr = _threshold_train(
                x,
                train_idx,
                tbl["dates"],
                tbl["anchors"],
                feats,
                signs,
                weights,
                inner_best["normalization"],
                inner_best["threshold"],
            )
            frozen = {
                "features": [FEATURE_NAMES[j] for j in feats],
                "signs": {FEATURE_NAMES[j]: float(s) for j, s in zip(feats, signs)},
                "weights": {FEATURE_NAMES[j]: float(w) for j, w in zip(feats, weights)},
                **inner_best,
                "threshold_value": thr,
            }
            vals = []
            for sl in _cohort_slices(tbl["dates"], tbl["anchors"], test_idx):
                v = _topk_mean(
                    x,
                    y,
                    sl,
                    feats,
                    signs,
                    weights,
                    tbl["symbols"],
                    inner_best["normalization"],
                    int(inner_best["topk"]),
                    thr,
                )
                if v is not None:
                    vals.append(v)
            outer_v = float(np.mean(vals)) if vals else None
        outer.append(
            {
                "held_out_day": hold,
                "selected_spec": inner_best,
                "inner_mean_of_selected": inner_best_mean,
                "outer_mean_target": outer_v,
                "frozen": frozen,
            }
        )
    xs = [r["outer_mean_target"] for r in outer if r["outer_mean_target"] is not None]
    return {
        "protocol": "nested_LODO_existing_search_space_only",
        "n_specs": len(space),
        "days": outer,
        "mean": float(np.mean(xs)) if xs else None,
        "median": float(np.median(xs)) if xs else None,
        "pos_days": int(sum(1 for x in xs if x > 0)),
        "n_days": len(xs),
        "SEARCH_SPACE": SEARCH_SPACE,
    }


def leak_table() -> list[dict[str, Any]]:
    """How the published C search used held-out days."""
    return [
        {
            "decision": "feature_eligibility",
            "fold_fit": "TRAIN_ONLY (coverage_and_corr on train)",
            "architecture_selection": "not used directly",
            "locked_published_model": "ALL_DEVELOPMENT_DAYS (coverage_and_corr(rows))",
            "held_out_used": True,
            "note": "Locked C features used global eligibility after spec pick.",
        },
        {
            "decision": "feature_direction",
            "fold_fit": "TRAIN_ONLY Pearson sign (not Spearman despite SEARCH_SPACE text)",
            "architecture_selection": "n/a",
            "locked_published_model": "ALL_DEVELOPMENT_DAYS",
            "held_out_used": True,
            "note": "SEARCH_SPACE string says global Spearman; code uses train Pearson for folds and all-days Pearson for lock.",
        },
        {
            "decision": "feature_ranking_greedy",
            "fold_fit": "TRAIN_ONLY",
            "architecture_selection": "n/a",
            "locked_published_model": "ALL_DEVELOPMENT_DAYS",
            "held_out_used": True,
            "note": "Published feature list is the all-days greedy list.",
        },
        {
            "decision": "normalization_mean_std",
            "fold_fit": "cross_sectional_z uses scoring-cohort mean/std (labels unused)",
            "architecture_selection": "n/a",
            "locked_published_model": "same cohort-at-score-time",
            "held_out_used": False,
            "note": "CS-z is contemporaneous within-anchor. Not a label leak.",
        },
        {
            "decision": "weight",
            "fold_fit": "TRAIN_ONLY",
            "architecture_selection": "n/a",
            "locked_published_model": "ALL_DEVELOPMENT_DAYS",
            "held_out_used": True,
            "note": "equal weights in the locked spec, so abs_corr weights unused in C lock.",
        },
        {
            "decision": "threshold",
            "fold_fit": "TRAIN_ONLY if p60",
            "architecture_selection": "n/a",
            "locked_published_model": "none (threshold_spec null)",
            "held_out_used": False,
            "note": "Locked C uses threshold none.",
        },
        {
            "decision": "TopK_selection",
            "fold_fit": "spec hyperparameter; scored on held-out in original search",
            "architecture_selection": "HELD-OUT mean picks topk 3 vs 5",
            "locked_published_model": "topk=3 from that pick, then in-sample ranking",
            "held_out_used": True,
            "note": "Spec competition uses held-out scores.",
        },
        {
            "decision": "candidate_architecture_selection",
            "fold_fit": "each spec's lodo_mean_target averages held-out days",
            "architecture_selection": "argmin/argmax over those held-out means",
            "locked_published_model": "winner refit on all development",
            "held_out_used": True,
            "note": "This is the primary LODO leak. Winner-curse spec selection.",
        },
        {
            "decision": "published_ranking_metrics",
            "fold_fit": "n/a",
            "architecture_selection": "n/a",
            "locked_published_model": "attach_rebuild_scores on full panel (in-sample)",
            "held_out_used": True,
            "note": "Top1=0.00221623 is not a LODO metric.",
        },
    ]
