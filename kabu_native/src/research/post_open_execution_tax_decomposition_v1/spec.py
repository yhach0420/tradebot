"""Parent parity + canonical W5 identity. No new fill rule. No Holdout."""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement import DEV_WAIT_SEC
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY
from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
from research.entry_execution_feasibility.fill import limit_bid_at_t0, standalone_fill
from research.passive_wait_policy_reassessment import WAIT_SEC_BY_ID
from research.post_open_execution_tax_decomposition_v1 import (
    ANALYSIS_ID,
    EXPECTED_ANCHOR_N,
    EXPECTED_EXECUTABLE_N,
    EXPECTED_PRIMARY_N,
    EXPECTED_X1_MEAN,
    EXPECTED_X1_MEDIAN,
    PARENT_ID,
    PARITY_ABS_TOL,
    REQUIRED_PARENT_NEXT,
    REQUIRED_PARENT_VERDICT,
    W5_PRECOMMIT_ID,
    W5_REF,
    W5_WAIT_SEC,
)
from research.post_open_execution_tax_decomposition_v1.isolation import PARENT_OUT, RESEARCH_ROOT

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "harvest.py",
    "analyze.py",
    "interpret.py",
    "publish.py",
    "__main__.py",
)

W5_SOURCE_FILES = (
    Path("src/research/entry_execution_feasibility/fill.py"),
    Path("src/research/e1_x34a_execution_policy/arms.py"),
)

PARENT_REPORT = PARENT_OUT / "report.json"
W5_PRECOMMIT_REPORT = RESEARCH_ROOT / "passive_wait5_precommit" / "report.json"


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update(name.encode("utf-8"))
        p = root / name
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    return h.hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def pin_parent() -> dict[str, Any]:
    path = PARENT_REPORT
    prev = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    d = dict(prev.get("decision") or {})
    a = dict(prev.get("answers") or {})
    pop = dict(prev.get("population") or {})
    verdict = str(d.get("VERDICT") or a.get("40_VERDICT") or "")
    nxt = str(d.get("NEXT") or a.get("41_NEXT") or "")
    ok = str(prev.get("ANALYSIS_ID") or "") == PARENT_ID and verdict == REQUIRED_PARENT_VERDICT and nxt == REQUIRED_PARENT_NEXT
    return {
        "ok": bool(ok),
        "PARENT_ID": PARENT_ID,
        "VERDICT": verdict,
        "NEXT": nxt,
        "CANDIDATE_STRATEGY_N": 0,
        "KIND": "MECHANISM_DISCOVERY_ONLY",
        "population": pop,
        "expected": {
            "anchor_n": EXPECTED_ANCHOR_N,
            "executable_n": EXPECTED_EXECUTABLE_N,
            "primary_n": EXPECTED_PRIMARY_N,
            "mean": EXPECTED_X1_MEAN,
            "median": EXPECTED_X1_MEDIAN,
        },
    }


def parent_row_parity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from research.post_open_causal_upside_mechanism_discovery_v1.analyze import population

    pop = population(rows)
    mean_ok = pop.get("EXEC_MARKOUT_10M_mean") is not None and abs(float(pop["EXEC_MARKOUT_10M_mean"]) - EXPECTED_X1_MEAN) <= PARITY_ABS_TOL
    med_ok = pop.get("EXEC_MARKOUT_10M_median") is not None and abs(float(pop["EXEC_MARKOUT_10M_median"]) - EXPECTED_X1_MEDIAN) <= PARITY_ABS_TOL
    ok = (
        int(pop.get("anchor_n") or 0) == EXPECTED_ANCHOR_N
        and int(pop.get("executable_n") or 0) == EXPECTED_EXECUTABLE_N
        and int(pop.get("primary_n") or 0) == EXPECTED_PRIMARY_N
        and mean_ok
        and med_ok
    )
    return {"ok": bool(ok), "observed": pop, "mean_ok": mean_ok, "median_ok": med_ok}


