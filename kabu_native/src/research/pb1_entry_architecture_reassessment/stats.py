"""Univariate rank correlation and quantile tables. No grid search."""
from __future__ import annotations

from math import sqrt
from typing import Any

from research.pb1_entry_architecture_reassessment import FOLDS, MIN_BUCKET_N, MIN_FOLD_N, MIN_SPEARMAN_N, QUANTILE_CUTS


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _mean(xs: list[float]) -> float | None:
    return float(sum(xs) / len(xs)) if xs else None


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    n = len(ys)
    if n % 2:
        return float(ys[n // 2])
    return float(ys[n // 2 - 1] + ys[n // 2]) / 2.0


def rankdata(xs: list[float]) -> list[float]:
    n = len(xs)
    order = sorted(range(n), key=lambda i: xs[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < int(MIN_SPEARMAN_N) or len(x) != len(y):
        return None
    rx, ry = rankdata(x), rankdata(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    denx = sqrt(sum((a - mx) ** 2 for a in rx))
    deny = sqrt(sum((b - my) ** 2 for b in ry))
    if denx <= 0 or deny <= 0:
        return None
    return float(num / (denx * deny))


def quantile_edges(xs: list[float]) -> list[float]:
    ys = sorted(xs)
    n = len(ys)
    edges = []
    for q in QUANTILE_CUTS:
        i = min(n - 1, max(0, int(q * n) - (1 if q > 0 and int(q * n) == q * n else 0)))
        i = min(n - 1, max(0, int(round((n - 1) * q))))
        edges.append(float(ys[i]))
    return edges


def bucket_of(v: float, edges: list[float]) -> int:
    for i, e in enumerate(edges):
        if v <= e:
            return i
    return len(edges)


def fold_quantiles(rows: list[dict[str, Any]], *, feat: str) -> list[dict[str, Any]]:
    out = []
    for fold in FOLDS:
        sub = [r for r in rows if r.get("arch_fold") == fold and _finite(r.get(feat))]
        if len(sub) < int(MIN_FOLD_N):
            out.append({"feature": feat, "fold": fold, "n": len(sub), "ok": False})
            continue
        xs = [float(r[feat]) for r in sub]
        edges = quantile_edges(xs)
        buckets: dict[int, list[dict[str, Any]]] = {i: [] for i in range(len(QUANTILE_CUTS) + 1)}
        for r in sub:
            buckets[bucket_of(float(r[feat]), edges)].append(r)
        q20 = buckets[0]
        q80 = buckets[len(QUANTILE_CUTS)]
        def _pack(name: str, grp: list[dict[str, Any]]) -> dict[str, Any]:
            nets = [float(x.get("net_bps") or 0) for x in grp]
            gross = [float(x.get("gross_bps") or 0) for x in grp]
            mfe = [float(x["MFE_bps"]) for x in grp if _finite(x.get("MFE_bps"))]
            mae = [float(x["MAE_bps"]) for x in grp if _finite(x.get("MAE_bps"))]
            de = sum(1 for x in grp if str(x.get("path_class") or "") in {"D_FAVORABLE_AND_EXIT_CAPTURED", "E_LATE_REVERSAL_AFTER_VALID_MOVE"})
            bc = sum(1 for x in grp if str(x.get("path_class") or "") in {"B_SMALL_EDGE_NEVER_EXPANDED", "C_FAVORABLE_THEN_FULL_GIVEBACK"})
            return {
                "n": len(grp),
                "mean_net_bps": _mean(nets),
                "median_net_bps": _median(nets),
                "mean_gross_bps": _mean(gross),
                "p_de": (de / len(grp)) if grp else None,
                "p_bc": (bc / len(grp)) if grp else None,
                "mean_mfe": _mean(mfe),
                "mean_mae": _mean(mae),
                "bucket": name,
            }
        packs = [_pack(f"Q{int(100 * ([0.0] + list(QUANTILE_CUTS))[i])}_{int(100 * (list(QUANTILE_CUTS) + [1.0])[i])}", buckets[i]) for i in range(len(buckets))]
        gap = None
        if len(q20) >= int(MIN_BUCKET_N) and len(q80) >= int(MIN_BUCKET_N):
            m20 = _mean([float(x.get("net_bps") or 0) for x in q20])
            m80 = _mean([float(x.get("net_bps") or 0) for x in q80])
            if m20 is not None and m80 is not None:
                gap = float(m80) - float(m20)
        rho = spearman(xs, [float(r.get("net_bps") or 0) for r in sub])
        out.append(
            {
                "feature": feat,
                "fold": fold,
                "n": len(sub),
                "ok": True,
                "spearman_net_bps": rho,
                "q80_minus_q20_net_bps": gap,
                "sign": (1 if gap and gap > 0 else (-1 if gap and gap < 0 else 0)),
                "buckets": packs,
            }
        )
    return out


def feature_summary(rows: list[dict[str, Any]], *, feat: str) -> dict[str, Any]:
    pairs = [(float(r[feat]), float(r.get("net_bps") or 0), float(r.get("entry_px") or 0)) for r in rows if _finite(r.get(feat))]
    rho_net = spearman([p[0] for p in pairs], [p[1] for p in pairs]) if pairs else None
    rho_px = spearman([p[0] for p in pairs], [p[2] for p in pairs]) if pairs else None
    folds = fold_quantiles(rows, feat=feat)
    signs = [int(f.get("sign") or 0) for f in folds if f.get("ok") and f.get("sign") in (-1, 1)]
    pos = sum(1 for s in signs if s > 0)
    neg = sum(1 for s in signs if s < 0)
    fold_names_pos = [str(f["fold"]) for f in folds if f.get("ok") and int(f.get("sign") or 0) > 0]
    fold_names_neg = [str(f["fold"]) for f in folds if f.get("ok") and int(f.get("sign") or 0) < 0]
    direction = 1 if pos > neg else (-1 if neg > pos else 0)
    aligned = fold_names_pos if direction > 0 else fold_names_neg
    has_dev = any(x.startswith("DEV_") for x in aligned)
    has_c1 = any(x.startswith("C1_") for x in aligned)
    return {
        "feature": feat,
        "n": len(pairs),
        "spearman_net_bps": rho_net,
        "spearman_entry_px": rho_px,
        "price_proxy": abs(float(rho_px or 0)) >= 0.85,
        "folds": folds,
        "n_folds_positive_gap": pos,
        "n_folds_negative_gap": neg,
        "direction": direction,
        "aligned_folds": aligned,
        "has_dev": has_dev,
        "has_c1": has_c1,
        "n_aligned_folds": len(aligned),
    }
