"""Offline Phase 0 foundation. No strategy. No purchase. Runtime 0/0/0."""
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
from research.fixed_universe_historical_foundation_v1 import (
    ANALYSIS_ID,
    CERTIFIED,
    ENTRY,
    EXIT,
    TRUE_OOS,
)
from research.fixed_universe_historical_foundation_v1.analyze import build_answers, build_report_body
from research.fixed_universe_historical_foundation_v1.isolation import (
    CACHE,
    OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.fixed_universe_historical_foundation_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.fixed_universe_historical_foundation_v1.spec import already_executed_check, source_sha256

JST = ZoneInfo("Asia/Tokyo")


def _safety() -> dict[str, Any]:
    return {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "ENTRY": bool(ENTRY),
        "EXIT": bool(EXIT),
        "RUNTIME_CHANGED": False,
        "PAPER_CHANGED": False,
        "CAPTURE_CHANGED": False,
        "LIVE_20260914_CHANGED": False,
        "DAY1_MINING_REOPENED": False,
        "DATA_PURCHASED": False,
        "HISTORICAL_DATA_DOWNLOADED": False,
        "STRATEGY_SEARCH_STARTED": False,
        "PROFIT_OPTIMIZATION": False,
        "UNIVERSE_FROZEN": False,
        "SIZING": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
    }


def _objective() -> dict[str, Any]:
    return {
        "PRIMARY_GOAL": (
            "Change the primary research track from short NEW_INFO capture ENTRY mining "
            "to Fixed Day-Trade Universe x long historical 1-minute x external context "
            "x sector x simple technical -> mechanism discovery."
        ),
        "PHASE": 0,
        "THIS_RUN_ANSWERS": "universe design, provider audit, time alignment, split",
        "THIS_RUN_DOES_NOT_ANSWER": "what strategy to trade",
        "NON_GOALS": [
            "strategy search",
            "profit optimization",
            "large grid",
            "new ENTRY",
            "new EXIT",
            "data purchase",
            "historical download",
            "continuous futures autosplice",
            "20260914 live change",
            "Day1 mining reopen",
            "Runtime change",
            "Paper change",
        ],
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
    print("SAFETY submit/cancel/live=0/0/0 PHASE0 FOUNDATION NO STRATEGY NO PURCHASE", flush=True)
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
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")

    body = build_report_body()
    after = snapshot(phase="POST")
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "REUSED_EXISTING_RESULT": False,
        "objective_alignment": _objective(),
        "hashes": {"SOURCE_SHA256": source_sha256()},
        **body,
        "decision": body["decision"] | {"WRITE_OVERLAP_N": overlap, "TRUE_OOS": False, "CERTIFIED": False},
        "safety": _safety(),
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "FREEZE_TIMESTAMP": datetime.now(JST).isoformat(),
    }
    _publish(report)
    print(f"OUT {OUT}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    _ = CACHE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
