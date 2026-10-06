"""Causal descriptors and rank associations. No threshold search and no symbol filter."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.event_time_volume_confirmed_impulse.features import _prefix
from research.v2_first_ratchet_transition_audit_v1.features import jpx_tick_size_yen
from research.v2_ratchet_state_transition_value_audit_v1.states import walk_position

# Established by the prior same-universe audit. A grouping label, not a feature.
ESTABLISHED_TOP10 = ("2737", "6920", "6976", "4062", "5803", "9984", "5801", "6857", "5706", "6981")

FAMILIES = {
    "tick_price_geometry": ("entry_price", "tick_size_bps", "ratchet_step_ticks", "ratchet_step_bps"),
    "spread_executability": ("spread_ticks", "spread_bps", "spread_bps_p75", "fresh_fraction"),
    "preentry_range": ("range_30_bps", "range_60_bps", "variability_30_bps", "variability_60_bps"),
    "event_activity": ("events_10", "events_30", "classified_10", "classified_30", "ticks_10"),
    "entry_impulse": ("break_bps", "vol_10", "vol_30", "ticks_10", "buy_participation", "classified_10"),
    "tightness_ratios": ("ratchet_step_to_spread", "ratchet_step_to_range"),
}


def _window(prefix: np.ndarray, times: np.ndarray, index: int, lag: float) -> float:
    t0 = float(times[index])
    left = int(np.searchsorted(times, t0 - lag, side="right"))
    right = int(np.searchsorted(times, t0, side="right"))
    return float(prefix[right] - prefix[left])


def _count(times: np.ndarray, index: int, lag: float) -> float:
    t0 = float(times[index])
    left = int(np.searchsorted(times, t0 - lag, side="right"))
    right = int(np.searchsorted(times, t0, side="right"))
    return float(right - left)


def _prior_prices(times: np.ndarray, prices: np.ndarray, index: int, lag: float) -> np.ndarray:
    t0 = float(times[index])
    left = int(np.searchsorted(times, t0 - lag, side="left"))
    sample = prices[left:index]
    return sample[np.isfinite(sample) & (sample > 0)]


def attach_audit_arrays(book: dict[str, Any], arrays: dict[str, np.ndarray]) -> None:
    book["_audit"] = {
        "t": arrays["t"],
        "px": arrays["px"],
        "ok": arrays["ok"],
        "vol_prefix": _prefix(arrays["vol"]),
        "ask_prefix": _prefix(arrays["ask"]),
        "bid_prefix": _prefix(arrays["bid"]),
        "tick_prefix": _prefix(arrays["tick"]),
    }


def trade_descriptors(book: dict[str, Any], index: int, entry_px: float) -> dict[str, Any]:
    """Values known at the entry event, plus frozen-path ratchet diagnostics."""
    audit = book["_audit"]
    times, prices = audit["t"], audit["px"]
    entry = float(entry_px)
    tick = jpx_tick_size_yen(entry)
    spread = float(book["spread"][index])
    spread_ok = spread == spread and spread >= 0 and entry > 0
    pre = float(book["pre_high"][index])
    px = float(book["px"][index])
    ask10 = _window(audit["ask_prefix"], times, index, 10.0)
    bid10 = _window(audit["bid_prefix"], times, index, 10.0)
    classified10 = ask10 + bid10
    p30 = _prior_prices(times, prices, index, 30.0)
    p60 = _prior_prices(times, prices, index, 60.0)
    fresh_left = int(np.searchsorted(times, float(times[index]) - 60.0, side="left"))
    fresh = audit["ok"][fresh_left:index]
    walked = walk_position(book, index)
    steps = []
    previous = float(walked["support0"])
    times_r = []
    for item in walked["ratchets"]:
        level = float(item["support"])
        steps.append(level - previous)
        previous = level
        times_r.append(float(item["t"]))
    gaps = [float(item["support"]) - float(walked["support0"]) for item in walked["ratchets"]]
    intervals = [times_r[i] - times_r[i - 1] for i in range(1, len(times_r))]
    return {
        "entry_price": entry,
        "tick_size": tick,
        "tick_size_bps": tick / entry * 10000.0,
        "spread_ticks": None if not spread_ok or tick <= 0 else spread / tick,
        "spread_bps": None if not spread_ok else spread / entry * 10000.0,
        "fresh_fraction": None if fresh.size == 0 else float(np.mean(fresh.astype(float))),
        "range_30_bps": _range_bps(p30, entry),
        "range_60_bps": _range_bps(p60, entry),
        "variability_30_bps": _variability_bps(p30, entry),
        "variability_60_bps": _variability_bps(p60, entry),
        "events_10": _count(times, index, 10.0),
        "events_30": _count(times, index, 30.0),
        "classified_10": classified10,
        "classified_30": _window(audit["ask_prefix"], times, index, 30.0) + _window(audit["bid_prefix"], times, index, 30.0),
        "vol_10": _window(audit["vol_prefix"], times, index, 10.0),
        "vol_30": _window(audit["vol_prefix"], times, index, 30.0),
        "ticks_10": _window(audit["tick_prefix"], times, index, 10.0),
        "buy_participation": None if classified10 <= 0 else ask10 / classified10,
        "break_bps": None if not (px == px and pre == pre and entry > 0) else (px - pre) / entry * 10000.0,
        "ratchet_n": int(walked["ratchet_n"]),
        "time_to_first_ratchet": None if not times_r else times_r[0] - float(walked["entry_t"]),
        "ratchet_interval": None if not intervals else float(np.median(intervals)),
        "ratchet_step_ticks": None if not steps or tick <= 0 else float(np.median(steps)) / tick,
        "ratchet_step_bps": None if not steps or entry <= 0 else float(np.median(steps)) / entry * 10000.0,
        "max_gap_ticks": None if not gaps or tick <= 0 else max(gaps) / tick,
        "max_gap_bps": None if not gaps or entry <= 0 else max(gaps) / entry * 10000.0,
    }


def _range_bps(sample: np.ndarray, entry: float) -> Optional[float]:
    if int(sample.size) < 2 or entry <= 0:
        return None
    return float((np.max(sample) - np.min(sample)) / entry * 10000.0)


def _variability_bps(sample: np.ndarray, entry: float) -> Optional[float]:
    if int(sample.size) < 2 or entry <= 0:
        return None
    return float(np.std(sample) / entry * 10000.0)


def _rank(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(values.size, dtype=float)
    i = 0
    while i < values.size:
        j = i + 1
        while j < values.size and values[order[j]] == values[order[i]]:
            j += 1
        average = 0.5 * (i + j - 1)
        for k in range(i, j):
            ranks[order[k]] = average
        i = j
    return ranks


def spearman(xs: list[Optional[float]], ys: list[Optional[float]]) -> Optional[float]:
    pairs = [(float(x), float(y)) for x, y in zip(xs, ys) if x is not None and y is not None and x == x and y == y]
    if len(pairs) < 6:
        return None
    left = np.array([p[0] for p in pairs])
    right = np.array([p[1] for p in pairs])
    if float(np.std(left)) == 0 or float(np.std(right)) == 0:
        return None
    a, b = _rank(left), _rank(right)
    return float(np.corrcoef(a, b)[0, 1])


def thirds(rows: list[dict[str, Any]], field: str) -> dict[str, list[dict[str, Any]]]:
    usable = [row for row in rows if row.get(field) is not None and row[field] == row[field]]
    usable.sort(key=lambda row: float(row[field]))
    n = len(usable)
    low, high = n // 3, 2 * n // 3
    return {"low": usable[:low], "middle": usable[low:high], "high": usable[high:]}


def self_check() -> None:
    perfect = spearman([1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6])
    opposite = spearman([1, 2, 3, 4, 5, 6], [6, 5, 4, 3, 2, 1])
    if perfect is None or abs(perfect - 1.0) > 1e-9 or opposite is None or abs(opposite + 1.0) > 1e-9:
        raise RuntimeError("spearman_self_check_failed")
