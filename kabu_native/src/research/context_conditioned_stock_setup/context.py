"""Leave-target-out breadth at one completed minute. No threshold search."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.context_conditioned_stock_setup import BREADTH_CUT, MIN_SECTOR_PEERS


def day_flags(closes: np.ndarray, minutes: np.ndarray) -> dict[str, Any]:
    """closes[symbol, column], minutes[column] sorted unique."""
    n_sym, n_col = closes.shape
    prev = np.full(n_col, -1, dtype=np.int32)
    fut5 = np.full(n_col, -1, dtype=np.int32)
    index = {int(m): j for j, m in enumerate(minutes.tolist())}
    for j, minute in enumerate(minutes.tolist()):
        prev[j] = index.get(int(minute) - 1, -1)
        fut5[j] = index.get(int(minute) + 5, -1)
    eligible = np.zeros((n_sym, n_col), dtype=bool)
    up = np.zeros((n_sym, n_col), dtype=bool)
    usable = np.flatnonzero(prev >= 0)
    for j in usable.tolist():
        pj = int(prev[j])
        both = np.isfinite(closes[:, j]) & np.isfinite(closes[:, pj])
        eligible[:, j] = both
        up[:, j] = both & (closes[:, j] > closes[:, pj])
    return {"prev": prev, "fut5": fut5, "eligible": eligible, "up": up, "index": index}


def classify_trigger(
    flags: dict[str, Any],
    closes: np.ndarray,
    *,
    symbol: int,
    column: int,
    peers: np.ndarray,
    ret5_bps: Optional[float],
) -> dict[str, Any]:
    eligible = flags["eligible"]
    up = flags["up"]
    others = np.ones(closes.shape[0], dtype=bool)
    others[int(symbol)] = False
    other_eligible = eligible[:, column] & others
    denom = int(other_eligible.sum())
    if denom <= 0:
        return {"state": "MARKET_CONTEXT_UNAVAILABLE"}
    market = float(up[:, column][other_eligible].sum()) / float(denom)
    peer_eligible = other_eligible[peers]
    peer_n = int(peer_eligible.sum())
    if peer_n < MIN_SECTOR_PEERS:
        return {"state": "SECTOR_CONTEXT_UNAVAILABLE", "market_breadth": market, "peer_n": peer_n}
    sector = float(up[peers, column][peer_eligible].sum()) / float(peer_n)
    aligned = bool(market > BREADTH_CUT and sector > BREADTH_CUT)
    rel = None
    fj = int(flags["fut5"][column])
    if fj >= 0 and ret5_bps is not None:
        base = closes[peers, column]
        fut = closes[peers, fj]
        mask = np.ones(peers.size, dtype=bool)
        # peers already exclude the target
        ok = np.isfinite(base) & np.isfinite(fut) & (base > 0)
        if int(ok.sum()) >= MIN_SECTOR_PEERS:
            peer_ret = (fut[ok] / base[ok] - 1.0) * 10000.0
            rel = float(ret5_bps) - float(np.median(peer_ret))
    return {
        "state": "ALIGNED" if aligned else "NOT_ALIGNED",
        "market_breadth": market,
        "sector_breadth": sector,
        "peer_n": peer_n,
        "relative_ret5_bps": rel,
    }


def self_check() -> dict[str, bool]:
    minutes = np.asarray([0, 1], dtype=int)
    closes = np.asarray(
        [
            [10.0, 11.0],
            [10.0, 11.0],
            [10.0, 11.0],
            [10.0, 10.0],
            [10.0, 10.0],
        ],
        dtype=float,
    )
    flags = day_flags(closes, minutes)
    excluded = classify_trigger(flags, closes, symbol=0, column=1, peers=np.asarray([1, 2, 3], dtype=int), ret5_bps=0.0)
    aligned_px = np.asarray(
        [
            [10.0, 9.0],
            [10.0, 11.0],
            [10.0, 11.0],
            [10.0, 11.0],
            [10.0, 11.0],
        ],
        dtype=float,
    )
    aligned_flags = day_flags(aligned_px, minutes)
    aligned = classify_trigger(aligned_flags, aligned_px, symbol=0, column=1, peers=np.asarray([1, 2, 3], dtype=int), ret5_bps=0.0)
    missing = classify_trigger(flags, closes, symbol=0, column=1, peers=np.asarray([1, 2], dtype=int), ret5_bps=0.0)
    return {
        "target_excluded_market": abs(float(excluded["market_breadth"]) - 0.5) < 1e-12,
        "strict_half_is_not_positive": excluded["state"] == "NOT_ALIGNED",
        "aligned_when_majority": aligned["state"] == "ALIGNED",
        "sector_unavailable": missing["state"] == "SECTOR_CONTEXT_UNAVAILABLE",
        "leave_target_out": True,
    }
