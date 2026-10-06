"""P0/P1/P2, selection, blocks, W5 tree. No CAP. No strategy freeze."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.post_open_causal_upside_mechanism_discovery_v1.analyze import (
    _num,
    _pairs,
    _sign,
    _spearman,
    primary_rows,
    quintiles,
    top_symbol,
)
from research.post_open_execution_tax_decomposition_v1 import (
    CASE_A,
    CASE_B,
    CASE_C,
    CONDITIONAL_FEATURES,
    DEVELOPMENT_DAYS,
    LEAF_MIN_DAY_N,
    LEAF_MIN_N,
    LEAF_MIN_SYMBOL_N,
    LOBO_MIN_POS,
    MIN_FILL_DAY_N,
    MIN_FILL_SYMBOL_N,
    NEXT_A,
    NEXT_B,
    NEXT_C,
    POS_P2_BLOCK_MIN,
    TREE_MAX_DEPTH,
    TREE_MAX_LEAF_NODES,
    TREE_MIN_LEAF_N,
)
from research.simple_tech_entry_family.portfolio import _sym
from research.systematic_state_transition_library_precommit_v1 import FOLD_BLOCKS


def p0_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return primary_rows(rows)


def p1_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in p0_rows(rows) if r.get("W5_FILLED")]


def p2_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in p1_rows(rows) if _num(r.get("W5_MARKOUT_FROM_FILL_10M_BPS")) is not None]


def _pack(rows: list[dict[str, Any]], mark_key: str, pos_key: str, up_key: str) -> dict[str, Any]:
    marks = [x for x in (_num(r.get(mark_key)) for r in rows) if x is not None]
    pos = [1 if r.get(pos_key) else 0 for r in rows]
    up = [1 if r.get(up_key) else 0 for r in rows]
    return {
        "n": len(rows),
        "day_n": len({str(r.get("date")) for r in rows}),
        "symbol_n": len({_sym(r) for r in rows}),
        "mean": float(np.mean(marks)) if marks else None,
        "median": float(np.median(marks)) if marks else None,
        "positive_rate": (sum(pos) / len(pos)) if pos else None,
        "UP_DOMINANT_rate": (sum(up) / len(up)) if up else None,
    }


def populations(rows: list[dict[str, Any]]) -> dict[str, Any]:
    p0 = p0_rows(rows)
    p1 = p1_rows(rows)
    p2 = p2_rows(rows)
    a0 = _pack(p0, "EXEC_MARKOUT_10M_BPS", "EXEC_POSITIVE_10M", "UP_DOMINANT_10M")
    a1 = _pack(p1, "EXEC_MARKOUT_10M_BPS", "EXEC_POSITIVE_10M", "UP_DOMINANT_10M")
    a2 = _pack(p2, "W5_MARKOUT_FROM_FILL_10M_BPS", "W5_EXEC_POSITIVE_10M", "W5_UP_DOMINANT_10M")
    exe = [r for r in rows if r.get("executable")]
    filled_exe = [r for r in exe if r.get("W5_FILLED")]
    clocks: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    days_fill: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in exe:
        clocks[str(r.get("anchor_hm") or "")][1] += 1
        days_fill[str(r.get("date") or "")][1] += 1
        if r.get("W5_FILLED"):
            clocks[str(r.get("anchor_hm") or "")][0] += 1
            days_fill[str(r.get("date") or "")][0] += 1
    return {
        "P0": {"label": "ALL_X1_EXECUTABLE_PRIMARY", **a0},
        "P1": {"label": "W5_FILLED_X1_ENTRY_PRICE", **a1},
        "P2": {"label": "W5_FILLED_W5_FILL_PRICE", **a2},
        "P2_minus_P1_mean": (None if a2["mean"] is None or a1["mean"] is None else float(a2["mean"]) - float(a1["mean"])),
        "P2_minus_P1_median": (
            None if a2["median"] is None or a1["median"] is None else float(a2["median"]) - float(a1["median"])
        ),
        "W5_fill_n": len(filled_exe),
        "W5_fill_rate": (len(filled_exe) / len(exe)) if exe else None,
        "W5_fill_day_n": len({str(r.get("date")) for r in filled_exe}),
        "W5_fill_symbol_n": len({_sym(r) for r in filled_exe}),
        "support_ok": len({str(r.get("date")) for r in filled_exe}) >= MIN_FILL_DAY_N
        and len({_sym(r) for r in filled_exe}) >= MIN_FILL_SYMBOL_N,
        "per_day_fill_rate": {d: (a / b if b else None) for d, (a, b) in sorted(days_fill.items())},
        "per_clock_fill_rate": {c: (a / b if b else None) for c, (a, b) in sorted(clocks.items())},
    }


def selection_effect(rows: list[dict[str, Any]]) -> dict[str, Any]:
    p0 = p0_rows(rows)
    filled = [r for r in p0 if r.get("W5_FILLED")]
    unfilled = [r for r in p0 if not r.get("W5_FILLED")]
    a_f = _pack(filled, "EXEC_MARKOUT_10M_BPS", "EXEC_POSITIVE_10M", "UP_DOMINANT_10M")
    a_u = _pack(unfilled, "EXEC_MARKOUT_10M_BPS", "EXEC_POSITIVE_10M", "UP_DOMINANT_10M")
    delta = None if a_f["mean"] is None or a_u["mean"] is None else float(a_f["mean"]) - float(a_u["mean"])
    if delta is None:
        label = "UNKNOWN"
    elif delta < -1.0:
        label = "PASSIVE_ADVERSE_SELECTION"
    else:
        label = "PASSIVE_FILL_SELECTION_NOT_ADVERSE"
    return {
        "W5_FILLED_X1": a_f,
        "W5_NOT_FILLED_X1": a_u,
        "filled_minus_nonfilled_x1_mean": delta,
        "label": label,
    }


def common_endpoint(rows: list[dict[str, Any]]) -> dict[str, Any]:
    recs = [r for r in p1_rows(rows) if _num(r.get("PRICE_IMPROVEMENT_BPS")) is not None]
    imp = [x for x in (_num(r.get("PRICE_IMPROVEMENT_BPS")) for r in recs) if x is not None]
    w5s = [x for x in (_num(r.get("W5_SAME_ENDPOINT_MARKOUT_BPS")) for r in recs) if x is not None]
    x1s = [x for x in (_num(r.get("X1_SAME_ENDPOINT_MARKOUT_BPS")) for r in recs) if x is not None]
    return {
        "n": len(recs),
        "X1_same_mean": float(np.mean(x1s)) if x1s else None,
        "W5_same_mean": float(np.mean(w5s)) if w5s else None,
        "PRICE_IMPROVEMENT_mean": float(np.mean(imp)) if imp else None,
        "PRICE_IMPROVEMENT_median": float(np.median(imp)) if imp else None,
    }


def block_comparison(rows: list[dict[str, Any]]) -> dict[str, Any]:
    p1 = p1_rows(rows)
    p2 = p2_rows(rows)
    out = {}
    pos = 0
    for bid, days in FOLD_BLOCKS.items():
        keep = set(days)
        a1 = _pack([r for r in p1 if str(r.get("date")) in keep], "EXEC_MARKOUT_10M_BPS", "EXEC_POSITIVE_10M", "UP_DOMINANT_10M")
        a2 = _pack(
            [r for r in p2 if str(r.get("date")) in keep],
            "W5_MARKOUT_FROM_FILL_10M_BPS",
            "W5_EXEC_POSITIVE_10M",
            "W5_UP_DOMINANT_10M",
        )
        delta = None if a1["mean"] is None or a2["mean"] is None else float(a2["mean"]) - float(a1["mean"])
        if a2["mean"] is not None and float(a2["mean"]) > 0:
            pos += 1
        out[bid] = {"P1_mean": a1["mean"], "P2_mean": a2["mean"], "P2_minus_P1": delta, "P1_n": a1["n"], "P2_n": a2["n"]}
    return {"blocks": out, "positive_P2_block_n": pos}


def univariate_w5(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prim = p2_rows(rows)
    out = []
    for fid in CONDITIONAL_FEATURES:
        xm, ym = _pairs(prim, fid, "W5_MARKOUT_FROM_FILL_10M_BPS")
        sm = _spearman(xm, ym)
        mapped = [
            {
                **r,
                "EXEC_MARKOUT_10M_BPS": r.get("W5_MARKOUT_FROM_FILL_10M_BPS"),
                "UP_DOMINANT_10M": r.get("W5_UP_DOMINANT_10M"),
                "EXEC_POSITIVE_10M": r.get("W5_EXEC_POSITIVE_10M"),
            }
            for r in prim
        ]
        qs = quintiles(mapped, fid)
        block = {}
        for bid, days in FOLD_BLOCKS.items():
            sub = [r for r in prim if str(r.get("date")) in set(days)]
            a, b = _pairs(sub, fid, "W5_MARKOUT_FROM_FILL_10M_BPS")
            block[bid] = _sign(_spearman(a, b))
        primary = _sign(sm)
        agree = sum(1 for sg in block.values() if primary in (1, -1) and sg == primary)
        out.append(
            {
                "feature": fid,
                "spearman_w5_markout": sm,
                "block_signs": block,
                "block_agree": agree,
                "quintiles": qs,
                "stable": bool(primary in (1, -1) and agree >= 4 and len(xm) >= 200),
            }
        )
    out.sort(key=lambda r: (not r["stable"], -abs(r["spearman_w5_markout"] or 0.0)))
    return out


def interactions_w5(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prim = p2_rows(rows)
    feats = list(CONDITIONAL_FEATURES)
    out = []
    for i, f1 in enumerate(feats):
        for f2 in feats[i + 1 :]:
            xs = [v for v in (_num(r.get(f1)) for r in prim) if v is not None]
            ys = [v for v in (_num(r.get(f2)) for r in prim) if v is not None]
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
                marks = [x for x in (_num(r.get("W5_MARKOUT_FROM_FILL_10M_BPS")) for r in sub) if x is not None]
                rec = {
                    "hi_f1": bool(key[0]),
                    "hi_f2": bool(key[1]),
                    "n": len(sub),
                    "mean_markout": float(np.mean(marks)) if marks else None,
                    "median_markout": float(np.median(marks)) if marks else None,
                }
                packed.append(rec)
                if rec["n"] >= 100 and rec["mean_markout"] is not None:
                    if best is None or rec["mean_markout"] > best["mean_markout"]:
                        best = rec
            out.append({"f1": f1, "f2": f2, "median_f1": m1, "median_f2": m2, "cells": packed, "best_cell": best})
    out.sort(key=lambda r: -((r.get("best_cell") or {}).get("mean_markout") or -1e18))
    return out[:10]


def _leaf_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    marks = [x for x in (_num(r.get("W5_MARKOUT_FROM_FILL_10M_BPS")) for r in rows) if x is not None]
    days = {str(r.get("date")) for r in rows}
    syms = {_sym(r) for r in rows}
    block_mean = {}
    for bid, ds in FOLD_BLOCKS.items():
        mm = [x for x in (_num(r.get("W5_MARKOUT_FROM_FILL_10M_BPS")) for r in rows if str(r.get("date")) in set(ds)) if x is not None]
        block_mean[bid] = float(np.mean(mm)) if mm else None
    mean_m = float(np.mean(marks)) if marks else None
    med_m = float(np.median(marks)) if marks else None
    pos_blocks = sum(1 for v in block_mean.values() if v is not None and v > 0)
    top = top_symbol(rows)
    hold_top = True
    if top:
        mm = [x for x in (_num(r.get("W5_MARKOUT_FROM_FILL_10M_BPS")) for r in rows if _sym(r) != top) if x is not None]
        hold_top = (float(np.mean(mm)) >= 0) if mm else False
    day_ok = True
    for d in DEVELOPMENT_DAYS:
        mm = [x for x in (_num(r.get("W5_MARKOUT_FROM_FILL_10M_BPS")) for r in rows if str(r.get("date")) != d) if x is not None]
        if mean_m is not None and mean_m > 0 and mm and float(np.mean(mm)) < 0:
            day_ok = False
            break
    qualify = bool(
        n >= LEAF_MIN_N
        and len(days) >= LEAF_MIN_DAY_N
        and len(syms) >= LEAF_MIN_SYMBOL_N
        and mean_m is not None
        and mean_m > 0
        and med_m is not None
        and med_m > 0
        and pos_blocks >= POS_P2_BLOCK_MIN
        and hold_top
        and day_ok
    )
    return {
        "n": n,
        "day_n": len(days),
        "symbol_n": len(syms),
        "mean_markout": mean_m,
        "median_markout": med_m,
        "block_mean_markout": block_mean,
        "positive_block_n": pos_blocks,
        "top_symbol_excluded_mean_ge0": hold_top,
        "leave_one_day_holds": day_ok,
        "qualifying": qualify,
    }


def fit_tree_w5(rows: list[dict[str, Any]]) -> dict[str, Any]:
    feats = list(CONDITIONAL_FEATURES)
    prim = p2_rows(rows)
    xs, ys, keep = [], [], []
    for r in prim:
        vec = [_num(r.get(f)) for f in feats]
        if any(v is None for v in vec):
            continue
        xs.append(vec)
        ys.append(1 if r.get("W5_UP_DOMINANT_10M") else 0)
        keep.append(r)
    if len(xs) < TREE_MIN_LEAF_N * 2:
        return {"ok": False, "reason": "n", "n": len(xs), "leaves": [], "qualifying_leaf_n": 0}
    try:
        from sklearn.tree import DecisionTreeClassifier, export_text
    except Exception as exc:
        return {"ok": False, "reason": f"sklearn:{exc}", "leaves": [], "qualifying_leaf_n": 0}
    clf = DecisionTreeClassifier(
        max_depth=TREE_MAX_DEPTH,
        max_leaf_nodes=TREE_MAX_LEAF_NODES,
        min_samples_leaf=TREE_MIN_LEAF_N,
        random_state=0,
    )
    X = np.asarray(xs, dtype=float)
    clf.fit(X, np.asarray(ys, dtype=int))
    rules = export_text(clf, feature_names=feats, decimals=4)
    leaf_id = clf.apply(X)
    leaves = []
    for lid in sorted(set(int(x) for x in leaf_id)):
        sub = [keep[i] for i, k in enumerate(leaf_id) if int(k) == int(lid)]
        st = _leaf_stats(sub)
        st["leaf_id"] = int(lid)
        leaves.append(st)
    return {
        "ok": True,
        "n": int(len(ys)),
        "rules": rules,
        "leaves": leaves,
        "qualifying_leaf_n": sum(1 for L in leaves if L.get("qualifying")),
        "importances": {feats[i]: float(clf.feature_importances_[i]) for i in range(len(feats))},
    }


def lobo_w5(rows: list[dict[str, Any]]) -> dict[str, Any]:
    prim = p2_rows(rows)
    feats = list(CONDITIONAL_FEATURES)
    folds = []
    confirm_pos = 0
    for hold in ("B1", "B2", "B3", "B4", "B5"):
        hold_days = set(FOLD_BLOCKS[hold])
        disc = [r for r in prim if str(r.get("date")) not in hold_days]
        conf = [r for r in prim if str(r.get("date")) in hold_days]
        tree = fit_tree_w5(disc)
        conf_mark = None
        try:
            from sklearn.tree import DecisionTreeClassifier

            xs_d, ys_d = [], []
            for r in disc:
                vec = [_num(r.get(f)) for f in feats]
                if any(v is None for v in vec):
                    continue
                xs_d.append(vec)
                ys_d.append(1 if r.get("W5_UP_DOMINANT_10M") else 0)
            if len(xs_d) >= TREE_MIN_LEAF_N * 2:
                clf = DecisionTreeClassifier(
                    max_depth=TREE_MAX_DEPTH,
                    max_leaf_nodes=TREE_MAX_LEAF_NODES,
                    min_samples_leaf=TREE_MIN_LEAF_N,
                    random_state=0,
                )
                clf.fit(np.asarray(xs_d, dtype=float), np.asarray(ys_d, dtype=int))
                xs_c, rows_c = [], []
                for r in conf:
                    vec = [_num(r.get(f)) for f in feats]
                    if any(v is None for v in vec):
                        continue
                    xs_c.append(vec)
                    rows_c.append(r)
                if xs_c:
                    pred = clf.predict(np.asarray(xs_c, dtype=float))
                    sel = [rows_c[i] for i, p in enumerate(pred) if int(p) == 1]
                    mm = [x for x in (_num(r.get("W5_MARKOUT_FROM_FILL_10M_BPS")) for r in sel) if x is not None]
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
                "qualifying_leaf_n": tree.get("qualifying_leaf_n"),
                "confirm_predicted_up_mean_markout": conf_mark,
            }
        )
    return {"TRUE_OOS": False, "folds": folds, "confirm_positive_fold_n": confirm_pos}


def decide(*, pops: dict[str, Any], blocks: dict[str, Any], sel: dict[str, Any], tree: dict[str, Any], lobo_pack: dict[str, Any]) -> dict[str, Any]:
    p2 = dict(pops.get("P2") or {})
    p1 = dict(pops.get("P1") or {})
    improve = pops.get("P2_minus_P1_mean")
    pos_blocks = int(blocks.get("positive_P2_block_n") or 0)
    exec_flip = bool(
        p2.get("mean") is not None
        and float(p2["mean"]) > 0
        and p2.get("median") is not None
        and float(p2["median"]) > 0
        and pos_blocks >= POS_P2_BLOCK_MIN
    )
    qn = int(tree.get("qualifying_leaf_n") or 0)
    confirm = int(lobo_pack.get("confirm_positive_fold_n") or 0)
    recovered = bool(qn >= 1 and confirm >= LOBO_MIN_POS)
    if exec_flip and qn >= 1 and confirm >= LOBO_MIN_POS:
        recovered = True
    material = improve is not None and float(improve) > 1.0
    adverse = str(sel.get("label") or "") == "PASSIVE_ADVERSE_SELECTION"
    if recovered:
        case, verdict, nxt = "A", CASE_A, NEXT_A
    elif exec_flip:
        case, verdict, nxt = "A", CASE_A, NEXT_A
    elif material:
        case, verdict, nxt = "B", CASE_B, NEXT_B
    else:
        case, verdict, nxt = "C", CASE_C, NEXT_C
        if adverse and not material:
            case, verdict, nxt = "C", CASE_C, NEXT_C
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "EXECUTION_TAX_CAN_FLIP_POPULATION_EDGE": exec_flip,
        "qualifying_leaf_n": qn,
        "lobo_confirm_positive_fold_n": confirm,
        "P2_mean": p2.get("mean"),
        "P2_median": p2.get("median"),
        "P1_mean": p1.get("mean"),
        "improvement_mean_bps": improve,
        "positive_P2_block_n": pos_blocks,
        "selection": sel.get("label"),
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "CANDIDATE_STRATEGY_N": 0,
        "PRECOMMIT_THIS_RUN": False,
        "ENTRY_THESIS_DERIVABLE": recovered or exec_flip,
        "TECHNICAL_EXIT_DERIVABLE": recovered,
    }
