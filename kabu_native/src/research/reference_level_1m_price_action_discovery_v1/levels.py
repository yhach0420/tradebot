"""Causal reference-level families. Values use completed bars / completed prior days only."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from research.one_minute_native_playbook_discovery_v1.states import to_min as bar_to_min
from research.reference_level_1m_price_action_discovery_v1 import GAP_MIN_BPS, NEAR_PDH_BPS

LEVEL_DEFS: tuple[dict[str, str], ...] = (
    {"level_id": "PDH", "family": "previous_day", "name": "previous_day_high", "availability": "completed prior trading day"},
    {"level_id": "PDL", "family": "previous_day", "name": "previous_day_low", "availability": "completed prior trading day"},
    {"level_id": "PDC", "family": "previous_day", "name": "previous_day_close", "availability": "completed prior trading day"},
    {"level_id": "OPEN", "family": "current_session", "name": "session_open", "availability": "first session bar open"},
    {"level_id": "VWAP", "family": "current_session", "name": "session_VWAP", "availability": "cumulative through completed bar T"},
    {"level_id": "CSH", "family": "current_session", "name": "current_session_high_so_far", "availability": "max high of bars strictly before T"},
    {"level_id": "CSL", "family": "current_session", "name": "current_session_low_so_far", "availability": "min low of bars strictly before T"},
    {"level_id": "OR5H", "family": "opening_range", "name": "OR5_high", "availability": "feature_bar >= 09:05; range 09:00-09:04 complete"},
    {"level_id": "OR5L", "family": "opening_range", "name": "OR5_low", "availability": "feature_bar >= 09:05; range 09:00-09:04 complete"},
    {"level_id": "OR15H", "family": "opening_range", "name": "OR15_high", "availability": "feature_bar >= 09:15; range 09:00-09:14 complete"},
    {"level_id": "OR15L", "family": "opening_range", "name": "OR15_low", "availability": "feature_bar >= 09:15; range 09:00-09:14 complete"},
    {"level_id": "D5H", "family": "multi_day", "name": "previous_5d_high", "availability": "max high of 5 completed prior days"},
    {"level_id": "D5L", "family": "multi_day", "name": "previous_5d_low", "availability": "min low of 5 completed prior days"},
    {"level_id": "D20H", "family": "multi_day", "name": "previous_20d_high", "availability": "max high of 20 completed prior days"},
    {"level_id": "D20L", "family": "multi_day", "name": "previous_20d_low", "availability": "min low of 20 completed prior days"},
    {"level_id": "GAP_UP_EDGE", "family": "gap_structure", "name": "gap_up_upper_edge_session_open", "availability": "gap_up known at first bar; open > PDC by GAP_MIN_BPS"},
    {"level_id": "GAP_UP_PDC", "family": "gap_structure", "name": "gap_up_lower_edge_PDC", "availability": "same as gap_up"},
    {"level_id": "GAP_DN_EDGE", "family": "gap_structure", "name": "gap_down_lower_edge_session_open", "availability": "gap_down known at first bar; open < PDC by GAP_MIN_BPS"},
    {"level_id": "GAP_DN_PDC", "family": "gap_structure", "name": "gap_down_upper_edge_PDC", "availability": "same as gap_down"},
)

FAMILY_IDS = ("previous_day", "current_session", "opening_range", "gap_structure", "multi_day")


@dataclass
class DayOHLC:
    date: str
    high: float
    low: float
    close: float
    open: float


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f and f != 0.0


def range_hl(rec: dict[str, Any], t0: str, t1: str) -> tuple[float, float]:
    hs: list[float] = []
    ls: list[float] = []
    for i, t in enumerate(rec["t"]):
        if t0 <= str(t) <= t1:
            h = float(rec["h"][i])
            l = float(rec["l"][i])
            if h == h:
                hs.append(h)
            if l == l:
                ls.append(l)
    if not hs or not ls:
        return float("nan"), float("nan")
    return float(max(hs)), float(min(ls))


def prior_extremes(hist: list[DayOHLC], n: int) -> tuple[float, float]:
    if len(hist) < n:
        return float("nan"), float("nan")
    w = hist[-n:]
    hs = [d.high for d in w if _finite(d.high)]
    ls = [d.low for d in w if _finite(d.low)]
    if len(hs) < n or len(ls) < n:
        return float("nan"), float("nan")
    return float(max(hs)), float(min(ls))


def dist_bps(px: float, level: float) -> float:
    if not _finite(px) or not _finite(level):
        return float("nan")
    return float((px / level - 1.0) * 10_000.0)


def near_level(px: float, level: float, bps: float = NEAR_PDH_BPS) -> bool:
    d = dist_bps(px, level)
    return d == d and abs(d) <= float(bps)


def gap_side(session_open: float, pdc: float) -> str | None:
    d = dist_bps(session_open, pdc)
    if d != d:
        return None
    if d >= GAP_MIN_BPS:
        return "GAP_UP"
    if d <= -GAP_MIN_BPS:
        return "GAP_DOWN"
    return None


# avoid unused import if clock.to_min missing
def _ok_min(t: str) -> int | None:
    return bar_to_min(t)


assert _ok_min("09:05") == 9 * 60 + 5
assert gap_side(101.0, 100.0) == "GAP_UP"
assert gap_side(99.0, 100.0) == "GAP_DOWN"
assert gap_side(100.05, 100.0) is None
