"""Leave-target-out breadth and peer controls. Regime is the frozen-pool contract."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.causal_driver_pb1.phase2_discovery.clock import CLOCK_MINS, N_CLOCK
from research.causal_driver_pb1.phase2_discovery.infer import LOG_BPS
from research.causal_driver_pb1.sector_breadth_discovery import GLOBAL_MIN_FRAC, GLOBAL_MIN_N, SECTOR_MIN_FRAC, SECTOR_MIN_N
from research.causal_driver_pb1.sector_breadth_discovery.features import CLOCK_AM, N_AM, clock_ok_for_horizon, constituent_ret_clock
from research.causal_driver_pb1.sector_breadth_precommit_v1_2.control_gate import required_ex_sector_valid_n
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1.regimes import REGIME_MULTI, REGIME_NONE, REGIME_SINGLE


def target_y(*, px: np.ndarray, age: np.ndarray, horizon: int, max_age: float) -> np.ndarray:
    out = np.full((px.shape[0], px.shape[1], N_CLOCK), np.nan, dtype=np.float64)
    for ci, ai in enumerate(CLOCK_AM):
        if not clock_ok_for_horizon(int(CLOCK_MINS[ci]), int(horizon)):
            continue
        i1 = int(ai) + int(horizon)
        if i1 >= N_AM:
            continue
        p1 = px[:, :, i1]
        p0 = px[:, :, int(ai)]
        ok = (
            np.isfinite(p1)
            & np.isfinite(p0)
            & (p1 > 0)
            & (p0 > 0)
            & (age[:, :, i1] <= max_age)
            & (age[:, :, int(ai)] <= max_age)
        )
        tmp = np.full(p1.shape, np.nan, dtype=np.float64)
        tmp[ok] = LOG_BPS * np.log(p1[ok] / p0[ok])
        out[:, :, ci] = tmp
    return out


def breadth_ex_target(*, ret: np.ndarray, listed: np.ndarray, global_scope: bool) -> np.ndarray:
    """Breadth of `ret` members excluding each member itself. ret/listed share axis 0."""
    valid = np.isfinite(ret) & listed[:, :, None]
    n_valid = valid.sum(axis=0)
    n_up = ((ret > 0) & valid).sum(axis=0)
    n_down = ((ret < 0) & valid).sum(axis=0)
    n_listed = listed.sum(axis=0).astype(np.float64)
    n_valid_ex = n_valid[None, :, :] - valid.astype(np.float64)
    n_up_ex = n_up[None, :, :] - ((ret > 0) & valid).astype(np.float64)
    n_down_ex = n_down[None, :, :] - ((ret < 0) & valid).astype(np.float64)
    n_listed_ex = n_listed[None, :, None] - listed[:, :, None].astype(np.float64)
    breadth = (n_up_ex - n_down_ex) / np.maximum(n_valid_ex, 1.0)
    if global_scope:
        ok = (n_valid_ex >= float(GLOBAL_MIN_N)) & (n_valid_ex >= float(GLOBAL_MIN_FRAC) * np.maximum(n_listed_ex, 0.0))
        ok = ok & (n_valid_ex >= 2)
    else:
        thresh = np.maximum(float(SECTOR_MIN_N), np.ceil(float(SECTOR_MIN_FRAC) * np.maximum(n_listed_ex, 0.0)))
        ok = (n_listed_ex > 0) & (n_valid_ex >= thresh) & (n_valid_ex >= float(SECTOR_MIN_FRAC) * n_listed_ex)
    return np.where(ok & listed[:, :, None], np.clip(breadth, -1.0, 1.0), np.nan)


def _sector_mean(past: np.ndarray, listed: np.ndarray, mem: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    block = past[mem]
    listed_m = listed[mem]
    valid = np.isfinite(block)
    n_peer = listed_m.sum(axis=0)[None, :, None] - listed_m[:, :, None].astype(np.int16)
    n_valid = valid.sum(axis=0)[None, :, :] - valid.astype(np.int16)
    acc = np.where(valid, block, 0.0).sum(axis=0)
    acc_ex = acc[None, :, :] - np.where(valid, block, 0.0)
    mean = acc_ex / np.maximum(n_valid, 1)
    return mean, n_peer, n_valid


def prepare(*, px: np.ndarray, age: np.ndarray, listed: np.ndarray, symbols: list[str], sector_of: dict[str, str], assignment_by_symbol: dict[str, dict[str, Any]], y_by_horizon: dict[int, set[str]] | None) -> dict[str, Any]:
    past = constituent_ret_clock(px=px, age=age, w=5)
    breadth = {
        (1, True): breadth_ex_target(ret=constituent_ret_clock(px=px, age=age, w=1), listed=listed, global_scope=True),
        (3, False): None,
        (1, False): None,
    }
    s3650 = np.array([sector_of[s] == "3650" for s in symbols], dtype=np.bool_)
    ret1 = constituent_ret_clock(px=px, age=age, w=1)
    ret3 = constituent_ret_clock(px=px, age=age, w=3)
    b1 = np.full(past.shape, np.nan, dtype=np.float64)
    b3 = np.full(past.shape, np.nan, dtype=np.float64)
    idx = np.where(s3650)[0]
    b1[idx] = breadth_ex_target(ret=ret1[idx], listed=listed[idx], global_scope=False)
    b3[idx] = breadth_ex_target(ret=ret3[idx], listed=listed[idx], global_scope=False)
    breadth[(1, False)] = b1
    breadth[(3, False)] = b3
    del ret1, ret3
    y120 = {}
    y60 = {}
    for h in (1, 3):
        full120 = np.full(past.shape, np.nan, dtype=np.float64)
        full60 = np.full(past.shape, np.nan, dtype=np.float64)
        if y_by_horizon is None:
            keep_idx = np.arange(len(symbols), dtype=np.int32)
        else:
            keep_idx = np.array([i for i, s in enumerate(symbols) if s in y_by_horizon.get(h, set())], dtype=np.int32)
        if keep_idx.size:
            full120[keep_idx] = target_y(px=px[keep_idx], age=age[keep_idx], horizon=h, max_age=120.0)
            full60[keep_idx] = target_y(px=px[keep_idx], age=age[keep_idx], horizon=h, max_age=60.0)
        y120[h] = full120
        y60[h] = full60
    members: dict[str, np.ndarray] = {}
    for i, s in enumerate(symbols):
        members.setdefault(str(sector_of[s]), []).append(i)
    mem_arr = {sid: np.array(ix, dtype=np.int32) for sid, ix in members.items()}
    sector_x = np.full(past.shape, np.nan, dtype=np.float64)
    omit = np.zeros(len(symbols), dtype=np.bool_)
    for sid, mem in mem_arr.items():
        mean, n_peer, n_valid = _sector_mean(past, listed, mem)
        multi_ok = (n_peer >= 2) & (n_valid >= 2) & (n_valid >= 0.80 * n_peer)
        single_ok = (n_peer == 1) & (n_valid == 1)
        for local, si in enumerate(mem):
            regime = assignment_by_symbol[symbols[int(si)]]["control_regime"]
            if regime == REGIME_MULTI:
                sector_x[int(si)] = np.where(multi_ok[local], mean[local], np.nan)
            elif regime == REGIME_SINGLE:
                sector_x[int(si)] = np.where(single_ok[local], mean[local], np.nan)
            elif regime == REGIME_NONE:
                omit[int(si)] = True
            else:
                raise RuntimeError("regime")
    n_s = len(symbols)
    lookup = np.array([required_ex_sector_valid_n(i) for i in range(n_s + 1)], dtype=np.int32)
    market = np.full(past.shape, np.nan, dtype=np.float64)
    valid_past = np.isfinite(past) & listed[:, :, None]
    for sid, mem in mem_arr.items():
        inside = np.zeros(n_s, dtype=np.bool_)
        inside[mem] = True
        outside = ~inside
        n_mex = listed[outside].sum(axis=0).astype(np.int32)
        n_mkt = valid_past[outside].sum(axis=0).astype(np.int32)
        acc = np.where(valid_past[outside], past[outside], 0.0).sum(axis=0)
        req = lookup[np.clip(n_mex, 0, n_s)]
        ok = (n_mex[:, None] > 0) & (n_mkt >= req[:, None]) & (n_mkt >= 0.80 * n_mex[:, None])
        mean = np.where(ok, acc / np.maximum(n_mkt, 1), np.nan)
        market[inside] = mean[None, :, :]
    return {"past": past, "breadth": breadth, "y120": y120, "y60": y60, "sector_x": sector_x, "omit_sector": omit, "market": market, "listed": listed}
