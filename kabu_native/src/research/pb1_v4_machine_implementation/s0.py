"""S0 DISTINCTIVE_OPENING_ACTIVITY_V4. Prior-only. Not old GENUINELY_IN_PLAY."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_machine_implementation import S0_ATR_RANGE_MIN, S0_GAP_ATR_MIN, S0_GAP_RANGE_MIN, S0_RANGE_MIN


def _max_range(bars_open: list[dict[str, Any]]) -> float | None:
    rs = [float(b["range"]) for b in bars_open if _finite(b.get("range"))]
    return max(rs) if rs else None


def classify_s0(
    *,
    bars_open: list[dict[str, Any]],
    normal_opening_5m: Any,
    abs_gap_atr: Any,
    atr20: Any = None,
    xs_rank_pct: Any = None,
    tv_0915_pctl: Any = None,
) -> dict[str, Any]:
    """Price movement vs own normal is required. xs/TV never sufficient alone."""
    mx_abs = _max_range(bars_open)
    mx = (
        float(mx_abs) / float(normal_opening_5m)
        if _finite(mx_abs) and _finite(normal_opening_5m) and float(normal_opening_5m) > 0
        else None
    )
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
        reason = "opening_5m_range_vs_own_normal"
    elif atr_ok:
        ok = True
        reason = "opening_5m_range_vs_atr20_until_own_normal_exists"
    elif gap_ok:
        ok = True
        reason = "gap_plus_visible_opening_range"
    return {
        "ok": ok,
        "reason": reason,
        "max_range_over_normal_5m": mx,
        "max_range_over_atr20": mx_atr,
        "abs_gap_atr": float(abs_gap_atr) if _finite(abs_gap_atr) else None,
        "xs_rank_pct": xs_rank_pct,
        "tv_0915_pctl": tv_0915_pctl,
        "xs_alone_sufficient": False,
        "tv_alone_sufficient": False,
        "old_genuinely_in_play_reused": False,
        "S0_RANGE_MIN": float(S0_RANGE_MIN),
        "S0_GAP_ATR_MIN": float(S0_GAP_ATR_MIN),
        "S0_GAP_RANGE_MIN": float(S0_GAP_RANGE_MIN),
        "S0_ATR_RANGE_MIN": float(S0_ATR_RANGE_MIN),
    }
