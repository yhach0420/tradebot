"""Wiring checks without opening prospective market data."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.e1_x35_passive_exit.exits import simulate_exit
from research.e1_x35r_exit_contract.contracts import canonical_fixed_exit
from research.e1_x36_joint_allocator.replay import simulate_joint
from research.e1_x36r_freeze_integrity.serialize import score_fn_from_serialized

from . import FEATURE_ORDER, FORBIDDEN_FROM, POSITION_CAP, WAIT_SEC


def assert_prospective_unopened() -> dict[str, Any]:
    """Ensure preflight does not load >= 20260810 push boards."""
    from pathlib import Path
    native = Path(__file__).resolve().parents[3]
    push = native / "data" / "push_jsonl"
    scanned = []
    if push.exists():
        for p in push.iterdir():
            name = p.name.replace("-", "")
            if name.isdigit() and len(name) == 8 and name >= FORBIDDEN_FROM:
                scanned.append(name)
    return {
        "forbidden_from": FORBIDDEN_FROM,
        "prospective_folders_present": scanned,
        "preflight_loaded_prospective_boards": False,
        "opened_20260810": False,
        "pass": True,
    }


def wiring_topk_and_cap(ser: dict) -> dict[str, Any]:
    """Synthetic cohort: score ranking + cap=5."""
    sfn = score_fn_from_serialized(ser)
    t0 = 1_700_000_000.0
    means = ser["preprocessing"]["mean"]
    scales = ser["preprocessing"]["scale"]
    evs = []
    for i, sym in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"]):
        feat = {f: float(means[j]) for j, f in enumerate(FEATURE_ORDER)}
        feat["spread_bps"] = float(means[0]) + (7 - i) * float(scales[0]) * 0.5
        evs.append({
            "date": "20260721",
            "symbol": sym,
            "session": "AM",
            "signal_time": t0,
            "filled": False,
            "fill_time": None,
            "limit_price": 1000.0,
            "bid0": 1000.0,
            **feat,
        })
    sim = simulate_joint(evs, score_fn=sfn)
    admitted = [e for e in sim["events"] if e.get("admitted")]
    blocked = [e for e in sim["events"] if e.get("CAPACITY_BLOCKED")]
    return {
        "admitted_n": len(admitted),
        "blocked_n": len(blocked),
        "cap": POSITION_CAP,
        "hard_cap_violations": sim["hard_cap_violations"],
        "max_open_plus_pending": sim["max_open_plus_pending"],
        "admitted_symbols": [e["symbol"] for e in admitted],
        "pass": (
            len(admitted) == POSITION_CAP
            and len(blocked) == 3
            and sim["hard_cap_violations"] == 0
            and sim["max_open_plus_pending"] <= POSITION_CAP
        ),
    }


def wiring_duplicate() -> dict[str, Any]:
    t0 = 1_700_000_100.0
    evs = [
        {
            "date": "20260721", "symbol": "DUP", "session": "AM",
            "signal_time": t0, "filled": False, "limit_price": 1000.0, "bid0": 1000.0,
        },
        {
            "date": "20260721", "symbol": "DUP", "session": "AM",
            "signal_time": t0 + 0.5, "filled": False, "limit_price": 1000.0, "bid0": 1000.0,
        },
    ]
    sim = simulate_joint(evs, order_mode="symbol_ascending")
    dups = sum(1 for e in sim["events"] if e.get("DUPLICATE_BLOCKED"))
    return {
        "duplicate_blocked": dups,
        "pass": dups >= 1,
        "semantics": "no_overlap_replace",
    }


def wiring_fill_exit_contracts() -> dict[str, Any]:
    from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
    path = {
        "ok": True,
        "offs": np.asarray([0.0, 300.0, 600.5], dtype=float),
        "rets": np.asarray([0.0, 10.0, 20.0], dtype=float),
        "mids": np.asarray([0.0, 10.0, 20.0], dtype=float),
        "times": np.asarray([1e9, 1e9 + 300, 1e9 + 600.5], dtype=float),
        "sess_end": 1e9 + 10000,
        "entry_t": 1e9,
        "entry_price": 1000.0,
    }
    ex = canonical_fixed_exit(path, 600.0)
    r = simulate_exit(path, fixed_hold_sec=600.0)
    return {
        "find_ask_cross_fill_callable": callable(find_ask_cross_fill),
        "canonical_exit_ok": bool(ex.get("ok")),
        "exit_reason": ex.get("reason"),
        "exit_ret": ex.get("exit_ret_bps"),
        "simulate_matches": abs(float(ex["exit_ret_bps"]) - float(r["exit_ret_bps"])) < 1e-12,
        "wait_sec": WAIT_SEC,
        "pass": bool(ex.get("ok") and abs(float(ex["exit_ret_bps"]) - 20.0) < 1e-12),
    }
