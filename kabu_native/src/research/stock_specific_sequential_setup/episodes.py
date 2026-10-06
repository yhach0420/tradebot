"""Causal episode walker. Stage times use only bars at or before the stage bar."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.simple_tech_entry_family.indicators import ema, rci_series
from research.stock_specific_sequential_setup import FAMILIES, HORIZONS, PRIMARY_HORIZON, STRUCTURE_BARS, WARMUP_BARS


def _trend(e9: np.ndarray, e21: np.ndarray, i: int) -> bool:
    if i < WARMUP_BARS - 1:
        return False
    a, b, c = float(e9[i]), float(e21[i]), float(e21[i - 3])
    if not (a == a and b == b and c == c):
        return False
    return a > b and b > c


def _fwd(minutes: np.ndarray, close: np.ndarray, high: np.ndarray, low: np.ndarray, i: int, horizon: int) -> Optional[tuple[float, float, float]]:
    j = i + int(horizon)
    if j >= int(close.size):
        return None
    if int(minutes[j]) - int(minutes[i]) != int(horizon):
        return None
    base = float(close[i])
    if not (base == base) or base <= 0:
        return None
    window_h = high[i + 1 : j + 1]
    window_l = low[i + 1 : j + 1]
    if window_h.size != horizon or not (np.all(np.isfinite(window_h)) and np.all(np.isfinite(window_l))):
        return None
    if not np.isfinite(close[j]):
        return None
    return (float(close[j]) / base - 1.0, float(np.max(window_h)) / base - 1.0, float(np.min(window_l)) / base - 1.0)


def _pack(minutes: np.ndarray, close: np.ndarray, high: np.ndarray, low: np.ndarray, i: int) -> dict[str, Optional[float]]:
    out: dict[str, Optional[float]] = {}
    for horizon in HORIZONS:
        got = _fwd(minutes, close, high, low, i, horizon)
        if got is None:
            out[f"ret_{horizon}"] = None
            out[f"mfe_{horizon}"] = None
            out[f"mae_{horizon}"] = None
        else:
            out[f"ret_{horizon}"] = got[0] * 10000.0
            out[f"mfe_{horizon}"] = got[1] * 10000.0
            out[f"mae_{horizon}"] = got[2] * 10000.0
    return out


def _primitive(name: str, i: int, *, close, high, volume, e9, rci, pullback: int, pb_high_before: float, ema_touched: bool, rci_min_before: float) -> bool:
    if name == "CLOSE_PROGRESSION":
        return float(close[i]) > float(close[i - 1])
    if name == "EMA9_RECLAIM":
        return bool(ema_touched) and float(close[i]) > float(e9[i]) and float(e9[i]) == float(e9[i])
    if name == "PRIOR_BAR_HIGH_RECLAIM":
        return float(close[i]) > float(high[i - 1])
    if name == "PULLBACK_HIGH_RECLAIM":
        return pb_high_before == pb_high_before and float(close[i]) > float(pb_high_before)
    if name == "VOLUME_REEXPANSION":
        if i < 5:
            return False
        base = volume[i - 5 : i]
        if int(base.size) != 5 or not np.all(np.isfinite(base)):
            return False
        return float(volume[i]) > float(np.median(base))
    if name == "RCI_RECOVERY":
        cur, prev = float(rci[i]), float(rci[i - 1])
        if not (cur == cur and prev == prev and rci_min_before == rci_min_before):
            return False
        return cur > prev and cur > float(rci_min_before)
    raise KeyError(name)


def walk_session(minutes: np.ndarray, o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, v: np.ndarray, tv: np.ndarray) -> list[dict[str, Any]]:
    _ = o
    n = int(c.size)
    if n < WARMUP_BARS:
        return []
    e9 = ema(c, 9)
    e21 = ema(c, 21)
    rci = rci_series(c, 9)
    numer = np.cumsum(np.where(np.isfinite(tv), tv, np.where(np.isfinite(c) & np.isfinite(v), c * v, 0.0)))
    denom = np.cumsum(np.where(np.isfinite(v) & (v > 0), v, 0.0))
    vwap = np.divide(numer, denom, out=np.full(n, np.nan), where=denom > 0)
    rows: list[dict[str, Any]] = []
    i = WARMUP_BARS - 1
    while i < n:
        if not _trend(e9, e21, i):
            i += 1
            continue
        trend_start = i
        struct = float(h[i])
        impulse = None
        impulse_high = None
        impulse_close = None
        pullback = None
        pb_low = None
        pb_low_i = None
        pb_high_before = float("nan")
        ema_touched = False
        rci_min_before = float("nan")
        stab = None
        stab_low = None
        stab_low_i = None
        stab_lower_low = None
        stab_below_ema21 = None
        fired: dict[str, Optional[tuple[int, float, float]]] = {name: None for name in FAMILIES}
        lower_low = False
        close_below_ema21 = False
        i += 1
        while i < n and _trend(e9, e21, i):
            if impulse is None:
                if float(c[i]) > float(c[i - 1]) and float(h[i]) > struct:
                    impulse = i
                    impulse_high = float(h[i])
                    impulse_close = float(c[i])
                struct = max(struct, float(h[i]))
            elif pullback is None:
                if float(h[i]) > float(impulse_high):
                    impulse_high = float(h[i])
                if i > int(impulse) and float(c[i]) < float(impulse_close):
                    pullback = i
                    pb_low = float(l[i])
                    pb_low_i = i
                    pb_high_before = float(h[i])
                    lower_low = float(l[i]) < float(l[impulse])
                    close_below_ema21 = float(c[i]) <= float(e21[i])
                    ema_touched = float(c[i]) <= float(e9[i])
                    rci_min_before = float(rci[i])
                struct = max(struct, float(h[i]))
            else:
                if float(l[i]) < float(pb_low):
                    pb_low = float(l[i])
                    pb_low_i = i
                    stab = None
                    lower_low = True
                elif stab is None and i > int(pullback) and float(l[i]) >= float(l[i - 1]) and float(c[i]) >= float(c[i - 1]):
                    stab = i
                    stab_low = float(pb_low)
                    stab_low_i = int(pb_low_i)
                    stab_lower_low = bool(lower_low)
                    stab_below_ema21 = bool(close_below_ema21 or float(c[i]) <= float(e21[i]))
                if float(c[i]) <= float(e21[i]):
                    close_below_ema21 = True
                if stab is not None:
                    for name in FAMILIES:
                        if fired[name] is None and _primitive(
                            name,
                            i,
                            close=c,
                            high=h,
                            volume=v,
                            e9=e9,
                            rci=rci,
                            pullback=int(pullback),
                            pb_high_before=pb_high_before,
                            ema_touched=ema_touched,
                            rci_min_before=rci_min_before,
                        ):
                            fired[name] = (int(i), float(pb_low), float(rci_min_before) if rci_min_before == rci_min_before else float("nan"))
                ema_touched = ema_touched or float(c[i]) <= float(e9[i])
                if float(rci[i]) == float(rci[i]):
                    rci_min_before = float(rci[i]) if rci_min_before != rci_min_before else min(float(rci_min_before), float(rci[i]))
                pb_high_before = float(h[i]) if pb_high_before != pb_high_before else max(float(pb_high_before), float(h[i]))
            i += 1
        end = i
        signals = []
        for name, snapped in fired.items():
            if snapped is None:
                continue
            at, sig_low, sig_rci_min = snapped
            left = max(0, int(at) - STRUCTURE_BARS)
            resistance = float(np.nanmax(h[left:at])) if at > left else float("nan")
            support = float(np.nanmin(l[left:at])) if at > left else float("nan")
            base = float(c[at])
            room = None if not (resistance == resistance and base > 0) else (resistance - base) / base * 10000.0
            vol_imp = float(v[impulse]) if impulse is not None else float("nan")
            vol_pb = float(np.nanmedian(v[int(pullback) : int(at)])) if pullback is not None else float("nan")
            vol_re = float(v[at])
            signals.append(
                {
                    "family": name,
                    "bar": int(at),
                    "minute": int(minutes[at]),
                    "room_to_resistance_bps": room,
                    "support": support,
                    "resistance": resistance,
                    "impulse_high": impulse_high,
                    "pullback_low": sig_low,
                    "volume_impulse": vol_imp,
                    "volume_pullback": vol_pb,
                    "volume_reacceleration": vol_re,
                    "pullback_impulse_volume_ratio": None if not (vol_imp == vol_imp and vol_imp > 0 and vol_pb == vol_pb) else vol_pb / vol_imp,
                    "reacceleration_pullback_volume_ratio": None if not (vol_pb == vol_pb and vol_pb > 0) else vol_re / vol_pb,
                    "rci_impulse": None if impulse is None or float(rci[impulse]) != float(rci[impulse]) else float(rci[impulse]),
                    "rci_pullback_min": None if sig_rci_min != sig_rci_min else float(sig_rci_min),
                    "rci_signal": None if float(rci[at]) != float(rci[at]) else float(rci[at]),
                    **_pack(minutes, c, h, l, int(at)),
                }
            )
        stage_bars = {
            "TREND_CONTEXT": trend_start,
            "IMPULSE": impulse,
            "PULLBACK": pullback,
            "STABILIZATION": stab,
        }
        stages = {}
        for key, bar in stage_bars.items():
            stages[key] = None if bar is None else {"bar": int(bar), "minute": int(minutes[bar]), **_pack(minutes, c, h, l, int(bar))}
        depth = None
        if impulse_high is not None and stab_low is not None and float(impulse_high) > 0 and stab is not None:
            depth = (float(impulse_high) - float(stab_low)) / float(impulse_high) * 10000.0
        stab_i = None if stab is None else int(stab)
        rows.append(
            {
                "trend_start_minute": int(minutes[trend_start]),
                "impulse_minute": None if impulse is None else int(minutes[impulse]),
                "pullback_start_minute": None if pullback is None else int(minutes[pullback]),
                "pullback_low_minute": None if pb_low_i is None else int(minutes[pb_low_i]),
                "stabilization_minute": None if stab is None else int(minutes[stab]),
                "reacceleration_minute": None if not signals else int(minutes[min(s["bar"] for s in signals)]),
                "reached_impulse": impulse is not None,
                "reached_pullback": pullback is not None,
                "reached_stabilization": stab is not None,
                "end_bar": int(end),
                "stages": stages,
                "pullback_depth_bps": depth,
                "distance_ema9_bps": None
                if stab_i is None or not (float(e9[stab_i]) == float(e9[stab_i]) and float(c[stab_i]) > 0)
                else (float(c[stab_i]) - float(e9[stab_i])) / float(c[stab_i]) * 10000.0,
                "distance_ema21_bps": None
                if stab_i is None or not (float(e21[stab_i]) == float(e21[stab_i]) and float(c[stab_i]) > 0)
                else (float(c[stab_i]) - float(e21[stab_i])) / float(c[stab_i]) * 10000.0,
                "distance_vwap_bps": None
                if stab_i is None or not (float(vwap[stab_i]) == float(vwap[stab_i]) and float(c[stab_i]) > 0)
                else (float(c[stab_i]) - float(vwap[stab_i])) / float(c[stab_i]) * 10000.0,
                "lower_low": stab_lower_low,
                "close_below_ema21": stab_below_ema21,
                "pullback_duration_bars": None if stab is None or pullback is None else int(stab) - int(pullback),
                "stabilization_ret5": None if stab is None else stages["STABILIZATION"][f"ret_{PRIMARY_HORIZON}"],
                "signals": signals,
            }
        )
    return rows


def self_check() -> dict[str, bool]:
    minutes = np.arange(9 * 60, 9 * 60 + 40, dtype=int)
    close = np.concatenate([np.linspace(100, 110, 30), np.array([112, 109, 108.5, 109.5, 111, 113, 114, 115, 116, 117], dtype=float)])
    high = close + 0.4
    low = close - 0.4
    high[30] = 113
    low[32] = 107
    close[32] = 108
    volume = np.full(close.size, 1000.0)
    volume[35] = 5000.0
    got = walk_session(minutes, close, high, low, close, volume, close * volume)
    reached = [row for row in got if row["reached_stabilization"]]
    families = set()
    for row in reached:
        families.update(sig["family"] for sig in row["signals"])
    return {
        "walked": bool(got),
        "stabilized": bool(reached),
        "has_close_progression": "CLOSE_PROGRESSION" in families,
        "no_future_stage_before_trend": all(row["trend_start_minute"] <= (row["impulse_minute"] or row["trend_start_minute"]) for row in got),
    }
