"""Futures book-pressure engine. Price returns are outcomes/controls only. No sendorder."""
from __future__ import annotations

import math
import statistics
from bisect import bisect_right
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.futures_context_day1_effect_check_v1.engine import (
    AsOf,
    expected_symbols,
    fut_px,
    load_stock_series,
    mean,
    median,
    stock_outcomes_at,
)
from research.futures_microstructure_pressure_day1_v1 import (
    EARLY_END_HM,
    GRID_END_HM,
    GRID_START_HM,
    IMB_MEDIAN_WINDOW_SEC,
    LATE_START_HM,
    PLACEBO_SHIFT_SEC,
    QUOTE_PRESSURE_WINDOW_SEC,
    SIGNED_FEATURES,
    STAGE1_HORIZONS_SEC,
    STAGE2_HORIZONS_SEC,
    STAGE2_LABEL,
    TRADING_DATE,
)
from research.new_causal_information_acquisition_v1.reconcile import iter_jsonl, parse_received_at
from research.new_causal_information_acquisition_v1.writer import day_layout

JST = ZoneInfo("Asia/Tokyo")


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _epoch(dt: datetime) -> float:
    return dt.astimezone(JST).timestamp()


def clock_dt(day: str, hm: tuple[int, int]) -> datetime:
    return datetime(int(day[:4]), int(day[4:6]), int(day[6:8]), hm[0], hm[1], 0, tzinfo=JST)


def minute_grid(day: str) -> list[datetime]:
    t = clock_dt(day, GRID_START_HM)
    end = clock_dt(day, GRID_END_HM)
    out = []
    while t <= end:
        out.append(t)
        t += timedelta(minutes=1)
    return out


def block_of(hm: tuple[int, int]) -> str:
    minutes = hm[0] * 60 + hm[1]
    early_end = EARLY_END_HM[0] * 60 + EARLY_END_HM[1]
    late_start = LATE_START_HM[0] * 60 + LATE_START_HM[1]
    if minutes <= early_end:
        return "EARLY"
    if minutes >= late_start:
        return "LATE"
    return "OTHER"


def sgn(x: Optional[float]) -> int:
    if x is None:
        return 0
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def imb(bid_qty: Optional[float], ask_qty: Optional[float]) -> Optional[float]:
    if bid_qty is None or ask_qty is None:
        return None
    if bid_qty < 0 or ask_qty < 0:
        return None
    den = bid_qty + ask_qty
    if den == 0:
        return None
    return (bid_qty - ask_qty) / den


def level_qty(level: Any) -> Optional[float]:
    if not isinstance(level, dict):
        return None
    q = _f(level.get("qty") if level.get("qty") is not None else level.get("Qty"))
    if q is None or q < 0:
        return None
    return q


def depth_side_qty(rec: dict[str, Any], prefix: str) -> Optional[float]:
    total = 0.0
    seen = False
    for i in range(1, 11):
        q = level_qty(rec.get(f"{prefix}{i}"))
        if q is None:
            continue
        total += q
        seen = True
    return total if seen else None


def l1_qty(rec: dict[str, Any], side: str) -> Optional[float]:
    if side == "bid":
        q = level_qty(rec.get("Bid1"))
        if q is not None:
            return q
        return level_qty(rec.get("Buy1"))
    q = level_qty(rec.get("Ask1"))
    if q is not None:
        return q
    return level_qty(rec.get("Sell1"))


def l1_px(rec: dict[str, Any], side: str) -> Optional[float]:
    key = "Bid1" if side == "bid" else "Ask1"
    lv = rec.get(key)
    if isinstance(lv, dict):
        px = _f(lv.get("price") if lv.get("price") is not None else lv.get("Price"))
        if px is not None and px > 0:
            return px
    alt = rec.get("Buy1") if side == "bid" else rec.get("Sell1")
    if isinstance(alt, dict):
        px = _f(alt.get("Price"))
        if px is not None and px > 0:
            return px
    return None


class FutTape:
    def __init__(
        self,
        times: list[float],
        px: list[Optional[float]],
        l1: list[Optional[float]],
        depth: list[Optional[float]],
        bid_upd: list[int],
        ask_upd: list[int],
        px_times: list[float],
        px_vals: list[float],
    ) -> None:
        self.times = times
        self.px = px
        self.l1 = l1
        self.depth = depth
        self.bid_upd = bid_upd
        self.ask_upd = ask_upd
        self.px_asof = AsOf(px_times, px_vals)

    def px_at(self, t_epoch: float) -> Optional[float]:
        v, _ = self.px_asof.at(t_epoch)
        if v is None or not math.isfinite(float(v)) or float(v) <= 0:
            return None
        return float(v)


