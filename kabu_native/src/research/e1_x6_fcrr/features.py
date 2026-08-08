"""Causal feature buffers for FCRR (asof_time <= decision_time always)."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Optional

from .config import THRESHOLDS


@dataclass
class Tick:
    t: float
    mid: float
    bid: float
    ask: float
    vwap: float
    cum_vol: float
    spread_bps: float


@dataclass
class FeatureBuffer:
    """Per-symbol causal history. Missing values never coerced to 0 for PASS."""
    ticks: Deque[Tick] = field(default_factory=lambda: deque(maxlen=20000))
    _times: Deque[float] = field(default_factory=lambda: deque(maxlen=20000))
    session_high: float = float("nan")
    last_cum_vol: float = float("nan")
    vol_integrity_ok: bool = True
    vol_reset_count: int = 0

    def push(self, t: float, bid: float, ask: float, vwap: float, cum_vol: float) -> Optional[str]:
        if not (math.isfinite(bid) and math.isfinite(ask) and bid > 0 and ask > 0 and ask >= bid):
            return "BAD_QUOTE"
        if not (math.isfinite(vwap) and vwap > 0):
            return "VWAP_MISSING"
        if not math.isfinite(cum_vol):
            return "VOLUME_MISSING"
        # Cumulative volume can reset at session boundaries; do not permanent-fail.
        if math.isfinite(self.last_cum_vol) and cum_vol + 1e-12 < self.last_cum_vol:
            self.vol_reset_count += 1
        mid = 0.5 * (bid + ask)
        spread = (ask - bid) / mid * 10000.0
        self.ticks.append(Tick(t, mid, bid, ask, vwap, cum_vol, spread))
        self._times.append(t)
        self.last_cum_vol = cum_vol
        if not math.isfinite(self.session_high) or mid > self.session_high:
            self.session_high = mid
        return None

    def age(self, now: float) -> float:
        if not self.ticks:
            return float("inf")
        return now - self.ticks[-1].t

    def history_span(self) -> float:
        if len(self.ticks) < 2:
            return 0.0
        return self.ticks[-1].t - self.ticks[0].t

    def _left_idx(self, lo: float) -> int:
        """First index with t >= lo (bisect on parallel times deque)."""
        # bisect_left on deque via list view is costly; use manual from right for hot path
        times = self._times
        n = len(times)
        if n == 0:
            return 0
        # binary search
        lo_i, hi_i = 0, n
        while lo_i < hi_i:
            mid_i = (lo_i + hi_i) // 2
            if times[mid_i] < lo - 1e-12:
                lo_i = mid_i + 1
            else:
                hi_i = mid_i
        return lo_i

    def snapshot(self, now: float) -> dict[str, Any]:
        """Return features at `now`. Incomplete fields are None (never 0-filled PASS)."""
        q = THRESHOLDS["quality"]
        out: dict[str, Any] = {"asof_time": now, "complete": False, "reason": ""}
        n = len(self.ticks)
        if n == 0:
            out["reason"] = "NO_TICKS"
            return out
        last = self.ticks[-1]
        if last.t > now + 1e-9:
            out["reason"] = "FUTURE_TICK"
            return out
        if self.age(now) > q["freshness_max_sec"] + 1e-9:
            out["reason"] = "STALE"
            return out
        span = self.history_span()
        if span + 1e-9 < q["price_history_sec_min"]:
            out["reason"] = "PRICE_HISTORY_SHORT"
            return out
        if span + 1e-9 < q["volume_history_sec_min"]:
            out["reason"] = "VOLUME_HISTORY_SHORT"
            return out

        mid = last.mid
        vwap = last.vwap
        bid, ask = last.bid, last.ask
        spread = last.spread_bps

        # One 300s window (largest lookback used) — all metrics from this slice.
        i0 = self._left_idx(now - 300.0)
        # Materialize only the needed suffix once
        w = [self.ticks[i] for i in range(i0, n)]
        if len(w) < 2:
            out["reason"] = "WINDOW_SHORT"
            return out

        def sub(sec: float) -> list[Tick]:
            lo = now - sec
            # w is sorted by t; find first >= lo
            j = 0
            m = len(w)
            while j < m and w[j].t < lo - 1e-12:
                j += 1
            return w[j:]

        def ret(sec: float) -> Optional[float]:
            ww = sub(sec)
            if len(ww) < 2 or ww[0].mid <= 0:
                return None
            return (ww[-1].mid / ww[0].mid - 1.0) * 10000.0

        def slope(sec: float) -> Optional[float]:
            ww = sub(sec)
            if len(ww) < 4:
                return None
            t0 = ww[0].t
            xs = [x.t - t0 for x in ww]
            ys = [x.mid for x in ww]
            nn = len(xs)
            mx = sum(xs) / nn
            my = sum(ys) / nn
            num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            den = sum((x - mx) ** 2 for x in xs)
            if den <= 0:
                return None
            return num / den

        def atr(sec: float) -> Optional[float]:
            ww = sub(sec)
            if len(ww) < 4:
                return None
            highs = [x.mid for x in ww]
            return max(highs) - min(highs)

        def vol_delta(sec: float) -> Optional[float]:
            ww = sub(sec)
            if len(ww) < 2:
                return None
            return max(0.0, ww[-1].cum_vol - ww[0].cum_vol)

        def active_windows(win: float, look: float) -> tuple[Optional[int], Optional[float]]:
            n_bins = int(look / win)
            if n_bins <= 0:
                return None, None
            ww = sub(look)
            if len(ww) < 2:
                return 0, None
            first_cv = [None] * n_bins
            last_cv = [None] * n_bins
            for x in ww:
                i = int((now - x.t) // win)
                if i < 0 or i >= n_bins:
                    continue
                if first_cv[i] is None:
                    first_cv[i] = x.cum_vol
                last_cv[i] = x.cum_vol
            active = []
            for i in range(n_bins):
                if first_cv[i] is None or last_cv[i] is None:
                    continue
                d = last_cv[i] - first_cv[i]
                if d > 0:
                    active.append(d)
            med = sorted(active)[len(active) // 2] if active else None
            return len(active), med

        def price_updates(sec: float) -> Optional[int]:
            ww = sub(sec)
            if len(ww) < 2:
                return None
            c = 0
            prev = ww[0].mid
            for x in ww[1:]:
                if abs(x.mid - prev) > 1e-12:
                    c += 1
                    prev = x.mid
            return c

        def tick_vol_ratio(sec: float, up: bool) -> Optional[float]:
            ww = sub(sec)
            if len(ww) < 3:
                return None
            up_v = dn_v = 0.0
            for i in range(1, len(ww)):
                dv = max(0.0, ww[i].cum_vol - ww[i - 1].cum_vol)
                if ww[i].mid > ww[i - 1].mid + 1e-12:
                    up_v += dv
                elif ww[i].mid < ww[i - 1].mid - 1e-12:
                    dn_v += dv
            tot = up_v + dn_v
            if tot <= 0:
                return None
            return (up_v / tot) if up else (dn_v / tot)

        ret180 = ret(180.0)
        slope180 = slope(180.0)
        atr180 = atr(180.0)
        vol10 = vol_delta(10.0)
        vol30 = vol_delta(30.0)
        a10_n, med10 = active_windows(10.0, 120.0)
        a30_n, med30 = active_windows(30.0, 300.0)
        act120, _ = active_windows(20.0, 120.0)
        pu10 = price_updates(10.0)
        pu60 = price_updates(60.0)

        w120 = sub(120.0)
        bin_counts = [0] * 12
        if len(w120) >= 2:
            prev_m = w120[0].mid
            for x in w120[1:]:
                i = int((now - x.t) // 10.0)
                if 0 <= i < 12 and abs(x.mid - prev_m) > 1e-12:
                    bin_counts[i] += 1
                prev_m = x.mid
        pu_meds = [c for c in bin_counts if c > 0]
        pu_med = sorted(pu_meds)[len(pu_meds) // 2] if pu_meds else None

        dist_high = None
        dist_vwap = None
        if atr180 is not None and atr180 > 0 and math.isfinite(self.session_high):
            dist_high = (self.session_high - mid) / atr180
            dist_vwap = (mid - vwap) / atr180

        out.update({
            "complete": True,
            "reason": "",
            "mid": mid,
            "bid": bid,
            "ask": ask,
            "vwap": vwap,
            "spread_bps": spread,
            "ret_15s": ret(15.0),
            "ret_30s": ret(30.0),
            "ret_180s": ret180,
            "linear_slope_180s": slope180,
            "atr_180s": atr180,
            "volume_10s": vol10,
            "volume_30s": vol30,
            "median_active_volume_10s_120s": med10,
            "median_active_volume_30s_300s": med30,
            "active_10s_windows_120s": a10_n,
            "active_30s_windows_300s": a30_n,
            "active_volume_windows_120s": act120,
            "uptick_volume_ratio_10s": tick_vol_ratio(10.0, True),
            "uptick_volume_ratio_30s": tick_vol_ratio(30.0, True),
            "down_tick_volume_ratio_15s": tick_vol_ratio(15.0, False),
            "down_tick_volume_ratio_60s": tick_vol_ratio(60.0, False),
            "price_update_count_10s": pu10,
            "price_update_count_60s": pu60,
            "median_price_update_count_10s_120s": pu_med,
            "session_high": self.session_high,
            "distance_from_session_high": dist_high,
            "distance_above_vwap": dist_vwap,
            "trade_side_quality": "TICK_RULE_INFERRED",
        })
        return out
