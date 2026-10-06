"""Native 1-minute HKG/CHI observations. Live bars only. BAR_START. Discovery dates only. No 5-minute grid."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add, in_lunch, interval_crosses_lunch, parse_hhmm
from research.hkg_china_a50_driver_response_v1 import ASOF_MAX_LAG_MS, FWD_HORIZONS, MIN_SECTOR_N_AT_CLOCK
from research.hkg_china_a50_driver_response_v1.features import asof_indices, attach_features

JST = ZoneInfo("Asia/Tokyo")


def tod_bucket(hhmm: str) -> str:
    p = parse_hhmm(hhmm)
    if p is None:
        return "unknown"
    m = p[0] * 60 + p[1]
    if m < 10 * 60:
        return "open"
    if m < 11 * 60 + 25:
        return "mid_am"
    if m < 14 * 60:
        return "early_pm"
    return "late_pm"


def _mins(hhmm: str) -> int | None:
    p = parse_hhmm(hhmm)
    if p is None:
        return None
    return p[0] * 60 + p[1]


def _jst_ms(day: str, hhmm: str) -> int:
    hh, mm = hhmm.split(":")
    dt = datetime(int(day[:4]), int(day[4:6]), int(day[6:8]), int(hh), int(mm), tzinfo=JST)
    return int(dt.timestamp() * 1000)


def _ret(a: float, b: float) -> float:
    if not np.isfinite(a) or not np.isfinite(b) or b == 0:
        return float("nan")
    return float((a / b - 1.0) * 10_000.0)


def _prep(sg: pd.DataFrame) -> dict[str, Any]:
    sg = sg.sort_values("time_label")
    times = [str(t)[:5] for t in sg["time_label"].tolist()]
    c = pd.to_numeric(sg["close"], errors="coerce").to_numpy(dtype=float)
    o = pd.to_numeric(sg["open"], errors="coerce").to_numpy(dtype=float)
    return {"idx": {t: i for i, t in enumerate(times)}, "c": c, "o": o, "times": times}


def _px(rec: dict[str, Any], hh: str | None, which: str = "c") -> float:
    if not hh:
        return float("nan")
    i = rec["idx"].get(hh)
    if i is None:
        return float("nan")
    x = rec[which][i]
    return float(x) if np.isfinite(x) else float("nan")


def _median(xs: list[float]) -> float:
    a = np.asarray([x for x in xs if np.isfinite(x)], dtype=float)
    if a.size == 0:
        return float("nan")
    return float(np.median(a))


def _sec_bucket() -> dict[str, Any]:
    return defaultdict(list)


def _pack(df: pd.DataFrame) -> dict[str, Any]:
    feat = attach_features(df)
    live = feat["live"].to_numpy(dtype=bool) if "live" in feat.columns else np.ones(len(feat), dtype=bool)
    return {
        "avail": feat["available_at_utc_ms"].to_numpy(dtype=np.int64),
        "ret1": feat["fx_ret_1m_bps"].to_numpy(dtype=float),
        "ret3": feat["fx_ret_3m_bps"].to_numpy(dtype=float),
        "ret5": feat["fx_ret_5m_bps"].to_numpy(dtype=float),
        "slope": feat["fx_slope_3m"].to_numpy(dtype=float),
        "rv": feat["fx_rv_5m"].to_numpy(dtype=float),
        "mid": feat["fx_mid"].to_numpy(dtype=float),
        "ts": feat["ts_utc_ms"].to_numpy(dtype=np.int64),
        "live": live,
        "jst_hhmm": feat["jst_hhmm"].astype(str).to_numpy() if "jst_hhmm" in feat.columns else np.array([""] * len(feat)),
        "jst_date": feat["jst_date"].astype(str).to_numpy() if "jst_date" in feat.columns else np.array([""] * len(feat)),
    }


def _asof_live(pack: dict[str, Any], decision_ms: int) -> int:
    i = int(asof_indices(pack["avail"], np.asarray([decision_ms], dtype=np.int64))[0])
    if i < 0:
        return -1
    lag = int(decision_ms) - int(pack["avail"][i])
    if lag < 0 or lag > int(ASOF_MAX_LAG_MS):
        return -1
    if not bool(pack["live"][i]):
        return -1
    return i


def _push(b: dict[str, Any], pack: dict[str, Any], i: int, prefix: str) -> None:
    if i < 0:
        b[f"{prefix}1"].append(float("nan"))
        b[f"{prefix}3"].append(float("nan"))
        b[f"{prefix}5"].append(float("nan"))
        b[f"{prefix}slope"].append(float("nan"))
        b[f"{prefix}rv"].append(float("nan"))
        return
    b[f"{prefix}1"].append(float(pack["ret1"][i]))
    b[f"{prefix}3"].append(float(pack["ret3"][i]))
    b[f"{prefix}5"].append(float(pack["ret5"][i]))
    b[f"{prefix}slope"].append(float(pack["slope"][i]))
    b[f"{prefix}rv"].append(float(pack["rv"][i]))


def _level(pack: dict[str, Any], i: int) -> float:
    if i < 0:
        return float("nan")
    return float(pack["mid"][i])


def _first_live_hhmm(pack: dict[str, Any], day: str) -> str | None:
    dates = pack["jst_date"]
    live = pack["live"]
    hh = pack["jst_hhmm"]
    r1 = pack["ret1"]
    idx = np.where((dates == day) & live & np.isfinite(r1))[0]
    if idx.size == 0:
        return None
    return str(hh[int(idx[0])])


def build_observations(
    *,
    minutes: pd.DataFrame,
    hkg: pd.DataFrame,
    chi: pd.DataFrame,
    fx: pd.DataFrame | None,
    es: pd.DataFrame | None,
    sector_of: dict[str, str],
    date_to_block: dict[str, str],
) -> dict[str, Any]:
    hkg_pack = _pack(hkg)
    chi_pack = _pack(chi)
    fx_pack = _pack(fx) if fx is not None and not fx.empty else None
    es_pack = _pack(es) if es is not None and not es.empty else None
    sectors = sorted({s for s in sector_of.values() if s})
    sec_obs: dict[str, dict[str, Any]] = {s: _sec_bucket() for s in sectors}
    stk_obs: dict[str, dict[str, Any]] = {}
    open_sec: dict[str, dict[str, Any]] = {s: _sec_bucket() for s in sectors}
    open_stk: dict[str, dict[str, Any]] = {}
    n_clocks = 0
    n_ok = 0
    n_skip_no_live = 0
    n_skip_0900_stale = 0
    grouped = minutes.groupby("date", sort=True)
    n_days = grouped.ngroups

    for di, (day, g) in enumerate(grouped, start=1):
        day = str(day)
        block = date_to_block.get(day) or ""
        per = {str(sym): _prep(sg) for sym, sg in g.groupby("symbol", sort=False)}
        for sym in per:
            stk_obs.setdefault(sym, _sec_bucket())
            open_stk.setdefault(sym, _sec_bucket())
        times = sorted({t for rec in per.values() for t in rec["idx"] if _mins(t) is not None})

        first_hkg = _first_live_hhmm(hkg_pack, day)
        first_chi = _first_live_hhmm(chi_pack, day)
        for driver, first in (("hkg", first_hkg), ("chi", first_chi)):
            if not first:
                continue
            if in_lunch(first) or first < "09:00" or first >= "15:00":
                continue
            d_open = hhmm_add(first, 1) or first
            pack = hkg_pack if driver == "hkg" else chi_pack
            i0 = _asof_live(pack, _jst_ms(day, d_open))
            if i0 < 0:
                continue
            by_sec: dict[str, list[str]] = defaultdict(list)
            for sym in per:
                by_sec[sector_of.get(sym) or ""].append(sym)
            stock_now = {sym: _px(rec, first) for sym, rec in per.items()}
            fwd_o: dict[int, dict[str, float]] = {h: {} for h in (1, 3, 5, 10)}
            for h in (1, 3, 5, 10):
                end = hhmm_add(d_open, h - 1)
                if not end or interval_crosses_lunch(d_open, hhmm_add(d_open, h) or end):
                    for sym in per:
                        fwd_o[h][sym] = float("nan")
                    continue
                for sym, rec in per.items():
                    fwd_o[h][sym] = _ret(_px(rec, end), stock_now[sym])
            for sec, members in by_sec.items():
                if sec not in open_sec or len(members) < MIN_SECTOR_N_AT_CLOCK:
                    continue
                b = open_sec[sec]
                b["date"].append(day)
                b["block"].append(block)
                b["driver"].append(driver)
                b["first_live_jst"].append(first)
                b["x1"].append(float(pack["ret1"][i0]))
                for h in (1, 3, 5, 10):
                    b[f"y{h}"].append(_median([fwd_o[h][s] for s in members]))
            for sym in per:
                b = open_stk[sym]
                b["date"].append(day)
                b["block"].append(block)
                b["driver"].append(driver)
                b["first_live_jst"].append(first)
                b["x1"].append(float(pack["ret1"][i0]))
                for h in (1, 3, 5, 10):
                    b[f"y{h}"].append(fwd_o[h][sym])

        for t in times:
            d = hhmm_add(t, 1)
            if d is None or in_lunch(t) or in_lunch(d):
                continue
            last = t
            n_clocks += 1
            if last == "09:00" or d == "09:00":
                # Japan 09:00 is allowed only if a live HKG/CHI bar is already available.
                i_hkg_pre = _asof_live(hkg_pack, _jst_ms(day, "09:00"))
                i_chi_pre = _asof_live(chi_pack, _jst_ms(day, "09:00"))
                if i_hkg_pre < 0 and i_chi_pre < 0:
                    n_skip_0900_stale += 1
                    continue
            stock_now = {sym: _px(rec, last) for sym, rec in per.items()}
            stock_bar = {sym: _ret(_px(rec, last, "c"), _px(rec, last, "o")) for sym, rec in per.items()}
            fwd: dict[int, dict[str, float]] = {h: {} for h in FWD_HORIZONS}
            for h in FWD_HORIZONS:
                end = hhmm_add(d, h - 1)
                if not end or interval_crosses_lunch(d, hhmm_add(d, h) or end):
                    for sym in per:
                        fwd[h][sym] = float("nan")
                    continue
                for sym, rec in per.items():
                    fwd[h][sym] = _ret(_px(rec, end), stock_now[sym])
            by_sec = defaultdict(list)
            for sym in per:
                by_sec[sector_of.get(sym) or ""].append(sym)
            mkt_fwd = {h: _median(list(fwd[h].values())) for h in FWD_HORIZONS}
            i_hkg = _asof_live(hkg_pack, _jst_ms(day, d))
            i_chi = _asof_live(chi_pack, _jst_ms(day, d))
            i_hkg_next = _asof_live(hkg_pack, _jst_ms(day, hhmm_add(d, 1) or d))
            i_chi_next = _asof_live(chi_pack, _jst_ms(day, hhmm_add(d, 1) or d))
            i_fx = _asof_live(fx_pack, _jst_ms(day, d)) if fx_pack is not None else -1
            i_es = _asof_live(es_pack, _jst_ms(day, d)) if es_pack is not None else -1
            if i_hkg < 0 and i_chi < 0:
                n_skip_no_live += 1
                continue
            used = False
            for sec, members in by_sec.items():
                if sec not in sec_obs or len(members) < MIN_SECTOR_N_AT_CLOCK:
                    continue
                b = sec_obs[sec]
                raw_bar = _median([stock_bar[s] for s in members])
                ex_bar = _median([stock_bar[s] for s in per if sector_of.get(s) != sec])
                _push(b, hkg_pack, i_hkg, "hkg")
                _push(b, chi_pack, i_chi, "chi")
                if fx_pack is not None:
                    _push(b, fx_pack, i_fx, "fx")
                if es_pack is not None:
                    _push(b, es_pack, i_es, "es")
                b["date"].append(day)
                b["block"].append(block)
                b["tod"].append(tod_bucket(d))
                b["clock"].append(d)
                b["sim_raw"].append(raw_bar)
                b["sim_resid"].append(raw_bar - ex_bar if np.isfinite(raw_bar) and np.isfinite(ex_bar) else float("nan"))
                b["rev_hkg"].append(float(hkg_pack["ret1"][i_hkg_next]) if i_hkg_next >= 0 else float("nan"))
                b["rev_chi"].append(float(chi_pack["ret1"][i_chi_next]) if i_chi_next >= 0 else float("nan"))
                b["n"].append(len(members))
                for h in FWD_HORIZONS:
                    raw = _median([fwd[h][s] for s in members])
                    ex = _median([fwd[h][s] for s in per if sector_of.get(s) != sec])
                    b[f"y{h}"].append(raw)
                    b[f"yr{h}"].append(raw - ex if np.isfinite(raw) and np.isfinite(ex) else float("nan"))
                used = True
            if not used:
                continue
            n_ok += 1
            for sym, rec in per.items():
                sec = sector_of.get(sym) or ""
                members = by_sec.get(sec) or []
                b = stk_obs[sym]
                _push(b, hkg_pack, i_hkg, "hkg")
                _push(b, chi_pack, i_chi, "chi")
                if fx_pack is not None:
                    _push(b, fx_pack, i_fx, "fx")
                if es_pack is not None:
                    _push(b, es_pack, i_es, "es")
                b["date"].append(day)
                b["block"].append(block)
                b["tod"].append(tod_bucket(d))
                b["clock"].append(d)
                b["sim_raw"].append(stock_bar[sym])
                b["rev_hkg"].append(float(hkg_pack["ret1"][i_hkg_next]) if i_hkg_next >= 0 else float("nan"))
                b["rev_chi"].append(float(chi_pack["ret1"][i_chi_next]) if i_chi_next >= 0 else float("nan"))
                for h in FWD_HORIZONS:
                    y = fwd[h][sym]
                    sec_y = _median([fwd[h][s] for s in members]) if members else float("nan")
                    b[f"y{h}"].append(y)
                    b[f"ym{h}"].append(y - mkt_fwd[h] if np.isfinite(y) and np.isfinite(mkt_fwd[h]) else float("nan"))
                    b[f"ys{h}"].append(y - sec_y if np.isfinite(y) and np.isfinite(sec_y) else float("nan"))
        if di % 20 == 0 or di == n_days:
            print(f"OBS {di}/{n_days} clocks={n_clocks} ok={n_ok} no_live={n_skip_no_live} skip0900={n_skip_0900_stale}", flush=True)
    return {
        "sector": sec_obs,
        "stock": stk_obs,
        "foreign_open_sector": open_sec,
        "foreign_open_stock": open_stk,
        "n_clocks": n_clocks,
        "n_ok": n_ok,
        "n_skip_no_live": n_skip_no_live,
        "n_skip_0900_stale": n_skip_0900_stale,
        "n_days": n_days,
        "resolution_min": 1,
        "used_five_minute_grid": False,
        "hkg_chi_not_combined_score": True,
        "asof_max_lag_ms": ASOF_MAX_LAG_MS,
        "driver_feature_ids": ("hkg_ret_1m", "chi_ret_1m", "hkg_ret_3m", "chi_ret_3m", "hkg_ret_5m", "chi_ret_5m", "slope_3m", "rv_5m"),
        "did_not_forward_fill_closed_market": True,
        "did_not_test_0900_without_live_driver": True,
    }