def load_fut_tape(path: Path) -> FutTape:
    times: list[float] = []
    px: list[Optional[float]] = []
    l1: list[Optional[float]] = []
    depth: list[Optional[float]] = []
    bid_upd: list[int] = []
    ask_upd: list[int] = []
    px_times: list[float] = []
    px_vals: list[float] = []
    prev_bid: tuple[Optional[float], Optional[float]] = (None, None)
    prev_ask: tuple[Optional[float], Optional[float]] = (None, None)
    for _i, rec in iter_jsonl(path):
        if rec is None:
            continue
        dt = parse_received_at(rec.get("received_at"))
        if dt is None:
            continue
        bp, bq = l1_px(rec, "bid"), l1_qty(rec, "bid")
        ap, aq = l1_px(rec, "ask"), l1_qty(rec, "ask")
        bu = 0
        au = 0
        if times:
            if (bp, bq) != prev_bid:
                bu = 1
            if (ap, aq) != prev_ask:
                au = 1
        te = _epoch(dt)
        times.append(te)
        p = fut_px(rec)
        px.append(p)
        if p is not None:
            px_times.append(te)
            px_vals.append(p)
        l1.append(imb(bq, aq))
        depth.append(imb(depth_side_qty(rec, "Buy"), depth_side_qty(rec, "Sell")))
        bid_upd.append(bu)
        ask_upd.append(au)
        prev_bid, prev_ask = (bp, bq), (ap, aq)
    return FutTape(times, px, l1, depth, bid_upd, ask_upd, px_times, px_vals)


def window_median(times: list[float], values: list[Optional[float]], t_end: float, lookback: int) -> tuple[Optional[float], int]:
    i0 = bisect_right(times, t_end - float(lookback))
    i1 = bisect_right(times, t_end)
    xs = [float(values[i]) for i in range(i0, i1) if values[i] is not None]
    return median(xs), len(xs)


def quote_pressure(tape: FutTape, t_end: float, lookback: int = QUOTE_PRESSURE_WINDOW_SEC) -> tuple[Optional[float], int, int]:
    i0 = bisect_right(tape.times, t_end - float(lookback))
    i1 = bisect_right(tape.times, t_end)
    bid_n = int(sum(tape.bid_upd[i0:i1]))
    ask_n = int(sum(tape.ask_upd[i0:i1]))
    den = bid_n + ask_n
    if den == 0:
        return None, bid_n, ask_n
    return (bid_n - ask_n) / den, bid_n, ask_n


def px_ret(tape: FutTape, t0: float, horizon_sec: int) -> Optional[float]:
    a = tape.px_at(t0)
    b = tape.px_at(t0 + float(horizon_sec))
    if a is None or b is None or a == 0:
        return None
    return (b - a) / a


def stock_mid_ret(quotes: AsOf, t_epoch: float, lookback_sec: int) -> Optional[float]:
    now, _ = quotes.at(t_epoch)
    lag, _ = quotes.at(t_epoch - float(lookback_sec))
    if now is None or lag is None:
        return None
    m1, m0 = now[2], lag[2]
    if m1 is None or m0 is None or m0 == 0:
        return None
    return (float(m1) - float(m0)) / float(m0)


def ew_stock_past(stocks: dict[str, AsOf], symbols: list[str], t_epoch: float, lookback: int) -> Optional[float]:
    xs = []
    for sym in symbols:
        series = stocks.get(sym)
        if series is None:
            continue
        r = stock_mid_ret(series, t_epoch, lookback)
        if r is not None:
            xs.append(r)
    return mean(xs)


