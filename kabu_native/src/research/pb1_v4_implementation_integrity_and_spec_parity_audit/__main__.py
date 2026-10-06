"""PB1 V4 implementation integrity and spec-parity audit. Diagnosis only. Runtime 0/0/0."""
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
from research.pb1_v4_implementation_integrity_and_spec_parity_audit import ANALYSIS_ID, FROZEN_V4_SHA, PROGRAM_ID
from research.pb1_v4_implementation_integrity_and_spec_parity_audit.analyze import build_report_body
from research.pb1_v4_implementation_integrity_and_spec_parity_audit.bind import bind_prior
from research.pb1_v4_implementation_integrity_and_spec_parity_audit.counts import audit_counts
from research.pb1_v4_implementation_integrity_and_spec_parity_audit.invariants import audit_identity, audit_invariants
from research.pb1_v4_implementation_integrity_and_spec_parity_audit.isolation import (
    CACHE,
    OUT,
    V4_CACHE,
    V4_OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_v4_implementation_integrity_and_spec_parity_audit.leakage import audit_legacy_leakage
from research.pb1_v4_implementation_integrity_and_spec_parity_audit.parity import audit_parity
from research.pb1_v4_implementation_integrity_and_spec_parity_audit.publish import build_answers, write_artifacts
from research.pb1_v4_implementation_integrity_and_spec_parity_audit.spec import source_sha256
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as v4_machine_sha256

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
        "V4_RULE_CHANGED": False,
        "V4_1_CREATED": False,
        "PROSPECTIVE_FACE_RUN": False,
        "FUTURE_OUTCOME_USED": False,
        "WALK_RE_RUN": False,
        "INDEPENDENT_FACE_REVIEW_RUN": False,
        "COMPLETE_STRATEGY_NOT_RUN": True,
        "ECONOMIC_TEST_RUN": False,
        "RETURN_TEST": False,
        "MFE_MAE": False,
    }


