"""Per-date MKT_EX_SECTOR_PAST_5M using V1.2 feasibility branch. Not a global 80% relaxation."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.causal_driver_pb1.cross_sectional_discovery.features import CLOCK_AM, PAST_CONTROL_MIN, _basket_pair
from research.causal_driver_pb1.phase2_discovery.clock import N_CLOCK
from research.causal_driver_pb1.phase2_discovery.infer import LOG_BPS
from research.causal_driver_pb1.cross_sectional_discovery.features import EPS, N_AM
from research.causal_driver_pb1.sector_breadth_discovery import DRIVER_AGE_SEC
from research.causal_driver_pb1.sector_breadth_precommit_v1_2 import MKT_EX_MIN_FRAC, OLD_MKT_EX_MIN_N
from research.causal_driver_pb1.sector_breadth_precommit_v1_2.control_gate import required_ex_sector_valid_n


def mkt_ex_past_5m(
    *,
    px: np.ndarray,
    age: np.ndarray,
    members: np.ndarray,
    listed: np.ndarray,
    max_age: float = DRIVER_AGE_SEC,
) -> dict[str, Any]:
    mem = members[:, None]
    n_listed = (mem & listed).sum(axis=0).astype(np.int32)
    req = np.array([required_ex_sector_valid_n(int(x)) for x in n_listed], dtype=np.int32)
    used_correction = bool(np.any(n_listed < int(OLD_MKT_EX_MIN_N)))
    i_t = CLOCK_AM
    i0 = i_t - int(PAST_CONTROL_MIN)
    i1 = i_t
    horizon_ok = np.ones(N_CLOCK, dtype=np.bool_)
    if n_listed.size and int(req.min()) == int(req.max()):
        lag, n_ok = _basket_pair(
            px=px,
            age=age,
            members=members,
            listed=listed,
            i0=i0,
            i1=i1,
            horizon_ok=horizon_ok,
            max_age=max_age,
            min_n=int(req[0]),
            min_frac=float(MKT_EX_MIN_FRAC),
        )
    else:
        lag, n_ok = _basket_pair_required(
            px=px,
            age=age,
            members=members,
            listed=listed,
            i0=i0,
            i1=i1,
            horizon_ok=horizon_ok,
            max_age=max_age,
            required_n=req,
            min_frac=float(MKT_EX_MIN_FRAC),
        )
    return {
        "lag": lag,
        "n_ok": n_ok,
        "n_listed_min": int(n_listed.min()) if n_listed.size else 0,
        "n_listed_max": int(n_listed.max()) if n_listed.size else 0,
        "required_n_min": int(req.min()) if req.size else 0,
        "required_n_max": int(req.max()) if req.size else 0,
        "used_correction_branch": used_correction,
        "finite_lag_n": int(np.isfinite(lag).sum()),
    }


def _basket_pair_required(
    *,
    px: np.ndarray,
    age: np.ndarray,
    members: np.ndarray,
    listed: np.ndarray,
    i0: np.ndarray,
    i1: np.ndarray,
    horizon_ok: np.ndarray,
    max_age: float,
    required_n: np.ndarray,
    min_frac: float,
) -> tuple[np.ndarray, np.ndarray]:
    n_d = px.shape[1]
    fut = np.full((n_d, N_CLOCK), np.nan, dtype=np.float64)
    n_ok = np.zeros((n_d, N_CLOCK), dtype=np.int16)
    mem = members[:, None]
    n_listed = (mem & listed).sum(axis=0).astype(np.float64)
    req = required_n.astype(np.float64)
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
        keep = (n >= req) & (frac >= float(min_frac))
        if not np.any(keep):
            continue
        r = np.where(fresh, LOG_BPS * np.log(np.clip(p1, EPS, None) / np.clip(p0, EPS, None)), 0.0)
        eqw = r.sum(axis=0) / np.maximum(n, 1)
        fut[keep, ci] = eqw[keep]
    return fut, n_ok
