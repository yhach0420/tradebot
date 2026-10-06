"""Next-bar open execution path. No same-bar fill. No close[T] fill."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.cross_sectional_peer_propagation_discovery_v1 import PATH_HORIZON_M, PRIMARY_HORIZON_M, SESSION_FLAT


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def execution_path(rec: dict[str, Any], session_idx: list[int], pos: int, sign: int) -> dict[str, Any]:
    out: dict[str, Any] = {
        "same_bar_outcome": False,
        "entry_ok": False,
        "signal_close": None,
        "next_open": None,
        "signal_to_entry_bps": None,
        "xo_ret_1m_bps": None,
        "xo_ret_3m_bps": None,
        "xo_ret_5m_bps": None,
        "xo_ret_10m_bps": None,
        "xo_ret_20m_bps": None,
        "xo_mfe_bps": None,
        "xo_mae_bps": None,
        "xo_fwd_n": 0,
        "xo_primary_complete": False,
    }
    n_sess = len(session_idx)
    if pos < 0 or pos + 1 >= n_sess:
        return out
    i_sig = session_idx[pos]
    j_entry = session_idx[pos + 1]
    if str(rec["t"][j_entry]) >= SESSION_FLAT:
        return out
    c_sig = rec["c"][i_sig]
    o_next = rec["o"][j_entry]
    if not (_finite(c_sig) and _finite(o_next)) or float(c_sig) <= 0 or float(o_next) <= 0:
        return out
    s = float(sign)
    base = float(o_next)
    out["entry_ok"] = True
    out["signal_close"] = float(c_sig)
    out["next_open"] = base
    out["signal_to_entry_bps"] = s * (base / float(c_sig) - 1.0) * 1e4
    mfe = mae = None
    rets = {1: None, 3: None, 5: None, 10: None, 20: None}
    step = 0
    for k in range(1, int(PATH_HORIZON_M) + 1):
        if pos + k >= n_sess:
            break
        j = session_idx[pos + k]
        if str(rec["t"][j]) >= SESSION_FLAT:
            break
        h, l, c = rec["h"][j], rec["l"][j], rec["c"][j]
        if not (_finite(h) and _finite(l)):
            continue
        up = (float(h) / base - 1.0) * 1e4
        dn = (float(l) / base - 1.0) * 1e4
        hi_s, lo_s = s * up, s * dn
        if hi_s < lo_s:
            hi_s, lo_s = lo_s, hi_s
        if mfe is None or hi_s > mfe:
            mfe = hi_s
        if mae is None or lo_s < mae:
            mae = lo_s
        if _finite(c) and k in rets:
            rets[k] = s * (float(c) / base - 1.0) * 1e4
        step = k
    out.update(
        {
            "xo_ret_1m_bps": rets[1],
            "xo_ret_3m_bps": rets[3],
            "xo_ret_5m_bps": rets[5],
            "xo_ret_10m_bps": rets[10],
            "xo_ret_20m_bps": rets[20],
            "xo_mfe_bps": mfe,
            "xo_mae_bps": mae,
            "xo_fwd_n": step,
            "xo_primary_complete": rets[int(PRIMARY_HORIZON_M)] is not None,
        }
    )
    _ = np.nan
    return out


def remaining_from_close(rec: dict[str, Any], session_idx: list[int], pos: int, sign: int) -> dict[str, Any]:
    from research.cross_sectional_peer_propagation_discovery_v1.outcomes import trading_path

    return trading_path(rec, session_idx, pos, sign)
