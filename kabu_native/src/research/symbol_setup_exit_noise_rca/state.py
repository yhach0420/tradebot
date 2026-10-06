"""Frozen thesis labels. No persistence length is chosen here."""
from __future__ import annotations

from typing import Any, Optional

from research.symbol_setup_baseline_complete_strategy_precommit.runner import loss_reason, thesis_live


def _finite(v: object) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def bar_state(ind: dict, i: int) -> Optional[dict[str, Any]]:
    if int(i) < 3:
        return None
    e9 = ind["ema9"][i]
    e21 = ind["ema21"][i]
    lag = ind["ema21"][i - 3]
    close = ind["close"][i]
    if not (_finite(e9) and _finite(e21) and _finite(lag)):
        return None
    e9f, e21f, lagf = float(e9), float(e21), float(lag)
    gap = e9f - e21f
    slope = e21f - lagf
    den = float(close) if _finite(close) and float(close) > 0 else None
    live = bool(thesis_live(e9f, e21f, lagf))
    reason = None if live else loss_reason(e9f, e21f, lagf)
    return {
        "index": int(i),
        "finalize_t": float(ind["finalize_t"][i]),
        "close": None if den is None else den,
        "ema9": e9f,
        "ema21": e21f,
        "ema21_lag3": lagf,
        "fast_slow_gap_yen": gap,
        "fast_slow_gap_bps": None if den is None else gap / den * 10000.0,
        "slow_slope_3bar_yen": slope,
        "slow_slope_3bar_bps": None if den is None else slope / den * 10000.0,
        "fast_slow_relation_live": bool(e9f > e21f),
        "slow_trend_rising": bool(e21f > lagf),
        "thesis_live": live,
        "reason": reason,
        "slope_only": reason == "SLOW_TREND_SLOPE_LOSS",
    }


def duration_bars(states: list[Optional[dict[str, Any]]]) -> Optional[int]:
    """Consecutive slope-only bars from the loss bar until thesis, a cross, or the data ends."""
    if not states or states[0] is None or not states[0]["slope_only"]:
        return None
    n = 0
    for state in states:
        if state is None:
            break
        if state["thesis_live"] or not state["fast_slow_relation_live"]:
            break
        if not state["slope_only"]:
            break
        n += 1
    return n


def classify_slope(forward: list[Optional[dict[str, Any]]], loss_bid: Optional[float]) -> dict[str, Any]:
    """Labels were fixed before the scan. Forward bars are the next completed bars only."""
    observed = [row for row in forward if row is not None]
    recovered_at = None
    for k, row in enumerate(observed, start=1):
        if k > 3:
            break
        if row["thesis_live"]:
            recovered_at = k
            break
    window = observed[:3]
    cross = any(not row["fast_slow_relation_live"] for row in window)
    bid3 = window[2].get("bid") if len(window) >= 3 else None
    bid_below = loss_bid is not None and bid3 is not None and float(bid3) < float(loss_bid)
    temporary = recovered_at is not None
    price = False
    if temporary:
        rec = observed[int(recovered_at) - 1]
        price = loss_bid is not None and rec.get("bid") is not None and float(rec["bid"]) > float(loss_bid)
    if temporary and price:
        label = "TEMPORARY_AND_PRICE_RECOVERED"
    elif temporary:
        label = "TEMPORARY_SLOPE_INTERRUPTION"
    elif cross or bid_below:
        label = "TERMINAL_SLOPE_FAILURE"
    else:
        label = "AMBIGUOUS_SLOPE_FAILURE"
    return {
        "label": label,
        "temporary_slope_interruption": temporary,
        "temporary_and_price_recovered": bool(temporary and price),
        "recovered_within_1": recovered_at == 1,
        "recovered_within_2": recovered_at is not None and recovered_at <= 2,
        "recovered_within_3": temporary,
        "recovered_at_bar": recovered_at,
        "terminal": label == "TERMINAL_SLOPE_FAILURE",
        "ambiguous": label == "AMBIGUOUS_SLOPE_FAILURE",
    }
