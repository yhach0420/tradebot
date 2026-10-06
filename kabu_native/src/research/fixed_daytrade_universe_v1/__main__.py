"""Offline universe freeze. No strategy. No PnL. Runtime 0/0/0. Fail closed without 60d daily."""
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
from research.fixed_daytrade_universe_v1 import ANALYSIS_ID, ENTRY, EXIT
from research.fixed_daytrade_universe_v1.analyze import build_answers, build_report_body
from research.fixed_daytrade_universe_v1.isolation import (
    CACHE,
    OUT,
    REF_JQUANTS,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.fixed_daytrade_universe_v1.phase0_fix import patch_phase0_autosplice
from research.fixed_daytrade_universe_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.fixed_daytrade_universe_v1.secrets import assert_no_secret
from research.fixed_daytrade_universe_v1.spec import already_executed_check, source_sha256

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
        "STRATEGY_SEARCH_STARTED": False,
        "PANEL_BUILT": False,
        "OUTCOME_USED": False,
        "YFINANCE_USED": False,
        "UNIVERSE_REGISTRATION_CHANGED": False,
    }


def _objective() -> dict[str, Any]:
    return {
        "PRIMARY_GOAL": "Acquire official J-Quants daily 60d and retry Fixed Day-Trade Universe freeze, or fail closed.",
        "NON_GOALS": [
            "strategy search",
            "ENTRY search",
            "EXIT search",
            "PnL",
            "future-return ranking",
            "parameter search",
            "winner/loser selection",
            "20260914 live change",
            "qualitative CORE_LIQUID freeze",
        ],
    }


def _publish(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    assert_no_secret(report, where="report")
    sheets = build_sheets(report)
    assert tuple(sheets.keys()) == SHEET_ORDER
    assert_no_secret(sheets, where="sheets")
    write_artifacts(report, sheets)


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 UNIVERSE FREEZE NO STRATEGY NO PNL", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print("JQUANTS_KEY_PRESENT_BOOL_ONLY see report.daily_source.credentials.present", flush=True)

    reused = already_executed_check()
    if reused.get("REUSED_EXISTING_RESULT"):
        print("REUSE_EXISTING_FROZEN_RESULT=true", flush=True)
        print("STOP.", flush=True)
        return 0

    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")

    phase0_fix = patch_phase0_autosplice()
    body = build_report_body(phase0_fix=phase0_fix)
    after = snapshot(phase="POST")
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "REUSED_EXISTING_RESULT": False,
        "objective_alignment": _objective(),
        "hashes": {"SOURCE_SHA256": source_sha256()},
        **body,
        "decision": body["decision"] | {"WRITE_OVERLAP_N": overlap},
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
    print(f"PHASE0_AUTOSPLICE_FIX {phase0_fix.get('from')} -> {phase0_fix.get('to')}", flush=True)
    print("STOP.", flush=True)
    _ = CACHE
    _ = REF_JQUANTS
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
