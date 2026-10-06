"""Matched fail-rule controls. Same symbol, time of day, pre-event vol, gap. No future outcomes."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

RNG_REL_TOL = 0.50


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


def find_fail_rule(tf: dict[str, Any], pool: list[dict[str, Any]]) -> dict[str, Any] | None:
    buckets = [str(tf.get("bucket") or "")]
    try:
        b0 = int(buckets[0])
        buckets.extend([f"{b0 - 30:04d}", f"{b0 + 30:04d}"])
    except ValueError:
        pass
    best = None
    best_d = 1e9
    for bi, bucket in enumerate(buckets):
        for x in pool:
            if str(x.get("symbol") or "") != str(tf.get("symbol") or ""):
                continue
            if str(x.get("date") or "") == str(tf.get("date") or "") and int(x.get("i") or -1) == int(tf.get("i") or -2):
                continue
            if str(x.get("bucket") or "") != bucket:
                continue
            if x.get("gap") != tf.get("gap"):
                continue
            if not _rel(x.get("rng_rel"), tf.get("rng_rel"), RNG_REL_TOL):
                continue
            d = abs(float(x.get("rng_rel") or 0) - float(tf.get("rng_rel") or 0)) + 0.1 * bi
            if d < best_d:
                best_d = d
                best = x
        if best is not None:
            return best
    return best


def index_by_symbol(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[str(r.get("symbol") or "")].append(r)
    return by
