"""Executable next-open path. No same-bar fill. Structural invalidation, not a fixed bps stop."""
from __future__ import annotations

from typing import Any

from research.sma5_25_75_trend_pullback_playbook_discovery_v1 import PATH_HORIZON_M, PRIMARY_HORIZON_M, SESSION_FLAT


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def execution_path(
    rec: dict[str, Any],
    session_idx: list[int],
    pos: int,
    sign: int,
    invalidation: Any,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "same_bar_outcome": False,
        "entry_ok": False,
        "signal_close": None,
        "next_open": None,
        "signal_to_entry_bps": None,
        "risk_bps": None,
        "ret_5m_bps": None,
        "ret_10m_bps": None,
        "ret_20m_bps": None,
        "mfe_bps": None,
        "mae_bps": None,
        "time_to_mfe": None,
        "time_to_invalidation": None,
        "mfe_over_risk": None,
        "invalidated": False,
        "fwd_n": 0,
        "primary_complete": False,
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
    if _finite(invalidation) and float(invalidation) > 0:
        risk = s * (base - float(invalidation)) / base * 1e4
        if risk <= 0:
            out["signal_close"] = float(c_sig)
            out["next_open"] = base
            out["signal_to_entry_bps"] = s * (base / float(c_sig) - 1.0) * 1e4
            out["risk_bps"] = float(risk)
            return out
        out["risk_bps"] = float(risk)
    out["entry_ok"] = True
    out["signal_close"] = float(c_sig)
    out["next_open"] = base
    out["signal_to_entry_bps"] = s * (base / float(c_sig) - 1.0) * 1e4
    mfe = mae = None
    t_mfe = None
    t_inv = None
    rets = {5: None, 10: None, 20: None}
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
            t_mfe = k
        if mae is None or lo_s < mae:
            mae = lo_s
        if t_inv is None and _finite(invalidation) and _finite(c):
            if s * (float(c) - float(invalidation)) < 0:
                t_inv = k
        if _finite(c) and k in rets:
            rets[k] = s * (float(c) / base - 1.0) * 1e4
        step = k
    risk = out.get("risk_bps")
    out.update(
        {
            "ret_5m_bps": rets[5],
            "ret_10m_bps": rets[10],
            "ret_20m_bps": rets[20],
            "mfe_bps": mfe,
            "mae_bps": mae,
            "time_to_mfe": t_mfe,
            "time_to_invalidation": t_inv,
            "mfe_over_risk": None if mfe is None or not _finite(risk) or float(risk) <= 0 else float(mfe) / float(risk),
            "invalidated": t_inv is not None,
            "fwd_n": step,
            "primary_complete": rets[int(PRIMARY_HORIZON_M)] is not None,
        }
    )
    return out
