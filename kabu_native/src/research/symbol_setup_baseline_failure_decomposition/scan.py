"""One diagnostic pass over the frozen 35-session surface. Not a new strategy replay."""
from __future__ import annotations

import gc
from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.am_entry_profit_improvement import DEV_WAIT_SEC
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY
from research.entry_execution_feasibility.fill import standalone_fill
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from research.simple_tech_entry_family.harvest import _Buf, _bare, _board_row, _snap_at
from research.simple_tech_entry_family.indicators import ema
from research.simple_tech_entry_family.stages import (
    attach_indicators,
    board_support,
    calendar_bar_index,
    evaluable,
    execution_eligible,
    price_action,
    pullback_setup,
    reversal_rci,
    trend_up,
    volume_confirm,
)
from research.simple_tech_entry_family.v7_bars import aggregate_bars
from research.symbol_setup_baseline_complete_development.replay import thesis_exit
from research.symbol_setup_baseline_complete_strategy_precommit.runner import thesis_live
from research.symbol_setup_baseline_failure_decomposition import (
    BAR_HORIZONS,
    FILL_MARK_HORIZONS,
    POST_EXIT_HORIZONS,
    QUOTE_HORIZONS,
)
from small_paper.v1r_live_dual_lane import session_end_for_position

# Day counts printed by the frozen development replay. A mismatch aborts the diagnostic.
EXPECTED_S5 = {
    "20260722": 2, "20260723": 2, "20260724": 1, "20260727": 0, "20260728": 1, "20260729": 4,
    "20260730": 4, "20260731": 6, "20260803": 5, "20260804": 4, "20260805": 0, "20260806": 3,
    "20260807": 9, "20260810": 4, "20260812": 4, "20260813": 1, "20260817": 4, "20260818": 3,
    "20260819": 5, "20260820": 10, "20260821": 5, "20260824": 7, "20260825": 1, "20260826": 1,
    "20260827": 10, "20260828": 3, "20260831": 2, "20260901": 1, "20260902": 0, "20260903": 6,
    "20260904": 1, "20260907": 6, "20260908": 3, "20260909": 2, "20260910": 1,
}
EXPECTED_S6 = {
    "20260722": 0, "20260723": 0, "20260724": 1, "20260727": 0, "20260728": 1, "20260729": 1,
    "20260730": 0, "20260731": 2, "20260803": 2, "20260804": 1, "20260805": 0, "20260806": 1,
    "20260807": 2, "20260810": 2, "20260812": 1, "20260813": 0, "20260817": 3, "20260818": 1,
    "20260819": 0, "20260820": 3, "20260821": 3, "20260824": 2, "20260825": 1, "20260826": 1,
    "20260827": 7, "20260828": 2, "20260831": 1, "20260901": 1, "20260902": 0, "20260903": 1,
    "20260904": 0, "20260907": 3, "20260908": 0, "20260909": 0, "20260910": 1,
}
EXPECTED_FILL = {
    "20260722": 0, "20260723": 0, "20260724": 1, "20260727": 0, "20260728": 0, "20260729": 0,
    "20260730": 0, "20260731": 0, "20260803": 0, "20260804": 0, "20260805": 0, "20260806": 0,
    "20260807": 0, "20260810": 1, "20260812": 0, "20260813": 0, "20260817": 2, "20260818": 0,
    "20260819": 0, "20260820": 0, "20260821": 2, "20260824": 0, "20260825": 0, "20260826": 0,
    "20260827": 0, "20260828": 0, "20260831": 0, "20260901": 0, "20260902": 0, "20260903": 0,
    "20260904": 0, "20260907": 0, "20260908": 0, "20260909": 0, "20260910": 1,
}
FROZEN_TRADES = {
    ("20260724", "6522", -3200.0, "SESSION_FAIL_CLOSE"),
    ("20260810", "5706", -9000.0, "SLOW_TREND_SLOPE_LOSS"),
    ("20260817", "285A", -41000.0, "SLOW_TREND_SLOPE_LOSS"),
    ("20260817", "3099", -500.0, "SLOW_TREND_SLOPE_LOSS"),
    ("20260821", "285A", 27000.0, "SLOW_TREND_SLOPE_LOSS"),
    ("20260821", "3103", -300.0, "SLOW_TREND_SLOPE_LOSS"),
    ("20260910", "5713", -4500.0, "BOTH_TREND_COMPONENTS_LOST"),
}
STAGE_ORDER = (
    "MA_TREND",
    "BB_LOCATION",
    "RCI_REVERSAL",
    "PRICE_ACTION_TRIGGER",
    "VOLUME_PARTICIPATION",
    "BOARD_SUPPORT_VETO",
)


