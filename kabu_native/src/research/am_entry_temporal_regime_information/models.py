"""RF fit/score: representation transform on candidate features, then append extras."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
from sklearn.ensemble import RandomForestClassifier

from research.am_entry_information_expansion import FAMILY_RF, JOINT_SCORE_KEY, RF_CLF_PARAMS, TARGET
from research.am_entry_information_expansion.models import _apply_norm, _proba_joint, _y_ok, contamination_n
from research.am_entry_temporal_regime_information.cohort_state import extra_medians
from research.canonical_entry_performance_rebase.analyze import _f
from research.wait5_session_target_learnability.oof import _feat_matrix


def model_features(spec: dict[str, Any]) -> list[str]:
    cand = list(spec.get("candidate_features") or spec.get("features") or [])
    extra = list(spec.get("extra_features") or [])
    return list(dict.fromkeys((*cand, *extra)))


def prepare_rows(
    rows: list[dict[str, Any]],
    spec: dict[str, Any],
    extra_med: dict[str, Optional[float]] | None,
) -> list[dict[str, Any]]:
    cand = list(spec.get("candidate_features") or spec.get("features") or [])
    extra = list(spec.get("extra_features") or [])
    norm = str(spec.get("normalization") or "none")
    prepared = _apply_norm(rows, cand, norm)
    if not extra:
        return prepared
    med = extra_med or {}
    out = []
    for r in prepared:
        rec = dict(r)
        for f in extra:
            if _f(rec.get(f)) is None:
                rec[f] = med.get(f)
        out.append(rec)
    return out


def fit_rf(train: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    cand = list(spec.get("candidate_features") or spec.get("features") or [])
    extra = list(spec.get("extra_features") or [])
    feats = model_features(spec)
    norm = str(spec.get("normalization") or "none")
    med = extra_medians(train, extra)
    prepared = [r for r in train if _y_ok(r)]
    prepared = prepare_rows(prepared, spec, med)
    X, feat_ok = _feat_matrix(prepared, feats)
    y = np.asarray([str(r[TARGET]) for r in prepared], dtype=object)
    ok = feat_ok
    base = {
        "kind": "fail",
        "family": FAMILY_RF,
        "features": feats,
        "candidate_features": cand,
        "extra_features": extra,
        "normalization": norm,
        "extra_medians": med,
        "rf": dict(RF_CLF_PARAMS),
    }
    if int(ok.sum()) < 40 or len(set(y[ok].tolist())) < 2:
        return base
    params = dict(RF_CLF_PARAMS)
    params["n_jobs"] = 1
    clf = RandomForestClassifier(**params)
    clf.fit(X[ok], y[ok])
    return {
        "kind": "rf",
        "family": FAMILY_RF,
        "features": feats,
        "candidate_features": cand,
        "extra_features": extra,
        "normalization": norm,
        "extra_medians": med,
        "rf": dict(RF_CLF_PARAMS),
        "model": clf,
        "train_n": int(ok.sum()),
    }


def score_rf(rows: list[dict[str, Any]], fit: dict[str, Any]) -> list[dict[str, Any]]:
    spec = {
        "candidate_features": list(fit.get("candidate_features") or []),
        "extra_features": list(fit.get("extra_features") or []),
        "normalization": str(fit.get("normalization") or "none"),
    }
    feats = list(fit.get("features") or model_features(spec))
    kind = str(fit.get("kind") or "fail")
    prepared = prepare_rows(rows, spec, dict(fit.get("extra_medians") or {}))
    out: list[dict[str, Any]] = []
    if kind == "fail":
        for r in prepared:
            rec = dict(r)
            rec[JOINT_SCORE_KEY] = None
            rec["P_WIN"] = None
            rec["P_LOSS"] = None
            rec["P_NEUTRAL"] = None
            out.append(rec)
        return out
    X, ok = _feat_matrix(prepared, feats)
    joint = np.full(len(prepared), np.nan, dtype=float)
    p_win = np.full(len(prepared), np.nan, dtype=float)
    p_loss = np.full(len(prepared), np.nan, dtype=float)
    p_neu = np.full(len(prepared), np.nan, dtype=float)
    if int(ok.sum()) > 0:
        j, w, lo, n = _proba_joint(fit.get("model"), X[ok])
        joint[ok] = j
        p_win[ok] = w
        p_loss[ok] = lo
        p_neu[ok] = n
    for r, sc, pw, pl, pn, g in zip(prepared, joint, p_win, p_loss, p_neu, ok):
        rec = dict(r)
        rec[JOINT_SCORE_KEY] = float(sc) if bool(g) and sc == sc else None
        rec["P_WIN"] = float(pw) if bool(g) and pw == pw else None
        rec["P_LOSS"] = float(pl) if bool(g) and pl == pl else None
        rec["P_NEUTRAL"] = float(pn) if bool(g) and pn == pn else None
        out.append(rec)
    return out


__all__ = ["fit_rf", "score_rf", "model_features", "prepare_rows", "contamination_n"]
