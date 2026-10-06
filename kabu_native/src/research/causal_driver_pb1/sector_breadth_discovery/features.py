"""Breadth/dispersion drivers on last-completed as-of prices. No exact-print."""
from __future__ import annotations

from math import ceil
from typing import Any

import numpy as np

from research.causal_driver_pb1.cross_sectional_discovery.features import (
    AM_MINS,
    CLOCK_AM,
    N_AM,
    basket_future_and_lag,
    build_am,
    listed_mask,
)
from research.causal_driver_pb1.phase2_discovery.clock import CLOCK_MINS, N_CLOCK, hhmm_to_min
from research.causal_driver_pb1.phase2_discovery.infer import LOG_BPS
from research.causal_driver_pb1.sector_breadth_discovery import (
    DRIVER_AGE_SEC,
    GLOBAL_MIN_FRAC,
    GLOBAL_MIN_N,
    HORIZON_LAST_T,
    LOOKBACKS,
    METRICS,
    SECTOR_MIN_FRAC,
    SECTOR_MIN_N,
)

EPS = 1e-12


def clock_ok_for_horizon(t_min: int, horizon: int) -> bool:
    last = hhmm_to_min(HORIZON_LAST_T[str(int(horizon))])
    return int(t_min) <= last


def listing_from_px(*, px: np.ndarray, symbols: list[str], dates: list[str]) -> dict[str, str | None]:
    present = np.isfinite(px) & (px > 0)
    day_ok = present.any(axis=2)
    out: dict[str, str | None] = {}
    for i, s in enumerate(symbols):
        hits = np.where(day_ok[i])[0]
        out[s] = dates[int(hits[0])] if hits.size else None
    return out


def merge_listing(a: dict[str, str | None], b: dict[str, str | None]) -> dict[str, str | None]:
    out = dict(a)
    for k, v in b.items():
        if out.get(k) is None:
            out[k] = v
        elif v is not None and str(v) < str(out[k]):
            out[k] = v
    return out


def scope_members(*, symbols: list[str], sector_of: dict[str, str], sector_id: str | None) -> np.ndarray:
    if sector_id is None:
        return np.ones(len(symbols), dtype=np.bool_)
    return np.array([sector_of.get(s) == str(sector_id) for s in symbols], dtype=np.bool_)


def mkt_ex_members(*, symbols: list[str], sector_of: dict[str, str], sector_id: str) -> np.ndarray:
    return np.array([sector_of.get(s) != str(sector_id) for s in symbols], dtype=np.bool_)


def constituent_ret_clock(*, px: np.ndarray, age: np.ndarray, w: int, max_age: float = DRIVER_AGE_SEC) -> np.ndarray:
    """(n_s, n_d, n_clock) lookback log-bps. Both endpoints age<=max_age."""
    n_s, n_d, _ = px.shape
    out = np.full((n_s, n_d, N_CLOCK), np.nan, dtype=np.float64)
    for ci, ai in enumerate(CLOCK_AM):
        a0 = int(ai) - int(w)
        if a0 < 0:
            continue
        p1 = px[:, :, int(ai)]
        p0 = px[:, :, a0]
        g1 = age[:, :, int(ai)]
        g0 = age[:, :, a0]
        ok = np.isfinite(p1) & np.isfinite(p0) & (p1 > 0) & (p0 > 0) & (g1 <= max_age) & (g0 <= max_age)
        tmp = np.full(p1.shape, np.nan, dtype=np.float64)
        tmp[ok] = LOG_BPS * np.log(p1[ok] / p0[ok])
        out[:, :, ci] = tmp
    return out


def constituent_ret_offset(*, px: np.ndarray, age: np.ndarray, w: int, k: int, max_age: float = DRIVER_AGE_SEC) -> np.ndarray:
    n_s, n_d, _ = px.shape
    out = np.full((n_s, n_d, N_CLOCK), np.nan, dtype=np.float64)
    for ci, ai in enumerate(CLOCK_AM):
        i1 = int(ai) + int(k)
        i0 = i1 - int(w)
        if i0 < 0 or i1 < 0 or i1 >= N_AM or i0 >= N_AM:
            continue
        p1 = px[:, :, i1]
        p0 = px[:, :, i0]
        g1 = age[:, :, i1]
        g0 = age[:, :, i0]
        ok = np.isfinite(p1) & np.isfinite(p0) & (p1 > 0) & (p0 > 0) & (g1 <= max_age) & (g0 <= max_age)
        tmp = np.full(p1.shape, np.nan, dtype=np.float64)
        tmp[ok] = LOG_BPS * np.log(p1[ok] / p0[ok])
        out[:, :, ci] = tmp
    return out


