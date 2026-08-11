"""Shallow learned EXIT scorers (low DoF; train-only fit)."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np


DECISION_OFFS = (5, 10, 20, 30, 45, 60, 90, 120, 180, 240, 300, 420, 600)


def _prefix_feats(path: dict[str, Any], i: int) -> list[float]:
    rets = path["rets"]
    offs = path["offs"]
    rr = rets[: i + 1]
    mfe = float(np.max(rr))
    mae = float(np.min(rr))
    ret = float(rr[-1])
    giveback = mfe - ret if mfe > 0 else 0.0
    imb = path.get("imb")
    spr = path.get("spread")
    bq = path.get("bid_qty")
    er = path.get("event_rate")
    imb_v = float(imb[i]) if imb is not None and np.isfinite(imb[i]) else 0.0
    spr_v = float(spr[i]) if spr is not None and np.isfinite(spr[i]) else 0.0
    bq0 = path.get("bid_qty0") or 1.0
    bq_v = float(bq[i]) / float(bq0) if bq is not None and np.isfinite(bq[i]) and bq0 else 1.0
    er0 = path.get("er0") or 1.0
    er_v = float(er[i]) / float(er0) if er is not None and np.isfinite(er[i]) and er0 else 1.0
    return [
        float(offs[i]),
        ret,
        mfe,
        mae,
        giveback,
        giveback / mfe if mfe > 1e-6 else 0.0,
        imb_v,
        spr_v,
        bq_v,
        er_v,
    ]


def build_decision_rows(
    trades: list[dict[str, Any]],
    *,
    hold_fallback: float = 600.0,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]]]:
    """
    Rows at each decision off. Label y=1 if exiting now beats FIXED hold_fallback PnL path.
    Uses only path info up to decision time for features; label uses future (train only).
    """
    xs: list[list[float]] = []
    ys: list[int] = []
    meta: list[dict[str, Any]] = []
    for tr in trades:
        path = tr["path"]
        if not path.get("ok") or path["offs"].size == 0:
            continue
        offs, rets, times = path["offs"], path["rets"], path["times"]
        # fixed600 ret
        j600 = int(np.searchsorted(offs, hold_fallback, side="left"))
        if j600 >= offs.size:
            j600 = offs.size - 1
        fixed_ret = float(rets[j600])
        for off in DECISION_OFFS:
            if off >= hold_fallback - 1e-9:
                continue
            j = int(np.searchsorted(offs, float(off), side="left"))
            if j >= offs.size:
                continue
            if float(offs[j]) > float(off) + 30:  # no tick near off
                continue
            feats = _prefix_feats(path, j)
            now_ret = float(rets[j])
            # exit now better if now_ret > fixed_ret (avoid holding into worse)
            y = 1 if now_ret > fixed_ret + 1e-9 else 0
            xs.append(feats)
            ys.append(y)
            meta.append({
                "date": tr["date"],
                "symbol": tr["symbol"],
                "fill_time": tr["fill_time"],
                "off": float(offs[j]),
                "i": j,
                "now_ret": now_ret,
                "fixed_ret": fixed_ret,
            })
    if not xs:
        return np.zeros((0, 10)), np.zeros(0, dtype=int), []
    return np.asarray(xs, dtype=float), np.asarray(ys, dtype=int), meta


def fit_logistic_l1(X: np.ndarray, y: np.ndarray, *, C: float = 0.5) -> Optional[dict[str, Any]]:
    if X.shape[0] < 40 or len(set(y.tolist())) < 2:
        return None
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        return None
    sc = StandardScaler()
    Xs = sc.fit_transform(X)
    clf = LogisticRegression(
        penalty="l1", solver="saga", C=C, max_iter=2000, random_state=0
    )
    clf.fit(Xs, y)
    return {"kind": "logistic_l1", "C": C, "scaler": sc, "clf": clf}


def fit_shallow_tree(X: np.ndarray, y: np.ndarray, *, depth: int = 3) -> Optional[dict[str, Any]]:
    if X.shape[0] < 40 or len(set(y.tolist())) < 2:
        return None
    try:
        from sklearn.tree import DecisionTreeClassifier
    except ImportError:
        return None
    clf = DecisionTreeClassifier(max_depth=depth, min_samples_leaf=15, random_state=0)
    clf.fit(X, y)
    return {"kind": "tree", "depth": depth, "clf": clf}


def predict_proba(model: dict[str, Any], x: list[float]) -> float:
    X = np.asarray([x], dtype=float)
    if model["kind"] == "logistic_l1":
        Xs = model["scaler"].transform(X)
        return float(model["clf"].predict_proba(Xs)[0, 1])
    return float(model["clf"].predict_proba(X)[0, 1])


def apply_learned_exit(
    path: dict[str, Any],
    model: dict[str, Any],
    *,
    threshold: float,
    hold_sec: float = 600.0,
) -> dict[str, Any]:
    if not path.get("ok") or path["offs"].size == 0:
        return {"ok": False}
    offs, rets, times = path["offs"], path["rets"], path["times"]
    for off in DECISION_OFFS:
        if off >= hold_sec - 1e-9:
            break
        j = int(np.searchsorted(offs, float(off), side="left"))
        if j >= offs.size:
            continue
        if abs(float(offs[j]) - float(off)) > 15:
            continue
        p = predict_proba(model, _prefix_feats(path, j))
        if p >= threshold:
            return {
                "ok": True,
                "exit_ret_bps": float(rets[j]),
                "hold_sec": float(offs[j]),
                "exit_time": float(times[j]),
                "reason": "LEARNED",
                "triggered": True,
                "mfe_at_exit": float(np.max(rets[: j + 1])),
                "p_exit": p,
            }
    # fallback hold
    j = int(np.searchsorted(offs, hold_sec, side="left"))
    if j >= offs.size:
        j = offs.size - 1
    return {
        "ok": True,
        "exit_ret_bps": float(rets[j]),
        "hold_sec": float(offs[j]),
        "exit_time": float(times[j]),
        "reason": "FIXED_HOLD",
        "triggered": False,
        "mfe_at_exit": float(np.max(rets[: j + 1])),
        "p_exit": None,
    }