class PopulationMismatch(RuntimeError):
    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail))
        self.detail = detail


def _num(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return x


def _bps(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    return (b / a - 1.0) * 10000.0


def _next_ok(ok: np.ndarray) -> np.ndarray:
    n = int(ok.size)
    nxt = np.full(n, -1, dtype=np.int32)
    nxt_i = -1
    for i in range(n - 1, -1, -1):
        if bool(ok[i]):
            nxt_i = i
        nxt[i] = nxt_i
    return nxt


def _mask(board: dict[str, np.ndarray], side: str) -> np.ndarray:
    n = int(board["t"].size)
    if n == 0:
        return np.asarray([], dtype=bool)
    px = board[side]
    qty = board[f"{side}_qty"]
    fresh = board["fresh_sec"]
    return (
        board["executable"]
        & ~board["special"]
        & np.isfinite(fresh)
        & (fresh <= float(BOARD_FRESHNESS_SEC) + 1e-12)
        & np.isfinite(px)
        & (px > 0)
        & np.isfinite(qty)
        & (qty >= float(MIN_QTY) - 1e-12)
    )


def _first(t: np.ndarray, px: np.ndarray, nxt: np.ndarray, event_t: float, sess_end: float) -> tuple[Optional[float], Optional[float]]:
    n = int(t.size)
    if n == 0:
        return None, None
    i0 = int(np.searchsorted(t, float(event_t), side="left"))
    if i0 >= n:
        return None, None
    j = int(nxt[i0])
    if j < 0:
        return None, None
    tj = float(t[j])
    if tj > float(sess_end) + 1e-12:
        return None, None
    return tj, float(px[j])


def _bar_bps(ind: dict[str, np.ndarray], i: int) -> dict[int, Optional[float]]:
    minutes = ind["minute_epoch"]
    close = ind["close"]
    c0 = _num(close[i])
    out: dict[int, Optional[float]] = {}
    for k in BAR_HORIZONS:
        if c0 is None or c0 <= 0:
            out[k] = None
            continue
        j = calendar_bar_index(minutes, float(minutes[i]), int(k))
        cj = _num(close[j]) if j is not None else None
        out[k] = _bps(c0, cj)
    return out


def _quote_bps(t: np.ndarray, ask: np.ndarray, bid: np.ndarray, nxt_ask: np.ndarray, nxt_bid: np.ndarray, t0: float, am_end: float) -> dict[int, Optional[float]]:
    _, entry = _first(t, ask, nxt_ask, t0, am_end)
    out: dict[int, Optional[float]] = {}
    for h in QUOTE_HORIZONS:
        if entry is None:
            out[h] = None
            continue
        _, exit_bid = _first(t, bid, nxt_bid, t0 + float(h), am_end)
        out[h] = _bps(entry, exit_bid)
    return out


def _w5(board: dict[str, np.ndarray], ask_ok: np.ndarray, bid_ok: np.ndarray, t0: float) -> dict[str, Optional[float]]:
    t = board["t"]
    if int(t.size) == 0:
        return {"min_ask_w5": None, "max_bid_w5": None}
    i0 = int(np.searchsorted(t, float(t0), side="left"))
    i1 = int(np.searchsorted(t, float(t0) + float(DEV_WAIT_SEC), side="right"))
    asks = board["ask"][i0:i1][ask_ok[i0:i1]] if i1 > i0 else np.asarray([])
    bids = board["bid"][i0:i1][bid_ok[i0:i1]] if i1 > i0 else np.asarray([])
    return {
        "min_ask_w5": float(np.min(asks)) if asks.size else None,
        "max_bid_w5": float(np.max(bids)) if bids.size else None,
    }


def _path(board: dict[str, np.ndarray], bid_ok: np.ndarray, nxt_bid: np.ndarray, *, fill_t: float, fill_px: float, exit_t: float, am_end: float) -> dict[str, Any]:
    t = board["t"]
    bid = board["bid"]
    i0 = int(np.searchsorted(t, float(fill_t), side="left"))
    i1 = int(np.searchsorted(t, float(exit_t), side="right"))
    mfe = None
    mae = None
    t_mfe = None
    t_mae = None
    for i in range(i0, i1):
        if not bool(bid_ok[i]):
            continue
        ti = float(t[i])
        if ti + 1e-12 < float(fill_t) or ti > float(exit_t) + 1e-12:
            continue
        r = _bps(fill_px, float(bid[i]))
        if r is None:
            continue
        if mfe is None or r > mfe:
            mfe = r
            t_mfe = ti - float(fill_t)
        if mae is None or r < mae:
            mae = r
            t_mae = ti - float(fill_t)
    realized = _bps(fill_px, None)
    marks = {}
    for h in FILL_MARK_HORIZONS:
        _, px = _first(t, bid, nxt_bid, float(fill_t) + float(h), am_end)
        marks[f"bid_markout_{h}s_bps"] = _bps(fill_px, px)
    return {
        "mfe_bps": mfe,
        "mae_bps": mae,
        "time_to_mfe": t_mfe,
        "time_to_mae": t_mae,
        **marks,
    }


def _htf(fin5: np.ndarray, e9: np.ndarray, e21: np.ndarray, t0: float) -> dict[str, Any]:
    if int(fin5.size) == 0:
        return {"htf_observable": False, "htf_class": "HTF_TREND_NOT_ALIGNED", "htf_ema9": None, "htf_ema21": None, "htf_ema21_lag3": None}
    j = int(np.searchsorted(fin5, float(t0), side="right")) - 1
    if j < 23:
        return {"htf_observable": False, "htf_class": "HTF_TREND_NOT_ALIGNED", "htf_ema9": None, "htf_ema21": None, "htf_ema21_lag3": None}
    a, b, c = _num(e9[j]), _num(e21[j]), _num(e21[j - 3])
    if a is None or b is None or c is None:
        return {"htf_observable": False, "htf_class": "HTF_TREND_NOT_ALIGNED", "htf_ema9": a, "htf_ema21": b, "htf_ema21_lag3": c}
    aligned = bool(thesis_live(a, b, c))
    return {
        "htf_observable": True,
        "htf_class": "HTF_TREND_ALIGNED" if aligned else "HTF_TREND_NOT_ALIGNED",
        "htf_ema9": a,
        "htf_ema21": b,
        "htf_ema21_lag3": c,
    }


def _recovery(ind: dict[str, np.ndarray], loss_i: Optional[int], am_end: float) -> dict[str, Any]:
    out: dict[str, Any] = {"loss_bar_index": loss_i}
    if loss_i is None:
        for k in (1, 2, 3):
            out[f"live_at_plus_{k}"] = None
            out[f"recovered_within_{k}"] = None
        return out
    flags = []
    for k in (1, 2, 3):
        j = int(loss_i) + k
        live = None
        if j < int(ind["finalize_t"].size) and float(ind["finalize_t"][j]) <= float(am_end) + 1e-12:
            e9, e21 = _num(ind["ema9"][j]), _num(ind["ema21"][j])
            e21p = _num(ind["ema21"][j - 3]) if j >= 3 else None
            live = bool(thesis_live(e9, e21, e21p)) if e9 is not None and e21 is not None and e21p is not None else None
        flags.append(live)
        out[f"live_at_plus_{k}"] = live
        seen = [x for x in flags if x is not None]
        out[f"recovered_within_{k}"] = (True in seen) if seen else None
    return out


def _loss_state(ind: dict[str, np.ndarray], i: int) -> dict[str, Any]:
    e9, e21 = _num(ind["ema9"][i]), _num(ind["ema21"][i])
    e21p = _num(ind["ema21"][i - 3]) if i >= 3 else None
    return {
        "ema9": e9,
        "ema21": e21,
        "ema21_lag3": e21p,
        "ema9_minus_ema21": None if e9 is None or e21 is None else e9 - e21,
        "ema21_minus_lag3": None if e21 is None or e21p is None else e21 - e21p,
        "ema9_still_above_ema21": None if e9 is None or e21 is None else bool(e9 > e21),
        "slope_nonpositive": None if e21 is None or e21p is None else bool(e21 <= e21p),
    }


def _hold_scan(ind: dict[str, np.ndarray], board: dict[str, np.ndarray], bid_ok: np.ndarray, *, fill_t: float, fill_px: float, exit_t: float, am_end: float) -> dict[str, Any]:
    fin = ind["finalize_t"]
    start = int(np.searchsorted(fin, float(fill_t), side="right"))
    bars = 0
    live_n = 0
    below_ema9_n = 0
    worst_close_bps = None
    live_worst_bid = None
    t = board["t"]
    bid = board["bid"]
    for i in range(start, int(fin.size)):
        obs = float(fin[i])
        if obs <= float(fill_t) + 1e-12 or obs > float(am_end) + 1e-12:
            continue
        if obs > float(exit_t) + 1e-12:
            break
        e9, e21 = _num(ind["ema9"][i]), _num(ind["ema21"][i])
        e21p = _num(ind["ema21"][i - 3]) if i >= 3 else None
        live = bool(thesis_live(e9, e21, e21p)) if None not in (e9, e21, e21p) else False
        cl = _num(ind["close"][i])
        bars += 1
        if live:
            live_n += 1
        if e9 is not None and cl is not None and cl < e9:
            below_ema9_n += 1
        cb = _bps(fill_px, cl)
        if cb is not None and (worst_close_bps is None or cb < worst_close_bps):
            worst_close_bps = cb
        if live:
            left = float(fill_t) if i == 0 else max(float(fill_t), float(fin[i - 1]))
            i0 = int(np.searchsorted(t, left, side="left"))
            i1 = int(np.searchsorted(t, obs, side="right"))
            for k in range(i0, i1):
                if not bool(bid_ok[k]):
                    continue
                rb = _bps(fill_px, float(bid[k]))
                if rb is not None and (live_worst_bid is None or rb < live_worst_bid):
                    live_worst_bid = rb
    return {
        "hold_completed_bars": bars,
        "thesis_live_bars": live_n,
        "close_below_ema9_bars": below_ema9_n,
        "worst_close_bps_during_hold": worst_close_bps,
        "worst_executable_bid_bps_while_thesis_live": live_worst_bid,
    }


class Bag:
    def __init__(self) -> None:
        self.n = 0
        self.values: dict[str, list[float]] = {}

    def add(self, key: str, value: Optional[float]) -> None:
        self.values.setdefault(key, [])
        if key not in getattr(self, "_seen", {}):
            pass
        if value is not None:
            self.values[key].append(float(value))

    def bump(self) -> None:
        self.n += 1


def _empty_stages() -> dict[str, dict[str, Bag]]:
    return {name: {"input": Bag(), "pass": Bag()} for name in STAGE_ORDER}


def _add_response(bag: Bag, bar: dict[int, Optional[float]], quote: dict[int, Optional[float]]) -> None:
    bag.bump()
    for k, v in bar.items():
        bag.add(f"bar_{k}m_bps", v)
    for k, v in quote.items():
        bag.add(f"quote_{k}s_bps", v)


def _day(day: str, capture: Path, symbols: list[str], groups: dict[str, dict[str, str]]) -> dict[str, Any]:
    am_start = float(hm_epoch(day, 9, 0))
    am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
    bufs = {s: _Buf() for s in symbols}
    builders = {s: SymbolBarBuilder(am_start=am_start, am_end=am_end) for s in symbols}
    uni = set(symbols)
    for rec in iter_push(capture):
        sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
        if not sym or sym not in uni:
            continue
        pay = dict(rec.get("payload") or rec.get("original_payload") or {})
        et = capture_event_epoch(rec, pay)
        if et is None or float(et) < am_start - 120.0 or float(et) > am_end + 2.0:
            continue
        recv = record_event_stamp(rec)
        if recv:
            pay["received_at"] = recv
        row = _board_row(pay, float(et))
        bufs[sym].append(row)
        builders[sym].on_event(
            et=float(et),
            px=row["px"] if row["px"] == row["px"] else None,
            cum_vol=row.get("cum_vol"),
            bid=row["bid"] if row["bid"] == row["bid"] else None,
            ask=row["ask"] if row["ask"] == row["ask"] else None,
            continuous=bool(row.get("continuous")),
        )
    stages = _empty_stages()
    signals: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    veto_reasons: dict[str, int] = {}
    counts = {"s5": 0, "s6": 0, "s7": 0, "fill": 0}
    lineage = groups[day]["lineage"]
    fold = groups[day]["fold"]
    for sym in symbols:
        builders[sym].close_session()
        raw = builders[sym].as_arrays()
        bar_integrity(raw, am_start=am_start, am_end=am_end)
        if int(raw["minute_epoch"].size) == 0:
            continue
        ind = attach_indicators(raw)
        board = bufs[sym].view()
        t = board["t"]
        ask_ok = _mask(board, "ask")
        bid_ok = _mask(board, "bid")
        nxt_ask = _next_ok(ask_ok)
        nxt_bid = _next_ok(bid_ok)
        agg, _leak = aggregate_bars(raw, width_sec=300.0, am_start=am_start, am_end=am_end)
        e9_5 = ema(agg["close"], 9) if int(agg["close"].size) else np.asarray([])
        e21_5 = ema(agg["close"], 21) if int(agg["close"].size) else np.asarray([])
        fin5 = agg["finalize_t"] if int(agg["close"].size) else np.asarray([])
        n = int(ind["close"].size)
        for i in range(n):
            if not evaluable(i, n):
                continue
            t0 = float(ind["finalize_t"][i])
            if t0 + float(DEV_WAIT_SEC) > am_end + 1e-12:
                continue
            s1 = trend_up(ind, i)
            s2 = bool(s1) and pullback_setup(ind, i)
            s3 = bool(s2) and reversal_rci(ind, i)
            s4 = bool(s3) and price_action(ind, i)
            s5 = bool(s4) and volume_confirm(ind, i)
            flags = {
                "MA_TREND": s1,
                "BB_LOCATION": s2,
                "RCI_REVERSAL": s3,
                "PRICE_ACTION_TRIGGER": s4,
                "VOLUME_PARTICIPATION": s5,
            }
            # Quote/bar responses are computed for every stage input. Technical rows keep the full record.
            need = s1 or s5
            bar = _bar_bps(ind, i) if need or s2 or s3 or s4 else None
            quote = _quote_bps(t, board["ask"], board["bid"], nxt_ask, nxt_bid, t0, am_end) if bar is not None else None
            prev = True
            for name in STAGE_ORDER[:5]:
                if prev:
                    if bar is None:
                        bar = _bar_bps(ind, i)
                        quote = _quote_bps(t, board["ask"], board["bid"], nxt_ask, nxt_bid, t0, am_end)
                    _add_response(stages[name]["input"], bar, quote)
                if prev and flags[name]:
                    _add_response(stages[name]["pass"], bar, quote)
                prev = bool(prev and flags[name])
            if not s5:
                continue
            counts["s5"] += 1
            snap = _snap_at(board, t0)
            board_ok, meta = board_support(snap) if snap.get("ok") else (False, {"board_reason": "NO_BOARD"})
            reason = str(meta.get("board_reason") or "")
            if not board_ok:
                veto_reasons[reason or "NO_BOARD"] = int(veto_reasons.get(reason or "NO_BOARD", 0)) + 1
            _add_response(stages["BOARD_SUPPORT_VETO"]["input"], bar, quote)
            if board_ok:
                counts["s6"] += 1
                _add_response(stages["BOARD_SUPPORT_VETO"]["pass"], bar, quote)
            bid = _num(snap.get("bid")) if snap.get("ok") else None
            ask = _num(snap.get("ask")) if snap.get("ok") else None
            bq = _num(snap.get("bid_qty")) if snap.get("ok") else None
            aq = _num(snap.get("ask_qty")) if snap.get("ok") else None
            fresh = _num(snap.get("fresh_sec")) if snap.get("ok") else None
            spread = _num(snap.get("spread_bps")) if snap.get("ok") else None
            eligible = bool(snap.get("ok") and execution_eligible(snap) and bid is not None and bid > 0)
            htf = _htf(fin5, e9_5, e21_5, t0)
            row = {
                "date": day,
                "symbol": sym,
                "lineage": lineage,
                "fold": fold,
                "signal_time": t0,
                "close": _num(ind["close"][i]),
                "bid": bid,
                "ask": ask,
                "spread_bps": spread,
                "bid_qty": bq,
                "ask_qty": aq,
                "ask_bid_qty_ratio": (aq / bq) if aq is not None and bq not in (None, 0) else None,
                "fresh_sec": fresh,
                "execution_eligible": bool(snap.get("ok") and execution_eligible(snap)),
                "board_pass": bool(board_ok),
                "board_reason": reason,
                "bar_bps": {str(k): bar[k] for k in BAR_HORIZONS},
                "quote_bps": {str(k): quote[k] for k in QUOTE_HORIZONS},
                **htf,
            }
            signals.append(row)
            if not (board_ok and eligible):
                continue
            counts["s7"] += 1
            limit = float(bid)
            fill = standalone_fill(board, t0=t0, wait_sec=float(DEV_WAIT_SEC), limit_price=limit, sess_end=float(am_end))
            w5 = _w5(board, ask_ok, bid_ok, t0)
            pend = {
                **row,
                "limit_bid": limit,
                "signal_ask": ask,
                "signal_spread_bps": spread,
                "fill_or_expire": "FILLED" if fill.get("WOULD_FILL") else "EXPIRED",
                "fill_time": fill.get("fill_t"),
                "fill_latency_sec": (float(fill["fill_t"]) - t0) if fill.get("WOULD_FILL") and fill.get("fill_t") is not None else None,
                **w5,
            }
            pending.append(pend)
            if not (fill.get("WOULD_FILL") and fill.get("fill_t") is not None and fill.get("fill_price") is not None):
                continue
            counts["fill"] += 1
            ex = thesis_exit(ind, board, fill_t=float(fill["fill_t"]), fill_px=float(fill["fill_price"]), am_end=am_end)
            loss_i = None
            if ex.get("thesis_lost_at") is not None:
                loss_i = int(np.searchsorted(ind["finalize_t"], float(ex["thesis_lost_at"]), side="left"))
                if loss_i >= n or abs(float(ind["finalize_t"][loss_i]) - float(ex["thesis_lost_at"])) > 1e-6:
                    loss_i = None
            state = _loss_state(ind, loss_i) if loss_i is not None else {}
            post = {}
            if ex.get("exit_t") is not None and ex.get("exit_price") is not None:
                for h in POST_EXIT_HORIZONS:
                    _, px = _first(t, board["bid"], nxt_bid, float(ex["exit_t"]) + float(h), am_end)
                    post[f"post_exit_{h}s_bps"] = _bps(float(ex["exit_price"]), px)
                path = _path(
                    board, bid_ok, nxt_bid,
                    fill_t=float(fill["fill_t"]), fill_px=float(fill["fill_price"]),
                    exit_t=float(ex["exit_t"]), am_end=am_end,
                )
                realized = _bps(float(fill["fill_price"]), float(ex["exit_price"]))
                path["realized_bps"] = realized
                path["giveback_bps"] = None if path["mfe_bps"] is None or realized is None else float(path["mfe_bps"]) - float(realized)
            else:
                path = {"realized_bps": None, "mfe_bps": None, "mae_bps": None, "giveback_bps": None, "time_to_mfe": None, "time_to_mae": None}
            hold = {}
            if ex.get("exit_t") is not None:
                hold = _hold_scan(
                    ind, board, bid_ok,
                    fill_t=float(fill["fill_t"]), fill_px=float(fill["fill_price"]),
                    exit_t=float(ex["exit_t"]), am_end=am_end,
                )
            trades.append(
                {
                    "date": day,
                    "symbol": sym,
                    "lineage": lineage,
                    "fold": fold,
                    "signal_time": t0,
                    "fill_time": float(fill["fill_t"]),
                    "entry_price": float(fill["fill_price"]),
                    "exit_time": ex.get("exit_t"),
                    "exit_price": ex.get("exit_price"),
                    "exit_reason": ex.get("exit_reason"),
                    "pnl_yen_100": ex.get("pnl_yen_100"),
                    "thesis_lost_at": ex.get("thesis_lost_at"),
                    "thesis_loss_reason": ex.get("thesis_loss_reason"),
                    "fill_latency_sec": pend["fill_latency_sec"],
                    "spread_bps": spread,
                    "ask_bid_qty_ratio": row["ask_bid_qty_ratio"],
                    "htf_class": htf["htf_class"],
                    "htf_observable": htf["htf_observable"],
                    "loss_state": state,
                    "recovery": _recovery(ind, loss_i, am_end),
                    "post_exit": post,
                    "hold": hold,
                    **path,
                }
            )
    return {"counts": counts, "stages": stages, "signals": signals, "pending": pending, "trades": trades, "veto_reasons": veto_reasons}


def _merge_bags(dst: Bag, src: Bag) -> None:
    dst.n += src.n
    for key, vals in src.values.items():
        dst.values.setdefault(key, []).extend(vals)


def scan(sessions: list[dict[str, Any]], groups: dict[str, dict[str, str]]) -> dict[str, Any]:
    stages = _empty_stages()
    signals: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    veto: dict[str, int] = {}
    totals = {"s5": 0, "s6": 0, "s7": 0, "fill": 0}
    for sess in sessions:
        day = str(sess["date"])
        if day > "20260910":
            raise RuntimeError("surface_past_cutoff")
        cap = find_capture_dir(day)
        if cap is None:
            raise RuntimeError(f"missing_capture:{day}")
        one = _day(day, Path(cap), [str(s) for s in list(sess["symbols"])], groups)
        c = one["counts"]
        print(f"{day} s5={c['s5']} s6={c['s6']} s7={c['s7']} fill={c['fill']}", flush=True)
        if c["s5"] != EXPECTED_S5[day] or c["s6"] != EXPECTED_S6[day] or c["s7"] != EXPECTED_S6[day] or c["fill"] != EXPECTED_FILL[day]:
            raise PopulationMismatch({"date": day, "got": c, "expected_s5": EXPECTED_S5[day], "expected_s6": EXPECTED_S6[day], "expected_fill": EXPECTED_FILL[day]})
        for key in totals:
            totals[key] += int(c[key])
        for name in STAGE_ORDER:
            _merge_bags(stages[name]["input"], one["stages"][name]["input"])
            _merge_bags(stages[name]["pass"], one["stages"][name]["pass"])
        signals.extend(one["signals"])
        pending.extend(one["pending"])
        trades.extend(one["trades"])
        for k, v in one["veto_reasons"].items():
            veto[k] = int(veto.get(k, 0)) + int(v)
        gc.collect()
    got = {(r["date"], r["symbol"], float(r["pnl_yen_100"]), str(r["exit_reason"])) for r in trades}
    if totals["s5"] != 121 or totals["s6"] != 44 or totals["s7"] != 44 or totals["fill"] != 7 or (44 - totals["fill"]) != 37 or got != FROZEN_TRADES:
        raise PopulationMismatch({"totals": totals, "trade_match": got == FROZEN_TRADES, "trades": sorted(got)})
    return {"totals": totals, "stages": stages, "signals": signals, "pending": pending, "trades": trades, "veto_reasons": veto}
