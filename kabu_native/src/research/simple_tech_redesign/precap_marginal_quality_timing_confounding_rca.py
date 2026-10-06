"""Offline pre-CAP timing confounding RCA. No Capture control. No new rule."""
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
from research.simple_tech_redesign.isolation import (
    PRECAP_TIMING_RCA_OUT,
    SLOT_RELEASE_MARGINAL_RCA_OUT,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.precap_marginal_quality_timing_confounding_rca_analyze import decide, evaluate_cohort
from research.simple_tech_redesign.precap_marginal_quality_timing_confounding_rca_harvest import harvest_day
from research.simple_tech_redesign.precap_marginal_quality_timing_confounding_rca_publish import (
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.simple_tech_redesign.precap_marginal_quality_timing_confounding_rca_spec import (
    ANALYSIS_ID,
    CAP_CHANGED,
    EARLIEST_POSSIBLE_IF_CASE_A,
    ENTRY_CHANGED,
    FWD_OUTLIER_DAY,
    FWD_OUTLIER_SYMBOL,
    NEW_ENTRY_FILTER,
    NEW_EXIT_RULE,
    PARENT_VERDICT,
    THRESHOLD_SEARCH,
    TIME_FILTER,
    TRUE_OOS,
    canonical_timing_spec,
    source_sha256_timing,
    spec_sha256_timing,
)

INTEGRITY_ZERO = (
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
    "NEW_EXIT_RULE_N",
    "THRESHOLD_SEARCH_N",
    "ACTIVE_CAPTURE_INPUT_N",
    "ENTRY_CHANGE_N",
    "CAP_CHANGE_N",
    "TIME_FILTER_N",
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _slim(pack: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in pack.items() if k not in {"candidates", "_match_primary"}}


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any]) -> int:
    decision = {
        "CASE": "G",
        "VERDICT": "SIMPLE_TECH_PRECAP_TIMING_RCA_INTEGRITY_FAILED",
        "NEXT": msg,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "first_eligible_prospective_date": None,
        "earliest_possible_if_case_A": EARLIEST_POSSIBLE_IF_CASE_A,
        "prospective_armed": False,
        "NEW_ENTRY_FILTER": False,
        "CAP_CHANGED": False,
        "NEW_EXIT_RULE": False,
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": decision["VERDICT"],
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "first_eligible_prospective_date": None,
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
    sha = spec_sha256_timing()
    source_sha = source_sha256_timing()
    pre = snapshot(phase="PRE")
    leak = {k: 0 for k in INTEGRITY_ZERO}
    leak.update({"SUBMIT_N": int(SUBMIT_N), "CANCEL_N": int(CANCEL_N), "LIVE_ORDER_N": int(LIVE_ORDER_N)})
    leak["NEW_EXIT_RULE_N"] = int(bool(NEW_EXIT_RULE))
    leak["THRESHOLD_SEARCH_N"] = int(bool(THRESHOLD_SEARCH))
    leak["ENTRY_CHANGE_N"] = int(bool(ENTRY_CHANGED or NEW_ENTRY_FILTER))
    leak["CAP_CHANGE_N"] = int(bool(CAP_CHANGED))
    leak["TIME_FILTER_N"] = int(bool(TIME_FILTER))
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or ""))
    print(f"PREFLIGHT {ANALYSIS_ID} spec={sha[:12]} today={TODAY}", flush=True)
    if v1_spec_sha256() != PARENT_SPEC_SHA256_EXPECTED or not self_check().get("ok"):
        return _stop("STOP. Preflight failed.", pre=pre, leak=leak)
    if SESSION != "AM" or NEW_EXIT_RULE or THRESHOLD_SEARCH or NEW_ENTRY_FILTER or CAP_CHANGED or TIME_FILTER or bool(TRUE_OOS):
        return _stop("STOP. Forbidden flags.", pre=pre, leak=leak)
    if any(str(d) >= "20260903" for d in list(ELIGIBLE_DAYS) + list(LOCKED_SERIES_DAYS)):
        return _stop("STOP. Day list includes 20260903+.", pre=pre, leak=leak)
    if TODAY in set(ELIGIBLE_DAYS) or TODAY in set(LOCKED_SERIES_DAYS):
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak)
    parent = _load(SLOT_RELEASE_MARGINAL_RCA_OUT / "report.json")
    parent_req = dict(parent.get("required") or {})
    parent_dec = dict(parent.get("decision") or {})
    if str(parent_req.get("VERDICT") or parent_dec.get("VERDICT") or "") != PARENT_VERDICT:
        return _stop("STOP. Parent marginal-quality RCA verdict mismatch.", pre=pre, leak=leak)
    if str(parent_req.get("PRIMARY_CAUSAL_BOTTLENECK") or parent_dec.get("PRIMARY_CAUSAL_BOTTLENECK") or "") != "MARGINAL_ENTRY_QUALITY":
        return _stop("STOP. Observed economic bottleneck mismatch.", pre=pre, leak=leak)
    leak["ACTIVE_CAPTURE_INPUT_N"] = input_active_file_n(
        [str(SLOT_RELEASE_MARGINAL_RCA_OUT / "report.json")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active capture input.", pre=pre, leak=leak)
    dev_bodies: list[dict[str, Any]] = []
    fwd_bodies: list[dict[str, Any]] = []
    for day in list(ELIGIBLE_DAYS):
        body = harvest_day(day, cohort="DEVELOPMENT", today=TODAY)
        if not body.get("ok"):
            return _stop(f"STOP. Harvest DEVELOPMENT {day}: {body.get('blocker')}.", pre=pre, leak=leak)
        print(f"DEVELOPMENT {day} exec={body.get('executable_n')} admitted={body.get('admitted_n')} hyp={body.get('hyp_n')} v22={body.get('v22_join_n')}", flush=True)
        dev_bodies.append(body)
    for day in list(LOCKED_SERIES_DAYS):
        body = harvest_day(day, cohort="FORWARD_BURNED", today=TODAY)
        if not body.get("ok"):
            return _stop(f"STOP. Harvest FORWARD_BURNED {day}: {body.get('blocker')}.", pre=pre, leak=leak)
        print(f"FORWARD_BURNED {day} exec={body.get('executable_n')} admitted={body.get('admitted_n')} hyp={body.get('hyp_n')} v22={body.get('v22_join_n')}", flush=True)
        fwd_bodies.append(body)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    dev_full = evaluate_cohort(dev_bodies, cohort="DEVELOPMENT")
    fwd_full = evaluate_cohort(fwd_bodies, cohort="FORWARD_BURNED")
    if not bool(dev_full.get("identity", {}).get("ok")) or not bool(fwd_full.get("identity", {}).get("ok")):
        return _stop(f"STOP. Identity mismatch DEV={dev_full.get('identity')} FWD={fwd_full.get('identity')}.", pre=pre, leak=leak)
    fwd_ex_bodies = []
    for body in fwd_bodies:
        rec = dict(body)
        rec["candidates"] = [
            c
            for c in list(body.get("candidates") or [])
            if not (str(c.get("date") or "") == FWD_OUTLIER_DAY and str(c.get("symbol") or "") == FWD_OUTLIER_SYMBOL)
        ]
        fwd_ex_bodies.append(rec)
    fwd_ex = evaluate_cohort(fwd_ex_bodies, cohort="FORWARD_BURNED", enforce_identity=False)
    decision = decide(dev_full, fwd_full, fwd_ex, leak_ok=leak_ok)
    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    report = {
        "analysis_id": ANALYSIS_ID,
        "spec_sha256": sha,
        "source_sha256": source_sha,
        "canonical_spec": canonical_timing_spec(),
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": decision.get("VERDICT"),
            "CASE": decision.get("CASE"),
            "PRIMARY_MECHANISM": decision.get("PRIMARY_MECHANISM"),
            "OBSERVED_ECONOMIC_BOTTLENECK": "MARGINAL_ENTRY_QUALITY",
            "INTRINSIC_MECHANISM_CONFIRMED": decision.get("INTRINSIC_MECHANISM_CONFIRMED"),
            "FAMILY_CLOSED": True,
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "first_eligible_prospective_date": None,
            "earliest_possible_if_case_A": EARLIEST_POSSIBLE_IF_CASE_A,
            "prospective_armed": False,
            "NEW_ENTRY_FILTER": False,
            "CAP_CHANGED": False,
            "NEW_EXIT_RULE": False,
            "TIME_FILTER": False,
        },
        "decision": decision,
        "development": _slim(dev_full),
        "forward": _slim(fwd_full),
        "forward_ex_285A": _slim(fwd_ex),
        "leak": leak,
        "preflight": pre,
        "postflight": post,
        "reporting_semantics": reporting,
        "_markdown": "",
    }
    sheets_src = {
        **report,
        "development": {**_slim(dev_full), "candidates": list(dev_full.get("candidates") or [])},
        "forward": {**_slim(fwd_full), "candidates": list(fwd_full.get("candidates") or [])},
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(sheets_src))
    print(
        f"FINAL {ANALYSIS_ID} CASE={decision.get('CASE')} VERDICT={decision.get('VERDICT')} MECH={decision.get('PRIMARY_MECHANISM')} out={PRECAP_TIMING_RCA_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if leak_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
