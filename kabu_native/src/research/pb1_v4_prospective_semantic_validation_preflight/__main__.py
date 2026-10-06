"""Calendar reason erratum + prospective harness preflight. Runtime 0/0/0. No 20260924 open."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_c0_indicator_exit.isolation import advanced
from research.fixed_daytrade_universe_v1.secrets import assert_no_secret
from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256 as v4_machine_sha256
from research.pb1_v4_clarified_machine_correction_v4.spec import source_sha256 as v4_source_sha256
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep import (
    EXPECTED_SPEC_SHA,
    EXPECTED_V4_MACHINE_SHA256,
    EXPECTED_V4_SOURCE_SHA256,
    FROZEN_IDENTITY,
)
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.inventory import file_inventory, source_inventory_sha
from research.pb1_v4_prospective_semantic_validation_preflight import (
    ANALYSIS_ID,
    CALENDAR_CORRECTED_PRECOMMIT_SHA256,
    CASE_BIND,
    CASE_FAIL,
    CASE_READY,
    DO_NOT_START,
    EXPECTED_SOURCE_INVENTORY_SHA256,
    NEXT_START,
    PARENT_CALENDAR_VERDICT,
    PROGRAM_ID,
)
from research.pb1_v4_prospective_semantic_validation_preflight.checks import run_checks
from research.pb1_v4_prospective_semantic_validation_preflight.erratum import apply_reason_erratum, verify_parent_precommit
from research.pb1_v4_prospective_semantic_validation_preflight.isolation import (
    CACHE,
    CAL_OUT,
    CAL_SRC,
    CLARIFIED_SPEC_SRC,
    FREEZE_OUT,
    FREEZE_SRC,
    OUT,
    V4_SRC,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_v4_prospective_semantic_validation_preflight.publish import SHEET_ORDER, build_answers, build_sheets, write_artifacts
from research.pb1_v4_prospective_semantic_validation_preflight.spec import source_sha256
from research.pb1_v4_semantic_spec_clarification.spec import spec_sha256

JST = ZoneInfo("Asia/Tokyo")
HARNESS_SRC = Path(__file__).resolve().parent


def _fingerprint(root: Path, *, py_only: bool = False) -> str:
    h = hashlib.sha256()
    if not root.exists():
        return ""
    if py_only:
        files = sorted(p for p in root.glob("*.py") if p.is_file())
    else:
        files = sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    for p in files:
        rel = p.relative_to(root)
        h.update(str(rel).encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 PB1_V4_PROSPECTIVE_PREFLIGHT PROSPECTIVE CLOSED CONFIRMATION CLOSED FROZEN_VAL CLOSED", flush=True)
    print(f"PROGRAM_ID {PROGRAM_ID}", flush=True)
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    fps = {
        "v4_src": _fingerprint(V4_SRC, py_only=True),
        "freeze_src": _fingerprint(FREEZE_SRC, py_only=True),
        "cal_src": _fingerprint(CAL_SRC, py_only=True),
        "clarified_src": _fingerprint(CLARIFIED_SPEC_SRC, py_only=True),
        "freeze_out": _fingerprint(FREEZE_OUT),
        "cal_out": _fingerprint(CAL_OUT),
    }
    if spec_sha256() != EXPECTED_SPEC_SHA:
        raise RuntimeError("SPEC_SHA_CHANGED")
    if v4_machine_sha256() != EXPECTED_V4_MACHINE_SHA256:
        raise RuntimeError("V4_MACHINE_SHA_CHANGED")
    if v4_source_sha256() != EXPECTED_V4_SOURCE_SHA256:
        raise RuntimeError("V4_SOURCE_SHA_CHANGED")
    parent_path = CAL_OUT / "report.json"
    if not parent_path.is_file():
        raise RuntimeError("PARENT_CALENDAR_REPORT_MISSING")
    parent_rep = json.loads(parent_path.read_text(encoding="utf-8"))
    parent_verdict = str((parent_rep.get("decision") or {}).get("VERDICT") or "")
    parent_pre = dict(parent_rep.get("corrected_precommit") or {})
    parent_verify = verify_parent_precommit(parent_pre)
    if not parent_verify.get("ok") or parent_verdict != PARENT_CALENDAR_VERDICT:
        corrected = {}
        checks = {"ok": False, "rows": [], "fail_n": 16}
        decision = {"VERDICT": CASE_BIND, "NEXT": DO_NOT_START, "reasons": ["parent_calendar_precommit_mismatch"]}
    else:
        corrected = apply_reason_erratum(parent_pre)
        checks = run_checks(corrected_precommit=corrected, parent_precommit=parent_pre, harness_src=HARNESS_SRC)
        ok = bool(checks.get("ok"))
        decision = {
            "VERDICT": CASE_READY if ok else CASE_FAIL,
            "NEXT": NEXT_START if ok else DO_NOT_START,
            "reasons": [] if ok else ["preflight_check_failed"],
        }
    fps_after = {
        "v4_src": _fingerprint(V4_SRC, py_only=True),
        "freeze_src": _fingerprint(FREEZE_SRC, py_only=True),
        "cal_src": _fingerprint(CAL_SRC, py_only=True),
        "clarified_src": _fingerprint(CLARIFIED_SPEC_SRC, py_only=True),
        "freeze_out": _fingerprint(FREEZE_OUT),
        "cal_out": _fingerprint(CAL_OUT),
    }
    if fps != fps_after:
        raise RuntimeError(f"PRIOR_MUTATED { {k: fps[k] != fps_after[k] for k in fps} }")
    if v4_machine_sha256() != EXPECTED_V4_MACHINE_SHA256:
        raise RuntimeError("V4_MACHINE_SHA_CHANGED_AFTER")
    after = snapshot(phase="POST")
    inv_sha = source_inventory_sha(file_inventory())
    safety = {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "SPEC_CHANGED": False,
        "V4_CHANGED": False,
        "THRESHOLD_RETUNED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "OLD_CONFIRMATION_OPENED": False,
        "FROZEN_VALIDATION_OPENED": False,
        "FUTURE_OUTCOME_USED": False,
        "PNL_USED": False,
        "MFE_MAE_USED": False,
        "submit/cancel/live": "0/0/0",
        "20260924_BARS_LOADED": False,
    }
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "FROZEN_IDENTITY": FROZEN_IDENTITY,
        "hashes": {
            "PREFLIGHT_SOURCE_SHA256": source_sha256(),
            "SPEC_SHA256": spec_sha256(),
            "PB1_V4_CLARIFIED_MACHINE_CORRECTION_V4_SHA256": v4_machine_sha256(),
            "PB1_V4_SOURCE_SHA256": v4_source_sha256(),
            "SOURCE_INVENTORY_SHA256": inv_sha,
            "EXPECTED_SOURCE_INVENTORY_SHA256": EXPECTED_SOURCE_INVENTORY_SHA256,
            "CALENDAR_CORRECTED_PRECOMMIT_SHA256": CALENDAR_CORRECTED_PRECOMMIT_SHA256,
            "NEW_PRECOMMIT_SHA256": corrected.get("PRECOMMIT_SHA256"),
        },
        "parent_verify": parent_verify,
        "corrected_precommit": corrected,
        "checks": checks,
        "decision": decision,
        "safety": safety,
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "DISCOVERY_TIMESTAMP": datetime.now(JST).isoformat(),
        "spec_changed": False,
        "v4_changed": False,
        "prospective_event_consumed": False,
        "future_economic_outcome_used": False,
        "contamination_ledger_changed": False,
        "eligibility_rule_changed": False,
        "stopping_rule_changed": False,
    }
    report["answers"] = build_answers(report)
    CACHE.mkdir(parents=True, exist_ok=True)
    assert_no_secret(report, where="report")
    sheets = build_sheets(report)
    if tuple(sheets.keys()) != SHEET_ORDER:
        raise RuntimeError("SHEET_ORDER_MISMATCH")
    write_artifacts(report, sheets)
    print(f"OUT {OUT}", flush=True)
    print(f"VERDICT {decision.get('VERDICT')}", flush=True)
    print(f"NEXT {decision.get('NEXT')}", flush=True)
    ans = dict(report.get("answers") or {})
    for k in (
        "SPEC_CHANGED",
        "V4_CHANGED",
        "THRESHOLD_RETUNED",
        "FIRST_ELIGIBLE_JP_CASH_SESSION",
        "20260921_COUNTS_AS_SESSION",
        "20260922_COUNTS_AS_SESSION",
        "20260923_COUNTS_AS_SESSION",
        "ELIGIBILITY_UNCHANGED",
        "OLD_PRECOMMIT_SHA256",
        "PARENT_PRECOMMIT_SHA256",
        "NEW_PRECOMMIT_SHA256",
        "PREFLIGHT_PASS_N",
        "PREFLIGHT_FAIL_N",
        "START_DAY_GATE",
        "PROSPECTIVE_DATA_OPENED",
        "OLD_CONFIRMATION_OPENED",
        "FROZEN_VALIDATION_OPENED",
        "submit/cancel/live",
    ):
        print(f"{k} = {ans.get(k)}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
