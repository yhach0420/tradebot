"""Causal stock-state selectors at Day1 clocks. received_at <= T only. No ranking tape."""
from __future__ import annotations

import math
import statistics
from bisect import bisect_right
from pathlib import Path
from typing import Any, Optional

from research.futures_context_day1_effect_check_v1 import (
    ANCHOR_HM,
    EARLY_HM,
    HORIZON_LABEL,
    HORIZONS_SEC,
    LATE_HM,
)
from research.futures_context_day1_effect_check_v1.engine import (
    AsOf,
    _epoch,
    clock_dt,
    expected_symbols,
    load_futures_series,
    mean,
    median,
    ret_asof,
    stock_outcomes_at,
    stock_quotes,
)
from research.futures_x_stock_state_interaction_day1_v1 import (
    BID_UPD_WINDOW_SEC,
    LOOKBACK_RET_SEC,
    OTU_WINDOW_SEC,
    SELECTORS,
    TERCILE_N,
    TRADING_DATE,
    UNIVERSE_N,
)
from research.new_causal_information_acquisition_v1.reconcile import iter_jsonl, parse_received_at
from research.new_causal_information_acquisition_v1.writer import day_layout


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _hm_key(hm: tuple[int, int]) -> str:
    return f"{hm[0]:02d}:{hm[1]:02d}"


def block_of(hm: tuple[int, int]) -> str:
    if hm in EARLY_HM:
        return "EARLY"
    if hm in LATE_HM:
        return "LATE"
    return "OTHER"


def signed_bucket(v: Optional[float]) -> Optional[str]:
    if v is None:
        return None
    if v > 0:
        return "UP"
    if v < 0:
        return "DOWN"
    return None


def agreement_180(nk: Optional[float], tx: Optional[float]) -> Optional[str]:
    if nk is None or tx is None:
        return None
    if nk > 0 and tx > 0:
        return "BOTH_UP"
    if nk < 0 and tx < 0:
        return "BOTH_DOWN"
    if (nk > 0 and tx < 0) or (nk < 0 and tx > 0):
        return "MIXED"
    return None


def alignment_180(nk: Optional[float], cash: Optional[float]) -> Optional[str]:
    if nk is None or cash is None:
        return None
    if (nk > 0 and cash > 0) or (nk < 0 and cash < 0):
        return "ALIGNED"
    if (nk > 0 and cash < 0) or (nk < 0 and cash > 0):
        return "DISAGREED"
    return None


def _count_window(ts: list[float], t: float, sec: float) -> int:
    lo = bisect_right(ts, float(t) - float(sec))
    hi = bisect_right(ts, float(t))
    return max(0, hi - lo)


def _last_le(ts: list[float], t: float) -> Optional[int]:
    i = bisect_right(ts, float(t)) - 1
    return i if i >= 0 else None


def _bid_clock_epoch(pay: dict[str, Any]) -> Optional[float]:
    buy = pay.get("Buy1") if isinstance(pay.get("Buy1"), dict) else {}
    dt = parse_received_at(buy.get("Time")) if buy else None
    if dt is None:
        dt = parse_received_at(pay.get("BidTime"))
    if dt is None:
        return None
    return float(dt.timestamp())


