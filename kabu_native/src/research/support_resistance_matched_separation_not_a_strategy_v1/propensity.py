"""Cross-fitted overlap-weighted propensity. Regularized logistic only. No outcome in the fit."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.support_resistance_matched_separation_not_a_strategy_v1 import EVAL_BLOCKS, LOGISTIC_C
from research.support_resistance_matched_separation_not_a_strategy_v1.matchability import PRE_VARS, smd

COV = ("r1", "r3", "r5", "vol_rel", "rng_rel", "tod_min", "gap_num", "mkt_num", "sec_num")


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _mat(rows: list[dict[str, Any]], names: tuple[str, ...], med: dict[str, float] | None = None) -> tuple[np.ndarray, dict[str, float]]:
    meds = dict(med or {})
    if not meds:
        for k in names:
            xs = [float(r[k]) for r in rows if _finite(r.get(k))]
            meds[k] = float(np.median(xs)) if xs else 0.0
    X = np.zeros((len(rows), len(names)), dtype=float)
    for i, r in enumerate(rows):
        for j, k in enumerate(names):
            v = r.get(k)
            X[i, j] = float(v) if _finite(v) else meds[k]
    return X, meds


def _fit(X: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except Exception as exc:
        return {"ok": False, "reason": f"sklearn:{exc}"}
    if X.shape[0] < 80 or len(set(int(v) for v in y)) < 2:
        return {"ok": False, "reason": "n"}
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    clf = LogisticRegression(C=LOGISTIC_C, solver="lbfgs", max_iter=2000, penalty="l2")
    clf.fit(Xs, y)
    return {"ok": True, "model": clf, "scaler": scaler}


def _pred(pack: dict[str, Any], X: np.ndarray) -> np.ndarray:
    if not pack.get("ok"):
        return np.full(X.shape[0], 0.5)
    Xs = pack["scaler"].transform(X)
    return pack["model"].predict_proba(Xs)[:, 1]


def overlap_weighted(events: list[dict[str, Any]], pools: list[dict[str, Any]], *, question: str) -> dict[str, Any]:
    treated = [
        {**e, "treated": 1, "y_p20": e.get("tr_p20_before_m20"), "y_end": e.get("tr_end_bps"), "y_mfe": e.get("tr_mfe_bps")}
        for e in events
        if str(e.get("block") or "") in EVAL_BLOCKS and str(e.get("question") or "") == question
    ]
    controls = [
        p
        for p in pools
        if str(p.get("block") or "") in EVAL_BLOCKS and str(p.get("question") or "") == question
    ]
    rows = treated + controls
    if len(treated) < 40 or len(controls) < 40:
        return {"ok": False, "reason": "n", "question": question}
    ehat = np.full(len(rows), np.nan)
    acc: list[list[float]] = [[] for _ in rows]
    models = {}
    for hold in EVAL_BLOCKS:
        fit_rows = [r for r in rows if str(r.get("block") or "") == hold]
        score_idx = [i for i, r in enumerate(rows) if str(r.get("block") or "") != hold]
        if len(fit_rows) < 40 or not score_idx:
            continue
        Xf, med = _mat(fit_rows, COV)
        yf = np.asarray([int(r.get("treated") or 0) for r in fit_rows], dtype=int)
        pack = _fit(Xf, yf)
        models[hold] = {"ok": pack.get("ok"), "n": len(fit_rows)}
        if not pack.get("ok"):
            continue
        Xs, _ = _mat([rows[i] for i in score_idx], COV, med)
        pred = _pred(pack, Xs)
        for k, i in enumerate(score_idx):
            acc[i].append(float(pred[k]))
    ehat = np.asarray([float(np.mean(v)) if v else 0.5 for v in acc], dtype=float)
    ehat = np.clip(ehat, 0.01, 0.99)
    y = np.asarray([int(r.get("treated") or 0) for r in rows], dtype=int)
    w = np.where(y == 1, 1.0 - ehat, ehat)
    yt = np.asarray([1.0 if r.get("y_p20") else 0.0 if r.get("y_p20") is not None else np.nan for r in rows], dtype=float)
    ok = np.isfinite(yt)
    wt, wc = w * (y == 1) * ok, w * (y == 0) * ok
    if float(wt.sum()) <= 0 or float(wc.sum()) <= 0:
        return {"ok": False, "reason": "weights", "question": question}
    mu_t = float(np.nansum(wt * yt) / wt.sum())
    mu_c = float(np.nansum(wc * yt) / wc.sum())
    ess = float((w[ok].sum() ** 2) / np.sum(w[ok] ** 2)) if np.sum(w[ok] ** 2) > 0 else 0.0
    block_gap: dict[str, float | None] = {}
    for b in EVAL_BLOCKS:
        idx = [i for i, r in enumerate(rows) if str(r.get("block") or "") == b]
        if len(idx) < 20:
            block_gap[b] = None
            continue
        ii = np.asarray(idx, dtype=int)
        wtt = w[ii] * (y[ii] == 1) * np.isfinite(yt[ii])
        wcc = w[ii] * (y[ii] == 0) * np.isfinite(yt[ii])
        if float(wtt.sum()) <= 0 or float(wcc.sum()) <= 0:
            block_gap[b] = None
            continue
        block_gap[b] = float(np.nansum(wtt * yt[ii]) / wtt.sum() - np.nansum(wcc * yt[ii]) / wcc.sum())
    bal_before = {}
    bal_after = {}
    for k in COV:
        xt = np.asarray([float(r[k]) if _finite(r.get(k)) else np.nan for r in treated], dtype=float)
        xc = np.asarray([float(r[k]) if _finite(r.get(k)) else np.nan for r in controls], dtype=float)
        bal_before[k] = smd(xt[np.isfinite(xt)], xc[np.isfinite(xc)])
        xall = np.asarray([float(r[k]) if _finite(r.get(k)) else np.nan for r in rows], dtype=float)
        m = np.isfinite(xall)
        if m.sum() < 10:
            bal_after[k] = None
            continue
        ww = w[m]
        yy = y[m]
        xa = xall[m]
        mt = float(np.sum(ww[yy == 1] * xa[yy == 1]) / max(np.sum(ww[yy == 1]), 1e-12))
        mc = float(np.sum(ww[yy == 0] * xa[yy == 0]) / max(np.sum(ww[yy == 0]), 1e-12))
        vt = float(np.average((xa[yy == 1] - mt) ** 2, weights=ww[yy == 1])) if np.sum(yy == 1) > 2 else 0.0
        vc = float(np.average((xa[yy == 0] - mc) ** 2, weights=ww[yy == 0])) if np.sum(yy == 0) > 2 else 0.0
        den = np.sqrt(0.5 * (vt + vc))
        bal_after[k] = float((mt - mc) / den) if den > 1e-12 else 0.0
    return {
        "ok": True,
        "question": question,
        "model": "l2_logistic",
        "C": LOGISTIC_C,
        "xgboost": False,
        "random_forest": False,
        "neural_net": False,
        "feature_search": False,
        "outcome_used_to_fit_propensity": False,
        "cross_fit": "leave_one_eval_block_out",
        "n_treated": len(treated),
        "n_control": len(controls),
        "overlap_mean_e": float(np.mean(ehat)),
        "effective_sample_size": ess,
        "max_weight": float(np.max(w)),
        "mean_weight": float(np.mean(w)),
        "p99_weight": float(np.percentile(w, 99)),
        "extreme_weights": bool(float(np.max(w)) > 20.0 * float(np.median(w))),
        "gap_p20_before_m20": mu_t - mu_c,
        "treated_p20": mu_t,
        "control_p20": mu_c,
        "block_gap_p20": block_gap,
        "balance_before": bal_before,
        "balance_after": bal_after,
        "block_models": models,
        "unused_pre_vars_not_in_original_match": [v for v in PRE_VARS if v not in COV],
    }


def block_gaps(events: list[dict[str, Any]], *, question: str, matched_only: bool) -> dict[str, float | None]:
    from research.support_resistance_first_interaction_matched_causal_test_v1.stats import pair_stats

    out: dict[str, float | None] = {}
    for b in EVAL_BLOCKS:
        rows = [r for r in events if str(r.get("question") or "") == question and str(r.get("block") or "") == b]
        if matched_only:
            rows = [r for r in rows if r.get("matched")]
        st = pair_stats(rows)
        out[b] = st.get("gap_p20_before_m20")
    return out
