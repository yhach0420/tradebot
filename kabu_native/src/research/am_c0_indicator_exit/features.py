"""Canonical indicator arrays at event time. No holding-time features. No future."""
from __future__ import annotations

from collections import deque
from typing import Any, Optional

import numpy as np

from research.am_c0_indicator_exit import POSITION_FEATURES, REGIME_FEATURES, SYMBOL_FEATURES
from research.e1_x28_executable_joint.board import BOARD_FRESHNESS_SEC, MIN_QTY
from research.e1_x35_passive_exit.paths import _valid_bid
from research.joint_feature_architecture import MIN_PERCENTILE_SAMPLES

SYMBOL_TO_REGIME = {
    "mid_ret_60s": "MARKET_MEDIAN_RET_60S",
    "mid_ret_180s": "MARKET_MEDIAN_RET_180S",
    "spread_bps": "MARKET_MEDIAN_SPREAD_BPS",
    "imbalance": "MARKET_MEDIAN_IMBALANCE",
    "event_rate_60s": "MARKET_MEDIAN_EVENT_RATE_60S",
    "volume_rate_60s": "MARKET_MEDIAN_VOLUME_RATE_60S",
    "volume_percentile_60s": "MARKET_MEDIAN_VOLUME_PERCENTILE_60S",
    "trading_value_percentile_180s": "MARKET_MEDIAN_TRADING_VALUE_PERCENTILE_180S",
    "distance_from_vwap_bps": "MARKET_MEDIAN_DISTANCE_VWAP_BPS",
    "rebound_from_recent_low_bps": "MARKET_MEDIAN_REBOUND_LOW_BPS",
}


class _BIT:
    def __init__(self, n: int) -> None:
        self.n = int(n)
        self.t = np.zeros(self.n + 1, dtype=np.int64)

    def add(self, i: int, v: int = 1) -> None:
        i = int(i) + 1
        while i <= self.n:
            self.t[i] += v
            i += i & -i

    def prefix(self, i: int) -> int:
        i = int(i) + 1
        s = 0
        while i > 0:
            s += int(self.t[i])
            i -= i & -i
        return s


def _expanding_percentile(values: np.ndarray, min_n: int) -> np.ndarray:
    n = int(values.size)
    out = np.full(n, np.nan, dtype=float)
    idx = np.flatnonzero(np.isfinite(values))
    if idx.size == 0:
        return out
    fv = values[idx]
    uniq = np.unique(fv)
    ranks = np.searchsorted(uniq, fv)
    bit = _BIT(int(uniq.size))
    seen = 0
    for j, rnk in enumerate(ranks):
        seen += 1
        le = bit.prefix(int(rnk))
        if seen >= int(min_n):
            out[int(idx[j])] = float(le + 1) / float(seen)
        bit.add(int(rnk), 1)
    return out


def _lookback_index(t: np.ndarray, lag_sec: float) -> np.ndarray:
    return np.searchsorted(t, t - float(lag_sec), side="right") - 1


def _rolling_min(t: np.ndarray, xs: np.ndarray, window: float) -> np.ndarray:
    n = int(t.size)
    out = np.full(n, np.nan, dtype=float)
    dq: deque[int] = deque()
    left = 0
    for i in range(n):
        lo = float(t[i]) - float(window)
        while left < n and float(t[left]) < lo - 1e-12:
            left += 1
        while dq and dq[0] < left:
            dq.popleft()
        v = float(xs[i]) if xs[i] == xs[i] else np.nan
        if v == v:
            while dq and (not (xs[dq[-1]] == xs[dq[-1]]) or float(xs[dq[-1]]) >= v):
                dq.pop()
            dq.append(i)
        if dq:
            out[i] = float(xs[dq[0]])
    return out


def vol_arrays_from_events(events: list[tuple[float, Optional[float], Optional[float]]]) -> dict[str, np.ndarray]:
    if not events:
        z = np.asarray([], dtype=float)
        return {"t": z, "vol": z, "dvol": z, "tv_inc": z, "mid": z}
    t = np.asarray([e[0] for e in events], dtype=float)
    vol = np.asarray([e[1] if e[1] is not None else np.nan for e in events], dtype=float)
    mid = np.asarray([e[2] if e[2] is not None else np.nan for e in events], dtype=float)
    dvol = np.zeros(t.size, dtype=float)
    if t.size >= 2:
        prev = vol[:-1]
        cur = vol[1:]
        good = np.isfinite(prev) & np.isfinite(cur) & (cur >= prev)
        dvol[1:] = np.where(good, cur - prev, 0.0)
    tv_inc = np.where(np.isfinite(mid) & (mid > 0) & (dvol > 0), mid * dvol, 0.0)
    return {"t": t, "vol": vol, "dvol": dvol, "tv_inc": tv_inc, "mid": mid}


