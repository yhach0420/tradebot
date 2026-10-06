"""Causal onset event universe. Identities freeze before outcomes. Discovery dates only at design time."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add, in_lunch, parse_hhmm
from research.cause_first_mechanism_discovery_v1.panel import (
    _finite_median,
    _finite_std,
    _num,
    _prior_max,
    _prior_mean,
    _up_ratio,
    in_invalid_entry,
)
from research.causal_path_to_complete_strategy_v1 import GRID_STEP_MIN
from research.fixed_universe_historical_foundation_v1.technical import ema, session_vwap

FAMILIES = (
    "BREADTH_UP",
    "BREADTH_DOWN",
    "BREADTH_ACCEL",
    "BREADTH_DECEL",
    "DISP_EXPAND",
    "DISP_CONTRACT",
    "LEADERSHIP_EXPAND",
    "LEADERSHIP_COLLAPSE",
    "SECTOR_RS_IMPROVE",
    "SECTOR_RS_DETERIORATE",
    "SECTOR_BREADTH_UP",
    "SECTOR_BREADTH_DOWN",
    "VWAP_RECLAIM",
    "VWAP_LOSS",
    "EMA_IMPROVE",
    "EMA_DETERIORATE",
    "BREAKOUT20",
    "PULLBACK_START",
    "RS_IMPROVE",
    "RS_DETERIORATE",
    "RELVOL_EXPAND",
    "VA_EXPAND",
    "RANGE_EXPAND",
    "COMPRESSION_RELEASE",
)

# Precommitted onset cuts. Not M1 0.60/0.40 level gates. Not Confirmation-tuned.
BREADTH_UP_FROM = 0.50
BREADTH_UP_TO = 0.55
BREADTH_DOWN_FROM = 0.50
BREADTH_DOWN_TO = 0.45
RELVOL_CROSS = 1.50
VA_CROSS = 1.50
RANGE_CROSS = 1.50
COMPRESSION = 0.60
RELEASE = 1.00
LEAD_DHHI = 0.02
DISP_EXPAND_MULT = 1.25
DISP_CONTRACT_MULT = 0.80
MIN_DISP = 0.002
ACCEL_CUT = 0.05


def grid_clocks(*, step: int = GRID_STEP_MIN) -> list[str]:
    out: list[str] = []
    for m in range(9 * 60 + 25, 11 * 60 + 25, int(step)):
        out.append(f"{m // 60:02d}:{m % 60:02d}")
    for m in range(12 * 60 + 40, 15 * 60 + 5, int(step)):
        out.append(f"{m // 60:02d}:{m % 60:02d}")
    return out


def _mins(hhmm: str) -> int | None:
    p = parse_hhmm(hhmm)
    if p is None:
        return None
    return p[0] * 60 + p[1]


def _prep_symbol(sg: pd.DataFrame) -> dict[str, Any]:
    sg = sg.sort_values("time_label")
    times = [str(t)[:5] for t in sg["time_label"].tolist()]
    o = _num(sg["open"])
    h = _num(sg["high"])
    l = _num(sg["low"])
    c = _num(sg["close"])
    v = _num(sg["volume"])
    va = _num(sg["trading_value"])
    return {
        "t": times,
        "idx": {t: i for i, t in enumerate(times)},
        "o": o,
        "h": h,
        "l": l,
        "c": c,
        "v": v,
        "va": va,
        "e9": ema(c, 9),
        "e21": ema(c, 21),
        "vw": session_vwap(h, l, c, v, va),
        "rng": np.where(np.isfinite(c) & (c != 0), (h - l) / np.abs(c), np.nan),
    }


def _ret(rec: dict[str, Any], i: int, look: str | None) -> float:
    if not look:
        return float("nan")
    j = rec["idx"].get(look)
    if j is None:
        return float("nan")
    a, b = rec["c"][i], rec["c"][j]
    if not np.isfinite(a) or not np.isfinite(b) or b == 0:
        return float("nan")
    return float(a / b - 1.0)


def _cross_up(prev: float, now: float, lo: float, hi: float) -> bool:
    return np.isfinite(prev) and np.isfinite(now) and prev < lo and now >= hi


def _cross_down(prev: float, now: float, hi: float, lo: float) -> bool:
    return np.isfinite(prev) and np.isfinite(now) and prev > hi and now <= lo


def _bool_onset(prev: bool, now: bool) -> bool:
    return bool(now) and not bool(prev)


def scan_day(*, date: str, g: pd.DataFrame, sector_of: dict[str, str], clocks: list[str]) -> list[dict[str, Any]]:
    per: dict[str, dict[str, Any]] = {}
    for sym, sg in g.groupby("symbol", sort=False):
        per[str(sym)] = _prep_symbol(sg)

    snapshots: list[dict[str, Any]] = []
    for clock in clocks:
        entry_t = hhmm_add(clock, 1)
        if entry_t is None or in_invalid_entry(entry_t) or in_lunch(clock):
            snapshots.append({"clock": clock, "ok": False})
            continue
        look5 = hhmm_add(clock, -5)
        look15 = hhmm_add(clock, -15)
        rows = []
        rets5: list[float] = []
        vols: list[float] = []
        vas: list[float] = []
        ema_flags: list[float] = []
        for sym, rec in per.items():
            i = rec["idx"].get(clock)
            if i is None:
                continue
            c0 = rec["c"][i]
            if not np.isfinite(c0) or c0 == 0:
                continue
            r5 = _ret(rec, i, look5)
            r15 = _ret(rec, i, look15)
            vw = rec["vw"][i]
            e9v, e21v = rec["e9"][i], rec["e21"][i]
            above_vwap = bool(np.isfinite(vw) and c0 > vw)
            ema_ok = bool(np.isfinite(e9v) and np.isfinite(e21v) and e9v > e21v)
            hi20 = _prior_max(rec["h"], i, 20)
            brk = bool(np.isfinite(hi20) and rec["c"][i] >= hi20)
            vm = _prior_mean(rec["v"], i, 20)
            vam = _prior_mean(rec["va"], i, 20)
            rm = _prior_mean(rec["rng"], i, 20)
            vol_rel = float(rec["v"][i] / vm) if np.isfinite(vm) and vm > 0 and np.isfinite(rec["v"][i]) else float("nan")
            va_rel = float(rec["va"][i] / vam) if np.isfinite(vam) and vam > 0 and np.isfinite(rec["va"][i]) else float("nan")
            rng_rel = float(rec["rng"][i] / rm) if np.isfinite(rm) and rm > 0 and np.isfinite(rec["rng"][i]) else float("nan")
            vwap_dist = float((c0 - vw) / c0) if np.isfinite(vw) else float("nan")
            ema_dist = float((e9v - e21v) / c0) if np.isfinite(e9v) and np.isfinite(e21v) else float("nan")
            dist_hi = float((c0 - hi20) / c0) if np.isfinite(hi20) and hi20 != 0 else float("nan")
            rows.append(
                {
                    "symbol": sym,
                    "sector": sector_of.get(sym) or "",
                    "i": i,
                    "ret_5m": r5,
                    "ret_15m": r15,
                    "above_vwap": above_vwap,
                    "ema9_gt_ema21": ema_ok,
                    "breakout20": brk,
                    "hi20": hi20 if np.isfinite(hi20) else None,
                    "vol_rel20": vol_rel,
                    "va_rel20": va_rel,
                    "rng_rel20": rng_rel,
                    "vwap_dist": vwap_dist,
                    "ema_dist": ema_dist,
                    "dist_20high": dist_hi,
                    "close": float(c0),
                }
            )
            rets5.append(r5)
            vols.append(rec["v"][i])
            vas.append(rec["va"][i])
            if np.isfinite(e9v) and np.isfinite(e21v):
                ema_flags.append(1.0 if ema_ok else 0.0)
        if len(rows) < 20:
            snapshots.append({"clock": clock, "ok": False})
            continue
        up5 = _up_ratio(rets5)
        med5 = _finite_median(rets5)
        disp5 = _finite_std(rets5)
        pos = np.array([x for x in rets5 if np.isfinite(x) and x > 0], dtype=float)
        lead_hhi = float(np.sum((pos / pos.sum()) ** 2)) if pos.size and pos.sum() > 0 else float("nan")
        vol_active = float(np.mean([1.0 if np.isfinite(x) and x > 0 else 0.0 for x in vols])) if vols else float("nan")
        sector_rets: dict[str, list[float]] = defaultdict(list)
        for r, r5 in zip(rows, rets5):
            if np.isfinite(r5):
                sector_rets[r["sector"]].append(r5)
        sector_up = {k: _up_ratio(vs) for k, vs in sector_rets.items()}
        by_sym = {}
        for r, r5 in zip(rows, rets5):
            r["rs_5m"] = (float(r5) - float(med5)) if np.isfinite(r5) and med5 is not None else float("nan")
            r["sector_up_5m"] = sector_up.get(r["sector"])
            r["sector_n"] = len(sector_rets.get(r["sector"]) or [])
            r["sector_rs"] = (
                float(r["sector_up_5m"]) - float(up5) if r["sector_up_5m"] is not None and up5 is not None else float("nan")
            )
            by_sym[r["symbol"]] = r
        snapshots.append(
            {
                "clock": clock,
                "ok": True,
                "entry_t": entry_t,
                "up_5m": up5,
                "median_ret_5m": med5,
                "dispersion_5m": disp5,
                "leadership_hhi": lead_hhi,
                "volume_active_ratio": vol_active,
                "ema_breadth": float(np.mean(ema_flags)) if ema_flags else float("nan"),
                "active_n": len(rows),
                "sector_up": sector_up,
                "by_sym": by_sym,
                "per": None,
            }
        )

    events: list[dict[str, Any]] = []
    prev_ok = None
    prev2_ok = None
    for snap in snapshots:
        clock = snap["clock"]
        if not snap.get("ok"):
            prev2_ok, prev_ok = prev_ok, None
            continue
        if prev_ok is None:
            prev2_ok, prev_ok = prev_ok, snap
            continue
        entry_t = snap["entry_t"]
        up, pup = snap["up_5m"], prev_ok["up_5m"]
        up2 = prev2_ok["up_5m"] if prev2_ok else None
        slope = (float(up) - float(pup)) if up is not None and pup is not None else float("nan")
        prev_slope = (float(pup) - float(up2)) if pup is not None and up2 is not None else float("nan")
        accel = (float(slope) - float(prev_slope)) if np.isfinite(slope) and np.isfinite(prev_slope) else float("nan")
        disp, pdisp = snap["dispersion_5m"], prev_ok["dispersion_5m"]
        hhi, phhi = snap["leadership_hhi"], prev_ok["leadership_hhi"]
        market_base = {
            "date": date,
            "feature_bar": clock,
            "event_time": entry_t,
            "feature_available_at": entry_t,
            "entry_bar": entry_t,
            "same_bar_close_entry": False,
            "execution_x0": "next_bar_open_after_feature_bar_end",
            "active_n": snap["active_n"],
            "breadth_level": up,
            "breadth_slope": slope,
            "breadth_accel": accel,
            "median_ret_5m": snap["median_ret_5m"],
            "dispersion_5m": disp,
            "dispersion_change": (float(disp) - float(pdisp)) if disp is not None and pdisp is not None else float("nan"),
            "leadership_hhi": hhi,
            "leadership_change": (float(hhi) - float(phhi)) if np.isfinite(hhi) and np.isfinite(phhi) else float("nan"),
            "volume_active_ratio": snap["volume_active_ratio"],
            "ema_breadth": snap["ema_breadth"],
            "future_used_as_decision_feature": False,
        }

        def emit(family: str, symbol: str, extra: dict[str, Any]) -> None:
            eid = f"{date}|{symbol}|{entry_t}|{family}"
            rec = {
                **market_base,
                "event_id": eid,
                "symbol": symbol,
                "event_family": family,
                **extra,
            }
            events.append(rec)

        if _cross_up(float(pup) if pup is not None else float("nan"), float(up) if up is not None else float("nan"), BREADTH_UP_FROM, BREADTH_UP_TO):
            emit("BREADTH_UP", "MARKET", {"layer": "MARKET"})
        if _cross_down(float(pup) if pup is not None else float("nan"), float(up) if up is not None else float("nan"), BREADTH_DOWN_FROM, BREADTH_DOWN_TO):
            emit("BREADTH_DOWN", "MARKET", {"layer": "MARKET"})
        prev_accel = prev_ok.get("stored_accel")
        if np.isfinite(accel) and accel >= ACCEL_CUT and (prev_accel is None or not np.isfinite(prev_accel) or float(prev_accel) < ACCEL_CUT):
            emit("BREADTH_ACCEL", "MARKET", {"layer": "MARKET"})
        if np.isfinite(accel) and accel <= -ACCEL_CUT and (prev_accel is None or not np.isfinite(prev_accel) or float(prev_accel) > -ACCEL_CUT):
            emit("BREADTH_DECEL", "MARKET", {"layer": "MARKET"})
        if disp is not None and pdisp is not None and pdisp > 0 and disp >= pdisp * DISP_EXPAND_MULT and disp >= MIN_DISP:
            emit("DISP_EXPAND", "MARKET", {"layer": "MARKET"})
        if disp is not None and pdisp is not None and pdisp > 0 and disp <= pdisp * DISP_CONTRACT_MULT:
            emit("DISP_CONTRACT", "MARKET", {"layer": "MARKET"})
        if np.isfinite(hhi) and np.isfinite(phhi) and (hhi - phhi) >= LEAD_DHHI:
            emit("LEADERSHIP_EXPAND", "MARKET", {"layer": "MARKET"})
        if np.isfinite(hhi) and np.isfinite(phhi) and (phhi - hhi) >= LEAD_DHHI:
            emit("LEADERSHIP_COLLAPSE", "MARKET", {"layer": "MARKET"})

        all_secs = set(snap["sector_up"]) | set(prev_ok["sector_up"])
        for sec in all_secs:
            now_s = snap["sector_up"].get(sec)
            prev_s = prev_ok["sector_up"].get(sec)
            now_rs = (float(now_s) - float(up)) if now_s is not None and up is not None else float("nan")
            prev_rs = (float(prev_s) - float(pup)) if prev_s is not None and pup is not None else float("nan")
            extra = {"layer": "SECTOR", "sector": sec, "sector_up_5m": now_s, "sector_rs": now_rs, "sector_rs_slope": (now_rs - prev_rs) if np.isfinite(now_rs) and np.isfinite(prev_rs) else float("nan")}
            if np.isfinite(prev_rs) and np.isfinite(now_rs) and prev_rs <= 0 and now_rs > 0:
                emit("SECTOR_RS_IMPROVE", f"SECTOR:{sec}", extra)
            if np.isfinite(prev_rs) and np.isfinite(now_rs) and prev_rs > 0 and now_rs <= 0:
                emit("SECTOR_RS_DETERIORATE", f"SECTOR:{sec}", extra)
            if _cross_up(float(prev_s) if prev_s is not None else float("nan"), float(now_s) if now_s is not None else float("nan"), BREADTH_UP_FROM, BREADTH_UP_TO):
                emit("SECTOR_BREADTH_UP", f"SECTOR:{sec}", extra)
            if _cross_down(float(prev_s) if prev_s is not None else float("nan"), float(now_s) if now_s is not None else float("nan"), BREADTH_DOWN_FROM, BREADTH_DOWN_TO):
                emit("SECTOR_BREADTH_DOWN", f"SECTOR:{sec}", extra)

        for sym, now in snap["by_sym"].items():
            prev = prev_ok["by_sym"].get(sym)
            if not prev:
                continue
            rec = per[sym]
            extra = {
                "layer": "STOCK",
                "sector": now["sector"],
                "sector_up_5m": now.get("sector_up_5m"),
                "sector_rs": now.get("sector_rs"),
                "sector_rs_slope": (
                    float(now["sector_rs"]) - float(prev["sector_rs"])
                    if np.isfinite(now.get("sector_rs") if now.get("sector_rs") is not None else float("nan"))
                    and np.isfinite(prev.get("sector_rs") if prev.get("sector_rs") is not None else float("nan"))
                    else float("nan")
                ),
                "sector_n": now.get("sector_n"),
                "ret_5m": now["ret_5m"],
                "ret_15m": now["ret_15m"],
                "rs_5m": now["rs_5m"],
                "rs_slope": (float(now["rs_5m"]) - float(prev["rs_5m"])) if np.isfinite(now["rs_5m"]) and np.isfinite(prev["rs_5m"]) else float("nan"),
                "vwap_dist": now["vwap_dist"],
                "vwap_transition_reclaim": bool(now["above_vwap"] and not prev["above_vwap"]),
                "ema_dist": now["ema_dist"],
                "ema9_gt_ema21": now["ema9_gt_ema21"],
                "dist_20high": now["dist_20high"],
                "hi20": now.get("hi20"),
                "vol_rel20": now["vol_rel20"],
                "va_rel20": now["va_rel20"],
                "rng_rel20": now["rng_rel20"],
                "pullback_state": bool(np.isfinite(now["ret_15m"]) and np.isfinite(now["ret_5m"]) and now["ret_15m"] > 0 and now["ret_5m"] < 0),
                "rec": rec,
                "bar_i": now["i"],
            }
            if _bool_onset(prev["above_vwap"], now["above_vwap"]):
                emit("VWAP_RECLAIM", sym, extra)
            if _bool_onset(not prev["above_vwap"], not now["above_vwap"]) and prev["above_vwap"] and not now["above_vwap"]:
                emit("VWAP_LOSS", sym, extra)
            if _bool_onset(prev["ema9_gt_ema21"], now["ema9_gt_ema21"]):
                emit("EMA_IMPROVE", sym, extra)
            if prev["ema9_gt_ema21"] and not now["ema9_gt_ema21"]:
                emit("EMA_DETERIORATE", sym, extra)
            if _bool_onset(prev["breakout20"], now["breakout20"]):
                emit("BREAKOUT20", sym, extra)
            pull_now = extra["pullback_state"]
            pull_prev = bool(np.isfinite(prev["ret_15m"]) and np.isfinite(prev["ret_5m"]) and prev["ret_15m"] > 0 and prev["ret_5m"] < 0)
            if pull_now and not pull_prev:
                emit("PULLBACK_START", sym, extra)
            if np.isfinite(prev["rs_5m"]) and np.isfinite(now["rs_5m"]) and prev["rs_5m"] <= 0 and now["rs_5m"] > 0:
                emit("RS_IMPROVE", sym, extra)
            if np.isfinite(prev["rs_5m"]) and np.isfinite(now["rs_5m"]) and prev["rs_5m"] > 0 and now["rs_5m"] <= 0:
                emit("RS_DETERIORATE", sym, extra)
            if _cross_up(prev["vol_rel20"], now["vol_rel20"], RELVOL_CROSS, RELVOL_CROSS):
                emit("RELVOL_EXPAND", sym, extra)
            if _cross_up(prev["va_rel20"], now["va_rel20"], VA_CROSS, VA_CROSS):
                emit("VA_EXPAND", sym, extra)
            if _cross_up(prev["rng_rel20"], now["rng_rel20"], RANGE_CROSS, RANGE_CROSS):
                emit("RANGE_EXPAND", sym, extra)
            if np.isfinite(prev["rng_rel20"]) and np.isfinite(now["rng_rel20"]) and prev["rng_rel20"] < COMPRESSION and now["rng_rel20"] >= RELEASE and now["above_vwap"]:
                emit("COMPRESSION_RELEASE", sym, extra)
        snap["stored_accel"] = accel
        prev2_ok, prev_ok = prev_ok, snap
    return events


def freeze_event_ids(events: list[dict[str, Any]]) -> str:
    ids = sorted(str(e["event_id"]) for e in events)
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()


def strip_runtime(events: list[dict[str, Any]]) -> None:
    for e in events:
        e.pop("rec", None)


def build_universe(
    *,
    minutes: pd.DataFrame,
    sector_of: dict[str, str],
    forbidden_dates: set[str],
    clocks: list[str] | None = None,
) -> dict[str, Any]:
    if minutes.empty:
        return {"ok": False, "events": [], "reason": "no_minutes"}
    if minutes["date"].isin(list(forbidden_dates)).any():
        raise RuntimeError("frozen_validation_accessed_during_event_build")
    clocks = clocks or grid_clocks()
    events: list[dict[str, Any]] = []
    grouped = minutes.groupby("date", sort=True)
    n_dates = grouped.ngroups
    for i, (day, g) in enumerate(grouped, start=1):
        day_s = str(day)
        if day_s in forbidden_dates:
            raise RuntimeError("frozen_validation_accessed_during_event_build")
        day_events = scan_day(date=day_s, g=g, sector_of=sector_of, clocks=clocks)
        events.extend(day_events)
        if i % 25 == 0:
            print(f"EVENTS {i}/{n_dates} n={len(events)}", flush=True)
    sha = freeze_event_ids(events)
    counts: dict[str, int] = defaultdict(int)
    for e in events:
        counts[str(e["event_family"])] += 1
    return {
        "ok": True,
        "events": events,
        "event_n": len(events),
        "families": dict(counts),
        "event_id_sha256": sha,
        "identities_frozen_before_outcomes": True,
        "clocks": clocks,
        "grid_step_min": GRID_STEP_MIN,
        "copied_m4_m6_thresholds": False,
        "m1_breadth_level_gates_used": False,
    }


def family_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for e in events:
        out[str(e["event_family"])] += 1
    return dict(out)


def primitives_spec() -> list[dict[str, Any]]:
    return [
        {"family": "BREADTH_UP", "layer": "MARKET", "rule": "up_5m crosses from <0.50 to >=0.55", "entry_signal": False},
        {"family": "BREADTH_DOWN", "layer": "MARKET", "rule": "up_5m crosses from >0.50 to <=0.45", "entry_signal": False},
        {"family": "BREADTH_ACCEL", "layer": "MARKET", "rule": "second difference of up_5m >= 0.05", "entry_signal": False},
        {"family": "BREADTH_DECEL", "layer": "MARKET", "rule": "second difference of up_5m <= -0.05", "entry_signal": False},
        {"family": "DISP_EXPAND", "layer": "MARKET", "rule": "dispersion *1.25 and >=0.002", "entry_signal": False},
        {"family": "DISP_CONTRACT", "layer": "MARKET", "rule": "dispersion *0.80", "entry_signal": False},
        {"family": "LEADERSHIP_EXPAND", "layer": "MARKET", "rule": "HHI +0.02", "entry_signal": False},
        {"family": "LEADERSHIP_COLLAPSE", "layer": "MARKET", "rule": "HHI -0.02", "entry_signal": False},
        {"family": "SECTOR_RS_IMPROVE", "layer": "SECTOR", "rule": "sector_up - market_up crosses to >0", "entry_signal": False},
        {"family": "SECTOR_RS_DETERIORATE", "layer": "SECTOR", "rule": "sector RS crosses to <=0", "entry_signal": False},
        {"family": "SECTOR_BREADTH_UP", "layer": "SECTOR", "rule": "sector up_5m <0.50 to >=0.55", "entry_signal": False},
        {"family": "SECTOR_BREADTH_DOWN", "layer": "SECTOR", "rule": "sector up_5m >0.50 to <=0.45", "entry_signal": False},
        {"family": "VWAP_RECLAIM", "layer": "STOCK", "rule": "close crosses above session VWAP", "entry_signal": False},
        {"family": "VWAP_LOSS", "layer": "STOCK", "rule": "close crosses below session VWAP", "entry_signal": False},
        {"family": "EMA_IMPROVE", "layer": "STOCK", "rule": "ema9>ema21 onset", "entry_signal": False},
        {"family": "EMA_DETERIORATE", "layer": "STOCK", "rule": "ema9>ema21 loss", "entry_signal": False},
        {"family": "BREAKOUT20", "layer": "STOCK", "rule": "close first reaches prior 20-bar high", "entry_signal": False},
        {"family": "PULLBACK_START", "layer": "STOCK", "rule": "ret_15m>0 and ret_5m<0 onset", "entry_signal": False},
        {"family": "RS_IMPROVE", "layer": "STOCK", "rule": "rs_5m crosses to >0", "entry_signal": False},
        {"family": "RS_DETERIORATE", "layer": "STOCK", "rule": "rs_5m crosses to <=0", "entry_signal": False},
        {"family": "RELVOL_EXPAND", "layer": "STOCK", "rule": "vol/20-mean crosses 1.5", "entry_signal": False},
        {"family": "VA_EXPAND", "layer": "STOCK", "rule": "trading-value/20-mean crosses 1.5", "entry_signal": False},
        {"family": "RANGE_EXPAND", "layer": "STOCK", "rule": "range/20-mean crosses 1.5", "entry_signal": False},
        {"family": "COMPRESSION_RELEASE", "layer": "STOCK", "rule": "range_rel <0.60 then >=1.0 while above VWAP", "entry_signal": False},
    ]
