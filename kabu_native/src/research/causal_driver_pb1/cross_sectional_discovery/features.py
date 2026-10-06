"""Last-completed as-of features. Leader age 60s. Target age 120s or 60s. No exact-print."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.causal_driver_pb1.cross_sectional_discovery import (
    GLOBAL_MIN_FRAC,
    GLOBAL_MIN_LEADERS,
    GLOBAL_MIN_N,
    LEADER_AGE_SEC,
    LOOKBACKS,
    MKT_MIN_FRAC,
    MKT_MIN_N,
    PAST_CONTROL_MIN,
    PEER_MIN_FRAC,
    PEER_MIN_N,
)
from research.causal_driver_pb1.cross_sectional_precommit import HORIZON_LAST_T
from research.causal_driver_pb1.phase2_discovery.clock import CLOCK_MINS, N_CLOCK, hhmm_to_min
from research.causal_driver_pb1.phase2_discovery.infer import LOG_BPS
from research.causal_driver_pb1.response.asof import age_sec_at_decision, locf_same_session, price_at_decision

AM_START = hhmm_to_min("09:00")
AM_END = hhmm_to_min("11:30")
AM_MINS = tuple(range(AM_START, AM_END + 1))
N_AM = len(AM_MINS)
CLOCK_AM = np.array([int(t) - AM_START for t in CLOCK_MINS], dtype=np.int32)
EPS = 1e-12


def clock_ok_for_horizon(t_min: int, horizon: int) -> bool:
    last = hhmm_to_min(HORIZON_LAST_T[str(int(horizon))])
    return int(t_min) <= last


def build_am(*, close: np.ndarray) -> dict[str, np.ndarray]:
    asof, src = locf_same_session(close)
    n_s, n_d, _ = asof.shape
    px = np.full((n_s, n_d, N_AM), np.nan, dtype=np.float64)
    age = np.full((n_s, n_d, N_AM), np.nan, dtype=np.float64)
    for i, t in enumerate(AM_MINS):
        px[:, :, i] = price_at_decision(asof, int(t))
        age[:, :, i] = age_sec_at_decision(src, int(t))
    return {"asof": asof, "src": src, "px": px, "age": age}


def listed_mask(*, symbols: list[str], dates: list[str], listing_start: dict[str, Any]) -> np.ndarray:
    starts = np.array([str(listing_start.get(s) or "99999999") for s in symbols])
    darr = np.array([str(d) for d in dates])
    return darr[None, :] >= starts[:, None]


def _logret(p1: np.ndarray, p0: np.ndarray) -> np.ndarray:
    out = np.full(p1.shape, np.nan, dtype=np.float64)
    ok = np.isfinite(p1) & np.isfinite(p0) & (p1 > 0) & (p0 > 0)
    out[ok] = LOG_BPS * np.log(p1[ok] / p0[ok])
    return out


def leader_ret_clock(
    *,
    px: np.ndarray,
    age: np.ndarray,
    si: int,
    w: int,
    max_age: float = LEADER_AGE_SEC,
) -> np.ndarray:
    """(n_d, n_clock) leader lookback return. Both endpoints age<=60."""
    n_d = px.shape[1]
    out = np.full((n_d, N_CLOCK), np.nan, dtype=np.float64)
    for ci, ai in enumerate(CLOCK_AM):
        a0 = int(ai) - int(w)
        if a0 < 0:
            continue
        p1 = px[si, :, int(ai)]
        p0 = px[si, :, a0]
        g1 = age[si, :, int(ai)]
        g0 = age[si, :, a0]
        ok = np.isfinite(p1) & np.isfinite(p0) & (p1 > 0) & (p0 > 0) & (g1 <= max_age) & (g0 <= max_age)
        tmp = _logret(p1, p0)
        out[ok, ci] = tmp[ok]
    return out


def leader_ret_offset(
    *,
    px: np.ndarray,
    age: np.ndarray,
    si: int,
    w: int,
    k: int,
    max_age: float = LEADER_AGE_SEC,
) -> np.ndarray:
    n_d = px.shape[1]
    out = np.full((n_d, N_CLOCK), np.nan, dtype=np.float64)
    for ci, ai in enumerate(CLOCK_AM):
        i1 = int(ai) + int(k)
        i0 = i1 - int(w)
        if i0 < 0 or i1 < 0 or i1 >= N_AM or i0 >= N_AM:
            continue
        p1 = px[si, :, i1]
        p0 = px[si, :, i0]
        g1 = age[si, :, i1]
        g0 = age[si, :, i0]
        ok = np.isfinite(p1) & np.isfinite(p0) & (p1 > 0) & (p0 > 0) & (g1 <= max_age) & (g0 <= max_age)
        tmp = _logret(p1, p0)
        out[ok, ci] = tmp[ok]
    return out


def global_leader_ret(leader_rets: list[np.ndarray], *, min_n: int = GLOBAL_MIN_LEADERS) -> np.ndarray:
    stack = np.stack(leader_rets, axis=0)
    valid = np.isfinite(stack)
    n = valid.sum(axis=0)
    acc = np.where(valid, stack, 0.0).sum(axis=0)
    out = acc / np.maximum(n, 1)
    out[n < int(min_n)] = np.nan
    return out


def _basket_pair(
    *,
    px: np.ndarray,
    age: np.ndarray,
    members: np.ndarray,
    listed: np.ndarray,
    i0: np.ndarray,
    i1: np.ndarray,
    horizon_ok: np.ndarray,
    max_age: float,
    min_n: int,
    min_frac: float,
) -> tuple[np.ndarray, np.ndarray]:
    n_d = px.shape[1]
    fut = np.full((n_d, N_CLOCK), np.nan, dtype=np.float64)
    n_ok = np.zeros((n_d, N_CLOCK), dtype=np.int16)
    mem = members[:, None]
    n_listed = (mem & listed).sum(axis=0).astype(np.float64)
    for ci in range(N_CLOCK):
        if not bool(horizon_ok[ci]):
            continue
        a0 = int(i0[ci])
        a1 = int(i1[ci])
        if a0 < 0 or a1 < 0 or a0 >= N_AM or a1 >= N_AM:
            continue
        p0 = px[:, :, a0]
        p1 = px[:, :, a1]
        g0 = age[:, :, a0]
        g1 = age[:, :, a1]
        fresh = (
            mem
            & listed
            & np.isfinite(p0)
            & np.isfinite(p1)
            & (p0 > 0)
            & (p1 > 0)
            & (g0 <= max_age)
            & (g1 <= max_age)
        )
        n = fresh.sum(axis=0)
        n_ok[:, ci] = n.astype(np.int16)
        frac = n / np.maximum(n_listed, 1.0)
        keep = (n >= int(min_n)) & (frac >= float(min_frac))
        if not np.any(keep):
            continue
        r = np.where(fresh, LOG_BPS * np.log(np.clip(p1, EPS, None) / np.clip(p0, EPS, None)), 0.0)
        eqw = r.sum(axis=0) / np.maximum(n, 1)
        fut[keep, ci] = eqw[keep]
    return fut, n_ok


def basket_future_and_lag(
    *,
    px: np.ndarray,
    age: np.ndarray,
    members: np.ndarray,
    listed: np.ndarray,
    horizon: int,
    max_age: float,
    min_n: int,
    min_frac: float,
) -> dict[str, np.ndarray]:
    h_ok = np.array([clock_ok_for_horizon(CLOCK_MINS[ci], int(horizon)) for ci in range(N_CLOCK)], dtype=np.bool_)
    i_t = CLOCK_AM
    fut, n_fut = _basket_pair(
        px=px,
        age=age,
        members=members,
        listed=listed,
        i0=i_t,
        i1=i_t + int(horizon),
        horizon_ok=h_ok,
        max_age=max_age,
        min_n=min_n,
        min_frac=min_frac,
    )
    lag, n_lag = _basket_pair(
        px=px,
        age=age,
        members=members,
        listed=listed,
        i0=i_t - int(PAST_CONTROL_MIN),
        i1=i_t,
        horizon_ok=np.ones(N_CLOCK, dtype=np.bool_),
        max_age=max_age,
        min_n=min_n,
        min_frac=min_frac,
    )
    return {"y": fut, "lag": lag, "n_fut": n_fut, "n_lag": n_lag}


def scope_members(*, symbols: list[str], sector_of: dict[str, str], leader_set: set[str], sector_id: str | None) -> np.ndarray:
    if sector_id is None:
        return np.array([s not in leader_set for s in symbols], dtype=np.bool_)
    return np.array(
        [sector_of.get(s) == str(sector_id) and s not in leader_set for s in symbols],
        dtype=np.bool_,
    )


def build_scope_catalog(frozen_leaders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scopes = []
    for row in frozen_leaders:
        sid = str(row["sector_id"])
        scopes.append(
            {
                "scope_id": f"SAME_SECTOR_{sid}",
                "mechanism": "SAME_SECTOR_LEADER",
                "sector_id": sid,
                "sector_name": row.get("sector_name"),
                "leader_symbol": row["leader_symbol"],
                "global": False,
            }
        )
    scopes.append(
        {
            "scope_id": "GLOBAL_LEADER_BASKET",
            "mechanism": "GLOBAL_LEADER_BASKET",
            "sector_id": None,
            "leader_symbol": None,
            "leader_symbols": [r["leader_symbol"] for r in frozen_leaders],
            "global": True,
        }
    )
    return scopes


def precompute_drivers(*, px, age, symbols: list[str], frozen_leaders: list[dict[str, Any]]) -> dict[str, Any]:
    pos = {s: i for i, s in enumerate(symbols)}
    leader_si = [pos[r["leader_symbol"]] for r in frozen_leaders]
    by_leader: dict[str, dict[int, np.ndarray]] = {}
    for r, si in zip(frozen_leaders, leader_si):
        by_w = {}
        for w in LOOKBACKS:
            by_w[int(w)] = leader_ret_clock(px=px, age=age, si=int(si), w=int(w))
        by_leader[r["leader_symbol"]] = by_w
    glob: dict[int, np.ndarray] = {}
    for w in LOOKBACKS:
        glob[int(w)] = global_leader_ret([by_leader[r["leader_symbol"]][int(w)] for r in frozen_leaders])
    return {"by_leader": by_leader, "global": glob, "leader_si": leader_si, "pos": pos}
