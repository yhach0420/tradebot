"""Univariate, quintiles, blocks, tree, LOBO, 2-way. No strategy freeze."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.post_open_causal_upside_mechanism_discovery_v1 import (
    CASE_FOUND,
    CASE_NONE,
    DEVELOPMENT_DAYS,
    FEATURE_IDS,
    LEAF_MIN_DAY_N,
    LEAF_MIN_N,
    LEAF_MIN_SYMBOL_N,
    MAX_TREE_FEATURES,
    MIN_BLOCK_AGREE,
    NEXT_FOUND,
    NEXT_NONE,
    TREE_MAX_DEPTH,
    TREE_MAX_LEAF_NODES,
    TREE_MIN_LEAF_N,
)
from research.simple_tech_entry_family.portfolio import _sym
from research.systematic_state_transition_library_precommit_v1 import FOLD_BLOCKS


def _num(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _sign(x: Optional[float]) -> Optional[int]:
    if x is None or x != x:
        return None
    if abs(float(x)) < 1e-12:
        return 0
    return 1 if float(x) > 0 else -1


def executable_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("executable")]


def primary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in executable_rows(rows):
        if _num(r.get("EXEC_MARKOUT_10M_BPS")) is None:
            continue
        if _num(r.get("PATH_EDGE_10M_BPS")) is None:
            continue
        out.append(r)
    return out


def _spearman(xs: list[float], ys: list[float]) -> Optional[float]:
    if len(xs) < 30:
        return None
    a = np.asarray(xs, dtype=float)
    b = np.asarray(ys, dtype=float)
    try:
        from scipy.stats import spearmanr

        r, _p = spearmanr(a, b)
        return None if r != r else float(r)
    except Exception:
        ra = np.argsort(np.argsort(a)).astype(float)
        rb = np.argsort(np.argsort(b)).astype(float)
        if float(np.std(ra)) < 1e-12 or float(np.std(rb)) < 1e-12:
            return None
        return float(np.corrcoef(ra, rb)[0, 1])


def population(rows: list[dict[str, Any]]) -> dict[str, Any]:
    prim = primary_rows(rows)
    exe = executable_rows(rows)
    marks = [_num(r.get("EXEC_MARKOUT_10M_BPS")) for r in prim]
    marks = [x for x in marks if x is not None]
    up = [1 if r.get("UP_DOMINANT_10M") else 0 for r in prim]
    pos = [1 if r.get("EXEC_POSITIVE_10M") else 0 for r in prim]
    days = {str(r.get("date")) for r in prim}
    syms = {_sym(r) for r in prim}
    return {
        "anchor_n": len(rows),
        "executable_n": len(exe),
        "primary_n": len(prim),
        "day_n": len(days),
        "symbol_n": len(syms),
        "EXEC_MARKOUT_10M_mean": float(np.mean(marks)) if marks else None,
        "EXEC_MARKOUT_10M_median": float(np.median(marks)) if marks else None,
        "UP_DOMINANT_rate": (sum(up) / len(up)) if up else None,
        "EXEC_POSITIVE_rate": (sum(pos) / len(pos)) if pos else None,
    }


def feature_integrity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prim = primary_rows(rows)
    n = len(prim)
    out = []
    for fid in FEATURE_IDS:
        finite = 0
        days: set[str] = set()
        syms: set[str] = set()
        for r in prim:
            v = _num(r.get(fid))
            if v is None:
                continue
            finite += 1
            days.add(str(r.get("date") or ""))
            syms.add(_sym(r))
        out.append(
            {
                "feature": fid,
                "availability_clock": "INGRESS",
                "n": n,
                "finite_n": finite,
                "finite_rate": 0.0 if n <= 0 else finite / n,
                "missing_rate": 0.0 if n <= 0 else 1.0 - finite / n,
                "day_coverage": len(days),
                "symbol_coverage": len(syms),
            }
        )
    return out


def top_symbol(rows: list[dict[str, Any]]) -> Optional[str]:
    by: dict[str, int] = defaultdict(int)
    for r in rows:
        by[_sym(r)] += 1
    if not by:
        return None
    return max(by.keys(), key=lambda s: by[s])


def _pairs(rows: list[dict[str, Any]], fid: str, ykey: str) -> tuple[list[float], list[float]]:
    xs = []
    ys = []
    for r in rows:
        x = _num(r.get(fid))
        y = _num(r.get(ykey)) if ykey not in ("UP_DOMINANT_10M", "EXEC_POSITIVE_10M") else (1.0 if r.get(ykey) else 0.0)
        if x is None or y is None:
            continue
        xs.append(x)
        ys.append(float(y))
    return xs, ys


def quintiles(rows: list[dict[str, Any]], fid: str) -> list[dict[str, Any]]:
    prim = [r for r in rows if _num(r.get(fid)) is not None]
    if len(prim) < 50:
        return []
    xs = np.asarray([_num(r.get(fid)) for r in prim], dtype=float)
    try:
        edges = np.quantile(xs, [0.2, 0.4, 0.6, 0.8])
    except Exception:
        return []
    bins: list[list[dict[str, Any]]] = [[] for _ in range(5)]
    for r in prim:
        v = float(_num(r.get(fid)))
        if v <= edges[0] + 1e-15:
            bins[0].append(r)
        elif v <= edges[1] + 1e-15:
            bins[1].append(r)
        elif v <= edges[2] + 1e-15:
            bins[2].append(r)
        elif v <= edges[3] + 1e-15:
            bins[3].append(r)
        else:
            bins[4].append(r)
    out = []
    for i, got in enumerate(bins):
        marks = [_num(r.get("EXEC_MARKOUT_10M_BPS")) for r in got]
        marks = [x for x in marks if x is not None]
        up = sum(1 for r in got if r.get("UP_DOMINANT_10M"))
        pos = sum(1 for r in got if r.get("EXEC_POSITIVE_10M"))
        n = len(got)
        out.append(
            {
                "q": i + 1,
                "n": n,
                "mean_markout": float(np.mean(marks)) if marks else None,
                "median_markout": float(np.median(marks)) if marks else None,
                "UP_DOMINANT_rate": (up / n) if n else None,
                "EXEC_POSITIVE_rate": (pos / n) if n else None,
            }
        )
    return out


def univariate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prim = primary_rows(rows)
    pop = population(rows)
    out = []
    for fid in FEATURE_IDS:
        xm, ym = _pairs(prim, fid, "EXEC_MARKOUT_10M_BPS")
        xp, yp = _pairs(prim, fid, "PATH_EDGE_10M_BPS")
        sm = _spearman(xm, ym)
        sp = _spearman(xp, yp)
        qs = quintiles(prim, fid)
        q_sign = None
        if qs and qs[0].get("mean_markout") is not None and qs[-1].get("mean_markout") is not None:
            q_sign = _sign(float(qs[-1]["mean_markout"]) - float(qs[0]["mean_markout"]))
        block_m = {}
        block_p = {}
        valid = 0
        for bid, days in FOLD_BLOCKS.items():
            keep = set(days)
            sub = [r for r in prim if str(r.get("date")) in keep]
            a, b = _pairs(sub, fid, "EXEC_MARKOUT_10M_BPS")
            c, d = _pairs(sub, fid, "PATH_EDGE_10M_BPS")
            sm_b = _sign(_spearman(a, b))
            sp_b = _sign(_spearman(c, d))
            block_m[bid] = sm_b
            block_p[bid] = sp_b
            if sm_b in (1, -1) and sp_b in (1, -1):
                valid += 1
        primary = _sign(sm)
        agree_m = sum(1 for sg in block_m.values() if primary in (1, -1) and sg == primary)
        agree_p = sum(1 for sg in block_p.values() if _sign(sp) in (1, -1) and sg == _sign(sp))
        top = top_symbol(prim)
        hold_top = True
        if top:
            sub = [r for r in prim if _sym(r) != top]
            a, b = _pairs(sub, fid, "EXEC_MARKOUT_10M_BPS")
            hold_top = _sign(_spearman(a, b)) == primary
        day_hold = True
        for d in DEVELOPMENT_DAYS:
            sub = [r for r in prim if str(r.get("date")) != d]
            a, b = _pairs(sub, fid, "EXEC_MARKOUT_10M_BPS")
            sg = _sign(_spearman(a, b))
            if primary in (1, -1) and sg not in (primary, 0) and sg != primary:
                day_hold = False
                break
        hz_ok = True
        for key in ("EXEC_MARKOUT_5M_BPS", "EXEC_MARKOUT_15M_BPS"):
            a, b = _pairs(prim, fid, key)
            sg = _sign(_spearman(a, b))
            if primary in (1, -1) and sg in (1, -1) and sg != primary:
                hz_ok = False
        candidate = bool(
            primary in (1, -1)
            and _sign(sp) == primary
            and agree_m >= MIN_BLOCK_AGREE
            and agree_p >= MIN_BLOCK_AGREE
            and hold_top
            and day_hold
            and hz_ok
            and len(xm) >= 200
        )
        out.append(
            {
                "feature": fid,
                "spearman_markout": sm,
                "spearman_path_edge": sp,
                "quintile_q5_minus_q1_sign": q_sign,
                "block_markout_signs": block_m,
                "block_path_signs": block_p,
                "block_agree_markout": agree_m,
                "block_agree_path": agree_p,
                "top_symbol_excluded_holds": hold_top,
                "leave_one_day_holds": day_hold,
                "horizon_5_15_holds": hz_ok,
                "quintiles": qs,
                "mechanism_candidate": candidate,
                "pop_up_rate": pop.get("UP_DOMINANT_rate"),
            }
        )
    out.sort(key=lambda r: (not r["mechanism_candidate"], -abs(r["spearman_markout"] or 0.0)))
    return out


def _leaf_stats(rows: list[dict[str, Any]], pop: dict[str, Any]) -> dict[str, Any]:
    n = len(rows)
    marks = [_num(r.get("EXEC_MARKOUT_10M_BPS")) for r in rows]
    marks = [x for x in marks if x is not None]
    days = {str(r.get("date")) for r in rows}
    syms = {_sym(r) for r in rows}
    up = sum(1 for r in rows if r.get("UP_DOMINANT_10M"))
    pos = sum(1 for r in rows if r.get("EXEC_POSITIVE_10M"))
    block_mean = {}
    for bid, ds in FOLD_BLOCKS.items():
        sub = [r for r in rows if str(r.get("date")) in set(ds)]
        mm = [_num(r.get("EXEC_MARKOUT_10M_BPS")) for r in sub]
        mm = [x for x in mm if x is not None]
        block_mean[bid] = float(np.mean(mm)) if mm else None
    mean_m = float(np.mean(marks)) if marks else None
    med_m = float(np.median(marks)) if marks else None
    up_rate = (up / n) if n else None
    pos_rate = (pos / n) if n else None
    pos_blocks = sum(1 for v in block_mean.values() if v is not None and v >= 0)
    qualify = bool(
        n >= LEAF_MIN_N
        and len(days) >= LEAF_MIN_DAY_N
        and len(syms) >= LEAF_MIN_SYMBOL_N
        and mean_m is not None
        and mean_m > 0
        and med_m is not None
        and med_m > 0
        and up_rate is not None
        and up_rate > float(pop.get("UP_DOMINANT_rate") or 0)
        and pos_rate is not None
        and pos_rate > float(pop.get("EXEC_POSITIVE_rate") or 0)
        and pos_blocks >= MIN_BLOCK_AGREE
    )
    return {
        "n": n,
        "day_n": len(days),
        "symbol_n": len(syms),
        "mean_markout": mean_m,
        "median_markout": med_m,
        "UP_DOMINANT_rate": up_rate,
        "EXEC_POSITIVE_rate": pos_rate,
        "block_mean_markout": block_mean,
        "positive_block_n": pos_blocks,
        "qualifying": qualify,
    }


def fit_tree(rows: list[dict[str, Any]], *, features: list[str]) -> dict[str, Any]:
    feats = [f for f in features if f][:MAX_TREE_FEATURES]
    prim = primary_rows(rows)
    pop = population(rows)
    if not feats:
        return {"ok": False, "reason": "no_features", "leaves": []}
    xs = []
    ys = []
    keep = []
    for r in prim:
        vec = [_num(r.get(f)) for f in feats]
        if any(v is None for v in vec):
            continue
        xs.append(vec)
        ys.append(1 if r.get("UP_DOMINANT_10M") else 0)
        keep.append(r)
    if len(xs) < TREE_MIN_LEAF_N * 2:
        return {"ok": False, "reason": "n", "n": len(xs), "leaves": []}
    try:
        from sklearn.tree import DecisionTreeClassifier, export_text
    except Exception as exc:
        return {"ok": False, "reason": f"sklearn:{exc}", "leaves": []}
    clf = DecisionTreeClassifier(
        max_depth=TREE_MAX_DEPTH,
        max_leaf_nodes=TREE_MAX_LEAF_NODES,
        min_samples_leaf=TREE_MIN_LEAF_N,
        random_state=0,
    )
    X = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=int)
    clf.fit(X, y)
    used = [feats[i] for i in range(len(feats)) if float(clf.feature_importances_[i]) > 1e-12]
    rules = export_text(clf, feature_names=feats, decimals=4)
    leaf_id = clf.apply(X)
    leaves = []
    for lid in sorted(set(int(x) for x in leaf_id)):
        sub = [keep[i] for i, k in enumerate(leaf_id) if int(k) == int(lid)]
        st = _leaf_stats(sub, pop)
        st["leaf_id"] = int(lid)
        st["pred"] = int(np.round(np.mean([1 if r.get("UP_DOMINANT_10M") else 0 for r in sub])))
        leaves.append(st)
    return {
        "ok": True,
        "n": int(len(ys)),
        "features_used": used,
        "importances": {feats[i]: float(clf.feature_importances_[i]) for i in range(len(feats))},
        "rules": rules,
        "leaves": leaves,
        "qualifying_leaf_n": sum(1 for L in leaves if L.get("qualifying")),
        "max_depth": TREE_MAX_DEPTH,
        "max_leaf_nodes": TREE_MAX_LEAF_NODES,
        "min_leaf_n": TREE_MIN_LEAF_N,
    }


def lobo(rows: list[dict[str, Any]], *, candidates: list[str]) -> dict[str, Any]:
    prim = primary_rows(rows)
    folds = []
    reappear: dict[str, int] = defaultdict(int)
    confirm_pos = 0
    for hold in ("B1", "B2", "B3", "B4", "B5"):
        hold_days = set(FOLD_BLOCKS[hold])
        disc = [r for r in prim if str(r.get("date")) not in hold_days]
        conf = [r for r in prim if str(r.get("date")) in hold_days]
        feat_dir = {}
        for fid in candidates:
            a, b = _pairs(disc, fid, "EXEC_MARKOUT_10M_BPS")
            c, d = _pairs(conf, fid, "EXEC_MARKOUT_10M_BPS")
            ds = _sign(_spearman(a, b))
            cs = _sign(_spearman(c, d))
            same = ds in (1, -1) and ds == cs
            feat_dir[fid] = {"discovery_sign": ds, "confirm_sign": cs, "same": same}
            if same:
                reappear[fid] += 1
        tree = fit_tree(disc, features=candidates)
        q = [L for L in list(tree.get("leaves") or []) if L.get("qualifying")]
        conf_mark = None
        if q:
            marks = [_num(r.get("EXEC_MARKOUT_10M_BPS")) for r in conf]
            marks = [x for x in marks if x is not None]
            conf_mark = float(np.mean(marks)) if marks else None
            # confirm mean of discovery-qualifying applied via same tree on confirm
            try:
                from sklearn.tree import DecisionTreeClassifier

                feats = candidates[:MAX_TREE_FEATURES]
                xs_d, ys_d, _k = [], [], []
                for r in disc:
                    vec = [_num(r.get(f)) for f in feats]
                    if any(v is None for v in vec):
                        continue
                    xs_d.append(vec)
                    ys_d.append(1 if r.get("UP_DOMINANT_10M") else 0)
                clf = DecisionTreeClassifier(
                    max_depth=TREE_MAX_DEPTH,
                    max_leaf_nodes=TREE_MAX_LEAF_NODES,
                    min_samples_leaf=TREE_MIN_LEAF_N,
                    random_state=0,
                )
                clf.fit(np.asarray(xs_d, dtype=float), np.asarray(ys_d, dtype=int))
                xs_c = []
                rows_c = []
                for r in conf:
                    vec = [_num(r.get(f)) for f in feats]
                    if any(v is None for v in vec):
                        continue
                    xs_c.append(vec)
                    rows_c.append(r)
                if xs_c:
                    pred = clf.predict(np.asarray(xs_c, dtype=float))
                    sel = [rows_c[i] for i, p in enumerate(pred) if int(p) == 1]
                    mm = [_num(r.get("EXEC_MARKOUT_10M_BPS")) for r in sel]
                    mm = [x for x in mm if x is not None]
                    conf_mark = float(np.mean(mm)) if mm else None
            except Exception:
                pass
        if conf_mark is not None and conf_mark > 0:
            confirm_pos += 1
        folds.append(
            {
                "holdout_block": hold,
                "discovery_n": len(disc),
                "confirm_n": len(conf),
                "feature_direction": feat_dir,
                "tree_features": tree.get("features_used"),
                "qualifying_leaf_n": tree.get("qualifying_leaf_n"),
                "confirm_predicted_up_mean_markout": conf_mark,
            }
        )
    return {
        "TRUE_OOS": False,
        "folds": folds,
        "feature_same_direction_fold_n": dict(reappear),
        "confirm_positive_fold_n": confirm_pos,
        "stable_across_folds": [f for f, n in reappear.items() if n >= 4],
    }


def interactions(rows: list[dict[str, Any]], *, features: list[str]) -> list[dict[str, Any]]:
    prim = primary_rows(rows)
    feats = features[:6]
    out = []
    for i, f1 in enumerate(feats):
        for f2 in feats[i + 1 :]:
            vals1 = [_num(r.get(f1)) for r in prim]
            vals2 = [_num(r.get(f2)) for r in prim]
            xs = [v for v in vals1 if v is not None]
            ys = [v for v in vals2 if v is not None]
            if len(xs) < 200 or len(ys) < 200:
                continue
            m1 = float(np.median(xs))
            m2 = float(np.median(ys))
            cells = {(0, 0): [], (0, 1): [], (1, 0): [], (1, 1): []}
            for r in prim:
                a = _num(r.get(f1))
                b = _num(r.get(f2))
                if a is None or b is None:
                    continue
                cells[(1 if a > m1 else 0, 1 if b > m2 else 0)].append(r)
            packed = []
            best = None
            for key, sub in cells.items():
                marks = [_num(r.get("EXEC_MARKOUT_10M_BPS")) for r in sub]
                marks = [x for x in marks if x is not None]
                rec = {
                    "hi_f1": bool(key[0]),
                    "hi_f2": bool(key[1]),
                    "n": len(sub),
                    "mean_markout": float(np.mean(marks)) if marks else None,
                    "UP_DOMINANT_rate": (sum(1 for r in sub if r.get("UP_DOMINANT_10M")) / len(sub)) if sub else None,
                }
                packed.append(rec)
                if rec["n"] >= 100 and rec["mean_markout"] is not None:
                    if best is None or rec["mean_markout"] > best["mean_markout"]:
                        best = rec
            out.append({"f1": f1, "f2": f2, "median_f1": m1, "median_f2": m2, "cells": packed, "best_cell": best})
    out.sort(key=lambda r: -((r.get("best_cell") or {}).get("mean_markout") or -1e18))
    return out[:12]


def decide(*, uni: list[dict[str, Any]], tree: dict[str, Any], lobo_pack: dict[str, Any]) -> dict[str, Any]:
    stable = [u for u in uni if u.get("mechanism_candidate")]
    qn = int(tree.get("qualifying_leaf_n") or 0)
    confirm = int(lobo_pack.get("confirm_positive_fold_n") or 0)
    found = bool(stable) and qn >= 1 and confirm >= 3
    return {
        "CASE": "FOUND" if found else "NONE",
        "VERDICT": CASE_FOUND if found else CASE_NONE,
        "NEXT": NEXT_FOUND if found else NEXT_NONE,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "V5_RESCUE": False,
        "CANDIDATE_STRATEGY_N": 0 if not found else 0,
        "stable_feature_n": len(stable),
        "stable_features": [u["feature"] for u in stable],
        "qualifying_leaf_n": qn,
        "lobo_confirm_positive_fold_n": confirm,
        "PRECOMMIT_THIS_RUN": False,
    }
