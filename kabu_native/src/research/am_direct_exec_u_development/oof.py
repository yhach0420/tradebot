"""LODO EXEC_U_TARGET regressor and 3-arm evaluation. No Stage2. No P_FILL refit. No oracle."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestRegressor

from research.am_direct_exec_u_development import FINAL_SELECTION_N
from research.am_wait5_two_stage_development.oof import eval_arm, select_control, select_fill_only
from research.canonical_entry_performance_rebase.analyze import _f, rank_group, row_key, session_of
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.entry_objective_redesign_c3.oof import normalize_rows, spearman
from research.passive_wait_policy_reassessment.analyze import _median, _rate
from research.wait5_session_target_learnability import FORBIDDEN_FEATURES, RF_REG_PARAMS
from research.wait5_session_target_learnability.oof import _feat_matrix, _train_ok

EXTRA_FORBIDDEN = FORBIDDEN_FEATURES + (
    "TARGET_EXEC_U",
    "pred_EXEC_U",
    "EXEC_U_TARGET",
    "fill_score",
    "pred_U",
    "pred_D",
)


def select_direct(grp: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return rank_group(grp, "pred_EXEC_U")[: int(FINAL_SELECTION_N)]


def attach_targets(rows: list[dict[str, Any]]) -> dict[str, int]:
    miss = nonzero = zero = 0
    for r in rows:
        try:
            yi = int(r.get("Y_FILL5"))
        except (TypeError, ValueError):
            yi = None
        if yi not in (0, 1):
            miss += 1
            r["TARGET_EXEC_U"] = None
            continue
        if yi == 1:
            u = _f(r.get("U_FILL"))
            if u is None:
                miss += 1
                r["TARGET_EXEC_U"] = None
                continue
            r["TARGET_EXEC_U"] = float(u)
        else:
            r["TARGET_EXEC_U"] = 0.0
        if abs(float(r["TARGET_EXEC_U"])) > 0.0:
            nonzero += 1
        else:
            zero += 1
    return {
        "TARGET_ROW_N": len(rows),
        "TARGET_NONZERO_N": nonzero,
        "TARGET_ZERO_N": zero,
        "TARGET_MISSING_N": miss,
    }


def join_fill_score(rows: list[dict[str, Any]], scored: list[dict[str, Any]]) -> int:
    by = {row_key(r): r.get("fill_score") for r in scored}
    miss = 0
    for r in rows:
        k = row_key(r)
        if k not in by:
            miss += 1
            r["fill_score"] = None
            continue
        r["fill_score"] = by.get(k)
    return miss


def _symset(rows: list[dict[str, Any]]) -> set[str]:
    return {str(r.get("symbol") or "") for r in rows}


def distinctness(fill_only: dict[str, Any], direct: dict[str, Any]) -> dict[str, Any]:
    fo = fill_only.get("selected_by") or {}
    du = direct.get("selected_by") or {}
    keys = sorted(set(fo) | set(du))
    eq = ne = 0
    swapped_in = swapped_out = 0
    for k in keys:
        a = _symset(fo.get(k) or [])
        b = _symset(du.get(k) or [])
        if a == b:
            eq += 1
        else:
            ne += 1
        swapped_in += len(b - a)
        swapped_out += len(a - b)
    tot = eq + ne
    return {
        "DIRECT_EQ_FILL_ONLY_COHORT_N": eq,
        "DIRECT_NE_FILL_ONLY_COHORT_N": ne,
        "DIRECT_NE_FILL_ONLY_COHORT_RATE": _rate(ne, tot),
        "SWAPPED_IN_N": swapped_in,
        "SWAPPED_OUT_N": swapped_out,
    }


def _strip(arm: dict[str, Any]) -> dict[str, Any]:
    out = dict(arm)
    out.pop("selected_by", None)
    return out


def slim_scored_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = (
        "date",
        "anchor",
        "symbol",
        "session",
        "fill_score",
        "pred_EXEC_U",
        "Y_FILL5",
        "U_FILL",
        "D_FILL",
        "current_score",
    )
    return [{k: r.get(k) for k in keys} for r in rows]


def slim_selected(arm: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for (date, sess, an), grp in (arm.get("selected_by") or {}).items():
        for r in grp:
            rows.append(
                {
                    "date": date,
                    "session": sess,
                    "anchor": an,
                    "symbol": r.get("symbol"),
                    "fill_score": r.get("fill_score"),
                    "pred_EXEC_U": r.get("pred_EXEC_U"),
                    "Y_FILL5": r.get("Y_FILL5"),
                    "U_FILL": r.get("U_FILL"),
                    "D_FILL": r.get("D_FILL"),
                }
            )
    return rows


def _oof_spearman(scored: list[dict[str, Any]], days: list[str]) -> dict[str, Any]:
    pred: list[float] = []
    act: list[float] = []
    daily = {d: ([], []) for d in days}
    for r in scored:
        p = _f(r.get("pred_EXEC_U"))
        y = _f(r.get("TARGET_EXEC_U"))
        if p is None or y is None:
            continue
        pred.append(float(p))
        act.append(float(y))
        d = str(r.get("date") or "")
        if d in daily:
            daily[d][0].append(float(p))
            daily[d][1].append(float(y))
    day_rows = []
    xs: list[float] = []
    for d in days:
        sp = spearman(daily[d][0], daily[d][1])
        day_rows.append({"date": d, "SPEARMAN": sp})
        if sp is not None:
            xs.append(float(sp))
    return {
        "OVERALL_OOF_SPEARMAN": spearman(pred, act),
        "DAILY_MEDIAN_SPEARMAN": _median(xs),
        "POSITIVE_SPEARMAN_DAYS": sum(1 for v in xs if v > 0),
        "NEGATIVE_SPEARMAN_DAYS": sum(1 for v in xs if v < 0),
        "ZERO_SPEARMAN_DAYS": sum(1 for v in xs if v == 0),
        "daily": day_rows,
    }


def process_am_direct(payload: dict[str, Any]) -> dict[str, Any]:
    spec = dict(payload.get("spec") or {})
    rid = str(spec.get("representation_id") or payload.get("representation_id") or "")
    days = [str(d) for d in (payload.get("days") or ELIGIBLE_DAYS)]
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    print(f"  start AM-DIRECT {rid}", flush=True)
    raw = json.loads(Path(payload["rows_path"]).read_text(encoding="utf-8"))
    rows = list(raw.get("rows") or [])
    scored_raw = json.loads(Path(payload["fill_score_path"]).read_text(encoding="utf-8"))
    join_miss = join_fill_score(rows, list(scored_raw.get("rows") or []))
    leak = {
        "PM_ROWS_USED_N": 0,
        "FUTURE_EVENT_USE_N": 0,
        "TARGET_CONTAMINATION_N": 0,
        "HELDOUT_FIT_LEAK_N": 0,
        "NORMALIZER_HELDOUT_ROW_N": 0,
        "TARGET_MISSING_N": 0,
        "MODEL_HYPERPARAMETER_SEARCH_N": 0,
        "STAGE1_USE_N": 0,
        "STAGE2_USE_N": 0,
        "ORACLE_SELECTION_USE_N": 0,
        "JOIN_FILL_SCORE_MISS_N": int(join_miss),
    }
    for f in feats:
        if str(f) in EXTRA_FORBIDDEN:
            leak["TARGET_CONTAMINATION_N"] += 1
    am = []
    for r in rows:
        if r.get("future_event_use"):
            leak["FUTURE_EVENT_USE_N"] += 1
        if session_of(r) != "AM":
            leak["PM_ROWS_USED_N"] += 1
            continue
        if _f(r.get("TARGET_EXEC_U")) is None:
            leak["TARGET_MISSING_N"] += 1
            continue
        am.append(r)

    scored: list[dict[str, Any]] = []
    fold_n = 0
    for hold in days:
        tr_raw = [r for r in am if str(r.get("date")) != hold]
        te_raw = [r for r in am if str(r.get("date")) == hold]
        leak["NORMALIZER_HELDOUT_ROW_N"] += sum(1 for r in tr_raw if str(r.get("date")) == hold)
        leak["HELDOUT_FIT_LEAK_N"] += sum(1 for r in tr_raw if str(r.get("date")) == hold)
        tr_prep = normalize_rows(tr_raw, feats, norm)
        te_prep = normalize_rows(te_raw, feats, norm)
        tr = [r for r in _train_ok(tr_prep, feats) if _f(r.get("TARGET_EXEC_U")) is not None]
        pred = np.full(len(te_prep), np.nan, dtype=float)
        if len(tr) >= 40 and te_prep:
            X, ok = _feat_matrix(tr, feats)
            y = np.asarray([float(r["TARGET_EXEC_U"]) for r in tr], dtype=float)
            if int(ok.sum()) >= 40:
                model = RandomForestRegressor(**RF_REG_PARAMS)
                model.fit(X[ok], y[ok])
                Xt, tok = _feat_matrix(te_prep, feats)
                if int(tok.sum()) > 0:
                    pred[tok] = np.asarray(model.predict(Xt[tok]), dtype=float)
                fold_n += 1
                print(f"    AM-DIRECT {rid} hold={hold} train={int(ok.sum())} te={len(te_prep)}", flush=True)
        for r, pr in zip(te_prep, pred):
            rec = dict(r)
            rec["pred_EXEC_U"] = float(pr) if pr == pr else None
            scored.append(rec)

    control = eval_arm(scored, select_control, days)
    fill_only = eval_arm(scored, select_fill_only, days)
    direct = eval_arm(scored, select_direct, days)
    dist = distinctness(fill_only, direct)
    sp = _oof_spearman(scored, days)
    print(
        f"  done AM-DIRECT {rid} folds={fold_n} "
        f"ctrl={control.get('SELECTED_FILL_RATE')} fo={fill_only.get('SELECTED_FILL_RATE')} "
        f"du={direct.get('SELECTED_FILL_RATE')} ne={dist.get('DIRECT_NE_FILL_ONLY_COHORT_RATE')} "
        f"sp={sp.get('OVERALL_OOF_SPEARMAN')}",
        flush=True,
    )
    out = {
        "ok": True,
        "session": "AM",
        "representation_id": rid,
        "feature_set": spec.get("feature_set"),
        "normalization": spec.get("normalization"),
        "n_features": spec.get("n_features"),
        "outer_folds": fold_n,
        "CONTROL": _strip(control),
        "FILL_ONLY": _strip(fill_only),
        "DIRECT_EXEC_U": _strip(direct),
        "distinctness": dist,
        "spearman": {k: v for k, v in sp.items() if k != "daily"},
        "spearman_daily": sp.get("daily"),
        "integrity": leak,
    }
    if bool(payload.get("keep_selected")):
        out["selected"] = {
            "CONTROL": slim_selected(control),
            "FILL_ONLY": slim_selected(fill_only),
            "DIRECT_EXEC_U": slim_selected(direct),
        }
    if bool(payload.get("keep_scored")):
        out["scored"] = slim_scored_rows(scored)
    return out
