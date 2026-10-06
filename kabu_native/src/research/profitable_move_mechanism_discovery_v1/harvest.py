"""Sealed DEV harvest of causal snapshots and executable markout labels. Not strategy trades."""
from __future__ import annotations

import gzip
import pickle
import sys
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from _p1_inventory import resolve_universe
from research.anchor_vs_event_driven.run_comparison import _bare, find_capture_dir, iter_push
from research.profitable_move_mechanism_discovery_v1 import (
    BURNED_HOLDOUT_DAYS,
    DEVELOPMENT_DAYS,
    FEATURE_LOOKAHEAD_N,
    FIXED_HORIZONS,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    PRIMARY_DISCOVERY_HORIZON,
    STRESS_DAYS,
)
from research.profitable_move_mechanism_discovery_v1.engine import DiscoveryEngine
from research.profitable_move_mechanism_discovery_v1.isolation import CACHE, TODAY
from research.profitable_move_mechanism_discovery_v1.predicates import (
    BAR_FNS,
    assert_st_bindings,
    bar_series,
    breadth_expanding,
    evaluable_i,
    indicators,
)

AUDIT = {
    "HOLDOUT_BURNED_READ_N": 0,
    "STRESS_READ_N": 0,
    "STRESS_FILE_OPEN_N": 0,
    "FUTURE_DATA_N": 0,
    "CAPTURE_DISCOVERED_UNIVERSE_N": 0,
    "FEATURE_LOOKAHEAD_N": 0,
    "LABEL_USED_AS_INPUT_N": 0,
}


def _dump_gz(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as fh:
        pickle.dump(body, fh, protocol=4)


def _load_gz(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with gzip.open(path, "rb") as fh:
        got = pickle.load(fh)
    return got if isinstance(got, dict) else {}


def assert_dev_day(day: str) -> None:
    d = str(day)
    if d != d[:8] or d > str(MAX_RESEARCH_DATE):
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"FUTURE_OR_BEYOND_MAX {d}")
    if d in BURNED_HOLDOUT_DAYS:
        AUDIT["HOLDOUT_BURNED_READ_N"] += 1
        raise RuntimeError(f"HOLDOUT_READ {d}")
    if d in STRESS_DAYS:
        AUDIT["STRESS_READ_N"] += 1
        AUDIT["STRESS_FILE_OPEN_N"] += 1
        raise RuntimeError(f"STRESS_READ {d}")
    if d in FORBIDDEN_INPUT_DAYS or d >= "20260903" or d == str(TODAY):
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"FORBIDDEN_DAY {d}")
    if d not in DEVELOPMENT_DAYS:
        raise RuntimeError(f"NOT_DEV_DAY {d}")


def cache_path(day: str, source_hash: str) -> Path:
    return CACHE / f"day_{day}_{str(source_hash)[:12]}.pkl.gz"


def _first_ge(t: np.ndarray, px: np.ndarray, t0: float) -> tuple[Optional[float], Optional[float]]:
    if int(t.size) == 0:
        return None, None
    i = int(np.searchsorted(t, float(t0), side="left"))
    if i >= int(t.size):
        return None, None
    return float(t[i]), float(px[i])


def _first_in(t: np.ndarray, px: np.ndarray, lo: float, hi: float) -> tuple[Optional[float], Optional[float]]:
    if int(t.size) == 0:
        return None, None
    i = int(np.searchsorted(t, float(lo), side="left"))
    j = int(np.searchsorted(t, float(hi), side="left"))
    if i >= j:
        return None, None
    return float(t[i]), float(px[i])


def resolve_entry(standing: dict[str, Any], ask_t: np.ndarray, ask_px: np.ndarray, t_i: float) -> tuple[Optional[float], Optional[float]]:
    if bool(standing.get("ask_ok")):
        px = standing.get("ask")
        try:
            v = float(px)
        except (TypeError, ValueError):
            v = float("nan")
        if v == v and v > 0:
            return float(t_i), v
    et, ep = _first_ge(ask_t, ask_px, float(t_i))
    return et, ep


def resolve_exit_h(
    standing_h: dict[str, Any] | None,
    bid_t: np.ndarray,
    bid_px: np.ndarray,
    t_i: float,
    h_min: int,
) -> tuple[Optional[float], Optional[float]]:
    lo = float(t_i) + float(h_min) * 60.0
    hi = lo + 60.0
    if standing_h and bool(standing_h.get("bid_ok")):
        px = standing_h.get("bid")
        try:
            v = float(px)
        except (TypeError, ValueError):
            v = float("nan")
        if v == v and v > 0:
            return lo, v
    et, ep = _first_in(bid_t, bid_px, lo, hi)
    return et, ep


