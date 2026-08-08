"""Fixed 10s grid with causal as-of fill (no future, no session cross)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

from . import (
    AM_END,
    AM_START,
    GRID_SEC,
    PM_END,
    PM_START,
    PRICE_FRESH_MAX,
    VALUE_FRESH_MAX,
    VOLUME_FRESH_MAX,
    VWAP_FRESH_MAX,
)

JST = ZoneInfo("Asia/Tokyo")


def _hm(day: str, hm: tuple[int, int]) -> datetime:
    return datetime(int(day[:4]), int(day[4:6]), int(day[6:]), hm[0], hm[1], 0, tzinfo=JST)


def session_grid_times(day: str) -> list[tuple[str, datetime]]:
    out: list[tuple[str, datetime]] = []
    for sess, start, end in (("AM", AM_START, AM_END), ("PM", PM_START, PM_END)):
        t0, t1 = _hm(day, start), _hm(day, end)
        t = t0
        while t <= t1:
            out.append((sess, t))
            t += timedelta(seconds=GRID_SEC)
    return out


def _asof_idx(times: np.ndarray, target: float) -> int:
    """Largest i with times[i] <= target; -1 if none."""
    i = int(np.searchsorted(times, target, side="right") - 1)
    return i


def build_symbol_day_grid(
    day: str,
    symbol: str,
    ticks: list[dict[str, Any]],
    source_id: str,
) -> list[dict[str, Any]]:
    if not ticks:
        return []
    times = np.asarray([r["t"] for r in ticks], dtype=float)
    prices = np.asarray([r["price"] for r in ticks], dtype=float)
    vols = np.asarray([r["vol"] if r["vol"] is not None else np.nan for r in ticks], dtype=float)
    vals = np.asarray([r["value"] if r["value"] is not None else np.nan for r in ticks], dtype=float)
    vwaps = np.asarray([r["vwap"] if r["vwap"] is not None else np.nan for r in ticks], dtype=float)
    price_t = np.asarray([r["price_t"] for r in ticks], dtype=float)
    vol_t = np.asarray([r["vol_t"] for r in ticks], dtype=float)
    value_t = np.asarray([r["value_t"] for r in ticks], dtype=float)
    vwap_t = np.asarray([r["vwap_t"] for r in ticks], dtype=float)
    vol_reset = np.asarray([1.0 if r["vol_reset"] else 0.0 for r in ticks], dtype=float)

    am_end = _hm(day, AM_END).timestamp()
    pm_start = _hm(day, PM_START).timestamp()

    rows = []
    for sess, gt in session_grid_times(day):
        g = gt.timestamp()
        # session boundary: do not fill across lunch
        if sess == "AM":
            # only use ticks with t <= g and t within AM window start..AM_END+epsilon
            # as-of still uses any prior tick in session — restrict search to AM ticks
            mask_t = times <= g
            # forbid using PM ticks for AM (none should exist with t<=g if g<=am_end)
            if g > am_end + 1:
                continue
        else:
            # PM: do not use AM ticks for fill (session跨ぎfill禁止)
            # only ticks with t >= pm_start and t <= g
            pass

        i = _asof_idx(times, g)
        if i < 0:
            rows.append(_empty_grid(day, sess, gt, symbol, source_id, "NO_PRIOR_TICK"))
            continue
        if sess == "PM" and times[i] < pm_start:
            # would be session-cross fill from AM
            rows.append(_empty_grid(day, sess, gt, symbol, source_id, "SESSION_CROSS_FILL_BLOCKED"))
            continue
        if sess == "AM" and times[i] > am_end + 60:
            rows.append(_empty_grid(day, sess, gt, symbol, source_id, "SESSION_MISMATCH"))
            continue

        px_age = g - float(price_t[i])
        vol_age = g - float(vol_t[i]) if not np.isnan(vols[i]) else None
        val_age = g - float(value_t[i]) if not np.isnan(vals[i]) else None
        vwap_age = g - float(vwap_t[i]) if not np.isnan(vwaps[i]) else None

        reasons = []
        ok = True
        if px_age > PRICE_FRESH_MAX:
            reasons.append("PRICE_STALE")
            ok = False
        if vol_age is None or vol_age > VOLUME_FRESH_MAX:
            reasons.append("VOLUME_STALE_OR_MISSING")
            ok = False
        if val_age is None or val_age > VALUE_FRESH_MAX:
            reasons.append("VALUE_STALE_OR_MISSING")
            ok = False
        if vwap_age is None or vwap_age > VWAP_FRESH_MAX:
            reasons.append("VWAP_STALE_OR_MISSING")
            ok = False

        rows.append({
            "date": day,
            "session": sess,
            "grid_time": gt.isoformat(),
            "grid_epoch": g,
            "symbol": symbol,
            "CurrentPrice": float(prices[i]),
            "TradingVolume": None if np.isnan(vols[i]) else float(vols[i]),
            "TradingValue": None if np.isnan(vals[i]) else float(vals[i]),
            "VWAP": None if np.isnan(vwaps[i]) else float(vwaps[i]),
            "price_age_sec": px_age,
            "volume_age_sec": vol_age,
            "value_age_sec": val_age,
            "vwap_age_sec": vwap_age,
            "source_identity": source_id,
            "quality_status": "OK" if ok else "FEATURE_NOT_EVALUABLE",
            "quality_reasons": reasons,
            "vol_reset_flag": bool(vol_reset[i] > 0),
            "_tick_idx": i,
        })
    return rows


def _empty_grid(day, sess, gt, symbol, source_id, reason) -> dict[str, Any]:
    return {
        "date": day,
        "session": sess,
        "grid_time": gt.isoformat(),
        "grid_epoch": gt.timestamp(),
        "symbol": symbol,
        "CurrentPrice": None,
        "TradingVolume": None,
        "TradingValue": None,
        "VWAP": None,
        "price_age_sec": None,
        "volume_age_sec": None,
        "value_age_sec": None,
        "vwap_age_sec": None,
        "source_identity": source_id,
        "quality_status": "FEATURE_NOT_EVALUABLE",
        "quality_reasons": [reason],
        "vol_reset_flag": False,
        "_tick_idx": -1,
    }


def day_price_volume_quality(day: str, symbol_grids: dict[str, list[dict]]) -> dict[str, Any]:
    """Aggregate price/volume quality (board quality not used)."""
    all_rows = [r for rows in symbol_grids.values() for r in rows]
    if not all_rows:
        return {"date": day, "quality_status": "PRICE_VOLUME_DAY_INVALID", "reasons": ["no_rows"]}
    n = len(all_rows)
    def cov(key):
        return sum(1 for r in all_rows if r.get(key) is not None) / n

    am = sum(1 for r in all_rows if r["session"] == "AM")
    pm = sum(1 for r in all_rows if r["session"] == "PM")
    ok_frac = sum(1 for r in all_rows if r["quality_status"] == "OK") / n
    reasons = []
    status = "PRICE_VOLUME_DAY_VALID"
    if am == 0 or pm == 0:
        reasons.append("missing_session")
        status = "PRICE_VOLUME_DAY_INVALID"
    if cov("CurrentPrice") < 0.5:
        reasons.append("low_price_coverage")
        status = "PRICE_VOLUME_DAY_INVALID"
    if cov("TradingVolume") < 0.3:
        reasons.append("low_volume_coverage")
        status = "PRICE_VOLUME_DAY_INVALID"
    # 20260615-19: do NOT exclude for board imbalance (N/A here)
    return {
        "date": day,
        "quality_status": status,
        "reasons": reasons,
        "CurrentPrice_coverage": cov("CurrentPrice"),
        "TradingVolume_coverage": cov("TradingVolume"),
        "TradingValue_coverage": cov("TradingValue"),
        "VWAP_coverage": cov("VWAP"),
        "ok_grid_fraction": ok_frac,
        "AM_rows": am,
        "PM_rows": pm,
        "n_grid_rows": n,
        "n_symbols": len(symbol_grids),
    }
