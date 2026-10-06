"""Offline slot-release marginal admission quality RCA. No Capture control. No new rule."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")

from research.am_entry_profit_improvement import CANCEL_N, ELIGIBLE_DAYS, LIVE_ORDER_N, SESSION, SUBMIT_N
from research.simple_tech_entry_family.indicators import self_check
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v13_analyze import reporting_semantics
from research.simple_tech_redesign.branch_u_bb_spec import PARENT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.entry_anchored_floor_break_candidate_analyze import residual_index
from research.simple_tech_redesign.exit_composition_shift_rca_analyze import load_residual_rows
from research.simple_tech_redesign.isolation import (
    ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT,
    SLOT_RELEASE_MARGINAL_RCA_OUT,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_analyze import decide, evaluate_cohort
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_harvest import harvest_day
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_publish import (
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_spec import (
    ANALYSIS_ID,
    CAP_CHANGED,
    EARLIEST_POSSIBLE_IF_CASE_A,
    ENTRY_CHANGED,
    FROZEN_CANDIDATE_ID,
    FROZEN_VERDICT,
    NEW_ENTRY_FILTER,
    NEW_EXIT_RULE,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    canonical_marginal_spec,
    source_sha256_marginal,
    spec_sha256_marginal,
)

INTEGRITY_ZERO = ("SUBMIT_N", "CANCEL_N", "LIVE_ORDER_N", "NEW_EXIT_RULE_N", "THRESHOLD_SEARCH_N", "ACTIVE_CAPTURE_INPUT_N", "ENTRY_CHANGE_N", "CAP_CHANGE_N")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str, source_sha: str) -> int:
    decision = {
        "CASE": "G",
        "VERDICT": "SIMPLE_TECH_SLOT_RELEASE_MARGINAL_RCA_INTEGRITY_FAILED",
        "NEXT": msg,
        "CANDIDATE_FROZEN": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "first_eligible_prospective_date": None,
        "earliest_possible_if_case_A": EARLIEST_POSSIBLE_IF_CASE_A,
        "prospective_armed": False,
        "NEW_RULE_CREATED": False,
        "ENTRY_CHANGED": False,
        "CAP_CHANGED": False,
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": decision["VERDICT"],
            "CANDIDATE_FROZEN": False,
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "first_eligible_prospective_date": None,
            "earliest_possible_if_case_A": EARLIEST_POSSIBLE_IF_CASE_A,
            "prospective_armed": False,
        },
        "decision": decision,
        "development": {},
        "forward": {},
        "leak": leak,
        "preflight": pre,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))
    print(msg, flush=True)
    print("STOP.", flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    sha = spec_sha256_marginal()
    source_sha = source_sha256_marginal()
    pre = snapshot(phase="PRE")
    leak = {k: 0 for k in INTEGRITY_ZERO}
    leak.update({"SUBMIT_N": int(SUBMIT_N), "CANCEL_N": int(CANCEL_N), "LIVE_ORDER_N": int(LIVE_ORDER_N)})
    leak["NEW_EXIT_RULE_N"] = int(bool(NEW_EXIT_RULE))
    leak["THRESHOLD_SEARCH_N"] = int(bool(THRESHOLD_SEARCH))
    leak["ENTRY_CHANGE_N"] = int(bool(ENTRY_CHANGED or NEW_ENTRY_FILTER))
    leak["CAP_CHANGE_N"] = int(bool(CAP_CHANGED))
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or ""))
    print(f"PREFLIGHT {ANALYSIS_ID} spec={sha[:12]} today={TODAY}", flush=True)
    if v1_spec_sha256() != PARENT_SPEC_SHA256_EXPECTED or not self_check().get("ok"):
        return _stop("STOP. Preflight failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if SESSION != "AM" or NEW_EXIT_RULE or THRESHOLD_SEARCH or NEW_ENTRY_FILTER or CAP_CHANGED or ENTRY_CHANGED or bool(TRUE_OOS):
        return _stop("STOP. Forbidden flags.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if any(str(d) >= "20260903" for d in list(ELIGIBLE_DAYS) + list(LOCKED_SERIES_DAYS)):
        return _stop("STOP. Day list includes 20260903+.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if TODAY in set(ELIGIBLE_DAYS) or TODAY in set(LOCKED_SERIES_DAYS):
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    frozen = _load(ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT / "report.json")
    frozen_dec = dict(frozen.get("decision") or {})
    frozen_req = dict(frozen.get("required") or {})
    if str(frozen_dec.get("VERDICT") or frozen_req.get("VERDICT") or "") != FROZEN_VERDICT:
        return _stop("STOP. Frozen CASE D verdict mismatch.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if bool(frozen_dec.get("CANDIDATE_FROZEN")):
        return _stop("STOP. Rejected candidate must not be frozen.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if str(frozen_req.get("CANDIDATE_ID") or frozen.get("candidate_id") or "") != FROZEN_CANDIDATE_ID:
        return _stop("STOP. Frozen candidate id mismatch.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if frozen_dec.get("PRIMARY_NEXT_EXIT_TARGET") is not None or frozen_req.get("PRIMARY_NEXT_EXIT_TARGET") is not None:
        return _stop("STOP. PRIMARY_NEXT_EXIT_TARGET must remain None.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    leak["ACTIVE_CAPTURE_INPUT_N"] = input_active_file_n(
        [str(ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT / "report.json")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active capture input.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    try:
        residual_dev, residual_fwd, _body = load_residual_rows()
    except (AssertionError, FileNotFoundError) as exc:
        return _stop(f"STOP. Residual SoT: {exc}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    harvested_ok = True
    dev_bodies: list[dict[str, Any]] = []
    fwd_bodies: list[dict[str, Any]] = []
    for day in list(ELIGIBLE_DAYS):
        body = harvest_day(day, cohort="DEVELOPMENT", today=TODAY)
        if not body.get("ok"):
            return _stop(f"STOP. Harvest DEVELOPMENT {day}: {body.get('blocker')}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
        print(f"DEVELOPMENT {day} cap_only={len((body.get('cap_inventory') or {}).get('cap_only') or [])} incr={sum(1 for c in body.get('chains') or [] if c.get('is_incremental'))}", flush=True)
        dev_bodies.append(body)
    for day in list(LOCKED_SERIES_DAYS):
        body = harvest_day(day, cohort="FORWARD_BURNED", today=TODAY)
        if not body.get("ok"):
            return _stop(f"STOP. Harvest FORWARD_BURNED {day}: {body.get('blocker')}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
        print(f"FORWARD_BURNED {day} cap_only={len((body.get('cap_inventory') or {}).get('cap_only') or [])} incr={sum(1 for c in body.get('chains') or [] if c.get('is_incremental'))}", flush=True)
        fwd_bodies.append(body)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    dev = evaluate_cohort(cohort="DEVELOPMENT", bodies=dev_bodies, residual=residual_index(residual_dev))
    fwd = evaluate_cohort(cohort="FORWARD_BURNED", bodies=fwd_bodies, residual=residual_index(residual_fwd))
    if not bool(dev.get("identity", {}).get("ok")) or not bool(fwd.get("identity", {}).get("ok")):
        return _stop(f"STOP. Identity mismatch DEV={dev.get('identity')} FWD={fwd.get('identity')}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    decision = decide(dev, fwd, leak_ok=leak_ok, harvested_ok=harvested_ok)
    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    slim_dev = dict(dev)
    slim_fwd = dict(fwd)
    report = {
        "analysis_id": ANALYSIS_ID,
        "spec_sha256": sha,
        "source_sha256": source_sha,
        "canonical_spec": canonical_marginal_spec(),
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": decision.get("VERDICT"),
            "CASE": decision.get("CASE"),
            "CANDIDATE_FROZEN": False,
            "FAMILY_CLOSED": True,
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "PRIMARY_NEXT_EXIT_TARGET": None,
            "first_eligible_prospective_date": None,
            "earliest_possible_if_case_A": EARLIEST_POSSIBLE_IF_CASE_A,
            "prospective_armed": False,
            "NEW_RULE_CREATED": False,
            "ENTRY_CHANGED": False,
            "CAP_CHANGED": False,
            "PRIMARY_CAUSAL_BOTTLENECK": decision.get("PRIMARY_CAUSAL_BOTTLENECK"),
        },
        "decision": decision,
        "development": slim_dev,
        "forward": slim_fwd,
        "leak": leak,
        "preflight": pre,
        "postflight": post,
        "reporting_semantics": reporting,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))
    print(f"FINAL {ANALYSIS_ID} CASE={decision.get('CASE')} VERDICT={decision.get('VERDICT')} BOTTLENECK={decision.get('PRIMARY_CAUSAL_BOTTLENECK')} out={SLOT_RELEASE_MARGINAL_RCA_OUT}", flush=True)
    print("STOP.", flush=True)
    return 0 if leak_ok and harvested_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
