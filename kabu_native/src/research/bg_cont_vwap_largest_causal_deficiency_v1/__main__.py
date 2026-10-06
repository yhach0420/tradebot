"""BG_CONT_VWAP exit family. No Kabu 50. Frozen Validation closed. Runtime 0/0/0."""
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
from research.bg_cont_vwap_largest_causal_deficiency_v1 import ANALYSIS_ID, PROGRAM_ID
from research.bg_cont_vwap_largest_causal_deficiency_v1.analyze import build_answers, build_report_body
from research.bg_cont_vwap_largest_causal_deficiency_v1.isolation import (
    ATLAS_OUT,
    CACHE,
    CAUSAL_PATH_OUT,
    CAUSE_FIRST_OUT,
    CME_OUT,
    EXTERNAL_OUT,
    FOUNDATION_OUT,
    FREEZE_OUT,
    HIGHER_MAG_OUT,
    MAPPING_OUT,
    OUT,
    PARENT_OUT,
    RCA_OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.bg_cont_vwap_largest_causal_deficiency_v1.publish import SHEET_ORDER, build_markdown, build_sheets, write_artifacts
from research.bg_cont_vwap_largest_causal_deficiency_v1.spec import source_sha256
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
        "OLD_CONFIRMATION_USED_TO_DESIGN": False,
        "HM1_TUNED": False,
        "YFINANCE_USED": False,
        "FIVE_MINUTE_GRID": False,
        "NEW_PAID_DATA": False,
        "PROMOTED": False,
        "V27_BOLTED": False,
        "ENTRY_CHANGED": False,
        "TAKE_PROFIT_TESTED": False,
        "WINNER_FILTER": False,
        "SYMBOL_EXCLUSION": False,
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
    print("SAFETY submit/cancel/live=0/0/0 EXIT FAMILY ONLY NO V27 NO TP NO KABU50 FROZEN_VAL CLOSED", flush=True)
    print(f"PROGRAM_ID {PROGRAM_ID}", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    fps = {
        k: _fingerprint(p)
        for k, p in {
            "rca": RCA_OUT,
            "parent": PARENT_OUT,
            "atlas": ATLAS_OUT,
            "freeze": FREEZE_OUT,
            "foundation": FOUNDATION_OUT,
            "cause": CAUSE_FIRST_OUT,
            "path": CAUSAL_PATH_OUT,
            "mag": HIGHER_MAG_OUT,
            "ext": EXTERNAL_OUT,
            "map": MAPPING_OUT,
            "cme": CME_OUT,
        }.items()
    }
    body = build_report_body()
    after = snapshot(phase="POST")
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Test whether one-bar VWAP-loss sensitivity is the causally rescuable part of BG_CONT_VWAP giveback.",
            "NON_GOALS": [
                "V27 / EMA / RSI / MACD",
                "take-profit / winner filters / symbol exclusion",
                "ENTRY changes",
                "MFE threshold grid",
                "3-bar persistence",
                "promote Complete Strategy",
                "open Frozen Validation",
                "design from old Confirmation",
                "Kabu 50",
                "paid data",
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
    for k, p in {
        "rca": RCA_OUT,
        "parent": PARENT_OUT,
        "atlas": ATLAS_OUT,
        "freeze": FREEZE_OUT,
        "foundation": FOUNDATION_OUT,
        "cause": CAUSE_FIRST_OUT,
        "path": CAUSAL_PATH_OUT,
        "mag": HIGHER_MAG_OUT,
        "ext": EXTERNAL_OUT,
        "map": MAPPING_OUT,
        "cme": CME_OUT,
    }.items():
        if fps[k] != _fingerprint(p):
            raise RuntimeError(f"PRIOR_MUTATED_{k}")
    print(f"OUT {OUT}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    _ = CACHE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
