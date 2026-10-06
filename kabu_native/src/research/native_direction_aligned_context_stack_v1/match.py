"""Same-symbol same-DIR matched controls. Nuisance only. No rule features. No outcome."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.native_direction_aligned_context_stack_v1 import RNG_REL_TOL

NUISANCE = ("tod_min", "rng_rel", "gap_num")


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


def index_fail(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str, str], list[dict[str, Any]]]:
    by: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[(str(r.get("symbol") or ""), str(r.get("direction") or ""), str(r.get("bucket") or ""), str(r.get("gap") or ""))].append(r)
    return by


def find_control(
    tf: dict[str, Any],
    by: dict[tuple[str, str, str, str], list[dict[str, Any]]],
    *,
    match_market: bool,
    match_sector: bool = False,
) -> dict[str, Any] | None:
    buckets = [str(tf.get("bucket") or "")]
    try:
        b0 = int(buckets[0])
        buckets.extend([f"{b0 - 30:04d}", f"{b0 + 30:04d}"])
    except ValueError:
        pass
    best = None
    best_d = 1e9
    key0 = (str(tf.get("symbol") or ""), str(tf.get("direction") or ""), "", str(tf.get("gap") or ""))
    for bi, bucket in enumerate(buckets):
        pool = by.get((key0[0], key0[1], bucket, key0[3]), ())
        for x in pool:
            if str(x.get("date") or "") == str(tf.get("date") or "") and int(x.get("i") or -1) == int(tf.get("i") or -2):
                continue
            if match_market and x.get("mkt_sign") != tf.get("mkt_sign"):
                continue
            if match_sector and x.get("sec_sign") != tf.get("sec_sign"):
                continue
            if not _rel(x.get("rng_rel"), tf.get("rng_rel"), RNG_REL_TOL):
                continue
            d = 0.0
            if _finite(x.get("rng_rel")) and _finite(tf.get("rng_rel")):
                d += abs(float(x["rng_rel"]) - float(tf["rng_rel"]))
            d += 0.1 * bi
            if d < best_d:
                best_d = d
                best = x
        if best is not None:
            return best
    return best


def balance(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    after = {}
    keys = list(NUISANCE) + ["aligned_market_rel"]
    for k in keys:
        xt = [float(a[k]) for a, b in pairs if _finite(a.get(k))]
        xc = [float(b[k]) for a, b in pairs if _finite(b.get(k))]
        after[k] = {"smd": _smd(xt, xc)}
    return {"after": after, "not_matched_on": "rule-defining features or future outcome"}