def bucket(clocks: list[dict[str, Any]], feat: str, outcome: str) -> dict[str, Any]:
    pos = [c for c in clocks if isinstance(c.get(feat), (int, float)) and c[feat] > 0]
    neg = [c for c in clocks if isinstance(c.get(feat), (int, float)) and c[feat] < 0]
    zed = [c for c in clocks if isinstance(c.get(feat), (int, float)) and c[feat] == 0]
    pos_xs = [float(c[outcome]) for c in pos if c.get(outcome) is not None]
    neg_xs = [float(c[outcome]) for c in neg if c.get(outcome) is not None]
    all_xs = [float(c[outcome]) for c in clocks if c.get(outcome) is not None]
    pos_m, neg_m = mean(pos_xs), mean(neg_xs)
    pop_m = mean(all_xs)
    shift = None
    if pos_m is not None and neg_m is not None:
        shift = pos_m - neg_m
    elif pos_m is not None:
        shift = pos_m
    return {
        "feature": feat,
        "outcome": outcome,
        "pos_n": len(pos_xs),
        "neg_n": len(neg_xs),
        "zero_n": len(zed),
        "pos_mean": pos_m,
        "neg_mean": neg_m,
        "pos_median": median(pos_xs),
        "neg_median": median(neg_xs),
        "pop_mean": pop_m,
        "shift": shift,
        "direction": sgn(shift),
        "pos_minus_pop": (pos_m - pop_m) if pos_m is not None and pop_m is not None else None,
        "neg_minus_pop": (neg_m - pop_m) if neg_m is not None and pop_m is not None else None,
    }


def four_state(clocks: list[dict[str, Any]], press: str, prior: str, outcome: str) -> dict[str, Any]:
    names = {
        (1, 1): "press_buy_prior_up",
        (1, -1): "press_buy_prior_down",
        (-1, 1): "press_sell_prior_up",
        (-1, -1): "press_sell_prior_down",
    }
    buckets: dict[str, list[float]] = {n: [] for n in names.values()}
    members: dict[str, list[str]] = {n: [] for n in names.values()}
    for c in clocks:
        name = names.get((sgn(c.get(press)), sgn(c.get(prior))))
        if name is None or c.get(outcome) is None:
            continue
        buckets[name].append(float(c[outcome]))
        members[name].append(str(c.get("clock")))
    out = {}
    for name, xs in buckets.items():
        out[name] = {"n": len(xs), "mean": mean(xs), "median": median(xs), "clocks_n": len(members[name])}
    buy_down = out["press_buy_prior_down"]["mean"]
    buy_up = out["press_buy_prior_up"]["mean"]
    sell_up = out["press_sell_prior_up"]["mean"]
    sell_down = out["press_sell_prior_down"]["mean"]
    against = None
    aligned = None
    if buy_down is not None and sell_up is not None:
        against = buy_down - sell_up
    if buy_up is not None and sell_down is not None:
        aligned = buy_up - sell_down
    explains = False
    if aligned is not None and sgn(aligned) != 0:
        if against is None or sgn(against) != sgn(aligned):
            explains = True
    return {"states": out, "aligned_shift": aligned, "against_shift": against, "prior_explains": explains}


def features_at(nk: FutTape, tx: FutTape, t_epoch: float) -> dict[str, Any]:
    nk_l1, nk_l1_n = window_median(nk.times, nk.l1, t_epoch, IMB_MEDIAN_WINDOW_SEC)
    tx_l1, tx_l1_n = window_median(tx.times, tx.l1, t_epoch, IMB_MEDIAN_WINDOW_SEC)
    nk_d, nk_d_n = window_median(nk.times, nk.depth, t_epoch, IMB_MEDIAN_WINDOW_SEC)
    tx_d, tx_d_n = window_median(tx.times, tx.depth, t_epoch, IMB_MEDIAN_WINDOW_SEC)
    nk_q, nk_qb, nk_qa = quote_pressure(nk, t_epoch)
    tx_q, tx_qb, tx_qa = quote_pressure(tx, t_epoch)
    if nk_l1 is None or tx_l1 is None:
        agr = None
    elif nk_l1 > 0 and tx_l1 > 0:
        agr = 1.0
    elif nk_l1 < 0 and tx_l1 < 0:
        agr = -1.0
    else:
        agr = 0.0
    return {
        "NK_L1_IMB": nk_l1,
        "TOPIX_L1_IMB": tx_l1,
        "NK_DEPTH10_IMB": nk_d,
        "TOPIX_DEPTH10_IMB": tx_d,
        "NK_QUOTE_PRESSURE_60S": nk_q,
        "TOPIX_QUOTE_PRESSURE_60S": tx_q,
        "CROSS_L1_AGREEMENT": agr,
        "_n": {
            "NK_L1_IMB": nk_l1_n,
            "TOPIX_L1_IMB": tx_l1_n,
            "NK_DEPTH10_IMB": nk_d_n,
            "TOPIX_DEPTH10_IMB": tx_d_n,
            "NK_QUOTE_bid_n": nk_qb,
            "NK_QUOTE_ask_n": nk_qa,
            "TOPIX_QUOTE_bid_n": tx_qb,
            "TOPIX_QUOTE_ask_n": tx_qa,
        },
    }


