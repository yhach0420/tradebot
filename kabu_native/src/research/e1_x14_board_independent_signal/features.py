"""Board-forbidden-free feature + label + cluster contracts."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from . import CLUSTER_WINDOW_SEC, FORBIDDEN_BOARD_COLUMNS, LABEL_HORIZONS, MIN_RS_UNIVERSE


def _ret(px_now: float, px_then: Optional[float]) -> Optional[float]:
    if px_then is None or px_then <= 0 or px_now is None:
        return None
    return (px_now / px_then) - 1.0


def _bps(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a / b - 1.0) * 10000.0


def attach_path_volume_features(
    grid_rows: list[dict[str, Any]],
    ticks: list[dict[str, Any]],
    start_index: int = 0,
) -> list[dict[str, Any]]:
    """Causal features from same-symbol grid history + tick path. No board fields.

    start_index>0 recomputes only grid_rows[start_index:] and restores session
    high/low from earlier OK rows (incremental T1; default 0 is unchanged).
    """
    if not grid_rows or not ticks:
        return grid_rows
    times = np.asarray([t["t"] for t in ticks], dtype=float)
    prices = np.asarray([t["price"] for t in ticks], dtype=float)
    vols = np.asarray([t["vol"] if t["vol"] is not None else np.nan for t in ticks], dtype=float)

    # index grids by epoch for lookback
    by_ep = {r["grid_epoch"]: r for r in grid_rows if r.get("CurrentPrice") is not None}

    sess_high: dict[str, float] = {}
    sess_low: dict[str, float] = {}
    start_index = max(0, min(int(start_index), len(grid_rows)))
    for r in grid_rows[:start_index]:
        if r.get("quality_status") != "OK" or r.get("CurrentPrice") is None:
            continue
        px = float(r["CurrentPrice"])
        sess = r["session"]
        sess_high[sess] = max(sess_high.get(sess, px), px)
        sess_low[sess] = min(sess_low.get(sess, px), px)

    for r in grid_rows[start_index:]:
        for col in FORBIDDEN_BOARD_COLUMNS:
            assert col not in r or r.get(col) is None
        if r.get("quality_status") != "OK" or r.get("CurrentPrice") is None:
            r["feature_status"] = "FEATURE_NOT_EVALUABLE"
            continue
        g = r["grid_epoch"]
        sess = r["session"]
        px = float(r["CurrentPrice"])
        sess_high[sess] = max(sess_high.get(sess, px), px)
        sess_low[sess] = min(sess_low.get(sess, px), px)

        def px_at_lag(sec: int) -> Optional[float]:
            tgt = g - sec
            # same session only
            i = int(np.searchsorted(times, tgt, side="right") - 1)
            if i < 0:
                return None
            # session cross block for lookback into other session
            if sess == "PM":
                # need tick in PM
                from datetime import datetime
                from zoneinfo import ZoneInfo
                JST = ZoneInfo("Asia/Tokyo")
                day = r["date"]
                pm0 = datetime(int(day[:4]), int(day[4:6]), int(day[6:]), 12, 30, tzinfo=JST).timestamp()
                if times[i] < pm0:
                    return None
            return float(prices[i])

        def vol_at_lag(sec: int) -> Optional[float]:
            tgt = g - sec
            i = int(np.searchsorted(times, tgt, side="right") - 1)
            if i < 0 or np.isnan(vols[i]):
                return None
            if sess == "PM":
                from datetime import datetime
                from zoneinfo import ZoneInfo
                JST = ZoneInfo("Asia/Tokyo")
                day = r["date"]
                pm0 = datetime(int(day[:4]), int(day[4:6]), int(day[6:]), 12, 30, tzinfo=JST).timestamp()
                if times[i] < pm0:
                    return None
            return float(vols[i])

        r["return_30s"] = _ret(px, px_at_lag(30))
        r["return_60s"] = _ret(px, px_at_lag(60))
        r["return_180s"] = _ret(px, px_at_lag(180))
        r["return_300s"] = _ret(px, px_at_lag(300))
        r30 = r["return_30s"]
        # slope ~ return / time
        r["slope_60s"] = (r["return_60s"] / 60.0) if r["return_60s"] is not None else None
        r["slope_180s"] = (r["return_180s"] / 180.0) if r["return_180s"] is not None else None
        prior30 = _ret(px_at_lag(30) or px, px_at_lag(60)) if px_at_lag(30) else None
        r["acceleration_30s_vs_prior30s"] = (
            (r30 - prior30) if (r30 is not None and prior30 is not None) else None
        )
        vwap = r.get("VWAP")
        r["distance_from_vwap_bps"] = _bps(px, float(vwap)) if vwap else None
        r["distance_from_session_high_bps"] = _bps(px, sess_high[sess])
        r["distance_from_session_low_bps"] = _bps(px, sess_low[sess])

        # recent high/low over 180s from ticks
        i_now = int(np.searchsorted(times, g, side="right") - 1)
        i_180 = int(np.searchsorted(times, g - 180, side="right") - 1)
        if i_now >= 0 and i_180 >= 0 and i_now > i_180:
            window = prices[i_180 + 1: i_now + 1]
            rh, rl = float(np.max(window)), float(np.min(window))
            r["drawdown_from_recent_high_bps"] = _bps(px, rh)
            r["rebound_from_recent_low_bps"] = _bps(px, rl)
            r["recent_high_break"] = 1.0 if px >= rh - 1e-9 and len(window) > 2 else 0.0
            r["recent_low_break"] = 1.0 if px <= rl + 1e-9 and len(window) > 2 else 0.0
            r["range_width_180s"] = (rh - rl) / px if px else None
            # higher/lower low: compare min of last 90s vs prior 90s
            mid = int(np.searchsorted(times, g - 90, side="right") - 1)
            if mid > i_180:
                low_recent = float(np.min(prices[mid + 1: i_now + 1]))
                low_prior = float(np.min(prices[i_180 + 1: mid + 1]))
                r["higher_low_180s"] = 1.0 if low_recent > low_prior else 0.0
                r["lower_low_180s"] = 1.0 if low_recent < low_prior else 0.0
            else:
                r["higher_low_180s"] = None
                r["lower_low_180s"] = None
        else:
            for k in ("drawdown_from_recent_high_bps", "rebound_from_recent_low_bps",
                      "recent_high_break", "recent_low_break", "range_width_180s",
                      "higher_low_180s", "lower_low_180s"):
                r[k] = None

        i_60 = int(np.searchsorted(times, g - 60, side="right") - 1)
        if i_now >= 0 and i_60 >= 0 and i_now > i_60:
            w = prices[i_60 + 1: i_now + 1]
            r["range_width_60s"] = (float(np.max(w)) - float(np.min(w))) / px if px else None
        else:
            r["range_width_60s"] = None

        # volume deltas from cumulative TradingVolume
        v0 = r.get("TradingVolume")
        def vdelta(sec: int) -> Optional[float]:
            if v0 is None:
                return None
            v1 = vol_at_lag(sec)
            if v1 is None:
                return None
            if v0 + 1e-9 < v1:  # reset
                return None
            return float(v0 - v1)

        r["volume_delta_30s"] = vdelta(30)
        r["volume_delta_60s"] = vdelta(60)
        r["volume_delta_180s"] = vdelta(180)
        r["volume_delta_300s"] = vdelta(300)
        r["volume_rate_30s"] = (r["volume_delta_30s"] / 30.0) if r["volume_delta_30s"] is not None else None
        r["volume_rate_60s"] = (r["volume_delta_60s"] / 60.0) if r["volume_delta_60s"] is not None else None
        d30 = r["volume_delta_30s"]
        d120 = vdelta(120)
        r["volume_ratio_30s_vs_prior120s"] = (
            (d30 / ((d120 - d30) / 4.0)) if (d30 is not None and d120 is not None and d120 > d30 and (d120 - d30) > 0) else None
        )
        d60 = r["volume_delta_60s"]
        d300 = r["volume_delta_300s"]
        r["volume_ratio_60s_vs_prior300s"] = (
            (d60 / ((d300 - d60) / 5.0)) if (d60 is not None and d300 is not None and d300 > d60 and (d300 - d60) > 0) else None
        )
        # active fraction: share of 10s steps with positive volume delta
        def active_frac(sec: int) -> Optional[float]:
            steps = sec // 10
            if steps <= 0 or v0 is None:
                return None
            hits = 0
            known = 0
            for s in range(10, sec + 1, 10):
                a = vol_at_lag(s - 10)
                b = vol_at_lag(s) if s < sec else v0
                # compare cumulative at g-(s-10) vs g-s
                va = vol_at_lag(s)
                vb = vol_at_lag(s - 10) if s >= 10 else None
                if va is None or vb is None:
                    continue
                known += 1
                if vb > va:  # cumulative increased going forward... wait
                    # at lag s volume should be <= lag (s-10) if time moves forward
                    # vol_at_lag(s) is volume at g-s; vol_at_lag(s-10) at g-(s-10) later
                    if vol_at_lag(s - 10) is not None and vol_at_lag(s) is not None:
                        if float(vol_at_lag(s - 10)) > float(vol_at_lag(s)):
                            hits += 1
            return hits / known if known else None

        r["volume_active_fraction_180s"] = active_frac(180)
        r["volume_active_fraction_300s"] = active_frac(300)
        r["volume_persistence_180s"] = r["volume_active_fraction_180s"]
        r["volume_persistence_300s"] = r["volume_active_fraction_300s"]

        tv0 = r.get("TradingValue")
        def tvdelta(sec: int) -> Optional[float]:
            if tv0 is None:
                return None
            tgt = g - sec
            i = int(np.searchsorted(times, tgt, side="right") - 1)
            if i < 0:
                return None
            tv1 = ticks[i].get("value")
            if tv1 is None:
                return None
            if float(tv0) + 1e-9 < float(tv1):
                return None
            return float(tv0) - float(tv1)

        r["trading_value_delta_60s"] = tvdelta(60)
        r["trading_value_delta_180s"] = tvdelta(180)
        r["feature_status"] = "OK"
    return grid_rows


def attach_relative_strength(day_grids: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Same-timestamp universe only; min 20 evaluable symbols."""
    # group by grid_epoch
    by_g: dict[float, list[dict]] = {}
    for sym, rows in day_grids.items():
        for r in rows:
            if r.get("feature_status") != "OK":
                continue
            if r.get("return_60s") is None:
                continue
            by_g.setdefault(r["grid_epoch"], []).append(r)

    out = []
    for g, rows in by_g.items():
        if len(rows) < MIN_RS_UNIVERSE:
            for r in rows:
                r["relative_status"] = "RELATIVE_STRENGTH_NOT_EVALUABLE"
                r["rs_universe_n"] = len(rows)
            out.extend(rows)
            continue
        for key, med_name, pct_name in (
            ("return_60s", "universe_median_return_60s", "return_percentile_60s"),
            ("return_180s", "universe_median_return_180s", "return_percentile_180s"),
            ("return_300s", "universe_median_return_300s", None),
        ):
            xs = [float(r[key]) for r in rows if r.get(key) is not None]
            if not xs:
                continue
            med = float(np.median(xs))
            for r in rows:
                r[med_name] = med
                if r.get(key) is not None:
                    r[f"symbol_minus_median_{key}"] = float(r[key]) - med
                    if pct_name:
                        r[pct_name] = float(sum(1 for x in xs if x <= float(r[key])) / len(xs))
        # volume percentiles
        vxs = [(r, float(r["volume_delta_60s"])) for r in rows if r.get("volume_delta_60s") is not None]
        if vxs:
            vals = [v for _, v in vxs]
            for r, v in vxs:
                r["volume_percentile_60s"] = float(sum(1 for x in vals if x <= v) / len(vals))
        tvs = [(r, float(r["trading_value_delta_180s"])) for r in rows if r.get("trading_value_delta_180s") is not None]
        if tvs:
            vals = [v for _, v in tvs]
            for r, v in tvs:
                r["trading_value_percentile_180s"] = float(sum(1 for x in vals if x <= v) / len(vals))
        adv = sum(1 for r in rows if (r.get("return_60s") or 0) > 0)
        dec = sum(1 for r in rows if (r.get("return_60s") or 0) < 0)
        for r in rows:
            r["advancing_symbol_fraction"] = adv / len(rows)
            r["declining_symbol_fraction"] = dec / len(rows)
            r["relative_status"] = "OK"
            r["rs_universe_n"] = len(rows)
            # alias names from contract
            r["symbol_minus_median_return_60s"] = r.get("symbol_minus_median_return_60s")
            r["symbol_minus_median_return_180s"] = r.get("symbol_minus_median_return_180s")
            r["symbol_minus_median_return_300s"] = r.get("symbol_minus_median_return_300s")
        out.extend(rows)
    return out


