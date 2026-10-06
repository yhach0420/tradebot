"""Offline native discontinuous-up Full Causal. LEGACY_DEV only. Runtime 0/0/0."""
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
from research.post_open_native_discontinuous_up_repricing_full_strategy_v1 import (
    ANALYSIS_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    MAX_RESEARCH_DATE,
    OPENING_LINE_STATUS,
    STRATEGY_ID,
    TRUE_OOS,
)
from research.post_open_native_discontinuous_up_repricing_full_strategy_v1.analyze import build_answers, decide
from research.post_open_native_discontinuous_up_repricing_full_strategy_v1.isolation import (
    CACHE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.post_open_native_discontinuous_up_repricing_full_strategy_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.post_open_native_discontinuous_up_repricing_full_strategy_v1.semantics import prove_price_status_semantics
from research.post_open_native_discontinuous_up_repricing_full_strategy_v1.spec import (
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
        "PAPER_CHANGED": False,
        "HOLDOUT_OPENED": False,
        "STRESS_OPENED": False,
        "QUARANTINE_OPENED": False,
        "MBO_WORK": False,
        "FUTURES_WORK": False,
        "SIZING": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "POST_RESULT_STATUS_CODE_CHANGE": False,
        "POST_RESULT_ENTRY_CHANGE": False,
        "POST_RESULT_EXIT_CHANGE": False,
        "EXTRA_CANDIDATE_ADDED": False,
        "CALC_PRICE_RETUNE": False,
        "AOP_RESCUE": False,
        "ISQ_RESCUE": False,
        "OPENING_REOPEN": False,
    }


def _objective() -> dict[str, Any]:
    return {
        "PRIMARY_GOAL": (
            "Complete Full Causal long from native discontinuous-up CurrentPriceStatus/"
            "CurrentPriceChangeStatus event. LEGACY_DEV only."
        ),
        "PRIMARY_DECISION_UNIT": "COMPLETE_FULL_CAUSAL_STRATEGY",
        "PARENT": "POST_OPEN_CALC_PRICE_LEAD_ACCEPTANCE_FULL_STRATEGY_V1",
        "ARCHITECTURE": "NON_OPENING POST_OPEN NATIVE_DISCONTINUOUS_UP_REPRICING",
        "OPENING_CURRENT_DATA_LINE_STATUS": OPENING_LINE_STATUS,
        "ISQ_RESOLUTION_STATUS": "CLOSED",
        "AOP_ARCHITECTURE_STATUS": "CLOSED",
        "CALC_PRICE_STATUS": "INPUT_SUPPORTED STRATEGY_CLOSED RETUNE=false",
        "NON_GOALS": [
            "Opening",
            "ISQ rescue",
            "CalcPrice retune",
            "AOP subset/ratio",
            "Breakout/VWAP/TradeFlow/QuoteBurst/Board rescue",
            "guessed ChangeStatus UP code",
            "MBO",
            "futures",
            "Sizing",
        ],
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
    print("SAFETY submit/cancel/live=0/0/0 LEGACY_DEV NATIVE DISCONT NO MBO NO FUTURE", flush=True)
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

    proven = prove_price_status_semantics()
    print(
        "PHASE SEMANTICS",
        proven.get("SEMANTICS_PROVEN"),
        "NORMAL",
        proven.get("NORMAL_STATUS_CODE"),
        "DISCONT",
        proven.get("DISCONTINUOUS_STATUS_CODE"),
        "UP",
        proven.get("UP_CHANGE_STATUS_CODE"),
        "blocker",
        proven.get("BLOCKER"),
        flush=True,
    )

    if not proven.get("SEMANTICS_PROVEN"):
        decision = decide(semantics_ok=False)
    else:
        raise RuntimeError("SEMANTICS_PASS_REQUIRES_FULL_CAUSAL_PATH")

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
        "objective_alignment": _objective(),
        "parent": parent,
        "hashes": {"SOURCE_SHA256": source_sha256()},
        "data_boundary": {
            "ALLOWED": list(DEVELOPMENT_DAYS),
            "RAW_MARKET_DATES_OPENED": [],
            "SESSION": "AM",
            "FLATTEN": [11, 29],
            "HOLDOUT_READ": False,
            "STRESS_READ": False,
            "FUTURE_READ": False,
        },
        "status_semantics": proven,
        "raw_support": {},
        "structural": {},
        "isq_overlap": {},
        "duplicate_check": {},
        "precommit": {"FROZEN": False, "PRECOMMIT_BEFORE_ECONOMICS": False},
        "unit_tests": {},
        "canary": {},
        "episodes": [],
        "signals": [],
        "candidate_evals": [],
        "selected_logic": {},
        "decision": decision
        | {
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "SIZING": False,
            "MBO_WORK": False,
            "WRITE_OVERLAP_N": overlap,
            "INTENDED_STRATEGY_ID": STRATEGY_ID,
            "POST_RESULT_STATUS_CODE_CHANGE": False,
            "POST_RESULT_ENTRY_CHANGE": False,
            "POST_RESULT_EXIT_CHANGE": False,
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
