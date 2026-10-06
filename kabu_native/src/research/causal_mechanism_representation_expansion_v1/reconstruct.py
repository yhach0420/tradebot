"""Reconstruct raw P1/P2 historical signals from frozen definitions. No CAP. No occupancy. No strategy PnL."""
from __future__ import annotations

import gzip
import json
import pickle
import sys
import time
from pathlib import Path
from typing import Any, Optional

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from _p1_inventory import resolve_universe
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    find_capture_dir,
    iter_push,
    record_event_stamp,
)
from research.causal_mechanism_representation_expansion_v1 import (
    BURNED_HOLDOUT_DAYS,
    DEVELOPMENT_DAYS,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    P1_ID,
    P2_ID,
    STRESS_DAYS,
)
from research.causal_mechanism_representation_expansion_v1.isolation import CACHE, RESEARCH_ROOT, TODAY
from research.causal_mechanism_representation_expansion_v1.operators import reclaim_accept_indices
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC as E1_FRESH
from research.new_entry_breakout_continuation_v1.harvest import BOARD_FRESHNESS_SEC, board_row
from research.recovery_sequence_full_strategy_architecture_v1.entries import session_vwap_arr
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from research.systematic_state_transition_full_strategy_v1.entries import canary_r2_indices, library_signal_indices
from research.systematic_state_transition_full_strategy_v1.spec import frozen_library
from research.systematic_state_transition_full_strategy_v1.states import indicators
from small_paper.v1r_live_dual_lane import session_end_for_position

ST_CACHE = RESEARCH_ROOT / "_work" / "systematic_state_transition_full_strategy_v1"

