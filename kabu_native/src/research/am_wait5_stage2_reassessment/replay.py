"""Replay frozen AM development LODO scores. Same RF. No new family. No tuning."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor

from research.am_wait5_two_stage_development.oof import (
    _strip_selected,
    distinctness,
    eval_arm,
    select_control,
    select_fill_only,
    select_two_stage,
)
from research.canonical_entry_performance_rebase.analyze import session_of
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.entry_objective_redesign_c3.oof import normalize_rows
from research.wait5_session_target_learnability import FORBIDDEN_FEATURES, RF_CLF_PARAMS, RF_REG_PARAMS
from research.wait5_session_target_learnability.oof import _feat_matrix, _pos_proba, _reg_pop, _train_ok

SCORED_KEYS = (
    "date",
    "anchor",
    "symbol",
    "session",
    "current_score",
    "Y_FILL5",
    "U_FILL",
    "D_FILL",
    "fill_score",
    "pred_U",
    "pred_D",
)


def slim_scored(r: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in SCORED_KEYS:
        if k not in r:
            continue
        v = r.get(k)
        if k == "Y_FILL5" and v is not None:
            try:
                v = int(v)
            except (TypeError, ValueError):
                pass
        out[k] = v
    return out


def replay_frozen_oof(payload: dict[str, Any]) -> dict[str, Any]:
    """Identical fold/normalizer/RF contract as AM two-stage development. Recovers discarded scores."""
    spec = dict(payload.get("spec") or {})
    rid = str(spec.get("representation_id") or payload.get("representation_id") or "")
    days = [str(d) for d in (payload.get("days") or ELIGIBLE_DAYS)]
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    print(f"  replay-OOF {rid}", flush=True)
    raw = json.loads(Path(payload["rows_path"]).read_text(encoding="utf-8"))
    rows = list(raw.get("rows") or [])
    leak = {
        "PM_ROWS_USED_N": 0,
        "FUTURE_EVENT_USE_N": 0,
        "TARGET_CONTAMINATION_N": 0,
        "HELDOUT_FIT_LEAK_N": 0,
        "AM_NORMALIZER_HELDOUT_ROW_N": 0,
        "STAGE2_NONFILL_TARGET_TRAIN_N": 0,
    }
    for f in feats:
        if str(f) in FORBIDDEN_FEATURES:
            leak["TARGET_CONTAMINATION_N"] += 1
    am = []
    for r in rows:
        if r.get("future_event_use"):
            leak["FUTURE_EVENT_USE_N"] += 1
        if session_of(r) != "AM":
            leak["PM_ROWS_USED_N"] += 1
            continue
        am.append(r)

    scored: list[dict[str, Any]] = []
    fold_n = 0
    for hold in days:
        tr_raw = [r for r in am if str(r.get("date")) != hold]
        te_raw = [r for r in am if str(r.get("date")) == hold]
        leak["AM_NORMALIZER_HELDOUT_ROW_N"] += sum(1 for r in tr_raw if str(r.get("date")) == hold)
        leak["HELDOUT_FIT_LEAK_N"] += sum(1 for r in tr_raw if str(r.get("date")) == hold)
        tr_prep = normalize_rows(tr_raw, feats, norm)
        te_prep = normalize_rows(te_raw, feats, norm)
        tr = _train_ok(tr_prep, feats)
        if len(tr) < 40 or not te_prep:
            for r in te_prep:
                rec = dict(r)
                rec["fill_score"] = None
                rec["pred_U"] = None
                rec["pred_D"] = None
                scored.append(rec)
            continue
        X, ok = _feat_matrix(tr, feats)
        y = np.asarray([int(r.get("Y_FILL5") or 0) for r in tr], dtype=int)
        if int(ok.sum()) < 40:
            for r in te_prep:
                rec = dict(r)
                rec["fill_score"] = None
                rec["pred_U"] = None
                rec["pred_D"] = None
                scored.append(rec)
            continue
        clf = RandomForestClassifier(**RF_CLF_PARAMS)
        clf.fit(X[ok], y[ok])
        Xt, tok = _feat_matrix(te_prep, feats)
        proba = np.full(len(te_prep), np.nan, dtype=float)
        if int(tok.sum()) > 0:
            proba[tok] = _pos_proba(clf, Xt[tok])
        pred_u = np.full(len(te_prep), np.nan, dtype=float)
        pred_d = np.full(len(te_prep), np.nan, dtype=float)
        tru = _reg_pop(tr_prep, feats, "U_FILL")
        trd = _reg_pop(tr_prep, feats, "D_FILL")
        leak["STAGE2_NONFILL_TARGET_TRAIN_N"] += sum(1 for r in tru if int(r.get("Y_FILL5") or 0) != 1)
        leak["STAGE2_NONFILL_TARGET_TRAIN_N"] += sum(1 for r in trd if int(r.get("Y_FILL5") or 0) != 1)
        if len(tru) >= 40:
            Xu, oku = _feat_matrix(tru, feats)
            yu = np.asarray([float(r["U_FILL"]) for r in tru], dtype=float)
            if int(oku.sum()) >= 40:
                ru = RandomForestRegressor(**RF_REG_PARAMS)
                ru.fit(Xu[oku], yu[oku])
                if int(tok.sum()) > 0:
                    pred_u[tok] = np.asarray(ru.predict(Xt[tok]), dtype=float)
        if len(trd) >= 40:
            Xd, okd = _feat_matrix(trd, feats)
            yd = np.asarray([float(r["D_FILL"]) for r in trd], dtype=float)
            if int(okd.sum()) >= 40:
                rd = RandomForestRegressor(**RF_REG_PARAMS)
                rd.fit(Xd[okd], yd[okd])
                if int(tok.sum()) > 0:
                    pred_d[tok] = np.asarray(rd.predict(Xt[tok]), dtype=float)
        for r, pr, pu, pd, good in zip(te_prep, proba, pred_u, pred_d, tok):
            rec = dict(r)
            rec["fill_score"] = float(pr) if bool(good) and pr == pr else None
            rec["pred_U"] = float(pu) if bool(good) and pu == pu else None
            rec["pred_D"] = float(pd) if bool(good) and pd == pd else None
            scored.append(rec)
        fold_n += 1
        print(f"    replay {rid} hold={hold} train={int(ok.sum())} te={len(te_prep)}", flush=True)

    control = eval_arm(scored, select_control, days)
    fill_only = eval_arm(scored, select_fill_only, days)
    two_stage = eval_arm(scored, select_two_stage, days)
    dist = distinctness(fill_only, two_stage)
    print(
        f"  done replay {rid} folds={fold_n} "
        f"fo={fill_only.get('SELECTED_FILL_RATE')} ts={two_stage.get('SELECTED_FILL_RATE')}",
        flush=True,
    )
    return {
        "ok": True,
        "session": "AM",
        "representation_id": rid,
        "feature_set": spec.get("feature_set"),
        "normalization": spec.get("normalization"),
        "n_features": spec.get("n_features"),
        "outer_folds": fold_n,
        "CONTROL": _strip_selected(control),
        "FILL_ONLY": _strip_selected(fill_only),
        "TWO_STAGE": _strip_selected(two_stage),
        "distinctness": dist,
        "integrity": leak,
        "rows": [slim_scored(r) for r in scored],
        "NEW_MODEL_CREATED": False,
        "FROZEN_OOF_REPLAY": True,
    }
