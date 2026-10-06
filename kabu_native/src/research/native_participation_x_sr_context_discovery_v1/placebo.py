"""Deterministic ±1.0 ATR20 placebo bands. No redraw. Diagnostic only."""
from __future__ import annotations

import hashlib
from typing import Any

from research.native_participation_x_sr_context_discovery_v1 import PLACEBO_ATR


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _overlap(lo_a: float, hi_a: float, lo_b: float, hi_b: float) -> bool:
    return max(lo_a, lo_b) <= min(hi_a, hi_b)


def shift_sign(symbol: str, date: str) -> int:
    raw = hashlib.sha256(f"{symbol}|{date}".encode("utf-8")).hexdigest()
    return 1 if int(raw, 16) % 2 == 0 else -1


def placebo_band(z: dict[str, Any], *, atr: float, symbol: str, date: str, occupied: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not _finite(atr) or float(atr) <= 0:
        return None
    sgn = shift_sign(symbol, date)
    shift = float(sgn) * float(PLACEBO_ATR) * float(atr)
    lo = float(z["lo"]) + shift
    hi = float(z["hi"]) + shift
    for o in occupied:
        if _overlap(lo, hi, float(o["lo"]), float(o["hi"])):
            return None
    role = str(z.get("role") or "")
    return {
        "zone_id": f"placebo:{z.get('zone_id')}:{round(lo, 2)}",
        "role": role,
        "lo": lo,
        "hi": hi,
        "center": float(z.get("center") or 0) + shift,
        "selection_slot": z.get("selection_slot"),
        "selection_label": z.get("selection_label"),
        "placebo": True,
        "placebo_shift_sign": sgn,
    }
