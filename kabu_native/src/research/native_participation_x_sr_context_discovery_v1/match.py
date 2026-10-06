"""Same-symbol matched away-from-S/R native events. Pre-event covariates only. No event-bar TV/range match."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.native_participation_x_sr_context_discovery_v1 import MOM1_TOL, MOM3_TOL, MOM5_TOL, RNG_REL_TOL


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _close(a: Any, b: Any, tol: float) -> bool:
    if not _finite(a) or not _finite(b):
        return False
    return abs(float(a) - float(b)) <= float(tol)


def _rel(a: Any, b: Any, tol: float) -> bool:
    if not _finite(a) and not _finite(b):
        return True
    if not _finite(a) or not _finite(b):
        return False
    return abs(float(a) - float(b)) / max(abs(float(b)), 1e-9) <= float(tol)


def index_controls(controls: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for x in controls:
        by[(str(x.get("symbol") or ""), str(x.get("direction") or ""))].append(x)
    return by


def find_same_symbol(tf: dict[str, Any], pool: list[dict[str, Any]], *, allow_same_date: bool = False) -> dict[str, Any] | None:
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
            if str(x.get("signal_id") or "") == str(tf.get("signal_id") or ""):
                continue
            if (not allow_same_date) and str(x.get("date") or "") == str(tf.get("date") or ""):
                continue
            if str(x.get("date") or "") == str(tf.get("date") or "") and int(x.get("i") or -1) == int(tf.get("i") or -2):
                continue
            if str(x.get("bucket") or "") != bucket:
                continue
            if x.get("gap") != tf.get("gap"):
                continue
            if x.get("mkt_sign") != tf.get("mkt_sign"):
                continue
            if x.get("sec_sign") != tf.get("sec_sign"):
                continue
            if not _close(x.get("r5"), tf.get("r5"), MOM5_TOL):
                continue
            if not _close(x.get("r3"), tf.get("r3"), MOM3_TOL):
                continue
            if not _close(x.get("r1"), tf.get("r1"), MOM1_TOL):
                continue
            if not _rel(x.get("rng_rel"), tf.get("rng_rel"), RNG_REL_TOL):
                continue
            d = abs(float(x["r5"]) - float(tf["r5"])) + 0.5 * abs(float(x["r3"]) - float(tf["r3"])) + 0.1 * bi
            if d < best_d:
                best_d = d
                best = x
        if best is not None:
            return best
    return best


def find_cross_symbol(tf: dict[str, Any], others: list[dict[str, Any]]) -> dict[str, Any] | None:
    same_day = [x for x in others if str(x.get("date") or "") == str(tf.get("date") or "") and str(x.get("direction") or "") == str(tf.get("direction") or "")]
    return find_same_symbol(tf, same_day, allow_same_date=True)
