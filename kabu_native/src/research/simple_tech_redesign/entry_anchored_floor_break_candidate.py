"""Offline ENTRY_ANCHORED_PRE_UPSIDE_FLOOR_BREAK_V1 full causal portfolio evaluation."""
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
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")

from research.am_entry_profit_improvement import CANCEL_N, ELIGIBLE_DAYS, LIVE_ORDER_N, SESSION, SUBMIT_N
from research.simple_tech_entry_family.indicators import self_check
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v13_analyze import reporting_semantics
from research.simple_tech_redesign.branch_u_bb_spec import PARENT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.branch_u_causal_harvest import _add_arm, _empty_arm
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.entry_anchored_floor_break_candidate_analyze import decide, evaluate_cohort, residual_index
from research.simple_tech_redesign.entry_anchored_floor_break_candidate_harvest import harvest_day
from research.simple_tech_redesign.entry_anchored_floor_break_candidate_publish import (
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.simple_tech_redesign.entry_anchored_floor_break_candidate_spec import (
    ANALYSIS_ID,
    CANDIDATE_ID,
    FIRST_PROSPECTIVE_DAY,
    NEW_EXIT_RULE,
    SOURCE_RCA_VERDICT,
    STATE_MACHINE,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    canonical_candidate_spec,
    candidate_identity_hash,
    source_sha256_candidate,
    spec_sha256_candidate,
)
from research.simple_tech_redesign.exit_composition_shift_rca_analyze import load_residual_rows
from research.simple_tech_redesign.isolation import (
    ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT,
    ENTRY_ANCHORED_PULLBACK_RCA_OUT,
    EXIT_RESIDUAL_RCA_OUT,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)

INTEGRITY_ZERO = (
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
    "NEW_EXIT_RULE_N",
    "THRESHOLD_SEARCH_N",
    "ACTIVE_CAPTURE_INPUT_N",
    "FUTURE_QUOTE_CARRYBACK_N",
    "FUTURE_TIMESTAMP_CARRYBACK_N",
    "PRE_FILL_EXIT_N",
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _merge(bodies: list[dict[str, Any]]) -> dict[str, Any]:
    ctrl = _empty_arm()
    treat = _empty_arm()
    rows: list[dict[str, Any]] = []
    causes: list[dict[str, Any]] = []
    leftover_ok = True
    control_sot_ok = True
    treatment_sot_ok = True
    for body in bodies:
        _add_arm(ctrl, dict(body.get("control") or {}))
        _add_arm(treat, dict(body.get("treatment") or {}))
        rows.extend(list(body.get("rows") or []))
        causes.extend(list(body.get("incremental_causes") or []))
        leftover_ok = leftover_ok and bool(body.get("leftover_ok"))
        control_sot_ok = control_sot_ok and bool(body.get("control_sot_ok"))
        treatment_sot_ok = treatment_sot_ok and bool(body.get("treatment_sot_ok"))
    return {
        "control": ctrl,
        "treatment": treat,
        "rows": rows,
        "causes": causes,
        "leftover_ok": leftover_ok,
        "control_sot_ok": control_sot_ok,
        "treatment_sot_ok": treatment_sot_ok,
    }


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str, source_sha: str) -> int:
    decision = {
        "CASE": "F",
        "VERDICT": "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_INTEGRITY_FAILED",
        "NEXT": msg,
        "CANDIDATE_FROZEN": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "PRIMARY_NEXT_EXIT_TARGET": None,
        "first_eligible_prospective_date": None,
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "CANDIDATE_ID": CANDIDATE_ID,
            "VERDICT": decision["VERDICT"],
            "CANDIDATE_FROZEN": False,
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "CANDIDATE_SPEC_SHA256": sha,
            "SOURCE_SHA256": source_sha,
            "CANDIDATE_IDENTITY_HASH": candidate_identity_hash(spec_sha=sha, source_sha=source_sha),
            "PRIMARY_NEXT_EXIT_TARGET": None,
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
    sha = spec_sha256_candidate()
    source_sha = source_sha256_candidate()
    ident_hash = candidate_identity_hash(spec_sha=sha, source_sha=source_sha)
    pre = snapshot(phase="PRE")
    leak = {k: 0 for k in INTEGRITY_ZERO}
    leak.update({"SUBMIT_N": int(SUBMIT_N), "CANCEL_N": int(CANCEL_N), "LIVE_ORDER_N": int(LIVE_ORDER_N)})
    leak["NEW_EXIT_RULE_N"] = int(bool(NEW_EXIT_RULE))
    leak["THRESHOLD_SEARCH_N"] = int(bool(THRESHOLD_SEARCH))
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT candidate={CANDIDATE_ID} spec={sha[:12]} today={TODAY}", flush=True)

    if v1_spec_sha256() != PARENT_SPEC_SHA256_EXPECTED or not self_check().get("ok"):
        return _stop("STOP. Preflight failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if SESSION != "AM" or NEW_EXIT_RULE or THRESHOLD_SEARCH or bool(TRUE_OOS):
        return _stop("STOP. Forbidden flags.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if TODAY in set(ELIGIBLE_DAYS) or TODAY in set(LOCKED_SERIES_DAYS):
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if any(str(d) >= "20260903" for d in list(ELIGIBLE_DAYS) + list(LOCKED_SERIES_DAYS)):
        return _stop("STOP. Day list includes 20260903+.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    rca_req = dict(_load(ENTRY_ANCHORED_PULLBACK_RCA_OUT / "report.json").get("required") or {})
    rca_dec = dict(_load(ENTRY_ANCHORED_PULLBACK_RCA_OUT / "report.json").get("decision") or {})
    if str(rca_dec.get("VERDICT") or rca_req.get("VERDICT") or "") != SOURCE_RCA_VERDICT:
        return _stop("STOP. Prior floor-break RCA verdict mismatch.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if rca_req.get("PRIMARY_NEXT_EXIT_TARGET") is not None or rca_dec.get("PRIMARY_NEXT_EXIT_TARGET") is not None:
        return _stop("STOP. PRIMARY_NEXT_EXIT_TARGET must be None before this freeze run.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    leak["ACTIVE_CAPTURE_INPUT_N"] = input_active_file_n(
        [
            str(EXIT_RESIDUAL_RCA_OUT / "report.json"),
            str(ENTRY_ANCHORED_PULLBACK_RCA_OUT / "report.json"),
        ],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active capture input.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    try:
        residual_dev, residual_fwd, _residual_body = load_residual_rows()
    except (AssertionError, FileNotFoundError) as exc:
        return _stop(f"STOP. Residual SoT: {exc}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    harvested_ok = True
    dev_bodies: list[dict[str, Any]] = []
    fwd_bodies: list[dict[str, Any]] = []
    for day in list(ELIGIBLE_DAYS):
        if str(day) >= "20260903" or day == str(TODAY):
            return _stop(f"STOP. Forbidden day: {day}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
        body = harvest_day(day, cohort="DEVELOPMENT", spec_sha=sha, today=TODAY)
        if not body.get("ok"):
            harvested_ok = False
            print(f"harvest fail DEVELOPMENT {day}: {body.get('blocker')}", flush=True)
            return _stop(
                f"STOP. Harvest/identity fail DEVELOPMENT {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                sha=sha,
                source_sha=source_sha,
            )
        for k, v in dict(body.get("leak") or {}).items():
            leak[k] = int(leak.get(k, 0) or 0) + int(v or 0)
        dev_bodies.append(body)
    for day in list(LOCKED_SERIES_DAYS):
        if str(day) >= "20260903" or day == str(TODAY):
            return _stop(f"STOP. Forbidden FWD day: {day}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
        body = harvest_day(day, cohort="FORWARD_BURNED", spec_sha=sha, today=TODAY)
        if not body.get("ok"):
            harvested_ok = False
            print(f"harvest fail FORWARD_BURNED {day}: {body.get('blocker')}", flush=True)
            return _stop(
                f"STOP. Harvest/identity fail FORWARD_BURNED {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                sha=sha,
                source_sha=source_sha,
            )
        for k, v in dict(body.get("leak") or {}).items():
            leak[k] = int(leak.get(k, 0) or 0) + int(v or 0)
        fwd_bodies.append(body)

    carry = int(leak.get("FUTURE_QUOTE_CARRYBACK_N") or 0) + int(leak.get("FUTURE_TIMESTAMP_CARRYBACK_N") or 0) + int(leak.get("PRE_FILL_EXIT_N") or 0)
    if carry:
        return _stop("STOP. Future carryback or pre-fill EXIT != 0.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    dev_m = _merge(dev_bodies)
    fwd_m = _merge(fwd_bodies)
    ident_dev_preview = evaluate_cohort(
        cohort="DEVELOPMENT",
        days=list(ELIGIBLE_DAYS),
        harvest_rows=list(dev_m.get("rows") or []),
        ctrl_arm=dict(dev_m.get("control") or {}),
        treat_arm=dict(dev_m.get("treatment") or {}),
        residual=residual_index(residual_dev),
        causes=list(dev_m.get("causes") or []),
        leftover_ok=bool(dev_m.get("leftover_ok")),
        control_sot_ok=bool(dev_m.get("control_sot_ok")),
        treatment_sot_ok=bool(dev_m.get("treatment_sot_ok")),
    )
    if not bool(ident_dev_preview.get("identity", {}).get("ok")):
        return _stop(
            f"STOP. DEV CONTROL identity mismatch: {ident_dev_preview.get('identity')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
        )
    ident_fwd_preview = evaluate_cohort(
        cohort="FORWARD_BURNED",
        days=list(LOCKED_SERIES_DAYS),
        harvest_rows=list(fwd_m.get("rows") or []),
        ctrl_arm=dict(fwd_m.get("control") or {}),
        treat_arm=dict(fwd_m.get("treatment") or {}),
        residual=residual_index(residual_fwd),
        causes=list(fwd_m.get("causes") or []),
        leftover_ok=bool(fwd_m.get("leftover_ok")),
        control_sot_ok=bool(fwd_m.get("control_sot_ok")),
        treatment_sot_ok=bool(fwd_m.get("treatment_sot_ok")),
    )
    if not bool(ident_fwd_preview.get("identity", {}).get("ok")):
        return _stop(
            f"STOP. FWD CONTROL identity mismatch: {ident_fwd_preview.get('identity')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
        )

    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    decision = decide(ident_dev_preview, ident_fwd_preview, leak_ok=leak_ok, harvested_ok=harvested_ok)
    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    report = {
        "analysis_id": ANALYSIS_ID,
        "candidate_id": CANDIDATE_ID,
        "spec_sha256": sha,
        "source_sha256": source_sha,
        "candidate_identity_hash": ident_hash,
        "canonical_spec": canonical_candidate_spec(),
        "state_machine": STATE_MACHINE,
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "CANDIDATE_ID": CANDIDATE_ID,
            "VERDICT": decision.get("VERDICT"),
            "CASE": decision.get("CASE"),
            "CANDIDATE_FROZEN": decision.get("CANDIDATE_FROZEN"),
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "PRIMARY_NEXT_EXIT_TARGET": decision.get("PRIMARY_NEXT_EXIT_TARGET"),
            "CANDIDATE_SPEC_SHA256": sha,
            "SOURCE_SHA256": source_sha,
            "CANDIDATE_IDENTITY_HASH": ident_hash,
            "PARENT_SPEC_SHA256": PARENT_SPEC_SHA256_EXPECTED,
            "first_eligible_prospective_date": decision.get("first_eligible_prospective_date") or FIRST_PROSPECTIVE_DAY,
            "ENTRY_CHANGED": False,
            "SIZING_CHANGED": False,
            "THRESHOLD_SEARCH": False,
            "NEW_EXIT_RULE": False,
        },
        "decision": decision,
        "development": ident_dev_preview,
        "forward": ident_fwd_preview,
        "leak": leak,
        "preflight": pre,
        "postflight": post,
        "reporting_semantics": reporting,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))
    print(
        f"FINAL ANALYSIS_ID={ANALYSIS_ID} CASE={decision.get('CASE')} VERDICT={decision.get('VERDICT')} "
        f"FROZEN={decision.get('CANDIDATE_FROZEN')} DEV_TOTAL={ident_dev_preview.get('TOTAL_CAUSAL_DELTA')} "
        f"out={ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if harvested_ok and leak_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
