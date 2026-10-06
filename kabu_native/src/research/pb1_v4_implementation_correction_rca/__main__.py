"""PB1 V4 implementation correction RCA. Diagnosis only. Runtime 0/0/0."""
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
from research.pb1_v4_implementation_correction_rca import ANALYSIS_ID, PROGRAM_ID, V4_CORRECTED_MACHINE_SHA256
from research.pb1_v4_implementation_correction_rca.analyze import build_report_body
from research.pb1_v4_implementation_correction_rca.bind import bind_prior
from research.pb1_v4_implementation_correction_rca.constants_audit import inventory
from research.pb1_v4_implementation_correction_rca.isolation import (
    CACHE,
    CORRECTED_CACHE,
    CORRECTED_OUT,
    CORRECTED_SRC,
    FOUNDATION_OUT,
    LEAKED_SRC,
    OUT,
    RCA_OUT,
    SPEC_OUT,
    V2_OUT,
    V3_OUT,
    V31_OUT,
    V32_OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_v4_implementation_correction_rca.location_audit import (
    clear19,
    conditioned_parity,
    decompose_4063,
    failed_open_audit,
    interpret_7011_20250523,
    location_3382,
)
from research.pb1_v4_implementation_correction_rca.materialize import materialize_88
from research.pb1_v4_implementation_correction_rca.publish import (
    SHEET_ORDER,
    build_answers,
    build_sheets,
    finite_sanitize,
    write_artifacts,
)
from research.pb1_v4_implementation_correction_rca.s1_audit import s1_audit
from research.pb1_v4_implementation_correction_rca.spec import source_sha256
from research.pb1_v4_implementation_correction_rca.timeframe import compare
from research.pb1_v4_implementation_correction.definitions import machine_sha256 as corrected_machine_sha256

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
        "CORRECTED_MUTATED": False,
        "LEAKED_MUTATED": False,
        "PROSPECTIVE_EVENT_CONSUMED": False,
        "FUTURE_OUTCOME_USED": False,
        "INDEPENDENT_FACE_REVIEW_RUN": False,
        "COMPLETE_STRATEGY_NOT_RUN": True,
        "ECONOMIC_TEST_RUN": False,
        "RETURN_TEST": False,
        "MFE_MAE": False,
    }


