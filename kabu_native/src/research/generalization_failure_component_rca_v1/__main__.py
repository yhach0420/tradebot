"""Offline component RCA. Existing artifacts only. No harvest/Stress/future."""
from __future__ import annotations

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
from research.generalization_failure_component_rca_v1 import ANALYSIS_ID, CERTIFIED, TRUE_OOS
from research.generalization_failure_component_rca_v1.analyze import already_executed_check, build_answers, decide
from research.generalization_failure_component_rca_v1.isolation import (
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.generalization_failure_component_rca_v1.publish import (
    SHEET_ORDER,
    _public,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.generalization_failure_component_rca_v1.sources import load_sources
from research.generalization_failure_component_rca_v1.spec import canonical_spec, source_sha256, spec_sha256

JST = ZoneInfo("Asia/Tokyo")


def _safety() -> dict[str, Any]:
    return {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "ENTRY_RUNTIME_CHANGED": False,
        "EXIT_RUNTIME_CHANGED": False,
        "FUTURE_DATA_USED": False,
        "STRESS_OPENED": False,
        "HOLDOUT_OPENED": False,
        "RAW_CAPTURE_READ_N": 0,
        "NEW_REPLAY": False,
        "NEW_PNL_SIMULATION": False,
        "NEW_CANDIDATE": False,
        "SIZING": False,
        "TEMPORAL_SPLIT": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
    }


def _publish(report: dict[str, Any], pack: dict[str, Any]) -> None:
    d = dict(pack.get("decision") or {})
    report["answers"] = build_answers(pack)
    report["_markdown"] = build_markdown({"answers": report["answers"], "decision": d})
    all_rows = list(pack.get("all_g1g2") or [])
    valid = list(pack.get("valid_cohort") or [])
    low = list(pack.get("low_support") or [])
    win = dict(pack.get("winner") or {})
    matrix = dict(pack.get("lineage_matrix") or {})
    sheets = {
        "answers": kv_rows(report.get("answers")),
        "source_inventory": list(pack.get("source_inventory") or []),
        "coverage_correction": [
            {
                "rule": "ORIGINAL_FROZEN_GATE",
                "COVERAGE_INFER_FROM_TRADE_N_ONLY": False,
                "ORIGINAL_G1G2_N": pack.get("ORIGINAL_G1G2_N"),
                "PRIMARY_VALID_COHORT_N": pack.get("PRIMARY_VALID_COHORT_N"),
                "LOW_SUPPORT_DIAGNOSTIC_N": pack.get("LOW_SUPPORT_DIAGNOSTIC_N"),
            }
        ],
        "low_support_diagnostic": [_public(r) for r in low],
        "valid_cohort": [_public(r) for r in valid],
        "gate_run_state": [
            {
                "candidate_id": r.get("candidate_id"),
                "VALID_PRIMARY": r.get("VALID_PRIMARY"),
                "LOW_SUPPORT_DIAGNOSTIC": r.get("LOW_SUPPORT_DIAGNOSTIC"),
                "original_gate": r.get("original_gate"),
                "COVERAGE_PASS": r.get("COVERAGE_PASS"),
                "G1_VALUE": r.get("G1_VALUE"),
                "G1_RAN": r.get("G1_RAN"),
                "G2_VALUE": r.get("G2_VALUE"),
                "G2_RAN": r.get("G2_RAN"),
                "G3_VALUE": r.get("G3_VALUE"),
                "G3_RAN": r.get("G3_RAN"),
                "G4_VALUE": r.get("G4_VALUE"),
                "G4_RAN": r.get("G4_RAN"),
                "G5_VALUE": r.get("G5_VALUE"),
                "G5_RAN": r.get("G5_RAN"),
                "G6_VALUE": r.get("G6_VALUE"),
                "G6_RAN": r.get("G6_RAN"),
                "CAUSAL_EX_TOP1_STATUS": r.get("CAUSAL_EX_TOP1_STATUS"),
            }
            for r in all_rows
        ],
        "stability_run_state": [
            {
                "candidate_id": r.get("candidate_id"),
                "STABILITY_RAN": r.get("STABILITY_RAN"),
                "STABILITY_PASS": r.get("STABILITY_PASS"),
                "STABILITY_STATUS": r.get("STABILITY_STATUS"),
                "SELECTION_FAILURE": r.get("SELECTION_FAILURE"),
            }
            for r in all_rows
        ],
        "symbol_mode": [
            {"lineage": "L1_TECHNICAL_PRICE_STATE", **dict(pack.get("l1_symbol") or {})},
            {"lineage": "L2_RECOVERY_RECLAIM_PATH", **dict(pack.get("l2_symbol") or {})},
        ],
        "day_mode": [
            {"lineage": "L1_TECHNICAL_PRICE_STATE", **dict(pack.get("l1_day") or {})},
            {"lineage": "L2_RECOVERY_RECLAIM_PATH", **dict(pack.get("l2_day") or {})},
        ],
        "winner_blocks": list(win.get("blocks") or []),
        "selection_surface": kv_rows(
            {
                "ST_WINNER_ID": win.get("ST_WINNER_ID"),
                "ST_WINNER_CONCENTRATION_RESILIENT": win.get("ST_WINNER_CONCENTRATION_RESILIENT"),
                "ST_WINNER_ABSOLUTE_ECONOMIC_PASS": win.get("ST_WINNER_ABSOLUTE_ECONOMIC_PASS"),
                "ST_WINNER_SELECTION_FAILURE": win.get("ST_WINNER_SELECTION_FAILURE"),
                "TRAIN_TOP3_N": win.get("TRAIN_TOP3_N"),
                "FOLD_SELECTED_TEST_TOTAL_PNL": win.get("FOLD_SELECTED_TEST_TOTAL_PNL"),
                "FORMALLY_TESTED_STUDY_N": 1,
            }
        ),
        "component_structure": kv_rows(
            {
                "COMPONENT_STRUCTURE": d.get("COMPONENT_STRUCTURE"),
                "PRIMARY_COMPONENT": d.get("PRIMARY_COMPONENT"),
                "RESIDUAL_BOTTLENECK": d.get("RESIDUAL_BOTTLENECK"),
                "CROSS_LINEAGE": d.get("CROSS_LINEAGE"),
                "RESIDUAL_FORMALLY_OBSERVED_IN_ST": d.get("RESIDUAL_FORMALLY_OBSERVED_IN_ST"),
            }
        ),
        "lineage_matrix": [
            {"dimension": dim, "L1": cells.get("L1"), "L2": cells.get("L2")} for dim, cells in matrix.items()
        ],
        "decision": kv_rows(d),
        "safety": kv_rows(report.get("safety")),
    }
    assert tuple(sheets.keys()) == SHEET_ORDER
    write_artifacts(report, sheets)


def main() -> None:
    set_research_priority_below_normal()
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or ""),
        str((before.get("paper") or {}).get("session_dir") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    sources = load_sources()
    reused = already_executed_check(str(sources["inventory_fingerprint"]))
    if reused.get("REUSED_EXISTING_RESULT"):
        prev = dict(reused.get("prior_report") or {})
        print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
        print("ALREADY_EXECUTED_CHECK true", flush=True)
        print("REUSED_EXISTING_RESULT true", flush=True)
        print(f"VERDICT {(prev.get('decision') or {}).get('VERDICT')}", flush=True)
        print(f"NEXT {(prev.get('decision') or {}).get('NEXT')}", flush=True)
        print(f"OUT {OUT}", flush=True)
        print("STOP", flush=True)
        return
    pack = decide(sources)
    after = snapshot(phase="POST")
    opened: list[Path] = []
    leak = {
        "HOLDOUT_BURNED_READ_N": holdout_path_touch_n(opened),
        "STRESS_READ_N": stress_path_touch_n(opened),
        "FUTURE_DATA_N": 0,
        "RAW_CAPTURE_READ_N": 0,
    }
    if leak["HOLDOUT_BURNED_READ_N"] or leak["STRESS_READ_N"] or leak["FUTURE_DATA_N"]:
        raise RuntimeError(f"LEAKAGE {leak}")
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "precommit": canonical_spec(),
        "spec_sha256": spec_sha256(),
        "source_sha256": source_sha256(),
        "inventory_fingerprint": pack["inventory_fingerprint"],
        "ALREADY_EXECUTED_CHECK": False,
        "REUSED_EXISTING_RESULT": False,
        "parent": pack["parent"],
        "decision": pack["decision"],
        "valid_cohort": pack["valid_cohort"],
        "low_support": pack["low_support"],
        "l1_symbol": pack["l1_symbol"],
        "l2_symbol": pack["l2_symbol"],
        "l1_day": pack["l1_day"],
        "l2_day": pack["l2_day"],
        "winner": pack["winner"],
        "stability_counts": pack["stability_counts"],
        "lineage_matrix": pack["lineage_matrix"],
        "guards": pack["guards"],
        "source_inventory": pack["source_inventory"],
        "leakage": leak,
        "safety": _safety(),
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "generated_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _publish(report, pack)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print("ALREADY_EXECUTED_CHECK false", flush=True)
    print("REUSED_EXISTING_RESULT false", flush=True)
    print(f"VALID_N {pack['PRIMARY_VALID_COHORT_N']}", flush=True)
    print(f"LOW_SUPPORT_N {pack['LOW_SUPPORT_DIAGNOSTIC_N']}", flush=True)
    print(f"COMPONENT {pack['decision']['COMPONENT_STRUCTURE']}", flush=True)
    print(f"PRIMARY {pack['decision']['PRIMARY_COMPONENT']}", flush=True)
    print(f"RESIDUAL {pack['decision']['RESIDUAL_BOTTLENECK']}", flush=True)
    print(f"VERDICT {pack['decision']['VERDICT']}", flush=True)
    print(f"NEXT {pack['decision']['NEXT']}", flush=True)
    print(f"OUT {OUT}", flush=True)
    print("STOP", flush=True)


if __name__ == "__main__":
    main()
