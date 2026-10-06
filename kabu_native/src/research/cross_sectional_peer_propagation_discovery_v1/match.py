"""Same-symbol same-DIR matched controls. Own-price nuisance only. No peer treatment features. No outcome."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.cross_sectional_peer_propagation_discovery_v1 import (
    CONTROL_RESERVOIR,
    RET1_ABS_TOL,
    RET3_ABS_TOL,
    RET5_ABS_TOL,
    RNG_REL_TOL,
)

NUISANCE = ("tod_min", "rng_rel", "gap_num", "target_ret_1m", "target_ret_3m", "target_ret_5m")


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _rel(a: Any, b: Any, tol: float) -> bool:
    if not _finite(a) and not _finite(b):
        return True
    if not _finite(a) or not _finite(b):
        return False
    return abs(float(a) - float(b)) / max(abs(float(b)), 1e-9) <= float(tol)


def _abs(a: Any, b: Any, tol: float) -> bool:
    if not _finite(a) and not _finite(b):
        return True
    if not _finite(a) or not _finite(b):
        return False
    return abs(float(a) - float(b)) <= float(tol)


def _smd(a: list[float], b: list[float]) -> float | None:
    import numpy as np

    if len(a) < 5 or len(b) < 5:
        return None
    mx, my = float(np.mean(a)), float(np.mean(b))
    vx, vy = float(np.var(a, ddof=1)), float(np.var(b, ddof=1))
    sp = float(np.sqrt(max((vx + vy) / 2.0, 0.0)))
    if sp <= 1e-12:
        return 0.0
    return float((mx - my) / sp)


def key_of(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (str(row.get("symbol") or ""), str(row.get("direction") or ""), str(row.get("bucket") or ""), str(row.get("gap") or ""))


def reservoir_add(store: dict[tuple[str, str, str, str], list[dict[str, Any]]], row: dict[str, Any], rng) -> None:
    k = key_of(row)
    xs = store[k]
    cap = int(CONTROL_RESERVOIR)
    if len(xs) < cap:
        xs.append(row)
        return
    j = int(rng.integers(0, len(xs) + 1))
    if j < cap:
        xs[j] = row


def find_control(tf: dict[str, Any], by: dict[tuple[str, str, str, str], list[dict[str, Any]]]) -> dict[str, Any] | None:
    buckets = [str(tf.get("bucket") or "")]
    try:
        b0 = int(buckets[0])
        buckets.extend([f"{b0 - 30:04d}", f"{b0 + 30:04d}"])
    except ValueError:
        pass
    best = None
    best_d = 1e9
    base = (str(tf.get("symbol") or ""), str(tf.get("direction") or ""), "", str(tf.get("gap") or ""))
    for bi, bucket in enumerate(buckets):
        pool = by.get((base[0], base[1], bucket, base[3]), ())
        for x in pool:
            if str(x.get("date") or "") == str(tf.get("date") or "") and int(x.get("pos") or -1) == int(tf.get("pos") or -2):
                continue
            if not _rel(x.get("rng_rel"), tf.get("rng_rel"), RNG_REL_TOL):
                continue
            if not _abs(x.get("target_ret_1m"), tf.get("target_ret_1m"), RET1_ABS_TOL):
                continue
            if not _abs(x.get("target_ret_3m"), tf.get("target_ret_3m"), RET3_ABS_TOL):
                continue
            if not _abs(x.get("target_ret_5m"), tf.get("target_ret_5m"), RET5_ABS_TOL):
                continue
            d = 0.0
            for name, w in (("target_ret_1m", 8.0), ("target_ret_3m", 4.0), ("target_ret_5m", 2.0), ("rng_rel", 1.0)):
                if _finite(x.get(name)) and _finite(tf.get(name)):
                    d += w * abs(float(x[name]) - float(tf[name]))
            d += 0.1 * bi
            if d < best_d:
                best_d = d
                best = x
        if best is not None:
            return best
    return best


def balance(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    after = {}
    for k in NUISANCE:
        xt = [float(a[k]) for a, b in pairs if _finite(a.get(k))]
        xc = [float(b[k]) for a, b in pairs if _finite(b.get(k))]
        after[k] = {"smd": _smd(xt, xc)}
    return {"after": after, "not_matched_on": "peer treatment features or future outcome"}
