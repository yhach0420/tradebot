"""Offline pause protocol. No harvest. No PnL. Runtime 0/0/0. Future unread."""
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
from research.research_pause_and_data_requirements_redesign_v1 import (
    ANALYSIS_ID,
    CERTIFIED,
    LEGACY_DEV_DAYS,
    MAX_RESEARCH_DATE,
    TRUE_OOS,
)
from research.research_pause_and_data_requirements_redesign_v1.analyze import build_answers, build_report_body
from research.research_pause_and_data_requirements_redesign_v1.isolation import (
    CACHE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.research_pause_and_data_requirements_redesign_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.research_pause_and_data_requirements_redesign_v1.spec import (
    already_executed_check,
    pin_parent,
    source_sha256,
)

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
        "QUARANTINE_OPENED": False,
        "PAPER_20260907_READ": False,
        "PROSPECTIVE_DATA_CONSUMED": False,
        "SIZING": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "NEW_ECONOMIC_RUN": False,
        "NEW_OUTCOME_COMPUTE": False,
        "NEW_STRATEGY_CREATED": False,
        "NEW_ENTRY_CREATED": False,
        "NEW_EXIT_CREATED": False,
        "NEW_MODEL_CREATED": False,
        "NEW_THRESHOLD_CREATED": False,
        "NEW_CANDIDATE_LIBRARY_CREATED": False,
        "NEW_EXTERNAL_DATA_ACQUIRED": False,
        "ENTRY_ONLY_METHOD_REVIVAL": False,
        "FUTURE_DATASET_DATES_ASSIGNED": False,
    }


def _objective() -> dict[str, Any]:
    return {
        "PRIMARY_GOAL": (
            "EXISTING_SEALED_DEV_STRATEGY_RESEARCH_JUSTIFIABLY_EXHAUSTED_V1 を受け、"
            "現在の strategy research を正式に pause し、再開するために必要な "
            "DATA REQUIREMENTS / RESEARCH RESET REQUIREMENTS / FRESHNESS / "
            "INDEPENDENCE REQUIREMENTS を定義する。"
        ),
        "PRIMARY_DECISION": "RESTART_REQUIREMENTS",
        "THIS_RUN_ANSWERS": "What must be different before another strategy search is justified?",
        "THIS_RUN_DOES_NOT_ANSWER": "What strategy should we try next?",
        "NON_GOALS": [
            "new ENTRY",
            "new EXIT",
            "new indicator",
            "new information object",
            "new model",
            "new threshold",
            "new feature combination",
            "new strategy library",
            "old strategy rescue",
            "PnL analysis",
            "winner/loser analysis",
            "future validation",
            "Sizing",
        ],
        "STOP_IF_GOAL_MISMATCH": True,
        "THIS_IS_NOT_ANOTHER_STRATEGY_SEARCH": True,
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
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE PAUSE PROTOCOL NO PNL NO FUTURE", flush=True)
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

    parent = pin_parent()
    if not parent.get("ok"):
        raise RuntimeError(f"PARENT_PIN_FAIL {parent}")

    body = build_report_body(parent=parent)
    after = snapshot(phase="POST")
    if stress_path_touch_n([OUT, CACHE]) or holdout_path_touch_n([OUT, CACHE]):
        raise RuntimeError("SEALED_PATH_TOUCH")

    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "LEGACY_DEV_DAYS": list(LEGACY_DEV_DAYS),
        "REUSED_EXISTING_RESULT": False,
        "NEW_ECONOMIC_RUN": False,
        "NEW_OUTCOME_COMPUTE": False,
        "objective_alignment": _objective(),
        "hashes": {
            "SOURCE_SHA256": source_sha256(),
            "PAUSE_PROTOCOL_SPEC_SHA256": body["freeze"]["PAUSE_PROTOCOL_SPEC_SHA256"],
            "REMAINING_RESEARCH_METHOD_SPEC_SHA256": parent["REMAINING_RESEARCH_METHOD_SPEC_SHA256"],
        },
        **body,
        "decision": body["decision"]
        | {
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "SIZING": False,
            "WRITE_OVERLAP_N": overlap,
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
