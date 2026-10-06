"""Signed forward path after the next bar open. Not used to select rules. Not a 5pp continuation gate."""
from __future__ import annotations

from typing import Any

from research.cause_first_mechanism_discovery_v1.clock import in_lunch
from research.support_resistance_first_interaction_matched_causal_test_v1 import SESSION_FLAT


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def next_entry_i(rec: dict[str, Any], i: int) -> int | None:
    n = int(rec["n"])
    for j in range(i + 1, n):
        t = rec["t"][j]
        if in_lunch(t):
            continue
        if str(t) >= SESSION_FLAT:
            return None
        if _finite(rec["o"][j]) and float(rec["o"][j]) > 0:
            return j
    return None


def signed_path(rec: dict[str, Any], i_event: int, sign: int) -> dict[str, Any]:
    j = next_entry_i(rec, i_event)
    out = {
        "entry_i": j,
        "entry_t": rec["t"][j] if j is not None else None,
        "entry_open": float(rec["o"][j]) if j is not None else None,
        "sign": int(sign),
        "mfe_bps": None,
        "mae_bps": None,
        "end_bps": None,
        "mfe_before_mae": None,
        "p20_before_m20": None,
        "p40_before_m20": None,
        "p80_before_m30": None,
        "time_to_failure_min": None,
        "time_to_extension_min": None,
        "fwd_n": 0,
        "payoff_asymmetry": None,
    }
    if j is None:
        return out
    px = float(rec["o"][j])
    if not _finite(px) or px <= 0:
        return out
    s = float(sign)
    mfe = None
    mae = None
    mfe_i = mae_i = None
    end = None
    t20 = t40 = t80 = None
    t_fail20 = t_fail30 = None
    n = int(rec["n"])
    step = 0
    for k in range(j, n):
        t = rec["t"][k]
        if in_lunch(t):
            continue
        h, l, c = float(rec["h"][k]), float(rec["l"][k]), float(rec["c"][k])
        if not _finite(h) or not _finite(l):
            continue
        up = (h / px - 1.0) * 1e4
        dn = (l / px - 1.0) * 1e4
        a, b = s * up, s * dn
        hi_s, lo_s = max(a, b), min(a, b)
        if mfe is None or hi_s > mfe:
            mfe, mfe_i = hi_s, step
        if mae is None or lo_s < mae:
            mae, mae_i = lo_s, step
        if _finite(c):
            end = s * (c / px - 1.0) * 1e4
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
        if t_fail20 is None and mae is not None and mae <= -20:
            t_fail20 = step
        if t_fail30 is None and mae is not None and mae <= -30:
            t_fail30 = step
        step += 1
        if str(t) >= SESSION_FLAT:
            break
    out.update(
        {
            "mfe_bps": mfe,
            "mae_bps": mae,
            "end_bps": end,
            "mfe_before_mae": bool(mfe_i is not None and (mae_i is None or int(mfe_i) <= int(mae_i))) if mfe is not None else None,
            "p20_before_m20": t20,
            "p40_before_m20": t40,
            "p80_before_m30": t80,
            "time_to_failure_min": t_fail20,
            "time_to_extension_min": mfe_i if mfe is not None and mfe >= 20 else None,
            "fwd_n": step,
            "payoff_asymmetry": (float(mfe) / abs(float(mae))) if mfe is not None and mae is not None and mae != 0 else None,
        }
    )
    return out
