"""Offline discovery-label calibration. Existing DEV ledgers only. Runtime/Capture writes 0."""
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
from research.discovery_search_space_reassessment_v1 import ANALYSIS_ID, CERTIFIED, TRUE_OOS
from research.discovery_search_space_reassessment_v1.analyze import already_executed_check, build_answers, decide
from research.discovery_search_space_reassessment_v1.isolation import (
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.discovery_search_space_reassessment_v1.publish import SHEET_ORDER, build_markdown, build_sheets, write_artifacts
from research.discovery_search_space_reassessment_v1.spec import source_sha256

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
        "NEW_MECHANISM_SEARCH": False,
        "NEW_STRATEGY_CREATED": False,
        "FULL_STRATEGY_FROZEN": False,
        "CONTROLS_REOPENED": False,
        "CONTROLS_RETUNED": False,
        "CONTROLS_CERTIFIED": False,
    }


def _publish(report: dict[str, Any], pack: dict[str, Any]) -> None:
    d = dict(pack.get("decision") or {})
    report["answers"] = build_answers(pack)
    report["_markdown"] = build_markdown({"answers": report["answers"], "decision": d})
    sheets = build_sheets(report, pack)
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
    src_hash = source_sha256()
    reused = already_executed_check(src_hash)
    if reused.get("REUSED_EXISTING_RESULT"):
        prev = dict(reused.get("prior_report") or {})
        print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
        print("ALREADY_EXECUTED_CHECK true", flush=True)
        print(f"VERDICT {(prev.get('decision') or {}).get('VERDICT')}", flush=True)
        print(f"NEXT {(prev.get('decision') or {}).get('NEXT')}", flush=True)
        print(f"OUT {OUT}", flush=True)
        print("STOP", flush=True)
        return
    pack = decide(source_hash=src_hash, use_cache=True)
    after = snapshot(phase="POST")
    leak = {
        "HOLDOUT_BURNED_READ_N": holdout_path_touch_n([]),
        "STRESS_READ_N": stress_path_touch_n([]),
        "FUTURE_DATA_N": int((pack.get("data") or {}).get("FUTURE_DATE_READ_N") or 0),
    }
    if leak["HOLDOUT_BURNED_READ_N"] or leak["STRESS_READ_N"] or leak["FUTURE_DATA_N"]:
        raise RuntimeError(f"LEAKAGE {leak}")
    d = dict(pack.get("decision") or {})
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "source_sha256": src_hash,
        "ALREADY_EXECUTED_CHECK": False,
        "REUSED_EXISTING_RESULT": False,
        "pin": pack.get("pin"),
        "objective_alignment": pack.get("objective_alignment"),
        "P1": pack.get("P1"),
        "P2": pack.get("P2"),
        "NEGATIVE": pack.get("NEGATIVE"),
        "decision": d,
        "data": pack.get("data"),
        "AUDIT": pack.get("AUDIT"),
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
    print(f"P1_AVAILABLE {(pack.get('P1') or {}).get('CONTROL_EXECUTED_COHORT_AVAILABLE')}", flush=True)
    print(f"P2_AVAILABLE {(pack.get('P2') or {}).get('CONTROL_EXECUTED_COHORT_AVAILABLE')}", flush=True)
    print(f"FIXED_H5_HARD_GATE_VALID {d.get('FIXED_H5_HARD_GATE_VALID')}", flush=True)
    print(f"SECONDARY_CAUSE {d.get('SECONDARY_CAUSE')}", flush=True)
    print(f"VERDICT {d.get('VERDICT')}", flush=True)
    print(f"NEXT {d.get('NEXT')}", flush=True)
    print(f"OUT {OUT}", flush=True)
    print("STOP", flush=True)


if __name__ == "__main__":
    main()
