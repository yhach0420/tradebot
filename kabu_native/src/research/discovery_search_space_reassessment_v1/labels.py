"""Fixed H3/H5/H10 executable labels from actual fills. Strategy EXIT unused. No horizon search."""
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
from research.discovery_search_space_reassessment_v1 import (
    BURNED_HOLDOUT_DAYS,
    DEVELOPMENT_DAYS,
    FIXED_HORIZONS,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    STRESS_DAYS,
)
from research.discovery_search_space_reassessment_v1.engine import TapeEngine, first_in, first_in_mid, last_le_mid
from research.discovery_search_space_reassessment_v1.isolation import CACHE, TODAY
from research.profitable_move_mechanism_discovery_v1.harvest import resolve_entry, resolve_exit_h

AUDIT = {
    "HOLDOUT_BURNED_READ_N": 0,
    "STRESS_READ_N": 0,
    "FUTURE_DATA_N": 0,
}


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
        raise RuntimeError(f"STRESS_READ {d}")
    if d in FORBIDDEN_INPUT_DAYS or d >= "20260903" or d == str(TODAY):
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"FORBIDDEN_DAY {d}")
    if d not in DEVELOPMENT_DAYS:
        raise RuntimeError(f"NOT_DEV_DAY {d}")


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


def tape_path(day: str, source_hash: str) -> Path:
    return CACHE / f"tape_{day}_{str(source_hash)[:12]}.pkl.gz"


def harvest_tape(day: str, *, use_cache: bool = True, source_hash: str = "") -> dict[str, Any]:
    assert_dev_day(day)
    path = tape_path(day, source_hash)
    if use_cache:
        prev = _load_gz(path)
        if prev.get("date") == str(day) and prev.get("source_hash") == str(source_hash or "") and prev.get("ask") is not None:
            return prev
    cap = find_capture_dir(day)
    if cap is None:
        raise RuntimeError(f"NO_CAPTURE {day}")
    uni = resolve_universe(day, cap)
    if not uni.get("resolved"):
        raise RuntimeError(f"UNIVERSE_UNRESOLVED {day}")
    symbols = [_bare(s) for s in list(uni.get("symbols") or []) if _bare(s)]
    src = str(uni.get("source") or "")
    if src.startswith("capture") or "symbols_seen" in src:
        raise RuntimeError(f"CAPTURE_DISCOVERED_UNIVERSE {day}")
    eng = TapeEngine(day, symbols)
    n_rec = 0
    t0 = time.time()
    print(f"TAPE_DAY_START {day} universe_n={len(symbols)}", flush=True)
    for rec in iter_push(cap):
        n_rec += 1
        eng.ingest(rec)
        if n_rec % 200000 == 0:
            print(f"TAPE_DAY_PROG {day} n={n_rec} sec={time.time()-t0:.1f}", flush=True)
    eng.finish()
    packed = eng.result()
    out = {
        "date": str(day),
        "universe": symbols,
        "universe_source": src,
        "source_hash": str(source_hash or ""),
        "flatten_t": packed["flatten_t"],
        "am_end": packed["am_end"],
        "flags": packed["flags"],
        "ask": packed["ask"],
        "bid": packed["bid"],
        "mid": packed["mid"],
        "standing": packed["standing"],
        "record_n": n_rec,
        "elapsed_sec": float(time.time() - t0),
    }
    _dump_gz(path, out)
    print(f"TAPE_DAY_DONE {day} sec={time.time()-t0:.1f}", flush=True)
    return out


def _stand_ix(standing: dict[Any, Any]) -> dict[float, dict[str, Any]]:
    return {round(float(k), 6): v for k, v in (standing or {}).items()}


def _x1_ask(tape: dict[str, Any], sym: str, fill_t: float) -> tuple[Optional[float], Optional[float]]:
    stand_ix = _stand_ix(tape.get("standing") or {})
    st = (stand_ix.get(round(float(fill_t), 6)) or {}).get(sym) or {}
    ask = tape["ask"][sym]
    return resolve_entry(st, ask["t"], ask["px"], float(fill_t))


