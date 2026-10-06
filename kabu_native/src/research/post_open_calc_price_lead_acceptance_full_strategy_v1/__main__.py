"""Offline CalcPrice lead Full Causal. LEGACY_DEV only. Runtime 0/0/0."""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_c0_indicator_exit.isolation import advanced
from research.existing_data_entry_aligned_full_causal_logic_completion_v1.harvest import harvest_development as harvest_canary
from research.full_causal_mechanism_discovery_v1.analyze import canary_parity, evaluate_strategy
from research.post_open_calc_price_lead_acceptance_full_strategy_v1 import (
    ANALYSIS_ID,
    CANARY_ROW_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    EXECUTION_ID,
    MAX_RESEARCH_DATE,
    OPENING_LINE_STATUS,
    POSITION_CAP_N,
    STRATEGY_ID,
    TRUE_OOS,
)
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.analyze import (
    _g15,
    build_answers,
    decide,
    extra_counts,
)
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.duplicates import audit_duplicates
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.harvest import harvest_calc
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.isolation import (
    CACHE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.library import (
    freeze_library_sha,
    freeze_strategy_sha,
    strategy_spec,
)
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.publish import SHEET_ORDER, build_markdown, build_sheets, write_artifacts
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.scan import scan_post_open
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.semantics import prove_calcprice_semantics
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.spec import already_executed_check, pin_parent, source_sha256
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.synthetic import run_synthetic_tests
from research.simple_tech_entry_family.portfolio import _sym

JST = ZoneInfo("Asia/Tokyo")


def _safety() -> dict[str, Any]:
    return {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "RUNTIME_CHANGED": False,
        "CAPTURE_CHANGED": False,
        "PAPER_CHANGED": False,
        "HOLDOUT_OPENED": False,
        "STRESS_OPENED": False,
        "QUARANTINE_OPENED": False,
        "MBO_WORK": False,
        "FUTURES_WORK": False,
        "SIZING": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "POST_RESULT_ENTRY_CHANGE": False,
        "POST_RESULT_EXIT_CHANGE": False,
        "POST_RESULT_THRESHOLD_CHANGE": False,
        "EXTRA_CANDIDATE_ADDED": False,
        "AOP_RESCUE": False,
    }


def _objective() -> dict[str, Any]:
    return {
        "PRIMARY_GOAL": "Complete Full Causal strategy from post-open CalcPrice up-lead acceptance. LEGACY_DEV only.",
        "PRIMARY_DECISION_UNIT": "COMPLETE_FULL_CAUSAL_STRATEGY",
        "PARENT": "POST_OPEN_AGGREGATE_ORDER_PRESSURE_ACCEPTANCE_FULL_STRATEGY_V1",
        "ARCHITECTURE": "NON_OPENING POST_OPEN CALC_PRICE_LEAD",
        "OPENING_CURRENT_DATA_LINE_STATUS": OPENING_LINE_STATUS,
        "ISQ_RESOLUTION_STATUS": "CLOSED",
        "AOP_ARCHITECTURE_STATUS": "CLOSED",
        "NON_GOALS": [
            "Opening",
            "ISQ retune",
            "AOP field rescue",
            "MBO",
            "futures",
            "Sizing",
            "threshold rescue",
        ],
    }


def _publish(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    sheets = build_sheets(report)
    assert tuple(sheets.keys()) == SHEET_ORDER
    for ev in list(report.get("candidate_evals") or []):
        ev.pop("_trades", None)
    write_artifacts(report, sheets)


def apply_causal_g6(ev: dict[str, Any]) -> dict[str, Any]:
    top = ev.get("top_symbol")
    g = dict(ev.get("g_table") or {})
    if not top:
        g["G6"] = False
        ev["g_table"] = g
        ev["CAUSAL_EX_TOP1_PNL"] = None
        ev["CAUSAL_EX_TOP1_METHOD"] = "NO_TOP_SYMBOL"
        ev["BASE_QUALIFIED"] = False
        return ev
    key = _sym({"symbol": top})
    print(f"PHASE G6 HARVEST exclude={key}", flush=True)
    har = harvest_calc(exclude_symbol=key)
    if not har.get("ok"):
        g["G6"] = False
        ev["g_table"] = g
        ev["G6_BLOCKER"] = har.get("blocker")
        ev["BASE_QUALIFIED"] = False
        return ev
    rows = list((har.get("rows_by") or {}).get(STRATEGY_ID) or [])
    c_ev = evaluate_strategy(STRATEGY_ID, rows, days=list(DEVELOPMENT_DAYS), compute_g6=False, compute_blocks=False)
    causal_pnl = float(c_ev.get("TOTAL_PNL") or 0.0)
    g["G6"] = causal_pnl >= 0.0
    ev["g_table"] = g
    ev["CAUSAL_EX_TOP1_PNL"] = causal_pnl
    ev["CAUSAL_EX_TOP1_PF"] = c_ev.get("PF")
    ev["CAUSAL_EX_TOP1_METHOD"] = "UNIVERSE_REMOVAL_BEFORE_SIGNAL"
    blocks = ev.get("blocks") or {}
    ev["BASE_QUALIFIED"] = bool(
        ev.get("coverage_ok")
        and all(g.get(k) for k in ("G1", "G2", "G3", "G4", "G5", "G6"))
        and blocks.get("S1")
        and blocks.get("S2")
    )
    return ev


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 LEGACY_DEV CALC LEAD NO MBO NO FUTURE", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)

    reused = already_executed_check()
    if reused.get("REUSED_EXISTING_RESULT"):
        print("REUSE_EXISTING_RESULT=true", flush=True)
        print("STOP.", flush=True)
        return 0

    parent = pin_parent()
    if not parent.get("ok"):
        raise RuntimeError(f"PARENT_PIN_FAIL {parent}")

    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )

    proven = prove_calcprice_semantics()
    print("PHASE SEMANTICS", proven.get("SEMANTICS_PROVEN"), "formula", proven.get("FORMULA_PROVEN"), flush=True)

    dynamics: dict[str, Any] = {}
    dup: dict[str, Any] = {}
    tests: dict[str, Any] = {}
    canary: dict[str, Any] = {}
    precommit: dict[str, Any] = {"FROZEN": False}
    raw_dates: list[str] = []
    structural: dict[str, Any] = {}
    integrity_n: int | None = None
    evals: list[dict[str, Any]] = []
    selected: dict[str, Any] = {}
    signals_out: list[dict[str, Any]] = []
    episodes_out: list[dict[str, Any]] = []
    lib_sha = None

    if not proven.get("SEMANTICS_PROVEN"):
        decision = decide(semantics_ok=False)
    else:
        print("PHASE SCAN POST-OPEN CALCPRICE", flush=True)
        dynamics = scan_post_open(semantics_ok=True)
        if not dynamics.get("ok"):
            raise RuntimeError(f"SCAN_FAIL {dynamics.get('blocker')}")
        support = dict(dynamics.get("support") or {})
        fields = dict(dynamics.get("fields") or {})
        print("PHASE SUPPORT", support.get("PASS"), support.get("KIND"), support.get("REASONS"), flush=True)
        kind = str(support.get("KIND") or "")
        if not support.get("PASS"):
            decision = decide(
                semantics_ok=True,
                indep_ok=False if kind == "INDEP" else None,
                input_ok=False if kind == "INPUT" else (True if kind == "INDEP" else False),
            )
        else:
            dup = audit_duplicates(
                eq_current=fields.get("eq_current_rate"),
                eq_bid=fields.get("eq_bid1_rate"),
                eq_ask=fields.get("eq_ask1_rate"),
                eq_mid=fields.get("eq_mid_rate"),
            )
            dup_ok = (not dup.get("EXACT_DUPLICATE")) and (not dup.get("MATERIAL_SEMANTIC_DUPLICATE"))
            print("PHASE DUPLICATE exact", dup.get("EXACT_DUPLICATE"), "material", dup.get("MATERIAL_SEMANTIC_DUPLICATE"), flush=True)
            if not dup_ok:
                decision = decide(semantics_ok=True, input_ok=True, indep_ok=True, dup_ok=False)
            else:
                spec = strategy_spec()
                lib_sha = freeze_library_sha(spec)
                precommit = {
                    "SPEC_SHA256": lib_sha,
                    "LIBRARY_SHA256": lib_sha,
                    "STRATEGY_SHA256": freeze_strategy_sha(lib_sha),
                    "PRECOMMIT_BEFORE_STRUCTURAL_COUNTS": True,
                    "PRECOMMIT_BEFORE_ECONOMICS": True,
                    "SPEC": spec,
                    "FROZEN": True,
                }
                print("PHASE PRECOMMIT", lib_sha, flush=True)
                tests = run_synthetic_tests()
                print("PHASE TESTS", tests.get("PASS_N"), "/", tests.get("TOTAL"), flush=True)
                if not tests.get("ALL_PASS"):
                    decision = decide(semantics_ok=True, input_ok=True, indep_ok=True, dup_ok=True, tests_ok=False)
                else:
                    print("PHASE CANARY R2_X1_Z3", flush=True)
                    har_c = harvest_canary()
                    if not har_c.get("ok"):
                        raise RuntimeError(f"CANARY_HARVEST_FAIL {har_c.get('blocker')}")
                    canary_ev = evaluate_strategy(
                        CANARY_ROW_ID,
                        list((har_c.get("rows_by") or {}).get(CANARY_ROW_ID) or []),
                        days=list(DEVELOPMENT_DAYS),
                        meta={"SELECTABLE": False},
                        compute_g6=False,
                        compute_blocks=False,
                    )
                    canary = canary_parity(canary_ev)
                    canary_ok = bool(canary.get("HARD_PASS"))
                    print("PHASE CANARY", canary_ok, canary.get("observed"), flush=True)
                    if not canary_ok:
                        decision = decide(
                            semantics_ok=True, input_ok=True, indep_ok=True, dup_ok=True, tests_ok=True, canary_ok=False
                        )
                    else:
                        print("PHASE HARVEST CALC", flush=True)
                        har = harvest_calc()
                        if not har.get("ok"):
                            raise RuntimeError(f"CALC_HARVEST_FAIL {har.get('blocker')}")
                        raw_dates = list(DEVELOPMENT_DAYS)
                        structural = dict(har.get("structural") or {})
                        integrity_n = int(har.get("integrity_n") or 0)
                        signals_out = list(har.get("signals") or [])[:5000]
                        episodes_out = [
                            {
                                "date": e.get("date"),
                                "symbol": e.get("symbol"),
                                "episode_id": e.get("episode_id"),
                                "anchor": e.get("anchor"),
                                "calc_lead_start": e.get("calc_lead_start"),
                                "end_reason": e.get("end_reason"),
                                "signaled": e.get("signaled"),
                            }
                            for e in list(har.get("episodes") or [])[:5000]
                        ]
                        if integrity_n > 0:
                            decision = decide(
                                semantics_ok=True,
                                input_ok=True,
                                indep_ok=True,
                                dup_ok=True,
                                tests_ok=True,
                                canary_ok=True,
                                integrity_n=integrity_n,
                            )
                        else:
                            rows = list((har.get("rows_by") or {}).get(STRATEGY_ID) or [])
                            ev = evaluate_strategy(
                                STRATEGY_ID,
                                rows,
                                days=list(DEVELOPMENT_DAYS),
                                meta={"SELECTABLE": True, "MECHANISM_ID": STRATEGY_ID},
                                compute_g6=False,
                                compute_blocks=True,
                            )
                            extra = extra_counts(ev, rows)
                            ev["extra"] = extra
                            g = dict(ev.get("g_table") or {})
                            if ev.get("coverage_ok") and _g15(ev):
                                ev = apply_causal_g6(ev)
                            else:
                                g["G6"] = None
                                ev["g_table"] = g
                                ev["BASE_QUALIFIED"] = False
                            evals.append(ev)
                            print(
                                f"EVAL {STRATEGY_ID} trade={ev.get('trade_n')} cov={ev.get('coverage_ok')} "
                                f"pnl={ev.get('TOTAL_PNL')} robust={ev.get('BASE_QUALIFIED')}",
                                flush=True,
                            )
                            if ev.get("coverage_ok") and ev.get("BASE_QUALIFIED"):
                                selected = {
                                    "STRATEGY_ID": STRATEGY_ID,
                                    "FULL_STRATEGY_SPEC_SHA256": freeze_strategy_sha(str(lib_sha)),
                                    "LIBRARY_SHA": lib_sha,
                                    "LOGIC_COMPLETE": True,
                                    "TRUE_OOS": False,
                                    "CERTIFIED": False,
                                }
                            decision = decide(
                                semantics_ok=True,
                                input_ok=True,
                                indep_ok=True,
                                dup_ok=True,
                                tests_ok=True,
                                canary_ok=True,
                                integrity_n=integrity_n,
                                coverage_ok=bool(ev.get("coverage_ok")),
                                g15=_g15(ev) if ev.get("coverage_ok") else None,
                                robust=bool(ev.get("BASE_QUALIFIED")) if ev.get("coverage_ok") and _g15(ev) else None,
                            )

    after = snapshot(phase="POST")
    if stress_path_touch_n([OUT, CACHE]) or holdout_path_touch_n([OUT, CACHE]):
        raise RuntimeError("SEALED_PATH_TOUCH")

    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "REUSED_EXISTING_RESULT": False,
        "objective_alignment": _objective(),
        "parent": parent,
        "hashes": {"SOURCE_SHA256": source_sha256()},
        "data_boundary": {
            "ALLOWED": list(DEVELOPMENT_DAYS),
            "RAW_MARKET_DATES_OPENED": list(raw_dates) if raw_dates else (list(DEVELOPMENT_DAYS) if dynamics else []),
            "SESSION": "AM",
            "FLATTEN": [11, 29],
            "HOLDOUT_READ": False,
            "STRESS_READ": False,
            "FUTURE_READ": False,
        },
        "calcprice_semantics": proven,
        "calcprice_dynamics": dynamics,
        "duplicate_check": dup,
        "trade_update_semantics": {
            "OBSERVED_TRADE_UPDATE": "TradingVolume finite and > LAST_SEEN AND CurrentPrice finite > 0",
            "CURRENT_PRICE_TIME_USED_AS_TRADE_ID": False,
            "CURRENT_PRICE_TIME_USED_AS_AVAILABILITY": False,
            "AVAILABILITY_CLOCK": "INGRESS",
        },
        "unit_tests": tests,
        "canary": canary,
        "precommit": precommit,
        "structural": structural,
        "integrity": {"AFFECTED_CALC_DATA_INTEGRITY_ERROR_N": integrity_n, "REQUIRE_ZERO_BEFORE_ECONOMICS": True},
        "episodes": episodes_out,
        "signals": signals_out,
        "candidate_evals": evals,
        "execution": {"ID": EXECUTION_ID, "USED": bool(evals)},
        "portfolio": {"CAP": int(POSITION_CAP_N), "USED": bool(evals)},
        "selected_logic": selected,
        "decision": decision
        | {
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "SIZING": False,
            "MBO_WORK": False,
            "WRITE_OVERLAP_N": overlap,
            "POST_RESULT_ENTRY_CHANGE": False,
            "POST_RESULT_EXIT_CHANGE": False,
        },
        "safety": _safety(),
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "FREEZE_TIMESTAMP": datetime.now(JST).isoformat(),
    }
    _publish(report)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
