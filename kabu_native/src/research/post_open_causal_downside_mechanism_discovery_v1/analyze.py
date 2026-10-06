"""Short X1 / W5 populations, univariate, ranking, tree, LOBO. No strategy freeze."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.post_open_causal_downside_mechanism_discovery_v1 import (
    CASE_A,
    CASE_B,
    CASE_C,
    DEVELOPMENT_DAYS,
    FEATURE_IDS,
    LEAF_MIN_DAY_N,
    LEAF_MIN_N,
    LEAF_MIN_SYMBOL_N,
    LOBO_MIN_POS,
    MAX_TREE_FEATURES,
    MIN_BLOCK_AGREE,
    NEXT_A,
    NEXT_B,
    NEXT_C,
    POS_BLOCK_MIN,
    RIDGE_ALPHA,
    TREE_MAX_DEPTH,
    TREE_MAX_LEAF_NODES,
    TREE_MIN_LEAF_N,
)
from research.post_open_causal_upside_mechanism_discovery_v1.analyze import (
    _num,
    _pairs,
    _sign,
    _spearman,
    executable_rows,
    feature_integrity,
    top_symbol,
)
from research.simple_tech_entry_family.portfolio import _sym
from research.systematic_state_transition_library_precommit_v1 import FOLD_BLOCKS


def primary_short(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in executable_rows(rows):
        if _num(r.get("SHORT_EXEC_MARKOUT_10M_BPS")) is None:
            continue
        if _num(r.get("SHORT_PATH_EDGE_10M_BPS")) is None:
            continue
        out.append(r)
    return out


def _pack(rows: list[dict[str, Any]], mark_key: str, pos_key: str, down_key: str) -> dict[str, Any]:
    marks = [x for x in (_num(r.get(mark_key)) for r in rows) if x is not None]
    pos = [1 if r.get(pos_key) else 0 for r in rows]
    down = [1 if r.get(down_key) else 0 for r in rows]
    return {
        "n": len(rows),
        "day_n": len({str(r.get("date")) for r in rows}),
        "symbol_n": len({_sym(r) for r in rows}),
        "mean": float(np.mean(marks)) if marks else None,
        "median": float(np.median(marks)) if marks else None,
        "positive_rate": (sum(pos) / len(pos)) if pos else None,
        "DOWN_DOMINANT_rate": (sum(down) / len(down)) if down else None,
    }


def short_population(rows: list[dict[str, Any]]) -> dict[str, Any]:
    prim = primary_short(rows)
    exe = executable_rows(rows)
    pack = _pack(prim, "SHORT_EXEC_MARKOUT_10M_BPS", "SHORT_EXEC_POSITIVE_10M", "DOWN_DOMINANT_10M")
    mids = [x for x in (_num(r.get("MID_RETURN_10M_BPS")) for r in prim) if x is not None]
    return {
        "anchor_n": len(rows),
        "executable_n": len(exe),
        "primary_n": pack["n"],
        "day_n": pack["day_n"],
        "symbol_n": pack["symbol_n"],
        "SHORT_EXEC_MARKOUT_10M_mean": pack["mean"],
        "SHORT_EXEC_MARKOUT_10M_median": pack["median"],
        "SHORT_EXEC_POSITIVE_rate": pack["positive_rate"],
        "DOWN_DOMINANT_rate": pack["DOWN_DOMINANT_rate"],
        "MID_RETURN_10M_mean": float(np.mean(mids)) if mids else None,
        "MID_RETURN_10M_median": float(np.median(mids)) if mids else None,
        "MID_finite_n": len(mids),
    }


def execution_populations(rows: list[dict[str, Any]]) -> dict[str, Any]:
    s0 = primary_short(rows)
    s1 = [r for r in s0 if r.get("SHORT_W5_FILLED")]
    s2 = [r for r in s1 if _num(r.get("SHORT_W5_MARKOUT_10M_BPS")) is not None]
    a0 = _pack(s0, "SHORT_EXEC_MARKOUT_10M_BPS", "SHORT_EXEC_POSITIVE_10M", "DOWN_DOMINANT_10M")
    a1 = _pack(s1, "SHORT_EXEC_MARKOUT_10M_BPS", "SHORT_EXEC_POSITIVE_10M", "DOWN_DOMINANT_10M")
    a2 = _pack(s2, "SHORT_W5_MARKOUT_10M_BPS", "SHORT_W5_EXEC_POSITIVE_10M", "SHORT_W5_DOWN_DOMINANT_10M")
    exe = executable_rows(rows)
    filled = [r for r in exe if r.get("SHORT_W5_FILLED")]
    return {
        "S0": {"label": "ALL_SHORT_X1_EXECUTABLE_PRIMARY", **a0},
        "S1": {"label": "SHORT_W5_FILLED_X1_BID_ENTRY", **a1},
        "S2": {"label": "SHORT_W5_FILLED_W5_ASK_FILL", **a2},
        "S2_minus_S1_mean": (None if a2["mean"] is None or a1["mean"] is None else float(a2["mean"]) - float(a1["mean"])),
        "S2_minus_S1_median": (
            None if a2["median"] is None or a1["median"] is None else float(a2["median"]) - float(a1["median"])
        ),
        "SHORT_W5_fill_n": len(filled),
        "SHORT_W5_fill_rate": (len(filled) / len(exe)) if exe else None,
        "SHORT_W5_fill_day_n": len({str(r.get("date")) for r in filled}),
        "SHORT_W5_fill_symbol_n": len({_sym(r) for r in filled}),
    }


def block_s2(rows: list[dict[str, Any]]) -> dict[str, Any]:
    s1 = [r for r in primary_short(rows) if r.get("SHORT_W5_FILLED")]
    s2 = [r for r in s1 if _num(r.get("SHORT_W5_MARKOUT_10M_BPS")) is not None]
    out = {}
    pos = 0
    for bid, days in FOLD_BLOCKS.items():
        keep = set(days)
        a = [x for x in (_num(r.get("SHORT_EXEC_MARKOUT_10M_BPS")) for r in s1 if str(r.get("date")) in keep) if x is not None]
        b = [x for x in (_num(r.get("SHORT_W5_MARKOUT_10M_BPS")) for r in s2 if str(r.get("date")) in keep) if x is not None]
        s2m = float(np.mean(b)) if b else None
        rec = {
            "S1_mean": float(np.mean(a)) if a else None,
            "S2_mean": s2m,
            "S2_minus_S1": (None if (not a or not b) else float(np.mean(b)) - float(np.mean(a))),
            "S1_n": len(a),
            "S2_n": len(b),
        }
        out[bid] = rec
        if s2m is not None and s2m > 0:
            pos += 1
    return {"blocks": out, "positive_S2_block_n": pos}


def quintiles_short(rows: list[dict[str, Any]], fid: str) -> list[dict[str, Any]]:
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
        marks = [x for x in (_num(r.get("SHORT_EXEC_MARKOUT_10M_BPS")) for r in got) if x is not None]
        n = len(got)
        out.append(
            {
                "q": i + 1,
                "n": n,
                "mean_markout": float(np.mean(marks)) if marks else None,
                "median_markout": float(np.median(marks)) if marks else None,
                "DOWN_DOMINANT_rate": (sum(1 for r in got if r.get("DOWN_DOMINANT_10M")) / n) if n else None,
                "SHORT_EXEC_POSITIVE_rate": (sum(1 for r in got if r.get("SHORT_EXEC_POSITIVE_10M")) / n) if n else None,
            }
        )
    return out


def univariate_short(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prim = primary_short(rows)
    out = []
    for fid in FEATURE_IDS:
        xm, ym = _pairs(prim, fid, "SHORT_EXEC_MARKOUT_10M_BPS")
        xp, yp = _pairs(prim, fid, "SHORT_PATH_EDGE_10M_BPS")
        sm = _spearman(xm, ym)
        sp = _spearman(xp, yp)
        qs = quintiles_short(prim, fid)
        block_m = {}
        block_p = {}
        for bid, days in FOLD_BLOCKS.items():
            sub = [r for r in prim if str(r.get("date")) in set(days)]
            a, b = _pairs(sub, fid, "SHORT_EXEC_MARKOUT_10M_BPS")
            c, d = _pairs(sub, fid, "SHORT_PATH_EDGE_10M_BPS")
            block_m[bid] = _sign(_spearman(a, b))
            block_p[bid] = _sign(_spearman(c, d))
        primary = _sign(sm)
        agree_m = sum(1 for sg in block_m.values() if primary in (1, -1) and sg == primary)
        agree_p = sum(1 for sg in block_p.values() if _sign(sp) in (1, -1) and sg == _sign(sp))
        top = top_symbol(prim)
        hold_top = True
        if top:
            sub = [r for r in prim if _sym(r) != top]
            a, b = _pairs(sub, fid, "SHORT_EXEC_MARKOUT_10M_BPS")
            hold_top = _sign(_spearman(a, b)) == primary
        day_hold = True
        for d in DEVELOPMENT_DAYS:
            sub = [r for r in prim if str(r.get("date")) != d]
            sg = _sign(_spearman(*_pairs(sub, fid, "SHORT_EXEC_MARKOUT_10M_BPS")))
            if primary in (1, -1) and sg not in (primary, 0) and sg != primary:
                day_hold = False
                break
        hz_ok = True
        for key in ("SHORT_EXEC_MARKOUT_5M_BPS", "SHORT_EXEC_MARKOUT_15M_BPS"):
            sg = _sign(_spearman(*_pairs(prim, fid, key)))
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
                "block_markout_signs": block_m,
                "block_path_signs": block_p,
                "block_agree_markout": agree_m,
                "block_agree_path": agree_p,
                "top_symbol_excluded_holds": hold_top,
                "leave_one_day_holds": day_hold,
                "horizon_5_15_holds": hz_ok,
                "quintiles": qs,
                "mechanism_candidate": candidate,
            }
        )
    out.sort(key=lambda r: (not r["mechanism_candidate"], -abs(r["spearman_markout"] or 0.0)))
    return out


def _topk_slice(ranked: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    return ranked[: min(k, len(ranked))]


def oof_ranking(rows: list[dict[str, Any]], *, features: list[str]) -> dict[str, Any]:
    prim = primary_short(rows)
    feats = [f for f in features if f][:MAX_TREE_FEATURES]
    if not feats or len(prim) < 200:
        return {"ok": False, "reason": "no_stable_features", "monotonic": False}
    days = sorted({str(r.get("date")) for r in prim})
    scored: list[dict[str, Any]] = []
    try:
        from sklearn.linear_model import Ridge
        from sklearn.preprocessing import StandardScaler
    except Exception as exc:
        return {"ok": False, "reason": f"sklearn:{exc}", "monotonic": False}
    for hold in days:
        train = [r for r in prim if str(r.get("date")) != hold]
        test = [r for r in prim if str(r.get("date")) == hold]
        xs, ys, keep = [], [], []
        for r in train:
            vec = [_num(r.get(f)) for f in feats]
            y = _num(r.get("SHORT_EXEC_MARKOUT_10M_BPS"))
            if any(v is None for v in vec) or y is None:
                continue
            xs.append(vec)
            ys.append(y)
            keep.append(r)
        if len(xs) < 40:
            continue
        scaler = StandardScaler()
        X = scaler.fit_transform(np.asarray(xs, dtype=float))
        model = Ridge(alpha=float(RIDGE_ALPHA))
        model.fit(X, np.asarray(ys, dtype=float))
        for r in test:
            vec = [_num(r.get(f)) for f in feats]
            if any(v is None for v in vec):
                continue
            rec = dict(r)
            rec["OOF_SHORT_SCORE"] = float(model.predict(scaler.transform([vec]))[0])
            scored.append(rec)
    if len(scored) < 50:
        return {"ok": False, "reason": "oof_n", "n": len(scored), "monotonic": False}
    a, b = _pairs(scored, "OOF_SHORT_SCORE", "SHORT_EXEC_MARKOUT_10M_BPS")
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in scored:
        buckets[f"{r.get('date')}|{r.get('anchor_hm')}"].append(r)
    all_m = [x for x in (_num(r.get("SHORT_EXEC_MARKOUT_10M_BPS")) for r in scored) if x is not None]
    slices = {"ALL": scored}
    for k, name in ((10, "SHORT_TOP10"), (5, "SHORT_TOP5"), (3, "SHORT_TOP3"), (1, "SHORT_TOP1"), (10, "BOTTOM10")):
        got = []
        for grp in buckets.values():
            ordered = sorted(grp, key=lambda z: float(z.get("OOF_SHORT_SCORE") or -1e18), reverse=True)
            if name == "BOTTOM10":
                got.extend(ordered[-min(k, len(ordered)) :])
            else:
                got.extend(_topk_slice(ordered, k))
        slices[name] = got
    packed = {}
    for name, sub in slices.items():
        marks = [x for x in (_num(r.get("SHORT_EXEC_MARKOUT_10M_BPS")) for r in sub) if x is not None]
        packed[name] = {
            "n": len(sub),
            "mean": float(np.mean(marks)) if marks else None,
            "median": float(np.median(marks)) if marks else None,
        }
    means = [packed[k]["mean"] for k in ("ALL", "SHORT_TOP10", "SHORT_TOP5", "SHORT_TOP3", "SHORT_TOP1")]
    monotonic = all(a is not None and b is not None and float(a) < float(b) for a, b in zip(means, means[1:]))
    pop_m = packed["ALL"]["mean"]
    ranking_edge = bool(
        packed["SHORT_TOP3"]["mean"] is not None
        and packed["SHORT_TOP1"]["mean"] is not None
        and pop_m is not None
        and float(packed["SHORT_TOP3"]["mean"]) > float(pop_m)
        and float(packed["SHORT_TOP1"]["mean"]) > float(pop_m)
    )
    return {
        "ok": True,
        "features": feats,
        "alpha": float(RIDGE_ALPHA),
        "n": len(scored),
        "spearman_oof": _spearman(a, b),
        "slices": packed,
        "monotonic": monotonic,
        "ranking_edge": ranking_edge,
        "pop_mean": pop_m,
    }


def _leaf_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    marks = [x for x in (_num(r.get("SHORT_EXEC_MARKOUT_10M_BPS")) for r in rows) if x is not None]
    days = {str(r.get("date")) for r in rows}
    syms = {_sym(r) for r in rows}
    block_mean = {}
    for bid, ds in FOLD_BLOCKS.items():
        mm = [x for x in (_num(r.get("SHORT_EXEC_MARKOUT_10M_BPS")) for r in rows if str(r.get("date")) in set(ds)) if x is not None]
        block_mean[bid] = float(np.mean(mm)) if mm else None
    mean_m = float(np.mean(marks)) if marks else None
    med_m = float(np.median(marks)) if marks else None
    pos_blocks = sum(1 for v in block_mean.values() if v is not None and v > 0)
    top = top_symbol(rows)
    hold_top = True
    if top:
        mm = [x for x in (_num(r.get("SHORT_EXEC_MARKOUT_10M_BPS")) for r in rows if _sym(r) != top) if x is not None]
        hold_top = (float(np.mean(mm)) >= 0) if mm else False
    day_ok = True
    for d in DEVELOPMENT_DAYS:
        mm = [x for x in (_num(r.get("SHORT_EXEC_MARKOUT_10M_BPS")) for r in rows if str(r.get("date")) != d) if x is not None]
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
        and pos_blocks >= POS_BLOCK_MIN
        and hold_top
        and day_ok
    )
    return {
        "n": n,
        "day_n": len(days),
        "symbol_n": len(syms),
        "mean_markout": mean_m,
        "median_markout": med_m,
        "SHORT_EXEC_POSITIVE_rate": (sum(1 for r in rows if r.get("SHORT_EXEC_POSITIVE_10M")) / n) if n else None,
        "DOWN_DOMINANT_rate": (sum(1 for r in rows if r.get("DOWN_DOMINANT_10M")) / n) if n else None,
        "block_mean_markout": block_mean,
        "positive_block_n": pos_blocks,
        "top_symbol_excluded_mean_ge0": hold_top,
        "leave_one_day_holds": day_ok,
        "qualifying": qualify,
    }


def fit_tree_short(rows: list[dict[str, Any]], *, features: list[str]) -> dict[str, Any]:
    feats = [f for f in features if f][:MAX_TREE_FEATURES]
    prim = primary_short(rows)
    if not feats:
        return {"ok": False, "reason": "no_features", "leaves": [], "qualifying_leaf_n": 0}
    xs, ys, keep = [], [], []
    for r in prim:
        vec = [_num(r.get(f)) for f in feats]
        if any(v is None for v in vec):
            continue
        xs.append(vec)
        ys.append(1 if r.get("DOWN_DOMINANT_10M") else 0)
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
        "features_used": [feats[i] for i in range(len(feats)) if float(clf.feature_importances_[i]) > 1e-12],
        "importances": {feats[i]: float(clf.feature_importances_[i]) for i in range(len(feats))},
        "rules": rules,
        "leaves": leaves,
        "qualifying_leaf_n": sum(1 for L in leaves if L.get("qualifying")),
        "max_depth": TREE_MAX_DEPTH,
        "max_leaf_nodes": TREE_MAX_LEAF_NODES,
        "min_leaf_n": TREE_MIN_LEAF_N,
    }


def lobo_short(rows: list[dict[str, Any]], *, features: list[str]) -> dict[str, Any]:
    prim = primary_short(rows)
    feats = [f for f in features if f][:MAX_TREE_FEATURES]
    folds = []
    confirm_pos = 0
    for hold in ("B1", "B2", "B3", "B4", "B5"):
        hold_days = set(FOLD_BLOCKS[hold])
        disc = [r for r in prim if str(r.get("date")) not in hold_days]
        conf = [r for r in prim if str(r.get("date")) in hold_days]
        tree = fit_tree_short(disc, features=feats)
        conf_mark = None
        try:
            from sklearn.tree import DecisionTreeClassifier

            xs_d, ys_d = [], []
            for r in disc:
                vec = [_num(r.get(f)) for f in feats]
                if any(v is None for v in vec):
                    continue
                xs_d.append(vec)
                ys_d.append(1 if r.get("DOWN_DOMINANT_10M") else 0)
            if feats and len(xs_d) >= TREE_MIN_LEAF_N * 2:
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
                    mm = [x for x in (_num(r.get("SHORT_EXEC_MARKOUT_10M_BPS")) for r in sel) if x is not None]
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
                "confirm_predicted_down_mean_markout": conf_mark,
            }
        )
    return {"TRUE_OOS": False, "folds": folds, "confirm_positive_fold_n": confirm_pos}


def decide(*, pop: dict[str, Any], exec_pops: dict[str, Any], tree: dict[str, Any], lobo_pack: dict[str, Any], rank: dict[str, Any]) -> dict[str, Any]:
    s0 = dict(exec_pops.get("S0") or {})
    qn = int(tree.get("qualifying_leaf_n") or 0)
    confirm = int(lobo_pack.get("confirm_positive_fold_n") or 0)
    ranking_edge = bool(rank.get("ranking_edge"))
    selectable = bool(qn >= 1 and confirm >= LOBO_MIN_POS)
    pop_pos = bool(s0.get("mean") is not None and float(s0["mean"]) > 0 and s0.get("median") is not None and float(s0["median"]) > 0)
    leaves = [L for L in list(tree.get("leaves") or []) if L.get("qualifying")]
    leaf_pos = bool(leaves and leaves[0].get("mean_markout") is not None and float(leaves[0]["mean_markout"]) > 0 and float(leaves[0].get("median_markout") or 0) > 0)
    case_a = bool(selectable and ranking_edge and leaf_pos)
    if case_a:
        case, verdict, nxt = "A", CASE_A, NEXT_A
    elif pop_pos:
        case, verdict, nxt = "B", CASE_B, NEXT_B
    else:
        case, verdict, nxt = "C", CASE_C, NEXT_C
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "qualifying_leaf_n": qn,
        "lobo_confirm_positive_fold_n": confirm,
        "ranking_edge": ranking_edge,
        "ranking_monotonic": bool(rank.get("monotonic")),
        "population_short_positive": pop_pos,
        "S0_mean": s0.get("mean"),
        "S0_median": s0.get("median"),
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "CANDIDATE_STRATEGY_N": 0,
        "PRECOMMIT_THIS_RUN": False,
        "ENTRY_THESIS_DERIVABLE": case_a,
        "TECHNICAL_EXIT_DERIVABLE": case_a,
        "RUNTIME_SHORT_FORBIDDEN": True,
    }
