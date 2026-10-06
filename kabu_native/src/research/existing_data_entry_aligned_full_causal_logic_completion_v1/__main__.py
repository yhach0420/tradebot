"""Offline ENTRY-aligned Full Causal completion. LEGACY_DEV only. Runtime 0/0/0."""
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
from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.existing_data_entry_aligned_full_causal_logic_completion_v1 import (
    ANALYSIS_ID,
    CANARY_ROW_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    MAX_RESEARCH_DATE,
    TRUE_OOS,
)
from research.existing_data_entry_aligned_full_causal_logic_completion_v1.analyze import (
    build_answers,
    decide,
    freeze_selected_sha,
    integrity_gates,
    public_row,
    robust_sort_key,
    selected_logic,
)
from research.existing_data_entry_aligned_full_causal_logic_completion_v1.duplicates import audit_duplicates
from research.existing_data_entry_aligned_full_causal_logic_completion_v1.harvest import AUDIT, harvest_development
from research.existing_data_entry_aligned_full_causal_logic_completion_v1.isolation import (
    CACHE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.existing_data_entry_aligned_full_causal_logic_completion_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.existing_data_entry_aligned_full_causal_logic_completion_v1.spec import already_executed_check, source_sha256
from research.existing_data_entry_aligned_full_causal_logic_completion_v1.theses import ROLES, build_theses, library_rows, thesis_by_id
from research.full_causal_mechanism_discovery_v1.analyze import canary_parity, evaluate_strategy
from research.systematic_state_transition_library_precommit_v1.state_registry import build_state_registry

JST = ZoneInfo("Asia/Tokyo")


def _safety() -> dict[str, Any]:
    return {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "RUNTIME_CHANGED": False,
        "CAPTURE_CHANGED": False,
        "MARKET_DATA_PURCHASED": False,
        "HOLDOUT_OPENED": False,
        "STRESS_OPENED": False,
        "QUARANTINE_OPENED": False,
        "PAPER_20260907_READ": False,
        "SIZING": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "Z3_CANDIDATE_EXIT": False,
        "UNIVERSAL_EXIT": False,
        "EXIT_CROSS_PRODUCT": False,
        "POST_RESULT_ENTRY_CHANGE": False,
        "POST_RESULT_EXIT_CHANGE": False,
        "POST_RESULT_ROLE_CHANGE": False,
        "MBO_WORK_THIS_RUN": False,
        "PROVIDER_CONTACT": False,
    }


def _objective() -> dict[str, Any]:
    return {
        "PRIMARY_GOAL": (
            "Use only existing LEGACY_DEV data. Fix ENTRY_THESIS per raw ENTRY and define "
            "exactly one technical EXIT as thesis invalidation. Evaluate Complete Full Causal strategies."
        ),
        "PRIMARY_DECISION_UNIT": "COMPLETE_FULL_CAUSAL_STRATEGY",
        "CRITICAL_RULE": "EXIT must be derived from ENTRY thesis. Do NOT select EXIT by economics.",
        "NON_GOALS": [
            "universal EXIT",
            "Z3 candidate EXIT",
            "EXIT grid",
            "EXIT parameter search",
            "holding-time search",
            "new indicator",
            "new information source",
            "FLEX MBO",
            "future data",
            "Sizing",
            "old-candidate RCA",
        ],
        "STOP_IF_GOAL_MISMATCH": True,
    }


def _g15(ev: dict[str, Any]) -> bool:
    g = dict(ev.get("g_table") or {})
    return bool(g.get("G1") and g.get("G2") and g.get("G3") and g.get("G4") and g.get("G5"))


def _publish(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    sheets = build_sheets(report)
    assert tuple(sheets.keys()) == SHEET_ORDER
    write_artifacts(report, sheets)


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 LEGACY_DEV ENTRY-ALIGNED EXIT NO MBO NO FUTURE", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)

    reused = already_executed_check()
    if reused.get("REUSED_EXISTING_RESULT"):
        print("REUSE_EXISTING_RESULT=true", flush=True)
        print("STOP.", flush=True)
        return 0

    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )

    theses = build_theses()
    lib = library_rows(theses)
    dup = audit_duplicates(lib)
    lib_sha = dumps_sha256([{k: r[k] for k in sorted(r) if k != "ENTRY_REASON"} for r in lib])
    integ = integrity_gates(theses=theses, dup=dup)
    if int(integ["UNRESOLVED_THESIS_N"]) != 0:
        raise RuntimeError("UNRESOLVED_THESIS_STOP_BEFORE_ECONOMICS")

    print("PHASE HARVEST canary R2_X1_Z3 + 24 thesis strategies", flush=True)
    har = harvest_development()
    if not har.get("ok"):
        raise RuntimeError(f"HARVEST_FAIL {har.get('blocker')}")
    rows_by = dict(har.get("rows_by") or {})
    integ = integrity_gates(theses=theses, dup=dup)

    print("PHASE CANARY R2_X1_Z3", flush=True)
    canary_ev = evaluate_strategy(
        CANARY_ROW_ID,
        list(rows_by.get(CANARY_ROW_ID) or []),
        days=list(DEVELOPMENT_DAYS),
        meta={"SELECTABLE": False},
        compute_g6=False,
        compute_blocks=False,
    )
    canary = canary_parity(canary_ev)
    canary_ok = bool(canary.get("HARD_PASS"))
    integrity_ok = bool(integ.get("ALL_PASS"))

    economics_rows: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    block_rows: list[dict[str, Any]] = []
    symbol_rows: list[dict[str, Any]] = []
    selected_ev: dict[str, Any] | None = None
    selected_th: dict[str, Any] | None = None
    logic: dict[str, Any] = {}
    spec_sha = None
    coverage_n = g15_n = g16_n = robust_n = 0

    if canary_ok and integrity_ok:
        print("PHASE ECONOMICS 24 selectable", flush=True)
        by_id = thesis_by_id(theses)
        prelim: list[dict[str, Any]] = []
        for cand in lib:
            sid = str(cand["STRATEGY_ID"])
            ev = evaluate_strategy(
                sid,
                list(rows_by.get(sid) or []),
                days=list(DEVELOPMENT_DAYS),
                meta={"SELECTABLE": True, "MECHANISM_ID": sid},
                compute_g6=False,
                compute_blocks=True,
            )
            prelim.append(ev)
        coverage_n = sum(1 for e in prelim if e.get("coverage_ok"))
        g15_list = [e for e in prelim if e.get("coverage_ok") and _g15(e)]
        g15_n = len(g15_list)
        g6_cache: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for ev in g15_list:
            top = str(ev.get("top_symbol") or "")
            if not top:
                ev["g_table"]["G6"] = False
                continue
            print(f"PHASE G6 remove {top} before bars/states/ENTRY for {ev['STRATEGY_ID']}", flush=True)
            if top not in g6_cache:
                har6 = harvest_development(exclude_symbol=top)
                if not har6.get("ok"):
                    raise RuntimeError(f"G6_HARVEST_FAIL {har6.get('blocker')}")
                g6_cache[top] = har6.get("rows_by") or {}
            ev6 = evaluate_strategy(
                str(ev["STRATEGY_ID"]),
                list((g6_cache[top] or {}).get(str(ev["STRATEGY_ID"])) or []),
                days=list(DEVELOPMENT_DAYS),
                meta={"SELECTABLE": True},
                compute_g6=False,
                compute_blocks=False,
            )
            causal = ev6.get("TOTAL_PNL")
            ev["CAUSAL_EX_TOP1_PNL"] = causal
            ev["CAUSAL_EX_TOP1_TRADE_N"] = ev6.get("trade_n")
            ev["g_table"]["G6"] = causal is not None and float(causal) >= 0.0
        g16_list = [e for e in g15_list if (e.get("g_table") or {}).get("G6")]
        g16_n = len(g16_list)
        robust = []
        for ev in g16_list:
            b = dict(ev.get("blocks") or {})
            if bool(b.get("S1")) and bool(b.get("S2")):
                ev["ROBUST_DEV_QUALIFIED"] = True
                robust.append(ev)
            else:
                ev["ROBUST_DEV_QUALIFIED"] = False
        robust_n = len(robust)
        if robust:
            robust.sort(key=robust_sort_key)
            selected_ev = robust[0]
            selected_th = by_id.get(str(selected_ev["STRATEGY_ID"]))
            logic = selected_logic(selected_ev, selected_th)
            spec_sha = freeze_selected_sha(logic)
        for ev in prelim:
            economics_rows.append(public_row(ev))
            for d, pnl in dict(ev.get("daily") or {}).items():
                daily_rows.append({"STRATEGY_ID": ev["STRATEGY_ID"], "date": d, "pnl": pnl})
            for b in list((ev.get("blocks") or {}).get("blocks") or []):
                block_rows.append({"STRATEGY_ID": ev["STRATEGY_ID"], **b})
            symbol_rows.append(
                {
                    "STRATEGY_ID": ev["STRATEGY_ID"],
                    "top_symbol": ev.get("top_symbol"),
                    "top_symbol_pnl": ev.get("top_symbol_pnl"),
                }
            )
        decision = decide(canary_ok=True, integrity_ok=True, robust_n=robust_n, g15_n=g15_n)
    else:
        decision = decide(canary_ok=canary_ok, integrity_ok=integrity_ok, robust_n=0, g15_n=0)

    after = snapshot(phase="POST")
    if stress_path_touch_n([OUT, CACHE]) or holdout_path_touch_n([OUT, CACHE]):
        raise RuntimeError("SEALED_PATH_TOUCH")

    registry = build_state_registry()
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "REUSED_EXISTING_RESULT": False,
        "objective_alignment": _objective(),
        "hashes": {
            "SOURCE_SHA256": source_sha256(),
            "ENTRY_ALIGNED_LIBRARY_SHA256": lib_sha,
        },
        "states": [{"STATE_ID": r["STATE_ID"], "FAMILY": r["FAMILY"], "EXACT_DEFINITION": r["EXACT_DEFINITION"]} for r in registry],
        "state_roles": [{"STATE_ID": k, "ROLE": v} for k, v in ROLES.items()],
        "entry_theses": theses,
        "library_rows": lib,
        "candidate_library": {
            "RAW_ENTRY_N": 25,
            "STRUCTURALLY_INELIGIBLE_N": 1,
            "SELECTABLE_N": len(lib),
            "ENTRY_ALIGNED_LIBRARY_SHA256": lib_sha,
        },
        "duplicate_check": dup,
        "integrity": integ,
        "canary": canary,
        "coverage_PASS_N": coverage_n,
        "G15_N": g15_n,
        "G16_N": g16_n,
        "ROBUST_N": robust_n,
        "economics_rows": economics_rows,
        "daily_rows": daily_rows,
        "block_rows": block_rows,
        "symbol_rows": symbol_rows,
        "selection": {
            "ROBUST_N": robust_n,
            "RULE": "min_block_pnl, CAUSAL_EX_TOP1, EX_BEST_DAY, TOTAL_PNL, PF, abs_MaxDD, STRATEGY_ID",
            "SELECTED": (selected_ev or {}).get("STRATEGY_ID") if selected_ev else None,
        },
        "selected_eval": public_row(selected_ev) if selected_ev else {},
        "selected_logic": logic,
        "FULL_STRATEGY_SPEC_SHA256_LOGIC_V1": spec_sha,
        "decision": decision
        | {
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "SIZING": False,
            "WRITE_OVERLAP_N": overlap,
            "POST_RESULT_ENTRY_CHANGE": False,
            "POST_RESULT_EXIT_CHANGE": False,
        },
        "safety": _safety(),
        "harvest_audit": dict(AUDIT),
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