def valid_decision_mask(board: dict[str, np.ndarray]) -> tuple[np.ndarray, dict[str, int]]:
    t = np.asarray(board.get("t") if board.get("t") is not None else [], dtype=float)
    n = int(t.size)
    skip = {"ITAYOSE_SKIP_N": 0, "SPECIAL_SKIP_N": 0, "INVALID_SKIP_N": 0}
    if n == 0:
        return np.zeros(0, dtype=bool), skip
    spec = np.asarray(board.get("special") if board.get("special") is not None else np.zeros(n, dtype=bool), dtype=bool)
    exe = np.asarray(board.get("executable") if board.get("executable") is not None else np.ones(n, dtype=bool), dtype=bool)
    state = board.get("board_execution_state")
    mask = np.zeros(n, dtype=bool)
    for i in range(n):
        st = str(state[i] or "").upper() if state is not None else ""
        if spec[i] or "SPECIAL" in st:
            skip["SPECIAL_SKIP_N"] += 1
            continue
        if (not bool(exe[i])) or "ITAYOSE" in st or "PREOPEN" in st:
            skip["ITAYOSE_SKIP_N"] += 1
            continue
        if not _valid_bid(board, i):
            skip["INVALID_SKIP_N"] += 1
            continue
        fresh = float(board["fresh_sec"][i]) if np.isfinite(board["fresh_sec"][i]) else 0.0
        if fresh > BOARD_FRESHNESS_SEC + 1e-12:
            skip["INVALID_SKIP_N"] += 1
            continue
        qty = board["bid_qty"][i]
        if not np.isfinite(qty) or qty < MIN_QTY:
            skip["INVALID_SKIP_N"] += 1
            continue
        mask[i] = True
    return mask, skip


