"""Chronological folds. DEV split by unique development dates, not by PnL."""
from __future__ import annotations

from typing import Any

from research.pb1_complete_strategy_causal_repair_mechanism_discovery.load import confirmation_tertiles
from research.pb1_entry_architecture_reassessment import FOLDS
from research.pb1_v4_complete_strategy_build_and_economic_validation import DEV_FIRST, DEV_LAST


def dev_halves(dev_dates: list[str]) -> dict[str, str]:
    dates = sorted({str(d) for d in dev_dates if DEV_FIRST <= str(d) <= DEV_LAST})
    mid = len(dates) // 2
    out: dict[str, str] = {}
    for i, d in enumerate(dates):
        out[d] = "DEV_EARLY" if i < mid else "DEV_LATE"
    return out


def fold_of(date: str, *, dev_map: dict[str, str], c1_map: dict[str, str]) -> str:
    ds = str(date)
    if ds in dev_map:
        return dev_map[ds]
    return str(c1_map.get(ds) or "C1_LATE")


def assign_folds(trades: list[dict[str, Any]], *, discovery_dates: list[str], confirmation_dates: list[str]) -> None:
    dev_map = dev_halves(discovery_dates)
    c1_map = confirmation_tertiles(confirmation_dates)
    for t in trades:
        t["arch_fold"] = fold_of(str(t["date"]), dev_map=dev_map, c1_map=c1_map)
        assert t["arch_fold"] in FOLDS
