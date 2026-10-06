"""Offline existing-data ENTRY quality mechanism V1. No prospective harvest. No candidate eval."""
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

from research.am_c0_indicator_exit.isolation import advanced
from research.am_entry_profit_improvement import CANCEL_N, LIVE_ORDER_N, SUBMIT_N
from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.indicators import self_check
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v13_analyze import reporting_semantics
from research.simple_tech_redesign.branch_u_bb_spec import PARENT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.isolation import (
    PRECAP_EXISTING_MECHANISM_V1_OUT,
    PRECAP_TIMING_RCA_OUT,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_analyze import decide, evaluate
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_harvest import assert_research_day, harvest_blocks
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_publish import (
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_spec import (
    ANALYSIS_ID,
    FORBIDDEN_INPUT_DAYS,
    FUTURE_DATA_USED,
    MAX_RESEARCH_DATE,
    MISSING_POLICY_FROZEN,
    NEXT_CANDIDATE_ID_IF_CASE_A,
    NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED,
    PARENT_VERDICT,
    PROSPECTIVE_HARVEST_SUSPENDED,
    THRESHOLD_POLICY,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    all_research_days,
    canonical_mechanism_spec,
    source_sha256_mechanism,
    spec_sha256_mechanism,
)

INTEGRITY_ZERO = (
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
    "RESEARCH_WRITE_PATH_OVERLAP_N",
    "ACTIVE_CAPTURE_INPUT_N",
    "INPUT_20260903_N",
    "INPUT_20260904_N",
    "THRESHOLD_SEARCH_N",
    "FUTURE_DATA_N",
    "PROSPECTIVE_HARVESTER_RUN_N",
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str, source_sha: str) -> int:
    decision = decide({"identity_ok": False, "features": []}, leak_ok=False)
    decision["NEXT"] = msg
    report = {
        "analysis_id": ANALYSIS_ID,
        "spec_sha256": sha,
        "source_sha256": source_sha,
        "blocker": msg,
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": decision["VERDICT"],
            "CASE": "D",
            "FUTURE_DATA_USED": False,
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "BURNED_EXISTING_DATA_ONLY": True,
            "NEXT_THRESHOLD_POLICY_FROZEN": THRESHOLD_POLICY,
            "MISSING_POLICY_FROZEN": MISSING_POLICY_FROZEN,
            "NEXT_CANDIDATE_ID_IF_CASE_A": NEXT_CANDIDATE_ID_IF_CASE_A,
            "NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED": False,
            "TRUE_OOS": False,
            "CERTIFIED": False,
        },
        "decision": decision,
        "evaluation": {},
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
    sha = spec_sha256_mechanism()
    source_sha = source_sha256_mechanism()
    pre = snapshot(phase="PRE")
    leak = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["THRESHOLD_SEARCH_N"] = int(bool(THRESHOLD_SEARCH))
    leak["FUTURE_DATA_N"] = int(bool(FUTURE_DATA_USED))
    leak["PROSPECTIVE_HARVESTER_RUN_N"] = 0
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(
        f"PREFLIGHT {ANALYSIS_ID} spec={sha[:12]} today={TODAY} MAX_RESEARCH_DATE={MAX_RESEARCH_DATE} PROSPECTIVE_HARVEST_SUSPENDED=true",
        flush=True,
    )
    if v1_spec_sha256() != PARENT_SPEC_SHA256_EXPECTED or not self_check().get("ok"):
        return _stop("STOP. Preflight failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if bool(TRUE_OOS) or bool(THRESHOLD_SEARCH) or (not PROSPECTIVE_HARVEST_SUSPENDED):
        return _stop("STOP. Forbidden flags.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    for day in all_research_days():
        bad = assert_research_day(day, today=TODAY)
        if bad:
            return _stop(f"STOP. {bad}:{day}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
        if str(day) in FORBIDDEN_INPUT_DAYS or str(day) > str(MAX_RESEARCH_DATE):
            leak["FUTURE_DATA_N"] = 1
            return _stop("STOP. Future/forbidden day in research list.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    parent = _load(PRECAP_TIMING_RCA_OUT / "report.json")
    parent_req = dict(parent.get("required") or {})
    parent_dec = dict(parent.get("decision") or {})
    parent_verdict = str(parent_req.get("VERDICT") or parent_dec.get("VERDICT") or "")
    if parent_verdict != PARENT_VERDICT:
        return _stop("STOP. Parent timing-RCA verdict mismatch.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    leak["ACTIVE_CAPTURE_INPUT_N"] = input_active_file_n(
        [str(PRECAP_TIMING_RCA_OUT / "report.json"), str(PRECAP_EXISTING_MECHANISM_V1_OUT / "report.json")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]) or int(leak["INPUT_20260903_N"]) or int(leak["INPUT_20260904_N"]):
        return _stop("STOP. Active or forbidden capture input.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    harvested = harvest_blocks(spec_sha=sha, today=TODAY)
    if not harvested.get("ok"):
        return _stop(
            f"STOP. Harvest failed {harvested.get('date')}: {harvested.get('blocker')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
        )
    pack = evaluate(dict(harvested.get("bodies") or {}))
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO)
    decision = decide(pack, leak_ok=leak_ok)
    post = snapshot(phase="POST")
    pid = advanced(pre, post)
    report = {
        "analysis_id": ANALYSIS_ID,
        "spec_sha256": sha,
        "source_sha256": source_sha,
        "canonical_spec": canonical_mechanism_spec(),
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": decision.get("VERDICT"),
            "CASE": decision.get("CASE"),
            "FUTURE_DATA_USED": False,
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "BURNED_EXISTING_DATA_ONLY": True,
            "NEXT_THRESHOLD_POLICY_FROZEN": THRESHOLD_POLICY,
            "MISSING_POLICY_FROZEN": MISSING_POLICY_FROZEN,
            "NEXT_CANDIDATE_ID_IF_CASE_A": decision.get("NEXT_CANDIDATE_ID_IF_CASE_A"),
            "NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED": False,
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "CANDIDATE_FROZEN": False,
            "PROSPECTIVE_ARMED": False,
            "NEW_ENTRY_FILTER": False,
            "NEW_EXIT_RULE": False,
            "CAP_CHANGED": False,
            "INDEPENDENT_ALPHA_FAMILY": False,
            "selected_feature": decision.get("selected_feature"),
        },
        "decision": decision,
        "evaluation": pack,
        "leak": leak,
        "preflight": pre,
        "postflight": post,
        "pid": pid,
        "reporting_semantics": reporting_semantics(pre, post),
        "submit_cancel_live": [int(SUBMIT_N), int(CANCEL_N), int(LIVE_ORDER_N)],
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))
    print(
        f"CASE {decision.get('CASE')} {decision.get('VERDICT')} selected={decision.get('selected_feature')} "
        f"out={PRECAP_EXISTING_MECHANISM_V1_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if leak_ok and pack.get("identity_ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
