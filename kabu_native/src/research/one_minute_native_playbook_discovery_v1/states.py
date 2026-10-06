"""Causal 1-minute states. Features at bar T use [0, T] and are available at T+1m. No 5-minute grid."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add, interval_crosses_lunch, parse_hhmm
from research.cause_first_mechanism_discovery_v1.panel import _num, in_invalid_entry
from research.fixed_universe_historical_foundation_v1.technical import session_vwap
from research.one_minute_native_playbook_discovery_v1 import (
    COMPRESSION,
    IMPULSE_SIGMA,
    LOOKBACK_BARS,
    RANGE_EXPAND,
    VOL_EXPAND,
)


def to_min(hhmm: str) -> int | None:
    p = parse_hhmm(hhmm)
    if p is None:
        return None
    return p[0] * 60 + p[1]


def clock_ret(times: list[str], close: np.ndarray, idx: dict[str, int], i: int, n: int) -> float:
    tgt = hhmm_add(times[i], -int(n))
    if not tgt or interval_crosses_lunch(tgt, times[i]):
        return float("nan")
    j = idx.get(tgt)
    if j is None:
        return float("nan")
    a, b = close[i], close[j]
    if not np.isfinite(a) or not np.isfinite(b) or b == 0:
        return float("nan")
    return float(a / b - 1.0)


def prior_mean(x: np.ndarray, i: int, n: int) -> float:
    if i < n:
        return float("nan")
    w = x[i - n : i]
    w = w[np.isfinite(w)]
    if w.size < n:
        return float("nan")
    return float(np.mean(w))


def prior_std(x: np.ndarray, i: int, n: int) -> float:
    if i < n:
        return float("nan")
    w = x[i - n : i]
    w = w[np.isfinite(w)]
    if w.size < n:
        return float("nan")
    return float(np.std(w))


def prior_max(x: np.ndarray, i: int, n: int) -> float:
    if i < n:
        return float("nan")
    w = x[i - n : i]
    if not np.all(np.isfinite(w)):
        return float("nan")
    return float(np.max(w))


def prior_min(x: np.ndarray, i: int, n: int) -> float:
    if i < n:
        return float("nan")
    w = x[i - n : i]
    if not np.all(np.isfinite(w)):
        return float("nan")
    return float(np.min(w))


def prep_symbol(sg: pd.DataFrame) -> dict[str, Any]:
    sg = sg.sort_values("time_label")
    times = [str(t)[:5] for t in sg["time_label"].tolist()]
    o = _num(sg["open"])
    h = _num(sg["high"])
    l = _num(sg["low"])
    c = _num(sg["close"])
    v = _num(sg["volume"])
    va = _num(sg["trading_value"])
    n = int(c.size)
    idx = {t: i for i, t in enumerate(times)}
    vw = session_vwap(h, l, c, v, va)
    rng = np.where(np.isfinite(c) & (c != 0), (h - l) / np.abs(c), np.nan)
    r1 = np.full(n, np.nan)
    r3 = np.full(n, np.nan)
    r5 = np.full(n, np.nan)
    r15 = np.full(n, np.nan)
    for i in range(n):
        r1[i] = clock_ret(times, c, idx, i, 1)
        r3[i] = clock_ret(times, c, idx, i, 3)
        r5[i] = clock_ret(times, c, idx, i, 5)
        r15[i] = clock_ret(times, c, idx, i, 15)
    first = c[0] if n and np.isfinite(c[0]) and c[0] != 0 else float("nan")
    sess = np.where(np.isfinite(c) & np.isfinite(first) & (first != 0), c / first - 1.0, np.nan)
    return {
        "t": times,
        "idx": idx,
        "o": o,
        "h": h,
        "l": l,
        "c": c,
        "v": v,
        "va": va,
        "vw": vw,
        "rng": rng,
        "r1": r1,
        "r3": r3,
        "r5": r5,
        "r15": r15,
        "sess": sess,
        "n": n,
    }


def features_at(rec: dict[str, Any], i: int) -> dict[str, Any]:
    n = LOOKBACK_BARS
    c0 = rec["c"][i]
    vw = rec["vw"][i]
    r1 = rec["r1"][i]
    r3 = rec["r3"][i]
    r5 = rec["r5"][i]
    r15 = rec["r15"][i]
    sd1 = prior_std(rec["r1"], i, n)
    vm = prior_mean(rec["v"], i, n)
    vam = prior_mean(rec["va"], i, n)
    rm = prior_mean(rec["rng"], i, n)
    hi20 = prior_max(rec["h"], i, n)
    lo20 = prior_min(rec["l"], i, n)
    vol_rel = float(rec["v"][i] / vm) if np.isfinite(vm) and vm > 0 and np.isfinite(rec["v"][i]) else float("nan")
    va_rel = float(rec["va"][i] / vam) if np.isfinite(vam) and vam > 0 and np.isfinite(rec["va"][i]) else float("nan")
    rng_rel = float(rec["rng"][i] / rm) if np.isfinite(rm) and rm > 0 and np.isfinite(rec["rng"][i]) else float("nan")
    dist_vw = float(c0 / vw - 1.0) if np.isfinite(c0) and np.isfinite(vw) and vw != 0 else float("nan")
    dist_hi = float(c0 / hi20 - 1.0) if np.isfinite(c0) and np.isfinite(hi20) and hi20 != 0 else float("nan")
    dist_lo = float(c0 / lo20 - 1.0) if np.isfinite(c0) and np.isfinite(lo20) and lo20 != 0 else float("nan")
    prev_above = False
    prev_below = False
    if i > 0 and np.isfinite(rec["c"][i - 1]) and np.isfinite(rec["vw"][i - 1]):
        prev_above = rec["c"][i - 1] > rec["vw"][i - 1]
        prev_below = rec["c"][i - 1] <= rec["vw"][i - 1]
    above_vw = bool(np.isfinite(c0) and np.isfinite(vw) and c0 > vw)
    impulse_up = bool(np.isfinite(r1) and np.isfinite(sd1) and sd1 > 0 and r1 > 0 and r1 > IMPULSE_SIGMA * sd1)
    impulse_down = bool(np.isfinite(r1) and np.isfinite(sd1) and sd1 > 0 and r1 < 0 and r1 < -IMPULSE_SIGMA * sd1)
    pause = bool(np.isfinite(r1) and np.isfinite(sd1) and sd1 > 0 and abs(float(r1)) < 0.5 * sd1)
    return {
        "ret_1m": r1,
        "ret_3m": r3,
        "ret_5m": r5,
        "ret_15m": r15,
        "rvol_1m": sd1,
        "dist_vwap": dist_vw,
        "dist_20high": dist_hi,
        "dist_20low": dist_lo,
        "vol_rel20": vol_rel,
        "va_rel20": va_rel,
        "rng_rel20": rng_rel,
        "sess_ret": rec["sess"][i],
        "above_vwap": above_vw,
        "vwap_reclaim": bool(above_vw and prev_below),
        "vwap_loss": bool((not above_vw) and prev_above),
        "breakout20": bool(np.isfinite(c0) and np.isfinite(hi20) and c0 >= hi20),
        "breakdown20": bool(np.isfinite(c0) and np.isfinite(lo20) and c0 <= lo20),
        "hi20": hi20,
        "lo20": lo20,
        "pullback": bool(np.isfinite(r15) and np.isfinite(r5) and r15 > 0 and r5 < 0),
        "vol_expand": bool(np.isfinite(vol_rel) and vol_rel >= VOL_EXPAND),
        "va_expand": bool(np.isfinite(va_rel) and va_rel >= VOL_EXPAND),
        "range_expand": bool(np.isfinite(rng_rel) and rng_rel >= RANGE_EXPAND),
        "compression": bool(np.isfinite(rng_rel) and rng_rel <= COMPRESSION),
        "impulse_up": impulse_up,
        "impulse_down": impulse_down,
        "pause": pause,
        "feature_bar": rec["t"][i],
        "available_at": hhmm_add(rec["t"][i], 1),
        "entry_bar": hhmm_add(rec["t"][i], 1),
    }


def families_at(feat: dict[str, Any], *, recent_impulse_up: bool, lagging: bool, leader_impulse_recent: bool) -> list[str]:
    fams: list[str] = []
    if feat.get("impulse_up"):
        fams.append("IMPULSE_UP")
    if feat.get("impulse_down"):
        fams.append("IMPULSE_DOWN")
    if feat.get("vwap_reclaim"):
        fams.append("VWAP_RECLAIM")
    if feat.get("vwap_loss"):
        fams.append("VWAP_LOSS")
    if feat.get("breakout20"):
        fams.append("BREAKOUT20")
    if feat.get("breakdown20"):
        fams.append("BREAKDOWN20")
    if feat.get("pullback"):
        fams.append("PULLBACK_START")
    if feat.get("vol_expand"):
        fams.append("VOL_EXPAND")
    if feat.get("va_expand"):
        fams.append("VA_EXPAND")
    if feat.get("range_expand"):
        fams.append("RANGE_EXPAND")
    if feat.get("compression"):
        fams.append("COMPRESSION")
    if recent_impulse_up and feat.get("pause"):
        fams.append("PAUSE_AFTER_IMPULSE")
    if lagging and leader_impulse_recent and feat.get("impulse_up"):
        fams.append("LAG_CATCHUP")
    return fams


def entry_ok(entry_bar: str | None) -> bool:
    if not entry_bar:
        return False
    if in_invalid_entry(entry_bar):
        return False
    return True
