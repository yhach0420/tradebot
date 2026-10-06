"""Canonical causal predicates. Frozen historical or natural boundaries only. No outcome-invented thresholds."""
from __future__ import annotations

from typing import Any, Callable

import numpy as np

from research.simple_tech_entry_family import WARMUP_BARS
from research.simple_tech_entry_family.stages import (
    _ok,
    attach_indicators,
    board_support,
    price_action,
    pullback_setup,
    trend_up,
    volume_confirm,
)
from research.systematic_state_transition_full_strategy_v1.states import (
    STATE_FNS,
    bb_above_mid,
    close_above_vwap,
    rci_above_neg80,
)

FAMILY = {
    "S_MA_TREND_UP": "MA",
    "S_CLOSE_ABOVE_EMA21": "MA",
    "S_BB_ABOVE_MID": "BB",
    "S_RCI_ABOVE_NEG80": "RCI",
    "S_VOL_CONFIRM_1M": "VOLUME",
    "S_CLOSE_ABOVE_VWAP": "VWAP",
    "S_BID_GT_ASK_QTY": "BOARD",
    "S_BOARD_SUPPORT": "BOARD",
    "S_PULLBACK_SETUP": "PULLBACK",
    "S_PRICE_ACTION": "PRICE_ACTION",
    "S_CLOSE_GT_PREV_CLOSE": "PRICE_CHANGE",
    "S_BREADTH_EXPANDING": "CROSS_SECTION",
}

SOURCE_IDENTITY = {
    "S_MA_TREND_UP": "simple_tech_entry_family.stages.trend_up / ST STATE_FNS",
    "S_CLOSE_ABOVE_EMA21": "natural close > existing EMA21 (attach_indicators)",
    "S_BB_ABOVE_MID": "systematic_state_transition_full_strategy_v1.states.bb_above_mid",
    "S_RCI_ABOVE_NEG80": "systematic_state_transition_full_strategy_v1.states.rci_above_neg80",
    "S_VOL_CONFIRM_1M": "simple_tech_entry_family.stages.volume_confirm / ST S_VOL_CONFIRM_1M",
    "S_CLOSE_ABOVE_VWAP": "systematic_state_transition_full_strategy_v1.states.close_above_vwap",
    "S_BID_GT_ASK_QTY": "natural bid_qty > ask_qty at standing quote T_i",
    "S_BOARD_SUPPORT": "simple_tech_entry_family.stages.board_support frozen BOARD_ASK_BID_QTY_MAX_RATIO=2.0",
    "S_PULLBACK_SETUP": "simple_tech_entry_family.stages.pullback_setup frozen PULLBACK_LOOKBACK=3",
    "S_PRICE_ACTION": "simple_tech_entry_family.stages.price_action",
    "S_CLOSE_GT_PREV_CLOSE": "natural Close[i] > Close[i-1]",
    "S_BREADTH_EXPANDING": "CSB N_up[t] > N_up[t-1] among names with defined trend_up at t and t-1",
}

CUTPOINT_KIND = {
    "S_MA_TREND_UP": "FROZEN_HISTORICAL",
    "S_CLOSE_ABOVE_EMA21": "NATURAL_BOUNDARY",
    "S_BB_ABOVE_MID": "FROZEN_HISTORICAL",
    "S_RCI_ABOVE_NEG80": "FROZEN_HISTORICAL",
    "S_VOL_CONFIRM_1M": "FROZEN_HISTORICAL",
    "S_CLOSE_ABOVE_VWAP": "NATURAL_BOUNDARY",
    "S_BID_GT_ASK_QTY": "NATURAL_BOUNDARY",
    "S_BOARD_SUPPORT": "FROZEN_HISTORICAL",
    "S_PULLBACK_SETUP": "FROZEN_HISTORICAL",
    "S_PRICE_ACTION": "FROZEN_HISTORICAL",
    "S_CLOSE_GT_PREV_CLOSE": "NATURAL_BOUNDARY",
    "S_BREADTH_EXPANDING": "NATURAL_BOUNDARY",
}


def close_above_ema21(ind: dict[str, np.ndarray], i: int) -> bool:
    cl = float(ind["close"][i]) if _ok(ind["close"][i]) else None
    e = float(ind["ema21"][i]) if _ok(ind["ema21"][i]) else None
    if cl is None or e is None:
        return False
    return float(cl) > float(e)


def close_gt_prev_close(ind: dict[str, np.ndarray], i: int) -> bool:
    if int(i) < 1:
        return False
    a = float(ind["close"][i - 1]) if _ok(ind["close"][i - 1]) else None
    b = float(ind["close"][i]) if _ok(ind["close"][i]) else None
    if a is None or b is None:
        return False
    return float(b) > float(a)


BAR_FNS: dict[str, Callable[[dict[str, np.ndarray], int], bool]] = {
    "S_MA_TREND_UP": trend_up,
    "S_CLOSE_ABOVE_EMA21": close_above_ema21,
    "S_BB_ABOVE_MID": bb_above_mid,
    "S_RCI_ABOVE_NEG80": rci_above_neg80,
    "S_VOL_CONFIRM_1M": volume_confirm,
    "S_CLOSE_ABOVE_VWAP": close_above_vwap,
    "S_PULLBACK_SETUP": pullback_setup,
    "S_PRICE_ACTION": price_action,
    "S_CLOSE_GT_PREV_CLOSE": close_gt_prev_close,
}

BOARD_IDS = ("S_BID_GT_ASK_QTY", "S_BOARD_SUPPORT")
XS_IDS = ("S_BREADTH_EXPANDING",)


def assert_st_bindings() -> None:
    assert STATE_FNS["S_MA_TREND_UP"] is trend_up
    assert STATE_FNS["S_VOL_CONFIRM_1M"] is volume_confirm
    assert STATE_FNS["S_BB_ABOVE_MID"] is bb_above_mid
    assert STATE_FNS["S_RCI_ABOVE_NEG80"] is rci_above_neg80
    assert STATE_FNS["S_CLOSE_ABOVE_VWAP"] is close_above_vwap


def indicators(raw: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return attach_indicators(raw)


def evaluable_i(i: int, n: int) -> bool:
    return int(i) >= int(WARMUP_BARS) - 1 and int(i) < int(n)


def bar_series(ind: dict[str, np.ndarray], pred_id: str) -> np.ndarray:
    fn = BAR_FNS[pred_id]
    n = int(ind["close"].size)
    out = np.zeros(n, dtype=bool)
    for i in range(n):
        if evaluable_i(i, n):
            out[i] = bool(fn(ind, int(i)))
    return out


def board_at(standing: dict[str, Any], pred_id: str) -> bool:
    if pred_id == "S_BID_GT_ASK_QTY":
        return bool(standing.get("bid_gt_ask"))
    if pred_id == "S_BOARD_SUPPORT":
        return bool(standing.get("board_support"))
    raise KeyError(pred_id)


def breadth_expanding(trend_now: dict[str, bool], trend_prev: dict[str, bool]) -> bool:
    names = [s for s in trend_now if s in trend_prev]
    if not names:
        return False
    n_prev = sum(1 for s in names if bool(trend_prev[s]))
    n_now = sum(1 for s in names if bool(trend_now[s]))
    return int(n_now) > int(n_prev)


__all__ = [
    "FAMILY",
    "SOURCE_IDENTITY",
    "CUTPOINT_KIND",
    "BAR_FNS",
    "BOARD_IDS",
    "XS_IDS",
    "assert_st_bindings",
    "indicators",
    "evaluable_i",
    "bar_series",
    "board_at",
    "breadth_expanding",
    "board_support",
]
