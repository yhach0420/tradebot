"""
Phase 153d: Price / tick-ratio risk helpers for shadow universe filtering.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from research.low_price_risk_review import jpx_tick_size_yen, tick_ratio_pct

UNIVERSE_MODE = "core10-dynamic40-price-risk-filter-shadow"

MIN_CLOSE_PRICE = 300.0
MAX_TICK_RATIO_PCT = 5.0

DYNAMIC_SELECTED_REASON = "vol_liq_dynamic40_price_risk_filtered"
CORE_WARNING_REASON = "core_price_risk_warning"


def _as_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def close_from_feature(row: Mapping[str, Any]) -> float:
    return float(_as_float(row.get("close")) or 0.0)


def price_risk_fields(*, close_price: float) -> dict[str, Any]:
    tick = jpx_tick_size_yen(close_price) if close_price > 0 else 0.0
    tr = tick_ratio_pct(close_price) if close_price > 0 else 0.0
    return {
        "close_price": round(close_price, 4) if close_price else "",
        "tick_size": tick if close_price > 0 else "",
        "tick_ratio_pct": round(tr, 4) if close_price > 0 else "",
    }


def dynamic_price_risk_fail_reason(*, close_price: float, tick_ratio: float) -> str:
    reasons: list[str] = []
    if close_price <= 0:
        reasons.append("missing_close_price")
    elif close_price < MIN_CLOSE_PRICE:
        reasons.append(f"close_below_{int(MIN_CLOSE_PRICE)}")
    if close_price > 0 and tick_ratio > MAX_TICK_RATIO_PCT:
        reasons.append(f"tick_ratio_above_{MAX_TICK_RATIO_PCT:g}")
    return ";".join(reasons)


def passes_dynamic_price_risk(row: Mapping[str, Any]) -> bool:
    px = close_from_feature(row)
    if px <= 0:
        return False
    tr = tick_ratio_pct(px)
    return px >= MIN_CLOSE_PRICE and tr <= MAX_TICK_RATIO_PCT


def core_price_risk_warning(row: Mapping[str, Any]) -> Optional[str]:
    """Return warning detail if core symbol fails price/tick thresholds (warn only)."""
    px = close_from_feature(row)
    if px <= 0:
        return f"{CORE_WARNING_REASON}:missing_close_price"
    tr = tick_ratio_pct(px)
    parts: list[str] = []
    if px < MIN_CLOSE_PRICE:
        parts.append(f"close_below_{int(MIN_CLOSE_PRICE)}")
    if tr > MAX_TICK_RATIO_PCT:
        parts.append(f"tick_ratio_above_{MAX_TICK_RATIO_PCT:g}")
    if not parts:
        return None
    return f"{CORE_WARNING_REASON}:{';'.join(parts)}"


def enrich_row_price_risk(
    row: Mapping[str, Any],
    feat: Mapping[str, Any],
    *,
    slot: str,
) -> dict[str, Any]:
    px = close_from_feature(feat)
    fields = price_risk_fields(close_price=px)
    tr = float(fields["tick_ratio_pct"] or 0) if fields.get("tick_ratio_pct") != "" else 0.0
    out = {**dict(row), **fields}
    if slot == "core":
        warn = core_price_risk_warning(feat)
        if warn:
            out["price_risk_flag"] = "warning"
            out["price_risk_reason"] = warn
        else:
            out["price_risk_flag"] = "false"
            out["price_risk_reason"] = ""
    else:
        fail = dynamic_price_risk_fail_reason(close_price=px, tick_ratio=tr)
        out["price_risk_flag"] = "false" if not fail else "excluded_dynamic"
        out["price_risk_reason"] = "" if not fail else fail
    return out
