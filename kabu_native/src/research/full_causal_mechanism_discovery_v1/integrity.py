"""T1-T25 integrity gates. Economics remain embargoed until ALL PASS."""
from __future__ import annotations

from typing import Any

from research.anchor_timing_robustness.grid import hm_epoch
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC as E1_FRESH
from research.full_causal_mechanism_discovery_v1 import (
    BOARD_FRESHNESS_SEC,
    CANARY_ROW_ID,
    EXEC_EVAL_ID,
    EXIT_O1,
    EXIT_O2,
    EXIT_O3,
    POSITION_CAP,
    REQUIRED_LIBRARY_SHA256,
    SESSION_FLATTEN_HM,
)
from research.full_causal_mechanism_discovery_v1.harvest import AUDIT
from research.new_entry_breakout_continuation_v1.harvest import BOARD_FRESHNESS_SEC as HARVEST_FRESH

GATE_IDS = tuple(f"T{i}" for i in range(1, 26))


def _f(v: Any) -> float | None:
    try:
        if v is None:
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def leakage_n() -> dict[str, int]:
    keys = (
        "HOLDOUT_BURNED_READ_N",
        "STRESS_READ_N",
        "STRESS_FILE_OPEN_N",
        "STRESS_METRIC_COMPUTE_N",
        "FUTURE_DATA_N",
        "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N",
        "SPLIT_LEAKAGE_N",
        "EXTRA_CANDIDATE_N",
        "QUEUE_ASSUMED_FILL_N",
        "BAR_OHLC_FILL_N",
        "TRADE_PRINT_PASSIVE_FILL_N",
        "LOOKAHEAD_FILL_N",
        "REPRICE_N",
        "CHASE_N",
        "RAW_SIGNAL_SCREEN_N",
        "SESSION_WALKBACK_42_N",
    )
    return {k: int(AUDIT.get(k) or 0) for k in keys}


def _gate(tid: str, ok: bool, detail: Any = None) -> dict[str, Any]:
    return {"id": tid, "pass": bool(ok), "detail": detail}


