"""Frozen Family D predicates. Outcomes are not inputs."""
from __future__ import annotations

from typing import Optional

import numpy as np

from research.relative_strength_family_d import BREADTH_CUT, MIN_SECTOR_PEERS, RS_LOOKBACK, VOLUME_BARS, VOLUME_MULT


def breadth_panels(closes: np.ndarray, minutes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Leave-target-out inputs. A name is eligible only when both this bar and the prior minute exist."""
    n, m = closes.shape
    eligible = np.zeros((n, m), dtype=bool)
    up = np.zeros((n, m), dtype=bool)
    column = {int(minute): j for j, minute in enumerate(minutes.tolist())}
    for j, minute in enumerate(minutes.tolist()):
        prev = column.get(int(minute) - 1)
        if prev is None:
            continue
        now = closes[:, j]
        before = closes[:, int(prev)]
        ok = np.isfinite(now) & np.isfinite(before)
        eligible[:, j] = ok
        up[:, j] = ok & (now > before)
    return eligible, up


def session_slices(minutes: np.ndarray) -> list[tuple[int, int]]:
    n = int(minutes.size)
    if n == 0:
        return []
    cuts = [0]
    for i in range(1, n):
        if int(minutes[i]) - int(minutes[i - 1]) > 30:
            cuts.append(i)
    cuts.append(n)
    return [(cuts[k], cuts[k + 1]) for k in range(len(cuts) - 1)]


def continuation_mask(minutes: np.ndarray, high: np.ndarray, close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    """Prior-bar high break and the fixed 1.5x volume primitive. Both are strict on their own rules."""
    n = int(minutes.size)
    out = np.zeros(n, dtype=bool)
    if n < VOLUME_BARS + 1:
        return out
    one_minute = np.diff(minutes) == 1
    technical = np.zeros(n, dtype=bool)
    technical[1:] = one_minute & np.isfinite(close[1:]) & np.isfinite(high[:-1]) & (close[1:] > high[:-1])
    windows = np.lib.stride_tricks.sliding_window_view(volume, VOLUME_BARS)[:-1]
    finite = np.all(np.isfinite(windows), axis=1)
    med = np.median(windows, axis=1)
    participated = np.zeros(n, dtype=bool)
    participated[VOLUME_BARS:] = (volume[VOLUME_BARS:] > 0) & np.isfinite(volume[VOLUME_BARS:]) & finite & (volume[VOLUME_BARS:] >= VOLUME_MULT * med)
    out = technical & participated
    return out


def market_breadth(up: np.ndarray, eligible: np.ndarray, symbol: int, column: int) -> Optional[float]:
    others = eligible[:, column].copy()
    others[int(symbol)] = False
    denom = int(others.sum())
    if denom <= 0:
        return None
    return float(up[:, column][others].sum()) / float(denom)


def sector_breadth(up: np.ndarray, eligible: np.ndarray, symbol: int, column: int, peers: np.ndarray) -> Optional[float]:
    if int(peers.size) == 0:
        return None
    peer_ok = eligible[peers, column]
    # peers already exclude the target; keep the mask explicit
    peer_ok = peer_ok & (peers != int(symbol))
    denom = int(peer_ok.sum())
    if denom < MIN_SECTOR_PEERS:
        return None
    return float(up[peers, column][peer_ok].sum()) / float(denom)


def relative_strength(closes: np.ndarray, symbol: int, column: int, prior: int, peers: np.ndarray) -> Optional[float]:
    if prior < 0:
        return None
    base = float(closes[int(symbol), prior])
    now = float(closes[int(symbol), column])
    if not (base == base and now == now and base > 0):
        return None
    stock = now / base - 1.0
    if int(peers.size) == 0:
        return None
    peer_base = closes[peers, prior]
    peer_now = closes[peers, column]
    ok = (peers != int(symbol)) & np.isfinite(peer_base) & np.isfinite(peer_now) & (peer_base > 0)
    if int(ok.sum()) < MIN_SECTOR_PEERS:
        return None
    return float(stock - np.median(peer_now[ok] / peer_base[ok] - 1.0))


def self_check() -> dict[str, bool]:
    minutes = np.arange(0, 8, dtype=int)
    high = np.array([10, 11, 11, 11, 11, 11, 11, 12], dtype=float)
    close = np.array([10, 10, 10, 10, 10, 10, 10, 13], dtype=float)
    volume = np.array([10, 10, 10, 10, 10, 10, 10, 15], dtype=float)
    mask = continuation_mask(minutes, high, close, volume)
    below = volume.copy()
    below[-1] = 14.9
    tied = high.copy()
    tied[-2] = 13.0
    tied_close = close.copy()
    tied_close[-1] = 13.0
    up = np.array([[False], [True], [False], [False], [False], [False]])
    eligible = np.ones((6, 1), dtype=bool)
    weak = market_breadth(up, eligible, 0, 0)
    peers = np.array([1, 2, 3], dtype=int)
    sector = sector_breadth(up, eligible, 0, 0, peers)
    closes = np.array(
        [
            [10.0, 12.0],
            [10.0, 11.0],
            [10.0, 10.0],
            [10.0, 11.0],
        ]
    )
    rs = relative_strength(closes, 0, 1, 0, np.array([1, 2, 3], dtype=int))
    return {
        "exact_one_minute_break": bool(mask[-1]) and not bool(mask[-2]),
        "volume_equality_passes": bool(mask[-1]),
        "volume_below_multiple_fails": not bool(continuation_mask(minutes, high, close, below)[-1]),
        "equal_high_fails": not bool(continuation_mask(minutes, tied, tied_close, volume)[-1]),
        "half_breadth_is_weak_neutral": weak is not None and weak <= BREADTH_CUT and abs(weak - 0.2) < 1e-12,
        "sector_can_lead_without_majority": sector is not None and weak is not None and sector > weak and sector < BREADTH_CUT,
        "relative_strength_sign": rs is not None and rs > 0,
        "lookback_is_five": RS_LOOKBACK == 5 and VOLUME_BARS == 5 and abs(VOLUME_MULT - 1.5) < 1e-12,
    }
