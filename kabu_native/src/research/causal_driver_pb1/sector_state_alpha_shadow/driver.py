"""SECTOR_3650 breadth w=1 from BAR_START endpoints. Not leave-target-out."""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Sequence

import numpy as np

from research.causal_driver_pb1.phase2_discovery.clock import CLOCK_MINS
from research.causal_driver_pb1.phase2_discovery.infer import LOG_BPS
from research.causal_driver_pb1.sector_breadth_discovery import DRIVER_AGE_SEC, SECTOR_MIN_FRAC, SECTOR_MIN_N
from research.causal_driver_pb1.cross_sectional_discovery.features import CLOCK_AM, N_AM

CONSTITUENT_N = 30


@dataclass(frozen=True, slots=True)
class DriverObservation:
    available: bool
    value: float | None
    valid_n: int
    up_n: int
    down_n: int
    freshness_valid: bool
    n_listed: int
    required_valid_n: int


def required_valid_n(n_listed: int) -> int:
    return int(max(SECTOR_MIN_N, ceil(SECTOR_MIN_FRAC * int(n_listed))))


def breadth_from_endpoints(
    *,
    price_now: Sequence[float],
    age_now: Sequence[float],
    price_prev: Sequence[float],
    age_prev: Sequence[float],
    listed: Sequence[bool],
) -> DriverObservation:
    """Both endpoints must already be last completed same-session closes."""
    n = len(price_now)
    if not (n == len(age_now) == len(price_prev) == len(age_prev) == len(listed) == CONSTITUENT_N):
        raise RuntimeError("m3_constituent_n")
    listed_arr = np.asarray(listed, dtype=np.bool_)
    n_listed = int(listed_arr.sum())
    need = required_valid_n(n_listed) if n_listed else required_valid_n(CONSTITUENT_N)
    p1 = np.asarray(price_now, dtype=np.float64)
    p0 = np.asarray(price_prev, dtype=np.float64)
    g1 = np.asarray(age_now, dtype=np.float64)
    g0 = np.asarray(age_prev, dtype=np.float64)
    fresh = np.isfinite(p1) & np.isfinite(p0) & (p1 > 0) & (p0 > 0) & (g1 <= DRIVER_AGE_SEC) & (g0 <= DRIVER_AGE_SEC)
    valid = listed_arr & fresh
    ret = np.full(n, np.nan, dtype=np.float64)
    ret[valid] = LOG_BPS * np.log(p1[valid] / p0[valid])
    return breadth_from_returns(ret=ret, valid=valid, n_listed=n_listed, required=need)


def breadth_from_returns(*, ret: np.ndarray, valid: np.ndarray, n_listed: int, required: int) -> DriverObservation:
    n_valid = int(valid.sum())
    n_up = int(((ret > 0) & valid).sum())
    n_down = int(((ret < 0) & valid).sum())
    ok = n_valid >= int(required) and n_valid >= 2
    if not ok:
        return DriverObservation(False, None, n_valid, n_up, n_down, False, int(n_listed), int(required))
    breadth = float(np.clip((n_up - n_down) / float(n_valid), -1.0, 1.0))
    return DriverObservation(True, breadth, n_valid, n_up, n_down, True, int(n_listed), int(required))


def panel_driver(*, px: np.ndarray, age: np.ndarray, listed: np.ndarray) -> dict[str, np.ndarray]:
    """Same clock indexing as the parent constituent_ret_clock for w=1."""
    n_s, n_d, _ = px.shape
    if n_s != CONSTITUENT_N:
        raise RuntimeError("m3_constituent_n")
    ret = np.full((n_s, n_d, len(CLOCK_MINS)), np.nan, dtype=np.float64)
    for ci, ai in enumerate(CLOCK_AM):
        a0 = int(ai) - 1
        if a0 < 0 or int(ai) >= N_AM:
            continue
        p1 = px[:, :, int(ai)]
        p0 = px[:, :, a0]
        g1 = age[:, :, int(ai)]
        g0 = age[:, :, a0]
        ok = np.isfinite(p1) & np.isfinite(p0) & (p1 > 0) & (p0 > 0) & (g1 <= DRIVER_AGE_SEC) & (g0 <= DRIVER_AGE_SEC)
        ret[:, :, ci] = np.where(ok, LOG_BPS * np.log(np.where(ok, p1, 1.0) / np.where(ok, p0, 1.0)), np.nan)
    valid = listed[:, :, None] & np.isfinite(ret)
    n_listed = listed.sum(axis=0).astype(np.int32)
    n_valid = valid.sum(axis=0).astype(np.int32)
    n_up = ((ret > 0) & valid).sum(axis=0).astype(np.int32)
    n_down = ((ret < 0) & valid).sum(axis=0).astype(np.int32)
    need = np.maximum(SECTOR_MIN_N, np.ceil(SECTOR_MIN_FRAC * n_listed)).astype(np.int32)
    ok = (n_valid >= need[:, None]) & (n_valid >= 2)
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = (n_up - n_down) / np.maximum(n_valid, 1)
    breadth = np.where(ok, np.clip(raw, -1.0, 1.0), np.nan)
    return {
        "ret": ret,
        "breadth": breadth,
        "n_valid": n_valid,
        "n_up": n_up,
        "n_down": n_down,
        "ok": ok,
        "n_listed": n_listed,
        "age_now": age[:, :, CLOCK_AM],
    }
