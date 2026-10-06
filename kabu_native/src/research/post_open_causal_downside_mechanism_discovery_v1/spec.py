"""Pin execution-tax parent + freeze SHORT_W5 mirror. No Runtime short."""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY
from research.e1_x34a_execution_policy import arms as long_arms
from research.entry_execution_feasibility import fill as long_fill
from research.post_open_causal_downside_mechanism_discovery_v1 import (
    ANALYSIS_ID,
    EXPECTED_ANCHOR_N,
    EXPECTED_EXECUTABLE_N,
    EXPECTED_LONG_X1_MEAN,
    EXPECTED_LONG_X1_MEDIAN,
    EXPECTED_PRIMARY_N,
    PARENT_ID,
    PARITY_ABS_TOL,
    REQUIRED_PARENT_NEXT,
    REQUIRED_PARENT_VERDICT,
    W5_WAIT_SEC,
)
from research.post_open_causal_downside_mechanism_discovery_v1.isolation import TAX_OUT
from research.post_open_causal_downside_mechanism_discovery_v1.short_w5 import (
    SHORT_W5_SPEC_ID,
    find_bid_cross_fill,
    limit_ask_at_t0,
    standalone_short_fill,
)

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "short_w5.py",
    "harvest.py",
    "analyze.py",
    "interpret.py",
    "publish.py",
    "__main__.py",
)

PARENT_REPORT = TAX_OUT / "report.json"


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
    verdict = str(d.get("VERDICT") or a.get("37_VERDICT") or "")
    nxt = str(d.get("NEXT") or a.get("38_NEXT") or "")
    ok = str(prev.get("ANALYSIS_ID") or "") == PARENT_ID and verdict == REQUIRED_PARENT_VERDICT and nxt == REQUIRED_PARENT_NEXT
    return {
        "ok": bool(ok),
        "PARENT_ID": PARENT_ID,
        "VERDICT": verdict,
        "NEXT": nxt,
        "CANDIDATE_STRATEGY_N": 0,
        "KIND": str(prev.get("KIND") or ""),
        "CASE": d.get("CASE"),
    }


def parent_row_parity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from research.post_open_causal_upside_mechanism_discovery_v1.analyze import population

    pop = population(rows)
    mean_ok = pop.get("EXEC_MARKOUT_10M_mean") is not None and abs(float(pop["EXEC_MARKOUT_10M_mean"]) - EXPECTED_LONG_X1_MEAN) <= PARITY_ABS_TOL
    med_ok = pop.get("EXEC_MARKOUT_10M_median") is not None and abs(float(pop["EXEC_MARKOUT_10M_median"]) - EXPECTED_LONG_X1_MEDIAN) <= PARITY_ABS_TOL
    ok = (
        int(pop.get("anchor_n") or 0) == EXPECTED_ANCHOR_N
        and int(pop.get("executable_n") or 0) == EXPECTED_EXECUTABLE_N
        and int(pop.get("primary_n") or 0) == EXPECTED_PRIMARY_N
        and mean_ok
        and med_ok
    )
    return {"ok": bool(ok), "observed": pop, "mean_ok": mean_ok, "median_ok": med_ok}


def pin_short_w5() -> dict[str, Any]:
    existed = hasattr(long_arms, "find_bid_cross_fill") or hasattr(long_fill, "limit_ask_at_t0") or hasattr(long_fill, "standalone_short_fill")
    cross = inspect.getsource(find_bid_cross_fill)
    body = cross.split('"""', 2)[-1].lower() if cross.count('"""') >= 2 else cross.lower()
    semantics = {
        "canonical_short_passive_absent": not existed,
        "limit_is_last_ask_at_or_before_t0": "searchsorted" in inspect.getsource(limit_ask_at_t0) and "ask" in inspect.getsource(limit_ask_at_t0),
        "fill_price_equals_frozen_ask_limit": '"fill_price": float(limit_price)' in cross,
        "wait_sec_5": abs(float(W5_WAIT_SEC) - 5.0) < 1e-12,
        "bid_cross_ge_limit": "bid + 1e-12 >= float(limit_price)" in cross,
        "freshness_5s": abs(float(BOARD_FRESHNESS_SEC) - 5.0) < 1e-12,
        "min_qty_100": abs(float(MIN_QTY) - 100.0) < 1e-12,
        "no_reprice": "reprice" not in body,
        "no_chase": "chase" not in body,
        "no_fallback_bid": "fallback" not in body,
        "no_synthetic": "synthetic" not in body,
        "research_only_mirror": True,
    }
    root = Path(__file__).resolve().parent / "short_w5.py"
    sha = file_sha256(root)
    return {
        "ok": all(bool(v) for v in semantics.values()),
        "trusted_short_passive_existed": bool(existed),
        "SPEC_ID": SHORT_W5_SPEC_ID,
        "source_file": str(root),
        "source_function": "standalone_short_fill -> find_bid_cross_fill",
        "limit_function": "limit_ask_at_t0",
        "source_sha256": sha,
        "t0_limit": "Ask1 at last board <= T, frozen for the wait",
        "wait_start": "t0 (anchor T)",
        "wait_end": "min(t0 + 5s, session_end)",
        "wait_sec": float(W5_WAIT_SEC),
        "fill_price": "frozen Ask1 limit; no improvement vs crossed Bid",
        "fill_timestamp": "first causal board in wait with executable Bid1 >= limit",
        "freshness": float(BOARD_FRESHNESS_SEC),
        "min_qty": float(MIN_QTY),
        "repricing": False,
        "chase": False,
        "fallback_bid": False,
        "synthetic_fill": False,
        "semantics": semantics,
    }


def already_executed_check() -> dict[str, Any]:
    from research.post_open_causal_downside_mechanism_discovery_v1.isolation import OUT

    path = OUT / "report.json"
    if not path.is_file():
        return {"REUSED_EXISTING_RESULT": False}
    prev = json.loads(path.read_text(encoding="utf-8"))
    if str(prev.get("ANALYSIS_ID") or "") != ANALYSIS_ID:
        return {"REUSED_EXISTING_RESULT": False}
    if dict(prev.get("decision") or {}).get("VERDICT"):
        return {"REUSED_EXISTING_RESULT": True, "prior_report": prev}
    return {"REUSED_EXISTING_RESULT": False}
