"""PB1 V4 clarified-machine spec-parity audit. Diagnosis only. Runtime 0/0/0."""
from __future__ import annotations

import hashlib
import json
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
from research.pb1_v4_clarified_machine_implementation.definitions import machine_sha256 as clarified_machine_sha256
from research.pb1_v4_clarified_machine_spec_parity_audit import (
    ANALYSIS_ID,
    ANY_CODE_CHANGED,
    EXPECTED_MACHINE_SHA256,
    EXPECTED_SPEC_SHA256,
    PROGRAM_ID,
)
from research.pb1_v4_clarified_machine_spec_parity_audit.active_audit import active_audit
from research.pb1_v4_clarified_machine_spec_parity_audit.analyze import build_report_body
from research.pb1_v4_clarified_machine_spec_parity_audit.bind import bind_prior
from research.pb1_v4_clarified_machine_spec_parity_audit.constants_audit import inventory
from research.pb1_v4_clarified_machine_spec_parity_audit.hidden1m import recompute_hidden_1m
from research.pb1_v4_clarified_machine_spec_parity_audit.isolation import (
    CACHE,
    CLARIFIED_SPEC_OUT,
    CLARIFIED_SPEC_SRC,
    CORRECTED_OUT,
    CORRECTED_SRC,
    FOUNDATION_OUT,
    LEAKED_OUT,
    LEAKED_SRC,
    MACHINE_CACHE,
    MACHINE_OUT,
    MACHINE_SRC,
    OUT,
    READY_SPEC_OUT,
    READY_SPEC_SRC,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_v4_clarified_machine_spec_parity_audit.location_audit import binding_audit, execution_audit, failed_open_audit, location_audit
from research.pb1_v4_clarified_machine_spec_parity_audit.mismatch import classify_all
from research.pb1_v4_clarified_machine_spec_parity_audit.publish import SHEET_ORDER, build_answers, build_sheets, finite_sanitize, write_artifacts
from research.pb1_v4_clarified_machine_spec_parity_audit.reconstruct import join_88, load_walked, reconstruct_paths
from research.pb1_v4_clarified_machine_spec_parity_audit.seed_audit import flat_crawl_case, seed_audit, two_sided_internal
from research.pb1_v4_clarified_machine_spec_parity_audit.spec import source_sha256
from research.pb1_v4_implementation_correction import V4_LEGACY_LEAKED_IMPLEMENTATION
from research.pb1_v4_implementation_correction.definitions import machine_sha256 as corrected_machine_sha256
from research.pb1_v4_implementation_correction_rca import V4_CORRECTED_MACHINE_SHA256
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256
from research.pb1_v4_semantic_spec_clarification.spec import spec_sha256

JST = ZoneInfo("Asia/Tokyo")


def _fingerprint(root: Path, *, py_only: bool = False) -> str:
    h = hashlib.sha256()
    if not root.is_dir():
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
        "ANY_CODE_CHANGED": ANY_CODE_CHANGED,
        "READY_SPEC_MUTATED": False,
        "CLARIFIED_SPEC_MUTATED": False,
        "CORRECTED_MUTATED": False,
        "LEAKED_MUTATED": False,
        "CLARIFIED_MACHINE_MUTATED": False,
        "INDEPENDENT_FACE_REVIEW_RUN": False,
        "ECONOMIC_TEST_RUN": False,
        "RETURN_TEST": False,
        "MFE_MAE": False,
        "PROSPECTIVE_EVENT_CONSUMED": False,
        "PROSPECTIVE_FACE_DATA_CONSUMED": False,
        "GRID_SEARCH": False,
        "ML_CLASSIFIER": False,
    }