def _yen100(bid: float, ask: float) -> float:
    return (float(bid) - float(ask)) * 100.0


def _bps(bid: float, ask: float) -> float:
    return (float(bid) - float(ask)) / float(ask) * 10000.0


def snapshots_from_engine(eng: dict[str, Any]) -> list[dict[str, Any]]:
    assert_st_bindings()
    assert int(FEATURE_LOOKAHEAD_N) == 0
    assert int(PRIMARY_DISCOVERY_HORIZON) == 5
    universe = [str(s) for s in (eng.get("universe") or [])]
    flatten_t = float(eng["flatten_t"])
    bars = eng["bars"]
    ask = eng["ask"]
    bid = eng["bid"]
    standing = eng["standing"]
    stand_ix = {round(float(k), 6): v for k, v in (standing or {}).items()}
    timers = [float(t) for t in (eng.get("timers") or [])]
    last_h10 = float(flatten_t) - 10.0 * 60.0

    ind_map: dict[str, dict[str, np.ndarray]] = {}
    series: dict[str, dict[str, np.ndarray]] = {}
    for sym in universe:
        raw = bars[sym]
        if int(np.asarray(raw["close"]).size) == 0:
            continue
        ind = indicators(raw)
        ind_map[sym] = ind
        series[sym] = {pid: bar_series(ind, pid) for pid in BAR_FNS}

    out: list[dict[str, Any]] = []
    for t_i in timers:
        if float(t_i) > last_h10 + 1e-9:
            continue
        e = float(t_i) - 60.0
        trend_now: dict[str, bool] = {}
        trend_prev: dict[str, bool] = {}
        idx: dict[str, int] = {}
        for sym in universe:
            ind = ind_map.get(sym)
            if not ind:
                continue
            mins = np.asarray(ind["minute_epoch"], dtype=float)
            hits = np.where(np.abs(mins - e) <= 1e-9)[0]
            if int(hits.size) != 1:
                continue
            i = int(hits[0])
            n = int(mins.size)
            if not evaluable_i(i, n):
                continue
            fin = float(ind["finalize_t"][i])
            if fin > float(t_i) + 1e-9:
                AUDIT["FEATURE_LOOKAHEAD_N"] += 1
                continue
            idx[sym] = i
            ser = series[sym]
            trend_now[sym] = bool(ser["S_MA_TREND_UP"][i])
            trend_prev[sym] = bool(ser["S_MA_TREND_UP"][i - 1]) if i >= 1 else False
        xs_now = breadth_expanding(trend_now, trend_prev)
        xs_prev = False
        t_prev = float(t_i) - 60.0
        if round(t_prev, 6) in stand_ix:
            prev_now: dict[str, bool] = {}
            prev_prev: dict[str, bool] = {}
            for sym, i in idx.items():
                ser = series[sym]
                j = i - 1
                if j < 1:
                    continue
                prev_now[sym] = bool(ser["S_MA_TREND_UP"][j])
                prev_prev[sym] = bool(ser["S_MA_TREND_UP"][j - 1])
            if prev_now:
                xs_prev = breadth_expanding(prev_now, prev_prev)

        stand_row = stand_ix.get(round(float(t_i), 6))
        if stand_row is None:
            continue
        stand_h: dict[int, dict[str, dict[str, Any]]] = {}
        for h in FIXED_HORIZONS:
            th = float(t_i) + float(h) * 60.0
            pack = stand_ix.get(round(th, 6))
            if pack is not None:
                stand_h[int(h)] = pack

        for sym, i in idx.items():
            st = stand_row.get(sym) or {}
            et, ep = resolve_entry(st, ask[sym]["t"], ask[sym]["px"], float(t_i))
            preds_now = {pid: bool(series[sym][pid][i]) for pid in BAR_FNS}
            preds_prev = {pid: bool(series[sym][pid][i - 1]) if i >= 1 else False for pid in BAR_FNS}
            preds_now["S_BID_GT_ASK_QTY"] = bool(st.get("bid_gt_ask"))
            preds_now["S_BOARD_SUPPORT"] = bool(st.get("board_support"))
            preds_now["S_BREADTH_EXPANDING"] = bool(xs_now)
            st_prev = (stand_ix.get(round(float(t_i) - 60.0, 6)) or {}).get(sym) or {}
            if st_prev:
                preds_prev["S_BID_GT_ASK_QTY"] = bool(st_prev.get("bid_gt_ask"))
                preds_prev["S_BOARD_SUPPORT"] = bool(st_prev.get("board_support"))
            else:
                preds_prev["S_BID_GT_ASK_QTY"] = False
                preds_prev["S_BOARD_SUPPORT"] = False
            preds_prev["S_BREADTH_EXPANDING"] = bool(xs_prev)

            rec: dict[str, Any] = {
                "date": str(eng["date"]),
                "symbol": sym,
                "T_i": float(t_i),
                "minute_e": float(e),
                "i": int(i),
                "preds": preds_now,
                "preds_prev": preds_prev,
                "entry_t": et,
                "entry_ask": ep,
            }
            complete = et is not None and ep is not None and float(et) < float(t_i) + float(min(FIXED_HORIZONS)) * 60.0
            for h in FIXED_HORIZONS:
                xt, xp = None, None
                if complete and ep is not None:
                    sh = (stand_h.get(int(h)) or {}).get(sym)
                    xt, xp = resolve_exit_h(sh, bid[sym]["t"], bid[sym]["px"], float(t_i), int(h))
                yen = _yen100(xp, ep) if (xp is not None and ep is not None) else None
                bps = _bps(xp, ep) if (xp is not None and ep is not None and ep > 0) else None
                rec[f"bid_t_h{h}"] = xt
                rec[f"bid_h{h}"] = xp
                rec[f"markout_yen100_h{h}"] = yen
                rec[f"markout_bps_h{h}"] = bps
            rec["complete_triple"] = all(rec.get(f"markout_yen100_h{h}") is not None for h in FIXED_HORIZONS)
            out.append(rec)
    return out


