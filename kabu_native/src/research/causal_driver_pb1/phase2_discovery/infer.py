"""Vectorized FX features, basket outcomes, OLS FE, date-block bootstrap, gates."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.phase2_precommit import (
    BOOTSTRAP_N,
    BOOTSTRAP_SEED,
    FDR_Q,
    FX_LOOKBACKS_MIN,
    MKT105_MIN_VALID_SYMBOLS,
    RESPONSE_HORIZONS_MIN,
    SECTOR_CLOCK_COVERAGE_MIN,
)
from research.causal_driver_pb1.phase2_discovery import PAST_CONTROL_MIN, TARGET_SCOPES
from research.causal_driver_pb1.phase2_discovery.clock import (
    CLOCK_MINS,
    N_CLOCK,
    N_GRID,
    bar_index_for_available_t,
    clock_ok_for_horizon,
    grid_index,
)

LOG_BPS = 10000.0
EPS = 1e-12


def _logret(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    out = np.full(a.shape, np.nan, dtype=np.float64)
    ok = np.isfinite(a) & np.isfinite(b) & (a > 0) & (b > 0)
    out[ok] = LOG_BPS * np.log(a[ok] / b[ok])
    return out


def fx_lookback_cube(fx: dict[str, Any]) -> dict[int, np.ndarray]:
    """FX_RET_w at available-at time T for every grid minute. Unexpected missing inside lookback → NaN."""
    mid = fx["mid"]
    valid = fx["valid"]
    unexp = fx["unexpected_missing"]
    n_d = mid.shape[0]
    out: dict[int, np.ndarray] = {}
    for w in FX_LOOKBACKS_MIN:
        feat = np.full((n_d, N_GRID), np.nan, dtype=np.float64)
        for gi in range(w, N_GRID):
            now = gi
            lag = gi - w
            ok_end = valid[:, now] & valid[:, lag]
            interior = np.zeros(n_d, dtype=np.bool_)
            if w > 1:
                interior = unexp[:, lag + 1 : now].any(axis=1)
            good = ok_end & (~interior)
            feat[good, gi] = LOG_BPS * np.log(mid[good, now] / mid[good, lag])
        out[int(w)] = feat
    return out


def _clock_bar_indices() -> np.ndarray:
    return np.array([bar_index_for_available_t(t) for t in CLOCK_MINS], dtype=np.int32)


def basket_maps(sectors: dict[str, Any]) -> dict[str, np.ndarray]:
    rows = list(sectors.get("rows") or sectors.get("mapping_rows") or [])
    symbols = [r["symbol"] for r in rows]
    pos = {s: i for i, s in enumerate(symbols)}
    out: dict[str, np.ndarray] = {"MKT105_EQW": np.arange(len(symbols), dtype=np.int32)}
    by_sec: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        by_sec[str(r["sector_id"])].append(pos[r["symbol"]])
    for scope in TARGET_SCOPES:
        if scope == "MKT105_EQW":
            continue
        sid = scope.replace("TSE33_", "").replace("_EQW", "")
        out[scope] = np.array(by_sec.get(sid) or [], dtype=np.int32)
    return out


def target_return_and_lag(
    *,
    close: np.ndarray,
    valid: np.ndarray,
    idx: np.ndarray,
    horizon: int,
    is_mkt: bool,
    min_frac: float,
    min_n: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """EQW future h and past-5m control. idx = constituent symbol indices."""
    n_s_all, n_d, n_g = close.shape
    bars = _clock_bar_indices()
    t0 = bars
    t1 = bars + int(horizon)
    tlag = bars - int(PAST_CONTROL_MIN)
    sub_c = close[idx]
    sub_v = valid[idx]
    n_d = close.shape[1]
    n_g = close.shape[2]
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
        c0 = sub_c[:, :, t0[ci]]
        c1 = sub_c[:, :, t1[ci]]
        cl = sub_c[:, :, tlag[ci]]
        both = sub_v[:, :, t0[ci]] & sub_v[:, :, t1[ci]]
        both_l = sub_v[:, :, t0[ci]] & sub_v[:, :, tlag[ci]]
        n = both.sum(axis=0)
        n_ok[:, ci] = n.astype(np.int16)
        enough = n >= thr
        enough_l = both_l.sum(axis=0) >= thr
        keep = enough & enough_l
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


def symbol_horizon_return(*, close: np.ndarray, valid: np.ndarray, horizon: int) -> np.ndarray:
    n_s, n_d, n_g = close.shape
    bars = _clock_bar_indices()
    out = np.full((n_s, n_d, N_CLOCK), np.nan, dtype=np.float64)
    for ci in range(N_CLOCK):
        if not clock_ok_for_horizon(CLOCK_MINS[ci], horizon):
            continue
        i0 = bars[ci]
        i1 = bars[ci] + int(horizon)
        ok = valid[:, :, i0] & valid[:, :, i1]
        out[:, :, ci] = _logret(close[:, :, i1], close[:, :, i0])
        out[~ok, ci] = np.nan
    return out


def bh_qvalues(p: np.ndarray) -> np.ndarray:
    m = int(p.size)
    q = np.ones(m, dtype=np.float64)
    if m == 0:
        return q
    order = np.argsort(p)
    prev = 1.0
    for rank in range(m, 0, -1):
        i = int(order[rank - 1])
        val = float(p[i]) * m / rank
        prev = min(prev, val)
        q[i] = prev
    return np.clip(q, 0.0, 1.0)


def _w_ols_fe(y: np.ndarray, x: np.ndarray, minute_ids: np.ndarray, w: np.ndarray, n_min: int) -> np.ndarray | None:
    mask = (w > 0) & np.isfinite(y) & np.isfinite(x).all(axis=1)
    if int(mask.sum()) < (x.shape[1] + 8):
        return None
    yy = y[mask]
    xx = x[mask]
    mm = minute_ids[mask]
    ww = w[mask].astype(np.float64)
    sw = np.bincount(mm, weights=ww, minlength=n_min).astype(np.float64)
    den = np.maximum(sw[mm], 1e-15)
    yt = yy - np.bincount(mm, weights=ww * yy, minlength=n_min)[mm] / den
    k = xx.shape[1]
    xt = np.empty_like(xx)
    for j in range(k):
        xt[:, j] = xx[:, j] - np.bincount(mm, weights=ww * xx[:, j], minlength=n_min)[mm] / den
    wxt = xt * ww[:, None]
    xtx = xt.T @ wxt
    xty = wxt.T @ yt
    try:
        beta = np.linalg.solve(xtx, xty)
    except np.linalg.LinAlgError:
        beta, *_ = np.linalg.lstsq(xtx, xty, rcond=None)
    if not np.all(np.isfinite(beta)):
        return None
    return beta


def date_block_bootstrap(
    *,
    y: np.ndarray,
    x: np.ndarray,
    date_ids: np.ndarray,
    minute_ids: np.ndarray,
    n_dates: int,
    n_boot: int = BOOTSTRAP_N,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    n_min = int(minute_ids.max()) + 1 if minute_ids.size else 1
    ones = np.ones(y.shape[0], dtype=np.float64)
    b0 = _w_ols_fe(y, x, minute_ids, ones, n_min)
    if b0 is None:
        return {"ok": False, "beta": None}
    rng = np.random.RandomState(int(seed))
    boots = np.full((int(n_boot), x.shape[1]), np.nan, dtype=np.float64)
    for i in range(int(n_boot)):
        draw = rng.randint(0, int(n_dates), size=int(n_dates))
        w = np.bincount(draw, minlength=int(n_dates)).astype(np.float64)
        ww = w[date_ids]
        bb = _w_ols_fe(y, x, minute_ids, ww, n_min)
        if bb is not None:
            boots[i] = bb
    col = boots[:, 0]
    col = col[np.isfinite(col)]
    if col.size < max(50, int(n_boot) // 4):
        return {"ok": False, "beta": b0, "boot_n": int(col.size)}
    ci_lo, ci_hi = np.percentile(col, [2.5, 97.5])
    frac_pos = float(np.mean(col >= 0.0))
    frac_neg = float(np.mean(col <= 0.0))
    p = 2.0 * min(frac_pos, frac_neg)
    p = min(1.0, max(p, 1.0 / float(n_boot)))
    return {
        "ok": True,
        "beta": b0,
        "b_fx": float(b0[0]),
        "ci_lo": float(ci_lo),
        "ci_hi": float(ci_hi),
        "p_boot": float(p),
        "boot_n": int(col.size),
        "ci_excludes_0": bool((float(ci_lo) > 0.0) or (float(ci_hi) < 0.0)),
    }


def quintile_bounds(fx: np.ndarray) -> dict[str, Any]:
    v = fx[np.isfinite(fx)]
    if v.size < 20:
        return {"ok": False}
    q20, q40, q60, q80 = np.percentile(v, [20, 40, 60, 80])
    payload = {"Q20": float(q20), "Q40": float(q40), "Q60": float(q60), "Q80": float(q80)}
    payload["boundary_sha"] = sha256_obj(payload)
    payload["ok"] = True
    payload["n"] = int(v.size)
    return payload


def q5_minus_q1(y: np.ndarray, fx: np.ndarray, bounds: dict[str, Any]) -> float | None:
    if not bounds.get("ok"):
        return None
    ok = np.isfinite(y) & np.isfinite(fx)
    q1 = y[ok & (fx <= float(bounds["Q20"]))]
    q5 = y[ok & (fx >= float(bounds["Q80"]))]
    if q1.size < 5 or q5.size < 5:
        return None
    return float(np.mean(q5) - np.mean(q1))


def point_b_fx(y: np.ndarray, fx: np.ndarray, controls: list[np.ndarray], minute_ids: np.ndarray) -> float | None:
    cols = [fx] + list(controls)
    x = np.column_stack(cols)
    mask = np.isfinite(y) & np.isfinite(x).all(axis=1)
    if int(mask.sum()) < 40:
        return None
    n_min = int(minute_ids.max()) + 1
    b = _w_ols_fe(y[mask], x[mask], minute_ids[mask], np.ones(int(mask.sum()), dtype=np.float64), n_min)
    if b is None:
        return None
    return float(b[0])


def fit_sample(y: np.ndarray, fx: np.ndarray, controls: list[np.ndarray], date_ids: np.ndarray, minute_ids: np.ndarray, n_dates: int) -> dict[str, Any]:
    cols = [fx] + list(controls)
    x = np.column_stack(cols)
    mask = np.isfinite(y) & np.isfinite(x).all(axis=1)
    if int(mask.sum()) < 40:
        return {"ok": False, "n": int(mask.sum())}
    return {
        **date_block_bootstrap(
            y=y[mask],
            x=x[mask],
            date_ids=date_ids[mask],
            minute_ids=minute_ids[mask],
            n_dates=n_dates,
        ),
        "n": int(mask.sum()),
    }


def flatten_obs(arr: np.ndarray) -> np.ndarray:
    return arr.reshape(-1)


def date_minute_ids(n_dates: int) -> tuple[np.ndarray, np.ndarray]:
    date_ids = np.repeat(np.arange(n_dates, dtype=np.int32), N_CLOCK)
    minute_ids = np.tile(np.arange(N_CLOCK, dtype=np.int32), n_dates)
    return date_ids, minute_ids


def month_ids(dates: list[str], n_clock: int = N_CLOCK) -> np.ndarray:
    months = [d[:6] for d in dates]
    uniq = {m: i for i, m in enumerate(sorted(set(months)))}
    ids = np.array([uniq[m] for m in months], dtype=np.int32)
    return np.repeat(ids, n_clock), uniq


def apply_d_gates(
    *,
    rec: dict[str, Any],
    early: dict[str, Any],
    late: dict[str, Any],
    q51: float | None,
    sub_signs: list[int],
    lomo_frac: float | None,
    qval: float,
) -> dict[str, Any]:
    b = rec.get("b_fx")
    d1 = bool(early.get("ok") and late.get("ok") and np.sign(early["b_fx"]) == np.sign(late["b_fx"]) and np.sign(early["b_fx"]) != 0)
    d2 = bool(rec.get("ok") and rec.get("ci_excludes_0"))
    d3 = bool(qval <= FDR_Q)
    d4 = bool(q51 is not None and b is not None and np.sign(q51) == np.sign(b) and np.sign(b) != 0)
    d5 = bool(b is not None and sum(1 for s in sub_signs if s == int(np.sign(b))) >= 4)
    d6 = bool(lomo_frac is not None and lomo_frac >= 0.75)
    rec = dict(rec)
    rec.update(
        {
            "q": float(qval),
            "q5_minus_q1": q51,
            "D1": d1,
            "D2": d2,
            "D3": d3,
            "D4": d4,
            "D5": d5,
            "D6": d6,
            "candidate": bool(d1 and d2 and d3 and d4 and d5 and d6),
            "subgrid_same_sign_n": int(sum(1 for s in sub_signs if b is not None and s == int(np.sign(b)))),
            "lomo_same_sign_frac": lomo_frac,
            "early_b_fx": early.get("b_fx"),
            "late_b_fx": late.get("b_fx"),
        }
    )
    return rec