def attach_forward_labels(rows: list[dict[str, Any]], ticks: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    """Forward returns / MFE/MAE from CurrentPrice path — DIRECTIONAL_REFERENCE_PRICE_LABEL."""
    if not ticks:
        return rows
    times = np.asarray([t["t"] for t in ticks], dtype=float)
    prices = np.asarray([t["price"] for t in ticks], dtype=float)
    from datetime import datetime
    from zoneinfo import ZoneInfo
    JST = ZoneInfo("Asia/Tokyo")
    am_end = datetime(int(day[:4]), int(day[4:6]), int(day[6:]), 11, 30, tzinfo=JST).timestamp()
    pm_end = datetime(int(day[:4]), int(day[4:6]), int(day[6:]), 15, 0, tzinfo=JST).timestamp()

    for r in rows:
        r["label_type"] = "DIRECTIONAL_REFERENCE_PRICE_LABEL"
        r["executable_pnl"] = False
        if r.get("CurrentPrice") is None:
            continue
        g = r["grid_epoch"]
        sess = r["session"]
        sess_end = am_end if sess == "AM" else pm_end
        px0 = float(r["CurrentPrice"])
        i0 = int(np.searchsorted(times, g, side="right") - 1)
        if i0 < 0:
            continue
        for h in LABEL_HORIZONS:
            tgt = g + h
            if tgt > sess_end:  # no session cross
                r[f"forward_return_{h}s"] = None
                continue
            i1 = int(np.searchsorted(times, tgt, side="right") - 1)
            if i1 <= i0:
                r[f"forward_return_{h}s"] = None
                continue
            r[f"forward_return_{h}s"] = float(prices[i1] / px0 - 1.0)
            window = prices[i0: i1 + 1]
            mfe = float(np.max(window) / px0 - 1.0)
            mae = float(np.min(window) / px0 - 1.0)
            if h in (60, 180, 300):
                r[f"MFE_{h}s"] = mfe
                r[f"MAE_{h}s"] = mae
        # first-touch style
        for up, dn, name in ((0.005, -0.005, "plus5_before_minus5"),
                             (0.01, -0.01, "plus10_before_minus10"),
                             (0.005, -0.01, "plus5_before_minus10"),
                             (0.01, -0.015, "plus10_before_minus15")):
            hit = None
            t_up = t_dn = None
            lim = int(np.searchsorted(times, min(g + 300, sess_end), side="right") - 1)
            for i in range(i0, max(i0, lim) + 1):
                ret = float(prices[i] / px0 - 1.0)
                if t_up is None and ret >= up:
                    t_up = times[i] - g
                if t_dn is None and ret <= dn:
                    t_dn = times[i] - g
                if t_up is not None and t_dn is not None:
                    break
            if t_up is not None and (t_dn is None or t_up <= t_dn):
                hit = 1.0
            elif t_dn is not None and (t_up is None or t_dn < t_up):
                hit = 0.0
            r[name] = hit
            if name == "plus5_before_minus5":
                r["time_to_plus5"] = t_up
            if name == "plus10_before_minus10":
                r["time_to_plus10"] = t_up
    return rows


def cluster_anchors(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cluster same-symbol anchors with overlapping 300s forward windows; keep FIRST."""
    by_sym: dict[str, list] = {}
    for r in rows:
        if r.get("feature_status") != "OK":
            continue
        if r.get("forward_return_60s") is None and r.get("forward_return_180s") is None:
            continue
        by_sym.setdefault(r["symbol"], []).append(r)
    clusters = []
    reps = []
    cid = 0
    for sym, rs in by_sym.items():
        rs = sorted(rs, key=lambda x: x["grid_epoch"])
        i = 0
        while i < len(rs):
            start = rs[i]
            members = [start]
            j = i + 1
            last = start["grid_epoch"]
            while j < len(rs) and rs[j]["grid_epoch"] - start["grid_epoch"] <= CLUSTER_WINDOW_SEC:
                # overlapping forward window if within 300s
                members.append(rs[j])
                last = rs[j]["grid_epoch"]
                j += 1
            cid += 1
            cluster = {
                "cluster_id": f"{start['date']}|{sym}|{cid}",
                "date": start["date"],
                "symbol": sym,
                "first_anchor_time": start["grid_time"],
                "last_anchor_time": members[-1]["grid_time"],
                "raw_anchor_n": len(members),
                "representative_anchor": "CLUSTER_FIRST_ANCHOR",
                "grid_epoch": start["grid_epoch"],
            }
            # copy features/labels from first
            for k, v in start.items():
                if k not in cluster:
                    cluster[k] = v
            clusters.append(cluster)
            reps.append(cluster)
            i = j
    return reps
