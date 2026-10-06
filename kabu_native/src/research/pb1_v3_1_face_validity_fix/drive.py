"""CLEAN_OPENING_DRIVE_V2. Causal 09:00–09:14 only. 0.50 OR net not searched."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.impulse import opening_path
from research.pb1_v3_1_face_validity_fix import DRIVE_NET_OR_FRAC


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def classify_drive(path: dict[str, Any]) -> dict[str, Any]:
    """CLEAN_OPENING_DRIVE_V2 or NON_DIRECTIONAL_OPEN. No post-09:14 returns."""
    if not path.get("ok"):
        return {"state": "NON_DIRECTIONAL_OPEN", "DIR": 0, "reason": "or_incomplete"}
    rng = path.get("or_range")
    if not _finite(rng) or float(rng) <= 0:
        return {"state": "NON_DIRECTIONAL_OPEN", "DIR": 0, "reason": "or_range_zero"}
    rng = float(rng)
    open_px = path.get("open_0900")
    c15 = path.get("close_0914")
    c5 = path.get("close_0904")
    mid = path.get("or_mid")
    mfe = path.get("mfe_up")
    mae = path.get("mae_dn")
    net15 = path.get("net_or15")
    net5 = path.get("net_5m")
    frac = float(DRIVE_NET_OR_FRAC)
    if not (_finite(open_px) and _finite(c15) and _finite(mid) and _finite(mfe) and _finite(mae) and _finite(net15)):
        return {"state": "NON_DIRECTIONAL_OPEN", "DIR": 0, "reason": "missing_path"}

    bull = (
        float(c15) > float(open_px)
        and float(c15) >= float(mid)
        and float(net15) >= frac * rng
        and float(mfe) > float(mae)
        and _finite(c5)
        and _finite(net5)
        and float(net5) >= 0.0
    )
    bear = (
        float(c15) < float(open_px)
        and float(c15) <= float(mid)
        and float(net15) <= -frac * rng
        and float(mae) > float(mfe)
        and _finite(c5)
        and _finite(net5)
        and float(net5) <= 0.0
    )
    if bull and not bear:
        return {
            "state": "CLEAN_OPENING_DRIVE_V2",
            "DIR": 1,
            "reason": "bull_drive",
            "net15_frac": float(net15) / rng,
            "drive_net_or_frac": frac,
            "drive_frac_searched": False,
        }
    if bear and not bull:
        return {
            "state": "CLEAN_OPENING_DRIVE_V2",
            "DIR": -1,
            "reason": "bear_drive",
            "net15_frac": float(net15) / rng,
            "drive_net_or_frac": frac,
            "drive_frac_searched": False,
        }
    return {
        "state": "NON_DIRECTIONAL_OPEN",
        "DIR": 0,
        "reason": "not_clean_opening_drive_v2",
        "net15_frac": float(net15) / rng if rng else None,
        "drive_net_or_frac": frac,
        "drive_frac_searched": False,
    }


def opening_impulse_lost(*, sign: int, close: float, or_high: float, or_low: float) -> bool:
    """Completed close entered the opposite half of frozen OR15."""
    if not (_finite(close) and _finite(or_high) and _finite(or_low) and float(or_high) > float(or_low)):
        return False
    mid = 0.5 * (float(or_high) + float(or_low))
    if int(sign) > 0:
        return float(close) < mid
    return float(close) > mid


_ = opening_path
