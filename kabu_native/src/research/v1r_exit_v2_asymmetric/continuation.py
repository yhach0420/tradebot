"""600s Continuation Gate — extend to 750 only if continuation supported."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np


def generate_continuation_rules() -> list[dict[str, Any]]:
    """Low-DoF rules only: single or 2-variable."""
    rules: list[dict[str, Any]] = []

    # Single-variable
    for thr in (10, 20, 30, 40, 50):
        rules.append({"id": f"RET_ge_{thr}", "kind": "ret_ge", "ret_min": float(thr)})
    for thr in (30, 45, 60, 75, 100):
        rules.append({"id": f"MFE_ge_{thr}", "kind": "mfe_ge", "mfe_min": float(thr)})
    for thr in (0.3, 0.4, 0.5, 0.6):
        rules.append({"id": f"GB_frac_le_{int(thr*100)}", "kind": "gb_frac_le", "gb_frac_max": float(thr)})
    for thr in (0.0, 0.1, 0.2):
        rules.append({"id": f"IMB_ge_{int(thr*100)}", "kind": "imb_ge", "imb_min": float(thr)})
    for thr in (20, 30, 45):
        rules.append({"id": f"RECENT_RET_ge_{thr}", "kind": "recent_ret_ge", "recent_ret_min": float(thr), "look_sec": 60.0})

    # Two-variable
    for ret_min in (10, 20, 30):
        for mfe_min in (30, 45, 60):
            rules.append({
                "id": f"RET{ret_min}_MFE{mfe_min}",
                "kind": "ret_and_mfe",
                "ret_min": float(ret_min),
                "mfe_min": float(mfe_min),
            })
    for ret_min in (10, 20):
        for gb in (0.4, 0.5, 0.6):
            rules.append({
                "id": f"RET{ret_min}_GBle{int(gb*100)}",
                "kind": "ret_and_gb",
                "ret_min": float(ret_min),
                "gb_frac_max": float(gb),
            })
    for mfe_min in (45, 60):
        for imb_min in (0.0, 0.1):
            rules.append({
                "id": f"MFE{mfe_min}_IMB{int(imb_min*100)}",
                "kind": "mfe_and_imb",
                "mfe_min": float(mfe_min),
                "imb_min": float(imb_min),
            })

    # Always / never extend controls
    rules.append({"id": "ALWAYS_EXTEND", "kind": "always"})
    rules.append({"id": "NEVER_EXTEND", "kind": "never"})
    return rules


def features_at_600(bundle: dict[str, Any]) -> dict[str, float]:
    st = bundle["states"].get(600) or {}
    path = bundle["path"]
    recent = 0.0
    if path.get("ok") and path["offs"].size:
        offs, rets = path["offs"], path["rets"]
        j = int(np.searchsorted(offs, 600.0, side="left"))
        j = min(max(j, 0), offs.size - 1)
        j0 = int(np.searchsorted(offs, 540.0, side="left"))
        j0 = min(max(j0, 0), offs.size - 1)
        recent = float(rets[j]) - float(rets[j0])
    mfe = float(st.get("mfe") or 0)
    ret = float(st.get("ret") or 0)
    gb = float(st.get("dd_from_mfe") or 0)
    return {
        "ret": ret,
        "mfe": mfe,
        "gb_frac": (gb / mfe) if mfe > 1e-6 else 0.0,
        "imb": float(st["imbalance"]) if st.get("imbalance") is not None else 0.0,
        "spread": float(st["spread_bps"]) if st.get("spread_bps") is not None else 0.0,
        "er": float(st["event_rate"]) if st.get("event_rate") is not None else 0.0,
        "recent_ret": recent,
        "time_since_high": float(st.get("time_since_high") or 0),
        "recovery": 1.0 if st.get("recovery_persist") else 0.0,
        "sell": 1.0 if st.get("sell_persist") else 0.0,
    }


def continuation_supported(bundle: dict[str, Any], rule: dict[str, Any]) -> bool:
    f = features_at_600(bundle)
    kind = rule["kind"]
    if kind == "always":
        return True
    if kind == "never":
        return False
    if kind == "ret_ge":
        return f["ret"] >= float(rule["ret_min"]) - 1e-12
    if kind == "mfe_ge":
        return f["mfe"] >= float(rule["mfe_min"]) - 1e-12
    if kind == "gb_frac_le":
        return f["gb_frac"] <= float(rule["gb_frac_max"]) + 1e-12 and f["mfe"] >= 20
    if kind == "imb_ge":
        return f["imb"] >= float(rule["imb_min"]) - 1e-12 and f["ret"] >= 0
    if kind == "recent_ret_ge":
        return f["recent_ret"] >= float(rule["recent_ret_min"]) - 1e-12
    if kind == "ret_and_mfe":
        return f["ret"] >= float(rule["ret_min"]) - 1e-12 and f["mfe"] >= float(rule["mfe_min"]) - 1e-12
    if kind == "ret_and_gb":
        return (
            f["ret"] >= float(rule["ret_min"]) - 1e-12
            and f["gb_frac"] <= float(rule["gb_frac_max"]) + 1e-12
            and f["mfe"] >= 20
        )
    if kind == "mfe_and_imb":
        return f["mfe"] >= float(rule["mfe_min"]) - 1e-12 and f["imb"] >= float(rule["imb_min"]) - 1e-12
    if kind == "learned":
        model = rule.get("model")
        thr = float(rule.get("threshold") or 0.55)
        if model is None:
            return False
        return float(predict_extend_proba(model, f)) >= thr - 1e-12
    return False


FEAT_KEYS = ("ret", "mfe", "gb_frac", "imb", "recent_ret", "time_since_high", "recovery")


def _x_from_feats(f: dict[str, float]) -> list[float]:
    return [float(f[k]) for k in FEAT_KEYS]


def fit_continuation_models(bundles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Train-only shallow models: label = 1 if ret750 > ret600."""
    xs, ys = [], []
    for b in bundles:
        if b.get("ret600") is None or b.get("ret750") is None:
            continue
        # only trades that would reach 600 without early exit — caller filters
        f = features_at_600(b)
        xs.append(_x_from_feats(f))
        ys.append(1 if float(b["ret750"]) > float(b["ret600"]) + 1e-9 else 0)
    out: list[dict[str, Any]] = []
    if len(xs) < 40 or len(set(ys)) < 2:
        return out
    X = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=int)
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.tree import DecisionTreeClassifier
    except ImportError:
        return out

    sc = StandardScaler()
    Xs = sc.fit_transform(X)
    for C in (0.2, 0.5, 1.0):
        clf = LogisticRegression(
            penalty=None, solver="lbfgs", C=1.0, max_iter=2000, random_state=0
        )
        # use L2 via C on saga with l1_ratio=0 for compatibility; prefer ridge-ish
        try:
            clf = LogisticRegression(solver="lbfgs", C=C, max_iter=2000, random_state=0)
            clf.fit(Xs, y)
            for thr in (0.55, 0.65):
                out.append({
                    "id": f"LOG_C{C}_t{int(thr*100)}",
                    "kind": "learned",
                    "model": {"kind": "log", "scaler": sc, "clf": clf},
                    "threshold": thr,
                })
        except Exception:
            pass

    for depth in (1, 2):
        tree = DecisionTreeClassifier(max_depth=depth, min_samples_leaf=12, random_state=0)
        tree.fit(X, y)
        for thr in (0.55, 0.65):
            out.append({
                "id": f"TREE_d{depth}_t{int(thr*100)}",
                "kind": "learned",
                "model": {"kind": "tree", "clf": tree},
                "threshold": thr,
            })
    return out


def predict_extend_proba(model: dict[str, Any], f: dict[str, float]) -> float:
    x = np.asarray([_x_from_feats(f)], dtype=float)
    if model["kind"] == "log":
        xs = model["scaler"].transform(x)
        return float(model["clf"].predict_proba(xs)[0, 1])
    return float(model["clf"].predict_proba(x)[0, 1])
