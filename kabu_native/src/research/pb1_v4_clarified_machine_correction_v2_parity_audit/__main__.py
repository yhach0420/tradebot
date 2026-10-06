"""PB1 V4 Correction V2 parity audit. Diagnosis only. Runtime 0/0/0."""
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
from research.pb1_v4_clarified_machine_correction_v2_parity_audit import (
    ANALYSIS_ID,
    ANY_CODE_CHANGED,
    EXPECTED_CORRECTION_V2_SHA256,
    EXPECTED_PARENT_MACHINE_SHA256,
    EXPECTED_SPEC_SHA256,
    PROGRAM_ID,
)
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.active_audit import active_audit
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.analyze import build_report_body
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.bind import bind_prior
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.changed_audit import classify_changed
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.failed_open_audit import failed_open_audit
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.family_a_audit import family_a_audit
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.form_b_audit import form_b_audit
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.hidden1m import recompute_hidden_1m
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.isolation import (
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
    READY_SPEC_OUT,
    READY_SPEC_SRC,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.one_bar_audit import one_bar_audit
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.publish import SHEET_ORDER, build_answers, finite_sanitize, write_artifacts
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.reconstruct import (
    attach_snaps_and_reclassify,
    join_88,
    load_changed_manifest,
    load_walked,
)
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.spec import source_sha256
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.two_sided_audit import two_sided_audit
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
        "CORRECTED_MUTATED": False,
        "LEAKED_MUTATED": False,
        "PARENT_CLARIFIED_MACHINE_MUTATED": False,
        "CORRECTION_V2_MUTATED": False,
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
            "parent_src": PARENT_MACHINE_SRC,
            "parent_out": PARENT_MACHINE_OUT,
            "correction_src": CORRECTION_SRC,
            "correction_out": CORRECTION_OUT,
            "correction_cache": CORRECTION_CACHE,
            "foundation": FOUNDATION_OUT,
        }.items()
    }


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print(
        "SAFETY submit/cancel/live=0/0/0 PB1_V4_CLARIFIED_MACHINE_CORRECTION_V2_PARITY_AUDIT "
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
    print(
        f"LOADED_WALK setups={len(walked.get('setups') or [])} e0={len(walked.get('e0_events') or [])} "
        f"e1={len(walked.get('e1_events') or [])} funnel={len(walked.get('funnel_days') or [])}",
        flush=True,
    )
    rows = join_88(walked=walked)
    print(f"JOIN88 n={len(rows)}", flush=True)
    rows = attach_snaps_and_reclassify(rows)
    changed_raw = load_changed_manifest()
    CACHE.mkdir(parents=True, exist_ok=True)
    slim_rows = []
    for r in rows:
        snap = dict(r.get("snap") or {})
        slim_rows.append(
            {
                **{k: v for k, v in r.items() if k != "snap"},
                "snap": {
                    "atr20": snap.get("atr20"),
                    "open": snap.get("open"),
                    "or_high": snap.get("or_high"),
                    "or_low": snap.get("or_low"),
                    "pdh": snap.get("pdh"),
                    "pdl": snap.get("pdl"),
                    "pdc": snap.get("pdc"),
                    "vwap_open": snap.get("vwap_open"),
                    "clock_snap": snap.get("clock_snap"),
                    "bars": list(snap.get("bars") or [])[:12],
                    "zones": list(snap.get("zones") or []),
                    "seed_row_v2": snap.get("seed_row_v2"),
                    "failed_open_v2": snap.get("failed_open_v2"),
                    "intent_v2": snap.get("intent_v2"),
                    "location_at_0914_v2": snap.get("location_at_0914_v2"),
                },
            }
        )
    (CACHE / "reconstructed_88.json").write_text(
        json.dumps(finite_sanitize({"n": len(slim_rows), "rows": slim_rows}), ensure_ascii=False),
        encoding="utf-8",
    )
    changed = classify_changed(changed=changed_raw, rows=rows)
    one_bar = one_bar_audit(rows)
    two = two_sided_audit(rows)
    active = active_audit(rows)
    fo = failed_open_audit(rows)
    fam = family_a_audit(rows)
    formb = form_b_audit(rows)
    hidden = recompute_hidden_1m(walked=walked)
    print(
        f"CHANGED {changed.get('n')} HIDDEN_MISMATCH {hidden.get('mismatch_n')} "
        f"ONE_BAR {one_bar.get('ONE_BAR_DOMINATED_WITHOUT_FOLLOWTHROUGH_still_exists')} "
        f"CLEAR_OVER {two.get('clear_over_rejection_n')} GE3 {active.get('ACTIVE_rows_with_ge3_consecutive_nonreset')}",
        flush=True,
    )
    fps_after = _fps()
    body = build_report_body(
        bind,
        walked,
        rows,
        changed,
        one_bar,
        two,
        active,
        fo,
        fam,
        formb,
        hidden,
        fps,
        fps_after,
    )
    after = snapshot(phase="POST")
    safety = _safety()
    decision = dict(body.get("decision") or {})
    decision["WRITE_OVERLAP_N"] = overlap
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Audit whether Correction V2 916ec521 faithfully encodes CLARIFIED_V2",
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
            "PB1_V4_CLARIFIED_MACHINE_CORRECTION_V2_SHA256": correction_v2_sha256(),
            "PB1_V4_CLARIFIED_MACHINE_SHA256": parent_machine_sha256(),
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
    report["answers"] = build_answers(report, rows=rows)
    CACHE.mkdir(parents=True, exist_ok=True)
    assert_no_secret(report, where="report")
    sheets = build_sheets_safe(report)
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
    if correction_v2_sha256() != EXPECTED_CORRECTION_V2_SHA256:
        raise RuntimeError("CORRECTION_V2_SHA_DRIFT_AFTER")
    if parent_machine_sha256() != EXPECTED_PARENT_MACHINE_SHA256:
        raise RuntimeError("PARENT_MACHINE_SHA_DRIFT_AFTER")
    print(f"OUT {OUT}", flush=True)
    print(f"CORRECTION_V2_SHA {EXPECTED_CORRECTION_V2_SHA256}", flush=True)
    print(f"SPEC_SHA {EXPECTED_SPEC_SHA256}", flush=True)
    print(f"HIDDEN_MISMATCH_N {hidden.get('mismatch_n')}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    return 0


def build_sheets_safe(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    from research.pb1_v4_clarified_machine_correction_v2_parity_audit.publish import build_sheets

    return build_sheets(report)


if __name__ == "__main__":
    raise SystemExit(main())
