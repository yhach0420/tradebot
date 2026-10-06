"""Cause-first mechanism discovery. No Kabu 50. Frozen Validation closed. Runtime 0/0/0."""
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
from research.cause_first_mechanism_discovery_v1 import ANALYSIS_ID, PROGRAM_ID
from research.cause_first_mechanism_discovery_v1.analyze import build_answers, build_report_body
from research.cause_first_mechanism_discovery_v1.isolation import (
    CACHE,
    FOUNDATION_OUT,
    FREEZE_OUT,
    OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.cause_first_mechanism_discovery_v1.publish import SHEET_ORDER, build_markdown, build_sheets, write_artifacts
from research.cause_first_mechanism_discovery_v1.spec import source_sha256
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
        "FROZEN_VALIDATION_ACCESSED": False,
        "COMPLETE_STRATEGY_FROZEN": False,
        "SAME_BAR_CLOSE_ENTRY": False,
        "HISTORICAL_BID_ASK_INFERRED": False,
        "FOUNDATION_V2_REWRITTEN": False,
        "V1_MODIFIED": False,
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
    print("SAFETY submit/cancel/live=0/0/0 CAUSE_FIRST MECHANISM DISCOVERY NO KABU50 FROZEN_VAL CLOSED", flush=True)
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
    body = build_report_body()
    after = snapshot(phase="POST")
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Discover cause-first mechanisms on the V2 minute panel without Kabu 50 or Frozen Validation leakage.",
            "NON_GOALS": [
                "complete strategy freeze",
                "ENTRY-only success",
                "Kabu 50 runtime universe design",
                "generic 105->48 compression",
                "Frozen Validation tuning",
                "historical Bid/Ask inference",
                "rewrite foundation V2 or universe V1",
                "Paper/runtime/live change",
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
    print(f"OUT {OUT}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    _ = CACHE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
