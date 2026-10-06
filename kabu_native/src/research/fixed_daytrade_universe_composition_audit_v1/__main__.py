"""Composition audit runner. No strategy. V1 immutable. Runtime 0/0/0."""
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
from research.fixed_daytrade_universe_composition_audit_v1 import ANALYSIS_ID, ENTRY, EXIT
from research.fixed_daytrade_universe_composition_audit_v1.analyze import build_answers, build_report_body
from research.fixed_daytrade_universe_composition_audit_v1.isolation import (
    CACHE,
    FREEZE_OUT,
    OUT,
    REF_JQUANTS,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.fixed_daytrade_universe_composition_audit_v1.publish import SHEET_ORDER, build_markdown, build_sheets, write_artifacts
from research.fixed_daytrade_universe_composition_audit_v1.spec import already_executed_check, source_sha256
from research.fixed_daytrade_universe_v1.secrets import assert_no_secret

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
        "UNIVERSE_CHANGED": False,
        "V1_REWRITTEN": False,
        "PNL_USED": False,
        "FUTURE_RETURN_USED": False,
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
    print("SAFETY submit/cancel/live=0/0/0 COMPOSITION AUDIT NO STRATEGY V1 IMMUTABLE", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print(f"FREEZE_ARTIFACTS {FREEZE_OUT}", flush=True)

    _ = already_executed_check()
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
        "REUSED_EXISTING_RESULT": False,
        "objective_alignment": {
            "PRIMARY_GOAL": "Audit frozen 45 factor coverage for external→sector→name research.",
            "NON_GOALS": [
                "strategy search",
                "ENTRY/EXIT",
                "PnL",
                "future-return optimization",
                "Paper result",
                "20260914 outcome",
                "rewrite FIXED_DAYTRADE_UNIVERSE_V1",
            ],
        },
        "hashes": {"SOURCE_SHA256": source_sha256()},
        **body,
        "decision": dict(body.get("decision") or {}) | {"WRITE_OVERLAP_N": overlap},
        "safety": _safety(),
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "AUDIT_TIMESTAMP": datetime.now(JST).isoformat(),
    }
    freeze_before = (FREEZE_OUT / "universe_manifest.json").read_bytes()
    _publish(report)
    freeze_after = (FREEZE_OUT / "universe_manifest.json").read_bytes()
    if freeze_before != freeze_after:
        raise RuntimeError("V1_FREEZE_MUTATED")
    print(f"OUT {OUT}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    _ = CACHE
    _ = REF_JQUANTS
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
