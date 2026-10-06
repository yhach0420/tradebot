"""Offline FLEX MBO connection-spec resolution. Local docs only. No account. Runtime 0/0/0."""
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
from research.flex_mbo_connection_spec_resolution_v1 import (
    ANALYSIS_ID,
    CERTIFIED,
    LEGACY_DEV_DAYS,
    MAX_RESEARCH_DATE,
    TRUE_OOS,
)
from research.flex_mbo_connection_spec_resolution_v1.analyze import build_answers, build_report_body
from research.flex_mbo_connection_spec_resolution_v1.isolation import (
    CACHE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.flex_mbo_connection_spec_resolution_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.flex_mbo_connection_spec_resolution_v1.spec import (
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
        "RUNTIME_CHANGED": False,
        "CAPTURE_CHANGED": False,
        "MARKET_DATA_PURCHASED": False,
        "HISTORICAL_DATA_DOWNLOADED": False,
        "LIVE_FEED_STARTED": False,
        "SUBSCRIPTION_CHANGED": False,
        "CONTRACT_SIGNED": False,
        "ACCOUNT_CREATED": False,
        "PROVIDER_CONTACTED": False,
        "API_KEY_CREATED": False,
        "COLLECTOR_IMPLEMENTED": False,
        "REPLAY_ENGINE_IMPLEMENTED": False,
        "HOLDOUT_OPENED": False,
        "STRESS_OPENED": False,
        "QUARANTINE_OPENED": False,
        "PAPER_20260907_READ": False,
        "SIZING": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "RESEARCH_METHOD_FROZEN": False,
        "NEW_DEV_DATES_ASSIGNED": False,
        "TRUE_OOS_DATES_ASSIGNED": False,
    }


def _objective() -> dict[str, Any]:
    return {
        "PRIMARY_GOAL": (
            "Parent feasibility で未解決だった F8 event ordering replayability と "
            "F9 deterministic book reconstruction を、正式な FLEX MBO connection/specification "
            "documents から証明可能か判定する。"
        ),
        "SECONDARY": "protocol resolution後に残る legal/storage blockers を正確に分離する。",
        "PRIMARY_DECISION": "CONNECTION_SPEC_RESOLUTION_OR_ACCESS_REQUIRED",
        "THIS_RUN_DOES_NOT_DECIDE": [
            "alpha",
            "strategy",
            "ENTRY",
            "EXIT",
            "model",
            "purchase approval",
            "subscription",
            "future-use permission",
        ],
        "STRATEGY_RESEARCH_STATUS": "PAUSED",
        "RESTART_ALLOWED": False,
        "STOP_IF_GOAL_MISMATCH": True,
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
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE FLEX MBO SPEC RESOLUTION NO ACCOUNT NO MARKET DATA", flush=True)
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
        "objective_alignment": _objective(),
        "hashes": {
            "SOURCE_SHA256": source_sha256(),
            "EXTERNAL_INFORMATION_SOURCE_POLICY_SHA256": parent["EXTERNAL_INFORMATION_SOURCE_POLICY_SHA256"],
            "FEASIBILITY_SPEC_SHA256": parent["FEASIBILITY_SPEC_SHA256"],
            "RESOLUTION_SPEC_SHA256": body["decision"]["RESOLUTION_SPEC_SHA256"],
        },
        **body,
        "decision": body["decision"]
        | {
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "SIZING": False,
            "WRITE_OVERLAP_N": overlap,
            "MARKET_DATA_PURCHASED": False,
            "PROVIDER_CONTACTED": False,
            "ACCOUNT_CREATED": False,
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
