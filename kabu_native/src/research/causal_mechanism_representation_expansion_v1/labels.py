"""Discovery-unit labels on raw signals. Ask1 at/after signal_t0, Bid1 at H. No CAP. No strategy EXIT."""
from __future__ import annotations

import gzip
import pickle
import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.causal_mechanism_representation_expansion_v1 import (
    BURNED_HOLDOUT_DAYS,
    DEVELOPMENT_DAYS,
    FIXED_HORIZONS,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    STRESS_DAYS,
)
from research.causal_mechanism_representation_expansion_v1.isolation import CACHE, RESEARCH_ROOT, TODAY
from research.discovery_search_space_reassessment_v1.labels import universe_median_exec
from research.profitable_move_mechanism_discovery_v1.harvest import resolve_entry, resolve_exit_h

REASSESS_CACHE = RESEARCH_ROOT / "_work" / "discovery_search_space_reassessment_v1"

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


def _load_gz(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with gzip.open(path, "rb") as fh:
        got = pickle.load(fh)
    return got if isinstance(got, dict) else {}


def _stand_ix(standing: dict[Any, Any]) -> dict[float, dict[str, Any]]:
    return {round(float(k), 6): v for k, v in (standing or {}).items()}


def load_tape(day: str) -> dict[str, Any]:
    assert_dev_day(day)
    matches = sorted(REASSESS_CACHE.glob(f"tape_{day}_*.pkl.gz"))
    if not matches:
        raise RuntimeError(f"TAPE_MISSING {day}")
    body = _load_gz(matches[-1])
    if body.get("date") != str(day) or body.get("ask") is None:
        raise RuntimeError(f"TAPE_INVALID {day}")
    return body


def label_signal(tape: dict[str, Any], sig: dict[str, Any]) -> dict[str, Any]:
    sym = str(sig["symbol"])
    t0 = float(sig["signal_t0"])
    flatten_t = float(tape["flatten_t"])
    rec = dict(sig)
    rec["T_i"] = t0
    stand_ix = _stand_ix(tape.get("standing") or {})
    st = (stand_ix.get(round(t0, 6)) or {}).get(sym) or {}
    ask = (tape.get("ask") or {}).get(sym) or {"t": np.asarray([], dtype=float), "px": np.asarray([], dtype=float)}
    et, ep = resolve_entry(st, ask["t"], ask["px"], t0)
    rec["entry_t"] = et
    rec["entry_ask"] = ep
    bid = (tape.get("bid") or {}).get(sym) or {"t": np.asarray([], dtype=float), "px": np.asarray([], dtype=float)}
    for h in FIXED_HORIZONS:
        target = t0 + float(h) * 60.0
        if target > flatten_t + 1e-9 or ep is None:
            rec[f"bid_h{h}"] = None
            rec[f"markout_yen100_h{h}"] = None
            rec[f"markout_bps_h{h}"] = None
            continue
        sh = (stand_ix.get(round(target, 6)) or {}).get(sym)
        _xt, xp = resolve_exit_h(sh, bid["t"], bid["px"], t0, int(h))
        rec[f"bid_h{h}"] = xp
        yen = (float(xp) - float(ep)) * 100.0 if xp is not None else None
        rec[f"markout_yen100_h{h}"] = yen
        rec[f"markout_bps_h{h}"] = (
            (float(xp) - float(ep)) / float(ep) * 10000.0 if (xp is not None and ep > 0) else None
        )
    rec["complete_triple"] = all(rec.get(f"markout_yen100_h{h}") is not None for h in FIXED_HORIZONS)
    return rec


def attach_universe_excess(labeled: list[dict[str, Any]], tapes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
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
        t0 = float(rec["signal_t0"])
        for h in FIXED_HORIZONS:
            key = (day, round(t0, 6), int(h))
            if key not in cache:
                cache[key] = universe_median_exec(tape, t0, int(h))
            med = cache[key]
            own = rec.get(f"markout_yen100_h{h}")
            row[f"universe_median_yen100_h{h}"] = med
            row[f"excess_yen100_h{h}"] = (
                float(own) - float(med) if (own is not None and med is not None) else None
            )
        out.append(row)
    return out


def label_cohort(signals: list[dict[str, Any]], tapes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sig in signals:
        tape = tapes.get(str(sig["date"]))
        if not tape:
            continue
        rows.append(label_signal(tape, sig))
    return attach_universe_excess(rows, tapes)
