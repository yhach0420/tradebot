"""PB1 V4 implementation correction. Frozen spec. Leaked SHA immutable. Runtime 0/0/0."""
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
from research.pb1_v4_implementation_correction import ANALYSIS_ID, PROGRAM_ID, V4_LEGACY_LEAKED_IMPLEMENTATION
from research.pb1_v4_implementation_correction.analyze import build_report_body
from research.pb1_v4_implementation_correction.audit import audit_88
from research.pb1_v4_implementation_correction.bind import bind_prior
from research.pb1_v4_implementation_correction.calibrate import calibrate_from_descriptors
from research.pb1_v4_implementation_correction.isolation import (
    CACHE,
    FOUNDATION_OUT,
    LEAKED_OUT,
    OUT,
    RCA_OUT,
    SPEC_OUT,
    V1_OUT,
    V2_OUT,
    V3_OUT,
    V31_OUT,
    V32_OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_v4_implementation_correction.publish import SHEET_ORDER, build_answers, build_sheets, write_artifacts
from research.pb1_v4_implementation_correction.spec import source_sha256
from research.pb1_v4_implementation_correction.walk import emit_v4
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256

JST = ZoneInfo("Asia/Tokyo")
LEAKED_SRC = Path(__file__).resolve().parents[1] / "pb1_v4_machine_implementation"


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
        "RETEST_5MIN_GATE": False,
        "CLOCK_0930_CUTOFF": False,
        "NEW_INDICATOR": False,
        "V2_MUTATED": False,
        "V3_MUTATED": False,
        "V31_MUTATED": False,
        "V32_MUTATED": False,
        "LEAKED_V4_MUTATED": False,
        "INDEPENDENT_FACE_REVIEW_RUN": False,
        "COMPLETE_STRATEGY_NOT_RUN": True,
        "ECONOMIC_TEST_RUN": False,
        "RETURN_TEST": False,
        "MFE_MAE": False,
        "ECONOMIC_E0_E1_COMPARISON": False,
        "PROSPECTIVE_EVENT_CONSUMED": False,
        "V4_1_CREATED": False,
        "SPEC_CHANGED": False,
    }


