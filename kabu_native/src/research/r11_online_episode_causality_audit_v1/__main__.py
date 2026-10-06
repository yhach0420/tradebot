"""R11 online episode causality audit. No Kabu 50. Frozen Validation closed. Confirmation closed. Runtime 0/0/0."""
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
from research.fixed_daytrade_universe_v1.secrets import assert_no_secret
from research.r11_online_episode_causality_audit_v1 import ANALYSIS_ID, PROGRAM_ID
from research.r11_online_episode_causality_audit_v1.analyze import build_answers, build_report_body
from research.r11_online_episode_causality_audit_v1.isolation import (
    ATLAS_OUT,
    BG_EXIT_OUT,
    CACHE,
    CAUSAL_PATH_OUT,
    CAUSE_FIRST_OUT,
    CME_OUT,
    DISC_OUT,
    EXTERNAL_OUT,
    FOUNDATION_OUT,
    FREEZE_OUT,
    HIGHER_MAG_OUT,
    MAPPING_OUT,
    OUT,
    PARENT_OUT,
    R11_FREEZE_OUT,
    RCA_OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.r11_online_episode_causality_audit_v1.publish import SHEET_ORDER, build_markdown, build_sheets, write_artifacts
from research.r11_online_episode_causality_audit_v1.spec import source_sha256

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
        "KABU_50_APPLIED": False,
        "FROZEN_VALIDATION_OPENED": False,
        "OLD_CONFIRMATION_OPENED": False,
        "PARAMETER_CHANGED": False,
        "THRESHOLD_TUNED": False,
        "EXIT_CHANGED": False,
        "CAP_CHANGED": False,
        "SELECTED_DELAYED_OR_FIRST": False,
        "YFINANCE_USED": False,
        "NEW_PAID_DATA": False,
        "PROMOTED": False,
        "V27_BOLTED": False,
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
    print("SAFETY submit/cancel/live=0/0/0 R11 ONLINE CAUSALITY NO RETUNE FROZEN_VAL CLOSED CONFIRMATION CLOSED", flush=True)
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
            "r11f": R11_FREEZE_OUT,
            "disc": DISC_OUT,
            "bg": BG_EXIT_OUT,
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
            "PRIMARY_GOAL": "Test whether frozen last-event clustering is temporally realizable online.",
            "NON_GOALS": [
                "open Confirmation or Frozen Validation",
                "retune R11 / EXIT / CAP",
                "select delayed vs first-event on PnL",
                "Kabu 50",
                "paid data",
                "runtime activation",
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
        "r11f": R11_FREEZE_OUT,
        "disc": DISC_OUT,
        "bg": BG_EXIT_OUT,
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
