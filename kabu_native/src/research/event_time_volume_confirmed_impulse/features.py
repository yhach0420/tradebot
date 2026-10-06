"""Causal window math and the frozen first-event latch. No market outcomes."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.event_time_volume_confirmed_impulse import PRIOR_BINS_10, PRIOR_BINS_30
from research.event_time_volume_confirmed_impulse.contract import RESET_IMPLEMENTATION


def window_sum(prefix: np.ndarray, t: np.ndarray, left_lag: float, right_lag: float) -> np.ndarray:
    """Sum of values on (T-left_lag, T-right_lag]. prefix[k] sums the first k events."""
    left = np.searchsorted(t, t - float(left_lag), side="right")
    right = np.searchsorted(t, t - float(right_lag), side="right")
    return prefix[right] - prefix[left]


def _prefix(values: np.ndarray) -> np.ndarray:
    out = np.zeros(int(values.size) + 1, dtype=float)
    np.cumsum(values, out=out[1:])
    return out


def prior_median(prefix: np.ndarray, t: np.ndarray, width: float, bins: int) -> np.ndarray:
    stacked = np.empty((bins, int(t.size)), dtype=float)
    for k in range(1, bins + 1):
        stacked[k - 1] = window_sum(prefix, t, width * (k + 1), width * k)
    return np.median(stacked, axis=0)


def price_context(t: np.ndarray, px: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """PRE_BREAK_HIGH on [T-60, T) and the previous finite price. Current event is excluded."""
    n = int(t.size)
    pre_high = np.full(n, np.nan)
    previous = np.full(n, np.nan)
    if n == 0:
        return pre_high, previous
    from collections import deque

    dq: deque[int] = deque()
    last = np.nan
    i = 0
    while i < n:
        t0 = float(t[i])
        while dq and float(t[dq[0]]) < t0 - 60.0:
            dq.popleft()
        pre = float(px[dq[0]]) if dq else np.nan
        j = i
        while j < n and float(t[j]) == t0:
            pre_high[j] = pre
            previous[j] = last
            value = float(px[j])
            if value == value and value > 0:
                last = value
            j += 1
        for k in range(i, j):
            value = float(px[k])
            if not (value == value and value > 0):
                continue
            while dq and float(px[dq[-1]]) <= value:
                dq.pop()
            dq.append(k)
        i = j
    return pre_high, previous


def first_events(predicate: np.ndarray, price: np.ndarray, level: np.ndarray) -> np.ndarray:
    """Admit the first P event. Reset only after price <= frozen level and P is false."""
    open_episode = False
    frozen = np.nan
    keep: list[int] = []
    n = int(predicate.size)
    for i in range(n):
        flag = bool(predicate[i])
        px = float(price[i])
        finite = px == px and px > 0
        if not open_episode:
            if flag:
                keep.append(i)
                open_episode = True
                frozen = float(level[i])
            continue
        if finite and px <= float(frozen) and not flag:
            open_episode = False
    return np.asarray(keep, dtype=np.int32)


def spread_not_worse(
    t: np.ndarray,
    spread: np.ndarray,
    quote_ok: np.ndarray,
    index: int,
) -> bool:
    """Current fresh spread is at or below the median fresh spread on [T-60, T)."""
    if not bool(quote_ok[index]) or not (float(spread[index]) == float(spread[index])):
        return False
    t0 = float(t[index])
    left = int(np.searchsorted(t, t0 - 60.0, side="left"))
    prior = spread[left:index]
    ok = quote_ok[left:index]
    sample = prior[ok & np.isfinite(prior)]
    if int(sample.size) == 0:
        return False
    return float(spread[index]) <= float(np.median(sample))


def evaluate(t: np.ndarray, px: np.ndarray, volume: np.ndarray, ask_vol: np.ndarray, bid_vol: np.ndarray, tick: np.ndarray, session_start: float) -> dict[str, Any]:
    vol_prefix = _prefix(volume)
    ask_prefix = _prefix(ask_vol)
    bid_prefix = _prefix(bid_vol)
    tick_prefix = _prefix(tick)
    vol10 = window_sum(vol_prefix, t, 10.0, 0.0)
    vol30 = window_sum(vol_prefix, t, 30.0, 0.0)
    ask10 = window_sum(ask_prefix, t, 10.0, 0.0)
    bid10 = window_sum(bid_prefix, t, 10.0, 0.0)
    ticks10 = window_sum(tick_prefix, t, 10.0, 0.0)
    ready10 = t >= float(session_start) + 70.0 - 1e-9
    ready30 = t >= float(session_start) + 150.0 - 1e-9
    vol_accel_10 = ready10 & (vol10 > prior_median(vol_prefix, t, 10.0, PRIOR_BINS_10))
    vol_accel_30 = ready30 & (vol30 > prior_median(vol_prefix, t, 30.0, PRIOR_BINS_30))
    tick_accel_10 = ready10 & (ticks10 > prior_median(tick_prefix, t, 10.0, PRIOR_BINS_10))
    classified = ask10 + bid10
    buy = ask10 > bid10
    pre_high, previous = price_context(t, px)
    price_break = (
        np.isfinite(previous)
        & np.isfinite(pre_high)
        & np.isfinite(px)
        & (previous <= pre_high)
        & (px > pre_high)
    )
    return {
        "vol10": vol10,
        "ask10": ask10,
        "bid10": bid10,
        "classified10": classified,
        "vol_accel_10": vol_accel_10,
        "vol_accel_30": vol_accel_30,
        "tick_accel_10": tick_accel_10,
        "buy": buy,
        "price_break": price_break,
        "pre_high": pre_high,
        "ready10": ready10,
    }


def self_check() -> dict[str, bool]:
    t = np.arange(0, 80, 10, dtype=float)
    volume = np.ones(t.size)
    volume[-1] = 5.0
    zeros = np.zeros(t.size)
    px = np.array([10, 12, 11, 11, 11, 11, 11, 13], dtype=float)
    got = evaluate(t, px, volume, zeros, zeros, np.ones(t.size), session_start=0.0)
    # t=70 is the last point. Six prior 10s bins each contain one unit. Current is 5.
    accel_last = bool(got["vol_accel_10"][-1])
    equal_fails = True
    flat_vol = np.ones(t.size)
    flat = evaluate(t, px, flat_vol, zeros, zeros, np.ones(t.size), session_start=0.0)
    if bool(flat["vol_accel_10"][-1]):
        equal_fails = False
    pre, prev = price_context(t, px)
    # At the last event, [10, 70) max is 12 and previous finite price is 11.
    break_ok = bool(np.isfinite(pre[-1]) and pre[-1] == 12 and prev[-1] == 11 and px[-1] > pre[-1])
    not_current = bool(pre[-1] < px[-1])
    # Episode: admit index 1, hold through a still-true event, reset on a false event at/below the level, then admit again.
    predicate = np.array([False, True, True, False, True], dtype=bool)
    price = np.array([10, 12, 13, 9, 11], dtype=float)
    level = np.array([np.nan, 10, 10, 10, 9], dtype=float)
    admitted = first_events(predicate, price, level).tolist()
    episode_ok = admitted == [1, 4]
    held = np.array([False, True, True, False], dtype=bool)
    held_price = np.array([10, 12, 9, 11], dtype=float)
    held_level = np.array([np.nan, 10, 10, 9], dtype=float)
    # Index 2 is back through the level but P is still true, so the latch stays shut.
    hold_ok = first_events(held, held_price, held_level).tolist() == [1]
    spread_t = np.array([0.0, 10.0, 20.0, 70.0])
    spread = np.array([2.0, 2.0, 4.0, 2.0])
    ok = np.array([True, True, True, True])
    spread_pass = spread_not_worse(spread_t, spread, ok, 3)
    spread_fail = not spread_not_worse(spread_t, np.array([2.0, 2.0, 4.0, 5.0]), ok, 3)
    return {
        "reset_documented": len(RESET_IMPLEMENTATION) > 200,
        "strict_volume_accel": accel_last,
        "equal_median_fails": equal_fails,
        "price_break_excludes_current": break_ok and not_current,
        "first_event_then_reset": episode_ok,
        "no_reset_while_predicate_true": hold_ok,
        "spread_not_worse": spread_pass and spread_fail,
    }
