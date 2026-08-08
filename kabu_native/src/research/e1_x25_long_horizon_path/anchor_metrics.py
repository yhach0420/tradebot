"""Long-horizon as-of path metrics for one anchor (CurrentPrice events only)."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from . import (
    DOWNSIDE_BPS,
    FIRST_TOUCH,
    FRESHNESS_PRIMARY_SEC,
    HORIZONS,
    UPSIDE_BPS,
)


def _asof_idx(times: np.ndarray, tgt: float) -> int:
    return int(np.searchsorted(times, tgt, side="right") - 1)


def compute_anchor_metrics(
    *,
    times: np.ndarray,
    prices: np.ndarray,
    entry_epoch: float,
    entry_price: float,
    sess_end: float,
) -> dict[str, Any]:
    """
    Compute long-horizon path metrics from event path including as-of tick at/before entry.
    times/prices must start at i0 (as-of at entry) through sess_end.
    """
    out: dict[str, Any] = {"ok": False}
    if times.size == 0 or entry_price <= 0:
        return out

    # path indices from entry as-of
    i0 = 0
    lim = min(sess_end, times[-1])
    # returns / MFE / MAE per horizon
    rem = sess_end - entry_epoch
    for h in HORIZONS:
        key = f"{h}s"
        if rem + 1e-9 < h:
            out[f"eligible_{key}"] = False
            out[f"return_{key}_bps"] = np.nan
            out[f"MFE_{key}_bps"] = np.nan
            out[f"MAE_{key}_bps"] = np.nan
            out[f"censored_{key}"] = True
            continue
        tgt = entry_epoch + h
        i1 = _asof_idx(times, tgt)
        if i1 < i0:
            out[f"eligible_{key}"] = False
            out[f"return_{key}_bps"] = np.nan
            out[f"MFE_{key}_bps"] = np.nan
            out[f"MAE_{key}_bps"] = np.nan
            out[f"censored_{key}"] = True
            continue
        age = tgt - float(times[i1])
        out[f"price_age_{key}_sec"] = age
        if age > FRESHNESS_PRIMARY_SEC:
            # primary missing; still store raw for sensitivity elsewhere
            out[f"eligible_{key}"] = True
            out[f"fresh_ok_{key}"] = False
            out[f"return_{key}_bps"] = np.nan
            out[f"MFE_{key}_bps"] = np.nan
            out[f"MAE_{key}_bps"] = np.nan
            out[f"censored_{key}"] = False
            # sensitivity unrestricted stored separately
            ret_u = (float(prices[i1]) / entry_price - 1.0) * 10000.0
            win = prices[i0: i1 + 1]
            out[f"return_{key}_bps_unrestricted"] = ret_u
            out[f"MFE_{key}_bps_unrestricted"] = float(np.max(win) / entry_price - 1.0) * 10000.0
            out[f"MAE_{key}_bps_unrestricted"] = float(np.min(win) / entry_price - 1.0) * 10000.0
            continue
        out[f"eligible_{key}"] = True
        out[f"fresh_ok_{key}"] = True
        out[f"censored_{key}"] = False
        ret = (float(prices[i1]) / entry_price - 1.0) * 10000.0
        win = prices[i0: i1 + 1]
        mfe = float(np.max(win) / entry_price - 1.0) * 10000.0
        mae = float(np.min(win) / entry_price - 1.0) * 10000.0
        out[f"return_{key}_bps"] = ret
        out[f"MFE_{key}_bps"] = mfe
        out[f"MAE_{key}_bps"] = mae
        # giveback
        imfe = i0 + int(np.argmax(win))
        mfe_px = float(prices[imfe])
        after = prices[imfe: i1 + 1]
        max_gb = float((mfe_px - float(np.min(after))) / entry_price * 10000.0) if after.size else 0.0
        term_gb = float((mfe_px - float(prices[i1])) / entry_price * 10000.0)
        out[f"time_to_MFE_{key}_sec"] = float(times[imfe] - entry_epoch)
        out[f"max_giveback_after_MFE_{key}_bps"] = max_gb
        out[f"terminal_giveback_from_MFE_{key}_bps"] = term_gb

    # session close
    key = "session"
    i1 = _asof_idx(times, sess_end)
    if i1 >= i0:
        age = sess_end - float(times[i1])
        out[f"price_age_{key}_sec"] = age
        out[f"eligible_{key}"] = True
        out[f"censored_{key}"] = False
        if age <= FRESHNESS_PRIMARY_SEC:
            out[f"fresh_ok_{key}"] = True
            win = prices[i0: i1 + 1]
            out[f"return_{key}_bps"] = (float(prices[i1]) / entry_price - 1.0) * 10000.0
            out[f"MFE_{key}_bps"] = float(np.max(win) / entry_price - 1.0) * 10000.0
            out[f"MAE_{key}_bps"] = float(np.min(win) / entry_price - 1.0) * 10000.0
            imfe = i0 + int(np.argmax(win))
            mfe_px = float(prices[imfe])
            after = prices[imfe: i1 + 1]
            out[f"time_to_MFE_{key}_sec"] = float(times[imfe] - entry_epoch)
            out[f"max_giveback_after_MFE_{key}_bps"] = float((mfe_px - float(np.min(after))) / entry_price * 10000.0)
            out[f"terminal_giveback_from_MFE_{key}_bps"] = float((mfe_px - float(prices[i1])) / entry_price * 10000.0)
        else:
            out[f"fresh_ok_{key}"] = False
            out[f"return_{key}_bps"] = np.nan
            out[f"MFE_{key}_bps"] = np.nan
            out[f"MAE_{key}_bps"] = np.nan
    else:
        out[f"eligible_{key}"] = False
        out[f"censored_{key}"] = True

    # walk full path to sess_end for reach / first-touch / pre-rise
    lim_t = sess_end
    rets = (prices / entry_price - 1.0) * 10000.0
    offs = times - entry_epoch

    for up in UPSIDE_BPS:
        reached = False
        t_reach = np.nan
        px_reach = np.nan
        pre_mae = np.nan
        for j in range(times.size):
            if times[j] > lim_t + 1e-12:
                break
            if rets[j] >= up - 1e-12:
                reached = True
                t_reach = float(offs[j])
                px_reach = float(prices[j])
                pre_mae = float(np.min(rets[i0: j + 1]))
                break
        out[f"up_{up}_reached"] = reached
        out[f"up_{up}_time_sec"] = t_reach
        out[f"up_{up}_price"] = px_reach
        out[f"pre_reach_MAE_{up}_bps"] = pre_mae if reached else np.nan
        out[f"up_{up}_status"] = "REACHED" if reached else "NOT_REACHED"

    for dn in DOWNSIDE_BPS:
        reached = False
        t_reach = np.nan
        px_reach = np.nan
        for j in range(times.size):
            if times[j] > lim_t + 1e-12:
                break
            if rets[j] <= -dn + 1e-12:
                reached = True
                t_reach = float(offs[j])
                px_reach = float(prices[j])
                break
        out[f"dn_{dn}_reached"] = reached
        out[f"dn_{dn}_time_sec"] = t_reach
        out[f"dn_{dn}_price"] = px_reach

    for up, dn in FIRST_TOUCH:
        t_up = t_dn = None
        for j in range(times.size):
            if times[j] > lim_t + 1e-12:
                break
            if t_up is None and rets[j] >= up - 1e-12:
                t_up = float(offs[j])
            if t_dn is None and rets[j] <= -dn + 1e-12:
                t_dn = float(offs[j])
            if t_up is not None and t_dn is not None:
                break
        if t_up is not None and (t_dn is None or t_up <= t_dn):
            res, tt = "UP_FIRST", t_up
        elif t_dn is not None and (t_up is None or t_dn < t_up):
            res, tt = "DOWN_FIRST", t_dn
        else:
            res, tt = "NEITHER", np.nan
        out[f"ft_{up}_{dn}_result"] = res
        out[f"ft_{up}_{dn}_time_sec"] = tt

    out["ok"] = True
    out["remaining_to_session_sec"] = rem
    return out