def pin_w5() -> dict[str, Any]:
    native = Path(__file__).resolve().parents[3]
    fill_path = native / "src" / "research" / "entry_execution_feasibility" / "fill.py"
    arms_path = native / "src" / "research" / "e1_x34a_execution_policy" / "arms.py"
    src_cross = inspect.getsource(find_ask_cross_fill)
    src_stand = inspect.getsource(standalone_fill)
    src_limit = inspect.getsource(limit_bid_at_t0)
    combined = src_cross + src_stand + src_limit
    low = combined.lower()
    prev = json.loads(W5_PRECOMMIT_REPORT.read_text(encoding="utf-8")) if W5_PRECOMMIT_REPORT.is_file() else {}
    req = dict(prev.get("required") or {})
    rate_ok = all(abs(float(req.get(k) or 0) - float(v)) <= 1e-12 for k, v in W5_REF.items()) if req else False
    semantics = {
        "fill_price_equals_frozen_limit": '"fill_price": float(limit_price)' in src_cross,
        "wait_sec_is_argument": "wait_sec" in src_cross and float(WAIT_SEC_BY_ID["W5"]) == float(W5_WAIT_SEC),
        "dev_wait_sec_5": abs(float(DEV_WAIT_SEC) - float(W5_WAIT_SEC)) < 1e-12,
        "no_reprice_in_fill": "reprice" not in low,
        "no_chase_in_fill": "chase" not in low,
        "no_fallback_ask": "fallback" not in low,
        "no_synthetic_fill": "synthetic" not in low,
        "freshness_5s": abs(float(BOARD_FRESHNESS_SEC) - 5.0) < 1e-12,
        "min_qty_100": abs(float(MIN_QTY) - 100.0) < 1e-12,
        "standalone_calls_find_ask_cross_fill": "find_ask_cross_fill" in src_stand,
        "limit_is_last_bid_at_or_before_t0": "searchsorted" in src_limit and "bid" in src_limit,
        "w5_precommit_rates_match_reference": rate_ok,
        "w5_precommit_id": str(prev.get("ANALYSIS_ID") or "") == W5_PRECOMMIT_ID,
    }
    ok = all(bool(v) for v in semantics.values())
    sha = hashlib.sha256()
    sha.update(fill_path.read_bytes() if fill_path.is_file() else b"MISSING")
    sha.update(arms_path.read_bytes() if arms_path.is_file() else b"MISSING")
    sha.update(combined.encode("utf-8"))
    return {
        "ok": bool(ok),
        "CONTROL": "X1_IMMEDIATE_ASK",
        "TREATMENT": "CORRECTED_PASSIVE_FILL_W5",
        "source_file": str(fill_path),
        "source_function": "standalone_fill -> find_ask_cross_fill",
        "limit_function": "limit_bid_at_t0",
        "cross_function": "research.e1_x34a_execution_policy.arms.find_ask_cross_fill",
        "source_sha256": sha.hexdigest(),
        "fill_py_sha256": file_sha256(fill_path),
        "arms_py_sha256": file_sha256(arms_path),
        "t0_limit": "Bid1 at last board <= T, frozen for the wait",
        "wait_start": "t0 (anchor T)",
        "wait_end": "min(t0 + 5s, session_end)",
        "wait_sec": float(W5_WAIT_SEC),
        "fill_price": "frozen limit_price (Bid1 at t0); no improvement vs crossed Ask",
        "fill_timestamp": "first causal board in wait with executable Ask1 <= limit",
        "freshness": float(BOARD_FRESHNESS_SEC),
        "min_qty": float(MIN_QTY),
        "executable_board_rule": "require_executable_continuous=True; special boards skipped",
        "queue": "unobservable; not used",
        "repricing": False,
        "chase": False,
        "fallback_ask": False,
        "synthetic_fill": False,
        "reference_rates_not_this_population": dict(W5_REF),
        "w5_precommit_observed": {k: req.get(k) for k in W5_REF},
        "semantics": semantics,
    }


def already_executed_check() -> dict[str, Any]:
    from research.post_open_execution_tax_decomposition_v1.isolation import OUT

    path = OUT / "report.json"
    if not path.is_file():
        return {"REUSED_EXISTING_RESULT": False}
    prev = json.loads(path.read_text(encoding="utf-8"))
    if str(prev.get("ANALYSIS_ID") or "") != ANALYSIS_ID:
        return {"REUSED_EXISTING_RESULT": False}
    if dict(prev.get("decision") or {}).get("VERDICT"):
        return {"REUSED_EXISTING_RESULT": True, "prior_report": prev}
    return {"REUSED_EXISTING_RESULT": False}
