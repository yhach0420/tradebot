"""Univariate / block / monotonicity / LOBO / shallow tree. PnL is not the target."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.full_causal_mechanism_discovery_v1.analyze import occupancy_rows, replay
from research.prior_close_recapture_sustained_mechanism_v1 import (
    CASE_FOUND,
    CASE_NONE,
    DEVELOPMENT_DAYS,
    FEATURE_IDS,
    LABEL_FAILED,
    LABEL_SUSTAINED,
    LABEL_UNRESOLVED,
    MAX_TREE_FEATURES,
    MIN_BLOCK_AGREE,
    MIN_DAY_SUPPORT,
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


def _arr(rows: list[dict[str, Any]], key: str) -> np.ndarray:
    xs = [_num(r.get(key)) for r in rows]
    return np.asarray([x for x in xs if x is not None], dtype=float)


def _quant(a: np.ndarray) -> dict[str, Any]:
    if a.size == 0:
        return {"n": 0, "median": None, "q25": None, "q75": None}
    return {
        "n": int(a.size),
        "median": float(np.median(a)),
        "q25": float(np.percentile(a, 25)),
        "q75": float(np.percentile(a, 75)),
    }


def _sign(x: Optional[float]) -> Optional[int]:
    if x is None or x != x:
        return None
    if abs(float(x)) < 1e-12:
        return 0
    return 1 if float(x) > 0 else -1


def alpha_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if not r.get("WOULD_FILL"):
            continue
        lab = str(r.get("alpha_label") or "")
        if lab in (LABEL_SUSTAINED, LABEL_FAILED):
            out.append(r)
    return out


def reproduce_portfolio(rows: list[dict[str, Any]]) -> dict[str, Any]:
    occ = replay(rows)
    trades = list(occ.get("trades") or [])
    tech = [t for t in trades if str(t.get("exit_reason") or "") not in ("SESSION_CLOSE", "EXIT_MISS", "")]
    sess = [t for t in trades if str(t.get("exit_reason") or "") == "SESSION_CLOSE"]

    def _pnl(xs: list[dict[str, Any]]) -> float:
        return float(sum(float(t.get("pnl_yen_100") or 0.0) for t in xs))

    win = sum(1 for t in sess if float(t.get("pnl_yen_100") or 0.0) > 1e-12)
    loss = sum(1 for t in sess if float(t.get("pnl_yen_100") or 0.0) < -1e-12)
    flat = len(sess) - win - loss
    return {
        "occupancy_trade_n": int(occ.get("fill_n") or 0),
        "technical_exit_n": len(tech),
        "technical_exit_pnl": _pnl(tech),
        "session_close_n": len(sess),
        "session_close_pnl": _pnl(sess),
        "session_close_win_loss_flat": f"{win}/{loss}/{flat}",
        "cap_blocked": occ.get("cap_blocked"),
        "note": "CAP used only for V5 portfolio reproduction, not for alpha labels.",
    }


def label_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    alpha = alpha_rows(rows)
    sus = [r for r in alpha if r.get("alpha_label") == LABEL_SUSTAINED]
    fail = [r for r in alpha if r.get("alpha_label") == LABEL_FAILED]
    unresolved = [r for r in rows if r.get("WOULD_FILL") and r.get("execution_unresolved")]
    x1 = [r for r in rows if r.get("WOULD_FILL")]

    def _cov(xs: list[dict[str, Any]]) -> dict[str, Any]:
        days = {str(r.get("date")) for r in xs}
        return {"n": len(xs), "day_n": len(days), "symbol_n": len({_sym(r) for r in xs}), "days": sorted(days)}

    return {
        "signal_n": len(rows),
        "x1_fillable_n": len(x1),
        "SUSTAINED_RECAPTURE": _cov(sus),
        "FAILED_RECAPTURE": _cov(fail),
        "EXECUTION_UNRESOLVED": {
            "n": len(unresolved),
            "day_n": len({str(r.get("date")) for r in unresolved}),
            "symbol_n": len({_sym(r) for r in unresolved}),
            "mixed_into_alpha": False,
        },
        "alpha_n": len(alpha),
    }


def feature_integrity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alpha = alpha_rows(rows)
    n = len(alpha)
    out = []
    for fid in FEATURE_IDS:
        finite = 0
        days: set[str] = set()
        syms: set[str] = set()
        for r in alpha:
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
                "missing_rate": 0.0 if n <= 0 else 1.0 - finite / n,
                "finite_rate": 0.0 if n <= 0 else finite / n,
                "day_coverage": len(days),
                "symbol_coverage": len(syms),
                "current_price_time_inference": False,
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


def univariate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alpha = alpha_rows(rows)
    sus = [r for r in alpha if r.get("alpha_label") == LABEL_SUSTAINED]
    fail = [r for r in alpha if r.get("alpha_label") == LABEL_FAILED]
    out = []
    for fid in FEATURE_IDS:
        a_s = _arr(sus, fid)
        a_f = _arr(fail, fid)
        qs = _quant(a_s)
        qf = _quant(a_f)
        diff = None if (qs["median"] is None or qf["median"] is None) else float(qs["median"] - qf["median"])
        day_n = sum(
            1
            for d in DEVELOPMENT_DAYS
            if any(_num(r.get(fid)) is not None and str(r.get("date")) == d for r in alpha)
        )
        sym_n = len({_sym(r) for r in alpha if _num(r.get(fid)) is not None})
        block_signs = {}
        valid_blocks = 0
        for bid, days in FOLD_BLOCKS.items():
            keep = set(days)
            s_b = _arr([r for r in sus if str(r.get("date")) in keep], fid)
            f_b = _arr([r for r in fail if str(r.get("date")) in keep], fid)
            if s_b.size < 5 or f_b.size < 5:
                block_signs[bid] = None
                continue
            valid_blocks += 1
            block_signs[bid] = _sign(float(np.median(s_b) - np.median(f_b)))
        primary = _sign(diff)
        agree = sum(1 for sg in block_signs.values() if primary in (1, -1) and sg == primary)
        top = top_symbol(alpha)
        hold_top = True
        if top:
            s2 = _arr([r for r in sus if _sym(r) != top], fid)
            f2 = _arr([r for r in fail if _sym(r) != top], fid)
            if s2.size and f2.size:
                hold_top = _sign(float(np.median(s2) - np.median(f2))) == primary
            else:
                hold_top = False
        day_hold = True
        for d in DEVELOPMENT_DAYS:
            s2 = _arr([r for r in sus if str(r.get("date")) != d], fid)
            f2 = _arr([r for r in fail if str(r.get("date")) != d], fid)
            if s2.size < 5 or f2.size < 5:
                continue
            sg = _sign(float(np.median(s2) - np.median(f2)))
            if primary in (1, -1) and sg not in (primary, 0) and sg != primary:
                day_hold = False
                break
        candidate = bool(
            day_n >= MIN_DAY_SUPPORT
            and sym_n >= 2
            and primary in (1, -1)
            and agree >= MIN_BLOCK_AGREE
            and hold_top
            and day_hold
            and qs["n"] >= 20
            and qf["n"] >= 20
        )
        out.append(
            {
                "feature": fid,
                "sustained": qs,
                "failed": qf,
                "median_diff_S_minus_F": diff,
                "direction": "higher_in_sustained"
                if primary == 1
                else ("lower_in_sustained" if primary == -1 else "none"),
                "block_signs": block_signs,
                "block_agree_n": agree,
                "valid_block_n": valid_blocks,
                "day_support": day_n,
                "symbol_n": sym_n,
                "top_symbol_excluded_holds": hold_top,
                "leave_one_day_holds": day_hold,
                "mechanism_candidate": candidate,
            }
        )
    out.sort(key=lambda r: (not r["mechanism_candidate"], -abs(r["median_diff_S_minus_F"] or 0.0)))
    return out


def monotonicity(rows: list[dict[str, Any]], *, features: list[str] | None = None) -> list[dict[str, Any]]:
    alpha = alpha_rows(rows)
    out = []
    for fid in list(features or FEATURE_IDS):
        vals = [(_num(r.get(fid)), 1 if r.get("alpha_label") == LABEL_SUSTAINED else 0) for r in alpha]
        vals = [(v, y) for v, y in vals if v is not None]
        if len(vals) < 40:
            out.append({"feature": fid, "ok": False, "reason": "N<40"})
            continue
        xs = np.asarray([v for v, _y in vals], dtype=float)
        q = np.percentile(xs, [25, 50, 75])
        bins = []
        groups = [
            [y for v, y in vals if v <= q[0] + 1e-15],
            [y for v, y in vals if v > q[0] and v <= q[1] + 1e-15],
            [y for v, y in vals if v > q[1] and v <= q[2] + 1e-15],
            [y for v, y in vals if v > q[2]],
        ]
        for name, got in zip(("Q1", "Q2", "Q3", "Q4"), groups):
            n = len(got)
            rate = (sum(got) / n) if n else None
            bins.append({"bin": name, "n": n, "SUSTAINED_RATE": rate, "FAILED_RATE": None if rate is None else 1.0 - rate})
        rates = [b["SUSTAINED_RATE"] for b in bins if b["SUSTAINED_RATE"] is not None]
        mono_up = len(rates) == 4 and all(rates[i] <= rates[i + 1] + 1e-12 for i in range(3))
        mono_dn = len(rates) == 4 and all(rates[i] >= rates[i + 1] - 1e-12 for i in range(3))
        q4_minus_q1 = None
        if bins[0]["SUSTAINED_RATE"] is not None and bins[3]["SUSTAINED_RATE"] is not None:
            q4_minus_q1 = float(bins[3]["SUSTAINED_RATE"] - bins[0]["SUSTAINED_RATE"])
        out.append(
            {
                "feature": fid,
                "ok": True,
                "quartile_edges": [float(q[0]), float(q[1]), float(q[2])],
                "bins": bins,
                "monotonic_up": mono_up,
                "monotonic_down": mono_dn,
                "q4_minus_q1_sustained_rate": q4_minus_q1,
                "threshold_not_frozen": True,
            }
        )
    return out


def fit_tree(rows: list[dict[str, Any]], *, features: list[str]) -> dict[str, Any]:
    feats = [f for f in features if f][:MAX_TREE_FEATURES]
    if not feats:
        return {"ok": False, "reason": "no_features"}
    xs = []
    ys = []
    for r in alpha_rows(rows):
        vec = [_num(r.get(f)) for f in feats]
        if any(v is None for v in vec):
            continue
        xs.append(vec)
        ys.append(1 if r.get("alpha_label") == LABEL_SUSTAINED else 0)
    if len(xs) < (TREE_MIN_LEAF_N * 2) or sum(ys) < TREE_MIN_LEAF_N or (len(ys) - sum(ys)) < TREE_MIN_LEAF_N:
        return {"ok": False, "reason": "leaf_n", "n": len(xs), "sustained_n": int(sum(ys))}
    try:
        from sklearn.tree import DecisionTreeClassifier, export_text
    except Exception as exc:
        return {"ok": False, "reason": f"sklearn:{exc}"}
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
    return {
        "ok": True,
        "n": int(len(ys)),
        "sustained_n": int(sum(ys)),
        "features_used": used,
        "importances": {feats[i]: float(clf.feature_importances_[i]) for i in range(len(feats))},
        "rules": rules,
        "max_depth": TREE_MAX_DEPTH,
        "max_leaf_nodes": TREE_MAX_LEAF_NODES,
        "min_leaf_n": TREE_MIN_LEAF_N,
    }


def lobo(rows: list[dict[str, Any]], *, candidates: list[str]) -> dict[str, Any]:
    alpha = alpha_rows(rows)
    folds = []
    reappear: dict[str, int] = defaultdict(int)
    flip = 0
    for hold in ("B1", "B2", "B3", "B4", "B5"):
        hold_days = set(FOLD_BLOCKS[hold])
        disc = [r for r in alpha if str(r.get("date")) not in hold_days]
        conf = [r for r in alpha if str(r.get("date")) in hold_days]
        feat_dir = {}
        for fid in candidates:
            s = _arr([r for r in disc if r.get("alpha_label") == LABEL_SUSTAINED], fid)
            f = _arr([r for r in disc if r.get("alpha_label") == LABEL_FAILED], fid)
            c_s = _arr([r for r in conf if r.get("alpha_label") == LABEL_SUSTAINED], fid)
            c_f = _arr([r for r in conf if r.get("alpha_label") == LABEL_FAILED], fid)
            dsign = _sign(float(np.median(s) - np.median(f))) if s.size and f.size else None
            csign = _sign(float(np.median(c_s) - np.median(c_f))) if c_s.size and c_f.size else None
            same = dsign is not None and csign is not None and dsign == csign and dsign != 0
            if dsign in (1, -1) and csign in (1, -1) and dsign != csign:
                flip += 1
            feat_dir[fid] = {"discovery_sign": dsign, "confirm_sign": csign, "same": same}
            if same:
                reappear[fid] += 1
        tree = fit_tree(disc, features=candidates)
        folds.append(
            {
                "holdout_block": hold,
                "discovery_n": len(disc),
                "confirm_n": len(conf),
                "feature_direction": feat_dir,
                "tree_features": tree.get("features_used"),
                "tree_rules": tree.get("rules"),
            }
        )
    return {
        "TRUE_OOS": False,
        "folds": folds,
        "direction_flip_n": flip,
        "feature_same_direction_fold_n": dict(reappear),
        "stable_across_folds": [f for f, n in reappear.items() if n >= 4],
    }


def logistic_direction(rows: list[dict[str, Any]], *, features: list[str]) -> dict[str, Any]:
    feats = [f for f in features if f][:MAX_TREE_FEATURES]
    xs = []
    ys = []
    for r in alpha_rows(rows):
        vec = [_num(r.get(f)) for f in feats]
        if any(v is None for v in vec):
            continue
        xs.append(vec)
        ys.append(1 if r.get("alpha_label") == LABEL_SUSTAINED else 0)
    if len(xs) < 80 or not feats:
        return {"ok": False, "reason": "n"}
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except Exception as exc:
        return {"ok": False, "reason": f"sklearn:{exc}"}
    scaler = StandardScaler()
    X = scaler.fit_transform(np.asarray(xs, dtype=float))
    y = np.asarray(ys, dtype=int)
    clf = LogisticRegression(max_iter=200, random_state=0)
    clf.fit(X, y)
    coef = {feats[i]: float(clf.coef_[0][i]) for i in range(len(feats))}
    return {"ok": True, "coef_standardized": coef, "intercept": float(clf.intercept_[0]), "n": int(len(ys))}


def decide(candidates: list[dict[str, Any]], *, tree: dict[str, Any], lobo_pack: dict[str, Any]) -> dict[str, Any]:
    stable = [c for c in candidates if c.get("mechanism_candidate")]
    fold_stable = list(lobo_pack.get("stable_across_folds") or [])
    fold_ok = (not stable) or (int(lobo_pack.get("direction_flip_n") or 0) == 0) or bool(set(fold_stable) & {c["feature"] for c in stable})
    found = bool(stable) and int(lobo_pack.get("direction_flip_n") or 0) == 0
    return {
        "CASE": "FOUND" if found else "NONE",
        "VERDICT": CASE_FOUND if found else CASE_NONE,
        "NEXT": NEXT_FOUND if found else NEXT_NONE,
        "LOGIC_COMPLETE": False,
        "ROBUST_DEV_QUALIFIED": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "V5_RESCUE": False,
        "THRESHOLD_OPTIMIZATION": False,
        "stable_feature_n": len(stable),
        "stable_features": [c["feature"] for c in stable],
        "tree_ok": bool(tree.get("ok")),
        "fold_ok": fold_ok,
        "PREVIOUS_CLOSE_LINE_EXHAUSTED": (not found),
        "INTERPRETATION": None,
    }


assert occupancy_rows
assert replay
assert LABEL_UNRESOLVED
assert FOLD_BLOCKS
