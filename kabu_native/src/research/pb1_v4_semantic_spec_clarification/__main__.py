"""PB1 V4 semantic spec clarification. Parent READY_V1 preserved. No machine. Runtime 0/0/0."""
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
from research.pb1_v4_implementation_correction.definitions import machine_sha256 as corrected_machine_sha256
from research.pb1_v4_implementation_correction_rca import V4_CORRECTED_MACHINE_SHA256
from research.pb1_v4_semantic_spec_clarification import ANALYSIS_ID, PROGRAM_ID
from research.pb1_v4_semantic_spec_clarification.analyze import build_report_body
from research.pb1_v4_semantic_spec_clarification.bind import bind_prior
from research.pb1_v4_semantic_spec_clarification.isolation import (
    CORRECTED_OUT,
    CORRECTED_SRC,
    FOUNDATION_OUT,
    LEAKED_SRC,
    OUT,
    PARENT_SPEC_OUT,
    PARENT_SPEC_SRC,
    RCA_OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_v4_semantic_spec_clarification.publish import SHEET_ORDER, build_answers, build_sheets, write_artifacts
from research.pb1_v4_semantic_spec_clarification.spec import source_sha256, spec_sha256

JST = ZoneInfo("Asia/Tokyo")


def _fingerprint_py(root: Path) -> str:
    h = hashlib.sha256()
    if not root.is_dir():
        return ""
    for p in sorted(root.glob("*.py")):
        h.update(p.name.encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()


def _fingerprint_out(root: Path) -> str:
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
        "ANY_RULE_CHANGED": False,
        "V4_1_CREATED": False,
        "MACHINE_IMPLEMENTED": False,
        "CORRECTED_MUTATED": False,
        "LEAKED_MUTATED": False,
        "PARENT_SPEC_MUTATED": False,
        "PROSPECTIVE_EVENT_CONSUMED": False,
        "FUTURE_OUTCOME_USED": False,
        "EVENT_N_CALCULATED": False,
        "ECONOMIC_TEST_RUN": False,
        "RETURN_TEST": False,
        "MFE_MAE": False,
    }


def _slim_bind(bind: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bind.get("ok"),
        "reason": bind.get("reason"),
        "parent_spec_verdict": bind.get("parent_spec_verdict"),
        "parent_rca_verdict": bind.get("parent_rca_verdict"),
        "live_corrected_sha": bind.get("live_corrected_sha"),
        "report_corrected_sha": bind.get("report_corrected_sha"),
        "V4_CORRECTED_MACHINE_SHA256": bind.get("V4_CORRECTED_MACHINE_SHA256"),
        "corrected_sha_unchanged": bind.get("corrected_sha_unchanged"),
        "leaked_preserved": bind.get("leaked_preserved"),
        "parent_spec_source_sha256": bind.get("parent_spec_source_sha256"),
        "machine_implemented": False,
        "any_rule_changed": False,
    }


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 PB1_V4_SPEC_CLARIFICATION FROZEN_VAL CLOSED", flush=True)
    print(f"PROGRAM_ID {PROGRAM_ID}", flush=True)
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(str(before.get("ACTIVE_CAPTURE_PATH") or ""), str(before.get("ACTIVE_PAPER_SESSION") or ""))
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    parent_src_before = _fingerprint_py(PARENT_SPEC_SRC)
    parent_out_before = _fingerprint_out(PARENT_SPEC_OUT)
    corr_src_before = _fingerprint_py(CORRECTED_SRC)
    corr_out_before = _fingerprint_out(CORRECTED_OUT)
    leaked_src_before = _fingerprint_py(LEAKED_SRC)
    rca_out_before = _fingerprint_out(RCA_OUT)
    found_before = _fingerprint_out(FOUNDATION_OUT)
    bind = bind_prior()
    if not bind.get("ok"):
        raise RuntimeError(f"BIND_FAILED {bind.get('reason')}")
    if corrected_machine_sha256() != V4_CORRECTED_MACHINE_SHA256:
        raise RuntimeError("CORRECTED_SHA_CHANGED")
    body = build_report_body(bind)
    after = snapshot(phase="POST")
    safety = _safety()
    decision = dict(body.get("decision") or {})
    decision["WRITE_OVERLAP_N"] = overlap
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Clarify frozen V4 semantic spec: thesis vs execution, seed vs active, location vs retest, interpretation B",
            "NON_GOALS": [
                "overwrite PB1_V4_SEMANTIC_SPEC_READY_V1",
                "mutate 21fc72eb",
                "implement a machine",
                "V4.1",
                "threshold optimization",
                "PnL / returns / MFE / MAE",
                "prospective data",
            ],
        },
        "hashes": {
            "SOURCE_SHA256": source_sha256(),
            "SPEC_SHA256": spec_sha256(),
            "V4_CORRECTED_MACHINE_SHA256": V4_CORRECTED_MACHINE_SHA256,
            "V4_LEGACY_LEAKED_IMPLEMENTATION": body.get("V4_LEGACY_LEAKED_IMPLEMENTATION"),
        },
        "bind": _slim_bind(bind),
        **body,
        "decision": decision,
        "safety": safety,
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "DISCOVERY_TIMESTAMP": datetime.now(JST).isoformat(),
        "any_rule_changed": False,
        "future_economic_outcome_used": False,
        "prospective_event_consumed": False,
        "machine_implemented": False,
    }
    report["answers"] = build_answers(report)
    assert_no_secret(report, where="report")
    sheets = build_sheets(report)
    assert tuple(sheets.keys()) == SHEET_ORDER
    write_artifacts(report, sheets)
    if parent_src_before != _fingerprint_py(PARENT_SPEC_SRC):
        raise RuntimeError("PARENT_SPEC_SRC_MUTATED")
    if parent_out_before != _fingerprint_out(PARENT_SPEC_OUT):
        raise RuntimeError("PARENT_SPEC_OUT_MUTATED")
    if corr_src_before != _fingerprint_py(CORRECTED_SRC):
        raise RuntimeError("CORRECTED_SRC_MUTATED")
    if corr_out_before != _fingerprint_out(CORRECTED_OUT):
        raise RuntimeError("CORRECTED_OUT_MUTATED")
    if leaked_src_before != _fingerprint_py(LEAKED_SRC):
        raise RuntimeError("LEAKED_SRC_MUTATED")
    if rca_out_before != _fingerprint_out(RCA_OUT):
        raise RuntimeError("RCA_OUT_MUTATED")
    if found_before != _fingerprint_out(FOUNDATION_OUT):
        raise RuntimeError("FOUNDATION_OUT_MUTATED")
    if corrected_machine_sha256() != V4_CORRECTED_MACHINE_SHA256:
        raise RuntimeError("CORRECTED_SHA_CHANGED_AFTER")
    print(f"OUT {OUT}", flush=True)
    print(f"SPEC_SHA256 {spec_sha256()}", flush=True)
    print(f"CORRECTED_SHA {V4_CORRECTED_MACHINE_SHA256}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
