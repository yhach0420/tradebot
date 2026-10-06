"""PB1 V4 clarified-machine parity RCA. Diagnosis only. Runtime 0/0/0."""
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
from research.pb1_v4_clarified_machine_parity_rca import ANALYSIS_ID, ANY_CODE_CHANGED, EXPECTED_MACHINE_SHA256, EXPECTED_SPEC_SHA256, PROGRAM_ID
from research.pb1_v4_clarified_machine_parity_rca.analyze import build_report_body
from research.pb1_v4_clarified_machine_parity_rca.bind import bind_prior
from research.pb1_v4_clarified_machine_parity_rca.failed_open import failed_open_rca
from research.pb1_v4_clarified_machine_parity_rca.family_a import family_a_rca
from research.pb1_v4_clarified_machine_parity_rca.flat_crawl import flat_crawl_rca
from research.pb1_v4_clarified_machine_parity_rca.isolation import (
    CACHE,
    AUDIT_OUT,
    CLARIFIED_SPEC_OUT,
    CLARIFIED_SPEC_SRC,
    CORRECTED_OUT,
    CORRECTED_SRC,
    FOUNDATION_OUT,
    LEAKED_OUT,
    LEAKED_SRC,
    MACHINE_OUT,
    MACHINE_SRC,
    OUT,
    READY_SPEC_OUT,
    READY_SPEC_SRC,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_v4_clarified_machine_parity_rca.materialize import materialize_88
from research.pb1_v4_clarified_machine_parity_rca.one_bar import one_bar_rca
from research.pb1_v4_clarified_machine_parity_rca.progress import late_time_alignment, progress_log
from research.pb1_v4_clarified_machine_parity_rca.publish import SHEET_ORDER, build_answers, build_sheets, finite_sanitize, write_artifacts
from research.pb1_v4_clarified_machine_parity_rca.reclassify import reclassify_seven
from research.pb1_v4_clarified_machine_parity_rca.seed_two_sided import compare_8031_7741
from research.pb1_v4_clarified_machine_parity_rca.spec import source_sha256
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
        "MACHINE_MUTATED": False,
        "SPEC_MUTATED": False,
        "CORRECTED_MUTATED": False,
        "LEAKED_MUTATED": False,
        "V4_1_CREATED": False,
        "PROSPECTIVE_EVENT_CONSUMED": False,
        "FUTURE_OUTCOME_USED": False,
        "ECONOMIC_TEST_RUN": False,
        "RETURN_TEST": False,
        "MFE_MAE": False,
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
            "audit_out": AUDIT_OUT,
            "foundation": FOUNDATION_OUT,
        }.items()
    }


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 PB1_V4_CLARIFIED_MACHINE_PARITY_RCA FROZEN_VAL CLOSED CONFIRMATION CLOSED", flush=True)
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
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE / "materialized_88.json"
    if os.environ.get("PB1_V4_PARITY_RCA_RELOAD") == "1" and cache_path.is_file():
        mat = json.loads(cache_path.read_text(encoding="utf-8"))
        print("LOADED_RCA_CACHE", flush=True)
    else:
        mat = materialize_88(bind=bind)
        cache_path.write_text(json.dumps(finite_sanitize(mat, strip=False), ensure_ascii=False), encoding="utf-8")
    if not mat.get("ok"):
        raise RuntimeError(f"MATERIALIZE_FAILED {mat.get('reason')}")
    rows = list(mat.get("rows") or [])
    print(f"RCA_N {len(rows)} snapped={mat.get('snapped_n')}", flush=True)
    logs = {}
    for r in rows:
        logs[(str(r.get("symbol")), str(r.get("date")))] = progress_log(r)
    late = late_time_alignment(rows, logs)
    two = compare_8031_7741(rows)
    fo = failed_open_rca(rows)
    flat = flat_crawl_rca(rows)
    family = family_a_rca(rows)
    onebar = one_bar_rca(rows)
    reclass = reclassify_seven(rows=rows, late=late, fo=fo, flat=flat, two=two, logs=logs)
    (CACHE / "progress_logs.json").write_text(
        json.dumps(
            finite_sanitize({f"{s}|{d}": dict(log) for (s, d), log in logs.items()}, strip=False),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(
        f"CONFIRMED_BUGS {reclass.get('confirmed_machine_bug_n')} LATE_LEAKS {late.get('actual_active_leaks_after_time_alignment_n')} "
        f"3110 { (fo.get('3110') or {}).get('abc') } A3 {family.get('lack_real_causal_clear_n')}",
        flush=True,
    )
    fps_after = _fps()
    body = build_report_body(bind, mat, logs, late, two, fo, flat, family, onebar, reclass, fps, fps_after)
    after = snapshot(phase="POST")
    decision = dict(body.get("decision") or {})
    decision["WRITE_OVERLAP_N"] = overlap
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Diagnose why machine 33b1bf fails semantic parity against CLARIFIED_V2",
            "NON_GOALS": ["modify machine", "modify spec", "tune thresholds", "prospective data", "returns/PnL/MFE/MAE"],
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
        "safety": _safety(),
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
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
