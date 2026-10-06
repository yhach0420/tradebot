"""Causal last-completed stock close. As-of lookup of an observed bar, not a fabricated fill."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

import numpy as np

from research.causal_driver_pb1.contracts.time import JST, assert_aware, bar_start_available_at, causal_ok
from research.causal_driver_pb1.phase2_discovery.clock import (
    CLOCK_MINS,
    GRID_START_MIN,
    N_CLOCK,
    N_GRID,
    bar_index_for_available_t,
    clock_ok_for_horizon,
    grid_index,
    hhmm_to_min,
)

CASH_SESSION_OPEN = "09:00"
CASH_SESSION_OPEN_MIN = hhmm_to_min(CASH_SESSION_OPEN)
SAME_SESSION_ONLY = True
LOG_BPS = 10000.0
EPS = 1e-12
PAST_CONTROL_MIN = 5


@dataclass(frozen=True, slots=True)
class ResolvedPrice:
    close: float
    bar_start: datetime
    available_at: datetime
    decision_time: datetime
    price_age_sec: float
    reason: str | None = None


def last_completed_price(
    *,
    bars: Sequence[dict[str, Any]],
    decision_time: datetime,
    session_open: datetime | None = None,
    same_session_only: bool = SAME_SESSION_ONLY,
) -> ResolvedPrice | None:
    """Most recent 1m bar with available_at <= decision_time. Native close only. Same session by default."""
    dt = assert_aware(decision_time, field="decision_time")
    best: ResolvedPrice | None = None
    for raw in bars:
        start = assert_aware(raw["bar_start"], field="bar_start")
        if "available_at" in raw and raw["available_at"] is not None:
            avail = assert_aware(raw["available_at"], field="available_at")
        else:
            avail = bar_start_available_at(start)
        px = raw.get("close")
        if px is None or float(px) <= 0:
            continue
        if same_session_only:
            if start.astimezone(JST).date() != dt.astimezone(JST).date():
                continue
            open_at = session_open
            if open_at is None:
                open_at = dt.astimezone(JST).replace(hour=9, minute=0, second=0, microsecond=0)
            else:
                open_at = assert_aware(open_at, field="session_open")
            if start < open_at:
                continue
        if not causal_ok(available_at=avail, decision_time=dt):
            continue
        age = (dt - avail).total_seconds()
        cand = ResolvedPrice(
            close=float(px),
            bar_start=start,
            available_at=avail,
            decision_time=dt,
            price_age_sec=float(age),
        )
        if best is None or avail > best.available_at or (avail == best.available_at and start > best.bar_start):
            best = cand
    return best


def session_open_grid_index() -> int:
    return grid_index(CASH_SESSION_OPEN_MIN)


def locf_same_session(close: np.ndarray, *, session_g0: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Last observed close and source grid index along axis=-1. Pre-session bars are not sources. No prior-day carry."""
    if close.ndim != 3:
        raise ValueError("close_must_be_symbol_date_grid")
    g0 = session_open_grid_index() if session_g0 is None else int(session_g0)
    n_s, n_d, n_g = close.shape
    valid = np.isfinite(close) & (close > 0)
    if g0 > 0:
        valid = valid.copy()
        valid[:, :, :g0] = False
    idx = np.where(valid, np.arange(n_g, dtype=np.int32)[None, None, :], np.int32(-1))
    src = np.maximum.accumulate(idx, axis=2)
    flat_c = close.reshape(n_s * n_d, n_g)
    flat_s = src.reshape(n_s * n_d, n_g)
    take = np.maximum(flat_s, 0)
    gathered = np.take_along_axis(flat_c, take, axis=1)
    good = flat_s >= g0
    asof = np.where(good, gathered, np.nan).reshape(n_s, n_d, n_g)
    src_out = np.where(src >= g0, src, np.int32(-1))
    return asof.astype(np.float64, copy=False), src_out


