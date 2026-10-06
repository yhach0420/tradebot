"""Leave-one-day-out L2 logistic. Scaler and fit on train days only. Threshold 0.5 fixed."""
from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, brier_score_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler

from research.am_c0_indicator_exit import EXIT_FEATURES, EXIT_THRESHOLD, LOGREG_PARAMS, OUTER_FOLD_N
from research.am_entry_profit_improvement import ELIGIBLE_DAYS


def _complete_pack(trades: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[int]]:
    xs = []
    ys = []
    ws = []
    owners: list[int] = []
    for i, tr in enumerate(trades):
        X = tr.get("X")
        y = tr.get("y")
        w = tr.get("weight")
        c = tr.get("complete")
        if X is None or y is None or w is None or c is None:
            continue
        c = np.asarray(c, dtype=bool)
        if not np.any(c):
            continue
        xs.append(np.asarray(X, dtype=float)[c])
        ys.append(np.asarray(y, dtype=float)[c])
        ws.append(np.asarray(w, dtype=float)[c])
        owners.extend([i] * int(np.sum(c)))
    if not xs:
        return np.zeros((0, len(EXIT_FEATURES))), np.zeros((0,)), np.zeros((0,)), []
    return np.vstack(xs), np.concatenate(ys), np.concatenate(ws), owners


def _fit(X: np.ndarray, y: np.ndarray, w: np.ndarray) -> tuple[StandardScaler, LogisticRegression]:
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    clf = LogisticRegression(**dict(LOGREG_PARAMS))
    yy = (np.asarray(y) > 0.5).astype(int)
    if yy.size and int(np.unique(yy).size) < 2:
        # still fit; sklearn can fail on one-class — duplicate a tiny opposite with tiny weight
        X2 = np.vstack([Xs, Xs[:1]])
        y2 = np.concatenate([yy, np.array([1 - int(yy[0])], dtype=int)])
        w2 = np.concatenate([w, np.array([1e-12], dtype=float)])
        clf.fit(X2, y2, sample_weight=w2)
        return scaler, clf
    clf.fit(Xs, yy, sample_weight=w)
    return scaler, clf


def _predict(scaler: StandardScaler, clf: LogisticRegression, X: np.ndarray) -> np.ndarray:
    if X.size == 0:
        return np.zeros((0,), dtype=float)
    Xs = scaler.transform(X)
    proba = clf.predict_proba(Xs)
    classes = list(clf.classes_)
    if 1 in classes:
        return np.asarray(proba[:, classes.index(1)], dtype=float)
    return np.zeros((X.shape[0],), dtype=float)