def symbol_feature_table(
    board: dict[str, np.ndarray],
    vol: dict[str, np.ndarray],
    *,
    t_lo: float,
) -> dict[str, np.ndarray]:
    t = np.asarray(board.get("t") if board.get("t") is not None else [], dtype=float)
    n = int(t.size)
    empty = {k: np.full(n, np.nan, dtype=float) for k in SYMBOL_FEATURES}
    empty["t"] = t
    if n == 0:
        return empty
    bid = np.asarray(board["bid"][:n], dtype=float)
    ask = np.asarray(board["ask"][:n], dtype=float)
    bq = np.asarray(board["bid_qty"][:n], dtype=float)
    aq = np.asarray(board["ask_qty"][:n], dtype=float)
    spec = np.asarray(board["special"][:n], dtype=bool) if board.get("special") is not None else np.zeros(n, dtype=bool)
    ok = (~spec) & np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask > bid + 1e-12)
    mid = np.where(ok, 0.5 * (bid + ask), np.nan)
    den = bq + aq
    safe = ok & np.isfinite(den) & (den > 0)
    imb = np.where(safe, (bq - aq) / np.where(safe, den, 1.0), np.nan)
    spread = np.where(ok & (mid > 0), (ask - bid) / mid * 10000.0, np.nan)
    log_bq = np.where(ok & np.isfinite(bq) & (bq >= 0), np.log1p(bq), np.nan)

    i60 = _lookback_index(t, 60.0)
    i180 = _lookback_index(t, 180.0)
    past60_ok = (i60 >= 0) & (t[np.clip(i60, 0, n - 1)] >= float(t_lo) - 1e-12) & (t >= float(t_lo) + 60.0 - 1e-12)
    past180_ok = (i180 >= 0) & (t[np.clip(i180, 0, n - 1)] >= float(t_lo) - 1e-12) & (t >= float(t_lo) + 180.0 - 1e-12)
    mid_past60 = np.where(past60_ok, mid[np.clip(i60, 0, n - 1)], np.nan)
    mid_past180 = np.where(past180_ok, mid[np.clip(i180, 0, n - 1)], np.nan)
    ret60 = np.where(np.isfinite(mid) & np.isfinite(mid_past60) & (mid_past60 > 0), (mid / mid_past60 - 1.0) * 10000.0, np.nan)
    ret180 = np.where(
        np.isfinite(mid) & np.isfinite(mid_past180) & (mid_past180 > 0), (mid / mid_past180 - 1.0) * 10000.0, np.nan
    )
    i60_left = np.searchsorted(t, t - 60.0, side="left")
    event_rate = (np.arange(n) - i60_left + 1).astype(float) / 60.0

    rmin = _rolling_min(t, mid, 180.0)
    rebound = np.where(np.isfinite(mid) & (mid > 0) & np.isfinite(rmin), (mid - rmin) / mid * 10000.0, np.nan)

    vt = np.asarray(vol.get("t") if vol.get("t") is not None else [], dtype=float)
    vvol = np.asarray(vol.get("vol") if vol.get("vol") is not None else [], dtype=float)
    dvol = np.asarray(vol.get("dvol") if vol.get("dvol") is not None else [], dtype=float)
    tv_inc = np.asarray(vol.get("tv_inc") if vol.get("tv_inc") is not None else [], dtype=float)
    volume_rate = np.full(n, np.nan, dtype=float)
    tv_delta = np.full(n, np.nan, dtype=float)
    dist_vwap = np.full(n, np.nan, dtype=float)
    vol_pct = np.full(n, np.nan, dtype=float)
    tv_pct = np.full(n, np.nan, dtype=float)
    if vt.size:
        j = np.searchsorted(vt, t, side="right") - 1
        j60 = np.searchsorted(vt, t - 60.0, side="right") - 1
        j180 = np.searchsorted(vt, t - 180.0, side="right") - 1
        cum_d = np.cumsum(dvol) if dvol.size else np.asarray([], dtype=float)
        cum_tv = np.cumsum(tv_inc) if tv_inc.size else np.asarray([], dtype=float)
        age_ok60 = t >= float(t_lo) + 60.0 - 1e-12
        age_ok180 = t >= float(t_lo) + 180.0 - 1e-12
        for i in range(n):
            ji = int(j[i])
            if ji < 0:
                continue
            if float(vt[ji]) < float(t_lo) - 1e-12:
                continue
            if cum_d.size and float(cum_d[ji]) > 0 and np.isfinite(mid[i]) and mid[i] > 0:
                dist_vwap[i] = float((mid[i] - float(cum_tv[ji]) / float(cum_d[ji])) / mid[i] * 10000.0)
            if age_ok60[i]:
                ja = int(j60[i])
                now_v = float(vvol[ji]) if np.isfinite(vvol[ji]) else np.nan
                past_v = float(vvol[ja]) if ja >= 0 and float(vt[ja]) >= float(t_lo) - 1e-12 and np.isfinite(vvol[ja]) else np.nan
                if now_v == now_v and past_v == past_v:
                    volume_rate[i] = float(max(0.0, now_v - past_v) / 60.0)
                lo = 0 if ja < 0 else ja + 1
                tv_delta[i] = float(np.sum(tv_inc[lo : ji + 1]))
        if age_ok60.any():
            vol_delta_series = np.full(vt.size, np.nan, dtype=float)
            jj60 = np.searchsorted(vt, vt - 60.0, side="right") - 1
            for k in range(vt.size):
                if float(vt[k]) < float(t_lo) + 60.0 - 1e-12:
                    continue
                ja = int(jj60[k])
                if ja < 0 or float(vt[ja]) < float(t_lo) - 1e-12:
                    continue
                if np.isfinite(vvol[k]) and np.isfinite(vvol[ja]):
                    vol_delta_series[k] = float(max(0.0, float(vvol[k]) - float(vvol[ja])))
            pct_vol = _expanding_percentile(vol_delta_series, int(MIN_PERCENTILE_SAMPLES))
            for i in range(n):
                ji = int(j[i])
                if ji >= 0 and age_ok60[i]:
                    vol_pct[i] = float(pct_vol[ji]) if pct_vol[ji] == pct_vol[ji] else np.nan
        if age_ok180.any():
            tv180 = np.full(vt.size, np.nan, dtype=float)
            jj180 = np.searchsorted(vt, vt - 180.0, side="right") - 1
            ctv = np.concatenate([[0.0], np.cumsum(tv_inc)])
            for k in range(vt.size):
                if float(vt[k]) < float(t_lo) + 180.0 - 1e-12:
                    continue
                ja = int(jj180[k])
                lo = 0 if ja < 0 else ja + 1
                tv180[k] = float(ctv[k + 1] - ctv[lo])
            pct_tv = _expanding_percentile(tv180, int(MIN_PERCENTILE_SAMPLES))
            for i in range(n):
                ji = int(j[i])
                if ji >= 0 and age_ok180[i]:
                    tv_pct[i] = float(pct_tv[ji]) if pct_tv[ji] == pct_tv[ji] else np.nan

    out = {
        "t": t,
        "spread_bps": spread,
        "imbalance": imb,
        "mid_ret_60s": ret60,
        "mid_ret_180s": ret180,
        "event_rate_60s": event_rate,
        "log_bid_qty": log_bq,
        "distance_from_vwap_bps": dist_vwap,
        "rebound_from_recent_low_bps": rebound,
        "volume_rate_60s": volume_rate,
        "trading_value_delta_60s": tv_delta,
        "volume_percentile_60s": vol_pct,
        "trading_value_percentile_180s": tv_pct,
        "mid": mid,
        "bid": bid,
    }
    return out


