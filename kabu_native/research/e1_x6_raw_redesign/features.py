"""Phase A-2: fixed 5-second grid, as-of features, leave-one-out market features.

All-opportunity specification (independent of legacy X5 evaluation points):
- every symbol, fixed 5s grid per session (AM 09:00-11:30, PM 12:30-15:30 JST);
- at grid time t only information with timestamp <= t is used (no future-side
  interpolation, missing history => NaN, never filled);
- at most ONE evaluation per symbol per grid point;
- explicit freshness gate; non-evaluable points recorded with NOT_EVALUABLE reason;
- first 5 minutes of each session = feature warmup (no evaluation output);
- last 10 minutes of each session = no new ENTRY (entry_allowed=False);
- AM and PM are separate rolling windows (no cross-session history).

Market features are leave-one-out: the aggregate for symbol i excludes i.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

import numpy as np

from .event_input import EvalEvent

JST = ZoneInfo("Asia/Tokyo")

GRID_STEP_SEC = 5.0
WARMUP_SEC = 300.0
NO_ENTRY_TAIL_SEC = 600.0
FRESH_MAX_AGE_SEC = 30.0
SPREAD_MAX_BPS = 50.0

SESSION_TIMES = {"AM": ((9, 0), (11, 30)), "PM": ((12, 30), (15, 30))}

NOT_EVALUABLE_REASONS = (
    "NO_EVENT_YET", "STALE_QUOTE", "MISSING_QUOTE", "CROSSED_OR_ZERO_QUOTE",
    "UNHEALTHY_SPREAD", "WARMUP",
)

# Explicit formulas frozen into P1 (documentation of record for every feature).
FEATURE_FORMULAS: dict[str, str] = {
    "bid": "Buy1.Price as-of t (standard bid; kabu label AskPrice)",
    "ask": "Sell1.Price as-of t (standard ask; kabu label BidPrice)",
    "mid": "(bid+ask)/2",
    "spread_bps": "(ask-bid)/mid*1e4",
    "ret_15s_bps": "(mid[g]/mid[g-3]-1)*1e4 on 5s grid (NaN if older point not evaluable)",
    "ret_30s_bps": "(mid[g]/mid[g-6]-1)*1e4",
    "ret_60s_bps": "(mid[g]/mid[g-12]-1)*1e4",
    "ret_180s_bps": "(mid[g]/mid[g-36]-1)*1e4",
    "ret_300s_bps": "(mid[g]/mid[g-60]-1)*1e4",
    "rv_60s_bps": "std(diff(log(mid[g-12..g])))*1e4, requires >=8 finite mids in window",
    "rv_300s_bps": "std(diff(log(mid[g-60..g])))*1e4, requires >=40 finite mids",
    "high_60s": "max(mid[g-12..g])", "low_60s": "min(mid[g-12..g])",
    "high_180s": "max(mid[g-36..g])", "low_180s": "min(mid[g-36..g])",
    "high_300s": "max(mid[g-60..g])", "low_300s": "min(mid[g-60..g])",
    "range_pos_300s": "(mid-low_300s)/(high_300s-low_300s), NaN if range==0",
    "up_persist_60s": "fraction of last 12 grid steps with mid[k]>mid[k-1] (both finite)",
    "dir_eff_300s": "|mid[g]-mid[g-60]| / sum(|mid[k]-mid[k-1]| over window), NaN if denom==0",
    "accel_bps": "ret_60s_bps[g] - ret_60s_bps[g-12] (price acceleration)",
    "range_ratio_60_300": "(high_60s-low_60s)/(high_300s-low_300s), NaN if denom==0",
    "vol_ratio_60_300": "rv_60s_bps/rv_300s_bps, NaN if denom==0",
    "breakout_dev_bps": "(mid-high_300s_prev)/mid*1e4 where high_300s_prev=max(mid[g-61..g-1])",
    "pullback_bps": "(high_300s-mid)/mid*1e4",
    "spread_ok": "quote fresh (<=30s) AND ask>=bid>0 AND spread_bps<=50",
    # conditional on coverage (added only when inventory proves as-of availability)
    "vol_rate_60s": "(volume[g]-volume[g-12])/60 shares/s (cumulative TradingVolume as-of)",
    "vwap_dev_bps": "(mid-vwap_asof)/vwap_asof*1e4",
    "board_imbalance10": "(sum Buy1..10 Qty - sum Sell1..10 Qty)/(sum+sum) as-of",
    # market (leave-one-out; symbol i excluded from every aggregate)
    "mkt_ret_60s_med_bps": "median over others of ret_60s_bps",
    "mkt_ret_300s_med_bps": "median over others of ret_300s_bps",
    "mkt_up_ratio_60s": "fraction of others with ret_60s_bps>0",
    "mkt_ret_60s_iqr_bps": "IQR over others of ret_60s_bps",
    "mkt_rv_300s_med_bps": "median over others of rv_300s_bps",
    "mkt_vol_expansion": "median over others of vol_ratio_60_300",
    "mkt_spread_worse_ratio": "fraction of others with spread_bps > 1.5*their own median spread_bps over last 300s",
    "mkt_evaluable_n": "count of others evaluable at this grid",
}

SYMBOL_FEATURES_CORE = (
    "bid", "ask", "mid", "spread_bps",
    "ret_15s_bps", "ret_30s_bps", "ret_60s_bps", "ret_180s_bps", "ret_300s_bps",
    "rv_60s_bps", "rv_300s_bps",
    "high_60s", "low_60s", "high_180s", "low_180s", "high_300s", "low_300s",
    "range_pos_300s", "up_persist_60s", "dir_eff_300s", "accel_bps",
    "range_ratio_60_300", "vol_ratio_60_300", "breakout_dev_bps", "pullback_bps",
)
SYMBOL_FEATURES_CONDITIONAL = ("vol_rate_60s", "vwap_dev_bps", "board_imbalance10")
MARKET_FEATURES = (
    "mkt_ret_60s_med_bps", "mkt_ret_300s_med_bps", "mkt_up_ratio_60s",
    "mkt_ret_60s_iqr_bps", "mkt_rv_300s_med_bps", "mkt_vol_expansion",
    "mkt_spread_worse_ratio", "mkt_evaluable_n",
)


def session_grid_epochs(day: str, am_pm: str) -> np.ndarray:
    (h0, m0), (h1, m1) = SESSION_TIMES[am_pm]
    d = datetime(int(day[:4]), int(day[4:6]), int(day[6:]), tzinfo=JST)
    t0 = (d + timedelta(hours=h0, minutes=m0)).timestamp()
    t1 = (d + timedelta(hours=h1, minutes=m1)).timestamp()
    n = int((t1 - t0) / GRID_STEP_SEC) + 1
    return t0 + np.arange(n, dtype=np.float64) * GRID_STEP_SEC


@dataclass
class SymbolGrid:
    """As-of state of one symbol sampled on the session grid."""
    symbol: str
    grid: np.ndarray
    bid: np.ndarray
    ask: np.ndarray
    last_event_age: np.ndarray   # seconds since last event, inf if none
    volume: np.ndarray
    vwap: np.ndarray
    board_buy: np.ndarray
    board_sell: np.ndarray
    evaluable: np.ndarray = field(default=None)  # type: ignore[assignment]
    not_evaluable_reason: list = field(default_factory=list)


def build_symbol_grid(symbol: str, events: Sequence[EvalEvent], grid: np.ndarray) -> SymbolGrid:
    """Sample as-of values on the grid. Only events with ts<=t are used."""
    n = grid.shape[0]
    ets = np.asarray([e.ts_epoch for e in events], dtype=np.float64)
    order = np.argsort(ets, kind="stable")   # defensive: as-of requires sorted time
    ets = ets[order]

    def _series(getter) -> np.ndarray:
        vals = np.full(len(events), np.nan, dtype=np.float64)
        for k, i in enumerate(order):
            v = getter(events[int(i)])
            vals[k] = np.nan if v is None else float(v)
        # last non-NaN as-of value at each grid point
        idx = np.searchsorted(ets, grid, side="right") - 1
        out = np.full(n, np.nan, dtype=np.float64)
        # forward-carry of PAST values only (no future interpolation)
        carried = np.full(len(events), np.nan, dtype=np.float64)
        last = np.nan
        for k in range(len(events)):
            if not np.isnan(vals[k]):
                last = vals[k]
            carried[k] = last
        ok = idx >= 0
        out[ok] = carried[idx[ok]]
        return out

    bid = _series(lambda e: e.bid)
    ask = _series(lambda e: e.ask)
    vol = _series(lambda e: e.volume)
    vwp = _series(lambda e: e.vwap)
    bb = _series(lambda e: e.board_buy_qty10)
    bs = _series(lambda e: e.board_sell_qty10)
    idx = np.searchsorted(ets, grid, side="right") - 1
    age = np.full(n, np.inf, dtype=np.float64)
    ok = idx >= 0
    age[ok] = grid[ok] - ets[idx[ok]]

    sg = SymbolGrid(symbol=symbol, grid=grid, bid=bid, ask=ask, last_event_age=age,
                    volume=vol, vwap=vwp, board_buy=bb, board_sell=bs)
    ev = np.ones(n, dtype=bool)
    reasons = [""] * n
    warm_end = grid[0] + WARMUP_SEC
    for g in range(n):
        if not np.isfinite(age[g]):
            ev[g], reasons[g] = False, "NO_EVENT_YET"
        elif grid[g] < warm_end - 1e-9:
            ev[g], reasons[g] = False, "WARMUP"
        elif age[g] > FRESH_MAX_AGE_SEC + 1e-9:
            ev[g], reasons[g] = False, "STALE_QUOTE"
        elif np.isnan(bid[g]) or np.isnan(ask[g]):
            ev[g], reasons[g] = False, "MISSING_QUOTE"
        elif bid[g] <= 0 or ask[g] <= 0 or ask[g] < bid[g]:
            ev[g], reasons[g] = False, "CROSSED_OR_ZERO_QUOTE"
        else:
            sp = (ask[g] - bid[g]) / ((ask[g] + bid[g]) / 2.0) * 10000.0
            if sp > SPREAD_MAX_BPS + 1e-9:
                ev[g], reasons[g] = False, "UNHEALTHY_SPREAD"
    sg.evaluable = ev
    sg.not_evaluable_reason = reasons
    return sg


def _win_slice(g: int, steps: int) -> slice:
    return slice(max(0, g - steps), g + 1)


def compute_symbol_features(sg: SymbolGrid) -> dict[str, np.ndarray]:
    """Features from the as-of quote history.

    History uses every sane as-of quote (warmup/staleness do NOT erase history;
    they gate DECISION OUTPUT via sg.evaluable, applied in the state machines).
    """
    n = sg.grid.shape[0]
    with np.errstate(invalid="ignore"):
        quote_ok = (
            np.isfinite(sg.bid) & np.isfinite(sg.ask)
            & (sg.bid > 0) & (sg.ask > 0) & (sg.ask >= sg.bid)
        )
    mid = np.where(quote_ok, (sg.bid + sg.ask) / 2.0, np.nan)
    f: dict[str, np.ndarray] = {k: np.full(n, np.nan, dtype=np.float64) for k in
                                SYMBOL_FEATURES_CORE + SYMBOL_FEATURES_CONDITIONAL}
    f["bid"] = np.where(quote_ok, sg.bid, np.nan)
    f["ask"] = np.where(quote_ok, sg.ask, np.nan)
    f["mid"] = mid
    with np.errstate(all="ignore"):
        f["spread_bps"] = (f["ask"] - f["bid"]) / f["mid"] * 10000.0

    lag_steps = {"ret_15s_bps": 3, "ret_30s_bps": 6, "ret_60s_bps": 12,
                 "ret_180s_bps": 36, "ret_300s_bps": 60}
    with np.errstate(all="ignore"):
        for name, lag in lag_steps.items():
            out = np.full(n, np.nan)
            if n > lag:
                out[lag:] = (mid[lag:] / mid[:-lag] - 1.0) * 10000.0
            f[name] = out

        logmid = np.log(mid)
        dlog = np.full(n, np.nan)
        dlog[1:] = np.diff(logmid)
        for name, steps, minc in (("rv_60s_bps", 12, 8), ("rv_300s_bps", 60, 40)):
            for g in range(n):
                w = dlog[_win_slice(g, steps)][1:] if g >= 1 else dlog[0:0]
                fin = w[~np.isnan(w)]
                if fin.shape[0] >= minc:
                    f[name][g] = float(np.std(fin)) * 10000.0

        for name, steps, fn in (("high_60s", 12, np.nanmax), ("low_60s", 12, np.nanmin),
                                ("high_180s", 36, np.nanmax), ("low_180s", 36, np.nanmin),
                                ("high_300s", 60, np.nanmax), ("low_300s", 60, np.nanmin)):
            for g in range(n):
                w = mid[_win_slice(g, steps)]
                if np.any(~np.isnan(w)):
                    import warnings

                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", RuntimeWarning)
                        f[name][g] = float(fn(w))

        rng300 = f["high_300s"] - f["low_300s"]
        f["range_pos_300s"] = np.where(rng300 > 0, (mid - f["low_300s"]) / rng300, np.nan)
        f["range_ratio_60_300"] = np.where(
            rng300 > 0, (f["high_60s"] - f["low_60s"]) / rng300, np.nan
        )
        f["vol_ratio_60_300"] = np.where(
            f["rv_300s_bps"] > 0, f["rv_60s_bps"] / f["rv_300s_bps"], np.nan
        )

        dmid = np.full(n, np.nan)
        dmid[1:] = mid[1:] - mid[:-1]
        for g in range(n):
            w = dmid[_win_slice(g, 12)][1:] if g >= 1 else dmid[0:0]
            fin = w[~np.isnan(w)]
            if fin.shape[0] >= 6:
                f["up_persist_60s"][g] = float(np.mean(fin > 0))
            w3 = dmid[_win_slice(g, 60)][1:] if g >= 1 else dmid[0:0]
            fin3 = w3[~np.isnan(w3)]
            denom = float(np.sum(np.abs(fin3)))
            if fin3.shape[0] >= 40 and denom > 0 and g >= 60 and not np.isnan(mid[g - 60]) and not np.isnan(mid[g]):
                f["dir_eff_300s"][g] = abs(float(mid[g] - mid[g - 60])) / denom

        f["accel_bps"] = np.full(n, np.nan)
        if n > 12:
            f["accel_bps"][12:] = f["ret_60s_bps"][12:] - f["ret_60s_bps"][:-12]

        prev_high = np.full(n, np.nan)
        for g in range(1, n):
            w = mid[max(0, g - 61):g]
            fin = w[~np.isnan(w)]
            if fin.shape[0]:
                prev_high[g] = float(np.max(fin))
        f["breakout_dev_bps"] = np.where(mid > 0, (mid - prev_high) / mid * 10000.0, np.nan)
        f["pullback_bps"] = np.where(mid > 0, (f["high_300s"] - mid) / mid * 10000.0, np.nan)

        # conditional on coverage
        vr = np.full(n, np.nan)
        if n > 12:
            vr[12:] = (sg.volume[12:] - sg.volume[:-12]) / 60.0
        f["vol_rate_60s"] = vr
        f["vwap_dev_bps"] = np.where(sg.vwap > 0, (mid - sg.vwap) / sg.vwap * 10000.0, np.nan)
        tot = sg.board_buy + sg.board_sell
        f["board_imbalance10"] = np.where(
            quote_ok & (tot > 0), (sg.board_buy - sg.board_sell) / tot, np.nan
        )
    return f


def compute_market_loo(
    sym_feats: dict[str, dict[str, np.ndarray]],
    evaluable: dict[str, np.ndarray],
    n_grid: int,
) -> dict[str, dict[str, np.ndarray]]:
    """Leave-one-out market aggregates for every symbol on the shared grid."""
    import warnings

    symbols = sorted(sym_feats)
    ns = len(symbols)
    r60 = np.vstack([sym_feats[s]["ret_60s_bps"] for s in symbols])
    r300 = np.vstack([sym_feats[s]["ret_300s_bps"] for s in symbols])
    rv300 = np.vstack([sym_feats[s]["rv_300s_bps"] for s in symbols])
    vratio = np.vstack([sym_feats[s]["vol_ratio_60_300"] for s in symbols])
    spread = np.vstack([sym_feats[s]["spread_bps"] for s in symbols])
    ev = np.vstack([evaluable[s] for s in symbols]).astype(bool)

    # per-symbol rolling 300s median spread (for the "spread worse" flag)
    spread_med300 = np.full_like(spread, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for i in range(ns):
            for g in range(n_grid):
                w = spread[i, max(0, g - 60):g + 1]
                fin = w[~np.isnan(w)]
                if fin.shape[0] >= 12:
                    spread_med300[i, g] = float(np.median(fin))
        spread_worse = (spread > 1.5 * spread_med300) & ~np.isnan(spread) & ~np.isnan(spread_med300)

        out: dict[str, dict[str, np.ndarray]] = {
            s: {k: np.full(n_grid, np.nan) for k in MARKET_FEATURES} for s in symbols
        }
        for g in range(n_grid):
            evg = ev[:, g]
            for i, s in enumerate(symbols):
                mask = evg.copy()
                mask[i] = False  # leave-one-out: exclude the symbol itself
                o = out[s]
                o["mkt_evaluable_n"][g] = float(np.sum(mask))
                if not np.any(mask):
                    continue
                a60 = r60[mask, g]
                fin60 = a60[~np.isnan(a60)]
                if fin60.shape[0]:
                    o["mkt_ret_60s_med_bps"][g] = float(np.median(fin60))
                    o["mkt_up_ratio_60s"][g] = float(np.mean(fin60 > 0))
                    if fin60.shape[0] >= 4:
                        q75, q25 = np.percentile(fin60, [75, 25])
                        o["mkt_ret_60s_iqr_bps"][g] = float(q75 - q25)
                a300 = r300[mask, g]
                fin300 = a300[~np.isnan(a300)]
                if fin300.shape[0]:
                    o["mkt_ret_300s_med_bps"][g] = float(np.median(fin300))
                arv = rv300[mask, g]
                finrv = arv[~np.isnan(arv)]
                if finrv.shape[0]:
                    o["mkt_rv_300s_med_bps"][g] = float(np.median(finrv))
                avr = vratio[mask, g]
                finvr = avr[~np.isnan(avr)]
                if finvr.shape[0]:
                    o["mkt_vol_expansion"][g] = float(np.median(finvr))
                o["mkt_spread_worse_ratio"][g] = float(np.mean(spread_worse[mask, g]))
    return out


def entry_allowed_mask(grid: np.ndarray) -> np.ndarray:
    """No new ENTRY in the last 10 minutes of the session."""
    return grid <= (grid[-1] - NO_ENTRY_TAIL_SEC + 1e-9)


def continuous_lookback_ok(sg: SymbolGrid, steps: int = 60) -> np.ndarray:
    """True where the last `steps`*5s of grids were continuously data-covered.

    A grid is covered when its as-of quote age is within the freshness window;
    any gap (>30s without events) resets the run, so a 300s feature lookback
    can never silently span a data gap (Phase A-R1 §4/§11).
    """
    n = sg.grid.shape[0]
    ok = np.isfinite(sg.last_event_age) & (sg.last_event_age <= FRESH_MAX_AGE_SEC + 1e-9)
    out = np.zeros(n, dtype=bool)
    run = 0
    for g in range(n):
        run = run + 1 if ok[g] else 0
        out[g] = run >= steps + 1
    return out
