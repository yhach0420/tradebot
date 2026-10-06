"""PB1 V4 Correction V2 RCA. Diagnosis only. Runtime 0/0/0."""
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
from research.pb1_v4_clarified_machine_correction_v2.definitions import machine_sha256 as correction_v2_sha256
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.hidden1m import recompute_hidden_1m
from research.pb1_v4_clarified_machine_correction_v2_rca import (
    ANALYSIS_ID,
    ANY_CODE_CHANGED,
    EXPECTED_CORRECTION_V2_SHA256,
    EXPECTED_PARENT_MACHINE_SHA256,
    EXPECTED_SPEC_SHA256,
    PROGRAM_ID,
)
from research.pb1_v4_clarified_machine_correction_v2_rca.analyze import build_report_body
from research.pb1_v4_clarified_machine_correction_v2_rca.bind import bind_prior
from research.pb1_v4_clarified_machine_correction_v2_rca.controls import controls
from research.pb1_v4_clarified_machine_correction_v2_rca.isolation import (
    CACHE,
    CLARIFIED_SPEC_OUT,
    CLARIFIED_SPEC_SRC,
    CORRECTED_OUT,
    CORRECTED_SRC,
    CORRECTION_CACHE,
    CORRECTION_OUT,
    CORRECTION_SRC,
    FOUNDATION_OUT,
    LEAKED_OUT,
    LEAKED_SRC,
    OUT,
    PARENT_MACHINE_OUT,
    PARENT_MACHINE_SRC,
    PARITY_AUDIT_OUT,
    PARITY_AUDIT_SRC,
    READY_SPEC_OUT,
    READY_SPEC_SRC,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_v4_clarified_machine_correction_v2_rca.liveness_rca import liveness_rca
from research.pb1_v4_clarified_machine_correction_v2_rca.one_bar_rca import one_bar_rca
from research.pb1_v4_clarified_machine_correction_v2_rca.publish import SHEET_ORDER, build_answers, build_sheets, finite_sanitize, write_artifacts
from research.pb1_v4_clarified_machine_correction_v2_rca.reconstruct import attach_snaps_and_reclassify, join_88, load_walked
from research.pb1_v4_clarified_machine_correction_v2_rca.spec import source_sha256
from research.pb1_v4_clarified_machine_correction_v2_rca.two_sided_rca import two_sided_rca
from research.pb1_v4_clarified_machine_implementation.definitions import machine_sha256 as parent_machine_sha256
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
        "CORRECTION_V2_MUTATED": False,
        "PARENT_CLARIFIED_MACHINE_MUTATED": False,
        "CORRECTED_MUTATED": False,
        "LEAKED_MUTATED": False,
        "IMPLEMENTATION_RUN": False,
        "RETURN_TEST": False,
        "MFE_MAE": False,
        "PROSPECTIVE_EVENT_CONSUMED": False,
        "GRID_SEARCH": False,
        "V4_1_CREATED": False,
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
            "parent_src": PARENT_MACHINE_SRC,
            "parent_out": PARENT_MACHINE_OUT,
            "correction_src": CORRECTION_SRC,
            "correction_out": CORRECTION_OUT,
            "parity_audit_src": PARITY_AUDIT_SRC,
            "parity_audit_out": PARITY_AUDIT_OUT,
            "foundation": FOUNDATION_OUT,
        }.items()
    }


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print(
        "SAFETY submit/cancel/live=0/0/0 PB1_V4_CLARIFIED_MACHINE_CORRECTION_V2_RCA "
        "FROZEN_VAL CLOSED CONFIRMATION CLOSED",
        flush=True,
    )
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
    if correction_v2_sha256() != EXPECTED_CORRECTION_V2_SHA256:
        raise RuntimeError("CORRECTION_V2_SHA_DRIFT")
    if parent_machine_sha256() != EXPECTED_PARENT_MACHINE_SHA256:
        raise RuntimeError("PARENT_MACHINE_SHA_DRIFT")
    if leaked_machine_sha256() != V4_LEGACY_LEAKED_IMPLEMENTATION:
        raise RuntimeError("LEAKED_V4_SHA_CHANGED")
    if corrected_machine_sha256() != V4_CORRECTED_MACHINE_SHA256:
        raise RuntimeError("CORRECTED_V4_SHA_CHANGED")
    walked = load_walked()
    if not walked.get("ok"):
        raise RuntimeError("WALK_CACHE_MISSING")
    rows = join_88(walked=walked)
    rows = attach_snaps_and_reclassify(rows)
    print(f"RCA_SET n={len(rows)}", flush=True)
    one = one_bar_rca(rows)
    two = two_sided_rca(rows)
    live = liveness_rca(rows)
    ctrl = controls(rows)
    hidden = recompute_hidden_1m(walked=walked)
    print(
        f"ONE_BAR_HEAVY {one.get('one_bar_heavy_n')} SANDWICH {two.get('sandwich_n')} "
        f"E1_AFTER_LOSS {live.get('E1_after_ACTIVE_loss_n')} HIDDEN {hidden.get('mismatch_n')}",
        flush=True,
    )
    fps_after = _fps()
    body = build_report_body(bind, one, two, live, ctrl, hidden, fps, fps_after)
    after = snapshot(phase="POST")
    decision = dict(body.get("decision") or {})
    decision["WRITE_OVERLAP_N"] = overlap
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Diagnose three remaining semantic blockers in Correction V2 vs Clarified V2",
            "BLOCKERS": [
                "ONE_BAR_CONTINUED_INTENT_REPRESENTATION",
                "PULLBACK_VS_TWO_SIDED_AUCTION_REPRESENTATION",
                "ACTIVE_THESIS_LIVENESS_STATE_CONTRACT",
            ],
            "NON_GOALS": ["modify machine", "modify spec", "tune thresholds", "prospective", "PnL", "accuracy"],
        },
        "hashes": {
            "SOURCE_SHA256": source_sha256(),
            "SPEC_SHA256": spec_sha256(),
            "PB1_V4_CLARIFIED_MACHINE_CORRECTION_V2_SHA256": correction_v2_sha256(),
            "PB1_V4_CLARIFIED_MACHINE_SHA256": parent_machine_sha256(),
            "V4_CORRECTED_MACHINE_SHA256": V4_CORRECTED_MACHINE_SHA256,
            "V4_LEGACY_LEAKED_IMPLEMENTATION": V4_LEGACY_LEAKED_IMPLEMENTATION,
        },
        **body,
        "decision": decision,
        "safety": _safety(),
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "DISCOVERY_TIMESTAMP": datetime.now(JST).isoformat(),
        "future_economic_outcome_used": False,
        "prospective_event_consumed": False,
        "any_code_changed": False,
        "rca_set_n": len(rows),
    }
    report["answers"] = build_answers(report)
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "rca_slim.json").write_text(
        json.dumps(finite_sanitize({"n": len(rows), "one_bar_n": one.get("one_bar_heavy_n"), "sandwich_n": two.get("sandwich_n")}), ensure_ascii=False),
        encoding="utf-8",
    )
    assert_no_secret(report, where="report")
    sheets = build_sheets(report)
    assert tuple(sheets.keys()) == SHEET_ORDER
    write_artifacts(report, sheets)
    if fps != fps_after:
        raise RuntimeError(f"PRIOR_MUTATED { {k: fps[k] != fps_after[k] for k in fps} }")
    if spec_sha256() != EXPECTED_SPEC_SHA256:
        raise RuntimeError("CLARIFIED_SPEC_SHA_DRIFT_AFTER")
    if correction_v2_sha256() != EXPECTED_CORRECTION_V2_SHA256:
        raise RuntimeError("CORRECTION_V2_SHA_DRIFT_AFTER")
    if parent_machine_sha256() != EXPECTED_PARENT_MACHINE_SHA256:
        raise RuntimeError("PARENT_MACHINE_SHA_DRIFT_AFTER")
    print(f"OUT {OUT}", flush=True)
    print(f"CORRECTION_V2_SHA {EXPECTED_CORRECTION_V2_SHA256}", flush=True)
    print(f"SPEC_SHA {EXPECTED_SPEC_SHA256}", flush=True)
    print(f"SPEC_CHANGE_REQUIRED {decision.get('SPEC_CHANGE_REQUIRED')}", flush=True)
    print(f"VERDICT {decision.get('VERDICT')}", flush=True)
    print(f"NEXT {decision.get('NEXT')}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
