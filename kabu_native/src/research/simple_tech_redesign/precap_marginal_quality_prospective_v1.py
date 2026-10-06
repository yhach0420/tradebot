"""Offline freeze of prospective marginal-quality observation V1. No live control. No new rule."""
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
    PRECAP_PROSPECTIVE_V1_OUT,
    PRECAP_TIMING_RCA_OUT,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.precap_marginal_quality_prospective_v1_analyze import decide, evaluate
from research.simple_tech_redesign.precap_marginal_quality_prospective_v1_harvest import discover_window, harvest_window
from research.simple_tech_redesign.precap_marginal_quality_prospective_v1_publish import (
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.simple_tech_redesign.precap_marginal_quality_prospective_v1_spec import (
    ANALYSIS_ID,
    CAP_CHANGED,
    ENTRY_CHANGED,
    FEATURE_DISCOVERY,
    FIRST_ELIGIBLE_DATE,
    FORBIDDEN_INPUT_DAYS,
    MATCHING_IN_PRIMARY_VERDICT,
    NEW_ENTRY_FILTER,
    NEW_EXIT_RULE,
    PARENT_VERDICT,
    THRESHOLD_SEARCH,
    TIME_FILTER,
    TRUE_OOS,
    WINDOW_DAYS,
    canonical_prospective_spec,
    source_sha256_prospective,
    spec_sha256_prospective,
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
    "FEATURE_DISCOVERY_N",
    "MATCHING_VERDICT_N",
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _slim(pack: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in pack.items() if k not in {"candidates", "admitted", "blocked"}}


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str, source_sha: str) -> int:
    decision = {
        "CASE": "E",
        "VERDICT": "SIMPLE_TECH_PRECAP_MARGINAL_QUALITY_PROSPECTIVE_INSUFFICIENT",
        "NEXT": msg,
        "INTRINSIC_MECHANISM_CONFIRMED": False,
        "CANDIDATE_FROZEN": False,
        "PROSPECTIVE_OBSERVATION_PROTOCOL_FROZEN": True,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "first_eligible_prospective_date": FIRST_ELIGIBLE_DATE,
        "prospective_armed": True,
        "NEW_ENTRY_FILTER": False,
        "CAP_CHANGED": False,
        "NEW_EXIT_RULE": False,
        "MATCHING_USED_IN_PRIMARY_VERDICT": False,
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "spec_sha256": sha,
        "source_sha256": source_sha,
        "blocker": msg,
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": decision["VERDICT"],
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "first_eligible_prospective_date": FIRST_ELIGIBLE_DATE,
            "prospective_armed": True,
            "CANDIDATE_FROZEN": False,
            "PROSPECTIVE_OBSERVATION_PROTOCOL_FROZEN": True,
            "INTRINSIC_MECHANISM_CONFIRMED": False,
            "NEW_ENTRY_FILTER": False,
            "CAP_CHANGED": False,
            "NEW_EXIT_RULE": False,
        },
        "decision": decision,
        "evaluation": {},
        "discovery": {},
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
    sha = spec_sha256_prospective()
    source_sha = source_sha256_prospective()
    pre = snapshot(phase="PRE")
    leak = {k: 0 for k in INTEGRITY_ZERO}
    leak.update({"SUBMIT_N": int(SUBMIT_N), "CANCEL_N": int(CANCEL_N), "LIVE_ORDER_N": int(LIVE_ORDER_N)})
    leak["NEW_EXIT_RULE_N"] = int(bool(NEW_EXIT_RULE))
    leak["THRESHOLD_SEARCH_N"] = int(bool(THRESHOLD_SEARCH))
    leak["ENTRY_CHANGE_N"] = int(bool(ENTRY_CHANGED or NEW_ENTRY_FILTER))
    leak["CAP_CHANGE_N"] = int(bool(CAP_CHANGED))
    leak["TIME_FILTER_N"] = int(bool(TIME_FILTER))
    leak["FEATURE_DISCOVERY_N"] = int(bool(FEATURE_DISCOVERY))
    leak["MATCHING_VERDICT_N"] = int(bool(MATCHING_IN_PRIMARY_VERDICT))
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT {ANALYSIS_ID} spec={sha[:12]} today={TODAY} freeze=20260904", flush=True)
    if v1_spec_sha256() != PARENT_SPEC_SHA256_EXPECTED or not self_check().get("ok"):
        return _stop("STOP. Preflight failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if SESSION != "AM" or NEW_EXIT_RULE or THRESHOLD_SEARCH or NEW_ENTRY_FILTER or CAP_CHANGED or TIME_FILTER or bool(TRUE_OOS):
        return _stop("STOP. Forbidden flags.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if FEATURE_DISCOVERY or MATCHING_IN_PRIMARY_VERDICT:
        return _stop("STOP. Feature discovery / matching verdict forbidden.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if any(str(d) in FORBIDDEN_INPUT_DAYS or str(d) >= "20260903" for d in list(ELIGIBLE_DAYS) + list(LOCKED_SERIES_DAYS)):
        return _stop("STOP. Burned day list includes forbidden dates.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    parent = _load(PRECAP_TIMING_RCA_OUT / "report.json")
    parent_req = dict(parent.get("required") or {})
    parent_dec = dict(parent.get("decision") or {})
    if str(parent_req.get("VERDICT") or parent_dec.get("VERDICT") or "") != PARENT_VERDICT:
        return _stop("STOP. Parent timing RCA verdict mismatch.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    leak["ACTIVE_CAPTURE_INPUT_N"] = input_active_file_n(
        [str(PRECAP_TIMING_RCA_OUT / "report.json")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active capture input.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    discovery = discover_window(today=TODAY)
    for rec in list(discovery.get("usable") or []):
        day = str(rec.get("date") or "")
        if day in FORBIDDEN_INPUT_DAYS or day == TODAY or day > TODAY:
            return _stop(f"STOP. Usable day not sealed-eligible: {day}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
        cap_path = str(rec.get("capture_path") or "").replace("\\", "/")
        if TODAY in cap_path:
            return _stop("STOP. Capture path includes today.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    print(
        f"WINDOW usable={discovery.get('completed_days')} excluded={[r.get('date')+':'+str(r.get('reason')) for r in list(discovery.get('excluded') or [])]}",
        flush=True,
    )
    bodies = harvest_window(discovery, spec_sha=sha, today=TODAY)
    for body in bodies:
        if not body.get("ok"):
            return _stop(f"STOP. Harvest {body.get('date')}: {body.get('blocker')}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
        print(
            f"{body.get('date')} exec={body.get('executable_n')} admitted={body.get('admitted_n')} hyp={body.get('hyp_n')} t3={body.get('t3_join_n')}",
            flush=True,
        )
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    pack = evaluate(bodies)
    decision = decide(pack, leak_ok=leak_ok, discovery=discovery)
    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    slim = _slim(pack)
    report = {
        "analysis_id": ANALYSIS_ID,
        "spec_sha256": sha,
        "source_sha256": source_sha,
        "canonical_spec": canonical_prospective_spec(),
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": decision.get("VERDICT"),
            "CASE": decision.get("CASE"),
            "OBSERVED_ECONOMIC_BOTTLENECK": "MARGINAL_ENTRY_QUALITY",
            "INTRINSIC_MECHANISM_CONFIRMED": False,
            "PRIMARY_MECHANISM_FROZEN": "OUTLIER_DOMINATED",
            "PROSPECTIVE_OBSERVATION_PROTOCOL_FROZEN": True,
            "CANDIDATE_FROZEN": False,
            "FAMILY_CLOSED": True,
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "first_eligible_prospective_date": FIRST_ELIGIBLE_DATE,
            "prospective_armed": True,
            "NEW_ENTRY_FILTER": False,
            "CAP_CHANGED": False,
            "NEW_EXIT_RULE": False,
            "TIME_FILTER": False,
            "MATCHING_USED_IN_PRIMARY_VERDICT": False,
        },
        "decision": decision,
        "evaluation": slim,
        "discovery": {
            "window_days": list(WINDOW_DAYS),
            "completed_days": list(discovery.get("completed_days") or []),
            "planned": list(discovery.get("planned") or []),
            "excluded": list(discovery.get("excluded") or []),
            "today": TODAY,
        },
        "leak": leak,
        "preflight": pre,
        "postflight": post,
        "reporting_semantics": reporting,
        "_markdown": "",
    }
    sheets_src = {**report, "evaluation": {**slim, "candidates": list(pack.get("candidates") or [])}}
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(sheets_src))
    print(
        f"FINAL {ANALYSIS_ID} CASE={decision.get('CASE')} VERDICT={decision.get('VERDICT')} "
        f"blocked_n={pack.get('blocked_n')} out={PRECAP_PROSPECTIVE_V1_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if leak_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
