"""DEV structural 5m EMA21 availability. No signals, fills, trades, or PnL."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from _p1_inventory import resolve_universe
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import _bare, find_capture_dir, iter_push
from research.new_full_strategy_architecture_construction_v2 import (
    AM_END_HM,
    AM_START_HM,
    DEVELOPMENT_DAYS,
    EMA_LEVEL_PERIOD,
    FORBIDDEN_INPUT_DAYS,
    HTF_WIDTH_SEC,
    LEVEL_AVAILABLE_DAY_MIN,
    MAX_RESEARCH_DATE,
    SESSION_FLATTEN_HM,
    BURNED_HOLDOUT_DAYS,
    STRESS_DAYS,
)
from research.new_full_strategy_architecture_construction_v2.isolation import CACHE, TODAY
from research.new_full_strategy_implementation_and_dev_eval_v1.engine import arrival_epoch
from research.simple_tech_entry_family import EMA_LONG
from research.simple_tech_entry_family.bars import BAR_FIELDS, minute_epoch
from research.simple_tech_entry_family.indicators import ema
from research.simple_tech_entry_family.v7_bars import aggregate_bars

JST = ZoneInfo("Asia/Tokyo")
AUDIT = {
    "HOLDOUT_BURNED_READ_N": 0,
    "STRESS_READ_N": 0,
    "FUTURE_DATA_N": 0,
}


def _dump(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _iso(t: Optional[float]) -> Optional[str]:
    if t is None:
        return None
    return datetime.fromtimestamp(float(t), JST).strftime("%H:%M:%S")


def assert_dev_day(day: str) -> None:
    d = str(day)
    if d > str(MAX_RESEARCH_DATE) or d in FORBIDDEN_INPUT_DAYS or d >= "20260903" or d == str(TODAY):
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"FORBIDDEN_DAY {d}")
    if d in BURNED_HOLDOUT_DAYS:
        AUDIT["HOLDOUT_BURNED_READ_N"] += 1
        raise RuntimeError(f"HOLDOUT_READ {d}")
    if d in STRESS_DAYS:
        AUDIT["STRESS_READ_N"] += 1
        raise RuntimeError(f"STRESS_READ {d}")
    if d not in DEVELOPMENT_DAYS:
        raise RuntimeError(f"NOT_DEV_DAY {d}")


def _placeholder_1m(minutes: list[float]) -> dict[str, np.ndarray]:
    mins = sorted(float(x) for x in minutes)
    n = len(mins)
    raw = {k: np.ones(n, dtype=float) for k in BAR_FIELDS}
    raw["minute_epoch"] = np.asarray(mins, dtype=float)
    raw["open"] = np.full(n, 100.0)
    raw["high"] = np.full(n, 101.0)
    raw["low"] = np.full(n, 99.0)
    raw["close"] = np.full(n, 100.0)
    raw["volume"] = np.ones(n)
    raw["n_events"] = np.ones(n)
    raw["first_t"] = raw["minute_epoch"]
    raw["last_t"] = raw["minute_epoch"] + 50.0
    raw["finalize_t"] = raw["minute_epoch"] + 60.0
    raw["up_vol"] = np.ones(n)
    raw["down_vol"] = np.ones(n)
    raw["ask_vol"] = np.ones(n)
    raw["bid_vol"] = np.ones(n)
    raw["vwap_num"] = raw["close"] * raw["volume"]
    return raw


def first_last_ema21(htf: dict[str, np.ndarray], *, flatten_t: float) -> tuple[Optional[float], Optional[float]]:
    if not htf or int(htf.get("finalize_t", np.asarray([])).size) == 0:
        return None, None
    e = ema(np.asarray(htf["close"], dtype=float), int(EMA_LONG))
    first = None
    last = None
    for i in range(int(e.size)):
        if e[i] != e[i]:
            continue
        fin = float(htf["finalize_t"][i])
        if fin > float(flatten_t) + 1e-12:
            continue
        if first is None:
            first = fin
        last = fin
    return first, last


def scan_day(day: str, *, use_cache: bool = True) -> dict[str, Any]:
    assert_dev_day(day)
    path = CACHE / f"avail_{day}.json"
    if use_cache:
        prev = _load(path)
        if prev.get("date") == str(day) and bool(prev.get("scanned")):
            return prev
    cap = find_capture_dir(day)
    if cap is None:
        raise RuntimeError(f"NO_CAPTURE {day}")
    uni = resolve_universe(day, cap)
    if not uni.get("resolved"):
        raise RuntimeError(f"UNIVERSE_UNRESOLVED {day}")
    symbols = [_bare(s) for s in list(uni.get("symbols") or []) if _bare(s)]
    uni_set = set(symbols)
    am_start = hm_epoch(day, int(AM_START_HM[0]), int(AM_START_HM[1]))
    am_end = hm_epoch(day, int(AM_END_HM[0]), int(AM_END_HM[1]))
    flatten_t = hm_epoch(day, int(SESSION_FLATTEN_HM[0]), int(SESSION_FLATTEN_HM[1]))
    minutes: dict[str, set[float]] = {s: set() for s in symbols}
    n_rec = 0
    t0 = time.time()
    print(f"AVAIL_DAY_START {day} universe_n={len(symbols)}", flush=True)
    for rec in iter_push(cap):
        n_rec += 1
        arrival, _field = arrival_epoch(rec)
        if arrival is None:
            continue
        if float(arrival) < float(am_start) - 1e-12 or float(arrival) >= float(am_end) - 1e-12:
            continue
        pay = rec.get("payload") if isinstance(rec.get("payload"), dict) else rec.get("original_payload")
        sym = _bare(rec.get("symbol") or (pay or {}).get("Symbol"))
        if sym not in uni_set:
            continue
        minutes[sym].add(minute_epoch(float(arrival)))
        if n_rec % 400000 == 0:
            print(f"AVAIL_DAY_PROG {day} n={n_rec} sec={time.time()-t0:.1f}", flush=True)
    firsts: list[float] = []
    lasts: list[float] = []
    n_with = 0
    for sym, mins in minutes.items():
        if len(mins) < int(EMA_LEVEL_PERIOD) * 5:
            continue
        raw = _placeholder_1m(list(mins))
        htf, leak = aggregate_bars(raw, width_sec=float(HTF_WIDTH_SEC), am_start=float(am_start), am_end=float(am_end))
        _ = leak
        f, last = first_last_ema21(htf, flatten_t=float(flatten_t))
        if f is not None:
            n_with += 1
            firsts.append(float(f))
            if last is not None:
                lasts.append(float(last))
    first = min(firsts) if firsts else None
    last = max(lasts) if lasts else None
    before = first is not None and float(first) <= float(flatten_t) + 1e-12
    out = {
        "date": str(day),
        "scanned": True,
        "universe_n": len(symbols),
        "universe_source": uni.get("source"),
        "symbols_with_level_n": int(n_with),
        "FIRST_5M_EMA21_AVAILABLE_T": first,
        "FIRST_5M_EMA21_AVAILABLE_JST": _iso(first),
        "LAST_USABLE_LEVEL_T": last,
        "LAST_USABLE_LEVEL_JST": _iso(last),
        "LEVEL_AVAILABLE_BEFORE_1129": bool(before),
        "record_n": n_rec,
        "elapsed_sec": float(time.time() - t0),
        "flatten_jst": _iso(flatten_t),
        "htf_width_sec": float(HTF_WIDTH_SEC),
        "ema_period": int(EMA_LONG),
        "placeholder_ohlc": True,
        "prices_not_used_for_availability": True,
    }
    _dump(path, out)
    return out


def structural_availability(*, use_cache: bool = True) -> dict[str, Any]:
    rows = [scan_day(d, use_cache=use_cache) for d in DEVELOPMENT_DAYS]
    n_ok = sum(1 for r in rows if r.get("LEVEL_AVAILABLE_BEFORE_1129"))
    return {
        "days": rows,
        "LEVEL_AVAILABLE_DAY_N": int(n_ok),
        "LEVEL_AVAILABLE_DAY_MIN": int(LEVEL_AVAILABLE_DAY_MIN),
        "STRUCTURAL_COVERAGE_POSSIBLE": int(n_ok) >= int(LEVEL_AVAILABLE_DAY_MIN),
        "AUDIT": dict(AUDIT),
        "SIGNAL_N_COMPUTED": False,
        "FILL_N_COMPUTED": False,
        "TRADE_N_COMPUTED": False,
        "PNL_COMPUTED": False,
    }
