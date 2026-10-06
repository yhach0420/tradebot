"""Offline opening-input capability audit. LEGACY_DEV only. Runtime 0/0/0. No strategy."""
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
from research.opening_logic_input_capability_audit_v1 import (
    ANALYSIS_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    MAX_RESEARCH_DATE,
    TRUE_OOS,
)
from research.opening_logic_input_capability_audit_v1.analyze import assemble, build_answers
from research.opening_logic_input_capability_audit_v1.isolation import (
    CACHE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.opening_logic_input_capability_audit_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.opening_logic_input_capability_audit_v1.scan import AUDIT, audit_capture
from research.opening_logic_input_capability_audit_v1.spec import already_executed_check, pin_parent, source_sha256

JST = ZoneInfo("Asia/Tokyo")


def _safety() -> dict[str, Any]:
    return {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "RUNTIME_CHANGED": False,
        "CAPTURE_CHANGED": False,
        "PAPER_CHANGED": False,
        "MARKET_DATA_PURCHASED": False,
        "HOLDOUT_OPENED": False,
        "STRESS_OPENED": False,
        "QUARANTINE_OPENED": False,
        "PAPER_20260907_READ": False,
        "MBO_WORK_THIS_RUN": False,
        "EXTERNAL_ACQUISITION": False,
        "SIZING": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "STRATEGY_CREATED": False,
        "PNL_COMPUTED": False,
        "OUTCOME_INFORMATION_COMPUTED": False,
        "THRESHOLD_SELECTED": False,
    }


def _objective() -> dict[str, Any]:
    return {
        "PRIMARY_GOAL": (
            "Determine what TradeBot can already observe causally from preopen through actual open "
            "using existing Capture only, so Opening Logic requirements can be frozen without invention."
        ),
        "PRIMARY_DECISION": "OPENING_INPUT_CAPABILITY",
        "DOES_NOT_ANSWER": [
            "GU/GD prediction accuracy",
            "which threshold is profitable",
            "which ENTRY/EXIT is better",
            "PnL",
        ],
        "STOP_IF_GOAL_MISMATCH": True,
        "NO_SECOND_GENERIC_CAPABILITY_AUDIT": True,
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
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 LEGACY_DEV OPENING INPUT AUDIT NO MBO NO PNL", flush=True)
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

    print("PHASE SCAN 10 LEGACY_DEV days preopen through 09:02", flush=True)
    scanned = audit_capture()
    body = assemble(scanned)
    body["decision"] = dict(body.get("decision") or {}) | {
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "SIZING": False,
        "MBO_WORK_THIS_RUN": False,
        "STRATEGY_CREATED": False,
        "PNL_COMPUTED": False,
        "OUTCOME_INFORMATION_COMPUTED": False,
        "THRESHOLD_SELECTED": False,
        "WRITE_OVERLAP_N": overlap,
        "PARENT_ARCHITECTURE_CLOSED": True,
    }

    after = snapshot(phase="POST")
    if stress_path_touch_n([OUT, CACHE]) or holdout_path_touch_n([OUT, CACHE]):
        raise RuntimeError("SEALED_PATH_TOUCH")

    opened = list(AUDIT.get("RAW_MARKET_DATES_OPENED") or [])
    if any(d not in DEVELOPMENT_DAYS for d in opened):
        raise RuntimeError(f"DATE_BOUNDARY {opened}")

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
            "RAW_MARKET_DATES_OPENED": opened,
            "HOLDOUT_READ": False,
            "STRESS_READ": False,
            "QUARANTINE_READ": False,
            "PROSPECTIVE_READ": False,
            "SCAN_STOP": "09:02:00 JST (open availability only; not full session)",
            "OUTCOME_INFORMATION_COMPUTED": False,
        },
        **body,
        "scan_days": scanned.get("days"),
        "harvest_audit": dict(AUDIT),
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
