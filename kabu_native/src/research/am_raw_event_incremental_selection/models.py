"""RF fit/score: representation on candidate features, then snapshot extras, then raw extras."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
from sklearn.ensemble import RandomForestClassifier

from research.am_entry_information_expansion import FAMILY_RF, RF_CLF_PARAMS, TARGET
from research.am_entry_information_expansion.models import _y_ok, contamination_n
from research.am_entry_temporal_regime_information.cohort_state import extra_medians
from research.am_entry_temporal_regime_information.models import prepare_rows, score_rf
from research.am_raw_event_incremental_selection import RAW_EVENT_AVAILABLE
from research.canonical_entry_performance_rebase.analyze import _f
from research.raw_event_prediction_probe import RAW_DESCRIPTORS
from research.wait5_session_target_learnability.oof import _feat_matrix

RAW_EXTRA_SET = set(RAW_DESCRIPTORS) | {RAW_EVENT_AVAILABLE}


def model_features(spec: dict[str, Any]) -> list[str]:
    cand = list(spec.get("candidate_features") or spec.get("features") or [])
    extra = list(spec.get("extra_features") or [])
    return list(dict.fromkeys((*cand, *extra)))


def extra_medians_mixed(train: list[dict[str, Any]], extra: list[str]) -> dict[str, Optional[float]]:
    snap = [f for f in extra if f not in RAW_EXTRA_SET]
    raw = [f for f in extra if f in RAW_EXTRA_SET]
    med = extra_medians(train, snap)
    for f in raw:
        xs = []
        for r in train:
            v = _f(r.get(f))
            if v is not None:
                xs.append(float(v))
        med[f] = float(np.median(xs)) if xs else None
    return med


def fit_rf(train: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    cand = list(spec.get("candidate_features") or spec.get("features") or [])
    extra = list(spec.get("extra_features") or [])
    feats = model_features(spec)
    norm = str(spec.get("normalization") or "none")
    med = extra_medians_mixed(train, extra)
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


def missingness_raw(rows: list[dict[str, Any]], extra_feats: list[str], days: list[str]) -> list[dict[str, Any]]:
    from collections import defaultdict

    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_day[str(r.get("date") or "")].append(r)
    stats = {f: {"VALID_N": 0, "IMPUTED_N": 0} for f in extra_feats}
    for outer in days:
        train = []
        for d in days:
            if d != outer:
                train.extend(by_day.get(d) or [])
        med = extra_medians_mixed(train, extra_feats)
        for r in by_day.get(str(outer) or "") or []:
            for f in extra_feats:
                if _f(r.get(f)) is not None:
                    stats[f]["VALID_N"] += 1
                elif med.get(f) is not None:
                    stats[f]["IMPUTED_N"] += 1
                else:
                    stats[f]["IMPUTED_N"] += 1
    return [{"feature": f, **stats[f]} for f in extra_feats]


__all__ = [
    "fit_rf",
    "score_rf",
    "model_features",
    "prepare_rows",
    "contamination_n",
    "extra_medians_mixed",
    "missingness_raw",
]
