"""Offline method audit. No harvest. No PnL. Runtime 0/0/0."""
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
from research.existing_data_strategy_research_stop_reassessment_v1 import (
    ANALYSIS_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    MAX_RESEARCH_DATE,
    PRIMARY_DECISION_UNIT,
    TRUE_OOS,
)
from research.existing_data_strategy_research_stop_reassessment_v1.analyze import build_answers, build_report_body
from research.existing_data_strategy_research_stop_reassessment_v1.isolation import (
    CACHE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.existing_data_strategy_research_stop_reassessment_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.existing_data_strategy_research_stop_reassessment_v1.spec import (
    already_executed_check,
    pin_fdg,
    pin_object,
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
        "PAPER_20260907_READ": False,
        "SIZING": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "NEW_ECONOMIC_RUN": False,
        "NEW_OUTCOME_READ": False,
        "NEW_STRATEGY_CREATED": False,
        "NEW_ENTRY_CREATED": False,
        "NEW_EXIT_CREATED": False,
        "THRESHOLD_SEARCH": False,
        "FDG_RESCUED": False,
        "NEW_INFORMATION_OBJECT_SEARCH_CLOSED": True,
    }


def _objective() -> dict[str, Any]:
    return {
        "PRIMARY_GOAL": (
            "Decide whether strategy research on existing sealed DEV is genuinely exhausted, "
            "or whether exactly one materially distinct RESEARCH METHOD remains unevaluated "
            "under the corrected Complete Full Causal strategy decision unit."
        ),
        "PRIMARY_DECISION_UNIT": PRIMARY_DECISION_UNIT,
        "NON_GOALS": [
            "new indicator",
            "new information object",
            "new board feature",
            "new depth feature",
            "new ENTRY rule",
            "new EXIT rule",
            "threshold retune",
            "period retune",
            "CAP retune",
            "raw H5 screening",
            "old candidate rescue",
            "Sizing",
            "future/OOS use",
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
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE METHOD AUDIT NO PNL", flush=True)
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

    fdg = pin_fdg()
    obj = pin_object()
    if not fdg.get("ok"):
        raise RuntimeError(f"FDG_PIN_FAIL {fdg}")
    if not obj.get("ok"):
        raise RuntimeError(f"OBJECT_PIN_FAIL {obj}")

    body = build_report_body(fdg=fdg, obj=obj)
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
        "NEW_ECONOMIC_RUN": False,
        "NEW_OUTCOME_READ": False,
        "objective_alignment": _objective(),
        "hashes": {
            "SOURCE_SHA256": source_sha256(),
            "REMAINING_RESEARCH_METHOD_SPEC_SHA256": body["freeze"]["REMAINING_RESEARCH_METHOD_SPEC_SHA256"],
        },
        **body,
        "decision": body["decision"]
        | {
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "SIZING": False,
            "NEW_STRATEGY_CREATED": False,
            "NEW_ENTRY_CREATED": False,
            "NEW_EXIT_CREATED": False,
            "NEW_ECONOMIC_RUN": False,
            "FDG_RESCUED": False,
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