def _funnel_to_audit(funnel: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for f in funnel:
        out.append(
            {
                "symbol": f.get("symbol"),
                "date": f.get("date"),
                "machine_S0": f.get("S0"),
                "machine_S1": f.get("S1"),
                "machine_S2": f.get("S2"),
                "machine_S3": f.get("S3"),
                "machine_S4": f.get("S4"),
                "machine_E0": f.get("E0"),
                "machine_E1": f.get("E1"),
                "machine_DIR": f.get("DIR"),
                "machine_opening_state": f.get("opening_state"),
                "machine_death": f.get("death"),
                "location_family": f.get("location_family"),
                "location_reason": f.get("location_reason"),
            }
        )
    return out


def _slim_bind(bind: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bind.get("ok"),
        "reason": bind.get("reason"),
        "live_corrected_sha": bind.get("live_corrected_sha"),
        "report_corrected_sha": bind.get("report_corrected_sha"),
        "V4_CORRECTED_MACHINE_SHA256": bind.get("V4_CORRECTED_MACHINE_SHA256"),
        "corrected_sha_unchanged": bind.get("corrected_sha_unchanged"),
        "leaked_preserved": bind.get("leaked_preserved"),
        "parent_spec_verdict": bind.get("parent_spec_verdict"),
        "symbol_n": len(list(bind.get("symbols") or [])),
        "any_rule_changed": False,
    }


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 PB1_V4_CORRECTION_RCA FROZEN_VAL CLOSED", flush=True)
    print(f"PROGRAM_ID {PROGRAM_ID}", flush=True)
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(str(before.get("ACTIVE_CAPTURE_PATH") or ""), str(before.get("ACTIVE_PAPER_SESSION") or ""))
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    corr_src_before = _fingerprint_py(CORRECTED_SRC)
    leaked_src_before = _fingerprint_py(LEAKED_SRC)
    corr_out_before = _fingerprint_out(CORRECTED_OUT)
    fps = {k: _fingerprint_out(p) for k, p in {"spec": SPEC_OUT, "rca": RCA_OUT, "v32": V32_OUT, "v31": V31_OUT, "v3": V3_OUT, "v2": V2_OUT, "foundation": FOUNDATION_OUT, "corrected": CORRECTED_OUT}.items()}
    bind = bind_prior()
    if not bind.get("ok"):
        raise RuntimeError(f"BIND_FAILED {bind.get('reason')}")
    if corrected_machine_sha256() != V4_CORRECTED_MACHINE_SHA256:
        raise RuntimeError("CORRECTED_SHA_CHANGED")
    walked_path = CORRECTED_CACHE / "walked.json"
    walked = json.loads(walked_path.read_text(encoding="utf-8")) if walked_path.is_file() else {}
    audit_rows = _funnel_to_audit(list(walked.get("funnel_days") or []))
    print(f"FUNNEL_DAYS {len(audit_rows)}", flush=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_mat = CACHE / "materialized.json"
    if cache_mat.is_file():
        loaded = json.loads(cache_mat.read_text(encoding="utf-8"))
        if int(loaded.get("n") or 0) == 88 and list(loaded.get("rows") or []):
            material = {"ok": True, "n": 88, "rows": list(loaded.get("rows") or []), "timelines": dict(loaded.get("timelines") or {}), "future_outcome_n": 0}
            print("MATERIALIZE_FROM_CACHE 88", flush=True)
        else:
            material = materialize_88(bind, audit_rows=audit_rows)
    else:
        material = materialize_88(bind, audit_rows=audit_rows)
    if not material.get("ok"):
        raise RuntimeError(f"MATERIALIZE_FAILED {material.get('reason')}")
    (CACHE / "materialized.json").write_text(
        json.dumps(
            finite_sanitize({"n": material.get("n"), "rows": material.get("rows"), "timelines": material.get("timelines")}),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"MATERIALIZED {material.get('n')}", flush=True)
    constants = inventory()
    s1 = s1_audit(list(material.get("rows") or []))
    fo = failed_open_audit(list(material.get("rows") or []), dict(material.get("timelines") or {}))
    loc = location_3382(list(material.get("rows") or []), dict(material.get("timelines") or {}))
    clear = clear19(list(material.get("rows") or []), dict(material.get("timelines") or {}))
    d4063 = decompose_4063(list(material.get("rows") or []), dict(material.get("timelines") or {}))
    h7011 = interpret_7011_20250523(fo)
    cond = conditioned_parity(list(material.get("rows") or []))
    tf = compare(loc3382=loc, s2_s3_note="Current commit_s2_s3 sets S2 and S3 on the same completed-5m hold event.")
    corr_src_after = _fingerprint_py(CORRECTED_SRC)
    leaked_src_after = _fingerprint_py(LEAKED_SRC)
    body = build_report_body(
        bind=bind,
        constants=constants,
        material=material,
        s1=s1,
        failed_open=fo,
        loc3382=loc,
        clear=clear,
        d4063=d4063,
        h7011=h7011,
        conditioned=cond,
        timeframe=tf,
        corrected_fp_before=corr_src_before,
        corrected_fp_after=corr_src_after,
        leaked_fp_before=leaked_src_before,
        leaked_fp_after=leaked_src_after,
    )
    after = snapshot(phase="POST")
    safety = _safety()
    decision = dict(body.get("decision") or {})
    decision["WRITE_OVERLAP_N"] = overlap
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Diagnose remaining mismatch of corrected V4 vs frozen semantic spec",
            "NON_GOALS": ["mutate 21fc72eb", "V4.1", "threshold search", "PnL", "prospective data", "implementation correction in this RCA"],
        },
        "hashes": {
            "SOURCE_SHA256": source_sha256(),
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
    }
    report["answers"] = build_answers(report)
    assert_no_secret(report, where="report")
    sheets = build_sheets(report)
    assert tuple(sheets.keys()) == SHEET_ORDER
    write_artifacts(report, sheets)
    for k, p in {"spec": SPEC_OUT, "rca": RCA_OUT, "v32": V32_OUT, "v31": V31_OUT, "v3": V3_OUT, "v2": V2_OUT, "foundation": FOUNDATION_OUT, "corrected": CORRECTED_OUT}.items():
        if fps[k] != _fingerprint_out(p):
            raise RuntimeError(f"PRIOR_MUTATED_{k}")
    if corr_src_before != corr_src_after:
        raise RuntimeError("CORRECTED_SRC_MUTATED")
    if leaked_src_before != leaked_src_after:
        raise RuntimeError("LEAKED_SRC_MUTATED")
    if corrected_machine_sha256() != V4_CORRECTED_MACHINE_SHA256:
        raise RuntimeError("CORRECTED_SHA_CHANGED_AFTER")
    if corr_out_before != _fingerprint_out(CORRECTED_OUT):
        raise RuntimeError("CORRECTED_OUT_MUTATED")
    print(f"OUT {OUT}", flush=True)
    print(f"CORRECTED_SHA {V4_CORRECTED_MACHINE_SHA256}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
