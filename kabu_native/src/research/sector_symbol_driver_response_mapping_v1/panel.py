"""Causal 5-minute grid snapshots. BAR_START. Labels only on forward returns. Discovery dates only."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add, interval_crosses_lunch, parse_hhmm
from research.identify_minimum_missing_external_causal_information_v1.features import _dt, _index_proxy, features_at_T
from research.causal_path_to_complete_strategy_v1.events import grid_clocks
from research.sector_symbol_driver_response_mapping_v1 import FWD_HORIZONS, MIN_SECTOR_N_AT_CLOCK, PRIMARY_HORIZON_MIN

DRIVER_IDS = ("ETF_NK_1321", "ETF_TOPIX_1306", "MKT_MEDIAN_RET_1M", "MKT_BREADTH", "MKT_DISPERSION", "MKT_LEADERSHIP")


def _mins(hhmm: str) -> int | None:
    p = parse_hhmm(hhmm)
    if p is None:
        return None
    return p[0] * 60 + p[1]


def tod_bucket(hhmm: str) -> str:
    m = _mins(hhmm)
    if m is None:
        return "unknown"
    if m < 10 * 60:
        return "open"
    if m < 11 * 60 + 25:
        return "mid_am"
    if m < 14 * 60:
        return "early_pm"
    return "late_pm"


def symbol_history(minutes: pd.DataFrame) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for sym, g in minutes.groupby("symbol", sort=False):
        days = sorted({str(d) for d in g["date"].tolist()})
        out[str(sym)] = {"first": days[0] if days else None, "last": days[-1] if days else None, "n_days": len(days)}
    return out


def _prep(sg: pd.DataFrame) -> dict[str, Any]:
    sg = sg.sort_values("time_label")
    times = [str(t)[:5] for t in sg["time_label"].tolist()]
    c = pd.to_numeric(sg["close"], errors="coerce").to_numpy(dtype=float)
    return {"idx": {t: i for i, t in enumerate(times)}, "c": c}


def _close_at(rec: dict[str, Any], hh: str | None) -> float:
    if not hh:
        return float("nan")
    i = rec["idx"].get(hh)
    if i is None:
        return float("nan")
    x = rec["c"][i]
    return float(x) if np.isfinite(x) else float("nan")


def _ret(a: float, b: float) -> float:
    if not np.isfinite(a) or not np.isfinite(b) or b == 0:
        return float("nan")
    return float((a / b - 1.0) * 10_000.0)


def _bucket() -> dict[str, Any]:
    return {
        "d": {did: [] for did in DRIVER_IDS},
        "y": {h: [] for h in FWD_HORIZONS},
        "ym": {h: [] for h in FWD_HORIZONS},
        "sec_ret": [],
        "block": [],
        "tod": [],
        "vol": [],
        "date": [],
        "n": [],
    }


def _stock_bucket() -> dict[str, Any]:
    return {
        "d": {did: [] for did in DRIVER_IDS},
        "y5": [],
        "ym5": [],
        "ys5": [],
        "block": [],
        "tod": [],
        "vol": [],
        "date": [],
    }


def build_observations(
    *,
    minutes: pd.DataFrame,
    sector_of: dict[str, str],
    date_to_block: dict[str, str],
    nk_df: pd.DataFrame,
    tx_df: pd.DataFrame,
) -> dict[str, Any]:
    clocks = grid_clocks()
    nk_by = _index_proxy(nk_df)
    tx_by = _index_proxy(tx_df)
    sectors = sorted({s for s in sector_of.values() if s})
    sec_obs: dict[str, dict[str, Any]] = {s: _bucket() for s in sectors}
    stk_obs: dict[str, dict[str, Any]] = {}
    n_clocks = 0
    n_ok = 0
    grouped = minutes.groupby("date", sort=True)
    n_days = grouped.ngroups
    for di, (day, g) in enumerate(grouped, start=1):
        day = str(day)
        block = date_to_block.get(day) or ""
        per = {str(sym): _prep(sg) for sym, sg in g.groupby("symbol", sort=False)}
        for sym in per:
            stk_obs.setdefault(sym, _stock_bucket())
        nk_bars = nk_by.get(day, [])
        tx_bars = tx_by.get(day, [])
        abs_mkt_run: list[float] = []
        for clock in clocks:
            n_clocks += 1
            last_t = hhmm_add(clock, -1)
            if last_t is None or interval_crosses_lunch(last_t, clock):
                continue
            prev_t = hhmm_add(last_t, -1)
            decision = _dt(day, clock)
            nk_f = features_at_T(bars=nk_bars, decision=decision) if decision is not None else {}
            tx_f = features_at_T(bars=tx_bars, decision=decision) if decision is not None else {}
            stock_ret: dict[str, float] = {}
            stock_fwd: dict[str, dict[int, float]] = {}
            for sym, rec in per.items():
                cl = _close_at(rec, last_t)
                pv = _close_at(rec, prev_t) if prev_t else float("nan")
                stock_ret[sym] = _ret(cl, pv)
                fw = {}
                for h in FWD_HORIZONS:
                    end = hhmm_add(last_t, h)
                    if not end or interval_crosses_lunch(last_t, end):
                        fw[h] = float("nan")
                    else:
                        fw[h] = _ret(_close_at(rec, end), cl)
                stock_fwd[sym] = fw
            rets = np.asarray([v for v in stock_ret.values() if np.isfinite(v)], dtype=float)
            if rets.size < 20:
                continue
            n_ok += 1
            mkt_med = float(np.median(rets))
            breadth = float(np.mean(rets > 0))
            disp = float(np.std(rets)) if rets.size > 1 else float("nan")
            w = np.abs(rets)
            ssum = float(np.sum(w))
            hhi = float(np.sum((w / ssum) ** 2)) if ssum > 0 else float("nan")
            abs_mkt_run.append(abs(mkt_med))
            vol_raw = float(np.mean(abs_mkt_run[-6:])) if len(abs_mkt_run) >= 6 else float("nan")
            tod = tod_bucket(clock)
            drivers_now = {
                "ETF_NK_1321": float(nk_f["ret_60s_bps"]) if nk_f.get("ret_60s_bps") is not None else float("nan"),
                "ETF_TOPIX_1306": float(tx_f["ret_60s_bps"]) if tx_f.get("ret_60s_bps") is not None else float("nan"),
                "MKT_MEDIAN_RET_1M": mkt_med,
                "MKT_BREADTH": breadth * 10_000.0,
                "MKT_DISPERSION": disp,
                "MKT_LEADERSHIP": hhi,
            }
            by_sec: dict[str, list[str]] = defaultdict(list)
            for sym in per:
                by_sec[sector_of.get(sym) or ""].append(sym)
            sec_pack: dict[str, dict[str, Any]] = {}
            for sec, members in by_sec.items():
                if not sec or len(members) < MIN_SECTOR_N_AT_CLOCK:
                    continue
                rsec = np.asarray([stock_ret[m] for m in members if np.isfinite(stock_ret[m])], dtype=float)
                if rsec.size < MIN_SECTOR_N_AT_CLOCK:
                    continue
                ex = np.asarray([stock_ret[m] for m in per if sector_of.get(m) != sec and np.isfinite(stock_ret[m])], dtype=float)
                fwd_s = {}
                fwd_ex = {}
                for h in FWD_HORIZONS:
                    a = np.asarray([stock_fwd[m][h] for m in members if np.isfinite(stock_fwd[m][h])], dtype=float)
                    b = np.asarray([stock_fwd[m][h] for m in per if sector_of.get(m) != sec and np.isfinite(stock_fwd[m][h])], dtype=float)
                    fwd_s[h] = float(np.mean(a)) if a.size else float("nan")
                    fwd_ex[h] = float(np.median(b)) if b.size else float("nan")
                pack = {
                    "n": int(rsec.size),
                    "ret_1m": float(np.mean(rsec)),
                    "fwd": fwd_s,
                    "fwd_ex": fwd_ex,
                }
                sec_pack[sec] = pack
                bkt = sec_obs[sec]
                for did in DRIVER_IDS:
                    bkt["d"][did].append(drivers_now[did])
                for h in FWD_HORIZONS:
                    bkt["y"][h].append(fwd_s[h])
                    bkt["ym"][h].append(fwd_ex[h])
                bkt["sec_ret"].append(pack["ret_1m"])
                bkt["block"].append(block)
                bkt["tod"].append(tod)
                bkt["vol"].append(vol_raw)
                bkt["date"].append(day)
                bkt["n"].append(pack["n"])
            for sym in per:
                sec = sector_of.get(sym) or ""
                sn = sec_pack.get(sec)
                if not sn or not np.isfinite(stock_ret.get(sym, float("nan"))):
                    continue
                bkt = stk_obs[sym]
                for did in DRIVER_IDS:
                    bkt["d"][did].append(drivers_now[did])
                bkt["y5"].append(stock_fwd[sym][PRIMARY_HORIZON_MIN])
                bkt["ym5"].append(sn["fwd_ex"][PRIMARY_HORIZON_MIN])
                bkt["ys5"].append(sn["fwd"][PRIMARY_HORIZON_MIN])
                bkt["block"].append(block)
                bkt["tod"].append(tod)
                bkt["vol"].append(vol_raw)
                bkt["date"].append(day)
        if di % 25 == 0:
            print(f"GRID {di}/{n_days} ok_clocks={n_ok}", flush=True)
    return {
        "clocks": clocks,
        "n_days": n_days,
        "n_clocks_scanned": n_clocks,
        "n_clocks_ok": n_ok,
        "sectors": sectors,
        "driver_ids": list(DRIVER_IDS),
        "sec_obs": sec_obs,
        "stk_obs": stk_obs,
        "primary_horizon_min": PRIMARY_HORIZON_MIN,
        "same_bar_close_used_as_driver": False,
        "forward_returns_are_labels_only": True,
    }
