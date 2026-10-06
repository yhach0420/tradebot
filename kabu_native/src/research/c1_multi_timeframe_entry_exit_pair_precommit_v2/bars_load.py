"""DEV canonical completed 1m OHLC only. No C1 signals. No fills. No PnL."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import _bare, capture_event_epoch, iter_push
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2 import DEVELOPMENT_DAYS, MAX_RESEARCH_DATE
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.isolation import CACHE
from research.c1_multi_timeframe_full_strategy_v1.harvest import assert_dev_only_day, sealed_dev_caps
from research.new_entry_breakout_continuation_v1.harvest import board_row
from research.simple_full_strategy_discovery_v1.entries import session_vwap_arr
from research.simple_tech_entry_family.bars import SymbolBarBuilder
from small_paper.v1r_live_dual_lane import session_end_for_position

FIELDS = ("open", "high", "low", "close", "volume", "vwap_num", "finalize_t")


def _cache_path(day: str) -> Path:
    return CACHE / f"DEVELOPMENT_{day}_ohlc.npz"


def _meta_path(day: str) -> Path:
    return CACHE / f"DEVELOPMENT_{day}_ohlc_meta.json"


def _nonfinite_n(arr: np.ndarray) -> int:
    if arr is None or int(arr.size) == 0:
        return 0
    x = np.asarray(arr, dtype=float)
    return int(np.sum(~np.isfinite(x)))


def process_dev_day_ohlc(payload: dict[str, Any]) -> dict[str, Any]:
    day = str(payload["date"])
    assert_dev_only_day(day)
    if day > MAX_RESEARCH_DATE:
        raise RuntimeError(f"FUTURE:{day}")
    capture = Path(payload["capture_path"])
    universe = [_bare(s) for s in list(payload["universe"]) if _bare(s)]
    uni = set(universe)
    am_start = float(hm_epoch(day, 9, 0))
    am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
    builders: dict[str, SymbolBarBuilder] = {s: SymbolBarBuilder(am_start=am_start, am_end=am_end) for s in universe}
    events_n = 0
    for rec in iter_push(capture):
        sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
        if not sym or sym not in uni:
            continue
        pay = dict(rec.get("payload") or rec.get("original_payload") or {})
        et = capture_event_epoch(rec, pay)
        if et is None:
            continue
        if float(et) < am_start - 120.0:
            continue
        if float(et) > am_end + 2.0:
            continue
        events_n += 1
        row = board_row(rec, pay, float(et))
        builders[sym].on_event(
            et=float(et),
            px=row["px"] if row["px"] == row["px"] else None,
            cum_vol=row.get("cum_vol"),
            bid=row["bid"] if row["bid"] == row["bid"] else None,
            ask=row["ask"] if row["ask"] == row["ask"] else None,
            continuous=bool(row.get("continuous")),
        )
        if events_n % 400000 == 0:
            print(f"{day} ohlc events={events_n}", flush=True)
    arrays: dict[str, dict[str, np.ndarray]] = {}
    for s in universe:
        builders[s].close_session()
        raw = builders[s].as_arrays()
        arrays[s] = {k: np.asarray(raw[k], dtype=float) for k in FIELDS if k in raw}
    return {"ok": True, "date": day, "events_n": events_n, "arrays": arrays, "universe": universe}


def _save_day(day: str, body: dict[str, Any]) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    payload = {}
    symbols = list(body.get("universe") or [])
    for i, s in enumerate(symbols):
        arr = (body.get("arrays") or {}).get(s) or {}
        for k in FIELDS:
            payload[f"{i}__{k}"] = np.asarray(arr.get(k, []), dtype=float)
    np.savez_compressed(_cache_path(day), **payload)
    _meta_path(day).write_text(
        json.dumps({"ok": True, "date": day, "symbols": symbols, "events_n": body.get("events_n")}, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_day(day: str) -> Optional[dict[str, Any]]:
    cp = _cache_path(day)
    mp = _meta_path(day)
    if not cp.is_file() or not mp.is_file():
        return None
    meta = json.loads(mp.read_text(encoding="utf-8"))
    if not meta.get("ok") or str(meta.get("date") or "") != day:
        return None
    blob = np.load(cp)
    arrays = {}
    symbols = list(meta.get("symbols") or [])
    for i, s in enumerate(symbols):
        arrays[s] = {k: np.asarray(blob[f"{i}__{k}"], dtype=float) for k in FIELDS}
    return {"ok": True, "date": day, "arrays": arrays, "universe": symbols, "events_n": meta.get("events_n")}


def load_dev_ohlc() -> dict[str, Any]:
    invs, blockers = sealed_dev_caps()
    if blockers:
        return {"ok": False, "blocker": "CAPTURE", "blockers": blockers, "rows": []}
    rows: list[dict[str, Any]] = []
    integ = {
        "OPEN_NONFINITE_N": 0,
        "HIGH_NONFINITE_N": 0,
        "LOW_NONFINITE_N": 0,
        "CLOSE_NONFINITE_N": 0,
        "ARRAY_N": 0,
        "BAR_N": 0,
        "DAY_N": 0,
        "SYMBOL_DAY_N": 0,
        "EMPTY_ARRAY_N": 0,
        "CACHE_HIT_N": 0,
        "REPLAY_N": 0,
    }
    day_ok: dict[str, bool] = {}
    for inv in invs:
        day = str(inv["date"])
        if day not in DEVELOPMENT_DAYS:
            return {"ok": False, "blocker": f"NON_DEV:{day}", "rows": []}
        saved = _load_day(day)
        if saved is None:
            body = process_dev_day_ohlc(
                {"date": day, "capture_path": inv["capture_path"], "universe": list(inv["universe_symbols"])}
            )
            if not body.get("ok"):
                return {"ok": False, "blocker": f"OHLC:{day}", "rows": []}
            _save_day(day, body)
            saved = body
            integ["REPLAY_N"] += 1
            print(f"ohlc-replay DEVELOPMENT {day}", flush=True)
        else:
            integ["CACHE_HIT_N"] += 1
            print(f"ohlc-cache DEVELOPMENT {day}", flush=True)
        day_ok[day] = True
        integ["DAY_N"] += 1
        for s, arr in (saved.get("arrays") or {}).items():
            open_ = np.asarray(arr["open"], dtype=float)
            high = np.asarray(arr["high"], dtype=float)
            low = np.asarray(arr["low"], dtype=float)
            close = np.asarray(arr["close"], dtype=float)
            volume = np.asarray(arr.get("volume", []), dtype=float)
            vwap_num = np.asarray(arr.get("vwap_num", []), dtype=float)
            finalize = np.asarray(arr["finalize_t"], dtype=float)
            vwap = session_vwap_arr(close, volume, vwap_num)
            n = int(close.size)
            integ["ARRAY_N"] += 1
            integ["SYMBOL_DAY_N"] += 1
            integ["BAR_N"] += n
            if n == 0:
                integ["EMPTY_ARRAY_N"] += 1
            integ["OPEN_NONFINITE_N"] += _nonfinite_n(open_)
            integ["HIGH_NONFINITE_N"] += _nonfinite_n(high)
            integ["LOW_NONFINITE_N"] += _nonfinite_n(low)
            integ["CLOSE_NONFINITE_N"] += _nonfinite_n(close)
            rows.append(
                {
                    "day": day,
                    "symbol": s,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "vwap": vwap,
                    "finalize_t": finalize,
                    "bar_n": n,
                }
            )
    missing = [d for d in DEVELOPMENT_DAYS if not day_ok.get(str(d))]
    ok = not missing and len(invs) == 10
    return {
        "ok": ok,
        "blocker": None if ok else "DEV_DAYS_INCOMPLETE:" + ",".join(missing),
        "rows": rows,
        "integrity": integ,
        "day_ok": day_ok,
        "CANDIDATE_SIGNAL_COUNT_COMPUTED": False,
        "CANDIDATE_FILL_COUNT_COMPUTED": False,
        "CANDIDATE_TRADE_COUNT_COMPUTED": False,
        "CANDIDATE_PNL_COMPUTED": False,
    }