def harvest_day(day: str, *, use_cache: bool = True, source_hash: str = "") -> dict[str, Any]:
    assert_dev_day(day)
    path = cache_path(day, source_hash)
    if use_cache:
        prev = _load_gz(path)
        if prev.get("date") == str(day) and prev.get("source_hash") == str(source_hash or "") and prev.get("snapshots") is not None:
            return prev
    cap = find_capture_dir(day)
    if cap is None:
        raise RuntimeError(f"NO_CAPTURE {day}")
    uni = resolve_universe(day, cap)
    if not uni.get("resolved"):
        raise RuntimeError(f"UNIVERSE_UNRESOLVED {day} {uni.get('reason')}")
    symbols = [_bare(s) for s in list(uni.get("symbols") or []) if _bare(s)]
    src = str(uni.get("source") or "")
    if src.startswith("capture") or "symbols_seen" in src:
        AUDIT["CAPTURE_DISCOVERED_UNIVERSE_N"] += 1
        raise RuntimeError(f"CAPTURE_DISCOVERED_UNIVERSE {day}")
    eng = DiscoveryEngine(day, symbols)
    n_rec = 0
    t0 = time.time()
    print(f"HARVEST_DAY_START {day} universe_n={len(symbols)}", flush=True)
    for rec in iter_push(cap):
        n_rec += 1
        eng.ingest(rec)
        if n_rec % 200000 == 0:
            print(f"HARVEST_DAY_PROG {day} n={n_rec} sec={time.time()-t0:.1f}", flush=True)
    eng.finish()
    packed = eng.result()
    snaps = snapshots_from_engine(packed)
    out = {
        "date": str(day),
        "universe": symbols,
        "universe_source": src,
        "universe_n": len(symbols),
        "record_n": n_rec,
        "elapsed_sec": float(time.time() - t0),
        "capture_path": str(cap),
        "source_hash": str(source_hash or ""),
        "flags": packed.get("flags"),
        "snapshot_n": len(snaps),
        "complete_n": int(sum(1 for r in snaps if r.get("complete_triple"))),
        "snapshots": snaps,
    }
    _dump_gz(path, out)
    print(
        f"HARVEST_DAY_DONE {day} snap={len(snaps)} complete={out['complete_n']} sec={time.time()-t0:.1f}",
        flush=True,
    )
    return out


def harvest_all(*, use_cache: bool = True, source_hash: str = "") -> list[dict[str, Any]]:
    days = []
    for day in DEVELOPMENT_DAYS:
        days.append(harvest_day(str(day), use_cache=use_cache, source_hash=source_hash))
    return days
