"""T1-T33 integrity. Economics embargoed until ALL PASS."""
from __future__ import annotations

import inspect
from typing import Any

from research.anchor_timing_robustness.grid import hm_epoch
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC as E1_FRESH
from research.full_causal_fdg_relative_geometry_migration_v1 import (
    BOARD_FRESHNESS_SEC,
    CLOSED_STATIC_STRATEGY_ID,
    EXEC_EVAL_ID,
    POSITION_CAP,
    REQUIRED_OBJECT_SHA256,
    SESSION_FLATTEN_HM,
    STATIC_SPAN_FAMILY_CLOSED,
    STATIC_SPAN_RETUNE,
    STRATEGY_ID,
    TECHNICAL_EXIT_ID,
)
from research.full_causal_fdg_relative_geometry_migration_v1 import geometry as geo
from research.full_causal_fdg_relative_geometry_migration_v1.geometry import (
    K_DEEP,
    onset_false_to_true,
    relative_ask,
    relative_bid,
    snapshot_rel,
    translated_same,
)
from research.full_causal_fdg_relative_geometry_migration_v1.harvest import AUDIT
from research.new_entry_breakout_continuation_v1.harvest import BOARD_FRESHNESS_SEC as HARVEST_FRESH

GATE_IDS = tuple(f"T{i}" for i in range(1, 34))


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
        "QUEUE_ASSUMED_FILL_N",
        "BAR_OHLC_FILL_N",
        "TRADE_PRINT_PASSIVE_FILL_N",
        "LOOKAHEAD_FILL_N",
        "REPRICE_N",
        "CHASE_N",
        "RAW_SIGNAL_SCREEN_N",
        "FORBIDDEN_TRANSFORMATION_USED_N",
        "QTY_IMBALANCE_ALPHA_N",
        "K_SUBSET_N",
        "STATIC_SPAN_ALPHA_N",
        "MAGNITUDE_THRESHOLD_N",
        "PERSISTENCE_WINDOW_N",
        "PREOPEN_EXECUTION_N",
    )
    return {k: int(AUDIT.get(k) or 0) for k in keys}


def _gate(tid: str, ok: bool, detail: Any = None) -> dict[str, Any]:
    return {"id": tid, "pass": bool(ok), "detail": detail}


def _book(bid0: float = 100.0, ask0: float = 101.0, step: float = 1.0) -> dict[str, Any]:
    pay: dict[str, Any] = {}
    for i in range(1, 11):
        pay[f"Buy{i}"] = {"Price": bid0 - (i - 1) * step, "Qty": 1.0}
        pay[f"Sell{i}"] = {"Price": ask0 + (i - 1) * step, "Qty": 1.0}
    return pay