def label_fill(tape: dict[str, Any], trade: dict[str, Any]) -> dict[str, Any]:
    sym = str(trade["symbol"])
    fill_t = float(trade["entry_fill_t"])
    fill_px = float(trade["entry_fill_price"])
    flatten_t = float(tape["flatten_t"])
    rec = dict(trade)
    ask_t, ask_px = None, None
    if sym in (tape.get("ask") or {}):
        ask_t, ask_px = _x1_ask(tape, sym, fill_t)
    rec["x1_ask_t"] = ask_t
    rec["x1_ask_px"] = ask_px
    rec["X1_FILL_COMPATIBLE"] = (
        ask_px is not None and abs(float(ask_px) - float(fill_px)) <= max(1e-6, abs(float(fill_px)) * 1e-9)
    )
    mid = (tape.get("mid") or {}).get(sym) or {"t": np.asarray([], dtype=float), "bid": np.asarray([]), "ask": np.asarray([])}
    rec["ENTRY_MID"] = last_le_mid(mid["t"], mid["bid"], mid["ask"], fill_t)
    stand_ix = _stand_ix(tape.get("standing") or {})
    bid = (tape.get("bid") or {}).get(sym) or {"t": np.asarray([], dtype=float), "px": np.asarray([], dtype=float)}
    for h in FIXED_HORIZONS:
        target = fill_t + float(h) * 60.0
        if target > flatten_t + 1e-9:
            rec[f"exec_yen100_h{h}"] = None
            rec[f"mid_yen100_h{h}"] = None
            rec[f"cross_tax_yen100_h{h}"] = None
            rec[f"bid_h{h}"] = None
            rec[f"TARGET_MID_H{h}"] = None
            continue
        sh = (stand_ix.get(round(target, 6)) or {}).get(sym)
        _bt, bp = resolve_exit_h(sh, bid["t"], bid["px"], fill_t, int(h))
        rec[f"bid_h{h}"] = bp
        exec_m = (float(bp) - float(fill_px)) * 100.0 if bp is not None else None
        rec[f"exec_yen100_h{h}"] = exec_m
        tmid = first_in_mid(mid["t"], mid["bid"], mid["ask"], target, target + 60.0)
        rec[f"TARGET_MID_H{h}"] = tmid
        emid = rec.get("ENTRY_MID")
        mid_m = (float(tmid) - float(emid)) * 100.0 if (tmid is not None and emid is not None) else None
        rec[f"mid_yen100_h{h}"] = mid_m
        rec[f"cross_tax_yen100_h{h}"] = (
            float(mid_m) - float(exec_m) if (mid_m is not None and exec_m is not None) else None
        )
    return rec


def universe_median_exec(tape: dict[str, Any], fill_t: float, h: int) -> Optional[float]:
    flatten_t = float(tape["flatten_t"])
    target = float(fill_t) + float(h) * 60.0
    if target > flatten_t + 1e-9:
        return None
    stand_ix = _stand_ix(tape.get("standing") or {})
    vals: list[float] = []
    for sym in list(tape.get("universe") or []):
        ask = (tape.get("ask") or {}).get(sym)
        bid = (tape.get("bid") or {}).get(sym)
        if not ask or not bid:
            continue
        st = (stand_ix.get(round(float(fill_t), 6)) or {}).get(sym) or {}
        _et, ep = resolve_entry(st, ask["t"], ask["px"], float(fill_t))
        if ep is None:
            continue
        sh = (stand_ix.get(round(target, 6)) or {}).get(sym)
        _bt, bp = resolve_exit_h(sh, bid["t"], bid["px"], float(fill_t), int(h))
        if bp is None:
            continue
        vals.append((float(bp) - float(ep)) * 100.0)
    if not vals:
        return None
    return float(np.median(vals))


def attach_excess(labeled: list[dict[str, Any]], tapes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    cache: dict[tuple[str, float, int], Optional[float]] = {}
    out: list[dict[str, Any]] = []
    for rec in labeled:
        day = str(rec["date"])
        tape = tapes.get(day)
        row = dict(rec)
        if not tape:
            for h in FIXED_HORIZONS:
                row[f"excess_yen100_h{h}"] = None
            out.append(row)
            continue
        fill_t = float(rec["entry_fill_t"])
        for h in FIXED_HORIZONS:
            key = (day, round(fill_t, 6), int(h))
            if key not in cache:
                cache[key] = universe_median_exec(tape, fill_t, int(h))
            med = cache[key]
            own = rec.get(f"exec_yen100_h{h}")
            row[f"universe_median_yen100_h{h}"] = med
            row[f"excess_yen100_h{h}"] = (
                float(own) - float(med) if (own is not None and med is not None) else None
            )
        out.append(row)
    return out