def last_index_at(t: np.ndarray, t_query: float) -> int:
    if t.size == 0:
        return -1
    i = int(np.searchsorted(t, float(t_query), side="right") - 1)
    return i


def stack_symbol_matrix(table: dict[str, np.ndarray]) -> np.ndarray:
    cols = [np.asarray(table[k], dtype=float) for k in SYMBOL_FEATURES]
    return np.column_stack(cols)


def regime_from_last_rows(rows: list[np.ndarray]) -> np.ndarray:
    """rows: list of length-12 symbol feature vectors (may contain nan)."""
    out = np.full(len(REGIME_FEATURES), np.nan, dtype=float)
    if not rows:
        return out
    m = np.vstack(rows)
    # column order = SYMBOL_FEATURES
    def _med(col: int) -> float:
        v = m[:, col]
        v = v[np.isfinite(v)]
        return float(np.median(v)) if v.size else np.nan

    def _iqr(col: int) -> float:
        v = m[:, col]
        v = v[np.isfinite(v)]
        if v.size == 0:
            return np.nan
        return float(np.percentile(v, 75) - np.percentile(v, 25))

    def _breadth(col: int) -> float:
        v = m[:, col]
        v = v[np.isfinite(v)]
        if v.size == 0:
            return np.nan
        return float(np.mean(v > 0.0))

    idx = {k: i for i, k in enumerate(SYMBOL_FEATURES)}
    out[0] = _med(idx["mid_ret_60s"])
    out[1] = _med(idx["mid_ret_180s"])
    out[2] = _breadth(idx["mid_ret_60s"])
    out[3] = _breadth(idx["mid_ret_180s"])
    out[4] = _iqr(idx["mid_ret_60s"])
    out[5] = _iqr(idx["mid_ret_180s"])
    out[6] = _med(idx["spread_bps"])
    out[7] = _med(idx["imbalance"])
    out[8] = _med(idx["event_rate_60s"])
    out[9] = _med(idx["volume_rate_60s"])
    out[10] = _med(idx["volume_percentile_60s"])
    out[11] = _med(idx["trading_value_percentile_180s"])
    out[12] = _med(idx["distance_from_vwap_bps"])
    out[13] = _med(idx["rebound_from_recent_low_bps"])
    return out


def last_symbol_rows_at(
    tables: dict[str, dict[str, np.ndarray]],
    mats: dict[str, np.ndarray],
    t_query: float,
) -> list[np.ndarray]:
    rows = []
    for s, tab in tables.items():
        tt = tab.get("t")
        if tt is None or tt.size == 0:
            continue
        i = last_index_at(tt, t_query)
        if i < 0:
            continue
        rows.append(mats[s][i])
    return rows


def position_features(unreal_bps: np.ndarray) -> np.ndarray:
    n = int(unreal_bps.size)
    out = np.full((n, len(POSITION_FEATURES)), np.nan, dtype=float)
    peak = np.nan
    for i in range(n):
        u = float(unreal_bps[i]) if unreal_bps[i] == unreal_bps[i] else np.nan
        if u != u:
            continue
        peak = u if peak != peak else max(peak, u)
        out[i, 0] = u
        out[i, 1] = peak
        out[i, 2] = float(peak - u)
    return out
