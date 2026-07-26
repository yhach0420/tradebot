"""Auto-discover usable high-resolution capture / PUSH days."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from research.entry_exit_contract.constants import NATIVE, PUSH_CACHE


def discover_capture_days(native: Path = NATIVE) -> dict[str, Any]:
    push_dir = native / "results" / "research" / "volume_confirmed_impulse_entry" / "_push_cache"
    if not push_dir.is_dir():
        push_dir = PUSH_CACHE
    mc = native / "data" / "market_capture"
    push_days = sorted({p.name.replace("_push.pkl", "") for p in push_dir.glob("*_push.pkl")}) if push_dir.is_dir() else []
    mc_days = sorted([p.name for p in mc.iterdir() if p.is_dir()]) if mc.is_dir() else []
    days = sorted(set(push_days) & set(mc_days)) if mc_days else list(push_days)
    if not days:
        days = list(push_days)
    warmup = days[0] if days else None
    oos = tuple(days[1:]) if len(days) > 1 else tuple()
    return {
        "push_days": push_days,
        "market_capture_days": mc_days,
        "usable_days": days,
        "warmup_day": warmup,
        "oos_days": list(oos),
        "raw_push_day_count": len(push_days),
        "note": "intersection of PUSH cache and market_capture when both exist",
    }