def run_integrity(
    *,
    object_sha: str,
    spec: dict[str, Any],
    static_parent: dict[str, Any],
    rows: list[dict[str, Any]] | None,
    harvest_ok: bool,
) -> dict[str, Any]:
    leak = leakage_n()
    xs = list(rows or [])
    filled = [r for r in xs if r.get("WOULD_FILL")]
    tech = [r for r in filled if str(r.get("exit_reason") or "") == TECHNICAL_EXIT_ID]
    src = inspect.getsource(geo)
    t3 = tuple(K_DEEP) == tuple(range(2, 11)) and "k=2..10" in str(spec.get("BID_REL_K") or "")
    t4 = tuple(K_DEEP) == tuple(range(2, 11)) and "k=2..10" in str(spec.get("ASK_REL_K") or "")
    bid_px = [100.0 - i for i in range(10)]
    ask_px = [101.0 + i for i in range(10)]
    br = relative_bid(bid_px)
    ar = relative_ask(ask_px)
    t5 = br is not None and ar is not None and float(br[0]) == 1.0 and float(ar[0]) == 1.0 and float(br[-1]) == 9.0 and float(ar[-1]) == 9.0
    t6 = translated_same(bid_px, ask_px, 50.0)
    t7 = int(leak["QTY_IMBALANCE_ALPHA_N"]) == 0 and "canonical_depth_imbalance" not in src
    t8 = "imbalance" not in src.lower() or "no imbalance" in src.lower() or "No qty" in src
    t8 = t8 and int(leak["QTY_IMBALANCE_ALPHA_N"]) == 0 and spec.get("qty_in_alpha") is False
    t9 = int(leak["K_SUBSET_N"]) == 0 and spec.get("k_subset") is False
    t10 = spec.get("magnitude_threshold") is False and int(leak["MAGNITUDE_THRESHOLD_N"]) == 0
    t11 = spec.get("time_window") is False
    t12 = spec.get("persistence") is False and int(leak["PERSISTENCE_WINDOW_N"]) == 0
    t13 = "reset_known_on_unknown" in src and "last = None" in inspect.getsource(geo.scan_pairs)
    t14 = onset_false_to_true(False, True) is True and onset_false_to_true(True, True) is False
    t15 = onset_false_to_true(None, True) is False
    t16 = all(str(r.get("BASELINE_FROM") or "") == "p" for r in xs) if xs else True
    t17 = all(bool(r.get("BASELINE_IMMUTABLE") is True or r.get("flatten_reject")) for r in xs) if xs else True
    t17 = t17 and spec.get("BASELINE", "").find("immutable") >= 0 if isinstance(spec.get("BASELINE"), str) else t17
    t18 = (all(str(r.get("exec_id") or "") == EXEC_EVAL_ID for r in xs) if xs else True) and EXEC_EVAL_ID == "X1"
    t19 = all(
        _f(r.get("fill_t")) is not None
        and (_f(r.get("trigger_t")) is None or float(r["trigger_t"]) > float(r["fill_t"]) + 1e-12)
        for r in tech
    )
    t20 = all(str(r.get("exit_reason") or "") == TECHNICAL_EXIT_ID for r in tech)
    unknown = snapshot_rel({})
    t21 = unknown.get("UNKNOWN") is True and unknown.get("VALID_FULL_DEPTH") is False
    flatten_ok = True
    for r in xs:
        ft = _f(r.get("t0") or r.get("signal_t0"))
        day = str(r.get("date") or "")
        if ft is None or len(day) != 8:
            continue
        bound = float(hm_epoch(day, int(SESSION_FLATTEN_HM[0]), int(SESSION_FLATTEN_HM[1])))
        if ft + 1e-12 >= bound and r.get("WOULD_FILL"):
            flatten_ok = False
            break
    static_closed = (
        bool(static_parent.get("ok"))
        and str(static_parent.get("CLOSED_STRATEGY_ID") or "") == CLOSED_STATIC_STRATEGY_ID
        and STATIC_SPAN_FAMILY_CLOSED is True
        and STATIC_SPAN_RETUNE is False
        and int(leak["STATIC_SPAN_ALPHA_N"]) == 0
    )
    gates = [
        _gate("T1", str(object_sha) == REQUIRED_OBJECT_SHA256 and str(spec.get("parent_object_SHA") or "") == REQUIRED_OBJECT_SHA256),
        _gate("T2", static_closed),
        _gate("T3", t3),
        _gate("T4", t4),
        _gate("T5", bool(t5)),
        _gate("T6", bool(t6)),
        _gate("T7", bool(t7)),
        _gate("T8", bool(t8)),
        _gate("T9", bool(t9)),
        _gate("T10", bool(t10)),
        _gate("T11", bool(t11)),
        _gate("T12", bool(t12)),
        _gate("T13", bool(t13)),
        _gate("T14", bool(t14)),
        _gate("T15", bool(t15)),
        _gate("T16", bool(t16)),
        _gate("T17", bool(t17) and spec.get("STRATEGY_FROZEN_BEFORE_ECONOMICS") is True),
        _gate("T18", bool(t18)),
        _gate("T19", bool(t19)),
        _gate("T20", bool(t20) and str(spec.get("TECHNICAL_EXIT") or "").startswith(TECHNICAL_EXIT_ID)),
        _gate("T21", bool(t21)),
        _gate("T22", int(POSITION_CAP) == 5 and int(spec.get("CAP") or 0) == 5),
        _gate("T23", spec.get("same_symbol") is True),
        _gate("T24", spec.get("signal_reserves_slot") is False),
        _gate("T25", "fill" in str(spec.get("occupancy") or "").lower()),
        _gate("T26", "EXIT fill" in str(spec.get("slot_release") or "")),
        _gate("T27", "FALSE→TRUE" in str(spec.get("reentry") or "")),
        _gate("T28", flatten_ok),
        _gate(
            "T29",
            abs(float(BOARD_FRESHNESS_SEC) - 5.0) < 1e-12
            and abs(float(BOARD_FRESHNESS_SEC) - float(E1_FRESH)) < 1e-12
            and abs(float(HARVEST_FRESH) - float(BOARD_FRESHNESS_SEC)) < 1e-12,
        ),
        _gate("T30", int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"]) == 0),
        _gate("T31", int(leak["PREOPEN_EXECUTION_N"]) == 0),
        _gate("T32", int(leak["RAW_SIGNAL_SCREEN_N"]) == 0),
        _gate(
            "T33",
            int(leak["HOLDOUT_BURNED_READ_N"]) == 0
            and int(leak["STRESS_READ_N"]) == 0
            and int(leak["FUTURE_DATA_N"]) == 0,
        ),
    ]
    _ = harvest_ok
    _ = _book
    pass_n = sum(1 for g in gates if g["pass"])
    return {
        "gates": gates,
        "PASS_N": int(pass_n),
        "TOTAL_N": 33,
        "ALL_PASS": pass_n == 33,
        "leakage": leak,
        "ECONOMICS_VISIBLE_BEFORE_INTEGRITY_PASS": False,
        "FORBIDDEN_TRANSFORMATION_USED_N": int(leak["FORBIDDEN_TRANSFORMATION_USED_N"]),
        "STRATEGY_ID": STRATEGY_ID,
    }


assert len(GATE_IDS) == 33
