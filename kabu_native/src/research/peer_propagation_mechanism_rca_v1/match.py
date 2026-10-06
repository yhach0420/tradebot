"""Reuse frozen matching. Optional market-state restriction uses existing RET3_ABS_TOL."""
from __future__ import annotations

from typing import Any

from research.cross_sectional_peer_propagation_discovery_v1 import RET1_ABS_TOL, RET3_ABS_TOL, RET5_ABS_TOL, RNG_REL_TOL
from research.cross_sectional_peer_propagation_discovery_v1.match import _abs, _finite, _rel, balance, find_control, key_of


def find_control_market(tf: dict[str, Any], by: dict) -> dict[str, Any] | None:
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
            if not _abs(x.get("mkt_ret_3m"), tf.get("mkt_ret_3m"), RET3_ABS_TOL):
                continue
            d = 0.0
            for name, w in (("target_ret_1m", 8.0), ("target_ret_3m", 4.0), ("target_ret_5m", 2.0), ("rng_rel", 1.0), ("mkt_ret_3m", 4.0)):
                if _finite(x.get(name)) and _finite(tf.get(name)):
                    d += w * abs(float(x[name]) - float(tf[name]))
            d += 0.1 * bi
            if d < best_d:
                best_d = d
                best = x
        if best is not None:
            return best
    return best


_ = (find_control, balance, key_of)