def _fps() -> dict[str, str]:
    return {
        k: _fingerprint(p, py_only=bool(k.endswith("_src")))
        for k, p in {
            "ready_src": READY_SPEC_SRC,
            "ready_out": READY_SPEC_OUT,
            "clarified_src": CLARIFIED_SPEC_SRC,
            "clarified_out": CLARIFIED_SPEC_OUT,
            "corrected_src": CORRECTED_SRC,
            "corrected_out": CORRECTED_OUT,
            "leaked_src": LEAKED_SRC,
            "leaked_out": LEAKED_OUT,
            "machine_src": MACHINE_SRC,
            "machine_out": MACHINE_OUT,
            "foundation": FOUNDATION_OUT,
        }.items()
    }


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 PB1_V4_CLARIFIED_MACHINE_SPEC_PARITY_AUDIT FROZEN_VAL CLOSED CONFIRMATION CLOSED", flush=True)
    print(f"PROGRAM_ID {PROGRAM_ID}", flush=True)
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    fps = _fps()
    bind = bind_prior()
    if not bind.get("ok"):
        raise RuntimeError(f"BIND_FAILED {bind.get('reason')}")
    if spec_sha256() != EXPECTED_SPEC_SHA256:
        raise RuntimeError("CLARIFIED_SPEC_SHA_DRIFT")
    if clarified_machine_sha256() != EXPECTED_MACHINE_SHA256:
        raise RuntimeError("CLARIFIED_MACHINE_SHA_DRIFT")
    if leaked_machine_sha256() != V4_LEGACY_LEAKED_IMPLEMENTATION:
        raise RuntimeError("LEAKED_V4_SHA_CHANGED")
    if corrected_machine_sha256() != V4_CORRECTED_MACHINE_SHA256:
        raise RuntimeError("CORRECTED_V4_SHA_CHANGED")
    walked = load_walked()
    if not walked.get("ok"):
        raise RuntimeError("WALK_CACHE_MISSING")
    print(
        f"LOADED_WALK setups={len(walked.get('setups') or [])} e0={len(walked.get('e0_events') or [])} "
        f"e1={len(walked.get('e1_events') or [])} funnel={len(walked.get('funnel_days') or [])}",
        flush=True,
    )
    rows = join_88(walked=walked)
    print(f"JOIN88 n={len(rows)}", flush=True)
    recon = reconstruct_paths(bind=bind, rows=rows)
    if not recon.get("ok"):
        raise RuntimeError(f"RECONSTRUCT_FAILED {recon.get('reason')}")
    rows = list(recon.get("rows") or [])
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "reconstructed_88.json").write_text(
        json.dumps(finite_sanitize({"n": recon.get("n"), "rows": rows}), ensure_ascii=False),
        encoding="utf-8",
    )
    seed = seed_audit(rows)
    active = active_audit(rows)
    loc = location_audit(rows)
    fo = failed_open_audit(rows)
    exe = execution_audit(walked=walked, rows=rows)
    binding = binding_audit(walked=walked, rows=rows)
    hidden = recompute_hidden_1m(walked=walked)
    constants = inventory()
    mismatches = classify_all(rows, loc_audit=loc)
    case_8031 = two_sided_internal(rows, symbol="8031", date="20250225")
    case_7741 = two_sided_internal(rows, symbol="7741", date="20250314")
    case_8058 = flat_crawl_case(rows, symbol="8058", date="20250812")
    case_8630 = flat_crawl_case(rows, symbol="8630", date="20250911")
    print(
        f"HIDDEN_MISMATCH {hidden.get('mismatch_n')} LATE_ACTIVE {active.get('human_LATE_still_ACTIVE_at_location_n')} "
        f"STALE_RESET {active.get('ACTIVE_STALENESS_RESET_TOO_PERMISSIVE')} MATERIAL {mismatches.get('material_violation_n')}",
        flush=True,
    )
    fps_after = _fps()
    body = build_report_body(
        bind,
        walked,
        seed,
        active,
        loc,
        fo,
        exe,
        binding,
        hidden,
        constants,
        mismatches,
        case_8031,
        case_7741,
        case_8058,
        case_8630,
        fps,
        fps_after,
        extra={"rca_set_n": len(rows), "reconstruct_ok": recon.get("ok"), "future_outcome_n": 0},
    )
    after = snapshot(phase="POST")
    safety = _safety()
    decision = dict(body.get("decision") or {})
    decision["WRITE_OVERLAP_N"] = overlap
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Audit whether machine 33b1bf faithfully implements CLARIFIED_V2",
            "NON_GOALS": [
                "change code",
                "retune thresholds",
                "consume prospective data",
                "returns / PnL / MFE / MAE",
                "accuracy gate",
            ],
        },
        "hashes": {
            "SOURCE_SHA256": source_sha256(),
            "SPEC_SHA256": spec_sha256(),
            "PB1_V4_CLARIFIED_MACHINE_SHA256": clarified_machine_sha256(),
            "V4_CORRECTED_MACHINE_SHA256": V4_CORRECTED_MACHINE_SHA256,
            "V4_LEGACY_LEAKED_IMPLEMENTATION": V4_LEGACY_LEAKED_IMPLEMENTATION,
            "DETECTOR_SHA256": bind.get("DETECTOR_SHA256"),
            "STATE_MACHINE_SHA256": bind.get("STATE_MACHINE_SHA256"),
        },
        **body,
        "decision": decision,
        "safety": safety,
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "DISCOVERY_TIMESTAMP": datetime.now(JST).isoformat(),
        "future_economic_outcome_used": False,
        "prospective_event_consumed": False,
        "any_code_changed": False,
    }
    report["answers"] = build_answers(report)
    CACHE.mkdir(parents=True, exist_ok=True)
    assert_no_secret(report, where="report")
    sheets = build_sheets(report)
    assert tuple(sheets.keys()) == SHEET_ORDER
    write_artifacts(report, sheets)
    if fps != fps_after:
        raise RuntimeError(f"PRIOR_MUTATED { {k: fps[k] != fps_after[k] for k in fps} }")
    if leaked_machine_sha256() != V4_LEGACY_LEAKED_IMPLEMENTATION:
        raise RuntimeError("LEAKED_V4_SHA_CHANGED_AFTER")
    if corrected_machine_sha256() != V4_CORRECTED_MACHINE_SHA256:
        raise RuntimeError("CORRECTED_V4_SHA_CHANGED_AFTER")
    if spec_sha256() != EXPECTED_SPEC_SHA256:
        raise RuntimeError("CLARIFIED_SPEC_SHA_DRIFT_AFTER")
    if clarified_machine_sha256() != EXPECTED_MACHINE_SHA256:
        raise RuntimeError("CLARIFIED_MACHINE_SHA_DRIFT_AFTER")
    print(f"OUT {OUT}", flush=True)
    print(f"MACHINE_SHA {EXPECTED_MACHINE_SHA256}", flush=True)
    print(f"SPEC_SHA {EXPECTED_SPEC_SHA256}", flush=True)
    print(f"HIDDEN_MISMATCH_N {hidden.get('mismatch_n')}", flush=True)
    print(f"1M_CREATED_LOCATION_N {hidden.get('1m_created_location_n')}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
