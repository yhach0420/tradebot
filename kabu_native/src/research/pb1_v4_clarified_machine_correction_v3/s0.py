"""S0 WHY_THIS_STOCK. Same-clock range vs own prior clocks. Not first-bar-only."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_clarified_machine_correction_v3 import (
    S0_ATR_RANGE_MIN,
    S0_GAP_ATR_MIN,
    S0_GAP_RANGE_MIN,
    S0_RANGE_MIN,
)


def classify_s0(
    *,
    bars_open: list[dict[str, Any]],
    clock_snap: dict[str, Any],
    abs_gap_atr: Any,
    atr20: Any = None,
    xs_rank_pct: Any = None,
    tv_0915_pctl: Any = None,
) -> dict[str, Any]:
    clocks = list(clock_snap.get("same_clock") or [])
    ratios: list[float] = []
    for i, b in enumerate(bars_open[:3]):
        med = clocks[i].get("median") if i < len(clocks) else None
        rng = b.get("range")
        if _finite(rng) and _finite(med) and float(med) > 0:
            ratios.append(float(rng) / float(med))
    mx = max(ratios) if ratios else None
    rs = [float(b["range"]) for b in bars_open if _finite(b.get("range"))]
    mx_abs = max(rs) if rs else None
    mx_atr = float(mx_abs) / float(atr20) if _finite(mx_abs) and _finite(atr20) and float(atr20) > 0 else None
    range_ok = _finite(mx) and float(mx) >= float(S0_RANGE_MIN)
    atr_ok = (not _finite(mx)) and _finite(mx_atr) and float(mx_atr) >= float(S0_ATR_RANGE_MIN)
    gap_ok = (
        _finite(abs_gap_atr)
        and float(abs_gap_atr) >= float(S0_GAP_ATR_MIN)
        and (
            (_finite(mx) and float(mx) >= float(S0_GAP_RANGE_MIN))
            or ((not _finite(mx)) and _finite(mx_atr) and float(mx_atr) >= float(S0_ATR_RANGE_MIN))
        )
    )
    reason = "no_distinctive_price_movement"
    ok = False
    if range_ok:
        ok = True
        reason = "opening_5m_range_vs_same_clock"
    elif atr_ok:
        ok = True
        reason = "opening_5m_range_vs_atr20_until_same_clock_exists"
    elif gap_ok:
        ok = True
        reason = "gap_plus_visible_opening_range"
    return {
        "ok": ok,
        "reason": reason,
        "max_range_over_same_clock": mx,
        "max_range_over_atr20": mx_atr,
        "abs_gap_atr": float(abs_gap_atr) if _finite(abs_gap_atr) else None,
        "xs_rank_pct": xs_rank_pct,
        "tv_0915_pctl": tv_0915_pctl,
        "xs_alone_sufficient": False,
        "tv_alone_sufficient": False,
        "first_bar_only_normalizer": False,
        "S0_RANGE_MIN": float(S0_RANGE_MIN),
        "S0_GAP_ATR_MIN": float(S0_GAP_ATR_MIN),
        "S0_GAP_RANGE_MIN": float(S0_GAP_RANGE_MIN),
        "S0_ATR_RANGE_MIN": float(S0_ATR_RANGE_MIN),
    }