def build_minute_table(*, native_root: Path, day: str = TRADING_DATE) -> dict[str, Any]:
    layout = day_layout(day, native_root=native_root)
    symbols = expected_symbols(native_root, day)
    nk = load_fut_tape(layout["nk225mini_jsonl"])
    tx = load_fut_tape(layout["topix_jsonl"])
    stocks = load_stock_series(layout["stock"], symbols)
    leak = 0
    clocks: list[dict[str, Any]] = []
    for t in minute_grid(day):
        te = _epoch(t)
        hm = (t.hour, t.minute)
        feats = features_at(nk, tx, te)
        nmeta = feats.pop("_n")
        # causal leakage: last used futures event must be <= T
        i_nk = bisect_right(nk.times, te) - 1
        i_tx = bisect_right(tx.times, te) - 1
        if i_nk >= 0 and nk.times[i_nk] > te:
            leak += 1
        if i_tx >= 0 and tx.times[i_tx] > te:
            leak += 1
        row: dict[str, Any] = {
            "clock": f"{hm[0]:02d}:{hm[1]:02d}",
            "block": block_of(hm),
            "t_epoch": te,
            **{k: feats[k] for k in SIGNED_FEATURES},
            "feature_n": nmeta,
            "NK_PAST_RET_180S": None,
            "STOCK_PAST_RET_180S": ew_stock_past(stocks, symbols, te, 180),
        }
        a = nk.px_at(te)
        b = nk.px_at(te - 180.0)
        if a is not None and b is not None and b != 0:
            row["NK_PAST_RET_180S"] = (a - b) / b
        for h in STAGE1_HORIZONS_SEC:
            row[f"NK_RET_{h}S"] = px_ret(nk, te, h)
            row[f"TOPIX_RET_{h}S"] = px_ret(tx, te, h)
        mids = {lab: [] for lab in STAGE2_LABEL.values()}
        longs = {lab: [] for lab in STAGE2_LABEL.values()}
        shorts = {lab: [] for lab in STAGE2_LABEL.values()}
        for sym in symbols:
            series = stocks.get(sym)
            if series is None:
                continue
            for h, lab in STAGE2_LABEL.items():
                o = stock_outcomes_at(series, te, h)
                if o["MID_RETURN_BPS"] is not None:
                    mids[lab].append(o["MID_RETURN_BPS"])
                if o["LONG_EXEC_MARKOUT_BPS"] is not None:
                    longs[lab].append(o["LONG_EXEC_MARKOUT_BPS"])
                if o["SHORT_EXEC_MARKOUT_BPS"] is not None:
                    shorts[lab].append(o["SHORT_EXEC_MARKOUT_BPS"])
        for lab in STAGE2_LABEL.values():
            row[f"STK_MID_{lab}_mean"] = mean(mids[lab])
            row[f"STK_MID_{lab}_median"] = median(mids[lab])
            row[f"STK_LONG_{lab}_mean"] = mean(longs[lab])
            row[f"STK_LONG_{lab}_median"] = median(longs[lab])
            row[f"STK_SHORT_{lab}_mean"] = mean(shorts[lab])
            row[f"STK_SHORT_{lab}_median"] = median(shorts[lab])
            row[f"STK_{lab}_n"] = len(mids[lab])
        plc = features_at(nk, tx, te + float(PLACEBO_SHIFT_SEC))
        plc.pop("_n", None)
        row["placebo"] = {k: plc.get(k) for k in SIGNED_FEATURES}
        clocks.append(row)
    return {
        "symbols": symbols,
        "symbol_n": len(symbols),
        "clocks": clocks,
        "minute_clock_n": len(clocks),
        "future_leakage_n": leak,
        "nk_event_n": len(nk.times),
        "tx_event_n": len(tx.times),
    }
