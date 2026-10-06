"""AM LODO 3-arm evaluation. Frozen RF. No Exact. No PnL."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor

from research.am_wait5_two_stage_development import (
    FINAL_SELECTION_N,
    SLOTS,
    STAGE1_SHORTLIST_N,
)
from research.am_wait5_two_stage_interface_precommit.analyze import stage2_sort_key
from research.am_wait5_two_stage_objective_precommit.analyze import percentile_ranks
from research.canonical_entry_performance_rebase.analyze import _f, rank_group, session_of
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.entry_objective_redesign_c3 import MIN_COHORT_N
from research.entry_objective_redesign_c3.oof import normalize_rows
from research.multiobjective_feasibility.analyze import group_cohorts
from research.passive_wait_policy_reassessment.analyze import _median, _rate
from research.wait5_session_target_learnability import FORBIDDEN_FEATURES, RF_CLF_PARAMS, RF_REG_PARAMS
from research.wait5_session_target_learnability.oof import (
    _feat_matrix,
    _pos_proba,
    _reg_pop,
    _train_ok,
)


def select_control(grp: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return rank_group(grp, "current_score")[: int(FINAL_SELECTION_N)]


def select_fill_only(grp: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return rank_group(grp, "fill_score")[: int(FINAL_SELECTION_N)]


def select_two_stage(grp: list[dict[str, Any]]) -> list[dict[str, Any]]:
    short = rank_group(grp, "fill_score")[: int(STAGE1_SHORTLIST_N)]
    usable = [
        r
        for r in short
        if _f(r.get("pred_U")) is not None and _f(r.get("pred_D")) is not None
    ]
    if not usable:
        return short[: int(FINAL_SELECTION_N)]
    u_ranks = percentile_ranks([(float(r["pred_U"]), str(r.get("symbol") or "")) for r in usable])
    d_ranks = percentile_ranks([(float(r["pred_D"]), str(r.get("symbol") or "")) for r in usable])
    scored = []
    for r, ur, dr in zip(usable, u_ranks, d_ranks):
        rec = dict(r)
        rec["U_RANK"] = ur
        rec["D_RANK"] = dr
        rec["JOINT_QUALITY_SCORE"] = min(float(ur), float(dr))
        scored.append(rec)
    scored.sort(
        key=lambda r: stage2_sort_key(
            float(r["fill_score"]),
            float(r["U_RANK"]),
            float(r["D_RANK"]),
            str(r.get("symbol") or ""),
        )
    )
    return scored[: int(FINAL_SELECTION_N)]


def _symset(rows: list[dict[str, Any]]) -> set[str]:
    return {str(r.get("symbol") or "") for r in rows}


def _cohort_block(selected: list[dict[str, Any]]) -> dict[str, Any]:
    n_fill = 0
    exec_u = 0.0
    exec_d = 0.0
    cond_u: list[float] = []
    cond_d: list[float] = []
    for r in selected:
        filled = int(r.get("Y_FILL5") or 0) == 1
        if not filled:
            continue
        n_fill += 1
        u = _f(r.get("U_FILL"))
        d = _f(r.get("D_FILL"))
        exec_u += float(u) if u is not None else 0.0
        exec_d += float(d) if d is not None else 0.0
        if u is not None:
            cond_u.append(float(u))
        if d is not None:
            cond_d.append(float(d))
    slots = float(SLOTS)
    return {
        "n_sel": len(selected),
        "n_fill": n_fill,
        "any_fill": int(n_fill > 0),
        "exec_u": exec_u / slots,
        "exec_d": exec_d / slots,
        "cond_u": cond_u,
        "cond_d": cond_d,
    }


def eval_arm(
    rows: list[dict[str, Any]],
    select_fn,
    days: list[str],
) -> dict[str, Any]:
    by = group_cohorts(rows)
    n_coh = n_sel = n_fill = n_any = 0
    fills_per: list[int] = []
    exec_u: list[float] = []
    exec_d: list[float] = []
    cond_u: list[float] = []
    cond_d: list[float] = []
    daily = {
        d: {"coh": 0, "sel": 0, "fill": 0, "any": 0, "eu": [], "ed": []} for d in days
    }
    selected_by: dict[tuple, list[dict[str, Any]]] = {}
    for (date, sess, an), grp in by.items():
        if len(grp) < int(MIN_COHORT_N):
            continue
        top = select_fn(grp)
        if not top:
            continue
        blk = _cohort_block(top)
        selected_by[(str(date), str(sess), str(an))] = top
        n_coh += 1
        n_sel += int(blk["n_sel"])
        n_fill += int(blk["n_fill"])
        n_any += int(blk["any_fill"])
        fills_per.append(int(blk["n_fill"]))
        exec_u.append(float(blk["exec_u"]))
        exec_d.append(float(blk["exec_d"]))
        cond_u.extend(blk["cond_u"])
        cond_d.extend(blk["cond_d"])
        b = daily.get(str(date))
        if b is not None:
            b["coh"] += 1
            b["sel"] += int(blk["n_sel"])
            b["fill"] += int(blk["n_fill"])
            b["any"] += int(blk["any_fill"])
            b["eu"].append(float(blk["exec_u"]))
            b["ed"].append(float(blk["exec_d"]))
    day_rows = []
    for d in days:
        b = daily[d]
        if int(b["coh"]) <= 0:
            continue
        day_rows.append(
            {
                "date": d,
                "SELECTED_FILL_RATE": _rate(int(b["fill"]), int(b["sel"])),
                "ANY_SELECTED_FILL_COHORT_RATE": _rate(int(b["any"]), int(b["coh"])),
                "EXEC_U": float(np.mean(b["eu"])) if b["eu"] else None,
                "EXEC_D": float(np.mean(b["ed"])) if b["ed"] else None,
                "COHORT_N": int(b["coh"]),
                "SELECTED_N": int(b["sel"]),
                "WOULD_FILL_N": int(b["fill"]),
            }
        )
    return {
        "SELECTED_N": n_sel,
        "WOULD_FILL_N": n_fill,
        "COHORT_N": n_coh,
        "SELECTED_FILL_RATE": _rate(n_fill, n_sel),
        "ANY_SELECTED_FILL_COHORT_RATE": _rate(n_any, n_coh),
        "MEAN_FILLS_PER_COHORT": float(np.mean(fills_per)) if fills_per else None,
        "EXEC_U": float(np.mean(exec_u)) if exec_u else None,
        "EXEC_D": float(np.mean(exec_d)) if exec_d else None,
        "COND_U_MEAN": float(np.mean(cond_u)) if cond_u else None,
        "COND_U_MEDIAN": _median(cond_u),
        "COND_D_MEAN": float(np.mean(cond_d)) if cond_d else None,
        "COND_D_MEDIAN": _median(cond_d),
        "COND_N": len(cond_u),
        "daily": day_rows,
        "selected_by": selected_by,
    }


def distinctness(fill_only: dict[str, Any], two_stage: dict[str, Any]) -> dict[str, Any]:
    fo = fill_only.get("selected_by") or {}
    ts = two_stage.get("selected_by") or {}
    keys = sorted(set(fo) | set(ts))
    eq = ne = 0
    swapped_in = swapped_out = 0
    for k in keys:
        a = _symset(fo.get(k) or [])
        b = _symset(ts.get(k) or [])
        if a == b:
            eq += 1
        else:
            ne += 1
        swapped_in += len(b - a)
        swapped_out += len(a - b)
    tot = eq + ne
    return {
        "TWO_STAGE_EQ_FILL_ONLY_COHORT_N": eq,
        "TWO_STAGE_NE_FILL_ONLY_COHORT_N": ne,
        "TWO_STAGE_NE_FILL_ONLY_COHORT_RATE": _rate(ne, tot),
        "SWAPPED_IN_N": swapped_in,
        "SWAPPED_OUT_N": swapped_out,
    }


def _strip_selected(arm: dict[str, Any]) -> dict[str, Any]:
    out = dict(arm)
    out.pop("selected_by", None)
    return out


def process_am_dev(payload: dict[str, Any]) -> dict[str, Any]:
    spec = dict(payload.get("spec") or {})
    rid = str(spec.get("representation_id") or payload.get("representation_id") or "")
    days = [str(d) for d in (payload.get("days") or ELIGIBLE_DAYS)]
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    print(f"  start AM-DEV {rid}", flush=True)
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
        print(f"    AM-DEV {rid} hold={hold} train={int(ok.sum())} te={len(te_prep)}", flush=True)

    control = eval_arm(scored, select_control, days)
    fill_only = eval_arm(scored, select_fill_only, days)
    two_stage = eval_arm(scored, select_two_stage, days)
    dist = distinctness(fill_only, two_stage)
    print(
        f"  done AM-DEV {rid} folds={fold_n} "
        f"ctrl={control.get('SELECTED_FILL_RATE')} fo={fill_only.get('SELECTED_FILL_RATE')} "
        f"ts={two_stage.get('SELECTED_FILL_RATE')} ne={dist.get('TWO_STAGE_NE_FILL_ONLY_COHORT_RATE')}",
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
    }
