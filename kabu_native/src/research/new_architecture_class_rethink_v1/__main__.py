"""Offline NEW_ARCHITECTURE_CLASS_RETHINK_V1. No harvest/Stress/future/economics/rules."""
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
from research.new_architecture_class_rethink_v1 import ANALYSIS_ID
from research.new_architecture_class_rethink_v1.analyze import build_answers, decide
from research.new_architecture_class_rethink_v1.isolation import (
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.new_architecture_class_rethink_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.new_architecture_class_rethink_v1.spec import canonical_spec, source_sha256, spec_sha256

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
        "STRESS_RAW_READ_N": 0,
        "STRESS_FILE_OPEN_N": 0,
        "STRESS_NEW_METRIC_N": 0,
        "STRESS_NEW_REPLAY_N": 0,
        "FUTURE_DATA_N": 0,
        "BURNED_HOLDOUT_READ_N": 0,
        "ECONOMICS_RUN": False,
        "SIZING": False,
        "PRIOR_84_GRID_EXECUTED": False,
        "PFQ_RECON_RUN": False,
        "CANDIDATE_LIBRARY_GENERATED": False,
        "NEW_TRIGGER_DEFINITION_N": 0,
        "NEW_THRESHOLD_DEFINITION_N": 0,
        "COMPOSITE_ARCHITECTURE_EVENT_COUNT_N": 0,
    }


def _publish(report: dict[str, Any]) -> None:
    d = dict(report.get("decision") or {})
    report["answers"] = build_answers(d)
    report["_markdown"] = build_markdown({"answers": report["answers"], "decision": d})
    cov = dict(d.get("coverage") or {})
    sheets = {
        "answers": kv_rows({k: v for k, v in (report.get("answers") or {}).items() if not str(k).startswith("_")}),
        "eligibility": list(d.get("eligibility") or []),
        "priority_trace": list(d.get("priority_trace") or [{"CLASS_ID": "NONE"}]),
        "coverage_diagnostics": kv_rows(d.get("coverage_public")),
        "causality": kv_rows(d.get("causality")),
        "htf_asof_proof": kv_rows((cov.get("htf_asof_proof") or {})),
        "v7_day_coverage": list(cov.get("v7_days") or [{"empty": True}]),
        "st_day_coverage": list(cov.get("st_days") or [{"empty": True}]),
        "closed_classes": [{"ARCHITECTURE_ID": x} for x in list(d.get("closed_classes") or [])] or [{"ARCHITECTURE_ID": "NONE"}],
        "forensic": list(d.get("forensic") or [{"gap": "NONE"}]),
        "hashes": kv_rows(d.get("hashes")),
        "decision": kv_rows(
            {
                "CASE": d.get("CASE"),
                "CASE_NAME": d.get("CASE_NAME"),
                "VERDICT": d.get("VERDICT"),
                "NEXT": d.get("NEXT"),
                "SELECTED_ARCHITECTURE_CLASS": d.get("SELECTED_ARCHITECTURE_CLASS"),
                "ELIGIBLE_CLASS_IDS": d.get("ELIGIBLE_CLASS_IDS"),
                "PNL_USED_TO_SELECT_CLASS": False,
                "EVENT_COUNT_USED_TO_SELECT_CLASS": False,
                "FIFTH_CLASS_CREATED": False,
                "COMPOSITE_ARCHITECTURE_EVENT_COUNT_N": 0,
                "CANDIDATE_LIBRARY_GENERATED": False,
                "ECONOMICS_RUN": False,
            }
        ),
    }
    assert tuple(sheets.keys()) == SHEET_ORDER
    write_artifacts(report, sheets)


def main() -> None:
    set_research_priority_below_normal()
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str(before.get("ACTIVE_CAPTURE_PATH") or ""),
        str(before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    pack = decide()
    opened = [Path(p) for p in list((pack.get("coverage") or {}).get("opened_paths") or [])]
    if stress_path_touch_n(opened) or holdout_path_touch_n(opened):
        raise RuntimeError("SEALED_PATH_TOUCH")
    after = snapshot(phase="POST")
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "precommit": canonical_spec(),
        "spec_sha256": spec_sha256(),
        "source_sha256": source_sha256(),
        "decision": pack,
        "safety": _safety(),
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "generated_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _publish(report)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print(f"CASE {pack['CASE_NAME']}", flush=True)
    print(f"VERDICT {pack['VERDICT']}", flush=True)
    print(f"SELECTED {pack.get('SELECTED_ARCHITECTURE_CLASS')}", flush=True)
    print(f"NEXT {pack['NEXT']}", flush=True)
    print(f"ELIGIBLE {pack.get('ELIGIBLE_CLASS_IDS')}", flush=True)
    print(f"OUT {OUT}", flush=True)
    print("STOP", flush=True)


if __name__ == "__main__":
    main()
