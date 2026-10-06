"""PB1 V4 clarified machine. New SHA. Parents frozen. Runtime 0/0/0."""
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
from research.pb1_v4_clarified_machine_implementation import ANALYSIS_ID, EXPECTED_SPEC_SHA256, PROGRAM_ID
from research.pb1_v4_clarified_machine_implementation.analyze import build_report_body
from research.pb1_v4_clarified_machine_implementation.audit import audit_88
from research.pb1_v4_clarified_machine_implementation.bind import bind_prior
from research.pb1_v4_clarified_machine_implementation.calibrate import calibrate_from_descriptors
from research.pb1_v4_clarified_machine_implementation.definitions import machine_sha256
from research.pb1_v4_clarified_machine_implementation.invariants import count_invariants
from research.pb1_v4_clarified_machine_implementation.isolation import (
    CACHE,
    CLARIFIED_SPEC_OUT,
    CLARIFIED_SPEC_SRC,
    CORRECTED_OUT,
    CORRECTED_SRC,
    FOUNDATION_OUT,
    LEAKED_OUT,
    LEAKED_SRC,
    OUT,
    READY_SPEC_OUT,
    READY_SPEC_SRC,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_v4_clarified_machine_implementation.leakage import scan_package
from research.pb1_v4_clarified_machine_implementation.publish import SHEET_ORDER, build_answers, build_sheets, write_artifacts
from research.pb1_v4_clarified_machine_implementation.spec import source_sha256
from research.pb1_v4_clarified_machine_implementation.walk import emit_v4
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
        "CLOCK_0930_CUTOFF": False,
        "V4_1_CREATED": False,
        "READY_SPEC_MUTATED": False,
        "CLARIFIED_SPEC_MUTATED": False,
        "CORRECTED_MUTATED": False,
        "LEAKED_MUTATED": False,
        "INDEPENDENT_FACE_REVIEW_RUN": False,
        "ECONOMIC_TEST_RUN": False,
        "RETURN_TEST": False,
        "MFE_MAE": False,
        "ECONOMIC_E0_E1_COMPARISON": False,
        "PROSPECTIVE_EVENT_CONSUMED": False,
        "GRID_SEARCH": False,
        "ML_CLASSIFIER": False,
    }


