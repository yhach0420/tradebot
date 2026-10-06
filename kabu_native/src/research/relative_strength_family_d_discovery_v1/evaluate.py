"""Family D day labels. Forward returns stay outcomes."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.relative_strength_family_d import BREADTH_CUT, MIN_SECTOR_PEERS, PRIMARY_HORIZON, RS_LOOKBACK, VOLUME_BARS, VOLUME_MULT
from research.relative_strength_family_d.evaluate import _at, _label, _relative
from research.relative_strength_family_d.rules import breadth_panels, session_slices


def _parts(minutes: np.ndarray, high: np.ndarray, close: np.ndarray, volume: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = int(minutes.size)
    technical = np.zeros(n, dtype=bool)
    participated = np.zeros(n, dtype=bool)
    volume_ratio = np.full(n, np.nan)
    break_gap = np.full(n, np.nan)
    if n < VOLUME_BARS + 1:
        return technical, participated, volume_ratio, break_gap
    one_minute = np.zeros(n, dtype=bool)
    one_minute[1:] = np.diff(minutes) == 1
    technical[1:] = one_minute[1:] & np.isfinite(close[1:]) & np.isfinite(high[:-1]) & (close[1:] > high[:-1])
    break_gap[1:] = np.where(one_minute[1:] & np.isfinite(high[:-1]) & (high[:-1] > 0) & np.isfinite(close[1:]), close[1:] / high[:-1] - 1.0, np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(volume, VOLUME_BARS)[:-1]
    finite = np.all(np.isfinite(windows), axis=1)
    med = np.median(windows, axis=1)
    ok = finite & (med > 0) & np.isfinite(volume[VOLUME_BARS:])
    volume_ratio[VOLUME_BARS:] = np.where(ok, volume[VOLUME_BARS:] / med, np.nan)
    participated[VOLUME_BARS:] = (volume[VOLUME_BARS:] > 0) & np.isfinite(volume[VOLUME_BARS:]) & finite & (volume[VOLUME_BARS:] >= VOLUME_MULT * med)
    return technical, participated, volume_ratio, break_gap


def evaluate_day(
    day: str,
    n_symbols: int,
    pieces: list[tuple[int, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]],
    peers_of: dict[int, np.ndarray],
    sector_of: dict[int, str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    counts = {
        "continuation": 0,
        "context_unavailable": 0,
        "market_off_n": 0,
        "market_off_sum": 0.0,
        "market_off_pos": 0,
        "sector_off_n": 0,
        "sector_off_sum": 0.0,
        "sector_off_pos": 0,
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
            technical, participated, volume_ratio, break_gap = _parts(sm, sh, sc, sv)
            hit = np.flatnonzero(technical & participated)
            counts["continuation"] += int(hit.size)
            for local in hit.tolist():
                minute = int(sm[int(local)])
                column = column_of.get(minute)
                prior = column_of.get(minute - RS_LOOKBACK)
                if column is None or prior is None or _at(sm, minute - RS_LOOKBACK) < 0:
                    counts["context_unavailable"] += 1
                    continue
                denom = int(eligible_n[column]) - int(eligible[int(sym_i), column])
                if denom <= 0:
                    counts["context_unavailable"] += 1
                    continue
                market = (float(up_n[column]) - float(up[int(sym_i), column])) / float(denom)
                peer_base = closes[peers, prior]
                peer_now = closes[peers, column]
                peer_ok = np.isfinite(peer_base) & np.isfinite(peer_now) & (peer_base > 0)
                breadth_ok = eligible[peers, column]
                if int(breadth_ok.sum()) < MIN_SECTOR_PEERS or int(peer_ok.sum()) < MIN_SECTOR_PEERS:
                    counts["context_unavailable"] += 1
                    continue
                sector_up = float(up[peers, column][breadth_ok].sum()) / float(breadth_ok.sum())
                stock_base = float(closes[int(sym_i), prior])
                stock_now = float(closes[int(sym_i), column])
                if not (stock_base > 0 and np.isfinite(stock_now)):
                    counts["context_unavailable"] += 1
                    continue
                strength = (stock_now / stock_base - 1.0) - float(np.median(peer_now[peer_ok] / peer_base[peer_ok] - 1.0))
                market_ok = bool(market <= BREADTH_CUT)
                sector_ok = bool(sector_up > market)
                ret3, _a, _b = _label(sm, sc, sh, sl, int(local), 3)
                ret5, mfe5, mae5 = _label(sm, sc, sh, sl, int(local), PRIMARY_HORIZON)
                ret10, _c, _d = _label(sm, sc, sh, sl, int(local), 10)
                future = column_of.get(minute + PRIMARY_HORIZON)
                if future is None or _at(sm, minute + PRIMARY_HORIZON) < 0:
                    future = -1
                admitted = market_ok and sector_ok
                if not admitted and strength > 0.0 and np.isfinite(ret5):
                    if sector_ok and not market_ok:
                        counts["market_off_n"] += 1
                        counts["market_off_sum"] += float(ret5)
                        counts["market_off_pos"] += int(ret5 > 0)
                    elif market_ok and not sector_ok:
                        counts["sector_off_n"] += 1
                        counts["sector_off_sum"] += float(ret5)
                        counts["sector_off_pos"] += int(ret5 > 0)
                if not admitted:
                    continue
                events.append(
                    {
                        "date": day,
                        "symbol": int(sym_i),
                        "sector": sector,
                        "population": "FULL" if admitted and strength > 0.0 else "CONTROL" if admitted else "CONTEXT",
                        "market_ok": market_ok,
                        "sector_ok": sector_ok,
                        "market_breadth": market,
                        "sector_gap": float(sector_up - market),
                        "relative_strength": float(strength),
                        "volume_ratio": float(volume_ratio[int(local)]),
                        "break_gap": float(break_gap[int(local)]),
                        "ret3": ret3,
                        "ret5": ret5,
                        "ret10": ret10,
                        "rel5": _relative(closes, int(sym_i), int(column), int(future), peers, ret5),
                        "mfe5": mfe5,
                        "mae5": mae5,
                    }
                )
    return events, counts
