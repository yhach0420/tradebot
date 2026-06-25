"""
Phase538: Split position CAP helpers (PBv2 pool + OR overlay pool).

When or_overlay_enabled=true, PBv2 and OR positions use independent pools:
  PBv2 max cap_pbv2 (default 4), OR max cap_or (default 1), total max 5.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from research.exposure_gate import REJECT_MAX_CONCURRENT

ENTRY_TYPE_PBV2 = "PBV2"
ENTRY_TYPE_OR = "OR_OVERLAY"

REJECT_OR_CAP_FULL = "or_cap_full"
REJECT_PBV2_CAP_FULL = "pbv2_cap_full"


def entry_type_from_trade(trade: Mapping[str, Any]) -> str:
    et = str(trade.get("entry_type") or ENTRY_TYPE_PBV2).strip().upper()
    if et == ENTRY_TYPE_OR:
        return ENTRY_TYPE_OR
    return ENTRY_TYPE_PBV2


def split_pool_open_counts(observer: Any) -> tuple[int, int, int]:
    """Return (pbv2_open, or_open, total_open)."""
    if observer is None:
        return 0, 0, 0
    fn = getattr(observer, "open_count_by_entry_type", None)
    if callable(fn):
        pbv2, or_open = fn()
        return int(pbv2), int(or_open), int(pbv2) + int(or_open)
    total = int(observer.open_count())
    return total, 0, total


def observer_cap_kwargs_for_pool(
    observer: Any,
    symbol: str,
    *,
    entry_pool: str,
    cap_pbv2: int,
    cap_or: int,
) -> dict[str, Any]:
    pbv2_open, or_open, _total = split_pool_open_counts(observer)
    pool = str(entry_pool or ENTRY_TYPE_PBV2).strip().upper()
    if pool == ENTRY_TYPE_OR:
        return {
            "observer_open_count": or_open,
            "observer_symbol_open": bool(observer.has_open(symbol)) if observer else False,
            "max_concurrent_positions": int(cap_or),
        }
    return {
        "observer_open_count": pbv2_open,
        "observer_symbol_open": bool(observer.has_open(symbol)) if observer else False,
        "max_concurrent_positions": int(cap_pbv2),
    }


def legacy_observer_cap_kwargs(observer: Any, symbol: str) -> dict[str, Any]:
    if observer is None:
        return {"observer_open_count": 0, "observer_symbol_open": False}
    return {
        "observer_open_count": int(observer.open_count()),
        "observer_symbol_open": bool(observer.has_open(symbol)),
    }


def cap_reject_reason_for_pool(entry_pool: str) -> str:
    pool = str(entry_pool or ENTRY_TYPE_PBV2).strip().upper()
    if pool == ENTRY_TYPE_OR:
        return REJECT_OR_CAP_FULL
    return REJECT_PBV2_CAP_FULL


def is_split_cap_reject(reason: str, *, entry_pool: str) -> bool:
    if reason != REJECT_MAX_CONCURRENT:
        return False
    pool = str(entry_pool or ENTRY_TYPE_PBV2).strip().upper()
    return pool in (ENTRY_TYPE_PBV2, ENTRY_TYPE_OR)


def format_split_slot_usage(
    *,
    pbv2_open: int,
    or_open: int,
    cap_pbv2: int,
    cap_or: int,
) -> str:
    total_cap = int(cap_pbv2) + int(cap_or)
    total_open = int(pbv2_open) + int(or_open)
    return (
        f"PBv2 {pbv2_open}/{cap_pbv2} + OR {or_open}/{cap_or} "
        f"(total {total_open}/{total_cap})"
    )


def split_cap_summary_fields(
    config: Any,
    observer: Any,
) -> dict[str, Any]:
    if not getattr(config, "or_overlay_enabled", False):
        return {}
    cap_pbv2 = int(getattr(config, "cap_pbv2", 4) or 4)
    cap_or = int(getattr(config, "cap_or", 1) or 1)
    pbv2_open, or_open, total = split_pool_open_counts(observer)
    return {
        "or_overlay_enabled": True,
        "cap_pbv2": cap_pbv2,
        "cap_or": cap_or,
        "pbv2_pool_open": pbv2_open,
        "or_pool_open": or_open,
        "split_cap_total_open": total,
        "split_cap_total_max": cap_pbv2 + cap_or,
    }
