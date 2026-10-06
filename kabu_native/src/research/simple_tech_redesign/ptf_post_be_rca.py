"""Offline PTF post-BE state transition RCA. Mechanism discovery only."""
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
    EXIT_COMPOSITION_SHIFT_RCA_OUT,
    PTF_POST_BE_RCA_OUT,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.ptf_post_be_rca_analyze import (
    assert_be_identity,
    be_rows_from_residual,
    cohort_pack,
    decide,
    merge_harvest,
    sequence_rows,
)
from research.simple_tech_redesign.ptf_post_be_rca_harvest import harvest_day_be
from research.simple_tech_redesign.ptf_post_be_rca_publish import build_markdown, build_sheets, write_artifacts
from research.simple_tech_redesign.ptf_post_be_rca_spec import (
    ANALYSIS_ID,
    NEW_EXIT_RULE,
    SOURCE_COMPOSITION_VERDICT,
    THRESHOLD_SEARCH,
    canonical_ptf_post_be_spec,
    source_sha256_ptf_post_be,
    spec_sha256_ptf_post_be,
)

INTEGRITY_ZERO = (
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
    "NEW_EXIT_RULE_N",
    "THRESHOLD_SEARCH_N",
    "ACTIVE_CAPTURE_INPUT_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str, source_sha: str) -> int:
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": {"ANALYSIS_ID": ANALYSIS_ID, "VERDICT": "SIMPLE_TECH_PTF_POST_BE_INVALID"},
        "decision": {"CASE": "E", "VERDICT": "SIMPLE_TECH_PTF_POST_BE_INVALID", "NEXT": msg},
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))
    print(msg, flush=True)
    print("STOP.", flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    spec = canonical_ptf_post_be_spec()
    sha = spec_sha256_ptf_post_be(spec)
    source_sha = source_sha256_ptf_post_be()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["NEW_EXIT_RULE_N"] = int(bool(NEW_EXIT_RULE))
    leak["THRESHOLD_SEARCH_N"] = int(bool(THRESHOLD_SEARCH))
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )

    if v1_spec_sha256() != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Parent spec SHA drifted.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if not self_check().get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if SESSION != "AM" or NEW_EXIT_RULE or THRESHOLD_SEARCH:
        return _stop("STOP. Forbidden flags.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    comp = _load(EXIT_COMPOSITION_SHIFT_RCA_OUT / "report.json")
    comp_req = dict(comp.get("required") or {})
    if str(comp_req.get("VERDICT") or "") != SOURCE_COMPOSITION_VERDICT:
        return _stop("STOP. Composition shift verdict mismatch.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if comp_req.get("PRIMARY_NEXT_EXIT_TARGET") is not None:
        return _stop("STOP. PRIMARY_NEXT_EXIT_TARGET must remain None.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(EXIT_COMPOSITION_SHIFT_RCA_OUT / "report.json")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])

    try:
        dev_be, fwd_be = be_rows_from_residual()
        integrity = assert_be_identity(dev_be, fwd_be)
    except (AssertionError, FileNotFoundError) as exc:
        return _stop(f"STOP. BE identity fail: {exc}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    dev_days = [str(d) for d in ELIGIBLE_DAYS]
    fwd_days = [str(d) for d in LOCKED_SERIES_DAYS]
    if TODAY in dev_days + fwd_days or any(d in FORBIDDEN_DAYS for d in dev_days + fwd_days):
        return _stop("STOP. Forbidden day in inputs.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    harvested: list[dict[str, Any]] = []
    harvested_ok = True
    by_day_dev: dict[str, list] = defaultdict(list)
    by_day_fwd: dict[str, list] = defaultdict(list)
    for r in dev_be:
        by_day_dev[str(r.get("date") or "")].append(r)
    for r in fwd_be:
        by_day_fwd[str(r.get("date") or "")].append(r)

    for day in dev_days:
        body = harvest_day_be(day, cohort="DEVELOPMENT", be_rows=by_day_dev.get(day) or [], spec_sha=sha, today=TODAY)
        if not body.get("ok"):
            harvested_ok = False
            break
        harvested.extend(list(body.get("rows") or []))
        for k, v in dict(body.get("leak") or {}).items():
            leak[f"DEV_{k}"] = int(leak.get(f"DEV_{k}") or 0) + int(v or 0)

    if harvested_ok:
        for day in fwd_days:
            body = harvest_day_be(day, cohort="FORWARD_BURNED", be_rows=by_day_fwd.get(day) or [], spec_sha=sha, today=TODAY)
            if not body.get("ok"):
                harvested_ok = False
                break
            harvested.extend(list(body.get("rows") or []))
            for k, v in dict(body.get("leak") or {}).items():
                leak[f"FWD_{k}"] = int(leak.get(f"FWD_{k}") or 0) + int(v or 0)

    dev_m = merge_harvest(dev_be, [r for r in harvested if str(r.get("date") or "") in set(dev_days)])
    fwd_m = merge_harvest(fwd_be, [r for r in harvested if str(r.get("date") or "") in set(fwd_days)])
    post_ok = all(r.get("post_be_ok") for r in dev_m + fwd_m)
    if not post_ok:
        harvested_ok = False

    dev_pack = cohort_pack(dev_m, cohort="DEVELOPMENT")
    fwd_pack = cohort_pack(fwd_m, cohort="FORWARD_BURNED")
    decision = decide(dev_pack, fwd_pack, harvested_ok=harvested_ok and post_ok)

    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    ni_ok = bool(
        all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO)
        and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
        and reporting.get("RUNTIME_PID_UNCHANGED")
        and reporting.get("CAPTURE_PID_UNCHANGED")
    )
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "SOURCE_COMPOSITION_VERDICT": SOURCE_COMPOSITION_VERDICT,
        "PRIMARY_NEXT_EXIT_TARGET": None,
        "NEW_EXIT_RULE": False,
        "CANDIDATE_FROZEN": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "DEV_BE_N": integrity.get("dev_be_n"),
        "FWD_BE_N": integrity.get("fwd_be_n"),
        "VERDICT": decision.get("VERDICT"),
        "CASE": decision.get("CASE"),
        "PRIMARY_NEXT_MECHANISM": decision.get("PRIMARY_NEXT_MECHANISM"),
        "PTF_POST_BE_RCA_SPEC_SHA256": sha,
        "SOURCE_SHA256": source_sha,
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "decision": decision,
        "development": dev_pack,
        "forward": fwd_pack,
        "integrity": integrity,
        "sequence_rows": sequence_rows(dev_m + fwd_m),
        "preflight": pre,
        "postflight": post,
        "reporting": reporting,
        "leak": leak,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))
    print(
        f"FINAL ANALYSIS_ID={ANALYSIS_ID} VERDICT={decision.get('VERDICT')} CASE={decision.get('CASE')} "
        f"mechanism={decision.get('PRIMARY_NEXT_MECHANISM')} out={PTF_POST_BE_RCA_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if ni_ok and decision.get("CASE") in {"A", "B", "C", "D", "E"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
