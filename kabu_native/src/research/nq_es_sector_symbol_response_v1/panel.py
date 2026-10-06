"""Native 1-minute NQ/ES observations. BAR_START. Discovery dates only. Do not use 5-minute grid_clocks."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add, in_lunch, interval_crosses_lunch, parse_hhmm
from research.nq_es_sector_symbol_response_v1 import ASOF_MAX_LAG_MS, FWD_HORIZONS, MIN_SECTOR_N_AT_CLOCK
from research.nq_es_sector_symbol_response_v1.features import asof_indices, attach_features

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
    return {
        "avail": feat["available_at_utc_ms"].to_numpy(dtype=np.int64),
        "ret1": feat["fx_ret_1m_bps"].to_numpy(dtype=float),
        "ret3": feat["fx_ret_3m_bps"].to_numpy(dtype=float),
        "ret5": feat["fx_ret_5m_bps"].to_numpy(dtype=float),
        "mid": feat["fx_mid"].to_numpy(dtype=float),
        "ts": feat["ts_utc_ms"].to_numpy(dtype=np.int64),
    }


def _asof_strict(avail: np.ndarray, decision_ms: int) -> int:
    i = int(asof_indices(avail, np.asarray([decision_ms], dtype=np.int64))[0])
    if i < 0:
        return -1
    lag = int(decision_ms) - int(avail[i])
    if lag < 0 or lag > int(ASOF_MAX_LAG_MS):
        return -1
    return i


def _push(b: dict[str, Any], pack: dict[str, Any], i: int, prefix: str) -> None:
    if i < 0:
        b[f"{prefix}1"].append(float("nan"))
        b[f"{prefix}3"].append(float("nan"))
        b[f"{prefix}5"].append(float("nan"))
        return
    b[f"{prefix}1"].append(float(pack["ret1"][i]))
    b[f"{prefix}3"].append(float(pack["ret3"][i]))
    b[f"{prefix}5"].append(float(pack["ret5"][i]))


def _level(pack: dict[str, Any], i: int) -> float:
    if i < 0:
        return float("nan")
    return float(pack["mid"][i])


def build_observations(
    *,
    minutes: pd.DataFrame,
    nq: pd.DataFrame,
    es: pd.DataFrame,
    fx: pd.DataFrame | None,
    sector_of: dict[str, str],
    date_to_block: dict[str, str],
) -> dict[str, Any]:
    nq_pack = _pack(nq)
    es_pack = _pack(es)
    fx_pack = _pack(fx) if fx is not None and not fx.empty else None
    sectors = sorted({s for s in sector_of.values() if s})
    sec_obs: dict[str, dict[str, Any]] = {s: _sec_bucket() for s in sectors}
    stk_obs: dict[str, dict[str, Any]] = {}
    n_clocks = 0
    n_ok = 0
    n_stale = 0
    grouped = minutes.groupby("date", sort=True)
    n_days = grouped.ngroups
    disc_days = [str(d) for d, _g in grouped]
    prev_of = {disc_days[i]: disc_days[i - 1] for i in range(1, len(disc_days))}
    preopen: dict[str, dict[str, Any]] = {s: _sec_bucket() for s in sectors}
    preopen_stk: dict[str, dict[str, Any]] = {}

    for di, (day, g) in enumerate(grouped, start=1):
        day = str(day)
        block = date_to_block.get(day) or ""
        per = {str(sym): _prep(sg) for sym, sg in g.groupby("symbol", sort=False)}
        for sym in per:
            stk_obs.setdefault(sym, _sec_bucket())
            preopen_stk.setdefault(sym, _sec_bucket())
        times = sorted({t for rec in per.values() for t in rec["idx"] if _mins(t) is not None})
        prev = prev_of.get(day)
        if prev:
            i_nq_prev = _asof_strict(nq_pack["avail"], _jst_ms(prev, "15:30"))
            i_nq_pre = _asof_strict(nq_pack["avail"], _jst_ms(day, "08:59"))
            i_es_prev = _asof_strict(es_pack["avail"], _jst_ms(prev, "15:30"))
            i_es_pre = _asof_strict(es_pack["avail"], _jst_ms(day, "08:59"))
            nq_ov = _ret(_level(nq_pack, i_nq_pre), _level(nq_pack, i_nq_prev))
            es_ov = _ret(_level(es_pack, i_es_pre), _level(es_pack, i_es_prev))
            fx_ov = float("nan")
            if fx_pack is not None:
                i_fx_prev = _asof_strict(fx_pack["avail"], _jst_ms(prev, "15:30"))
                i_fx_pre = _asof_strict(fx_pack["avail"], _jst_ms(day, "08:59"))
                fx_ov = _ret(_level(fx_pack, i_fx_pre), _level(fx_pack, i_fx_prev))
            by_sec: dict[str, list[str]] = defaultdict(list)
            for sym in per:
                by_sec[sector_of.get(sym) or ""].append(sym)
            open_raw: dict[str, float] = {}
            m5: dict[str, float] = {}
            m15: dict[str, float] = {}
            m30: dict[str, float] = {}
            for sym, rec in per.items():
                o0 = _px(rec, "09:00", "o")
                c0 = _px(rec, "09:00", "c")
                c5 = _px(rec, "09:04", "c")
                c15 = _px(rec, "09:14", "c")
                c30 = _px(rec, "09:29", "c")
                open_raw[sym] = _ret(c0, o0)
                m5[sym] = _ret(c5, o0)
                m15[sym] = _ret(c15, o0)
                m30[sym] = _ret(c30, o0)
            mkt15 = _median(list(m15.values()))
            for sec, members in by_sec.items():
                if sec not in preopen or len(members) < MIN_SECTOR_N_AT_CLOCK:
                    continue
                raw15 = _median([m15[s] for s in members])
                ex = _median([m15[s] for s in per if sector_of.get(s) != sec])
                b = preopen[sec]
                b["date"].append(day)
                b["block"].append(block)
                b["nq_ov"].append(nq_ov)
                b["es_ov"].append(es_ov)
                b["fx_ov"].append(fx_ov)
                b["y_open"].append(_median([open_raw[s] for s in members]))
                b["y_5"].append(_median([m5[s] for s in members]))
                b["y_15"].append(raw15)
                b["y_30"].append(_median([m30[s] for s in members]))
                b["y_15_resid"].append(raw15 - ex if np.isfinite(raw15) and np.isfinite(ex) else float("nan"))
                b["y_open_resid"].append(
                    _median([open_raw[s] for s in members])
                    - _median([open_raw[s] for s in per if sector_of.get(s) != sec])
                )
            for sym, rec in per.items():
                b = preopen_stk[sym]
                sec = sector_of.get(sym) or ""
                sec15 = _median([m15[s] for s in by_sec.get(sec, [])])
                b["date"].append(day)
                b["block"].append(block)
                b["nq_ov"].append(nq_ov)
                b["es_ov"].append(es_ov)
                b["fx_ov"].append(fx_ov)
                b["y_open"].append(open_raw[sym])
                b["y_5"].append(m5[sym])
                b["y_15"].append(m15[sym])
                b["y_30"].append(m30[sym])
                b["y_15_mkt"].append(m15[sym] - mkt15 if np.isfinite(m15[sym]) and np.isfinite(mkt15) else float("nan"))
                b["y_15_sec"].append(m15[sym] - sec15 if np.isfinite(m15[sym]) and np.isfinite(sec15) else float("nan"))

        for t in times:
            d = hhmm_add(t, 1)
            if d is None or in_lunch(t) or in_lunch(d):
                continue
            last = t
            n_clocks += 1
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
            i_nq = _asof_strict(nq_pack["avail"], _jst_ms(day, d))
            i_es = _asof_strict(es_pack["avail"], _jst_ms(day, d))
            i_nq_next = _asof_strict(nq_pack["avail"], _jst_ms(day, hhmm_add(d, 1) or d))
            i_es_next = _asof_strict(es_pack["avail"], _jst_ms(day, hhmm_add(d, 1) or d))
            if i_nq < 0 and i_es < 0:
                n_stale += 1
                continue
            used = False
            for sec, members in by_sec.items():
                if sec not in sec_obs or len(members) < MIN_SECTOR_N_AT_CLOCK:
                    continue
                b = sec_obs[sec]
                raw_bar = _median([stock_bar[s] for s in members])
                ex_bar = _median([stock_bar[s] for s in per if sector_of.get(s) != sec])
                _push(b, nq_pack, i_nq, "nq")
                _push(b, es_pack, i_es, "es")
                b["date"].append(day)
                b["block"].append(block)
                b["tod"].append(tod_bucket(d))
                b["sim_raw"].append(raw_bar)
                b["sim_resid"].append(raw_bar - ex_bar if np.isfinite(raw_bar) and np.isfinite(ex_bar) else float("nan"))
                b["rev_nq"].append(float(nq_pack["ret1"][i_nq_next]) if i_nq_next >= 0 else float("nan"))
                b["rev_es"].append(float(es_pack["ret1"][i_es_next]) if i_es_next >= 0 else float("nan"))
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
                _push(b, nq_pack, i_nq, "nq")
                _push(b, es_pack, i_es, "es")
                b["date"].append(day)
                b["block"].append(block)
                b["tod"].append(tod_bucket(d))
                b["sim_raw"].append(stock_bar[sym])
                b["rev_nq"].append(float(nq_pack["ret1"][i_nq_next]) if i_nq_next >= 0 else float("nan"))
                b["rev_es"].append(float(es_pack["ret1"][i_es_next]) if i_es_next >= 0 else float("nan"))
                for h in FWD_HORIZONS:
                    y = fwd[h][sym]
                    sec_y = _median([fwd[h][s] for s in members]) if members else float("nan")
                    b[f"y{h}"].append(y)
                    b[f"ym{h}"].append(y - mkt_fwd[h] if np.isfinite(y) and np.isfinite(mkt_fwd[h]) else float("nan"))
                    b[f"ys{h}"].append(y - sec_y if np.isfinite(y) and np.isfinite(sec_y) else float("nan"))
        if di % 20 == 0 or di == n_days:
            print(f"OBS {di}/{n_days} clocks={n_clocks} ok={n_ok} stale_skip={n_stale}", flush=True)
    return {
        "sector": sec_obs,
        "stock": stk_obs,
        "preopen_sector": preopen,
        "preopen_stock": preopen_stk,
        "n_clocks": n_clocks,
        "n_ok": n_ok,
        "n_stale_skip": n_stale,
        "n_days": n_days,
        "resolution_min": 1,
        "used_five_minute_grid": False,
        "nq_es_not_combined_score": True,
        "asof_max_lag_ms": ASOF_MAX_LAG_MS,
        "driver_feature_ids": ("nq_ret_1m", "es_ret_1m", "nq_ret_3m", "es_ret_3m", "nq_ret_5m", "es_ret_5m"),
    }
