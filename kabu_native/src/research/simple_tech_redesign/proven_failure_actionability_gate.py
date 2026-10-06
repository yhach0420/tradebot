"""Offline proven failure causal actionability gate."""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
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
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.causal_board_rca_spec import FORBIDDEN_DAYS
from research.simple_tech_redesign.isolation import (
    PROVEN_FAILURE_ACTIONABILITY_OUT,
    PTF_POST_BE_RCA_OUT,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.proven_failure_actionability_analyze import (
    assert_identity,
    be_rows_from_residual,
    cohort_pack,
    decide,
    merge_harvest,
    sequence_audit_rows,
)
from research.simple_tech_redesign.proven_failure_actionability_gate_spec import (
    ANALYSIS_ID,
    NEW_EXIT_RULE,
    SOURCE_PTF_VERDICT,
    THRESHOLD_SEARCH,
    canonical_actionability_spec,
    source_sha256_actionability,
    spec_sha256_actionability,
)
from research.simple_tech_redesign.proven_failure_actionability_harvest import harvest_day_actionability
from research.simple_tech_redesign.proven_failure_actionability_publish import build_markdown, build_sheets, write_artifacts

INTEGRITY_ZERO = ("SUBMIT_N", "CANCEL_N", "LIVE_ORDER_N", "NEW_EXIT_RULE_N", "THRESHOLD_SEARCH_N", "ACTIVE_CAPTURE_INPUT_N")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str, source_sha: str) -> int:
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": {"ANALYSIS_ID": ANALYSIS_ID, "VERDICT": "SIMPLE_TECH_PROVEN_FAILURE_ACTIONABILITY_INVALID"},
        "decision": {"CASE": "D", "VERDICT": "SIMPLE_TECH_PROVEN_FAILURE_ACTIONABILITY_INVALID", "NEXT": msg},
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))
    print(msg, flush=True)
    print("STOP.", flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    sha = spec_sha256_actionability()
    source_sha = source_sha256_actionability()
    pre = snapshot(phase="PRE")
    leak = {k: 0 for k in INTEGRITY_ZERO}
    leak.update({"SUBMIT_N": int(SUBMIT_N), "CANCEL_N": int(CANCEL_N), "LIVE_ORDER_N": int(LIVE_ORDER_N)})
    leak["NEW_EXIT_RULE_N"] = int(bool(NEW_EXIT_RULE))
    leak["THRESHOLD_SEARCH_N"] = int(bool(THRESHOLD_SEARCH))
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )

    if v1_spec_sha256() != PARENT_SPEC_SHA256_EXPECTED or not self_check().get("ok"):
        return _stop("STOP. Preflight failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if SESSION != "AM" or NEW_EXIT_RULE or THRESHOLD_SEARCH:
        return _stop("STOP. Forbidden flags.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    ptf_req = dict(_load(PTF_POST_BE_RCA_OUT / "report.json").get("required") or {})
    if str(ptf_req.get("VERDICT") or "") != SOURCE_PTF_VERDICT:
        return _stop("STOP. PTF post-BE verdict mismatch.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if ptf_req.get("PRIMARY_NEXT_EXIT_TARGET") is not None:
        return _stop("STOP. PRIMARY_NEXT_EXIT_TARGET must be None.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    leak["ACTIVE_CAPTURE_INPUT_N"] = input_active_file_n(
        [str(PTF_POST_BE_RCA_OUT / "report.json")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active capture input.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    try:
        dev_be, fwd_be = be_rows_from_residual()
        integrity = assert_identity(dev_be, fwd_be)
    except (AssertionError, FileNotFoundError) as exc:
        return _stop(f"STOP. Identity: {exc}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    dev_days, fwd_days = [str(d) for d in ELIGIBLE_DAYS], [str(d) for d in LOCKED_SERIES_DAYS]
    by_day_dev: dict[str, list] = defaultdict(list)
    by_day_fwd: dict[str, list] = defaultdict(list)
    for r in dev_be:
        by_day_dev[str(r.get("date") or "")].append(r)
    for r in fwd_be:
        by_day_fwd[str(r.get("date") or "")].append(r)

    harvested: list[dict[str, Any]] = []
    ok = True
    for day in dev_days:
        b = harvest_day_actionability(day, cohort="DEVELOPMENT", be_rows=by_day_dev.get(day) or [], spec_sha=sha, today=TODAY)
        if not b.get("ok"):
            ok = False
            break
        harvested.extend(list(b.get("rows") or []))
    if ok:
        for day in fwd_days:
            b = harvest_day_actionability(day, cohort="FORWARD_BURNED", be_rows=by_day_fwd.get(day) or [], spec_sha=sha, today=TODAY)
            if not b.get("ok"):
                ok = False
                break
            harvested.extend(list(b.get("rows") or []))

    dev_m = merge_harvest(dev_be, [r for r in harvested if str(r.get("date") or "") in set(dev_days)])
    fwd_m = merge_harvest(fwd_be, [r for r in harvested if str(r.get("date") or "") in set(fwd_days)])
    post_ok = ok and all(r.get("actionability_ok") for r in dev_m + fwd_m)

    dev_pack = cohort_pack(dev_m, cohort="DEVELOPMENT")
    fwd_pack = cohort_pack(fwd_m, cohort="FORWARD_BURNED")
    decision = decide(dev_pack, fwd_pack, harvested_ok=post_ok)

    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    ni_ok = bool(all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and reporting.get("RUNTIME_PID_UNCHANGED"))

    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "SOURCE_PTF_VERDICT": SOURCE_PTF_VERDICT,
        "PRIMARY_NEXT_EXIT_TARGET": None,
        "NEW_EXIT_RULE": False,
        "CANDIDATE_FROZEN": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "DEV_BE_N": integrity["dev_be_n"],
        "FWD_BE_N": integrity["fwd_be_n"],
        "VERDICT": decision["VERDICT"],
        "CASE": decision["CASE"],
        "PRIMARY_NEXT_MECHANISM": decision.get("PRIMARY_NEXT_MECHANISM"),
        "ACTIONABILITY_GATE_SPEC_SHA256": sha,
        "SOURCE_SHA256": source_sha,
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "decision": decision,
        "development": dev_pack,
        "forward": fwd_pack,
        "integrity": integrity,
        "sequence_rows": sequence_audit_rows(dev_m + fwd_m),
        "preflight": pre,
        "postflight": post,
        "reporting": reporting,
        "leak": leak,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))
    print(
        f"FINAL {ANALYSIS_ID} VERDICT={decision['VERDICT']} CASE={decision['CASE']} "
        f"mechanism={decision.get('PRIMARY_NEXT_MECHANISM')} out={PROVEN_FAILURE_ACTIONABILITY_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if ni_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
