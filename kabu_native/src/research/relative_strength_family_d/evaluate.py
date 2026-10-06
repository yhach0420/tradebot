"""One Family D day. Forward returns are labels and never become features."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.relative_strength_family_d import BREADTH_CUT, MIN_SECTOR_PEERS, PRIMARY_HORIZON, RS_LOOKBACK
from research.relative_strength_family_d.rules import breadth_panels, continuation_mask, session_slices


def _at(minutes: np.ndarray, target: int) -> int:
    j = int(np.searchsorted(minutes, int(target)))
    if j < int(minutes.size) and int(minutes[j]) == int(target):
        return j
    return -1


def _label(minutes: np.ndarray, close: np.ndarray, high: np.ndarray, low: np.ndarray, i: int, horizon: int) -> tuple[float, float, float]:
    minute = int(minutes[i])
    j = _at(minutes, minute + int(horizon))
    if j < 0:
        return (np.nan, np.nan, np.nan)
    base = float(close[i])
    future = float(close[j])
    if not (base > 0 and np.isfinite(base) and np.isfinite(future)):
        return (np.nan, np.nan, np.nan)
    ret = (future / base - 1.0) * 10000.0
    if j != i + int(horizon):
        return (ret, np.nan, np.nan)
    window_h = high[i + 1 : j + 1]
    window_l = low[i + 1 : j + 1]
    if window_h.size != int(horizon) or not (np.all(np.isfinite(window_h)) and np.all(np.isfinite(window_l))):
        return (ret, np.nan, np.nan)
    return (ret, float(np.max(window_h)) / base * 10000.0 - 10000.0, float(np.min(window_l)) / base * 10000.0 - 10000.0)


def _relative(closes: np.ndarray, symbol: int, now: int, future: int, peers: np.ndarray, stock_bps: float) -> float:
    if not np.isfinite(stock_bps) or future < 0:
        return np.nan
    base = closes[peers, now]
    nxt = closes[peers, future]
    ok = np.isfinite(base) & np.isfinite(nxt) & (base > 0)
    if int(ok.sum()) < MIN_SECTOR_PEERS:
        return np.nan
    median = float(np.median(nxt[ok] / base[ok] - 1.0))
    return float(stock_bps - median * 10000.0)


def mechanism_self_check() -> dict[str, bool]:
    n = 11
    minutes = np.arange(n, dtype=int)

    def _bars(close: np.ndarray, last_volume: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        high = close.copy()
        low = close.copy()
        volume = np.full(n, 10.0, dtype=float)
        volume[-1] = last_volume
        return high, low, close, volume

    flat = np.full(n, 100.0, dtype=float)
    target = flat.copy()
    target[-1] = 110.0
    peer_up = flat.copy()
    peer_up[-1] = 101.0
    pieces = [
        (0, minutes, *_bars(target, 15.0)),
        (1, minutes, *_bars(peer_up, 10.0)),
        (2, minutes, *_bars(flat, 10.0)),
        (3, minutes, *_bars(flat, 10.0)),
        (4, minutes, *_bars(flat, 10.0)),
        (5, minutes, *_bars(flat, 10.0)),
    ]
    peers = {0: np.array([1, 2, 3], dtype=np.int32)}
    sector = {0: "A", 1: "A", 2: "A", 3: "A", 4: "B", 5: "B"}
    full_events, _counts = evaluate_day("D", 6, pieces, peers, sector)
    lagged = flat.copy()
    lagged[5] = 110.0
    lagged[-1] = 101.0
    strong = flat.copy()
    strong[-1] = 106.0
    control_pieces = [
        (0, minutes, *_bars(lagged, 15.0)),
        (1, minutes, *_bars(strong, 10.0)),
        (2, minutes, *_bars(flat, 10.0)),
        (3, minutes, *_bars(flat, 10.0)),
        (4, minutes, *_bars(flat, 10.0)),
        (5, minutes, *_bars(flat, 10.0)),
    ]
    control_events, _counts = evaluate_day("C", 6, control_pieces, peers, sector)
    return {
        "full_only_when_stronger_than_sector": len(full_events) == 1 and full_events[0]["population"] == "FULL",
        "control_when_relative_strength_is_not_positive": len(control_events) == 1 and control_events[0]["population"] == "CONTROL",
        "weak_market_and_stricter_sector": bool(full_events) and float(full_events[0]["market_breadth"]) <= 0.50 and float(full_events[0]["sector_breadth"]) > float(full_events[0]["market_breadth"]),
    }


def evaluate_day(
    day: str,
    n_symbols: int,
    pieces: list[tuple[int, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]],
    peers_of: dict[int, np.ndarray],
    sector_of: dict[int, str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    counts = {
        "continuation": 0,
        "market_unavailable": 0,
        "not_weak_neutral": 0,
        "sector_unavailable": 0,
        "sector_not_resilient": 0,
        "relative_strength_unavailable": 0,
    }
    if not pieces:
        return [], counts
    all_minutes = np.unique(np.concatenate([row[1] for row in pieces]))
    column_of = {int(minute): j for j, minute in enumerate(all_minutes.tolist())}
    closes = np.full((n_symbols, int(all_minutes.size)), np.nan)
    for sym_i, minutes, _high, _low, close, _volume in pieces:
        closes[int(sym_i), [column_of[int(m)] for m in minutes.tolist()]] = close
    eligible, up = breadth_panels(closes, all_minutes)
    eligible_n = eligible.sum(axis=0)
    up_n = up.sum(axis=0)
    events: list[dict[str, Any]] = []
    for sym_i, minutes, high, low, close, volume in pieces:
        peers = peers_of.get(int(sym_i))
        sector = sector_of.get(int(sym_i))
        if peers is None or sector is None:
            continue
        for lo, hi in session_slices(minutes):
            sm = minutes[lo:hi]
            sh = high[lo:hi]
            sl = low[lo:hi]
            sc = close[lo:hi]
            sv = volume[lo:hi]
            hit = np.flatnonzero(continuation_mask(sm, sh, sc, sv))
            counts["continuation"] += int(hit.size)
            for local in hit.tolist():
                minute = int(sm[int(local)])
                column = column_of.get(minute)
                prior_minute = minute - RS_LOOKBACK
                prior = column_of.get(prior_minute)
                if column is None or prior is None or _at(sm, prior_minute) < 0:
                    counts["relative_strength_unavailable"] += 1
                    continue
                denom = int(eligible_n[column]) - int(eligible[int(sym_i), column])
                if denom <= 0:
                    counts["market_unavailable"] += 1
                    continue
                market = (float(up_n[column]) - float(up[int(sym_i), column])) / float(denom)
                if market > BREADTH_CUT:
                    counts["not_weak_neutral"] += 1
                    continue
                peer_base = closes[peers, prior]
                peer_now = closes[peers, column]
                peer_ok = np.isfinite(peer_base) & np.isfinite(peer_now) & (peer_base > 0)
                breadth_ok = eligible[peers, column]
                breadth_denom = int(breadth_ok.sum())
                if breadth_denom < MIN_SECTOR_PEERS:
                    counts["sector_unavailable"] += 1
                    continue
                sector_up = float(up[peers, column][breadth_ok].sum()) / float(breadth_denom)
                if not (sector_up > market):
                    counts["sector_not_resilient"] += 1
                    continue
                stock_base = float(closes[int(sym_i), prior])
                stock_now = float(closes[int(sym_i), column])
                if int(peer_ok.sum()) < MIN_SECTOR_PEERS or not (stock_base > 0 and np.isfinite(stock_now)):
                    counts["relative_strength_unavailable"] += 1
                    continue
                strength = (stock_now / stock_base - 1.0) - float(np.median(peer_now[peer_ok] / peer_base[peer_ok] - 1.0))
                ret3, _mfe3, _mae3 = _label(sm, sc, sh, sl, int(local), 3)
                ret5, mfe5, mae5 = _label(sm, sc, sh, sl, int(local), PRIMARY_HORIZON)
                ret10, _mfe10, _mae10 = _label(sm, sc, sh, sl, int(local), 10)
                future = column_of.get(minute + PRIMARY_HORIZON)
                if future is None or _at(sm, minute + PRIMARY_HORIZON) < 0:
                    future = -1
                events.append(
                    {
                        "date": day,
                        "symbol": int(sym_i),
                        "sector": sector,
                        "minute": minute,
                        "local": int(lo + int(local)),
                        "population": "FULL" if strength > 0.0 else "CONTROL",
                        "market_breadth": market,
                        "sector_breadth": sector_up,
                        "relative_strength": float(strength),
                        "ret3": ret3,
                        "ret5": ret5,
                        "ret10": ret10,
                        "rel5": _relative(closes, int(sym_i), int(column), int(future), peers, ret5),
                        "mfe5": mfe5,
                        "mae5": mae5,
                    }
                )
    return events, counts
