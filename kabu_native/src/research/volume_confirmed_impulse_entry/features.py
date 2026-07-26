"""Causal volume / price-context features and VCIE trigger detection (no imputation).

Uses 1-second bars + prefix sums for fast window queries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Optional, Sequence
from uuid import uuid4

import numpy as np

from research.volume_confirmed_impulse_entry.constants import (
    MAX_CONTEXT_AGE_SEC,
    MAX_IMPULSE_TO_ENTRY_SEC,
    UPTICK_RATIO_GRID,
    VOL_IMPULSE_10S_GRID,
    VOL_IMPULSE_30S_GRID,
)
from research.volume_confirmed_impulse_entry.push_loader import PushTick


def aggregate_to_seconds(ticks: Sequence[PushTick]) -> list[PushTick]:
    if not ticks:
        return []
    out: list[PushTick] = []
    bucket: list[PushTick] = []
    cur_sec: Optional[int] = None

    def flush() -> None:
        nonlocal bucket
        if not bucket:
            return
        first, last = bucket[0], bucket[-1]
        vsum = 0.0
        tvsum = 0.0
        missing = False
        reset = False
        up = dn = buy = sell = 0.0
        for t in bucket:
            if t.dq_volume_reset:
                reset = True
            if t.volume_delta is None:
                missing = True
            else:
                vd = float(t.volume_delta)
                vsum += vd
                if t.tick_direction > 0:
                    up += vd
                elif t.tick_direction < 0:
                    dn += vd
                if t.buy_aggression is not None:
                    if t.buy_aggression >= 0.5:
                        buy += vd
                    else:
                        sell += vd
            if t.trading_value_delta is not None:
                tvsum += float(t.trading_value_delta)
        prev = out[-1].current_price if out else first.previous_price
        tick = 0
        if prev is not None:
            if last.current_price > prev:
                tick = 1
            elif last.current_price < prev:
                tick = -1
        q = last.trade_side_quality
        agg = last.buy_aggression
        if buy + sell > 0:
            agg = 1.0 if buy >= sell else 0.0
            q = "QUOTE_INFERRED" if q == "QUOTE_INFERRED" else "TICK_RULE_INFERRED"
        out.append(
            PushTick(
                day=last.day,
                symbol=last.symbol,
                event_time=last.event_time.replace(microsecond=0),
                current_price=last.current_price,
                previous_price=prev,
                cumulative_volume=last.cumulative_volume,
                volume_delta=None if missing or reset else vsum,
                cumulative_trading_value=last.cumulative_trading_value,
                trading_value_delta=tvsum if not reset else None,
                bid=last.bid,
                ask=last.ask,
                bid_qty=last.bid_qty,
                ask_qty=last.ask_qty,
                spread_bps=last.spread_bps,
                tick_direction=tick,
                trade_side_quality=q,
                buy_aggression=agg,
                price_age_sec=last.price_age_sec,
                board_age_sec=last.board_age_sec,
                dq_volume_reset=reset,
                sequence=last.sequence,
            )
        )
        # stash up/dn for ratio via attributes on object — use volume_delta split via tick
        bucket = []

    for t in ticks:
        sec = int(t.event_time.timestamp())
        if cur_sec is None:
            cur_sec = sec
        if sec != cur_sec:
            flush()
            cur_sec = sec
        bucket.append(t)
    flush()
    return out


@dataclass
class FeatureSnap:
    ok: bool
    reason: str = ""
    values: dict[str, Optional[float]] = field(default_factory=dict)


@dataclass
class ThresholdSet:
    vol_impulse_10s: float = 1.5
    vol_impulse_30s: float = 1.3
    uptick_ratio: float = 0.55
    ask_exec_ratio: float = 0.55
    hold_mode: str = "sec"
    hold_n: float = 5.0
    context_age_sec: float = 180.0
    use_ask_exec: bool = False


@dataclass
class Trigger:
    day: str
    symbol: str
    event_time: datetime
    entry_price: float
    method: str
    breakout_level: float
    breakout_kind: str
    features: dict[str, Optional[float]]
    trade_side_quality: str
    impulse_time: Optional[datetime]
    context_time: Optional[datetime]
    impulse_to_entry_sec: Optional[float]
    context_to_entry_sec: Optional[float]
    hold_ticks: int
    hold_sec: float
    episode_id: str
    thresholds: dict[str, Any]
    row_index: int


class _Prefix:
    """Prefix sums on 1s bars for O(1) window volume queries."""

    def __init__(self, bars: Sequence[PushTick]):
        n = len(bars)
        self.bars = bars
        self.ts = [b.event_time.timestamp() for b in bars]
        self.vol = [0.0] * (n + 1)
        self.up = [0.0] * (n + 1)
        self.dn = [0.0] * (n + 1)
        self.buy = [0.0] * (n + 1)
        self.sell = [0.0] * (n + 1)
        self.tv = [0.0] * (n + 1)
        self.ok = [True] * n
        for i, b in enumerate(bars):
            self.vol[i + 1] = self.vol[i]
            self.up[i + 1] = self.up[i]
            self.dn[i + 1] = self.dn[i]
            self.buy[i + 1] = self.buy[i]
            self.sell[i + 1] = self.sell[i]
            self.tv[i + 1] = self.tv[i]
            if b.dq_volume_reset or b.volume_delta is None:
                self.ok[i] = False
                continue
            vd = float(b.volume_delta)
            self.vol[i + 1] += vd
            if b.tick_direction > 0:
                self.up[i + 1] += vd
            elif b.tick_direction < 0:
                self.dn[i + 1] += vd
            if b.buy_aggression is not None:
                if b.buy_aggression >= 0.5:
                    self.buy[i + 1] += vd
                else:
                    self.sell[i + 1] += vd
            if b.trading_value_delta is not None:
                self.tv[i + 1] += float(b.trading_value_delta)

    def left_idx(self, i: int, sec: float, excl_cur: bool = False) -> int:
        t1 = self.ts[i]
        j = i - (1 if excl_cur else 0)
        while j >= 0 and t1 - self.ts[j] <= sec:
            j -= 1
        return j + 1

    def sum_vol(self, lo: int, hi_incl: int) -> Optional[float]:
        if lo > hi_incl or lo < 0:
            return None
        for k in range(lo, hi_incl + 1):
            if not self.ok[k]:
                return None
        return self.vol[hi_incl + 1] - self.vol[lo]


def _features_fast(p: _Prefix, i: int) -> FeatureSnap:
    if i < 40:
        return FeatureSnap(False, "insufficient_history")
    bars = p.bars
    cur = bars[i]
    vals: dict[str, Optional[float]] = {}

    for sec, name in ((5, "5s"), (10, "10s"), (30, "30s"), (60, "60s"), (120, "120s")):
        lo = p.left_idx(i, sec)
        if any(not p.ok[k] for k in range(lo, i + 1)):
            vals[f"volume_{name}"] = None
        else:
            vals[f"volume_{name}"] = p.vol[i + 1] - p.vol[lo]
        vals[f"tick_count_{name}"] = float(i - lo + 1)
        vals[f"trading_value_{name}"] = p.tv[i + 1] - p.tv[lo]

    for sec, name in ((10, "10s"), (30, "30s")):
        lo = p.left_idx(i, sec)
        if any(not p.ok[k] for k in range(lo, i + 1)):
            vals[f"uptick_volume_ratio_{name}"] = None
            vals[f"ask_execution_ratio_{name}"] = None
            vals[f"uptick_volume_{name}"] = None
            vals[f"downtick_volume_{name}"] = None
        else:
            up = p.up[i + 1] - p.up[lo]
            dn = p.dn[i + 1] - p.dn[lo]
            buy = p.buy[i + 1] - p.buy[lo]
            sell = p.sell[i + 1] - p.sell[lo]
            vals[f"uptick_volume_{name}"] = up
            vals[f"downtick_volume_{name}"] = dn
            tot = up + dn
            vals[f"uptick_volume_ratio_{name}"] = (up / tot) if tot > 0 else None
            at = buy + sell
            vals[f"ask_execution_ratio_{name}"] = (buy / at) if at > 0 else None

    v10 = vals.get("volume_10s")
    lo20 = p.left_idx(i, 20)
    s20 = None if any(not p.ok[k] for k in range(lo20, i + 1)) else p.vol[i + 1] - p.vol[lo20]
    prior10 = (s20 - v10) if s20 is not None and v10 is not None else None
    vals["volume_acceleration_10s"] = (v10 / prior10) if v10 is not None and prior10 and prior10 > 0 else None
    vals["volume_acceleration_30s"] = None
    tc10, tc5 = vals.get("tick_count_10s"), vals.get("tick_count_5s")
    vals["tick_acceleration_10s"] = (tc10 / tc5) if tc10 and tc5 and tc5 > 0 else None
    tv30, tv10 = vals.get("trading_value_30s"), vals.get("trading_value_10s")
    vals["turnover_acceleration_30s"] = (tv30 / tv10) if tv30 and tv10 and tv10 > 0 else None

    # baseline medians via completed non-overlapping windows
    def baseline(win: float, n: int) -> Optional[float]:
        vols = []
        end_t = p.ts[i] - win
        for _ in range(n):
            start_t = end_t - win
            # find indices
            hi = i - 1
            while hi >= 0 and p.ts[hi] > end_t:
                hi -= 1
            lo = hi
            while lo >= 0 and p.ts[lo] > start_t:
                lo -= 1
            lo += 1
            if hi < lo:
                return None
            if any(not p.ok[k] for k in range(lo, hi + 1)):
                return None
            vols.append(p.vol[hi + 1] - p.vol[lo])
            end_t = start_t
        if len(vols) < max(3, n // 2):
            return None
        m = median(vols)
        return float(m) if m > 0 else None

    b10 = baseline(10.0, 12)
    b30 = baseline(30.0, 10)
    v30 = vals.get("volume_30s")
    vals["volume_impulse_10s"] = (v10 / b10) if v10 is not None and b10 else None
    vals["volume_impulse_30s"] = (v30 / b30) if v30 is not None and b30 else None

    for sec, name in ((30, "30s"), (60, "60s"), (120, "120s")):
        lo = p.left_idx(i, sec, excl_cur=True)
        hi = i - 1
        if hi < lo:
            vals[f"micro_high_{name}"] = None
            vals[f"micro_low_{name}"] = None
        else:
            px = [bars[k].current_price for k in range(lo, hi + 1)]
            vals[f"micro_high_{name}"] = max(px)
            vals[f"micro_low_{name}"] = min(px)
    for sec, name in ((60, "60s"), (120, "120s"), (180, "180s")):
        lo = p.left_idx(i, sec, excl_cur=True)
        hi = i - 1
        if hi - lo + 1 < 3:
            vals[f"range_high_{name}"] = None
            vals[f"range_low_{name}"] = None
        else:
            px = [bars[k].current_price for k in range(lo, hi + 1)]
            vals[f"range_high_{name}"] = max(px)
            vals[f"range_low_{name}"] = min(px)

    rh, rl = vals.get("range_high_120s"), vals.get("range_low_120s")
    vals["range_width"] = (rh - rl) if rh is not None and rl is not None else None
    for sec, name in ((30, "30s"), (60, "60s"), (120, "120s")):
        lo = p.left_idx(i, sec, excl_cur=True)
        hi = i - 1
        if hi <= lo or bars[lo].current_price <= 0:
            vals[f"price_slope_{name}"] = None
        else:
            vals[f"price_slope_{name}"] = (bars[hi].current_price - bars[lo].current_price) / bars[lo].current_price * 100.0

    mh60, ml60 = vals.get("micro_high_60s"), vals.get("micro_low_60s")
    px = cur.current_price
    vals["dist_from_high_60s"] = ((mh60 - px) / mh60 * 100.0) if mh60 and mh60 > 0 else None
    vals["bounce_from_low_60s"] = ((px - ml60) / ml60 * 100.0) if ml60 and ml60 > 0 else None
    vals["recent_rise_60s"] = vals.get("price_slope_60s")
    near, rise = vals.get("dist_from_high_60s"), vals.get("price_slope_60s")
    vals["chase_overheat"] = 1.0 if (near is not None and near < 0.15 and rise is not None and rise > 1.5) else 0.0
    vals["accel_down"] = 1.0 if (rise is not None and rise < -1.0) else 0.0
    lo30 = p.left_idx(i, 30, excl_cur=True)
    if cur.spread_bps is not None and lo30 <= i - 1 and bars[lo30].spread_bps is not None:
        vals["spread_change_30s"] = cur.spread_bps - bars[lo30].spread_bps
    else:
        vals["spread_change_30s"] = None
    lo120 = p.left_idx(i, 120, excl_cur=True)
    nh = nl = 0
    if lo120 <= i - 1:
        hi = lo = bars[lo120].current_price
        for k in range(lo120 + 1, i):
            if bars[k].current_price > hi:
                nh += 1
                hi = bars[k].current_price
            if bars[k].current_price < lo:
                nl += 1
                lo = bars[k].current_price
    vals["new_high_count_120s"] = float(nh)
    vals["new_low_count_120s"] = float(nl)
    ctx_ok = False
    if rh is not None and rl is not None and rh > 0:
        if (rh - rl) / rh * 100.0 <= 0.8:
            ctx_ok = True
        if ml60 is not None and mh60 is not None and px < mh60 and (mh60 - ml60) / mh60 * 100.0 >= 0.2:
            ctx_ok = True
    vals["context_structure_ok"] = 1.0 if ctx_ok else 0.0
    vals["sell_pressure_faded"] = (
        1.0
        if (vals.get("new_low_count_120s") or 0) <= 2
        and (vals.get("uptick_volume_ratio_30s") is None or (vals.get("uptick_volume_ratio_30s") or 0) >= 0.45)
        else 0.0
    )
    if vals.get("volume_impulse_10s") is None and vals.get("volume_impulse_30s") is None:
        return FeatureSnap(False, "volume_impulse_not_evaluable", vals)
    return FeatureSnap(True, "", vals)


def compute_features_at(ticks: Sequence[PushTick], i: int) -> FeatureSnap:
    bars = ticks if (len(ticks) >= 2 and 0.5 <= (ticks[1].event_time - ticks[0].event_time).total_seconds() <= 2.5) else aggregate_to_seconds(ticks)
    if i >= len(bars):
        return FeatureSnap(False, "oob")
    return _features_fast(_Prefix(bars), i)


def _breakout_level(feat: dict[str, Optional[float]]) -> tuple[Optional[float], str]:
    for k in ("micro_high_60s", "micro_high_120s", "range_high_120s", "range_high_60s"):
        v = feat.get(k)
        if v is not None:
            return float(v), k
    return None, ""


def _check_hold(bars: Sequence[PushTick], cross_i: int, level: float, thr: ThresholdSet) -> tuple[bool, int, float, int]:
    t0 = bars[cross_i].event_time
    above = 0
    for j in range(cross_i, min(len(bars), cross_i + 30)):
        if bars[j].current_price <= level:
            return False, above, (bars[j].event_time - t0).total_seconds(), cross_i
        above += 1
        held = (bars[j].event_time - t0).total_seconds()
        if thr.hold_mode == "ticks" and above >= int(thr.hold_n):
            return True, above, held, j
        if thr.hold_mode == "sec" and held >= thr.hold_n:
            return True, above, held, j
    return False, above, 0.0, cross_i


def detect_triggers_for_symbol(
    ticks: Sequence[PushTick],
    *,
    method: str,
    thr: ThresholdSet,
    step: int = 2,
) -> list[Trigger]:
    if len(ticks) >= 2 and 0.5 <= (ticks[1].event_time - ticks[0].event_time).total_seconds() <= 2.5:
        bars = list(ticks)
    else:
        bars = aggregate_to_seconds(ticks)
    if len(bars) < 80:
        return []
    pfx = _Prefix(bars)
    out: list[Trigger] = []
    cooldown_until: Optional[datetime] = None
    i = 50
    while i < len(bars) - 3:
        cur = bars[i]
        if cooldown_until and cur.event_time < cooldown_until:
            i += step
            continue
        if not pfx.ok[i] or (cur.volume_delta or 0) <= 0 and cur.tick_direction == 0:
            i += step
            continue
        feat_snap = _features_fast(pfx, i)
        if not feat_snap.ok and method != "V1_CROSS":
            i += step
            continue
        feat = feat_snap.values
        level, kind = _breakout_level(feat)
        if level is None or cur.previous_price is None:
            i += step
            continue
        if not (cur.previous_price <= level < cur.current_price or (cur.previous_price <= level and cur.current_price > level)):
            i += step
            continue
        ch = feat.get("spread_change_30s")
        if (ch is not None and ch > 3.0) or (cur.spread_bps is not None and cur.spread_bps > 25.0):
            i += step
            continue

        if method != "V1_CROSS":
            vi10, vi30 = feat.get("volume_impulse_10s"), feat.get("volume_impulse_30s")
            if vi10 is None or vi30 is None or vi10 < thr.vol_impulse_10s or vi30 < thr.vol_impulse_30s:
                i += step
                continue
        if method in ("V3_TRADE_SIDE", "V4_FULL_VCIE", "V7_INDEPENDENT", "V5_PBV2_OR", "V6_PBV2_AND"):
            ratio = feat.get("ask_execution_ratio_10s") if thr.use_ask_exec else feat.get("uptick_volume_ratio_10s")
            need = thr.ask_exec_ratio if thr.use_ask_exec else thr.uptick_ratio
            if ratio is None or ratio < need:
                i += step
                continue
        if method in ("V4_FULL_VCIE", "V7_INDEPENDENT", "V5_PBV2_OR", "V6_PBV2_AND"):
            if feat.get("chase_overheat", 0) >= 1 or feat.get("accel_down", 0) >= 1:
                i += step
                continue
            if feat.get("sell_pressure_faded", 0) < 1 or feat.get("context_structure_ok", 0) < 1:
                i += step
                continue

        ok_hold, ht, hs, entry_i = _check_hold(bars, i, level, thr)
        if not ok_hold:
            i += step
            continue
        entry = bars[entry_i]
        impulse_t = cur.event_time
        impulse_lag = (entry.event_time - impulse_t).total_seconds()
        if method != "V1_CROSS" and impulse_lag > MAX_IMPULSE_TO_ENTRY_SEC:
            i += step
            continue
        ctx_start = entry.event_time - timedelta(seconds=min(thr.context_age_sec, 180.0))
        ctx_age = (entry.event_time - ctx_start).total_seconds()
        if ctx_age > MAX_CONTEXT_AGE_SEC:
            i += step
            continue

        out.append(
            Trigger(
                day=entry.day,
                symbol=entry.symbol,
                event_time=entry.event_time,
                entry_price=entry.current_price,
                method=method,
                breakout_level=level,
                breakout_kind=kind,
                features=dict(feat),
                trade_side_quality=entry.trade_side_quality,
                impulse_time=impulse_t,
                context_time=ctx_start,
                impulse_to_entry_sec=impulse_lag,
                context_to_entry_sec=ctx_age,
                hold_ticks=ht,
                hold_sec=hs,
                episode_id=uuid4().hex[:12],
                thresholds={
                    "vol_impulse_10s": thr.vol_impulse_10s,
                    "vol_impulse_30s": thr.vol_impulse_30s,
                    "uptick_ratio": thr.uptick_ratio,
                    "hold_mode": thr.hold_mode,
                    "hold_n": thr.hold_n,
                    "context_age_sec": thr.context_age_sec,
                    "bar": "1s_aggregated_from_push",
                },
                row_index=entry_i,
            )
        )
        cooldown_until = entry.event_time + timedelta(seconds=60)
        i = entry_i + 1
    return out


def fit_thresholds_on_train(by_sym: dict[str, list[PushTick]], *, method: str) -> ThresholdSet:
    impulses10: list[float] = []
    impulses30: list[float] = []
    upticks: list[float] = []
    n_sym = 0
    for ticks in by_sym.values():
        n_sym += 1
        if n_sym > 30:
            break
        bars = aggregate_to_seconds(ticks) if not (
            len(ticks) >= 2 and 0.5 <= (ticks[1].event_time - ticks[0].event_time).total_seconds() <= 2.5
        ) else list(ticks)
        if len(bars) < 80:
            continue
        pfx = _Prefix(bars)
        for i in range(50, len(bars), 60):
            fs = _features_fast(pfx, i)
            v = fs.values.get("volume_impulse_10s")
            w = fs.values.get("volume_impulse_30s")
            u = fs.values.get("uptick_volume_ratio_10s")
            if v is not None:
                impulses10.append(v)
            if w is not None:
                impulses30.append(w)
            if u is not None:
                upticks.append(u)

    def _pick(grid: Sequence[float], samples: list[float], default: float) -> float:
        if len(samples) < 30:
            return default
        target = float(np.quantile(samples, 0.70))
        return float(min(grid, key=lambda g: abs(g - target)))

    return ThresholdSet(
        vol_impulse_10s=_pick(VOL_IMPULSE_10S_GRID, impulses10, 1.5),
        vol_impulse_30s=_pick(VOL_IMPULSE_30S_GRID, impulses30, 1.3),
        uptick_ratio=_pick(UPTICK_RATIO_GRID, upticks, 0.55),
        ask_exec_ratio=0.55,
        hold_mode="sec",
        hold_n=5.0,
        context_age_sec=180.0,
        use_ask_exec=False,
    )
