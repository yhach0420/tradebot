"""Offline Full Causal mechanism discovery. Integrity then R2 canary then selectable economics. Runtime 0/0/0."""
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
from research.full_causal_mechanism_discovery_v1 import (
    ANALYSIS_ID,
    CANARY_ROW_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    MAX_RESEARCH_DATE,
    PRIMARY_DECISION_UNIT,
    TRUE_OOS,
)
from research.full_causal_mechanism_discovery_v1.analyze import (
    build_answers,
    canary_parity,
    decide,
    evaluate_all_selectable,
    evaluate_strategy,
    full_dev_rank_key,
    public_row,
    selection_transfer,
)
from research.full_causal_mechanism_discovery_v1.candidates import freeze_candidate_set
from research.full_causal_mechanism_discovery_v1.harvest import AUDIT, harvest_development
from research.full_causal_mechanism_discovery_v1.integrity import run_integrity
from research.full_causal_mechanism_discovery_v1.isolation import (
    CACHE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.full_causal_mechanism_discovery_v1.mapping import freeze_mapping
from research.full_causal_mechanism_discovery_v1.publish import SHEET_ORDER, build_markdown, build_sheets, write_artifacts
from research.full_causal_mechanism_discovery_v1.spec import already_executed_check, library_sha256, pin_parent, source_sha256

JST = ZoneInfo("Asia/Tokyo")


def _safety() -> dict[str, Any]:
    return {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "ENTRY_RUNTIME_CHANGED": False,
        "EXIT_RUNTIME_CHANGED": False,
        "CAPTURE_CHANGED": False,
        "FUTURE_DATA_USED": False,
        "STRESS_OPENED": False,
        "HOLDOUT_OPENED": False,
        "SIZING": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "THRESHOLD_SEARCH": False,
        "HORIZON_SEARCH": False,
        "EXIT_SEARCH": False,
        "ENTRY_EXIT_GRID": False,
        "RAW_SIGNAL_SCREENING_AS_PRIMARY_DECISION_UNIT": False,
    }


def _objective() -> dict[str, Any]:
    return {
        "PRIMARY_GOAL": "Evaluate frozen O1/O2/O3 42 as complete Full Causal strategies on sealed DEV.",
        "PRIMARY_DECISION_UNIT": PRIMARY_DECISION_UNIT,
        "NON_GOALS": [
            "raw ENTRY quality optimization",
            "CAP optimization",
            "EXIT optimization",
            "ENTRY x EXIT grid",
            "threshold search",
            "timeframe search",
            "horizon search",
            "R2 rescue",
            "ST rescue",
            "V4 rescue",
            "CSB rescue",
            "Sizing",
            "future/OOS validation",
        ],
        "STOP_IF_GOAL_MISMATCH": True,
    }


def _publish(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    sheets = build_sheets(report)
    assert tuple(sheets.keys()) == SHEET_ORDER
    write_artifacts(report, sheets)


def _base_report(**kwargs: Any) -> dict[str, Any]:
    out = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "safety": _safety(),
    }
    out.update(kwargs)
    return out


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE FULL CAUSAL MECHANISM DISCOVERY V1", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)

    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        AUDIT["SPLIT_LEAKAGE_N"] += 1

    parent = pin_parent()
    if not parent.get("ok"):
        raise RuntimeError(f"PARENT_PIN_FAIL {parent}")
    lib_sha = library_sha256()
    mapping = freeze_mapping()
    freeze = freeze_candidate_set()
    src_sha = source_sha256()
    impl_sha = src_sha
    reused = already_executed_check(
        library_sha=lib_sha,
        mapping_sha=str(mapping["FULL_STRATEGY_MAPPING_SHA256"]),
        candidate_set_sha=str(freeze["FULL_CAUSAL_CANDIDATE_SET_SHA256"]),
    )
    print(f"LIBRARY_SHA256 {lib_sha}", flush=True)
    print(f"MAPPING_SHA256 {mapping['FULL_STRATEGY_MAPPING_SHA256']}", flush=True)
    print(f"CANDIDATE_SET_SHA256 {freeze['FULL_CAUSAL_CANDIDATE_SET_SHA256']}", flush=True)
    print(
        f"CANDIDATE_N={freeze['FULL_CAUSAL_CANDIDATE_N']} CLOSED={freeze['CLOSED_REFERENCE_N']} "
        f"SELECTABLE={freeze['SELECTABLE_FULL_CAUSAL_CANDIDATE_N']}",
        flush=True,
    )
    if reused.get("REUSED_EXISTING_RESULT"):
        print("REUSE_EXISTING_RESULT=true", flush=True)
        print("STOP.", flush=True)
        return 0

    print("PHASE harvest canary + 42 Full Causal", flush=True)
    har = harvest_development()
    integ = run_integrity(
        library_sha=lib_sha,
        mapping_sha=str(mapping["FULL_STRATEGY_MAPPING_SHA256"]),
        candidate_set_sha=str(freeze["FULL_CAUSAL_CANDIDATE_SET_SHA256"]),
        freeze=freeze,
        rows_by=dict(har.get("rows_by") or {}) if har.get("ok") else None,
        harvest_ok=bool(har.get("ok")),
    )
    print(f"INTEGRITY {integ.get('PASS_N')}/{integ.get('TOTAL_N')} ALL_PASS={integ.get('ALL_PASS')}", flush=True)

    canary_pack: dict[str, Any] = {"CANARY_ID": CANARY_ROW_ID, "HARD_PASS": False}
    eco = None
    trans = None
    winner = None
    full_dev_id = None
    economics_opened = False

    if not integ.get("ALL_PASS"):
        decision = decide(
            integrity_pass=False,
            canary_ok=False,
            economics_opened=False,
            base_qualified=[],
            closed_qualified=[],
            transfer=None,
            full_dev_id=None,
        )
        report = _base_report(
            REUSED_EXISTING_RESULT=False,
            objective_alignment=_objective(),
            parent=parent,
            exit_mapping=mapping,
            candidate_set={k: v for k, v in freeze.items() if k != "candidates"} | {"candidates": freeze["candidates"]},
            hashes={
                "MECHANISM_LIBRARY_SHA256": lib_sha,
                "FULL_STRATEGY_MAPPING_SHA256": mapping["FULL_STRATEGY_MAPPING_SHA256"],
                "FULL_CAUSAL_CANDIDATE_SET_SHA256": freeze["FULL_CAUSAL_CANDIDATE_SET_SHA256"],
                "implementation_sha256": impl_sha,
                "SOURCE_SHA256": src_sha,
            },
            integrity=integ,
            canary=canary_pack,
            decision=decision,
            STOP_REASON=str(har.get("blocker") or "INTEGRITY"),
            isolation_before=before,
        )
        _publish(report)
        print(f"VERDICT {decision['VERDICT']}", flush=True)
        print("STOP.", flush=True)
        return 2

    rows_by = dict(har.get("rows_by") or {})
    print("PHASE CANARY R2_X1_Z3 original identity", flush=True)
    canary_ev = evaluate_strategy(
        CANARY_ROW_ID,
        list(rows_by.get(CANARY_ROW_ID) or []),
        days=list(DEVELOPMENT_DAYS),
        meta={"MECHANISM_ID": "O3_RECLAIM_ACCEPT_NEXT__R2", "OPERATOR": "O3_RECLAIM_ACCEPT_NEXT", "SELECTABLE": False},
        compute_g6=True,
        compute_blocks=False,
    )
    canary_pack = canary_parity(canary_ev)
    print(
        f"CANARY signal={canary_ev.get('signal_n')} fill={canary_ev.get('fill_n')} trades={canary_ev.get('trade_n')} "
        f"pnl={canary_ev.get('TOTAL_PNL')} pf={canary_ev.get('PF')} HARD_PASS={canary_pack.get('HARD_PASS')}",
        flush=True,
    )

    if canary_pack.get("HARD_PASS"):
        economics_opened = True
        print("PHASE selectable Full Causal economics", flush=True)
        eco = evaluate_all_selectable(rows_by, freeze, open_economics=True)
        qualified = list(eco.get("qualified") or [])
        closed_q = list(eco.get("closed_qualified") or [])
        if qualified:
            qualified.sort(key=full_dev_rank_key)
            winner = qualified[0]
            full_dev_id = str(winner["STRATEGY_ID"])
            print("PHASE five-fold LOBO selection transfer", flush=True)
            trans = selection_transfer(rows_by, [c for c in freeze["candidates"] if c.get("SELECTABLE")], full_dev_id)
        decision = decide(
            integrity_pass=True,
            canary_ok=True,
            economics_opened=True,
            base_qualified=qualified,
            closed_qualified=closed_q,
            transfer=trans,
            full_dev_id=full_dev_id,
        )
    else:
        decision = decide(
            integrity_pass=True,
            canary_ok=False,
            economics_opened=False,
            base_qualified=[],
            closed_qualified=[],
            transfer=None,
            full_dev_id=None,
        )

    after = snapshot(phase="POST")
    research_touched = [OUT, CACHE]
    if stress_path_touch_n(research_touched) or holdout_path_touch_n(research_touched):
        raise RuntimeError("SEALED_PATH_TOUCH")

    selected = public_row(winner) if decision.get("DEV_CANDIDATE") and winner is not None else None
    result_sha = dumps_sha256(
        {
            "VERDICT": decision.get("VERDICT"),
            "DEV_CANDIDATE": decision.get("DEV_CANDIDATE"),
            "BASE_QUALIFIED_IDS": None if eco is None else eco.get("BASE_QUALIFIED_IDS"),
            "CANARY_HARD_PASS": canary_pack.get("HARD_PASS"),
        }
    )
    report = _base_report(
        REUSED_EXISTING_RESULT=False,
        objective_alignment=_objective(),
        parent=parent,
        exit_mapping=mapping,
        candidate_set={k: v for k, v in freeze.items() if k != "by_id"},
        hashes={
            "MECHANISM_LIBRARY_SHA256": lib_sha,
            "FULL_STRATEGY_MAPPING_SHA256": mapping["FULL_STRATEGY_MAPPING_SHA256"],
            "FULL_CAUSAL_CANDIDATE_SET_SHA256": freeze["FULL_CAUSAL_CANDIDATE_SET_SHA256"],
            "implementation_sha256": impl_sha,
            "SOURCE_SHA256": src_sha,
            "DEV_result_SHA256": result_sha,
        },
        integrity=integ,
        canary=canary_pack,
        evaluated=list((eco or {}).get("evaluated") or []),
        closed_evaluated=list((eco or {}).get("closed_evaluated") or []),
        coverage_pass_n=None if eco is None else eco.get("coverage_pass_n"),
        g1_g5_survivor_n=None if eco is None else eco.get("g1_g5_survivor_n"),
        g6_survivor_n=None if eco is None else eco.get("g6_survivor_n"),
        s1_s2_survivor_n=None if eco is None else eco.get("s1_s2_survivor_n"),
        BASE_QUALIFIED_N=0 if eco is None else eco.get("BASE_QUALIFIED_N"),
        BASE_QUALIFIED_IDS=[] if eco is None else eco.get("BASE_QUALIFIED_IDS"),
        FULL_DEV_SELECTED_ID=full_dev_id,
        selection_transfer=trans,
        selected=selected,
        qualified=list((eco or {}).get("qualified") or []),
        closed_qualified=list((eco or {}).get("closed_qualified") or []),
        decision=decision,
        isolation_before=before,
        isolation_after=after,
        isolation_advanced=advanced(before, after),
        ECONOMICS_OPENED=economics_opened,
        FREEZE_TIMESTAMP=datetime.now(JST).isoformat(timespec="seconds") if decision.get("FULL_STRATEGY_DEV_FROZEN") else None,
    )
    _publish(report)
    print(f"VERDICT {decision.get('VERDICT')}", flush=True)
    print(f"NEXT {decision.get('NEXT')}", flush=True)
    print(f"OUT {OUT}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
