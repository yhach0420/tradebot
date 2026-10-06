"""Causal path to complete strategy. No Kabu 50. Frozen Validation closed. Runtime 0/0/0."""
from __future__ import annotations

import hashlib
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
from research.causal_path_to_complete_strategy_v1 import ANALYSIS_ID, PROGRAM_ID
from research.causal_path_to_complete_strategy_v1.analyze import build_answers, build_report_body
from research.causal_path_to_complete_strategy_v1.isolation import (
    CACHE,
    FOUNDATION_OUT,
    FREEZE_OUT,
    OUT,
    PREV_DISCOVERY_OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.causal_path_to_complete_strategy_v1.publish import SHEET_ORDER, build_markdown, build_sheets, write_artifacts
from research.causal_path_to_complete_strategy_v1.spec import source_sha256
from research.fixed_daytrade_universe_v1.secrets import assert_no_secret

JST = ZoneInfo("Asia/Tokyo")


def _fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    if not root.is_dir():
        return ""
    for p in sorted(root.iterdir()):
        if p.is_file():
            h.update(p.name.encode("utf-8"))
            h.update(p.read_bytes())
    return h.hexdigest()


def _safety() -> dict[str, Any]:
    return {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "RUNTIME_CHANGED": False,
        "PAPER_CHANGED": False,
        "CAPTURE_CHANGED": False,
        "LIVE_20260914_CHANGED": False,
        "KABU_REGISTRATION_CHANGED": False,
        "KABU_50_APPLIED": False,
        "FROZEN_VALIDATION_OPENED": False,
        "FROZEN_VALIDATION_ACCESSED": False,
        "M1_M11_RESCUED": False,
        "OLD_CONFIRMATION_USED_TO_DESIGN": False,
        "SAME_BAR_CLOSE_ENTRY": False,
        "HISTORICAL_BID_ASK_INFERRED": False,
        "FOUNDATION_V2_REWRITTEN": False,
        "V1_MODIFIED": False,
        "CAUSE_FIRST_OUT_REWRITTEN": False,
        "PAPER_DEPLOYABILITY_COMPRESSION_STARTED": False,
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
    print("SAFETY submit/cancel/live=0/0/0 CAUSAL PATH TO COMPLETE STRATEGY NO KABU50 FROZEN_VAL CLOSED", flush=True)
    print(f"PROGRAM_ID {PROGRAM_ID}", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)

    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    freeze_before = _fingerprint(FREEZE_OUT)
    foundation_before = _fingerprint(FOUNDATION_OUT)
    prev_before = _fingerprint(PREV_DISCOVERY_OUT)
    body = build_report_body()
    after = snapshot(phase="POST")
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Discovery-only causal event universe, path RCA, and at most 3 complete-strategy candidates.",
            "NON_GOALS": [
                "rescue M1-M11 by threshold/clock/horizon/symbol/date/stability-floor relaxation",
                "Kabu 50 runtime universe design",
                "generic 105->48 compression",
                "Frozen Validation tuning",
                "design from old Confirmation / M4 / M6 results",
                "historical Bid/Ask inference",
                "rewrite foundation V2, universe V1, or cause-first artifacts",
                "Paper/runtime/live change",
                "stop 20260914 captures",
                "feed 20260914 into Discovery",
            ],
        },
        "hashes": {"SOURCE_SHA256": source_sha256()},
        **body,
        "decision": dict(body.get("decision") or {}) | {"WRITE_OVERLAP_N": overlap},
        "safety": _safety(),
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "DISCOVERY_TIMESTAMP": datetime.now(JST).isoformat(),
    }
    _publish(report)
    if freeze_before != _fingerprint(FREEZE_OUT):
        raise RuntimeError("V1_FREEZE_MUTATED")
    if foundation_before != _fingerprint(FOUNDATION_OUT):
        raise RuntimeError("FOUNDATION_V2_MUTATED")
    if prev_before != _fingerprint(PREV_DISCOVERY_OUT):
        raise RuntimeError("CAUSE_FIRST_OUT_MUTATED")
    print(f"OUT {OUT}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    _ = CACHE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
