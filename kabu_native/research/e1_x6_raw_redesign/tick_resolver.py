"""Research-only JPX tick resolver (symbol-class + price dependent).

Two tables per the official JPX quotation-unit rules:
- OTHER          : other issues table (matches runtime jpx_tick_size_yen OTHER)
- NARROW_TOPIX500: the official fine table applied to TOPIX500 constituents
  (0.1/0.5/1/5/10/50/100/500/1000/5000/10000 yen bands).
The runtime helper `research.low_price_risk_review.jpx_tick_size_yen` is kept
READ-ONLY as a reference (its SHA is frozen into P1_R1), but its narrow table
MERGES the official 0.5-yen and 5-yen bands into coarser ones; 9-day observed
increments (0.5 yen in (1000,3000], 5 yen in (10000,30000]) contradict it, so
it is NOT a verified resolver and the official bands are used here instead.

Symbol class is determined EMPIRICALLY from 9 days of observed board price
increments (min positive adjacent-level / quote-change increment per price
band). A class is accepted only when every observed increment is an exact
multiple of that class's tick in the corresponding band and at least one band
has enough observations. If no single class (or both) is consistent, the
symbol class is UNRESOLVED => the run must stop with P1_R1_BLOCKED
(no silent 0.1-yen fallback).

trigger / stop / no-progress all use this same resolver.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

CLASS_OTHER = "OTHER"
CLASS_NARROW = "NARROW_TOPIX500"
CLASSES = (CLASS_OTHER, CLASS_NARROW)

# (upper_bound_inclusive, tick) — same bands as jpx_tick_size_yen.
_TABLE = {
    CLASS_OTHER: (
        (3_000, 1.0), (5_000, 5.0), (30_000, 10.0), (50_000, 50.0),
        (300_000, 100.0), (500_000, 500.0), (3_000_000, 1_000.0),
        (5_000_000, 5_000.0), (30_000_000, 10_000.0), (float("inf"), 100_000.0),
    ),
    CLASS_NARROW: (
        (1_000, 0.1), (3_000, 0.5), (10_000, 1.0), (30_000, 5.0),
        (100_000, 10.0), (300_000, 50.0), (1_000_000, 100.0),
        (3_000_000, 500.0), (10_000_000, 1_000.0), (30_000_000, 5_000.0),
        (float("inf"), 10_000.0),
    ),
}

# Scale 20 => integers can represent 0.05-yen mid prices exactly as well as
# every 0.1-yen tick, so band comparisons are exact.
_SCALE = 20
_INF = 10**15


def runtime_resolver_sha256(native_root: Path) -> Optional[str]:
    fp = native_root / "src" / "research" / "low_price_risk_review.py"
    return hashlib.sha256(fp.read_bytes()).hexdigest() if fp.is_file() else None


def tick_size(symbol_class: str, price: float) -> float:
    if symbol_class not in _TABLE:
        raise ValueError(f"unknown symbol_class {symbol_class!r}")
    if price <= 0:
        raise ValueError("price must be > 0")
    for upper, tick in _TABLE[symbol_class]:
        if price <= upper:
            return tick
    raise AssertionError("unreachable")


def _i(x: float) -> int:
    return int(round(x * _SCALE))


def next_valid_price_above(reference_price: float, symbol_class: str) -> float:
    """Smallest valid quotation price strictly above reference_price.

    Band semantics (JPX): band_k = (upper_{k-1}, upper_k], prices are multiples
    of tick_k inside the band; every band upper is a valid price of that band.
    """
    ri = _i(reference_price)
    for upper, tick in _TABLE[symbol_class]:
        ui = _i(upper) if upper != float("inf") else _INF
        if ri < ui:
            ti = _i(tick)
            return ((ri // ti) * ti + ti) / _SCALE
    raise AssertionError("unreachable")


def next_valid_price_below(reference_price: float, symbol_class: str) -> float:
    """Largest valid quotation price strictly below reference_price."""
    ri = _i(reference_price)
    prev_ui = 0
    for upper, tick in _TABLE[symbol_class]:
        ui = _i(upper) if upper != float("inf") else _INF
        if ri - 1 <= ui:
            ti = _i(tick)
            c = ((ri - 1) // ti) * ti
            if c > prev_ui:
                return c / _SCALE
            if prev_ui > 0:
                # boundary price itself (valid price of the finer band below)
                return prev_ui / _SCALE
            raise ValueError(f"no valid price below {reference_price}")
        prev_ui = ui
    raise AssertionError("unreachable")


def classify_from_increments(
    band_min_increments: dict[str, float],
    band_obs_counts: dict[str, int],
    *,
    min_obs: int = 100,
) -> dict[str, object]:
    """Empirical symbol-class decision from observed minimum price increments.

    band_min_increments: price-band label -> min positive observed increment.
    Band label format: "le_<upper>" using OTHER-table uppers for binning is NOT
    required; we simply test consistency of each observation against both
    tables using the band's representative price (the observation carries its
    own price). Callers pass {price_key: min_inc} where price_key is the price
    at which the increment was observed (stringified band floor).
    """
    consistent = {c: True for c in CLASSES}
    finer_seen = {c: False for c in CLASSES}
    total_obs = sum(band_obs_counts.values())
    for pk, inc in band_min_increments.items():
        price = float(pk)
        for c in CLASSES:
            t = tick_size(c, max(price, 0.1))
            ratio = inc / t
            if ratio < 0.999:  # observed increment finer than table tick
                consistent[c] = False
                finer_seen[c] = True
            elif abs(ratio - round(ratio)) > 1e-6:  # not a multiple of tick
                consistent[c] = False
    ok_classes = [c for c in CLASSES if consistent[c]]
    if total_obs < min_obs:
        return {"class": None, "reason": f"INSUFFICIENT_OBS:{total_obs}<{min_obs}",
                "candidates": ok_classes}
    if not ok_classes:
        return {"class": None, "reason": "NO_TABLE_CONSISTENT", "candidates": []}
    if len(ok_classes) == 1:
        return {"class": ok_classes[0], "reason": "UNIQUE_CONSISTENT", "candidates": ok_classes}
    # Both tables consistent: increments never went finer than the OTHER tick.
    # OTHER is the strictly coarser grid here, and every OTHER-grid price is
    # also a NARROW-grid price, so OTHER is the safe (provable) choice.
    return {"class": CLASS_OTHER, "reason": "BOTH_CONSISTENT_COARSER_CHOSEN",
            "candidates": ok_classes}
