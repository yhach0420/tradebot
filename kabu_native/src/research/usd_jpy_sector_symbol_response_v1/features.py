"""Simple USDJPY features at causal time T. No RSI/EMA/technical mining."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def attach_features(fx: pd.DataFrame) -> pd.DataFrame:
    out = fx.sort_values("ts_utc_ms").reset_index(drop=True).copy()
    mid = pd.to_numeric(out["mid_close"], errors="coerce").to_numpy(dtype=float)
    ts = pd.to_numeric(out["ts_utc_ms"], errors="coerce").to_numpy(dtype=np.int64)
    dt = np.diff(ts, prepend=ts[0] if len(ts) else 0)
    consec = np.ones(len(ts), dtype=bool)
    if len(ts):
        consec[0] = False
        consec[1:] = dt[1:] == 60_000

    def ret_bps(lag: int) -> np.ndarray:
        r = np.full(len(mid), np.nan, dtype=float)
        if len(mid) <= lag:
            return r
        prev = mid[:-lag]
        now = mid[lag:]
        ok = np.isfinite(prev) & np.isfinite(now) & (prev != 0)
        chain = np.ones(len(now), dtype=bool)
        for j in range(lag):
            chain &= consec[lag - j : len(mid) - j]
        ok &= chain
        r[lag:][ok] = (now[ok] / prev[ok] - 1.0) * 10_000.0
        return r

    out["fx_ret_1m_bps"] = ret_bps(1)
    out["fx_ret_3m_bps"] = ret_bps(3)
    out["fx_ret_5m_bps"] = ret_bps(5)
    out["fx_impulse_abs_1m"] = np.abs(out["fx_ret_1m_bps"].to_numpy(dtype=float))
    r1 = out["fx_ret_1m_bps"].to_numpy(dtype=float)
    slope = np.full(len(r1), np.nan)
    if len(r1) >= 3:
        slope[3:] = out["fx_ret_3m_bps"].to_numpy(dtype=float)[3:] / 3.0
    out["fx_slope_3m"] = slope
    rv = np.full(len(r1), np.nan)
    for i in range(5, len(r1)):
        w = r1[i - 4 : i + 1]
        if np.all(np.isfinite(w)):
            rv[i] = float(np.std(w, ddof=0))
    out["fx_rv_5m"] = rv
    out["fx_spread"] = pd.to_numeric(out["spread_close"], errors="coerce")
    out["fx_mid"] = mid
    return out


def asof_indices(available_at_ms: np.ndarray, decision_ms: np.ndarray) -> np.ndarray:
    """Last FX bar with available_at <= decision time. -1 if none."""
    idx = np.searchsorted(available_at_ms, decision_ms, side="right") - 1
    return idx.astype(np.int64)


def take(arr: np.ndarray, idx: np.ndarray) -> np.ndarray:
    out = np.full(idx.shape, np.nan, dtype=float)
    ok = idx >= 0
    out[ok] = arr[idx[ok]]
    return out
