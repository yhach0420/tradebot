"""PB1 V4 semantic spec. Frozen V3.2. No V4 machine. Runtime 0/0/0."""
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
from research.pb1_v4_opening_drive_location_reaccel_spec import ANALYSIS_ID, PROGRAM_ID
from research.pb1_v4_opening_drive_location_reaccel_spec.analyze import build_report_body
from research.pb1_v4_opening_drive_location_reaccel_spec.bind import bind_prior
from research.pb1_v4_opening_drive_location_reaccel_spec.isolation import (
    FOUNDATION_OUT,
    OUT,
    RCA_OUT,
    V1_OUT,
    V2_OUT,
    V3_OUT,
    V31_OUT,
    V32_CACHE,
    V32_OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_v4_opening_drive_location_reaccel_spec.publish import SHEET_ORDER, build_answers, build_sheets, write_artifacts
from research.pb1_v4_opening_drive_location_reaccel_spec.spec import source_sha256

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
        "KABU_50_APPLIED": False,
        "FROZEN_VALIDATION_OPENED": False,
        "OLD_CONFIRMATION_OPENED": False,
        "PNL_OPTIMIZATION": False,
        "THRESHOLD_OPTIMIZED": False,
        "V32_RULE_CHANGED": False,
        "V4_MACHINE_IMPLEMENTED": False,
        "EVENT_N_CALCULATED": False,
        "FUTURE_OUTCOME_USED": False,
    }


def _slim_bind(bind: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bind.get("ok"),
        "reason": bind.get("reason"),
        "parent_rca_verdict": bind.get("parent_rca_verdict"),
        "parent_rca_n": bind.get("parent_rca_n"),
        "parent_clear_n": bind.get("parent_clear_n"),
        "parent_v32_sha": bind.get("parent_v32_sha"),
        "live_v32_machine_sha256": bind.get("live_v32_machine_sha256"),
        "rca": bind.get("rca"),
        "v4_machine_not_run": True,
    }


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 PB1_V4_SPEC FROZEN_VAL CLOSED CONFIRMATION CLOSED", flush=True)
    print(f"PROGRAM_ID {PROGRAM_ID}", flush=True)
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
            "v32": V32_OUT,
            "v32c": V32_CACHE,
            "v31": V31_OUT,
            "v3": V3_OUT,
            "v2": V2_OUT,
            "v1": V1_OUT,
            "foundation": FOUNDATION_OUT,
        }.items()
    }
    bind = bind_prior()
    if not bind.get("ok"):
        raise RuntimeError(f"BIND_FAILED {bind.get('reason')}")
    body = build_report_body(bind)
    after = snapshot(phase="POST")
    safety = _safety()
    decision = dict(body.get("decision") or {})
    decision["WRITE_OVERLAP_N"] = overlap
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Semantic V4 specification: 5m setup, 1m execution confirmation only",
            "NON_GOALS": [
                "implement V4 machine",
                "event_n / Discovery walk",
                "PnL / returns / MFE / MAE",
                "freeze 1m numeric thresholds from RCA medians",
                "mutate V3.2",
                "retune IN-PLAY constants",
                "daily-bias gate",
            ],
        },
        "hashes": {
            "SOURCE_SHA256": source_sha256(),
            "PLAYBOOK_MACHINE_SHA256": body.get("MACHINE_SHA256"),
            "PARENT_V32_SHA": body.get("PARENT_V32_SHA"),
        },
        "bind": _slim_bind(bind),
        **body,
        "decision": decision,
        "safety": safety,
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "DISCOVERY_TIMESTAMP": datetime.now(JST).isoformat(),
    }
    report["answers"] = build_answers(report)
    assert_no_secret(report, where="report")
    sheets = build_sheets(report)
    assert tuple(sheets.keys()) == SHEET_ORDER
    write_artifacts(report, sheets)
    for k, p in {
        "rca": RCA_OUT,
        "v32": V32_OUT,
        "v32c": V32_CACHE,
        "v31": V31_OUT,
        "v3": V3_OUT,
        "v2": V2_OUT,
        "v1": V1_OUT,
        "foundation": FOUNDATION_OUT,
    }.items():
        if fps[k] != _fingerprint(p):
            raise RuntimeError(f"PRIOR_MUTATED_{k}")
    if not report.get("v32_unchanged"):
        raise RuntimeError("V32_SHA_DRIFT")
    if report.get("v4_machine_implemented"):
        raise RuntimeError("V4_MACHINE_MUST_NOT_RUN")
    print(f"OUT {OUT}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
