"""Offline delayed-open buy-special strategy. LEGACY_DEV only. Runtime 0/0/0."""
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
from research.delayed_open_buy_special_quote_release_full_strategy_v1 import (
    ANALYSIS_ID,
    CANARY_ROW_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    MAX_RESEARCH_DATE,
    TRUE_OOS,
)
from research.delayed_open_buy_special_quote_release_full_strategy_v1.analyze import build_answers, decide
from research.delayed_open_buy_special_quote_release_full_strategy_v1.duplicates import audit_duplicates
from research.delayed_open_buy_special_quote_release_full_strategy_v1.isolation import (
    CACHE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.delayed_open_buy_special_quote_release_full_strategy_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.delayed_open_buy_special_quote_release_full_strategy_v1.semantics import prove_market_states
from research.delayed_open_buy_special_quote_release_full_strategy_v1.spec import already_executed_check, pin_parent, source_sha256
from research.delayed_open_buy_special_quote_release_full_strategy_v1.strategy import strategy_spec
from research.existing_data_entry_aligned_full_causal_logic_completion_v1.harvest import harvest_development
from research.full_causal_mechanism_discovery_v1.analyze import canary_parity, evaluate_strategy

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
        "PAPER_20260907_READ": False,
        "MBO_WORK": False,
        "FUTURES_WORK": False,
        "EXTERNAL_ACQUISITION": False,
        "SIZING": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "POST_RESULT_ENTRY_CHANGE": False,
        "POST_RESULT_EXIT_CHANGE": False,
        "SECOND_STRATEGY_ATTEMPTED": False,
    }


def _objective() -> dict[str, Any]:
    return {
        "PRIMARY_GOAL": (
            "Complete one Full Causal strategy: delayed buy-side special quote → continuous open → "
            "OpeningPrice acceptance long → acceptance-loss exit. Existing LEGACY_DEV data only."
        ),
        "PRIMARY_DECISION_UNIT": "COMPLETE_FULL_CAUSAL_STRATEGY",
        "PARENT": "OPENING_LOGIC_PREOPEN_INPUT_NOT_SUPPORTED_V1",
        "NON_GOALS": [
            "preopen trajectory",
            "indicative-open prediction",
            "gap-size search",
            "PreviousClose reclaim",
            "MBO",
            "futures",
            "Sizing",
            "future validation",
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
    print("SAFETY submit/cancel/live=0/0/0 LEGACY_DEV DELAYED-OPEN BSQ NO MBO NO FUTURE", flush=True)
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

    dup = audit_duplicates()
    print("PHASE DUPLICATE", flush=True)
    canary: dict[str, Any] = {}
    canary_ok = False
    if not (dup.get("EXACT_COMPLETE_STRATEGY_DUPLICATE") or dup.get("MATERIAL_SEMANTIC_DUPLICATE")):
        print("PHASE CANARY R2_X1_Z3", flush=True)
        har = harvest_development()
        if not har.get("ok"):
            raise RuntimeError(f"CANARY_HARVEST_FAIL {har.get('blocker')}")
        canary_ev = evaluate_strategy(
            CANARY_ROW_ID,
            list((har.get("rows_by") or {}).get(CANARY_ROW_ID) or []),
            days=list(DEVELOPMENT_DAYS),
            meta={"SELECTABLE": False},
            compute_g6=False,
            compute_blocks=False,
        )
        canary = canary_parity(canary_ev)
        canary_ok = bool(canary.get("HARD_PASS"))

    proven = prove_market_states()
    print("PHASE SEMANTICS BUY_SPECIAL_DIRECTION_PROVEN", proven.get("BUY_SPECIAL_DIRECTION_PROVEN"), flush=True)

    precommit = {**strategy_spec(), "FROZEN": False, "FULL_STRATEGY_SPEC_SHA256_DO_BSQ_V1": None}
    decision = decide(dup=dup, canary_ok=canary_ok, proven=proven)

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
            "RAW_MARKET_DATES_OPENED": list(DEVELOPMENT_DAYS) if canary_ok else [],
            "HOLDOUT_READ": False,
            "STRESS_READ": False,
            "QUARANTINE_READ": False,
            "FUTURE_READ": False,
            "NOTE": "Canary reused sealed LEGACY_DEV harvest cache. Strategy economics not opened after direction stop.",
        },
        "already_executed": dup,
        "canary": canary,
        "state_semantics": proven,
        "strategy_precommit": precommit,
        "opening_episodes": [],
        "release_events": [],
        "accept_bars": [],
        "signal_rows": [],
        "execution": {"ID": "X1_IMMEDIATE_ASK", "USED": False},
        "portfolio": {"CAP": 5, "USED": False},
        "exits": {"TECHNICAL": "Z_RELEASE_PRICE_ACCEPTANCE_LOST", "USED": False},
        "selected_eval": {},
        "daily_rows": [],
        "symbol_rows": [],
        "diagnostics": {"NOTE": "No strategy diagnostics; stopped before episode construction."},
        "decision": decision
        | {
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "SIZING": False,
            "MBO_WORK": False,
            "WRITE_OVERLAP_N": overlap,
            "POST_RESULT_ENTRY_CHANGE": False,
            "POST_RESULT_EXIT_CHANGE": False,
            "SECOND_STRATEGY_ATTEMPTED": False,
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