def _slim_bind(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    return {
        "ok": bind.get("ok"),
        "reason": bind.get("reason"),
        "research_pool_n": bind.get("research_pool_n"),
        "parent_spec_verdict": bind.get("parent_spec_verdict"),
        "live_leaked_v4_sha": bind.get("live_leaked_v4_sha"),
        "report_leaked_v4_sha": bind.get("report_leaked_v4_sha"),
        "V4_LEGACY_LEAKED_IMPLEMENTATION": bind.get("V4_LEGACY_LEAKED_IMPLEMENTATION"),
        "leaked_preserved": bind.get("leaked_preserved"),
        "live_v32_machine_sha256": bind.get("live_v32_machine_sha256"),
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
        "spec_changed": False,
        "v4_1_created": False,
    }


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 PB1_V4_CORRECTION FROZEN_VAL CLOSED CONFIRMATION CLOSED", flush=True)
    print(f"PROGRAM_ID {PROGRAM_ID}", flush=True)
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    leaked_src_before = _fingerprint(LEAKED_SRC, py_only=True)
    leaked_out_before = _fingerprint(LEAKED_OUT)
    fps = {
        k: _fingerprint(p)
        for k, p in {
            "spec": SPEC_OUT,
            "rca": RCA_OUT,
            "v32": V32_OUT,
            "v31": V31_OUT,
            "v3": V3_OUT,
            "v2": V2_OUT,
            "v1": V1_OUT,
            "foundation": FOUNDATION_OUT,
            "leaked_out": LEAKED_OUT,
        }.items()
    }
    bind = bind_prior()
    if not bind.get("ok"):
        raise RuntimeError(f"BIND_FAILED {bind.get('reason')}")
    if leaked_machine_sha256() != V4_LEGACY_LEAKED_IMPLEMENTATION:
        raise RuntimeError("LEAKED_V4_SHA_CHANGED")
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_walk = CACHE / "walked.json"
    relabel = os.environ.get("PB1_V4_CORRECTION_RELABEL") == "1"
    if relabel and cache_walk.is_file():
        walked = json.loads(cache_walk.read_text(encoding="utf-8"))
        print("LOADED_WALK_CACHE", flush=True)
    else:
        walked = emit_v4(bind)
        cache_walk.write_text(json.dumps({k: v for k, v in walked.items()}, default=str, ensure_ascii=False), encoding="utf-8")
    if not walked.get("ok"):
        raise RuntimeError(f"WALK_FAILED {walked.get('reason')}")
    print(
        f"SETUP_N {len(walked.get('setups') or [])} E0 {len(walked.get('e0_events') or [])} "
        f"E1 {len(walked.get('e1_events') or [])} SAME_BAR {walked.get('same_bar_entry_n')}",
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
    print(f"AUDIT88 {audit.get('n')} S4_CLEAR {audit.get('clear_exemplar_s4_n')} DEVELOPMENT_PARITY_ONLY", flush=True)
    leaked_src_after = _fingerprint(LEAKED_SRC, py_only=True)
    leaked_out_after = _fingerprint(LEAKED_OUT)
    body = build_report_body(
        bind,
        walked,
        calib,
        audit,
        leaked_src_before,
        leaked_src_after,
        leaked_out_before,
        leaked_out_after,
    )
    after = snapshot(phase="POST")
    safety = _safety()
    decision = dict(body.get("decision") or {})
    decision["WRITE_OVERLAP_N"] = overlap
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Correct leaked V4 to frozen PB1_V4_SEMANTIC_SPEC_READY_V1",
            "NON_GOALS": [
                "V4.1",
                "threshold optimization",
                "economic optimization",
                "face validation",
                "PnL / PF / MFE / MAE / future return",
                "Confirmation or Frozen Validation",
                "mutate leaked V4 or parents",
                "prospective unseen events",
            ],
        },
        "hashes": {
            "SOURCE_SHA256": source_sha256(),
            "DETECTOR_SHA256": bind.get("DETECTOR_SHA256"),
            "STATE_MACHINE_SHA256": bind.get("STATE_MACHINE_SHA256"),
            "V4_CORRECTED_MACHINE_SHA256": body.get("V4_CORRECTED_MACHINE_SHA256"),
            "V4_LEGACY_LEAKED_IMPLEMENTATION": V4_LEGACY_LEAKED_IMPLEMENTATION,
            "PARENT_V32_SHA": body.get("PARENT_V32_SHA"),
            "PARENT_V31_SHA": body.get("PARENT_V31_SHA"),
            "PARENT_V3_SHA": body.get("PARENT_V3_SHA"),
            "PARENT_V2_SHA": body.get("PARENT_V2_SHA"),
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
    for k, p in {
        "spec": SPEC_OUT,
        "rca": RCA_OUT,
        "v32": V32_OUT,
        "v31": V31_OUT,
        "v3": V3_OUT,
        "v2": V2_OUT,
        "v1": V1_OUT,
        "foundation": FOUNDATION_OUT,
        "leaked_out": LEAKED_OUT,
    }.items():
        if fps[k] != _fingerprint(p):
            raise RuntimeError(f"PRIOR_MUTATED_{k}")
    if leaked_machine_sha256() != V4_LEGACY_LEAKED_IMPLEMENTATION:
        raise RuntimeError("LEAKED_V4_SHA_CHANGED_AFTER")
    if leaked_src_before != leaked_src_after:
        raise RuntimeError("LEAKED_V4_SOURCE_MUTATED")
    if int(report.get("same_bar_entry_n") or 0) != 0:
        raise RuntimeError("SAME_BAR_ENTRY")
    if not report.get("v32_unchanged"):
        raise RuntimeError("V32_SHA_DRIFT")
    print(f"OUT {OUT}", flush=True)
    print(f"LEAKED_SHA {V4_LEGACY_LEAKED_IMPLEMENTATION}", flush=True)
    print(f"CORRECTED_SHA {report['hashes']['V4_CORRECTED_MACHINE_SHA256']}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