AUDIT = {
    "HOLDOUT_BURNED_READ_N": 0,
    "STRESS_READ_N": 0,
    "FUTURE_DATA_N": 0,
    "CAP_APPLIED_N": 0,
    "OCCUPANCY_APPLIED_N": 0,
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


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def identity_key(row: dict[str, Any]) -> tuple[str, str, int, float]:
    t0 = row.get("signal_t0")
    if t0 is None:
        t0 = row.get("t0")
    return (str(row["date"]), str(row["symbol"]), int(row["i"]), round(float(t0), 6))


def _p2_candidate() -> dict[str, Any]:
    for cand in frozen_library():
        if str(cand["CANDIDATE_ID"]) == P2_ID:
            return dict(cand)
    raise RuntimeError("P2_CANDIDATE_MISSING")


def load_historical_raw(cid: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for day in DEVELOPMENT_DAYS:
        path = ST_CACHE / f"DEVELOPMENT_{day}_grid.json"
        body = _load_json(path)
        if not body.get("ok") or str(body.get("date") or "") != str(day):
            raise RuntimeError(f"HISTORICAL_RAW_MISSING {day} {cid}")
        for r in list((body.get("rows_by") or {}).get(cid) or []):
            t0 = r.get("signal_t0")
            if t0 is None:
                t0 = r.get("t0")
            out.append(
                {
                    "date": str(r.get("date") or day),
                    "symbol": str(r.get("symbol") or ""),
                    "i": int(r["i"]),
                    "signal_t0": float(t0),
                    "candidate_id": str(r.get("candidate_id") or cid),
                    "source": "HISTORICAL_ST_HARVEST",
                }
            )
    return out


def recon_path(day: str) -> Path:
    return CACHE / f"raw_signals_{day}.pkl.gz"


def _emit(day: str, symbol: str, i: int, t0: float, cid: str) -> dict[str, Any]:
    return {
        "date": str(day),
        "symbol": str(symbol),
        "i": int(i),
        "signal_t0": float(t0),
        "candidate_id": str(cid),
        "source": "FROZEN_DEFINITION_RECONSTRUCT",
    }


def reconstruct_day(day: str, *, use_cache: bool = True) -> dict[str, Any]:
    assert_dev_day(day)
    path = recon_path(day)
    if use_cache:
        prev = _load_gz(path)
        if prev.get("date") == str(day) and prev.get("p1") is not None and prev.get("p2") is not None:
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
    if abs(float(BOARD_FRESHNESS_SEC) - float(E1_FRESH)) > 1e-12:
        raise RuntimeError("BOARD_FRESHNESS_DRIFT")
    p2_cand = _p2_candidate()
    am_start = float(hm_epoch(day, 9, 0))
    am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
    builders: dict[str, SymbolBarBuilder] = {s: SymbolBarBuilder(am_start=am_start, am_end=am_end) for s in symbols}
    uni_set = set(symbols)
    n_rec = 0
    t0w = time.time()
    print(f"RECON_DAY_START {day} universe_n={len(symbols)}", flush=True)
    for rec in iter_push(cap):
        sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
        if not sym or sym not in uni_set:
            continue
        pay = dict(rec.get("payload") or rec.get("original_payload") or {})
        et = capture_event_epoch(rec, pay)
        if et is None:
            continue
        if float(et) < am_start - 120.0:
            continue
        if float(et) > am_end + 2.0:
            continue
        recv = record_event_stamp(rec)
        if recv:
            pay["received_at"] = recv
        n_rec += 1
        row = board_row(rec, pay, float(et))
        builders[sym].on_event(
            et=float(et),
            px=row["px"] if row["px"] == row["px"] else None,
            cum_vol=row.get("cum_vol"),
            bid=row["bid"] if row["bid"] == row["bid"] else None,
            ask=row["ask"] if row["ask"] == row["ask"] else None,
            continuous=bool(row.get("continuous")),
        )
        if n_rec % 400000 == 0:
            print(f"RECON_DAY_PROG {day} n={n_rec} sec={time.time()-t0w:.1f}", flush=True)
    p1: list[dict[str, Any]] = []
    p2: list[dict[str, Any]] = []
    r1: list[dict[str, Any]] = []
    r3: list[dict[str, Any]] = []
    for s in symbols:
        builders[s].close_session()
        raw = builders[s].as_arrays()
        integ = bar_integrity(raw, am_start=am_start, am_end=am_end)
        if int(integ.get("FUTURE_BAR_N") or 0):
            raise RuntimeError(f"FUTURE_BAR {day} {s}")
        if not integ.get("ok"):
            continue
        n = int(raw["close"].size)
        if n == 0:
            continue
        vwap = session_vwap_arr(raw["close"], raw["volume"], raw.get("vwap_num"))
        ind = indicators(raw)
        for i in canary_r2_indices(raw["open"], raw["high"], raw["low"], raw["close"], raw["volume"], vwap):
            p1.append(_emit(day, s, i, float(raw["finalize_t"][i]), P1_ID))
        for i in library_signal_indices(ind, p2_cand):
            p2.append(_emit(day, s, i, float(raw["finalize_t"][i]), P2_ID))
        for i in reclaim_accept_indices("R1", raw["open"], raw["high"], raw["low"], raw["close"], raw["volume"], vwap):
            r1.append(_emit(day, s, i, float(raw["finalize_t"][i]), "O3_RECLAIM_ACCEPT_NEXT__R1"))
        for i in reclaim_accept_indices("R3", raw["open"], raw["high"], raw["low"], raw["close"], raw["volume"], vwap):
            r3.append(_emit(day, s, i, float(raw["finalize_t"][i]), "O3_RECLAIM_ACCEPT_NEXT__R3"))
    out = {
        "date": str(day),
        "universe": symbols,
        "universe_source": src,
        "record_n": n_rec,
        "elapsed_sec": float(time.time() - t0w),
        "p1": p1,
        "p2": p2,
        "r1": r1,
        "r3": r3,
        "CAP_APPLIED": False,
        "OCCUPANCY_APPLIED": False,
    }
    _dump_gz(path, out)
    print(
        f"RECON_DAY_DONE {day} p1={len(p1)} p2={len(p2)} r1={len(r1)} r3={len(r3)} sec={time.time()-t0w:.1f}",
        flush=True,
    )
    return out


def reconstruct_all(*, use_cache: bool = True) -> dict[str, list[dict[str, Any]]]:
    p1: list[dict[str, Any]] = []
    p2: list[dict[str, Any]] = []
    r1: list[dict[str, Any]] = []
    r3: list[dict[str, Any]] = []
    for day in DEVELOPMENT_DAYS:
        body = reconstruct_day(str(day), use_cache=use_cache)
        p1.extend(list(body.get("p1") or []))
        p2.extend(list(body.get("p2") or []))
        r1.extend(list(body.get("r1") or []))
        r3.extend(list(body.get("r3") or []))
    return {"P1": p1, "P2": p2, "R1": r1, "R3": r3}


def parity_stats(gold: list[dict[str, Any]], recon: list[dict[str, Any]]) -> dict[str, Any]:
    g = {identity_key(r) for r in gold}
    r = {identity_key(x) for x in recon}
    inter = g & r
    prec = (float(len(inter)) / float(len(r))) if r else 0.0
    rec = (float(len(inter)) / float(len(g))) if g else 0.0
    return {
        "GOLD_N": int(len(g)),
        "RECON_N": int(len(r)),
        "INTERSECTION_N": int(len(inter)),
        "GOLD_ONLY_N": int(len(g - r)),
        "RECON_ONLY_N": int(len(r - g)),
        "SIGNAL_IDENTITY_PRECISION": float(prec),
        "SIGNAL_IDENTITY_RECALL": float(rec),
        "PARITY_PASS": bool(g) and abs(prec - 1.0) < 1e-12 and abs(rec - 1.0) < 1e-12,
    }


def reconstruct_controls(*, use_cache: bool = True) -> dict[str, Any]:
    gold_p1 = load_historical_raw(P1_ID)
    gold_p2 = load_historical_raw(P2_ID)
    recon = reconstruct_all(use_cache=use_cache)
    p1 = parity_stats(gold_p1, recon["P1"])
    p2 = parity_stats(gold_p2, recon["P2"])
    ok = bool(p1.get("PARITY_PASS")) and bool(p2.get("PARITY_PASS"))
    return {
        "ok": ok,
        "P1": {
            **p1,
            "CONTROL_ID": P1_ID,
            "RAW_SIGNAL_N": int(len(recon["P1"])),
            "signals": recon["P1"] if ok else [],
            "gold_n_rows": int(len(gold_p1)),
        },
        "P2": {
            **p2,
            "CONTROL_ID": P2_ID,
            "RAW_SIGNAL_N": int(len(recon["P2"])),
            "signals": recon["P2"] if ok else [],
            "gold_n_rows": int(len(gold_p2)),
        },
        "R1_SIGNAL_N": int(len(recon["R1"])),
        "R3_SIGNAL_N": int(len(recon["R3"])),
        "r1_signals": recon["R1"],
        "r3_signals": recon["R3"],
        "CAP_APPLIED": False,
        "OCCUPANCY_APPLIED": False,
        "AUDIT": dict(AUDIT),
    }
