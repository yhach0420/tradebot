"""Causal trading-minute peer and target features. Target excluded from every peer metric."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.cross_sectional_peer_propagation_discovery_v1 import (
    HIGH_Q,
    LEAD_ALREADY,
    LEAD_SIM,
    LEAD_TRUE,
    PEER_MIN_N,
    TV_ELEVATED_PCTL,
)

FEATURE_DEFINITIONS = {
    "DIR": "+1 bullish candidate, -1 bearish candidate",
    "PEER_RET_1M": "leave-one-out cross-sectional median of DIR-aligned 1 trading-minute peer returns; target excluded",
    "PEER_RET_3M": "leave-one-out cross-sectional median of DIR-aligned 3 trading-minute peer returns; target excluded",
    "PEER_BREADTH_1M": "fraction of peers with DIR-aligned 1m return > 0; target excluded",
    "PEER_BREADTH_3M": "fraction of peers with DIR-aligned 3m return > 0; target excluded",
    "PEER_TV_BREADTH": "fraction of peers with causal TV_CLOCK_PCTL > 0.50 (predeclared elevated; not searched)",
    "TARGET_RET_1M": "DIR * target 1 trading-minute return through completed T",
    "TARGET_RET_3M": "DIR * target 3 trading-minute return through completed T",
    "TARGET_RET_5M": "DIR * target 5 trading-minute return through completed T",
    "PEER_MINUS_TARGET_1M": "PEER_RET_1M - TARGET_RET_1M; positive = peers already moved more in DIR",
    "PEER_MINUS_TARGET_3M": "PEER_RET_3M - TARGET_RET_3M",
    "A": "peer directional strength = PEER_RET_3M",
    "B": "peer breadth = PEER_BREADTH_3M",
    "C": "target lag = PEER_MINUS_TARGET_3M",
    "P1": "BROAD_PEER_LEAD: HIGH B AND HIGH C",
    "P2": "STRONG_PEER_MEDIAN_LEAD: HIGH A AND HIGH C",
    "P3": "PEER_PARTICIPATION_LEAD: PEER_RET_3M > 0 AND HIGH PEER_TV_BREADTH AND HIGH C",
    "TRUE_PEER_LEAD": "PEER_RET_1M > 0 AND TARGET_RET_1M <= 0",
    "SIMULTANEOUS_MOVE": "PEER_RET_1M > 0 AND TARGET_RET_1M > 0",
    "TARGET_ALREADY_LED": "TARGET_RET_3M > 0 AND TARGET_RET_3M >= PEER_RET_3M",
    "HIGH": ">= D1-only 2/3 quantile of that metric among valid D1 (symbol, T, DIR) minutes",
}


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def sess_ret(close: np.ndarray, session_idx: list[int], pos: int, n: int) -> float:
    if pos < n:
        return float("nan")
    i = session_idx[pos]
    j = session_idx[pos - n]
    a, b = close[i], close[j]
    if not np.isfinite(a) or not np.isfinite(b) or float(b) == 0:
        return float("nan")
    return float(a / b - 1.0)


def leave_one_out_median(vals: np.ndarray) -> np.ndarray:
    n = int(vals.size)
    out = np.full(n, np.nan, dtype=float)
    if n < 2:
        return out
    for i in range(n):
        rest = np.concatenate([vals[:i], vals[i + 1 :]])
        rest = rest[np.isfinite(rest)]
        if rest.size:
            out[i] = float(np.median(rest))
    return out


def leave_one_out_frac(vals: np.ndarray, pred) -> np.ndarray:
    n = int(vals.size)
    out = np.full(n, np.nan, dtype=float)
    if n < 2:
        return out
    hits = pred(vals).astype(float)
    hits[~np.isfinite(vals)] = 0.0
    tot = float(np.isfinite(vals).sum())
    s = float(hits.sum())
    for i in range(n):
        den = tot - float(np.isfinite(vals[i]))
        if den < 1:
            continue
        out[i] = (s - hits[i]) / den
    return out


def peer_stats(aligned: np.ndarray, tv_pctl: np.ndarray, *, include_mask: np.ndarray) -> dict[str, np.ndarray]:
    n = int(aligned.size)
    med = np.full(n, np.nan)
    br = np.full(n, np.nan)
    tvb = np.full(n, np.nan)
    pn = np.zeros(n, dtype=int)
    leak = np.zeros(n, dtype=int)
    idx = np.flatnonzero(include_mask)
    if idx.size < int(PEER_MIN_N) + 1:
        return {"median": med, "breadth": br, "tv_breadth": tvb, "peer_n": pn, "target_in_peer_n": leak}
    sub_a = aligned[idx]
    sub_tv = tv_pctl[idx]
    loo_med = leave_one_out_median(sub_a)
    loo_br = leave_one_out_frac(sub_a, lambda v: v > 0)
    elev = np.where(np.isfinite(sub_tv) & (sub_tv > float(TV_ELEVATED_PCTL)), 1.0, 0.0)
    loo_tv = leave_one_out_frac(elev, lambda v: v > 0.5)
    n_peer = int(idx.size) - 1
    for k, i in enumerate(idx):
        med[i] = loo_med[k]
        br[i] = loo_br[k]
        tvb[i] = loo_tv[k]
        pn[i] = n_peer
        leak[i] = 0
    return {"median": med, "breadth": br, "tv_breadth": tvb, "peer_n": pn, "target_in_peer_n": leak}


def lead_class(peer_ret_1m: float, target_ret_1m: float, peer_ret_3m: float, target_ret_3m: float) -> str:
    if _finite(peer_ret_1m) and _finite(target_ret_1m) and float(peer_ret_1m) > 0 and float(target_ret_1m) <= 0:
        return LEAD_TRUE
    if _finite(peer_ret_1m) and _finite(target_ret_1m) and float(peer_ret_1m) > 0 and float(target_ret_1m) > 0:
        return LEAD_SIM
    if _finite(target_ret_3m) and _finite(peer_ret_3m) and float(target_ret_3m) > 0 and float(target_ret_3m) >= float(peer_ret_3m):
        return LEAD_ALREADY
    return "OTHER"


def p_flags(row: dict[str, Any], freeze: dict[str, Any]) -> dict[str, bool]:
    q = dict(freeze.get("high") or {})
    a = row.get("peer_ret_3m")
    b = row.get("peer_breadth_3m")
    c = row.get("peer_minus_target_3m")
    tv = row.get("peer_tv_breadth")
    ok = int(row.get("peer_n") or 0) >= int(PEER_MIN_N)
    high_a = ok and _finite(a) and float(a) >= float(q["peer_ret_3m"])
    high_b = ok and _finite(b) and float(b) >= float(q["peer_breadth_3m"])
    high_c = ok and _finite(c) and float(c) >= float(q["peer_minus_target_3m"])
    high_tv = ok and _finite(tv) and float(tv) >= float(q["peer_tv_breadth"])
    return {
        "P1": bool(high_b and high_c),
        "P2": bool(high_a and high_c),
        "P3": bool(ok and _finite(a) and float(a) > 0 and high_tv and high_c),
        "high_a": high_a,
        "high_b": high_b,
        "high_c": high_c,
        "high_tv": high_tv,
    }


def state_label(value: Any, q_lo: float, q_hi: float) -> str:
    if not _finite(value):
        return "UNAVAILABLE"
    v = float(value)
    if v >= q_hi:
        return "HIGH"
    if v <= q_lo:
        return "LOW"
    return "MID"


_ = HIGH_Q
