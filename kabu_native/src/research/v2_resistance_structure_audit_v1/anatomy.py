"""Pre-entry resistance anatomy. Future path is an outcome label only."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.event_time_impulse_complete_strategy_v2.rules import (
    activity_lost,
    exhausted,
    participation_lost,
    reconfirm,
    support_failure,
)
from research.low_price_risk_review import jpx_tick_size_yen


def _align(price: float, tick: float) -> float:
    return float(round(float(price) / tick) * tick)


def _zone_pass(times: np.ndarray, prices: np.ndarray, entry_t: float, lo: float, hi: float, center: float, tick: float) -> dict[str, Any]:
    episodes = []
    inside = False
    start = None
    dwell = 0.0
    last_t = None
    prev = np.nan
    exact = 0
    exact_inside = False
    for i in range(int(times.size)):
        t = float(times[i])
        if t >= entry_t:
            break
        price = float(prices[i])
        if not (price == price and price > 0):
            continue
        if prev == prev and prev < center - 1e-9 and abs(price - center) <= 1e-9:
            if not exact_inside:
                exact += 1
                exact_inside = True
        elif price < center - 1e-9:
            exact_inside = False
        if not inside:
            if prev == prev and prev < lo and lo <= price <= hi:
                inside = True
                start = t
                last_t = t
        elif price < lo:
            episodes.append((float(start), t))
            if last_t is not None:
                dwell += max(0.0, t - float(last_t))
            inside = False
            start = None
        else:
            if last_t is not None:
                dwell += max(0.0, t - float(last_t))
            last_t = t
        prev = price
    pull = {1: [], 3: [], 5: []}
    for _start, end in episodes:
        for horizon in (1, 3, 5):
            target = end + horizon
            if target >= entry_t:
                continue
            j = int(np.searchsorted(times, target, side="left"))
            if j >= int(times.size) or float(times[j]) >= entry_t:
                continue
            price = float(prices[j])
            if price == price:
                pull[horizon].append((lo - price) / tick)
    def _med(values: list[float]) -> Optional[float]:
        if not values:
            return None
        return float(np.median(np.asarray(values, dtype=float)))
    return {
        "touch_n": len(episodes) + (1 if inside else 0),
        "completed_n": len(episodes),
        "exact_touch_n": exact,
        "dwell_sec": dwell,
        "last_touch_t": None if not episodes and start is None else float(episodes[-1][0] if episodes else start),
        "rejection_n": len(episodes),
        "pull1": _med(pull[1]),
        "pull3": _med(pull[3]),
        "pull5": _med(pull[5]),
        "max_pull": None if not pull[5] and not pull[3] and not pull[1] else float(np.nanmax(np.asarray(pull[1] + pull[3] + pull[5], dtype=float))),
    }


def _next_zone(times: np.ndarray, prices: np.ndarray, entry_t: float, ask: float, tick: float) -> Optional[float]:
    best = None
    prev = np.nan
    for i in range(int(times.size)):
        t = float(times[i])
        if t >= entry_t:
            break
        price = float(prices[i])
        if not (price == price and price > 0):
            continue
        center = _align(price, tick)
        lo = center - tick
        if prev == prev and prev < lo and lo <= price <= center + tick and lo > ask:
            if best is None or center < best:
                best = center
        prev = price
    return best


def _conversion(times: np.ndarray, prices: np.ndarray, flags: np.ndarray, entry_t: float, exit_t: float, lo: float, hi: float) -> str:
    entered = False
    reconfirmed = False
    for i in range(int(np.searchsorted(times, entry_t, side="right")), int(times.size)):
        t = float(times[i])
        if t > exit_t + 1e-9:
            break
        price = float(prices[i])
        if not (price == price and price > 0):
            continue
        if price < lo:
            return "FAILED_SUPPORT"
        if lo <= price <= hi:
            entered = True
        if bool(flags[i]):
            reconfirmed = True
            if entered:
                return "RETEST_AND_HOLD"
    if entered and not reconfirmed:
        return "UNRESOLVED"
    return "CLEAN_HOLD"


def _ratchet_levels(book: dict[str, Any], index: int, exit_t: float) -> list[float]:
    support = float(book["pre_high"][index])
    last = float(book["t"][index])
    levels = []
    i = int(index) + 1
    n = int(book["t"].size)
    while i < n and float(book["t"][i]) <= exit_t + 1e-9:
        price = float(book["px"][i])
        event_t = float(book["t"][i])
        if reconfirm(bool(book["vol10"][i]), bool(book["vol30"][i]), bool(book["buy"][i]), float(book["classified"][i]), bool(book["tick"][i]), bool(book["break"][i])):
            new = float(book["pre_high"][i])
            if new == new and new > support:
                levels.append(new)
                support = new
            last = event_t
        if support_failure(price, support):
            break
        if exhausted(
            participation_lost(float(book["classified"][i]), float(book["ask10"][i]), float(book["bid10"][i])),
            activity_lost(bool(book["vol10"][i]), bool(book["vol30"][i]), bool(book["tick"][i])),
            event_t - last,
        ):
            break
        i += 1
    return levels


def analyze(book: dict[str, Any], trade: dict[str, Any]) -> dict[str, Any]:
    entry_t = float(trade["entry_t"])
    exit_t = float(trade["exit_t"])
    ask = float(trade["entry_px"])
    times = book["t"]
    prices = book["px"]
    index = int(np.searchsorted(times, entry_t, side="left"))
    if index >= int(times.size) or abs(float(times[index]) - entry_t) > 1e-3:
        return {"ok": False}
    pre = float(book["pre_high"][index])
    tick = float(jpx_tick_size_yen(ask))
    if not (pre == pre and tick > 0):
        return {"ok": False}
    center = _align(pre, tick)
    lo = center - tick
    hi = center + tick
    left = int(np.searchsorted(times, entry_t - 300.0, side="left"))
    window_t = times[left:index]
    window_p = prices[left:index]
    zone = _zone_pass(window_t, window_p, entry_t, lo, hi, center, tick)
    left60 = int(np.searchsorted(times, entry_t - 60.0, side="left"))
    zone60 = _zone_pass(times[left60:index], prices[left60:index], entry_t, lo, hi, center, tick)
    touches = int(zone["touch_n"])
    if touches <= 0:
        touch_class = "NO_PRIOR_TOUCH"
        status = "UNSTRUCTURED_RECENT_HIGH"
    elif touches == 1:
        touch_class = "ONE_TOUCH"
        status = "STRUCTURED_ZONE"
    elif touches == 2:
        touch_class = "TWO_TOUCHES"
        status = "STRUCTURED_ZONE"
    else:
        touch_class = "THREE_PLUS_TOUCHES"
        status = "STRUCTURED_ZONE"
    nxt = _next_zone(window_t, window_p, entry_t, ask, tick)
    if nxt is None:
        head_ticks = None
        head_bps = None
        ratio = None
        next_status = "OPEN_ABOVE"
        next_low = None
    else:
        next_low = nxt - tick
        head_ticks = (next_low - ask) / tick
        head_bps = (next_low / ask - 1.0) * 10000.0 if ask > 0 else None
        down = (ask - lo) / tick
        ratio = None if down == 0 else head_ticks / down
        next_status = "KNOWN"
    reconfirm_flag = (
        book["vol10"].astype(bool)
        & book["vol30"].astype(bool)
        & book["buy"].astype(bool)
        & (book["classified"] > 0)
        & book["tick"].astype(bool)
        & book["break"].astype(bool)
    )
    conversion = _conversion(times, prices, reconfirm_flag, entry_t, exit_t, lo, hi) if status == "STRUCTURED_ZONE" else "UNSTRUCTURED"
    levels = _ratchet_levels(book, index, exit_t)
    structured_ratchets = 0
    distinct = set()
    for level in levels:
        level_center = _align(level, tick)
        level_zone = _zone_pass(window_t, window_p, entry_t, level_center - tick, level_center + tick, level_center, tick)
        if int(level_zone["touch_n"]) >= 2:
            structured_ratchets += 1
            distinct.add(level_center)
    age = None if zone["last_touch_t"] is None else entry_t - float(zone["last_touch_t"])
    return {
        "ok": True,
        "pre_break": pre,
        "center": center,
        "tick": tick,
        "touch_class": touch_class,
        "status": status,
        "touch_n": touches,
        "touch_n_60": int(zone60["touch_n"]),
        "exact_touch_n": int(zone["exact_touch_n"]),
        "rejection_n": int(zone["rejection_n"]),
        "pull3": zone["pull3"],
        "dwell_sec": zone["dwell_sec"],
        "age_sec": age,
        "next_status": next_status,
        "headroom_ticks": head_ticks,
        "headroom_bps": head_bps,
        "headroom_ratio": ratio,
        "conversion": conversion,
        "reconstructed_ratchets": len(levels),
        "structured_ratchet_n": structured_ratchets,
        "distinct_structured_zones": len(distinct),
        "spread_bps": trade.get("spread_bps"),
    }
