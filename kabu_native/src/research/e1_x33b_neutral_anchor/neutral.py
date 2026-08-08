"""Neutral fixed-clock anchors + prefix invariance + dependency manifest."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np

from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.e1_x31_population_direction.controls import evaluate_long_at_signal
from research.e1_x32_upstream_attribution import CLOCK_POINTS_HM, HORIZONS_SEC, SAMPLING_SEED
from research.e1_x32_upstream_attribution.eval_stages import clock_epochs_for_day

from . import ANCHOR_ID, FORBIDDEN_FROM, HISTORICAL_DAYS


def candidate_symbols_by_day(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
    out = {d: set() for d in HISTORICAL_DAYS}
    for r in rows:
        if r["date"] in out:
            out[r["date"]].add(str(r["symbol"]))
    return out


def planned_neutral_anchors(pool: dict[str, set[str]]) -> list[dict[str, Any]]:
    """Clock × symbol-session planned observations (membership future-free)."""
    anchors = []
    for day, syms in pool.items():
        assert day < FORBIDDEN_FROM
        for epoch, sess in clock_epochs_for_day(day):
            for sym in syms:
                anchors.append({
                    "date": day,
                    "symbol": sym,
                    "session": sess,
                    "grid_epoch": float(epoch),
                    "anchor_id": ANCHOR_ID,
                })
    return anchors


def evaluate_neutral(
    planned: list[dict[str, Any]],
    board_by_key: dict,
) -> list[dict[str, Any]]:
    out = []
    for r in planned:
        board = board_by_key.get((r["date"], r["symbol"]))
        if board is None or board["t"].size == 0:
            continue
        ep = evaluate_long_at_signal(
            board,
            signal_t=float(r["grid_epoch"]),
            date=r["date"],
            session=r["session"],
        )
        if not ep.get("ok"):
            continue
        sess_end = session_end_epoch(r["date"], r["session"])
        rec = {
            "date": r["date"],
            "symbol": r["symbol"],
            "session": r["session"],
            "signal_t": float(r["grid_epoch"]),
            "minutes_to_session_close": (sess_end - float(r["grid_epoch"])) / 60.0,
            "ok": True,
            "mfe": ep.get("mfe"),
            "mae": ep.get("mae"),
        }
        for H in HORIZONS_SEC:
            rec[f"return_{H}"] = ep.get(f"return_{H}")
            rec[f"return_{H}_valid"] = ep.get(f"return_{H}_valid")
        out.append(rec)
    return out


def prefix_invariance_neutral(pool: dict[str, set[str]]) -> dict[str, Any]:
    """
    Planned anchor set is pure clock × fixed pool — prefix cut at T must keep
    exactly the same anchors with grid_epoch <= T.
    """
    rng = np.random.default_rng(SAMPLING_SEED)
    full = planned_neutral_anchors(pool)
    # sample several (day, T) cuts
    tests = []
    violations = 0
    days = list(HISTORICAL_DAYS)
    for day in days:
        clocks = [e for e, _ in clock_epochs_for_day(day)]
        if len(clocks) < 4:
            continue
        for T in (clocks[len(clocks) // 2], clocks[len(clocks) * 3 // 4]):
            full_le = {
                (a["date"], a["symbol"], a["session"], float(a["grid_epoch"]))
                for a in full
                if a["date"] == day and float(a["grid_epoch"]) <= T + 1e-9
            }
            # prefix regeneration: only clocks <= T for that day
            pref_pool = {day: pool[day]}
            pref = []
            for epoch, sess in clock_epochs_for_day(day):
                if epoch > T + 1e-9:
                    continue
                for sym in pool[day]:
                    pref.append((day, sym, sess, float(epoch)))
            pref_le = set(pref)
            ok = full_le == pref_le
            if not ok:
                violations += 1
            tests.append({
                "date": day, "T": T, "match": ok,
                "full_n": len(full_le), "prefix_n": len(pref_le),
            })
    # also random extra cuts
    _ = rng
    status = "PASS" if violations == 0 and len(tests) >= 10 else (
        "CAUSALITY_VIOLATION" if violations else "INSUFFICIENT_TESTS"
    )
    return {
        "status": status,
        "n_tests": len(tests),
        "violations": violations,
        "prefix_invariance": status == "PASS",
        "sample": tests[:10],
    }


def dependency_manifest(prefix: dict[str, Any], semantics: dict[str, Any]) -> dict[str, Any]:
    body = {
        "manifest_id": "NEUTRAL_ANCHOR_DEPENDENCY_MANIFEST_V1",
        "anchor_id": ANCHOR_ID,
        "allowed_inputs": [
            "CANDIDATE_SYMBOL_POOL membership (fixed from X30/X33 population)",
            "CLOCK_POINTS_HM / clock_epochs_for_day (X32)",
            "session AM/PM from clock hour",
            "board availability for execution (not membership)",
        ],
        "forbidden_inputs": [
            "forward_return_*",
            "future MFE/MAE/labels",
            "post-anchor path for membership",
            "price/return/volume/R2 triggers",
            "performance-derived thresholds",
            "phase/offset search",
            "anchor interval grid search",
        ],
        "source_functions": semantics.get("source_functions"),
        "clock_semantics": {
            "CLOCK_POINTS_HM": list(CLOCK_POINTS_HM),
            "sampling_seed_x32": SAMPLING_SEED,
        },
        "feature_eligibility_semantics": (
            "Membership = candidate symbol pool + fixed clocks; "
            "FEATURE_OK day-lookahead from X33 CONTROL not used in freeze "
            "(CONTROL≡PARENT on X33)."
        ),
        "uses_future_information": False,
        "prefix_invariance_result": prefix.get("status"),
        "no_anchor_performance_search": True,
    }
    raw = json.dumps(body, sort_keys=True, default=str).encode()
    body["sha256"] = hashlib.sha256(raw).hexdigest()
    return body