def locf_from_grid_start(close: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """RCA replica: accumulate from grid 08:00. Diagnostic only."""
    return locf_same_session(close, session_g0=0)


def price_at_decision(asof: np.ndarray, decision_min: int) -> np.ndarray:
    gi = bar_index_for_available_t(int(decision_min))
    if gi < 0 or gi >= asof.shape[-1]:
        return np.full(asof.shape[:-1], np.nan, dtype=np.float64)
    return asof[..., gi]


def age_sec_at_decision(src_idx: np.ndarray, decision_min: int) -> np.ndarray:
    gi = bar_index_for_available_t(int(decision_min))
    if gi < 0 or gi >= src_idx.shape[-1]:
        return np.full(src_idx.shape[:-1], np.nan, dtype=np.float64)
    src = src_idx[..., gi].astype(np.float64)
    avail_min = GRID_START_MIN + src + 1.0
    age = (float(decision_min) - avail_min) * 60.0
    age = np.where(src >= 0, age, np.nan)
    return age


def target_return_and_lag_asof(
    *,
    asof: np.ndarray,
    idx: np.ndarray,
    horizon: int,
    is_mkt: bool,
    min_frac: float,
    min_n: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """EQW future h and past-5m control from last-completed prices. No exact-print requirement."""
    n_g = asof.shape[2]
    bars = np.array([bar_index_for_available_t(t) for t in CLOCK_MINS], dtype=np.int32)
    t0 = bars
    t1 = bars + int(horizon)
    tlag = bars - int(PAST_CONTROL_MIN)
    sub = asof[idx]
    n_d = asof.shape[1]
    n_const = int(idx.size)
    fut = np.full((n_d, N_CLOCK), np.nan, dtype=np.float64)
    lag = np.full((n_d, N_CLOCK), np.nan, dtype=np.float64)
    n_ok = np.zeros((n_d, N_CLOCK), dtype=np.int16)
    thr = min_n if is_mkt else int(np.ceil(min_frac * n_const))
    for ci in range(N_CLOCK):
        if not clock_ok_for_horizon(CLOCK_MINS[ci], horizon):
            continue
        if t1[ci] >= n_g or tlag[ci] < 0 or t0[ci] < 0:
            continue
        c0 = sub[:, :, t0[ci]]
        c1 = sub[:, :, t1[ci]]
        cl = sub[:, :, tlag[ci]]
        v0 = np.isfinite(c0) & (c0 > 0)
        v1 = np.isfinite(c1) & (c1 > 0)
        vl = np.isfinite(cl) & (cl > 0)
        both = v0 & v1
        both_l = v0 & vl
        n = both.sum(axis=0)
        n_ok[:, ci] = n.astype(np.int16)
        keep = (n >= thr) & (both_l.sum(axis=0) >= thr)
        if not np.any(keep):
            continue
        r = np.where(both, LOG_BPS * np.log(np.clip(c1, EPS, None) / np.clip(c0, EPS, None)), 0.0)
        rl = np.where(both_l, LOG_BPS * np.log(np.clip(c0, EPS, None) / np.clip(cl, EPS, None)), 0.0)
        rs = r.sum(axis=0) / np.maximum(n, 1)
        rls = rl.sum(axis=0) / np.maximum(both_l.sum(axis=0), 1)
        fut[keep, ci] = rs[keep]
        lag[keep, ci] = rls[keep]
    cov = n_ok.astype(np.float64) / float(max(n_const, 1))
    return fut, lag, cov


def symbol_horizon_return_asof(*, asof: np.ndarray, horizon: int) -> np.ndarray:
    n_s, n_d, n_g = asof.shape
    bars = np.array([bar_index_for_available_t(t) for t in CLOCK_MINS], dtype=np.int32)
    out = np.full((n_s, n_d, N_CLOCK), np.nan, dtype=np.float64)
    for ci in range(N_CLOCK):
        if not clock_ok_for_horizon(CLOCK_MINS[ci], horizon):
            continue
        i0 = bars[ci]
        i1 = bars[ci] + int(horizon)
        if i1 >= n_g or i0 < 0:
            continue
        a = asof[:, :, i1]
        b = asof[:, :, i0]
        ok = np.isfinite(a) & np.isfinite(b) & (a > 0) & (b > 0)
        tmp = np.full((n_s, n_d), np.nan, dtype=np.float64)
        tmp[ok] = LOG_BPS * np.log(a[ok] / b[ok])
        out[:, :, ci] = tmp
    return out
