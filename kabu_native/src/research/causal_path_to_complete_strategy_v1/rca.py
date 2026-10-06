"""Within-family RCA on pre-event features. Continuous effects before thresholds. Discovery blocks only."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

STOCK_FAMILIES = (
    "VWAP_RECLAIM",
    "VWAP_LOSS",
    "EMA_IMPROVE",
    "EMA_DETERIORATE",
    "BREAKOUT20",
    "PULLBACK_START",
    "RS_IMPROVE",
    "RS_DETERIORATE",
    "RELVOL_EXPAND",
    "VA_EXPAND",
    "RANGE_EXPAND",
    "COMPRESSION_RELEASE",
)

FEATURES = (
    ("breadth_level", "MARKET", "LEVEL"),
    ("breadth_slope", "MARKET", "TRANSITION"),
    ("breadth_accel", "MARKET", "ACCELERATION"),
    ("median_ret_5m", "MARKET", "LEVEL"),
    ("dispersion_5m", "MARKET", "LEVEL"),
    ("dispersion_change", "MARKET", "TRANSITION"),
    ("leadership_hhi", "MARKET", "LEVEL"),
    ("leadership_change", "MARKET", "TRANSITION"),
    ("volume_active_ratio", "MARKET", "LEVEL"),
    ("ema_breadth", "MARKET", "LEVEL"),
    ("sector_up_5m", "SECTOR", "LEVEL"),
    ("sector_rs", "SECTOR", "LEVEL"),
    ("sector_rs_slope", "SECTOR", "TRANSITION"),
    ("rs_5m", "STOCK", "LEVEL"),
    ("rs_slope", "STOCK", "TRANSITION"),
    ("vwap_dist", "STOCK", "LEVEL"),
    ("ema_dist", "STOCK", "LEVEL"),
    ("dist_20high", "STOCK", "LEVEL"),
    ("vol_rel20", "STOCK", "LEVEL"),
    ("va_rel20", "STOCK", "LEVEL"),
    ("rng_rel20", "STOCK", "LEVEL"),
    ("ret_15m", "STOCK", "LEVEL"),
    ("ret_5m", "STOCK", "LEVEL"),
)

Y_KEY = "x0_h15_bps"
RHO_FLOOR = 0.012
BLOCK_AGREE = 3


def _finite(xs: list[Any]) -> np.ndarray:
    arr = np.asarray([float(x) for x in xs if x is not None and x == x], dtype=float)
    return arr[np.isfinite(arr)]


def _rank(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(a.size, dtype=float)
    ranks[order] = np.arange(1.0, a.size + 1.0)
    return ranks


def spearman(x: np.ndarray, y: np.ndarray) -> float | None:
    m = np.isfinite(x) & np.isfinite(y)
    if int(m.sum()) < 40:
        return None
    rx = _rank(x[m])
    ry = _rank(y[m])
    rx = rx - rx.mean()
    ry = ry - ry.mean()
    den = float(np.sqrt(np.sum(rx * rx) * np.sum(ry * ry)))
    if den == 0:
        return None
    return float(np.sum(rx * ry) / den)


def quintile_means(x: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    m = np.isfinite(x) & np.isfinite(y)
    if int(m.sum()) < 50:
        return {"n": int(m.sum()), "means": None, "monotonic": None, "u_shape": None}
    xx, yy = x[m], y[m]
    qs = np.percentile(xx, [20, 40, 60, 80])
    bins = np.digitize(xx, qs, right=True)
    means = []
    ns = []
    for b in range(5):
        sl = yy[bins == b]
        means.append(float(np.mean(sl)) if sl.size else None)
        ns.append(int(sl.size))
    finite = [m for m in means if m is not None]
    mono = None
    u_shape = None
    if len(finite) >= 4:
        diffs = np.diff(np.asarray(finite, dtype=float))
        mono = bool(np.all(diffs >= 0) or np.all(diffs <= 0))
        u_shape = bool(finite[0] > finite[2] and finite[-1] > finite[2]) or bool(finite[0] < finite[2] and finite[-1] < finite[2])
    return {"n": int(m.sum()), "means": means, "ns": ns, "monotonic": mono, "u_shape": u_shape, "q1": means[0], "q5": means[-1], "q5_minus_q1": (None if means[0] is None or means[-1] is None else float(means[-1] - means[0]))}


def _xy(rows: list[dict[str, Any]], feat: str) -> tuple[np.ndarray, np.ndarray]:
    x = []
    y = []
    for r in rows:
        xv = r.get(feat)
        yv = r.get(Y_KEY)
        if xv is None or yv is None:
            continue
        try:
            xf = float(xv)
            yf = float(yv)
        except (TypeError, ValueError):
            continue
        if xf == xf and yf == yf:
            x.append(xf)
            y.append(yf)
    return np.asarray(x, dtype=float), np.asarray(y, dtype=float)


def family_rca(*, events: list[dict[str, Any]], family: str, date_to_block: dict[str, str]) -> dict[str, Any]:
    rows = [e for e in events if e.get("event_family") == family and e.get("outcomes_attached") and e.get(Y_KEY) is not None]
    n = len(rows)
    tax: dict[str, int] = defaultdict(int)
    for r in rows:
        tax[str(r.get("path_taxonomy") or "UNLABELED")] += 1
    feat_rows = []
    useful_level = []
    useful_trans = []
    useful_acc = []
    for feat, layer, kind in FEATURES:
        x, y = _xy(rows, feat)
        rho = spearman(x, y)
        q = quintile_means(x, y)
        block_sign = []
        for bid in sorted(set(date_to_block.values())):
            sub = [r for r in rows if date_to_block.get(str(r.get("date"))) == bid]
            xb, yb = _xy(sub, feat)
            rb = spearman(xb, yb)
            if rb is None:
                continue
            block_sign.append({"block": bid, "rho": rb, "sign": int(np.sign(rb))})
        signs = [b["sign"] for b in block_sign if b["sign"] != 0]
        agree = 0
        if signs:
            maj = 1 if sum(signs) > 0 else -1
            agree = int(sum(1 for s in signs if s == maj))
        qmag = abs(float(q.get("q5_minus_q1") or 0.0))
        stable = bool(
            rho is not None
            and abs(rho) >= RHO_FLOOR
            and agree >= BLOCK_AGREE
            and len(signs) >= BLOCK_AGREE
            and qmag >= 1.0
        )
        rec = {
            "feature": feat,
            "layer": layer,
            "kind": kind,
            "spearman": rho,
            "q5_minus_q1": q.get("q5_minus_q1"),
            "monotonic": q.get("monotonic"),
            "u_shape": q.get("u_shape"),
            "quintile_means": q.get("means"),
            "block_rhos": block_sign,
            "block_agree_n": agree,
            "stable_across_blocks": stable,
        }
        feat_rows.append(rec)
        if stable:
            if kind == "LEVEL":
                useful_level.append(feat)
            elif kind == "TRANSITION":
                useful_trans.append(feat)
            elif kind == "ACCELERATION":
                useful_acc.append(feat)
    feat_rows.sort(key=lambda r: abs(r["spearman"] or 0.0), reverse=True)
    hi = [r for r in rows if r.get("path_quality_bps") is not None]
    hi.sort(key=lambda r: float(r["path_quality_bps"]), reverse=True)
    k = max(int(len(hi) * 0.2), 1) if hi else 0
    top = hi[:k]
    bot = hi[-k:] if k else []

    def mean_feat(xs: list[dict[str, Any]], feat: str) -> float | None:
        arr = _finite([x.get(feat) for x in xs])
        return float(np.mean(arr)) if arr.size else None

    contrast = []
    for feat, layer, kind in FEATURES:
        a = mean_feat(top, feat)
        b = mean_feat(bot, feat)
        contrast.append({"feature": feat, "kind": kind, "success_mean": a, "failure_mean": b, "diff": None if a is None or b is None else float(a - b)})
    contrast.sort(key=lambda r: abs(r["diff"] or 0.0), reverse=True)
    symbols = {str(r.get("symbol")) for r in rows}
    sectors = {str(r.get("sector") or "") for r in rows}
    return {
        "family": family,
        "n": n,
        "symbol_n": len(symbols),
        "sector_n": len(sectors),
        "taxonomy": dict(tax),
        "features": feat_rows[:12],
        "success_vs_failure_contrast": contrast[:10],
        "level_useful": useful_level,
        "transition_useful": useful_trans,
        "acceleration_useful": useful_acc,
        "stable_features": [f["feature"] for f in feat_rows if f.get("stable_across_blocks")],
    }


def diagnostic_interactions(*, events: list[dict[str, Any]], family: str, features: list[str]) -> dict[str, Any]:
    rows = [e for e in events if e.get("event_family") == family and e.get(Y_KEY) is not None]
    feats = [f for f in features if f][:6]
    pairs = []
    for i, a in enumerate(feats):
        for b in feats[i + 1 :]:
            xa, y = _xy(rows, a)
            xb, _y2 = _xy(rows, b)
            # align via rows again
            xs = []
            ys = []
            zs = []
            for r in rows:
                try:
                    fa, fb, yy = float(r.get(a)), float(r.get(b)), float(r.get(Y_KEY))
                except (TypeError, ValueError):
                    continue
                if fa == fa and fb == fb and yy == yy:
                    xs.append(fa)
                    ys.append(fb)
                    zs.append(yy)
            if len(zs) < 80:
                continue
            xa = np.asarray(xs)
            xb = np.asarray(ys)
            yv = np.asarray(zs)
            qa = np.percentile(xa, 80)
            qb = np.percentile(xb, 80)
            both = yv[(xa >= qa) & (xb >= qb)]
            rest = yv[~((xa >= qa) & (xb >= qb))]
            if both.size < 20 or rest.size < 20:
                continue
            pairs.append(
                {
                    "a": a,
                    "b": b,
                    "both_q5_mean": float(np.mean(both)),
                    "rest_mean": float(np.mean(rest)),
                    "lift": float(np.mean(both) - np.mean(rest)),
                    "n_both": int(both.size),
                }
            )
    pairs.sort(key=lambda r: abs(r["lift"]), reverse=True)
    tree = None
    try:
        from sklearn.tree import DecisionTreeRegressor

        use = feats[:5]
        X = []
        y = []
        for r in rows:
            row = []
            ok = True
            for f in use:
                try:
                    v = float(r.get(f))
                except (TypeError, ValueError):
                    ok = False
                    break
                if v != v:
                    ok = False
                    break
                row.append(v)
            if not ok:
                continue
            X.append(row)
            y.append(float(r[Y_KEY]))
        if len(y) >= 120 and use:
            mdl = DecisionTreeRegressor(max_depth=2, min_samples_leaf=40, random_state=0)
            mdl.fit(np.asarray(X), np.asarray(y))
            tree = {"features": use, "depth": 2, "importances": [float(x) for x in mdl.feature_importances_], "not_production_strategy": True}
    except Exception:
        tree = {"skipped": True, "reason": "sklearn_unavailable_or_fit_failed"}
    return {"family": family, "pairs": pairs[:8], "tree": tree, "model_is_not_strategy": True}


def rca_bundle(*, events: list[dict[str, Any]], date_to_block: dict[str, str]) -> dict[str, Any]:
    stock_events = [e for e in events if e.get("layer") == "STOCK"]
    families = [f for f in STOCK_FAMILIES]
    by_fam = {f: family_rca(events=stock_events, family=f, date_to_block=date_to_block) for f in families}
    pull = by_fam.get("PULLBACK_START") or {}
    brk = by_fam.get("BREAKOUT20") or {}
    other = {k: v for k, v in by_fam.items() if k not in {"PULLBACK_START", "BREAKOUT20"}}
    diags = []
    for fam in ("PULLBACK_START", "BREAKOUT20", "VWAP_RECLAIM", "COMPRESSION_RELEASE"):
        stable = list((by_fam.get(fam) or {}).get("stable_features") or [])
        top = [r["feature"] for r in list((by_fam.get(fam) or {}).get("features") or [])[:4]]
        diags.append(diagnostic_interactions(events=stock_events, family=fam, features=stable or top))
    level = sorted({x for v in by_fam.values() for x in v.get("level_useful") or []})
    trans = sorted({x for v in by_fam.values() for x in v.get("transition_useful") or []})
    acc = sorted({x for v in by_fam.values() for x in v.get("acceleration_useful") or []})
    structure = bool(
        level
        or trans
        or acc
        or any(v.get("stable_features") for v in by_fam.values())
        or any(abs(f.get("spearman") or 0) >= 0.01 and int(f.get("block_agree_n") or 0) >= 3 for v in by_fam.values() for f in list(v.get("features") or []))
    )
    chain = bool("breadth_slope" in trans or "breadth_level" in level) and bool(
        any("sector_rs" in (v.get("stable_features") or []) or "sector_up_5m" in (v.get("stable_features") or []) for v in by_fam.values())
    ) and bool(any("rs_5m" in (v.get("stable_features") or []) or "rs_slope" in (v.get("stable_features") or []) for v in by_fam.values()))
    stock_only = bool(any(v.get("stable_features") for v in by_fam.values())) and not chain
    return {
        "by_family": by_fam,
        "pullback": pull,
        "breakout": brk,
        "other": other,
        "diagnostics": diags,
        "level_useful": level,
        "transition_useful": trans,
        "acceleration_useful": acc,
        "stable_market_sector_stock_chain": chain,
        "stable_stock_only_chain": stock_only,
        "structure_found": structure,
    }