def _slim_bind(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    return {
        "ok": bind.get("ok"),
        "reason": bind.get("reason"),
        "research_pool_n": bind.get("research_pool_n"),
        "parent_ready_verdict": bind.get("parent_ready_verdict"),
        "parent_clarified_verdict": bind.get("parent_clarified_verdict"),
        "live_spec_sha256": bind.get("live_spec_sha256"),
        "EXPECTED_SPEC_SHA256": bind.get("EXPECTED_SPEC_SHA256"),
        "live_corrected_sha": bind.get("live_corrected_sha"),
        "V4_CORRECTED_MACHINE_SHA256": bind.get("V4_CORRECTED_MACHINE_SHA256"),
        "live_leaked_sha": bind.get("live_leaked_sha"),
        "V4_LEGACY_LEAKED_IMPLEMENTATION": bind.get("V4_LEGACY_LEAKED_IMPLEMENTATION"),
        "corrected_preserved": bind.get("corrected_preserved"),
        "leaked_preserved": bind.get("leaked_preserved"),
        "split": {
            "split_sha256": split.get("split_sha256"),
            "discovery_n": len(list(split.get("discovery_dates") or [])),
            "discovery_first": (list(split.get("discovery_dates") or []) or [None])[0],
            "discovery_last": (list(split.get("discovery_dates") or []) or [None])[-1],
        },
        "blocks": {"ok": blocks.get("ok"), "block_sha256": blocks.get("block_sha256")},
        "symbol_n": len(list(bind.get("symbols") or [])),
        "DETECTOR_SHA256": bind.get("DETECTOR_SHA256"),
        "STATE_MACHINE_SHA256": bind.get("STATE_MACHINE_SHA256"),
        "contaminated_symbol_dates_n": bind.get("contaminated_symbol_dates_n"),
    }


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 PB1_V4_CLARIFIED_MACHINE FROZEN_VAL CLOSED CONFIRMATION CLOSED", flush=True)
    print(f"PROGRAM_ID {PROGRAM_ID}", flush=True)
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    fps = {
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
            "foundation": FOUNDATION_OUT,
        }.items()
    }
    bind = bind_prior()
    if not bind.get("ok"):
        raise RuntimeError(f"BIND_FAILED {bind.get('reason')}")
    if spec_sha256() != EXPECTED_SPEC_SHA256:
        raise RuntimeError("CLARIFIED_SPEC_SHA_DRIFT")
    if leaked_machine_sha256() != V4_LEGACY_LEAKED_IMPLEMENTATION:
        raise RuntimeError("LEAKED_V4_SHA_CHANGED")
    if corrected_machine_sha256() != V4_CORRECTED_MACHINE_SHA256:
        raise RuntimeError("CORRECTED_V4_SHA_CHANGED")
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_walk = CACHE / "walked.json"
    relabel = os.environ.get("PB1_V4_CLARIFIED_RELABEL") == "1"
    if relabel and cache_walk.is_file():
        walked = json.loads(cache_walk.read_text(encoding="utf-8"))
        print("LOADED_WALK_CACHE", flush=True)
    else:
        walked = emit_v4(bind)
        cache_walk.write_text(json.dumps({k: v for k, v in walked.items()}, default=str, ensure_ascii=False), encoding="utf-8")
    if not walked.get("ok"):
        raise RuntimeError(f"WALK_FAILED {walked.get('reason')}")
    print(
        f"THESIS_N {len(walked.get('setups') or [])} E0 {len(walked.get('e0_events') or [])} "
        f"E1 {len(walked.get('e1_events') or [])} SAME_BAR {walked.get('same_bar_entry_n')} "
        f"HIDDEN_1M {walked.get('HIDDEN_1M_THESIS_PARITY')}",
        flush=True,
    )
    calib = calibrate_from_descriptors()
    audit = audit_88(
        funnel=list(walked.get("funnel_days") or []),
        setups=list(walked.get("setups") or []),
        e0_events=list(walked.get("e0_events") or []),
        e1_events=list(walked.get("e1_events") or []),
        bind=bind,
    )
    inv = count_invariants(funnel=list(walked.get("funnel_days") or []), walked=walked)
    leak = scan_package()
    print(f"AUDIT88 {audit.get('n')} INVARIANT_VIOLATIONS {inv.get('violations')} CLOCK_HITS {leak.get('unsupported_clock_semantic_count')}", flush=True)
    fps_after = {
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
            "foundation": FOUNDATION_OUT,
        }.items()
    }
    body = build_report_body(bind, walked, calib, audit, inv, leak, fps, fps_after)
    after = snapshot(phase="POST")
    safety = _safety()
    decision = dict(body.get("decision") or {})
    decision["WRITE_OVERLAP_N"] = overlap
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Implement PB1_V4_SEMANTIC_SPEC_CLARIFIED_V2 as a new machine",
            "NON_GOALS": [
                "overwrite READY_V1 or CLARIFIED_V2",
                "mutate 21fc72eb or fa0451bb",
                "threshold/PnL optimization",
                "face validation",
                "prospective unseen events",
                "Confirmation or Frozen Validation",
            ],
        },
        "hashes": {
            "SOURCE_SHA256": source_sha256(),
            "SPEC_SHA256": spec_sha256(),
            "PB1_V4_CLARIFIED_MACHINE_SHA256": machine_sha256(),
            "V4_CORRECTED_MACHINE_SHA256": V4_CORRECTED_MACHINE_SHA256,
            "V4_LEGACY_LEAKED_IMPLEMENTATION": V4_LEGACY_LEAKED_IMPLEMENTATION,
            "DETECTOR_SHA256": bind.get("DETECTOR_SHA256"),
            "STATE_MACHINE_SHA256": bind.get("STATE_MACHINE_SHA256"),
        },
        "bind": _slim_bind(bind),
        **body,
        "decision": decision,
        "safety": safety,
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "DISCOVERY_TIMESTAMP": datetime.now(JST).isoformat(),
        "v4_machine_implemented": True,
        "future_economic_outcome_used": False,
        "spec_changed": False,
        "prospective_event_consumed": False,
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
    if int(report.get("same_bar_entry_n") or 0) != 0:
        raise RuntimeError("SAME_BAR_ENTRY")
    print(f"OUT {OUT}", flush=True)
    print(f"SPEC_SHA {EXPECTED_SPEC_SHA256}", flush=True)
    print(f"LEAKED_SHA {V4_LEGACY_LEAKED_IMPLEMENTATION}", flush=True)
    print(f"CORRECTED_SHA {V4_CORRECTED_MACHINE_SHA256}", flush=True)
    print(f"NEW_SHA {report['hashes']['PB1_V4_CLARIFIED_MACHINE_SHA256']}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
