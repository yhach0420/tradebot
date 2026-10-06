"""Extract 1-3 D1 leaves and test the same predicates on D2/D3/D4. No leaf mining grid."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.native_path_state_discrimination_v1 import (
    EVAL_BLOCKS,
    MAX_EXTRACT_RULES,
    MIN_RULE_LIFT,
    MIN_RULE_N,
    PRED_CUTOFF,
    TRAIN_BLOCK,
)
from research.native_path_state_discrimination_v1.features import PRIMARY_FEATURES
from research.native_path_state_discrimination_v1.model import contrast_rows


def _leaf_paths(clf: Any, names: tuple[str, ...]) -> list[dict[str, Any]]:
    tree = clf.tree_
    feats = tree.feature
    th = tree.threshold
    left = tree.children_left
    right = tree.children_right
    out: list[dict[str, Any]] = []

    def walk(node: int, path: list[tuple[str, str, float]]) -> None:
        if left[node] == right[node]:
            val = tree.value[node][0]
            n = int(tree.n_node_samples[node])
            p1 = float(val[1] / val.sum()) if val.sum() else 0.0
            out.append({"leaf": int(node), "n_train": n, "p_favorable": p1, "predicates": list(path)})
            return
        name = names[int(feats[node])]
        t = float(th[node])
        walk(int(left[node]), path + [(name, "<=", t)])
        walk(int(right[node]), path + [(name, ">", t)])

    walk(0, [])
    return out


def _apply_pred(st: dict[str, Any], pred: tuple[str, str, float], med: dict[str, float]) -> bool:
    name, op, thr = pred
    v = st.get(name)
    if v is None or v != v:
        v = med.get(name, 0.0)
    v = float(v)
    return v <= thr if op == "<=" else v > thr


def match_rule(row: dict[str, Any], predicates: list[tuple[str, str, float]], med: dict[str, float]) -> bool:
    st = row.get("state") or {}
    return all(_apply_pred(st, p, med) for p in predicates)


def block_lift(rows: list[dict[str, Any]], predicates: list, med: dict[str, float]) -> dict[str, Any]:
    xs = contrast_rows(rows)
    if not xs:
        return {"n": 0, "n_hit": 0, "base": None, "hit_rate": None, "lift": None}
    y = np.asarray([int(r["y"]) for r in xs], dtype=int)
    hit = np.asarray([match_rule(r, predicates, med) for r in xs], dtype=bool)
    base = float(np.mean(y))
    if not np.any(hit):
        return {"n": int(y.size), "n_hit": 0, "base": base, "hit_rate": None, "lift": None}
    rate = float(np.mean(y[hit]))
    return {"n": int(y.size), "n_hit": int(np.sum(hit)), "base": base, "hit_rate": rate, "lift": rate - base}


def exit_kind_for_predicates(predicates: list[tuple[str, str, float]]) -> str:
    names = [p[0] for p in predicates]
    if any(n in {"dist_vwap", "vwap_reclaim", "above_vwap"} for n in names):
        return "reclaim"
    if any(n.startswith("g_") or n in {"sector_rel_sess", "leading", "lagging", "sector_rank_pct", "sector_breadth_pos"} for n in names):
        return "sector"
    if any(n in {"pullback", "pullback_depth", "ret_5m", "ret_3m", "ret_1m"} for n in names):
        return "impulse"
    if any(n in {"vol_rel20", "va_rel20", "rng_rel20", "vol_expand", "va_expand", "range_expand"} for n in names):
        return "impulse"
    return "impulse"


def extract(preq: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    trees = dict(preq.get("trees") or {})
    d1 = trees.get(TRAIN_BLOCK) or {}
    fit = dict(d1.get("fit") or {})
    med = dict(d1.get("med") or {})
    if not fit.get("ok"):
        return {"ok": False, "rules": [], "reason": "d1_tree_missing"}
    clf = fit["model"]
    leaves = _leaf_paths(clf, PRIMARY_FEATURES)
    d1_rows = [r for r in contrast_rows(rows) if str(r.get("block")) == TRAIN_BLOCK]
    base_d1 = float(np.mean([int(r["y"]) for r in d1_rows])) if d1_rows else 0.0
    scored = []
    for leaf in leaves:
        if int(leaf["n_train"]) < MIN_RULE_N:
            continue
        lift = float(leaf["p_favorable"]) - base_d1
        if lift < MIN_RULE_LIFT:
            continue
        preds = [tuple(p) for p in leaf["predicates"]]
        by_block = {}
        for b in EVAL_BLOCKS:
            by_block[b] = block_lift([r for r in rows if str(r.get("block")) == b], preds, med)
        d23 = block_lift([r for r in rows if str(r.get("block")) in {"D2", "D3"}], preds, med)
        d2l = by_block.get("D2") or {}
        d3l = by_block.get("D3") or {}
        stable = bool(
            d2l.get("lift") is not None
            and d3l.get("lift") is not None
            and float(d2l["lift"]) > 0
            and float(d3l["lift"]) > 0
            and int(d2l.get("n_hit") or 0) >= 40
            and int(d3l.get("n_hit") or 0) >= 40
        )
        scored.append(
            {
                "rule_id": f"R{leaf['leaf']}",
                "leaf": leaf["leaf"],
                "predicates": [{"feature": a, "op": b, "threshold": c} for a, b, c in preds],
                "predicates_t": preds,
                "n_train": leaf["n_train"],
                "p_favorable_train": leaf["p_favorable"],
                "lift_train": lift,
                "blocks": by_block,
                "D2_D3": d23,
                "stable_d2_d3": stable,
                "exit_kind": exit_kind_for_predicates(preds),
                "d4_cannot_certify": True,
            }
        )
    scored.sort(key=lambda r: (-int(r["stable_d2_d3"]), -float(r["lift_train"])))
    kept = scored[:MAX_EXTRACT_RULES]
    return {
        "ok": True,
        "d1_base_favorable": base_d1,
        "leaf_n": len(leaves),
        "candidate_leaf_n": len(scored),
        "rules": [{k: v for k, v in r.items() if k != "predicates_t"} for r in kept],
        "stable_rules": [r["rule_id"] for r in kept if r["stable_d2_d3"]],
        "max_rules": MAX_EXTRACT_RULES,
        "cutoff_not_searched": PRED_CUTOFF,
        "same_predicates_on_later_blocks": True,
    }
