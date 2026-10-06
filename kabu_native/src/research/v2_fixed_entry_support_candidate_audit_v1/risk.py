"""Open-risk, soft-exit integrity, and concentration measures. No new trading rule."""
from __future__ import annotations

import heapq
from typing import Any, Optional

import numpy as np

from research.event_time_impulse_complete_strategy_v2 import RECONFIRM_GAP_SEC
from research.event_time_impulse_complete_strategy_v2.rules import (
    activity_lost,
    exhausted,
    participation_lost,
    reconfirm,
    support_failure,
)
from research.event_time_impulse_complete_strategy_v2.simulate import _bid_at_or_after, _last_bid


def _q(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    return float(np.percentile(np.array(values, dtype=np.float64), pct))


def distribution(values: list[float]) -> dict[str, Optional[float]]:
    return {
        "median": _q(values, 50),
        "p75": _q(values, 75),
        "p90": _q(values, 90),
        "p95": _q(values, 95),
        "p99": _q(values, 99),
        "max": None if not values else float(np.max(values)),
        "min": None if not values else float(np.min(values)),
    }


class EquityTracker:
    def __init__(self) -> None:
        self.equity = 0.0
        self.peak = 0.0
        self.max_dd = 0.0
        self.worst_one = 0.0
        self.worst_agg = 0.0
        self.max_active = 0

    def observe(self, equity: float, open_marks: list[float]) -> None:
        self.peak = max(self.peak, equity)
        self.max_dd = min(self.max_dd, equity - self.peak)
        agg = float(sum(open_marks)) if open_marks else 0.0
        self.worst_agg = min(self.worst_agg, agg)
        if open_marks:
            self.worst_one = min(self.worst_one, min(open_marks))
        self.max_active = max(self.max_active, len(open_marks))

    def snapshot(self) -> dict[str, float]:
        return {
            "mtm_max_drawdown_yen": self.max_dd,
            "worst_open_loss_one_yen": self.worst_one,
            "worst_aggregate_open_loss_yen": self.worst_agg,
            "max_active_positions": self.max_active,
        }


def _push_bid(queue: list, book: dict[str, Any], sym: str, start: int, seq: list[int]) -> None:
    bt, bb, bn = book["bt"], book["bb"], book["bn"]
    i = start
    n = int(bt.size)
    last = None
    while i < n:
        if int(bn[i]) == i:
            px = float(bb[i])
            if px == px and px > 0 and px != last:
                seq[0] += 1
                heapq.heappush(queue, (float(bt[i]), 1, seq[0], sym, (i, px)))
                return
        i += 1


def mark_session(trades: list[dict[str, Any]], books: dict[str, Any], trackers: list[EquityTracker]) -> None:
    """Walk executable bids while positions are open. Equity carries each tracker's prior realized PnL."""
    queue: list = []
    seq = [0]
    for trade in trades:
        if trade.get("pnl_yen") is None or trade.get("exit_t") is None or trade.get("symbol") not in books:
            continue
        sym = str(trade["symbol"])
        seq[0] += 1
        heapq.heappush(queue, (float(trade["entry_t"]), 2, seq[0], sym, trade))
        seq[0] += 1
        heapq.heappush(queue, (float(trade["exit_t"]), 0, seq[0], sym, trade))
    open_pos: dict[str, dict[str, float]] = {}
    realized = {id(tr): float(tr.equity) for tr in trackers}
    while queue:
        _t, kind, _seq, sym, payload = heapq.heappop(queue)
        if kind == 1:
            index, px = payload
            row = open_pos.get(sym)
            if row is not None and _t <= row["exit_t"]:
                row["bid"] = float(px)
                _range(row)
                _push_bid(queue, books[sym], sym, int(index) + 1, seq)
        elif kind == 0:
            trade = payload
            row = open_pos.get(sym)
            if row is not None:
                row["bid"] = float(trade["exit_px"])
                _range(row)
                trade["mae_yen"] = row["mae"]
                trade["mfe_yen"] = row["mfe"]
                trade["max_giveback_yen"] = row["giveback"]
                open_pos.pop(sym, None)
            for tr in trackers:
                realized[id(tr)] += float(trade["pnl_yen"])
        else:
            trade = payload
            book = books[sym]
            loc = int(np.searchsorted(book["bt"], float(trade["entry_t"]), side="right")) - 1
            bid = float(trade["entry_px"])
            while loc >= 0:
                if int(book["bn"][loc]) == loc and float(book["bb"][loc]) > 0:
                    bid = float(book["bb"][loc])
                    break
                loc -= 1
            open_pos[sym] = {
                "entry": float(trade["entry_px"]), "bid": bid, "exit_t": float(trade["exit_t"]),
                "mae": 0.0, "mfe": 0.0, "peak": 0.0, "giveback": 0.0,
            }
            _range(open_pos[sym])
            _push_bid(queue, book, sym, int(np.searchsorted(book["bt"], float(trade["entry_t"]), side="left")), seq)
        marks = [(row["bid"] - row["entry"]) * 100.0 for row in open_pos.values()]
        for tr in trackers:
            tr.equity = realized[id(tr)] + float(sum(marks))
            tr.observe(tr.equity, marks)
    for tr in trackers:
        tr.equity = realized[id(tr)]
        tr.peak = max(tr.peak, tr.equity)


def _range(row: dict[str, float]) -> None:
    mark = (row["bid"] - row["entry"]) * 100.0
    row["mae"] = min(float(row["mae"]), mark)
    row["mfe"] = max(float(row["mfe"]), mark)
    row["peak"] = max(float(row["peak"]), mark)
    row["giveback"] = max(float(row["giveback"]), float(row["peak"]) - mark)


def _fill_matches(found: Optional[tuple[float, float]], trade: dict[str, Any]) -> bool:
    if found is None:
        return False
    return abs(found[0] - float(trade["exit_t"])) <= 1e-6 and abs(found[1] - float(trade["exit_px"])) <= 1e-4


def verify_soft_exit(book: dict[str, Any], signal_index: int, trade: dict[str, Any], sess_end: float) -> dict[str, Any]:
    """Replay the fixed-support path with the same fill rule as the candidate simulator."""
    times = book["t"]
    n = int(times.size)
    index = int(signal_index)
    support = float(book["pre_high"][index])
    entry_t = float(times[index])
    last = entry_t
    last_part = None
    last_act = None
    decision_t = None
    part = act = False
    i = index + 1
    pending = None
    while i < n:
        price = float(book["px"][i])
        event_t = float(times[i])
        if not (price == price and price > 0.0):
            found = _bid_at_or_after(book["bt"], book["bb"], book["bn"], event_t, sess_end)
            return {"ok": False, "why": "invalid_price" if found is not None else "invalid_pending", "exit_decision_t": event_t}
        if reconfirm(
            bool(book["vol10"][i]), bool(book["vol30"][i]), bool(book["buy"][i]),
            float(book["classified"][i]), bool(book["tick"][i]), bool(book["break"][i]),
        ):
            last = event_t
        part = participation_lost(float(book["classified"][i]), float(book["ask10"][i]), float(book["bid10"][i]))
        act = activity_lost(bool(book["vol10"][i]), bool(book["vol30"][i]), bool(book["tick"][i]))
        if part and last_part is None:
            last_part = event_t
        if act and last_act is None:
            last_act = event_t
        if support_failure(price, support):
            return {"ok": False, "why": "hard_exit_first", "exit_decision_t": event_t, "last_reconfirm_t": last}
        if exhausted(part, act, event_t - last):
            decision_t = event_t
            found = _bid_at_or_after(book["bt"], book["bb"], book["bn"], event_t, sess_end)
            if found is None:
                pending = "no_bid_at_decision"
                break
            body = {
                "entry_t": entry_t, "last_reconfirm_t": last, "participation_lost_t": last_part,
                "activity_lost_t": last_act, "exit_decision_t": decision_t, "exit_fill_t": found[0],
                "participation_lost": part, "activity_lost": act,
            }
            if _fill_matches(found, trade):
                return {"ok": True, "why": "ok", **body}
            return {"ok": False, "why": "fill_mismatch", **body, "trade_exit_t": float(trade["exit_t"]), "trade_exit_px": float(trade["exit_px"])}
        i += 1
    found = _last_bid(book["bt"], book["bb"], book["bn"], entry_t, sess_end)
    if pending == "no_bid_at_decision" and decision_t is not None and _fill_matches(found, trade):
        return {
            "ok": True, "why": "session_last_bid", "entry_t": entry_t, "last_reconfirm_t": last,
            "participation_lost_t": last_part, "activity_lost_t": last_act, "exit_decision_t": decision_t,
            "exit_fill_t": None if found is None else found[0], "participation_lost": part, "activity_lost": act,
        }
    return {"ok": False, "why": pending or "no_soft_exit", "exit_decision_t": decision_t, "last_reconfirm_t": last}


def extra_path(book: dict[str, Any], t0: float, t1: float, ref: float) -> tuple[Optional[float], Optional[float]]:
    bt, bb, bn = book["bt"], book["bb"], book["bn"]
    lo = int(np.searchsorted(bt, float(t0), side="left"))
    hi = int(np.searchsorted(bt, float(t1), side="right"))
    mae = mfe = 0.0
    seen = False
    for i in range(lo, hi):
        if int(bn[i]) != i:
            continue
        px = float(bb[i])
        if not (px == px and px > 0):
            continue
        mark = (px - ref) * 100.0
        mae = min(mae, mark) if seen else mark
        mfe = max(mfe, mark) if seen else mark
        seen = True
    if not seen:
        return None, None
    return mae, mfe
