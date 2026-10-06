"""Offline information-object expansion decision. No PnL. Runtime 0/0/0."""
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
from research.discovery_information_object_expansion_decision_v1 import (
    ANALYSIS_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    MAX_RESEARCH_DATE,
    TRUE_OOS,
)
from research.discovery_information_object_expansion_decision_v1.analyze import (
    build_answers,
    objective_alignment,
    run_decision,
)
from research.discovery_information_object_expansion_decision_v1.isolation import (
    CACHE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.discovery_information_object_expansion_decision_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.discovery_information_object_expansion_decision_v1.schema_audit import AUDIT, audit_dev_schema
from research.discovery_information_object_expansion_decision_v1.spec import already_executed_check, source_sha256

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
        "PAPER_20260907_READ": False,
        "SIZING": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "THRESHOLD_SEARCH": False,
        "PNL_READ": False,
        "MARKOUT_READ": False,
        "OUTCOME_READ_N": 0,
        "ECONOMICS_RUN": False,
        "CURRENT_42_RETUNE": False,
        "PREOPEN_EXECUTION_VALID": False,
    }


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
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE INFORMATION OBJECT EXPANSION DECISION V1", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)

    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        raise RuntimeError("WRITE_OVERLAP")

    print("PHASE DEV schema presence audit (no returns)", flush=True)
    schema = audit_dev_schema(use_cache=True)
    print(
        f"SCHEMA ok={schema.get('ok')} depth={schema.get('full_depth_available')} "
        f"event={schema.get('event_flow_available')} preopen={schema.get('preopen_available')} "
        f"prior={schema.get('prior_session_available')}",
        flush=True,
    )

    packed = run_decision(schema)
    if not packed["parent"].get("ok"):
        raise RuntimeError(f"PARENT_PIN_FAIL {packed['parent']}")
    if not packed["ioar"].get("ok"):
        raise RuntimeError(f"IOAR_PIN_FAIL {packed['ioar']}")
    if not packed["ueia"].get("ok"):
        raise RuntimeError(f"UEIA_PIN_FAIL {packed['ueia']}")

    spec_sha = str(packed["spec"]["INFORMATION_OBJECT_SPEC_SHA256"])
    reused = already_executed_check(spec_sha=spec_sha)
    print(f"INFORMATION_OBJECT_SPEC_SHA256 {spec_sha}", flush=True)
    if reused.get("REUSED_EXISTING_RESULT"):
        print("REUSE_EXISTING_RESULT=true", flush=True)
        print("STOP.", flush=True)
        return 0

    after = snapshot(phase="POST")
    research_touched = [OUT, CACHE]
    if stress_path_touch_n(research_touched) or holdout_path_touch_n(research_touched):
        raise RuntimeError("SEALED_PATH_TOUCH")

    src_sha = source_sha256()
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "REUSED_EXISTING_RESULT": False,
        "objective_alignment": objective_alignment(),
        "parent": packed["parent"],
        "ioar": packed["ioar"],
        "ueia": packed["ueia"],
        "schema": {
            k: v
            for k, v in schema.items()
            if k != "days"
        } | {"days": schema.get("days")},
        "prior_use": packed["prior_use"],
        "raw_objects": packed["raw_objects"],
        "underexplored": packed["underexplored"],
        "eligible": packed["eligible"],
        "selected": packed["selected"],
        "spec": packed["spec"],
        "hashes": {
            "INFORMATION_OBJECT_SPEC_SHA256": spec_sha,
            "SOURCE_SHA256": src_sha,
        },
        "decision": packed["decision"],
        "safety": _safety() | {"schema_AUDIT": dict(AUDIT)},
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "FREEZE_TIMESTAMP": datetime.now(JST).isoformat(),
        "OUTCOME_READ_N": 0,
        "ECONOMICS_RUN": False,
    }
    _publish(report)
    print(f"VERDICT {packed['decision']['VERDICT']}", flush=True)
    print(f"NEXT {packed['decision']['NEXT']}", flush=True)
    print(f"SELECTED {packed['decision'].get('SELECTED_OBJECT_ID')}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
