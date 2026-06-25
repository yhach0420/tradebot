"""
Phase513: Classic Momentum forward shadow session (live paper — logging only).

Evaluates RSI14>50 + Stoch K>D on 1m resampled price ring. Virtual positions only.
No ENTRY adoption. No notifications.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
HARD_STOP_PCT = -1.2
ENTRY_COOLDOWN_SEC = 300
RSI_PERIOD = 14
STOCH_PERIOD = 14
STOCH_SMOOTH = 3

SHADOW_CSV = "small_paper_shadow_classic_momentum.csv"

SHADOW_FIELDS: tuple[str, ...] = (
    "date",
    "symbol",
    "entry_time",
    "exit_time",
    "pnl_yen_100",
    "pnl_pct",
    "hold_minutes",
    "mfe_pct",
    "mae_pct",
    "entry_rsi",
    "entry_stoch_k",
    "entry_stoch_d",
    "exit_reason",
)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _resample_1m_ohlc(
    series: Sequence[tuple[float, float]],
    *,
    until: float,
) -> tuple[list[float], list[float], list[float]]:
    if not series:
        return [], [], []
    origin = series[0][0]
    buckets: dict[int, dict[str, float]] = {}
    for ts, px in series:
        if ts > until:
            break
        if px <= 0:
            continue
        key = int((ts - origin) // 60)
        b = buckets.setdefault(key, {"high": px, "low": px, "close": px})
        b["high"] = max(b["high"], px)
        b["low"] = min(b["low"], px)
        b["close"] = px
    keys = sorted(buckets)
    highs = [buckets[k]["high"] for k in keys]
    lows = [buckets[k]["low"] for k in keys]
    closes = [buckets[k]["close"] for k in keys]
    return highs, lows, closes


def _rsi14(closes: Sequence[float]) -> Optional[float]:
    if len(closes) < RSI_PERIOD + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas[-RSI_PERIOD:]]
    losses = [max(-d, 0.0) for d in deltas[-RSI_PERIOD:]]
    avg_gain = sum(gains) / RSI_PERIOD
    avg_loss = sum(losses) / RSI_PERIOD
    if avg_loss <= 1e-12:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - 100.0 / (1.0 + rs), 4)


def _stochastic(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
) -> tuple[Optional[float], Optional[float]]:
    if len(closes) < STOCH_PERIOD:
        return None, None
    wh = highs[-STOCH_PERIOD:]
    wl = lows[-STOCH_PERIOD:]
    hh, ll = max(wh), min(wl)
    if hh <= ll:
        return None, None
    k = 100.0 * (closes[-1] - ll) / (hh - ll)
    k_hist: list[float] = []
    for i in range(STOCH_PERIOD, len(closes) + 1):
        wh_i = highs[i - STOCH_PERIOD : i]
        wl_i = lows[i - STOCH_PERIOD : i]
        hhv, llv = max(wh_i), min(wl_i)
        if hhv > llv:
            k_hist.append(100.0 * (closes[i - 1] - llv) / (hhv - llv))
    d = sum(k_hist[-STOCH_SMOOTH:]) / min(len(k_hist), STOCH_SMOOTH) if k_hist else k
    return round(k, 4), round(d, 4)


@dataclass
class _VirtualPosition:
    symbol: str
    entry_ts: float
    entry_px: float
    entry_rsi: float
    entry_stoch_k: float
    entry_stoch_d: float
    peak_px: float
    trough_px: float


@dataclass
class ClassicMomentumForwardShadowSession:
    """Virtual momentum shadow — observe only, no production impact."""

    closed_rows: list[dict[str, Any]] = field(default_factory=list)
    _open: dict[str, _VirtualPosition] = field(default_factory=dict)
    _last_entry_ts: dict[str, float] = field(default_factory=dict)
    _last_minute_key: dict[str, int] = field(default_factory=dict)
    _last_px: dict[str, float] = field(default_factory=dict)

    def on_price_tick(
        self,
        *,
        symbol: str,
        price_ring: Sequence[tuple[float, float]],
        ts: float,
        px: float,
        day: str,
    ) -> None:
        sym = symbol.replace(".T", "")
        if px <= 0:
            return
        self._last_px[sym] = px
        pos = self._open.get(sym)
        if pos is not None:
            pos.peak_px = max(pos.peak_px, px)
            pos.trough_px = min(pos.trough_px, px)
            pnl_pct = (px - pos.entry_px) / pos.entry_px * 100.0 if pos.entry_px > 0 else 0.0
            if pnl_pct <= HARD_STOP_PCT:
                self._close(sym, ts=ts, px=px, reason="hard_stop", day=day)
                return

        if not price_ring:
            return
        origin = price_ring[0][0]
        minute_key = int((ts - origin) // 60)
        if self._last_minute_key.get(sym) == minute_key:
            return
        self._last_minute_key[sym] = minute_key

        highs, lows, closes = _resample_1m_ohlc(price_ring, until=ts)
        rsi = _rsi14(closes)
        sk, sd = _stochastic(highs, lows, closes)
        if rsi is None or sk is None or sd is None:
            return
        if sym in self._open:
            return
        last_ent = self._last_entry_ts.get(sym, 0.0)
        if ts - last_ent < ENTRY_COOLDOWN_SEC:
            return
        if rsi <= 50 or sk <= sd:
            return
        self._open[sym] = _VirtualPosition(
            symbol=sym,
            entry_ts=ts,
            entry_px=px,
            entry_rsi=rsi,
            entry_stoch_k=sk,
            entry_stoch_d=sd,
            peak_px=px,
            trough_px=px,
        )
        self._last_entry_ts[sym] = ts

    def finalize_session_end(self, *, ts: float, day: str) -> None:
        self.close_session_end(ts=ts, px_by_symbol=self._last_px, day=day)

    def close_session_end(self, *, ts: float, px_by_symbol: Mapping[str, float], day: str) -> None:
        for sym in list(self._open):
            px = _float(px_by_symbol.get(sym)) or self._open[sym].entry_px
            self._close(sym, ts=ts, px=float(px), reason="session_end", day=day)

    def _close(self, sym: str, *, ts: float, px: float, reason: str, day: str) -> None:
        pos = self._open.pop(sym, None)
        if pos is None:
            return
        pnl_pct = round((px - pos.entry_px) / pos.entry_px * 100.0, 4) if pos.entry_px > 0 else 0.0
        mfe = round((pos.peak_px - pos.entry_px) / pos.entry_px * 100.0, 4) if pos.entry_px > 0 else 0.0
        mae = round((pos.trough_px - pos.entry_px) / pos.entry_px * 100.0, 4) if pos.entry_px > 0 else 0.0
        hold_min = round(max(0.0, (ts - pos.entry_ts) / 60.0), 2)
        ent_iso = datetime.fromtimestamp(pos.entry_ts, tz=JST).isoformat()
        ex_iso = datetime.fromtimestamp(ts, tz=JST).isoformat()
        pnl_yen = round((px - pos.entry_px) * 100.0, 2)
        self.closed_rows.append(
            {
                "date": day[:8],
                "symbol": sym,
                "entry_time": ent_iso,
                "exit_time": ex_iso,
                "pnl_yen_100": pnl_yen,
                "pnl_pct": pnl_pct,
                "hold_minutes": hold_min,
                "mfe_pct": mfe,
                "mae_pct": mae,
                "entry_rsi": pos.entry_rsi,
                "entry_stoch_k": pos.entry_stoch_k,
                "entry_stoch_d": pos.entry_stoch_d,
                "exit_reason": reason,
            }
        )

    def summary_fields(self) -> dict[str, Any]:
        return {
            "classic_momentum_shadow_trade_count": len(self.closed_rows),
            "classic_momentum_shadow_pnl_yen_100": round(
                sum(_float(r.get("pnl_yen_100")) or 0.0 for r in self.closed_rows), 2
            ),
        }

    def write_session_csv(self, output_dir: Path) -> Path:
        path = output_dir / SHADOW_CSV
        write_header = not path.is_file()
        with path.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(SHADOW_FIELDS), extrasaction="ignore")
            if write_header:
                w.writeheader()
            for row in self.closed_rows:
                w.writerow(row)
        self.closed_rows.clear()
        return path
