"""One overlap-weighted diagnostic. No feature search. Not an ENTRY filter."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.sr_a2_bracketed_structure_complete_strategy_v1.confound import COV


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


def overlap_available(rows: list[dict[str, Any]]) -> dict[str, Any]:
    avail = [r for r in rows if r.get("opposing_zone_available")]
    skip = [r for r in rows if not r.get("opposing_zone_available")]
    packed = [{**r, "treated": 1 if r.get("opposing_zone_available") else 0} for r in avail + skip]
    if len(avail) < 40 or len(skip) < 40:
        return {"ok": False, "reason": "n", "used_as_entry_filter": False}
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except Exception as exc:
        return {"ok": False, "reason": f"sklearn:{exc}", "used_as_entry_filter": False}
    blocks = ("D1", "D2", "D3", "D4")
    ehat = [[] for _ in packed]
    for hold in blocks:
        fit = [r for r in packed if str(r.get("block") or "") == hold]
        idx = [i for i, r in enumerate(packed) if str(r.get("block") or "") != hold]
        if len(fit) < 40 or not idx:
            continue
        Xf, med = _mat(fit, COV)
        yf = np.asarray([int(r["treated"]) for r in fit], dtype=int)
        if len(set(yf.tolist())) < 2:
            continue
        scaler = StandardScaler()
        clf = LogisticRegression(C=1.0, solver="lbfgs", max_iter=2000, penalty="l2")
        clf.fit(scaler.fit_transform(Xf), yf)
        Xs, _ = _mat([packed[i] for i in idx], COV, med)
        pred = clf.predict_proba(scaler.transform(Xs))[:, 1]
        for k, i in enumerate(idx):
            ehat[i].append(float(pred[k]))
    p = np.clip(np.asarray([float(np.mean(v)) if v else 0.5 for v in ehat]), 0.01, 0.99)
    y = np.asarray([int(r["treated"]) for r in packed], dtype=int)
    w = np.where(y == 1, 1.0 - p, p)
    yt = np.asarray([1.0 if r.get("p20_before_m20") else 0.0 if r.get("p20_before_m20") is not None else np.nan for r in packed])
    ok = np.isfinite(yt)
    wt, wc = w * (y == 1) * ok, w * (y == 0) * ok
    if float(wt.sum()) <= 0 or float(wc.sum()) <= 0:
        return {"ok": False, "reason": "weights", "used_as_entry_filter": False}
    mu_t = float(np.nansum(wt * yt) / wt.sum())
    mu_c = float(np.nansum(wc * yt) / wc.sum())
    ym = np.asarray([float(r["path_mfe_bps"]) if _finite(r.get("path_mfe_bps")) else np.nan for r in packed])
    okm = np.isfinite(ym)
    wtm, wcm = w * (y == 1) * okm, w * (y == 0) * okm
    mfe_gap = None
    if float(wtm.sum()) > 0 and float(wcm.sum()) > 0:
        mfe_gap = float(np.nansum(wtm * ym) / wtm.sum() - np.nansum(wcm * ym) / wcm.sum())
    ess = float((w[ok].sum() ** 2) / np.sum(w[ok] ** 2)) if np.sum(w[ok] ** 2) > 0 else 0.0
    return {
        "ok": True,
        "model": "l2_logistic_overlap",
        "feature_search": False,
        "outcome_in_fit": False,
        "used_as_entry_filter": False,
        "candidate_selected_from_adjusted": False,
        "p20_available_adj": mu_t,
        "p20_skip_adj": mu_c,
        "p20_gap_adj": mu_t - mu_c,
        "mfe_gap_adj": mfe_gap,
        "ess": ess,
        "path_difference_remains": bool((mu_t - mu_c) > 0),
    }