def attach_oof(day_bodies: list[dict[str, Any]]) -> dict[str, Any]:
    by_day: dict[str, list[dict[str, Any]]] = {}
    for body in day_bodies:
        day = str(body.get("date") or "")
        by_day[day] = list(body.get("trades") or [])
    fold_rows = []
    coef_rows = []
    oof_y = []
    oof_p = []
    oof_w = []
    train_trade_n_total = 0
    for held in ELIGIBLE_DAYS:
        train_trades: list[dict[str, Any]] = []
        held_trades = list(by_day.get(str(held)) or [])
        for d in ELIGIBLE_DAYS:
            if str(d) == str(held):
                continue
            train_trades.extend(list(by_day.get(str(d)) or []))
        train_fit = [t for t in train_trades if str(t.get("role") or "") in {"TRAIN_CURRENT", "TRAIN_AUGMENT"}]
        Xtr, ytr, wtr, _ = _complete_pack(train_fit)
        scaler, clf = _fit(Xtr, ytr, wtr)
        coef = np.asarray(getattr(clf, "coef_", [[0.0] * len(EXIT_FEATURES)])[0], dtype=float)
        for name, v in zip(EXIT_FEATURES, coef):
            coef_rows.append({"heldout_day": held, "feature": name, "coef": float(v)})
        n_held_train_role = sum(
            1 for t in held_trades if str(t.get("role") or "") in {"TRAIN_CURRENT", "TRAIN_AUGMENT"}
        )
        Xh, yh, wh, owners = _complete_pack(
            [t for t in held_trades if str(t.get("role") or "") in {"TRAIN_CURRENT", "TRAIN_AUGMENT"}]
        )
        ph = _predict(scaler, clf, Xh) if Xh.size else np.zeros((0,), dtype=float)
        oof_y.extend(list(yh))
        oof_p.extend(list(ph))
        oof_w.extend(list(wh))
        # attach P to every heldout trade (all roles) using this fold's model
        for tr in held_trades:
            X = tr.get("X")
            c = tr.get("complete")
            n = int(np.asarray(tr.get("t")).size) if tr.get("t") is not None else 0
            p = np.zeros(n, dtype=float)
            if X is not None and c is not None and n:
                c = np.asarray(c, dtype=bool)
                if np.any(c):
                    p[c] = _predict(scaler, clf, np.asarray(X, dtype=float)[c])
            tr["p_exit"] = p
        train_trade_n_total = max(train_trade_n_total, len(train_fit))
        fold_rows.append(
            {
                "heldout_day": held,
                "TRAIN_TRADE_N": len(train_fit),
                "HELDOUT_TRADE_N": n_held_train_role,
                "TRAIN_EVENT_ROW_N": int(Xtr.shape[0]),
                "HELDOUT_EVENT_ROW_N": int(Xh.shape[0]),
                "EFFECTIVE_TRADE_WEIGHT_TOTAL": float(np.sum(wtr)) if wtr.size else 0.0,
                "HELD_POS_N": int(np.sum(yh > 0.5)) if yh.size else 0,
                "HELD_NEG_N": int(np.sum(yh <= 0.5)) if yh.size else 0,
            }
        )
    y = np.asarray(oof_y, dtype=float)
    p = np.asarray(oof_p, dtype=float)
    w = np.asarray(oof_w, dtype=float)
    auc = None
    bal = None
    brier = None
    if y.size and int(np.unique((y > 0.5).astype(int)).size) == 2:
        try:
            auc = float(roc_auc_score((y > 0.5).astype(int), p, sample_weight=w))
        except Exception:
            auc = None
        try:
            bal = float(balanced_accuracy_score((y > 0.5).astype(int), (p > float(EXIT_THRESHOLD)).astype(int), sample_weight=w))
        except Exception:
            bal = None
        try:
            brier = float(brier_score_loss((y > 0.5).astype(int), p, sample_weight=w))
        except Exception:
            brier = None
    mean_coef = []
    by_f: dict[str, list[float]] = {k: [] for k in EXIT_FEATURES}
    for r in coef_rows:
        by_f[str(r["feature"])].append(float(r["coef"]))
    for k in EXIT_FEATURES:
        xs = by_f.get(k) or []
        mean_coef.append({"feature": k, "mean_coef": float(np.mean(xs)) if xs else None, "std_coef": float(np.std(xs)) if xs else None})
    return {
        "OUTER_FOLD_N": int(OUTER_FOLD_N),
        "folds": fold_rows,
        "coef_rows": coef_rows,
        "mean_coef": mean_coef,
        "OOF_AUC": auc,
        "OOF_BALANCED_ACCURACY": bal,
        "OOF_BRIER": brier,
        "OOF_POS_N": int(np.sum(y > 0.5)) if y.size else 0,
        "OOF_NEG_N": int(np.sum(y <= 0.5)) if y.size else 0,
        "OOF_EVENT_ROW_N": int(y.size),
        "P_EXIT_MEAN": float(np.mean(p)) if p.size else None,
        "P_EXIT_P50": float(np.median(p)) if p.size else None,
        "P_EXIT_P90": float(np.percentile(p, 90)) if p.size else None,
        "TRAIN_TRADE_N": int(train_trade_n_total),
        "day_bodies": day_bodies,
    }


def first_exit(
    tr: dict[str, Any],
    *,
    threshold: float = float(EXIT_THRESHOLD),
) -> dict[str, Any]:
    t = tr.get("t")
    bid = tr.get("bid")
    p = tr.get("p_exit")
    fill_px = float(tr.get("fill_price") or 0.0)
    sess_end = float(tr.get("sess_end") or 0.0)
    if t is None or bid is None:
        return {
            "ok": False,
            "exit_t": None,
            "exit_price": None,
            "exit_reason": "AM_SESSION_END",
            "pnl_yen_100": None,
            "triggered": False,
            "p_exit": None,
        }
    t = np.asarray(t, dtype=float)
    bid = np.asarray(bid, dtype=float)
    if p is None:
        p = np.zeros(t.size, dtype=float)
    else:
        p = np.asarray(p, dtype=float)
    trigger_i = None
    for i in range(t.size):
        if float(p[i]) > float(threshold):
            trigger_i = i
            break
    if trigger_i is not None:
        px = float(bid[trigger_i])
        from replay.pnl_yen import compute_pnl_yen_100

        return {
            "ok": True,
            "exit_t": float(t[trigger_i]),
            "exit_price": px,
            "exit_reason": "INDICATOR_STATE_EXIT",
            "pnl_yen_100": float(compute_pnl_yen_100(fill_px, px)),
            "triggered": True,
            "p_exit": float(p[trigger_i]),
        }
    # session-end: last valid bid already in path; if empty, no path
    if t.size:
        px = float(bid[-1])
        from replay.pnl_yen import compute_pnl_yen_100

        return {
            "ok": True,
            "exit_t": float(t[-1]),
            "exit_price": px,
            "exit_reason": "AM_SESSION_END",
            "pnl_yen_100": float(compute_pnl_yen_100(fill_px, px)),
            "triggered": False,
            "p_exit": float(p[-1]) if p.size else None,
            "sess_end": sess_end,
        }
    return {
        "ok": False,
        "exit_t": None,
        "exit_price": None,
        "exit_reason": "AM_SESSION_END",
        "pnl_yen_100": None,
        "triggered": False,
        "p_exit": None,
    }