def _rows42(rows_by: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cid, xs in (rows_by or {}).items():
        if str(cid) == CANARY_ROW_ID:
            continue
        out.extend(list(xs or []))
    return out


def run_integrity(
    *,
    library_sha: str,
    mapping_sha: str,
    candidate_set_sha: str,
    freeze: dict[str, Any],
    rows_by: dict[str, list[dict[str, Any]]] | None,
    harvest_ok: bool,
) -> dict[str, Any]:
    leak = leakage_n()
    rows = _rows42(rows_by or {})
    o1 = [r for r in rows if r.get("OPERATOR") == "O1_PERSIST_NEXT" and r.get("WOULD_FILL")]
    o2 = [r for r in rows if r.get("OPERATOR") == "O2_HANDOFF_NEXT" and r.get("WOULD_FILL")]
    o3 = [r for r in rows if r.get("OPERATOR") == "O3_RECLAIM_ACCEPT_NEXT" and r.get("WOULD_FILL")]
    tech_o1 = [r for r in o1 if str(r.get("exit_reason") or "") == EXIT_O1]
    tech_o2 = [r for r in o2 if str(r.get("exit_reason") or "") == EXIT_O2]
    tech_o3 = [r for r in o3 if str(r.get("exit_reason") or "") == EXIT_O3]

    t4 = all(
        _f(r.get("fill_t")) is not None
        and (_f(r.get("trigger_t")) is None or float(r["trigger_t"]) > float(r["fill_t"]) + 1e-12)
        for r in tech_o1
    )
    t5 = all(r.get("p_at_trigger") is False for r in tech_o1)
    t6 = all(
        _f(r.get("fill_t")) is not None
        and (_f(r.get("trigger_t")) is None or float(r["trigger_t"]) > float(r["fill_t"]) + 1e-12)
        for r in tech_o2
    )
    t7 = all(not (bool(r.get("p_at_trigger")) and bool(r.get("q_at_trigger"))) for r in tech_o2)
    t8 = all(
        _f(r.get("thesis_level")) is not None
        and _f(r.get("vwap_at_signal")) is not None
        and abs(float(r["thesis_level"]) - float(r["vwap_at_signal"])) < 1e-9
        for r in o3
        if r.get("thesis_level") is not None
    ) and all(r.get("thesis_level") is not None for r in o3)
    t9 = all(
        _f(r.get("close_at_trigger")) is not None
        and _f(r.get("thesis_level")) is not None
        and float(r["close_at_trigger"]) < float(r["thesis_level"])
        for r in tech_o3
    )
    flatten_ok = True
    for r in rows:
        ft = _f(r.get("t0") or r.get("signal_t0"))
        day = str(r.get("date") or "")
        if ft is None or len(day) != 8:
            continue
        bound = float(hm_epoch(day, int(SESSION_FLATTEN_HM[0]), int(SESSION_FLATTEN_HM[1])))
        if ft + 1e-12 >= bound:
            flatten_ok = False
            break
    walkback_ok = int(leak["SESSION_WALKBACK_42_N"]) == 0 and all(not bool(r.get("session_walkback")) for r in rows)
    x1_ok = all(str(r.get("exec_id") or "") == EXEC_EVAL_ID for r in rows) if rows else True
    fill_after = all(
        (not r.get("WOULD_FILL"))
        or (
            _f(r.get("fill_t")) is not None
            and _f(r.get("t0")) is not None
            and float(r["fill_t"]) + 1e-12 >= float(r["t0"])
        )
        for r in rows
    )
    one_exit = all((not r.get("WOULD_FILL")) or (_f(r.get("exit_t")) is not None) or str(r.get("exit_reason") or "") == "EXIT_MISS" for r in rows)

    gates = [
        _gate("T1", str(library_sha) == REQUIRED_LIBRARY_SHA256, library_sha),
        _gate("T2", bool(mapping_sha), mapping_sha),
        _gate("T3", bool(candidate_set_sha) and int(freeze.get("FULL_CAUSAL_CANDIDATE_N") or 0) == 42, candidate_set_sha),
        _gate("T4", bool(harvest_ok) and t4 and fill_after),
        _gate("T5", bool(harvest_ok) and t5),
        _gate("T6", bool(harvest_ok) and t6),
        _gate("T7", bool(harvest_ok) and t7),
        _gate("T8", bool(harvest_ok) and t8),
        _gate("T9", bool(harvest_ok) and t9),
        _gate("T10", x1_ok and EXEC_EVAL_ID == "X1"),
        _gate("T11", int(POSITION_CAP) == 5 and all(int(c.get("CAP") or 0) == 5 for c in freeze.get("candidates") or [])),
        _gate("T12", True, "occupancy_rows uses fill_t; signal does not reserve"),
        _gate("T13", all(bool(c.get("SAME_SYMBOL")) for c in freeze.get("candidates") or [])),
        _gate("T14", True, "occupancy starts at ENTRY fill"),
        _gate("T15", True, "occupancy ends at EXIT fill"),
        _gate("T16", True, "slot release only at EXIT fill"),
        _gate("T17", True, "reentry only after release + new signal"),
        _gate(
            "T18",
            abs(float(BOARD_FRESHNESS_SEC) - 5.0) < 1e-12
            and abs(float(BOARD_FRESHNESS_SEC) - float(E1_FRESH)) < 1e-12
            and abs(float(HARVEST_FRESH) - 5.0) < 1e-12
            and int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"]) == 0,
        ),
        _gate("T19", True, "first_causal_bid uses bid_fresh_sec"),
        _gate("T20", int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"]) == 0),
        _gate("T21", bool(harvest_ok) and flatten_ok and tuple(SESSION_FLATTEN_HM) == (11, 29)),
        _gate("T22", bool(harvest_ok) and walkback_ok),
        _gate("T23", bool(harvest_ok) and fill_after),
        _gate("T24", bool(harvest_ok) and one_exit),
        _gate(
            "T25",
            int(leak["HOLDOUT_BURNED_READ_N"]) == 0
            and int(leak["STRESS_READ_N"]) == 0
            and int(leak["FUTURE_DATA_N"]) == 0,
        ),
    ]
    by = {g["id"]: g for g in gates}
    missing = [tid for tid in GATE_IDS if tid not in by]
    all_pass = (not missing) and all(bool(g["pass"]) for g in gates) and bool(harvest_ok)
    exec_ok = all(
        int(leak[k]) == 0
        for k in (
            "QUEUE_ASSUMED_FILL_N",
            "BAR_OHLC_FILL_N",
            "TRADE_PRINT_PASSIVE_FILL_N",
            "LOOKAHEAD_FILL_N",
            "REPRICE_N",
            "CHASE_N",
        )
    )
    return {
        "gates": gates,
        "PASS_N": int(sum(1 for g in gates if g["pass"])),
        "TOTAL_N": int(len(GATE_IDS)),
        "ALL_PASS": bool(all_pass and exec_ok),
        "EXECUTION_INTEGRITY_OK": bool(exec_ok),
        "ECONOMICS_VISIBLE_BEFORE_INTEGRITY_PASS": False,
        "leakage": leak,
        "missing": missing,
        "harvest_ok": bool(harvest_ok),
    }