def tercile_split(rows: list[dict[str, Any]], *, key: str = "value", n: int = TERCILE_N) -> Optional[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    ranked = [
        r
        for r in rows
        if r.get(key) is not None and math.isfinite(float(r[key]))
    ]
    ranked.sort(key=lambda r: (-float(r[key]), str(r.get("symbol") or "")))
    if len(ranked) < 2 * int(n):
        return None
    return ranked[: int(n)], ranked[-int(n) :]


class StockTape:
    """Causal per-symbol tape. Selectors and quotes use ingress <= T only."""

    def __init__(self) -> None:
        self.q_t: list[float] = []
        self.q: list[tuple[float, float, float]] = []
        self.st_t: list[float] = []
        self.under: list[Optional[float]] = []
        self.over: list[Optional[float]] = []
        self.otu_t: list[float] = []
        self.otu_val: list[float] = []
        self.otu_px: list[float] = []
        self.bid_upd_t: list[float] = []
        self._last_vol: Optional[float] = None
        self._last_bid_clock: Optional[float] = None
        self._cu: Optional[float] = None
        self._co: Optional[float] = None

    def quotes_asof(self) -> AsOf:
        return AsOf(self.q_t, self.q)

    def past_mid_ret(self, t: float, lookback: int = LOOKBACK_RET_SEC) -> Optional[float]:
        i_now = _last_le(self.q_t, t)
        i_lag = _last_le(self.q_t, float(t) - float(lookback))
        if i_now is None or i_lag is None:
            return None
        mid0 = self.q[i_lag][2]
        mid1 = self.q[i_now][2]
        if mid0 is None or mid1 is None or mid0 == 0:
            return None
        return (float(mid1) - float(mid0)) / float(mid0)

    def selectors_at(self, t: float) -> dict[str, Optional[float]]:
        i_q = _last_le(self.q_t, t)
        spread = None
        if i_q is not None:
            bid, ask, mid = self.q[i_q]
            if bid is not None and ask is not None and mid is not None and mid > 0:
                spread = 10000.0 * (float(ask) - float(bid)) / float(mid)
        n_otu = _count_window(self.otu_t, t, OTU_WINDOW_SEC)
        last_i = _last_le(self.otu_t, t)
        base_i = _last_le(self.otu_t, float(t) - float(OTU_WINDOW_SEC))
        val_delta = None
        if last_i is not None and base_i is not None:
            last_val = self.otu_val[last_i]
            base_val = self.otu_val[base_i]
            if (
                last_val is not None
                and base_val is not None
                and math.isfinite(last_val)
                and math.isfinite(base_val)
                and last_val >= base_val
                and base_val > 0
            ):
                val_delta = float(last_val) - float(base_val)
        bid_n = _count_window(self.bid_upd_t, t, BID_UPD_WINDOW_SEC)
        i_st = _last_le(self.st_t, t)
        under = self.under[i_st] if i_st is not None else None
        over = self.over[i_st] if i_st is not None else None
        return {
            "SPREAD_BPS": spread,
            "OBSERVED_TRADE_N_180S": float(n_otu),
            "TRADING_VALUE_DELTA_180S": val_delta,
            "BID_UPDATE_N_60S": float(bid_n),
            "UNDER_BUY_QTY": under,
            "OVER_SELL_QTY": over,
        }

    def push(self, *, t: float, rec: dict[str, Any]) -> None:
        payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else {}
        vol = _f(rec.get("trading_volume") if rec.get("trading_volume") is not None else payload.get("TradingVolume"))
        px = _f(rec.get("current_price") if rec.get("current_price") is not None else payload.get("CurrentPrice"))
        val = _f(rec.get("trading_value") if rec.get("trading_value") is not None else payload.get("TradingValue"))
        trade = vol is not None and px is not None and px > 0 and (
            self._last_vol is None or float(vol) > float(self._last_vol)
        )
        if trade:
            self.otu_t.append(float(t))
            self.otu_px.append(float(px))
            self.otu_val.append(float(val) if val is not None else float("nan"))
        if vol is not None and not (self._last_vol is not None and float(vol) < float(self._last_vol)):
            self._last_vol = float(vol)
        bid_c = _bid_clock_epoch(payload)
        if bid_c is not None and (self._last_bid_clock is None or float(bid_c) != float(self._last_bid_clock)):
            if self._last_bid_clock is not None:
                self.bid_upd_t.append(float(t))
            self._last_bid_clock = float(bid_c)
        under = _f(payload.get("UnderBuyQty"))
        over = _f(payload.get("OverSellQty"))
        if under is not None:
            self._cu = under
        if over is not None:
            self._co = over
        self.st_t.append(float(t))
        self.under.append(self._cu)
        self.over.append(self._co)
        bid, ask, mid = stock_quotes(rec)
        if bid is not None and ask is not None and mid is not None:
            self.q_t.append(float(t))
            self.q.append((float(bid), float(ask), float(mid)))


def load_stock_tapes(stock_dir: Path, expected: list[str]) -> dict[str, StockTape]:
    tapes = {s: StockTape() for s in expected}
    files = sorted(stock_dir.glob("push_part_*.jsonl")) if stock_dir.is_dir() else []
    for fp in files:
        for _i, rec in iter_jsonl(fp):
            if rec is None:
                continue
            payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else {}
            sym = str(rec.get("symbol") or payload.get("Symbol") or "").split("@", 1)[0]
            tape = tapes.get(sym)
            if tape is None:
                continue
            dt = parse_received_at(rec.get("received_at_jst") or rec.get("received_at"))
            if dt is None:
                continue
            tape.push(t=_epoch(dt), rec=rec)
    return tapes


def _basket_stats(members: list[dict[str, Any]], horizon: str, metric: str) -> dict[str, Any]:
    xs = []
    for m in members:
        pack = m.get(horizon) or {}
        v = pack.get(metric)
        if v is not None and math.isfinite(float(v)):
            xs.append(float(v))
    return {"n": len(xs), "mean": mean(xs), "median": median(xs), "values": xs}


def _spread_pack(top: list[dict[str, Any]], bottom: list[dict[str, Any]], horizon: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for metric in ("MID_RETURN_BPS", "LONG_EXEC_MARKOUT_BPS", "SHORT_EXEC_MARKOUT_BPS"):
        t = _basket_stats(top, horizon, metric)
        b = _basket_stats(bottom, horizon, metric)
        spread = None
        if t["mean"] is not None and b["mean"] is not None:
            spread = float(t["mean"]) - float(b["mean"])
        out[metric] = {"top": t, "bottom": b, "spread": spread}
    return out


def context_bucket_of(clock: dict[str, Any], family: str) -> Optional[str]:
    if family == "NK_RET_180S":
        return signed_bucket(clock.get("nk_ret_180"))
    if family == "AGREEMENT_180S":
        return clock.get("agreement_180")
    if family == "NK_VS_CASH_ALIGN_180S":
        return clock.get("alignment_180")
    return None


def build_interaction_table(*, native_root: Path, day: str = TRADING_DATE) -> dict[str, Any]:
    layout = day_layout(day, native_root=native_root)
    symbols = expected_symbols(native_root, day)
    if len(symbols) != UNIVERSE_N:
        raise RuntimeError(f"expected {UNIVERSE_N} stocks, got {len(symbols)}")
    nk = load_futures_series(layout["nk225mini_jsonl"])
    tx = load_futures_series(layout["topix_jsonl"])
    tapes = load_stock_tapes(layout["stock"], symbols)
    quotes = {s: tapes[s].quotes_asof() for s in symbols}
    leak = 0
    clocks: list[dict[str, Any]] = []

    for hm in ANCHOR_HM:
        t = clock_dt(day, hm)
        te = _epoch(t)
        nk_r, lk = ret_asof(nk, te, LOOKBACK_RET_SEC)
        tx_r, lk2 = ret_asof(tx, te, LOOKBACK_RET_SEC)
        leak += int(lk) + int(lk2)
        past: list[float] = []
        members: list[dict[str, Any]] = []
        for sym in symbols:
            tape = tapes[sym]
            sel = tape.selectors_at(te)
            pret = tape.past_mid_ret(te, LOOKBACK_RET_SEC)
            if pret is not None:
                past.append(float(pret))
            rec: dict[str, Any] = {
                "symbol": sym,
                "selectors": sel,
                "past_ret_180": pret,
            }
            series = quotes[sym]
            for h in HORIZONS_SEC:
                rec[HORIZON_LABEL[h]] = stock_outcomes_at(series, te, h)
            members.append(rec)
        cash_ew = mean(past) if past else None
        clock: dict[str, Any] = {
            "clock": _hm_key(hm),
            "hm": hm,
            "block": block_of(hm),
            "t_epoch": te,
            "nk_ret_180": nk_r,
            "tx_ret_180": tx_r,
            "cash_ew_past_ret_180": cash_ew,
            "nk_sign": signed_bucket(nk_r),
            "agreement_180": agreement_180(nk_r, tx_r),
            "alignment_180": alignment_180(nk_r, cash_ew),
            "stock_n": len(members),
            "members": members,
            "market": {},
            "selectors": {},
        }
        for h in HORIZONS_SEC:
            hl = HORIZON_LABEL[h]
            pack = {}
            for metric in ("MID_RETURN_BPS", "LONG_EXEC_MARKOUT_BPS", "SHORT_EXEC_MARKOUT_BPS"):
                pack[metric] = _basket_stats(members, hl, metric)
            clock["market"][hl] = pack
        for sel_name in SELECTORS:
            ranked_src = [{"symbol": m["symbol"], "value": (m["selectors"] or {}).get(sel_name), **m} for m in members]
            split = tercile_split(ranked_src)
            sel_row: dict[str, Any] = {
                "ranked_n": sum(1 for r in ranked_src if r.get("value") is not None and math.isfinite(float(r["value"]))),
                "top": [],
                "bottom": [],
                "horizons": {},
            }
            if split is not None:
                top, bottom = split
                sel_row["top"] = [{"symbol": x["symbol"], "value": x["value"]} for x in top]
                sel_row["bottom"] = [{"symbol": x["symbol"], "value": x["value"]} for x in bottom]
                for h in HORIZONS_SEC:
                    hl = HORIZON_LABEL[h]
                    sel_row["horizons"][hl] = _spread_pack(top, bottom, hl)
            clock["selectors"][sel_name] = sel_row
        clocks.append(clock)

    return {
        "symbols": symbols,
        "symbol_n": len(symbols),
        "clocks": clocks,
        "future_leakage_n": leak,
        "anchor_clock_n": len(ANCHOR_HM),
        "stock_anchor_n": len(ANCHOR_HM) * len(symbols),
    }


def mean_of(xs: list[Optional[float]]) -> Optional[float]:
    ys = [float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return statistics.mean(ys) if ys else None


def median_of(xs: list[Optional[float]]) -> Optional[float]:
    ys = [float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return statistics.median(ys) if ys else None
