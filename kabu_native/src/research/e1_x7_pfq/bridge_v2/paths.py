"""Event-time and fixed-grid executable path outcomes."""
from __future__ import annotations

from typing import Any, Optional

from . import COST_BPS, FRESHNESS_MAX_SEC, MAX_HOLD_SEC


def net_bps(entry_ask: float, bid: float, cost_bps: float = COST_BPS) -> float:
    if entry_ask <= 0:
        return float("nan")
    return ((bid - entry_ask) / entry_ask) * 1e4 - cost_bps


def _fresh_ok(quote_t: float, asof_t: float, max_age: float = FRESHNESS_MAX_SEC) -> bool:
    return 0.0 <= (asof_t - quote_t) <= max_age


def build_event_time_path(
    *,
    entry_ask: float,
    entry_t: float,
    end_t: float,
    session_end: float,
    bid_events: list[tuple[float, float]],
) -> dict[str, Any]:
    """bid_events: sorted (t, best_bid) same symbol/day/session, t >= entry_t."""
    horizon = min(entry_t + MAX_HOLD_SEC, end_t, session_end)
    points: list[tuple[float, float, float]] = []  # t, bid, net_bps
    for t, bid in bid_events:
        if t < entry_t:
            continue
        if t > horizon:
            break
        if not _fresh_ok(t, t):  # event itself is fresh at occurrence
            continue
        if bid is None or bid <= 0:
            continue
        nb = net_bps(entry_ask, float(bid))
        points.append((float(t), float(bid), nb))

    return _summarize_path(points, entry_t, mode="event_time")


def build_fixed_grid_path(
    *,
    entry_ask: float,
    entry_t: float,
    end_t: float,
    session_end: float,
    bid_events: list[tuple[float, float]],
) -> dict[str, Any]:
    """1s grid; last bid at or before grid time within freshness; no interpolation."""
    horizon = min(entry_t + MAX_HOLD_SEC, end_t, session_end)
    expected = int(max(0, int(horizon - entry_t))) + 1  # 0..floor(horizon-entry)
    # Use events only; maintain pointer
    i = 0
    n = len(bid_events)
    last_t: Optional[float] = None
    last_bid: Optional[float] = None
    points: list[tuple[float, float, float]] = []
    # Skip events before entry for pointer init — last quote before entry may seed if fresh
    j = 0
    while j < n and bid_events[j][0] < entry_t:
        last_t, last_bid = float(bid_events[j][0]), float(bid_events[j][1])
        j += 1
    i = j

    grid_t = entry_t
    while grid_t <= horizon + 1e-9:
        while i < n and bid_events[i][0] <= grid_t + 1e-12:
            last_t, last_bid = float(bid_events[i][0]), float(bid_events[i][1])
            i += 1
        if last_t is not None and last_bid is not None and _fresh_ok(last_t, grid_t):
            nb = net_bps(entry_ask, last_bid)
            points.append((float(grid_t), float(last_bid), nb))
        grid_t += 1.0

    out = _summarize_path(points, entry_t, mode="fixed_grid")
    out["fixed_grid_expected_points"] = expected
    out["fixed_grid_valid_points"] = len(points)
    out["fixed_grid_valid_rate"] = (len(points) / expected) if expected else 0.0
    return out


def _first_time_crossing(points: list[tuple[float, float, float]], thr: float, direction: str) -> Optional[float]:
    for t, _bid, nb in points:
        if direction == "plus" and nb >= thr:
            return t
        if direction == "minus" and nb <= thr:
            return t
    return None


def _summarize_path(points: list[tuple[float, float, float]], entry_t: float, *, mode: str) -> dict[str, Any]:
    if not points:
        return {
            "mode": mode,
            "valid_points": 0,
            "initial_liquidation_net_bps": None,
            "best_net_pnl_bps_300s": None,
            "worst_net_pnl_bps_300s": None,
            "time_to_net_plus5_sec": None,
            "time_to_net_plus10_sec": None,
            "time_to_net_minus10_sec": None,
            "time_to_net_minus15_sec": None,
            "t_plus5": None,
            "t_plus10": None,
            "t_minus10": None,
            "t_minus15": None,
            "evaluable": False,
        }
    nets = [p[2] for p in points]
    t_p5 = _first_time_crossing(points, 5.0, "plus")
    t_p10 = _first_time_crossing(points, 10.0, "plus")
    t_m10 = _first_time_crossing(points, -10.0, "minus")
    t_m15 = _first_time_crossing(points, -15.0, "minus")
    return {
        "mode": mode,
        "valid_points": len(points),
        "initial_liquidation_net_bps": nets[0],
        "best_net_pnl_bps_300s": max(nets),
        "worst_net_pnl_bps_300s": min(nets),
        "time_to_net_plus5_sec": None if t_p5 is None else (t_p5 - entry_t),
        "time_to_net_plus10_sec": None if t_p10 is None else (t_p10 - entry_t),
        "time_to_net_minus10_sec": None if t_m10 is None else (t_m10 - entry_t),
        "time_to_net_minus15_sec": None if t_m15 is None else (t_m15 - entry_t),
        "t_plus5": t_p5,
        "t_plus10": t_p10,
        "t_minus10": t_m10,
        "t_minus15": t_m15,
        "evaluable": True,
        "points": points,  # retained for first-touch / adverse; stripped before publish if needed
    }


def first_touch(t_plus: Optional[float], t_minus: Optional[float]) -> str:
    if t_plus is None and t_minus is None:
        return "NEITHER"
    if t_plus is None:
        return "MINUS_FIRST"
    if t_minus is None:
        return "PLUS_FIRST"
    if abs(t_plus - t_minus) < 1e-9:
        return "AMBIGUOUS_SAME_EVENT"
    if t_plus < t_minus:
        return "PLUS_FIRST"
    return "MINUS_FIRST"


def first_touch_bundle(path: dict[str, Any]) -> dict[str, str]:
    if not path.get("evaluable"):
        return {
            "plus5_vs_minus10": "NOT_EVALUABLE",
            "plus5_vs_minus15": "NOT_EVALUABLE",
            "plus10_vs_minus10": "NOT_EVALUABLE",
            "plus10_vs_minus15": "NOT_EVALUABLE",
        }
    return {
        "plus5_vs_minus10": first_touch(path.get("t_plus5"), path.get("t_minus10")),
        "plus5_vs_minus15": first_touch(path.get("t_plus5"), path.get("t_minus15")),
        "plus10_vs_minus10": first_touch(path.get("t_plus10"), path.get("t_minus10")),
        "plus10_vs_minus15": first_touch(path.get("t_plus10"), path.get("t_minus15")),
    }


def adverse_before(points: list[tuple[float, float, float]], t_target: Optional[float]) -> Optional[float]:
    if t_target is None or not points:
        return None
    worst = None
    for t, _b, nb in points:
        if t > t_target + 1e-12:
            break
        worst = nb if worst is None else min(worst, nb)
    return worst
