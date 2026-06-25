"""
Phase507 — Extended classic technical indicators on 1m bar series (research only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from research.phase501_classic_indicator_audit import (
    MACD_FAST,
    MACD_SIGNAL,
    MACD_SLOW,
    _ema_series,
    _macd_at_entry,
    _resample_1m_closes,
    _rsi,
    _sma,
)
from small_paper.allowed_trading_windows import DEFAULT_ALLOWED_WINDOWS, parse_allowed_trading_windows

INDICATOR_LOG_FIELDS = (
    "RSI14",
    "MACD",
    "MACD_signal",
    "MACD_histogram",
    "SMA5",
    "SMA20",
    "SMA25",
    "EMA5",
    "EMA20",
    "VWAP",
    "BB_upper",
    "BB_lower",
    "BB_mid",
    "ADX",
    "PLUS_DI",
    "MINUS_DI",
    "ATR14",
    "STOCH_K",
    "STOCH_D",
    "WILLIAMS_R",
    "CCI20",
    "ROC10",
    "MOMENTUM10",
    "MFI14",
    "OBV",
    "DONCHIAN_HIGH20",
    "DONCHIAN_LOW20",
    "DONCHIAN_MID20",
    "ICHIMOKU_TENKAN",
    "ICHIMOKU_KIJUN",
    "ICHIMOKU_CLOUD_POS",
)


@dataclass
class Bar1m:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float


@dataclass
class BarIndicatorRow:
    bar_index: int
    ts: datetime
    close: float
    values: dict[str, Optional[float]] = field(default_factory=dict)


def _in_trading_window(ts: datetime) -> bool:
    windows = parse_allowed_trading_windows(
        [{"start": s, "end": e} for s, e in DEFAULT_ALLOWED_WINDOWS]
    )
    t = ts.time()
    for w in windows:
        if w.start <= t <= w.end:
            return True
    return False


def ticks_to_1m_bars(series: Sequence[tuple[datetime, float]]) -> list[Bar1m]:
    if not series:
        return []
    origin = series[0][0]
    buckets: dict[int, dict[str, Any]] = {}
    for ts, px in series:
        if px <= 0:
            continue
        key = int((ts - origin).total_seconds() // 60)
        b = buckets.setdefault(
            key,
            {"ts": ts, "open": px, "high": px, "low": px, "close": px, "vol": 0.0, "pv": 0.0},
        )
        b["high"] = max(b["high"], px)
        b["low"] = min(b["low"], px)
        b["close"] = px
        b["ts"] = ts
        b["vol"] += 1.0
        b["pv"] += px
    bars: list[Bar1m] = []
    cum_pv = 0.0
    cum_vol = 0.0
    for key in sorted(buckets):
        b = buckets[key]
        cum_pv += b["pv"]
        cum_vol += b["vol"]
        vwap = cum_pv / cum_vol if cum_vol > 0 else b["close"]
        bars.append(
            Bar1m(
                ts=b["ts"],
                open=float(b["open"]),
                high=float(b["high"]),
                low=float(b["low"]),
                close=float(b["close"]),
                volume=float(b["vol"]),
                vwap=float(vwap),
            )
        )
    return bars


def _true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def _compute_adx(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if len(closes) < period + 2:
        return None, None, None
    tr_list: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr_list.append(_true_range(highs[i], lows[i], closes[i - 1]))
    if len(tr_list) < period:
        return None, None, None
    atr = sum(tr_list[-period:]) / period
    pdi = 100.0 * (sum(plus_dm[-period:]) / period) / max(atr, 1e-9)
    mdi = 100.0 * (sum(minus_dm[-period:]) / period) / max(atr, 1e-9)
    dx = 100.0 * abs(pdi - mdi) / max(pdi + mdi, 1e-9)
    return round(dx, 4), round(pdi, 4), round(mdi, 4)


def _stochastic(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14, smooth: int = 3
) -> tuple[Optional[float], Optional[float]]:
    if len(closes) < period:
        return None, None
    window_h = highs[-period:]
    window_l = lows[-period:]
    hh, ll = max(window_h), min(window_l)
    if hh <= ll:
        return None, None
    k = 100.0 * (closes[-1] - ll) / (hh - ll)
    # %D = SMA of last smooth %K values — approximate with current k for short series
    k_hist = []
    for i in range(period, len(closes) + 1):
        wh = highs[i - period : i]
        wl = lows[i - period : i]
        hhv, llv = max(wh), min(wl)
        if hhv > llv:
            k_hist.append(100.0 * (closes[i - 1] - llv) / (hhv - llv))
    d = sum(k_hist[-smooth:]) / min(len(k_hist), smooth) if k_hist else k
    return round(k, 4), round(d, 4)


def compute_bar_indicators(bars: Sequence[Bar1m], *, warmup: int = 30) -> list[BarIndicatorRow]:
    if not bars:
        return []
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    vols = [b.volume for b in bars]
    out: list[BarIndicatorRow] = []
    for i in range(len(bars)):
        row = BarIndicatorRow(bar_index=i, ts=bars[i].ts, close=bars[i].close)
        if i < warmup:
            out.append(row)
            continue
        sub_c = closes[: i + 1]
        sub_h = highs[: i + 1]
        sub_l = lows[: i + 1]
        v: dict[str, Optional[float]] = {k: None for k in INDICATOR_LOG_FIELDS}

        v["RSI14"] = _rsi(sub_c, 14)
        macd, sig, hist = _macd_at_entry(sub_c)
        v["MACD"] = macd
        v["MACD_signal"] = sig
        v["MACD_histogram"] = hist

        for p, key in ((5, "SMA5"), (20, "SMA20"), (25, "SMA25")):
            sma = _sma(sub_c, p)
            v[key] = round(sma, 6) if sma is not None else None

        ema5 = _ema_series(sub_c, 5)
        ema20 = _ema_series(sub_c, 20)
        v["EMA5"] = round(ema5[-1], 6) if ema5 else None
        v["EMA20"] = round(ema20[-1], 6) if ema20 else None
        v["VWAP"] = round(bars[i].vwap, 6)

        bb_mid = _sma(sub_c, 20)
        if bb_mid is not None and len(sub_c) >= 20:
            std = (sum((x - bb_mid) ** 2 for x in sub_c[-20:]) / 20) ** 0.5
            v["BB_mid"] = round(bb_mid, 6)
            v["BB_upper"] = round(bb_mid + 2 * std, 6)
            v["BB_lower"] = round(bb_mid - 2 * std, 6)

        adx, pdi, mdi = _compute_adx(sub_h, sub_l, sub_c)
        v["ADX"] = adx
        v["PLUS_DI"] = pdi
        v["MINUS_DI"] = mdi

        if len(sub_c) >= 15:
            trs = [_true_range(sub_h[j], sub_l[j], sub_c[j - 1]) for j in range(1, len(sub_c))]
            v["ATR14"] = round(sum(trs[-14:]) / 14, 6)

        sk, sd = _stochastic(sub_h, sub_l, sub_c)
        v["STOCH_K"] = sk
        v["STOCH_D"] = sd

        if len(sub_c) >= 14:
            hh14, ll14 = max(sub_h[-14:]), min(sub_l[-14:])
            if hh14 > ll14:
                v["WILLIAMS_R"] = round(-100.0 * (hh14 - sub_c[-1]) / (hh14 - ll14), 4)

        if len(sub_c) >= 20:
            tp = [(sub_h[j] + sub_l[j] + sub_c[j]) / 3 for j in range(len(sub_c))]
            sma_tp = sum(tp[-20:]) / 20
            md = sum(abs(tp[j] - sma_tp) for j in range(len(sub_c) - 19, len(sub_c))) / 20
            v["CCI20"] = round((tp[-1] - sma_tp) / (0.015 * md), 4) if md > 1e-9 else 0.0

        if len(sub_c) >= 11:
            v["ROC10"] = round((sub_c[-1] - sub_c[-11]) / sub_c[-11] * 100.0, 4) if sub_c[-11] > 0 else None
            v["MOMENTUM10"] = round(sub_c[-1] - sub_c[-11], 4)

        # MFI / OBV with tick-count volume proxy
        if len(sub_c) >= 15:
            pos_flow = neg_flow = 0.0
            for j in range(len(sub_c) - 14, len(sub_c)):
                tp_j = (sub_h[j] + sub_l[j] + sub_c[j]) / 3
                tp_prev = (sub_h[j - 1] + sub_l[j - 1] + sub_c[j - 1]) / 3
                raw = tp_j * vols[j]
                if tp_j >= tp_prev:
                    pos_flow += raw
                else:
                    neg_flow += raw
            if neg_flow <= 1e-9:
                v["MFI14"] = 100.0
            else:
                mfr = pos_flow / neg_flow
                v["MFI14"] = round(100.0 - 100.0 / (1.0 + mfr), 4)

        obv = 0.0
        for j in range(1, len(sub_c)):
            if sub_c[j] > sub_c[j - 1]:
                obv += vols[j]
            elif sub_c[j] < sub_c[j - 1]:
                obv -= vols[j]
        v["OBV"] = round(obv, 4)

        if len(sub_c) >= 20:
            dh = max(sub_h[-20:])
            dl = min(sub_l[-20:])
            v["DONCHIAN_HIGH20"] = round(dh, 6)
            v["DONCHIAN_LOW20"] = round(dl, 6)
            v["DONCHIAN_MID20"] = round((dh + dl) / 2, 6)

        if len(sub_c) >= 26:
            tenkan = (max(sub_h[-9:]) + min(sub_l[-9:])) / 2
            kijun = (max(sub_h[-26:]) + min(sub_l[-26:])) / 2
            v["ICHIMOKU_TENKAN"] = round(tenkan, 6)
            v["ICHIMOKU_KIJUN"] = round(kijun, 6)
            span_a = (tenkan + kijun) / 2
            span_b = (max(sub_h[-52:]) + min(sub_l[-52:])) / 2 if len(sub_c) >= 52 else span_a
            cloud_top = max(span_a, span_b)
            cloud_bot = min(span_a, span_b)
            if bars[i].close > cloud_top:
                v["ICHIMOKU_CLOUD_POS"] = 1.0
            elif bars[i].close < cloud_bot:
                v["ICHIMOKU_CLOUD_POS"] = -1.0
            else:
                v["ICHIMOKU_CLOUD_POS"] = 0.0

        row.values = v
        out.append(row)
    return out


def indicator_dict_at_entry(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    entry_px: float,
) -> dict[str, Optional[float]]:
    """Point-in-time classic indicator snapshot for logging (1m resample)."""
    bars = ticks_to_1m_bars(series)
    if not bars:
        return {k: None for k in INDICATOR_LOG_FIELDS}
    ind_rows = compute_bar_indicators(bars)
    for row in reversed(ind_rows):
        if row.ts <= entry_ts and row.values.get("RSI14") is not None:
            out = dict(row.values)
            out["VWAP"] = out.get("VWAP") or entry_px
            return out
    return {k: None for k in INDICATOR_LOG_FIELDS}
