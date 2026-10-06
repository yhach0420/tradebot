"""DIR-normalized target path AFTER completed bar T. Trading minutes only. No same-bar range."""
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


def trading_path(rec: dict[str, Any], session_idx: list[int], pos: int, sign: int) -> dict[str, Any]:
    i = session_idx[pos]
    px = rec["c"][i]
    out = {
        "same_bar_outcome": False,
        "ret_5m_bps": None,
        "ret_10m_bps": None,
        "ret_20m_bps": None,
        "mfe_bps": None,
        "mae_bps": None,
        "p20_before_m20": None,
        "p40_before_m20": None,
        "p80_before_m30": None,
        "fwd_n": 0,
        "primary_complete": False,
    }
    if not _finite(px) or float(px) <= 0:
        return out
    s = float(sign)
    base = float(px)
    mfe = mae = None
    t20 = t40 = t80 = None
    rets = {5: None, 10: None, 20: None}
    step = 0
    n_sess = len(session_idx)
    for k in range(1, int(PATH_HORIZON_M) + 1):
        if pos + k >= n_sess:
            break
        j = session_idx[pos + k]
        t = rec["t"][j]
        if str(t) >= SESSION_FLAT:
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
        if t20 is None and mfe is not None and mfe >= 20 and (mae is None or mae > -20):
            t20 = True
        if t20 is None and mae is not None and mae <= -20:
            t20 = False
        if t40 is None and mfe is not None and mfe >= 40 and (mae is None or mae > -20):
            t40 = True
        if t40 is None and mae is not None and mae <= -20:
            t40 = False
        if t80 is None and mfe is not None and mfe >= 80 and (mae is None or mae > -30):
            t80 = True
        if t80 is None and mae is not None and mae <= -30:
            t80 = False
        if _finite(c) and k in rets:
            rets[k] = s * (float(c) / base - 1.0) * 1e4
        step = k
    out.update(
        {
            "ret_5m_bps": rets[5],
            "ret_10m_bps": rets[10],
            "ret_20m_bps": rets[20],
            "mfe_bps": mfe,
            "mae_bps": mae,
            "p20_before_m20": t20,
            "p40_before_m20": t40,
            "p80_before_m30": t80,
            "fwd_n": step,
            "primary_complete": rets[int(PRIMARY_HORIZON_M)] is not None,
        }
    )
    return out
