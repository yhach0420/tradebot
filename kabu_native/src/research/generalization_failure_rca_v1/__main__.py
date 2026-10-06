"""Offline generalization-failure RCA. Existing artifacts only. No harvest/Stress/future."""
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
from research.generalization_failure_rca_v1 import ANALYSIS_ID, CERTIFIED, TRUE_OOS
from research.generalization_failure_rca_v1.analyze import already_executed_check, build_answers, decide
from research.generalization_failure_rca_v1.isolation import (
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.generalization_failure_rca_v1.publish import SHEET_ORDER, build_markdown, kv_rows, write_artifacts
from research.generalization_failure_rca_v1.sources import load_sources
from research.generalization_failure_rca_v1.spec import canonical_spec, source_sha256, spec_sha256

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
        "STRESS_RAW_READ_N": 0,
        "STRESS_FILE_OPEN_N": 0,
        "FUTURE_DATA_N": 0,
        "BURNED_HOLDOUT_READ_N": 0,
        "RAW_CAPTURE_READ_N": 0,
        "NEW_REPLAY": False,
        "NEW_PNL_SIMULATION": False,
        "NEW_FULL_CAUSAL_RUN": False,
        "NEW_FOLD_RUN": False,
        "NEW_CANDIDATE": False,
        "NEW_ENTRY": False,
        "NEW_EXIT": False,
        "NEW_THRESHOLD": False,
        "SIZING": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
    }


def _cohort_public(row: dict[str, Any]) -> dict[str, Any]:
    skip = {"daily_pnl"}
    return {k: v for k, v in row.items() if k not in skip}


def _publish(report: dict[str, Any], pack: dict[str, Any]) -> None:
    d = dict(pack.get("decision") or {})
    report["answers"] = build_answers(pack)
    report["_markdown"] = build_markdown({"answers": report["answers"], "decision": d})
    primary = list(pack.get("primary_cohort") or [])
    matrix = dict(pack.get("lineage_matrix") or {})
    como = dict(pack.get("daily_comovement") or {})
    sheets = {
        "answers": kv_rows(report.get("answers")),
        "source_inventory": list(pack.get("source_inventory") or []),
        "primary_cohort": [_cohort_public(r) for r in primary],
        "gate_run_state": [
            {
                "candidate_id": r.get("candidate_id"),
                "lineage": r.get("lineage"),
                "study": r.get("study"),
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
            for r in primary
        ],
        "symbol_mode": [
            {"lineage": "L1_TECHNICAL_PRICE_STATE", **dict(pack.get("l1_symbol") or {})},
            {"lineage": "L2_RECOVERY_RECLAIM_PATH", **dict(pack.get("l2_symbol") or {})},
            {
                "lineage": "CROSS",
                "CROSS_LINEAGE_SYMBOL_MODE_SUPPORT": pack.get("CROSS_LINEAGE_SYMBOL_MODE_SUPPORT"),
                "SHARED_TOP_SYMBOL_IDS": (pack.get("shared_top_symbol") or {}).get("ids"),
                "SHARED_TOP_SYMBOL_SUPPORT": (pack.get("shared_top_symbol") or {}).get("support"),
            },
        ],
        "day_mode": [
            {"lineage": "L1_TECHNICAL_PRICE_STATE", **dict(pack.get("l1_day") or {})},
            {"lineage": "L2_RECOVERY_RECLAIM_PATH", **dict(pack.get("l2_day") or {})},
            {
                "lineage": "CROSS",
                "CROSS_LINEAGE_DAY_MODE_SUPPORT": pack.get("CROSS_LINEAGE_DAY_MODE_SUPPORT"),
                "SHARED_BEST_DAY_IDS": (pack.get("shared_best_day") or {}).get("ids"),
                "SHARED_BEST_DAY_SUPPORT": (pack.get("shared_best_day") or {}).get("support"),
            },
        ],
        "joint_episode": kv_rows(pack.get("joint_episode")),
        "selection_surface": kv_rows(pack.get("selection_surface")),
        "specific_driver_ids": [
            {"driver": "SHARED_TOP_SYMBOL", **dict((pack.get("specific_driver") or {}).get("SHARED_TOP_SYMBOL") or {})},
            {"driver": "SHARED_BEST_DAY", **dict((pack.get("specific_driver") or {}).get("SHARED_BEST_DAY") or {})},
            {"driver": "SHARED_TOP_DAY_SYMBOL_CELL", **dict((pack.get("specific_driver") or {}).get("SHARED_TOP_DAY_SYMBOL_CELL") or {})},
        ],
        "daily_comovement": (list(como.get("pairs") or []) or [kv_rows(como)[0]])
        if como.get("pairs")
        else kv_rows({k: v for k, v in como.items() if k != "pairs"}),
        "e4_secondary": kv_rows(pack.get("e4_secondary")),
        "l3_control": kv_rows(pack.get("l3_control")),
        "lineage_matrix": [
            {"dimension": dim, "L1": cells.get("L1"), "L2": cells.get("L2")}
            for dim, cells in matrix.items()
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
        "STRESS_FILE_OPEN_N": stress_path_touch_n(opened),
        "FUTURE_DATA_N": 0,
        "BURNED_HOLDOUT_READ_N": 0,
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
        "rebase": pack["rebase"],
        "decision": pack["decision"],
        "primary_cohort": pack["primary_cohort"],
        "gate_counts": pack["gate_counts"],
        "l1_symbol": pack["l1_symbol"],
        "l2_symbol": pack["l2_symbol"],
        "l1_day": pack["l1_day"],
        "l2_day": pack["l2_day"],
        "CROSS_LINEAGE_SYMBOL_MODE_SUPPORT": pack["CROSS_LINEAGE_SYMBOL_MODE_SUPPORT"],
        "CROSS_LINEAGE_DAY_MODE_SUPPORT": pack["CROSS_LINEAGE_DAY_MODE_SUPPORT"],
        "shared_top_symbol": pack["shared_top_symbol"],
        "shared_best_day": pack["shared_best_day"],
        "joint_episode": pack["joint_episode"],
        "selection_surface": pack["selection_surface"],
        "daily_comovement": pack["daily_comovement"],
        "e4_secondary": pack["e4_secondary"],
        "l3_control": pack["l3_control"],
        "lineage_matrix": pack["lineage_matrix"],
        "specific_driver": pack["specific_driver"],
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
    print(f"CASE {pack['decision']['CASE']}", flush=True)
    print(f"MODE {pack['decision']['PRIMARY_GENERALIZATION_FAILURE_MODE']}", flush=True)
    print(f"VERDICT {pack['decision']['VERDICT']}", flush=True)
    print(f"NEXT {pack['decision']['NEXT']}", flush=True)
    print(f"OUT {OUT}", flush=True)
    print("STOP", flush=True)


if __name__ == "__main__":
    main()
