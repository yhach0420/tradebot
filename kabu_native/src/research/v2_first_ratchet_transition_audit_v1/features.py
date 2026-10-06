"""Fixed-landmark features. No threshold search. Future ratchet count is never an input."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

def jpx_tick_size_yen(price: float) -> float:
    """Same default table as research.low_price_risk_review.jpx_tick_size_yen."""
    p = float(price)
    if p <= 0:
        return 1.0
    if p <= 3000:
        return 1.0
    if p <= 5000:
        return 5.0
    if p <= 30000:
        return 10.0
    if p <= 50000:
        return 50.0
    if p <= 300000:
        return 100.0
    if p <= 500000:
        return 500.0
    if p <= 3000000:
        return 1000.0
    if p <= 5000000:
        return 5000.0
    if p <= 30000000:
        return 10000.0
    return 100000.0

TIME_LANDMARKS_MS = (0, 50, 100, 250, 500, 1000)
EVENT_LANDMARKS = (1, 2, 3, 5, 10)
# Precommitted before looking at outcomes. Not tuned.
MIN_ABS_D = 0.20
MIN_AUC_EDGE = 0.56
MIN_GROUP_N = 100


def _last_board(board_t: np.ndarray, values: np.ndarray, t: float) -> float:
    if board_t is None or int(board_t.size) == 0:
        return float("nan")
    j = int(np.searchsorted(board_t, t, side="right")) - 1
    if j < 0:
        return float("nan")
    value = float(values[j])
    return value


def _finite(values: list[float]) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    return arr[np.isfinite(arr)]


def window_features(book: dict[str, Any], signal_index: int, *, horizon_s: Optional[float], event_n: Optional[int]) -> dict[str, float]:
    """Features using only events from the signal through the fixed landmark."""
    times = book["t"]
    px = book["px"]
    i0 = int(signal_index)
    t0 = float(times[i0])
    n = int(times.size)
    if horizon_s is not None:
        right = int(np.searchsorted(times, t0 + float(horizon_s), side="right"))
    else:
        right = min(n, i0 + 1 + int(event_n))
    right = max(right, i0 + 1)
    sl = slice(i0, right)
    price = px[sl]
    signal_px = float(px[i0])
    pre = float(book["pre_high"][i0])
    ask0 = float(book["ask_px"][i0])
    tick = float(jpx_tick_size_yen(ask0 if ask0 == ask0 and ask0 > 0 else signal_px))
    if tick <= 0:
        tick = 1.0
    finite_px = price[np.isfinite(price)]
    last_px = float(finite_px[-1]) if finite_px.size else float("nan")
    board_t = book.get("board_t")
    board_bid = book.get("board_bid")
    board_ask = book.get("board_ask")
    fresh = book.get("board_fresh")
    bid0 = _last_board(board_t, board_bid, t0)
    askq0 = _last_board(board_t, board_ask, t0)
    bids = []
    asks = []
    freshes = []
    for t in times[sl]:
        bids.append(_last_board(board_t, board_bid, float(t)))
        asks.append(_last_board(board_t, board_ask, float(t)))
        if fresh is not None:
            freshes.append(_last_board(board_t, fresh, float(t)))
    bid_a = _finite(bids)
    ask_a = _finite(asks)
    last_bid = float(bid_a[-1]) if bid_a.size else float("nan")
    last_ask = float(ask_a[-1]) if ask_a.size else float("nan")
    ask_vol = book["ask_vol"][sl]
    bid_vol = book["bid_vol"][sl]
    vol = book["event_vol"][sl]
    buy_sum = float(np.nansum(ask_vol))
    sell_sum = float(np.nansum(bid_vol))
    gross = buy_sum + sell_sum
    spread0 = float(book["spread"][i0]) if book["spread"][i0] == book["spread"][i0] else float("nan")
    spreads = book["spread"][sl]
    finite_spread = spreads[np.isfinite(spreads)]
    last_spread = float(finite_spread[-1]) if finite_spread.size else float("nan")
    above = np.isfinite(price) & (price > pre)
    dist = (price - pre) / tick
    finite_dist = dist[np.isfinite(dist)]
    approaches = 0
    if finite_dist.size > 1:
        approaches = int(np.sum(np.diff(finite_dist) < 0))
    bid_up = bid_down = ask_up = ask_down = 0
    if bid_a.size > 1:
        d = np.diff(bid_a)
        bid_up = int(np.sum(d > 0))
        bid_down = int(np.sum(d < 0))
    if ask_a.size > 1:
        d = np.diff(ask_a)
        ask_up = int(np.sum(d > 0))
        ask_down = int(np.sum(d < 0))
    return {
        "px_minus_signal_ticks": (last_px - signal_px) / tick if last_px == last_px else float("nan"),
        "px_minus_ask_ticks": (last_px - ask0) / tick if last_px == last_px and ask0 == ask0 else float("nan"),
        "px_minus_pre_ticks": (last_px - pre) / tick if last_px == last_px else float("nan"),
        "max_favorable_ticks": float(np.nanmax(finite_dist)) if finite_dist.size else float("nan"),
        "max_adverse_ticks": float(np.nanmin(finite_dist)) if finite_dist.size else float("nan"),
        "frac_above_pre": float(np.mean(above)) if above.size else float("nan"),
        "new_high_count": float(np.sum(np.diff(np.maximum.accumulate(np.where(np.isfinite(price), price, -np.inf))) > 0)) if price.size else 0.0,
        "bid_minus_signal": last_bid - bid0 if last_bid == last_bid and bid0 == bid0 else float("nan"),
        "ask_minus_signal": last_ask - askq0 if last_ask == last_ask and askq0 == askq0 else float("nan"),
        "max_bid_improvement": float(np.nanmax(bid_a - bid0)) if bid_a.size and bid0 == bid0 else float("nan"),
        "max_ask_improvement": float(np.nanmax(ask_a - askq0)) if ask_a.size and askq0 == askq0 else float("nan"),
        "min_bid_retreat": float(np.nanmin(bid_a - bid0)) if bid_a.size and bid0 == bid0 else float("nan"),
        "min_ask_retreat": float(np.nanmin(ask_a - askq0)) if ask_a.size and askq0 == askq0 else float("nan"),
        "bid_upticks": float(bid_up),
        "bid_downticks": float(bid_down),
        "ask_upticks": float(ask_up),
        "ask_downticks": float(ask_down),
        "buy_volume": buy_sum,
        "sell_volume": sell_sum,
        "net_buy_volume": buy_sum - sell_sum,
        "buy_fraction": buy_sum / gross if gross > 0 else float("nan"),
        "buy_dominant_frac": float(np.mean(book["buy"][sl])) if sl.stop > sl.start else float("nan"),
        "push_n": float(right - i0),
        "volume_delta": float(np.nansum(vol)),
        "tick_accel_frac": float(np.mean(book["tick"][sl])) if sl.stop > sl.start else float("nan"),
        "vol_accel_frac": float(np.mean(book["vol10"][sl])) if sl.stop > sl.start else float("nan"),
        "spread_at_signal": spread0,
        "spread_at_landmark": last_spread,
        "spread_change": last_spread - spread0 if last_spread == last_spread and spread0 == spread0 else float("nan"),
        "frac_spread_le_signal": float(np.mean(finite_spread <= spread0)) if finite_spread.size and spread0 == spread0 else float("nan"),
        "fresh_at_landmark": float(freshes[-1]) if freshes else float("nan"),
        "px_to_support_ticks": (last_px - pre) / tick if last_px == last_px else float("nan"),
        "bid_to_support": (last_bid - pre) / tick if last_bid == last_bid else float("nan"),
        "min_px_to_support_ticks": float(np.nanmin(finite_dist)) if finite_dist.size else float("nan"),
        "support_approaches": float(approaches),
    }


def t0_parent(book: dict[str, Any], signal_index: int) -> dict[str, float]:
    i = int(signal_index)
    ask = float(book["ask_px"][i])
    px = float(book["px"][i])
    pre = float(book["pre_high"][i])
    tick = float(jpx_tick_size_yen(ask if ask == ask and ask > 0 else px)) or 1.0
    return {
        "vol_accel_10": float(bool(book["vol10"][i])),
        "vol_accel_30": float(bool(book["vol30"][i])),
        "buy_dominant": float(bool(book["buy"][i])),
        "tick_accel_10": float(bool(book["tick"][i])),
        "classified10": float(book["classified"][i]),
        "break_distance_ticks": (px - pre) / tick if px == px else float("nan"),
        "spread": float(book["spread"][i]) if book["spread"][i] == book["spread"][i] else float("nan"),
    }


FAMILIES = {
    "PRICE_PROGRESSION": ("px_minus_signal_ticks", "px_minus_ask_ticks", "px_minus_pre_ticks", "max_favorable_ticks", "max_adverse_ticks", "frac_above_pre", "new_high_count"),
    "BID_ASK_MIGRATION": ("bid_minus_signal", "ask_minus_signal", "max_bid_improvement", "max_ask_improvement", "min_bid_retreat", "min_ask_retreat", "bid_upticks", "bid_downticks", "ask_upticks", "ask_downticks"),
    "BUY_PARTICIPATION": ("buy_volume", "sell_volume", "net_buy_volume", "buy_fraction", "buy_dominant_frac"),
    "ACTIVITY_PERSISTENCE": ("push_n", "volume_delta", "tick_accel_frac", "vol_accel_frac"),
    "SPREAD_EXECUTABILITY": ("spread_at_signal", "spread_at_landmark", "spread_change", "frac_spread_le_signal", "fresh_at_landmark"),
    "SUPPORT_PRESSURE": ("px_to_support_ticks", "bid_to_support", "min_px_to_support_ticks", "support_approaches"),
}
QUINTILE_KEYS = ("px_minus_signal_ticks", "bid_minus_signal", "net_buy_volume", "push_n", "px_to_support_ticks")