def _md(report: dict[str, Any], answers: dict[str, Any], safety: dict[str, Any]) -> str:
    a = answers
    c = dict(report.get("counts") or {})
    par = dict(report.get("parity") or {})
    leak = dict(report.get("leakage") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        "Diagnosis only. Frozen V4 machine not mutated. No prospective face. No PnL.",
        "",
        f"- VERDICT: **{a.get('VERDICT')}**",
        f"- NEXT: **{a.get('NEXT')}**",
        f"- V4 SHA unchanged: `{report.get('V4_MACHINE_SHA256')}`",
        "",
        "## Count meanings (NOT a strict funnel)",
        "",
        str(c.get("why_reported_S3_gt_S2")),
        "",
        "Rename:",
        "",
    ]
    for k, v in dict(c.get("rename") or {}).items():
        lines.append(f"- `{k}` → {v}")
    lines += [
        "",
        "## STOP answers",
        "",
        f"- V4 SHA unchanged? **{a.get('V4_SHA_unchanged')}**",
        f"- Any rule changed? **{a.get('Any_rule_changed')}**",
        f"- Strict funnel counts? **{a.get('Strict_funnel_counts')}**",
        f"- S1_without_S0_n? **{a.get('S1_without_S0_n')}**",
        f"- S2_without_S1_n? **{a.get('S2_without_S1_n')}**",
        f"- S3_without_S2_n? **{a.get('S3_without_S2_n')}**",
        f"- S4_without_S3_n? **{a.get('S4_without_S3_n')}**",
        f"- Any setup identity mismatch? **{a.get('Any_setup_identity_mismatch')}**",
        f"- Any legacy V3/V3.2 rule leakage? **{a.get('Any_legacy_V3_V3_2_rule_leakage')}**",
        f"- Is NO_MEANINGFUL_ROOM explicitly part of frozen V4 spec? **{a.get('NO_MEANINGFUL_ROOM_explicitly_part_of_frozen_V4_spec')}**",
        f"- Is STRUCTURALLY_BLOCKED explicitly part of frozen V4 spec? **{a.get('STRUCTURALLY_BLOCKED_explicitly_part_of_frozen_V4_spec')}**",
        f"- Why only 7/19 CLEAR reach S4? {a.get('Why_only_7_of_19_CLEAR_reached_S4')}",
        f"- CLEAR misses S1/S2/S3/S4? **{a.get('CLEAR_miss_upstream_S1')} / {a.get('CLEAR_miss_S2')} / {a.get('CLEAR_miss_S3')} / {a.get('CLEAR_miss_S4')}**",
        f"- S1 mismatch implementation_bug / spec_ambiguity / expected_threshold? **{a.get('mismatch_implementation_bug_n')} / {a.get('mismatch_spec_ambiguity_n')} / {a.get('mismatch_expected_threshold_disagreement_n')}**",
        f"- 6787 cause: `{a.get('6787_location_mismatch_cause')}`",
        f"- 6963 cause: `{a.get('6963_location_mismatch_cause')}`",
        f"- 6857 cause: `{a.get('6857_location_mismatch_cause')}`",
        f"- 9432 cause: {a.get('9432_failure_layer_mismatch_cause')}",
        f"- Negative final reject accuracy? **{a.get('Negative_final_reject_accuracy')}**",
        f"- Negative correct-layer accuracy? **{a.get('Negative_correct_layer_accuracy')}**",
        f"- E0 invariant valid? **{a.get('E0_invariant_valid')}**",
        f"- E1 invariant valid? **{a.get('E1_invariant_valid')}**",
        f"- SAME_BAR_ENTRY? **{a.get('SAME_BAR_ENTRY')}**",
        f"- Any future outcome used? **{a.get('Any_future_outcome_used')}**",
        f"- Any PnL? **{a.get('Any_PnL')}**",
        f"- Prospective data consumed? **{a.get('Prospective_data_consumed')}**",
        f"- Old Confirmation opened? **{a.get('Old_Confirmation_opened')}**",
        f"- Frozen Validation opened? **{a.get('Frozen_Validation_opened')}**",
        f"- submit/cancel/live? **{a.get('submit_cancel_live')}**",
        f"- VERDICT? **{a.get('VERDICT')}**",
        f"- NEXT? **{a.get('NEXT')}**",
        "",
        "STOP.",
        "",
        f"CLEAR S4 {par.get('clear_s4_n')} / {par.get('clear_n')}. Spec full-stack {par.get('spec_full_stack_n')}.",
        f"Legacy leakage n={leak.get('legacy_rule_leakage_n')}.",
        f"Safety {safety}.",
    ]
    return "\n".join(lines)


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 PB1_V4_INTEGRITY_AUDIT FROZEN_VAL CLOSED CONFIRMATION CLOSED", flush=True)
    print(f"PROGRAM_ID {PROGRAM_ID}", flush=True)
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    v4_out_fp_before = _fingerprint(V4_OUT)
    sha_before = v4_machine_sha256()
    bind = bind_prior()
    if not bind.get("ok"):
        raise RuntimeError(f"BIND_FAILED {bind.get('reason')}")
    walked_path = V4_CACHE / "walked.json"
    walked = json.loads(walked_path.read_text(encoding="utf-8"))
    if not walked.get("ok"):
        raise RuntimeError("WALK_CACHE_INVALID")
    print(
        f"LOADED_WALK_CACHE setups={len(walked.get('setups') or [])} "
        f"funnel_days={len(walked.get('funnel_days') or [])} NO_REWALK",
        flush=True,
    )
    counts = audit_counts(walked)
    invariants = audit_invariants(walked)
    identity = audit_identity(walked)
    leakage = audit_legacy_leakage()
    parity = audit_parity(walked)
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "invariant_violations.json").write_text(
        json.dumps(invariants.get("violating_ids") or {}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    sha_after = v4_machine_sha256()
    v4_out_fp_after = _fingerprint(V4_OUT)
    if sha_before != FROZEN_V4_SHA or sha_after != FROZEN_V4_SHA:
        raise RuntimeError("V4_SHA_CHANGED")
    if v4_out_fp_before != v4_out_fp_after:
        raise RuntimeError("V4_OUT_MUTATED")
    body = build_report_body(
        bind=bind,
        counts=counts,
        invariants=invariants,
        identity=identity,
        leakage=leakage,
        parity=parity,
        v4_src_sha_before=sha_before,
        v4_src_sha_after=sha_after,
    )
    after = snapshot(phase="POST")
    safety = _safety()
    safety["WRITE_OVERLAP_N"] = overlap
    body["decision"]["WRITE_OVERLAP_N"] = overlap
    answers = build_answers(body)
    report = {
        **body,
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "created_at_jst": datetime.now(JST).isoformat(),
        "hashes": {
            "SOURCE_SHA256": source_sha256(),
            "V4_MACHINE_SHA256": sha_after,
            "PLAYBOOK_MACHINE_SHA256": sha_after,
        },
        "preflight": {"ok": True, "capture": before, "paper": after, "runtime": advanced(before, after)},
        "answers": answers,
        "safety": safety,
    }
    report["_markdown"] = _md(report, answers, safety)
    write_artifacts(report, answers, safety)
    print(f"VERDICT {answers.get('VERDICT')}", flush=True)
    print(f"NEXT {answers.get('NEXT')}", flush=True)
    print(f"S3_without_S2_n {answers.get('S3_without_S2_n')}", flush=True)
    print(f"LEGACY {answers.get('Any_legacy_V3_V3_2_rule_leakage')}", flush=True)
    assert_no_secret(report, where="report")
    if int(safety["SUBMIT_N"]) or int(safety["CANCEL_N"]) or int(safety["LIVE_ORDER_N"]):
        raise RuntimeError("LIVE_TOUCH")
    if answers.get("VERDICT") is None:
        raise RuntimeError("NO_VERDICT")
    print("STOP", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
