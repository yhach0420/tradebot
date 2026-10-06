"""Match fail-rule events on nuisance covariates only. No r15 / prior5 / outcomes."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.r14_trend_incrementality_rca_v1 import RNG_REL_TOL

NUISANCE = ("tod_min", "rng_rel", "gap_num", "mkt_rel", "sec_rel")


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


def index_fail(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    by: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[(str(r.get("symbol") or ""), str(r.get("bucket") or ""), str(r.get("gap") or ""))].append(r)
    return by


def find_control(tf: dict[str, Any], by: dict[tuple[str, str, str], list[dict[str, Any]]]) -> dict[str, Any] | None:
    buckets = [str(tf.get("bucket") or "")]
    try:
        b0 = int(buckets[0])
        buckets.extend([f"{b0 - 30:04d}", f"{b0 + 30:04d}"])
    except ValueError:
        pass
    best = None
    best_d = 1e9
    sym = str(tf.get("symbol") or "")
    gap = str(tf.get("gap") or "")
    for bi, bucket in enumerate(buckets):
        pool = by.get((sym, bucket, gap), ())
        for x in pool:
            if str(x.get("date") or "") == str(tf.get("date") or "") and int(x.get("i") or -1) == int(tf.get("i") or -2):
                continue
            if x.get("mkt_sign") != tf.get("mkt_sign"):
                continue
            if x.get("sec_sign") != tf.get("sec_sign"):
                continue
            if not _rel(x.get("rng_rel"), tf.get("rng_rel"), RNG_REL_TOL):
                continue
            d = 0.0
            if _finite(x.get("rng_rel")) and _finite(tf.get("rng_rel")):
                d += abs(float(x["rng_rel"]) - float(tf["rng_rel"]))
            if _finite(x.get("mkt_rel")) and _finite(tf.get("mkt_rel")):
                d += abs(float(x["mkt_rel"]) - float(tf["mkt_rel"]))
            if _finite(x.get("sec_rel")) and _finite(tf.get("sec_rel")):
                d += abs(float(x["sec_rel"]) - float(tf["sec_rel"]))
            d += 0.1 * bi
            if d < best_d:
                best_d = d
                best = x
        if best is not None:
            return best
    return best


def balance(treated: list[dict[str, Any]], matched_pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    before = {}
    after = {}
    ctrl_all = [c for _, c in matched_pairs]
    for k in NUISANCE:
        xt = [float(r[k]) for r in treated if _finite(r.get(k))]
        xc_b = [float(r[k]) for r in ctrl_all if _finite(r.get(k))] if ctrl_all else []
        before[k] = {
            "treated_mean": float(sum(xt) / len(xt)) if xt else None,
            "smd_vs_matched_pool": _smd(xt, xc_b) if matched_pairs else None,
        }
        xt_a = [float(a[k]) for a, b in matched_pairs if _finite(a.get(k))]
        xc_a = [float(b[k]) for a, b in matched_pairs if _finite(b.get(k))]
        after[k] = {"smd": _smd(xt_a, xc_a), "treated_mean": float(sum(xt_a) / len(xt_a)) if xt_a else None, "control_mean": float(sum(xc_a) / len(xc_a)) if xc_a else None}
    return {"before": before, "after": after, "covariates": list(NUISANCE), "not_matched_on": ["r15", "prior5_ret", "prior5_range_rel", "future_outcome"]}