def _xs_stats(ret: np.ndarray, valid: np.ndarray, n_listed: np.ndarray, *, global_scope: bool) -> dict[str, np.ndarray]:
    n_valid = valid.sum(axis=0).astype(np.float64)
    n_up = ((ret > 0) & valid).sum(axis=0).astype(np.float64)
    n_down = ((ret < 0) & valid).sum(axis=0).astype(np.float64)
    breadth = (n_up - n_down) / np.maximum(n_valid, 1.0)
    acc = np.where(valid, ret, 0.0).sum(axis=0)
    mean = acc / np.maximum(n_valid, 1.0)
    var = np.where(valid, (ret - mean) ** 2, 0.0).sum(axis=0) / np.maximum(n_valid - 1.0, 1.0)
    disp = np.sqrt(np.maximum(var, 0.0))
    if global_scope:
        ok = (n_valid >= GLOBAL_MIN_N) & (n_valid >= GLOBAL_MIN_FRAC * n_listed[:, None])
    else:
        thresh = np.maximum(SECTOR_MIN_N, np.ceil(SECTOR_MIN_FRAC * n_listed)).astype(np.int32)
        ok = n_valid >= thresh[:, None]
    ok = ok & (n_valid >= 2)
    breadth = np.where(ok, np.clip(breadth, -1.0, 1.0), np.nan)
    disp = np.where(ok, disp, np.nan)
    return {"BREADTH": breadth, "DISPERSION": disp, "n_valid": n_valid, "ok": ok}


def driver_from_ret(*, ret: np.ndarray, mem: np.ndarray, listed: np.ndarray, global_scope: bool) -> dict[str, np.ndarray]:
    valid = mem[:, None, None] & listed[:, :, None] & np.isfinite(ret)
    n_listed = (mem[:, None] & listed).sum(axis=0).astype(np.float64)
    return _xs_stats(ret, valid, n_listed, global_scope=global_scope)


def precompute_drivers(
    *,
    px: np.ndarray,
    age: np.ndarray,
    listed: np.ndarray,
    scopes: list[dict[str, Any]],
    symbols: list[str],
    sector_of: dict[str, str],
) -> dict[str, Any]:
    rets: dict[int, np.ndarray] = {}
    for w in LOOKBACKS:
        rets[int(w)] = constituent_ret_clock(px=px, age=age, w=int(w))
        print(f"DRIVER_RET w={w}", flush=True)
    by_scope: dict[str, dict[str, dict[int, np.ndarray]]] = {}
    n_valid: dict[str, dict[int, np.ndarray]] = {}
    mems: dict[str, np.ndarray] = {}
    for sc in scopes:
        mem = scope_members(symbols=symbols, sector_of=sector_of, sector_id=sc.get("sector_id"))
        mems[sc["scope_id"]] = mem
        by_scope[sc["scope_id"]] = {m: {} for m in METRICS}
        n_valid[sc["scope_id"]] = {}
        glob = sc["scope_id"] == "GLOBAL_105"
        for w in LOOKBACKS:
            st = driver_from_ret(ret=rets[int(w)], mem=mem, listed=listed, global_scope=glob)
            by_scope[sc["scope_id"]]["BREADTH"][int(w)] = st["BREADTH"]
            by_scope[sc["scope_id"]]["DISPERSION"][int(w)] = st["DISPERSION"]
            n_valid[sc["scope_id"]][int(w)] = st["n_valid"]
    return {"by_scope": by_scope, "rets": rets, "mems": mems, "n_valid": n_valid}


def driver_offset(
    *,
    px: np.ndarray,
    age: np.ndarray,
    listed: np.ndarray,
    mem: np.ndarray,
    w: int,
    k: int,
    global_scope: bool,
    metric: str,
) -> np.ndarray:
    ret = constituent_ret_offset(px=px, age=age, w=int(w), k=int(k))
    st = driver_from_ret(ret=ret, mem=mem, listed=listed, global_scope=global_scope)
    return st[str(metric)]


def driver_loo(
    *,
    ret: np.ndarray,
    mem: np.ndarray,
    listed: np.ndarray,
    drop_i: int,
    global_scope: bool,
    metric: str,
) -> np.ndarray:
    mem2 = mem.copy()
    mem2[int(drop_i)] = False
    st = driver_from_ret(ret=ret, mem=mem2, listed=listed, global_scope=global_scope)
    return st[str(metric)]
