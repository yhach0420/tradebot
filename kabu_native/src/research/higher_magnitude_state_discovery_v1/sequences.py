"""Sequence increment and path magnitude. Outcomes are labels only. Day/episode units, not raw event rows."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

Y = "x0_h15_bps"
PRIOR_CEILING = 6.0

SEQUENCE_ORDER = (
    "RECLAIM_ONLY",
    "PULLBACK_THEN_RECLAIM",
    "SECTOR_THEN_PULLBACK_THEN_RECLAIM",
    "BREAKOUT_ONLY",
    "COMPRESSION_THEN_BREAKOUT",
    "SECTOR_THEN_COMPRESSION_THEN_BREAKOUT",
    "RS_THEN_TECHNICAL",
    "SECTOR_THEN_RS_THEN_TECHNICAL",
    "OTHER",
)


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _day_mean(rows: list[dict[str, Any]], key: str) -> float | None:
    by: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if _finite(r.get(key)):
            by[str(r.get("date"))].append(float(r[key]))
    if not by:
        return None
    return float(np.mean([float(np.mean(vs)) for vs in by.values()]))


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    y = [float(r[Y]) for r in rows if _finite(r.get(Y))]
    mfe = [float(r["mfe_bps"]) for r in rows if _finite(r.get("mfe_bps"))]
    mae = [float(r["mae_bps"]) for r in rows if _finite(r.get("mae_bps"))]
    fav = [r for r in rows if _finite(r.get("mfe_bps")) and _finite(r.get("mae_bps"))]
    first = [1.0 if bool(r.get("mfe_before_mae")) else 0.0 for r in fav]
    ratio = []
    for r in fav:
        if abs(float(r["mae_bps"])) > 1e-9:
            ratio.append(float(r["mfe_bps"]) / abs(float(r["mae_bps"])))
    return {
        "episode_n": len(rows),
        "labeled_n": len(y),
        "day_n": len({str(r.get("date")) for r in rows}),
        "symbol_n": len({str(r.get("symbol")) for r in rows}),
        "mean_x0_h15": float(np.mean(y)) if y else None,
        "day_mean_x0_h15": _day_mean(rows, Y),
        "mean_x1_h15": (float(np.mean(y)) - 8.0) if y else None,
        "mean_mfe": float(np.mean(mfe)) if mfe else None,
        "mean_mae": float(np.mean(mae)) if mae else None,
        "mfe_mae_ratio": float(np.mean(ratio)) if ratio else None,
        "p_mfe_before_mae": float(np.mean(first)) if first else None,
        "exceeds_prior_1_6bps": bool(y and abs(float(np.mean(y))) > PRIOR_CEILING),
        "material_vs_prior": bool(y and float(np.mean(y)) > PRIOR_CEILING),
    }


def sequence_increment(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in episodes:
        by[str(e.get("sequence_class") or "OTHER")].append(e)
    rows = []
    for name in SEQUENCE_ORDER:
        st = _stats(by.get(name) or [])
        st["sequence_class"] = name
        rows.append(st)
    def grab(name: str, field: str) -> float | None:
        rec = next((r for r in rows if r["sequence_class"] == name), {})
        v = rec.get(field)
        return float(v) if v is not None else None

    increments = [
        {
            "comparison": "PULLBACK_THEN_RECLAIM vs RECLAIM_ONLY",
            "delta_day_mean_x0": _sub(grab("PULLBACK_THEN_RECLAIM", "day_mean_x0_h15"), grab("RECLAIM_ONLY", "day_mean_x0_h15")),
            "delta_mfe": _sub(grab("PULLBACK_THEN_RECLAIM", "mean_mfe"), grab("RECLAIM_ONLY", "mean_mfe")),
        },
        {
            "comparison": "SECTOR_THEN_PULLBACK_THEN_RECLAIM vs PULLBACK_THEN_RECLAIM",
            "delta_day_mean_x0": _sub(grab("SECTOR_THEN_PULLBACK_THEN_RECLAIM", "day_mean_x0_h15"), grab("PULLBACK_THEN_RECLAIM", "day_mean_x0_h15")),
            "delta_mfe": _sub(grab("SECTOR_THEN_PULLBACK_THEN_RECLAIM", "mean_mfe"), grab("PULLBACK_THEN_RECLAIM", "mean_mfe")),
        },
        {
            "comparison": "COMPRESSION_THEN_BREAKOUT vs BREAKOUT_ONLY",
            "delta_day_mean_x0": _sub(grab("COMPRESSION_THEN_BREAKOUT", "day_mean_x0_h15"), grab("BREAKOUT_ONLY", "day_mean_x0_h15")),
            "delta_mfe": _sub(grab("COMPRESSION_THEN_BREAKOUT", "mean_mfe"), grab("BREAKOUT_ONLY", "mean_mfe")),
        },
        {
            "comparison": "SECTOR_THEN_COMPRESSION_THEN_BREAKOUT vs COMPRESSION_THEN_BREAKOUT",
            "delta_day_mean_x0": _sub(
                grab("SECTOR_THEN_COMPRESSION_THEN_BREAKOUT", "day_mean_x0_h15"), grab("COMPRESSION_THEN_BREAKOUT", "day_mean_x0_h15")
            ),
            "delta_mfe": _sub(grab("SECTOR_THEN_COMPRESSION_THEN_BREAKOUT", "mean_mfe"), grab("COMPRESSION_THEN_BREAKOUT", "mean_mfe")),
        },
        {
            "comparison": "SECTOR_THEN_RS_THEN_TECHNICAL vs RS_THEN_TECHNICAL",
            "delta_day_mean_x0": _sub(grab("SECTOR_THEN_RS_THEN_TECHNICAL", "day_mean_x0_h15"), grab("RS_THEN_TECHNICAL", "day_mean_x0_h15")),
            "delta_mfe": _sub(grab("SECTOR_THEN_RS_THEN_TECHNICAL", "mean_mfe"), grab("RS_THEN_TECHNICAL", "mean_mfe")),
        },
    ]
    order_adds = any(d.get("delta_day_mean_x0") is not None and float(d["delta_day_mean_x0"]) >= 2.0 for d in increments)
    best = max((r for r in rows if r.get("day_mean_x0_h15") is not None), key=lambda r: float(r["day_mean_x0_h15"]), default=None)
    return {
        "by_class": rows,
        "increments": increments,
        "sequence_order_adds_value": bool(order_adds),
        "largest_class": None if best is None else {k: best.get(k) for k in ("sequence_class", "day_mean_x0_h15", "mean_mfe", "episode_n", "day_n")},
        "unit": "day_mean_of_episode_labels_not_raw_event_rows",
    }


def _sub(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return float(a - b)


def archetype_magnitude(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for name in ("CONTROLLED_PULLBACK_RECLAIM", "COMPRESSION_BREAKOUT_EXPANSION", "SECTOR_LEADER_TRANSITION"):
        rows = [e for e in episodes if e.get("archetype") == name]
        out[name] = _stats(rows)
        out[name]["archetype"] = name
    return out


def diagnostic_model(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    feats = ["sector_rs", "sector_rs_slope", "rs_5m", "vol_rel20", "rng_rel20", "breadth_level", "dispersion_change"]
    rows = [e for e in episodes if e.get("archetype") and _finite(e.get("mfe_bps"))]
    tree = {"skipped": True}
    try:
        from sklearn.tree import DecisionTreeRegressor

        X = []
        y = []
        for r in rows:
            vec = []
            ok = True
            for f in feats:
                if not _finite(r.get(f)):
                    ok = False
                    break
                vec.append(float(r[f]))
            if not ok:
                continue
            X.append(vec)
            y.append(float(r["mfe_bps"]))
        if len(y) >= 200:
            mdl = DecisionTreeRegressor(max_depth=2, min_samples_leaf=80, random_state=0)
            mdl.fit(np.asarray(X), np.asarray(y))
            tree = {
                "features": feats,
                "importances": [float(x) for x in mdl.feature_importances_],
                "depth": 2,
                "not_production": True,
            }
    except Exception as exc:
        tree = {"skipped": True, "reason": str(exc)[:200]}
    return {"tree": tree, "model_is_not_strategy": True, "n": len(rows)}
