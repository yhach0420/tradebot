"""D1-only tercile freeze. No D2 inspection. No threshold search."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np

from research.cross_sectional_peer_propagation_discovery_v1 import HIGH_Q, LOW_Q, PEER_MIN_N, TV_ELEVATED_PCTL

METRIC_KEYS = ("peer_ret_3m", "peer_breadth_3m", "peer_minus_target_3m", "peer_tv_breadth")


def _q(xs: list[float], p: float) -> float | None:
    if len(xs) < 100:
        return None
    return float(np.quantile(np.asarray(xs, dtype=float), float(p)))


def freeze_boundaries(d1_metrics: dict[str, list[float]]) -> dict[str, Any]:
    high = {}
    low = {}
    n = {}
    for k in METRIC_KEYS:
        xs = [float(x) for x in list(d1_metrics.get(k) or []) if x == x]
        n[k] = len(xs)
        high[k] = _q(xs, HIGH_Q)
        low[k] = _q(xs, LOW_Q)
    ok = all(high[k] is not None and low[k] is not None for k in METRIC_KEYS)
    rec = {
        "ok": ok,
        "high": high,
        "low": low,
        "n": n,
        "high_q": HIGH_Q,
        "low_q": LOW_Q,
        "peer_min_n": int(PEER_MIN_N),
        "tv_elevated_pctl": float(TV_ELEVATED_PCTL),
        "d1_only": True,
        "d2_not_read": True,
        "p_definitions": {
            "P1": "HIGH peer_breadth_3m AND HIGH peer_minus_target_3m",
            "P2": "HIGH peer_ret_3m AND HIGH peer_minus_target_3m",
            "P3": "peer_ret_3m > 0 AND HIGH peer_tv_breadth AND HIGH peer_minus_target_3m",
        },
        "matching": {
            "same_symbol": True,
            "same_dir": True,
            "nuisance": ["tod_bucket", "target_ret_1m", "target_ret_3m", "target_ret_5m", "rng_rel", "gap"],
            "not_matched_on": "peer-defining treatment features or future outcome",
        },
        "outcome": {
            "primary": "DIR-normalized target return over next 10 trading minutes after T",
            "base": "close[T]",
            "path_starts": "T+1 trading minute",
            "same_bar_range": False,
            "lunch_counted_as_trading_minutes": False,
        },
    }
    rec["FREEZE_SHA256"] = freeze_sha256(rec)
    rec["locked"] = True
    return rec


def freeze_sha256(rec: dict[str, Any]) -> str:
    payload = {k: v for k, v in rec.items() if k not in {"FREEZE_SHA256", "locked"}}
    h = hashlib.sha256()
    h.update(json.dumps(payload, sort_keys=True, default=str).encode("utf-8"))
    return h.hexdigest()
