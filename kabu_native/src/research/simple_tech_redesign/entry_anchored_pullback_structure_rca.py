"""Offline entry-anchored pullback structure EXIT RCA runner."""
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

from research.am_entry_profit_improvement import CANCEL_N, LIVE_ORDER_N, SESSION, SUBMIT_N
from research.simple_tech_entry_family.indicators import self_check
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v13_analyze import reporting_semantics
from research.simple_tech_redesign.branch_u_bb_spec import PARENT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.entry_anchored_pullback_structure_rca_analyze import (
    assert_population_identity,
    assert_reference_integrity,
    cohort_pack,
    decide,
    merge_harvest,
    trade_audit_rows,
)
from research.simple_tech_redesign.entry_anchored_pullback_structure_rca_harvest import harvest_day
from research.simple_tech_redesign.entry_anchored_pullback_structure_rca_publish import (
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.simple_tech_redesign.entry_anchored_pullback_structure_rca_spec import (
    ANALYSIS_ID,
    NEW_EXIT_RULE,
    SOURCE_THESIS_VERDICT,
    THRESHOLD_SEARCH,
    canonical_anchored_spec,
    source_sha256_anchored,
    spec_sha256_anchored,
)
from research.simple_tech_redesign.exit_composition_shift_rca_analyze import load_residual_rows
from research.simple_tech_redesign.isolation import (
    ENTRY_ANCHORED_PULLBACK_RCA_OUT,
    ENTRY_THESIS_INVALIDATION_RCA_OUT,
    EXIT_RESIDUAL_RCA_OUT,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)

INTEGRITY_ZERO = ("SUBMIT_N", "CANCEL_N", "LIVE_ORDER_N", "NEW_EXIT_RULE_N", "THRESHOLD_SEARCH_N", "ACTIVE_CAPTURE_INPUT_N")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str, source_sha: str) -> int:
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": {"ANALYSIS_ID": ANALYSIS_ID, "VERDICT": "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_INVALID"},
        "decision": {"CASE": "D", "VERDICT": "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_INVALID", "NEXT": msg},
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))
    print(msg, flush=True)
    print("STOP.", flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    sha = spec_sha256_anchored()
    source_sha = source_sha256_anchored()
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

    thesis_req = dict(_load(ENTRY_THESIS_INVALIDATION_RCA_OUT / "report.json").get("required") or {})
    if str(thesis_req.get("VERDICT") or "") != SOURCE_THESIS_VERDICT:
        return _stop("STOP. Prior thesis-invalidation verdict mismatch.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if thesis_req.get("PRIMARY_NEXT_EXIT_TARGET") is not None:
        return _stop("STOP. PRIMARY_NEXT_EXIT_TARGET must be None.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    leak["ACTIVE_CAPTURE_INPUT_N"] = input_active_file_n(
        [
            str(EXIT_RESIDUAL_RCA_OUT / "report.json"),
            str(ENTRY_THESIS_INVALIDATION_RCA_OUT / "report.json"),
        ],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active capture input.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    try:
        dev_rows, fwd_rows, residual_body = load_residual_rows()
        integrity = assert_population_identity(dev_rows, fwd_rows)
    except (AssertionError, FileNotFoundError) as exc:
        return _stop(f"STOP. Identity: {exc}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    by_day: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for cohort, rows in (("development", dev_rows), ("forward", fwd_rows)):
        for r in rows:
            by_day[(cohort, str(r.get("date") or ""))].append(r)

    harvested: list[dict[str, Any]] = []
    harvested_ok = True
    for (cohort, day), rows in sorted(by_day.items()):
        if str(day) >= "20260903":
            return _stop(f"STOP. Forbidden day in occupancy SoT: {day}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
        body = harvest_day(day, cohort=cohort, rows=rows, spec_sha=sha, today=TODAY)
        if not body.get("ok"):
            harvested_ok = False
            print(f"harvest fail {cohort} {day}: {body.get('blocker')}", flush=True)
            continue
        harvested.extend(list(body.get("rows") or []))
        for k, v in dict(body.get("leak") or {}).items():
            leak[k] = int(leak.get(k, 0)) + int(v or 0)

    dev = merge_harvest(dev_rows, harvested)
    fwd = merge_harvest(fwd_rows, harvested)
    try:
        parity = assert_reference_integrity(dev + fwd)
        integrity.update(parity)
    except AssertionError as exc:
        return _stop(f"STOP. Signal-reference parity: {exc}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    carryback_n = int(leak.get("FUTURE_QUOTE_CARRYBACK_N") or 0) + int(leak.get("FUTURE_TIMESTAMP_CARRYBACK_N") or 0)
    if carryback_n:
        return _stop("STOP. Future carryback != 0.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    dev_pack = cohort_pack(dev, cohort="DEVELOPMENT")
    fwd_pack = cohort_pack(fwd, cohort="FORWARD_BURNED")
    decision = decide(dev_pack, fwd_pack, harvested_ok=harvested_ok, carryback_n=carryback_n)
    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    report = {
        "analysis_id": ANALYSIS_ID,
        "spec_sha256": sha,
        "source_sha256": source_sha,
        "canonical_spec": canonical_anchored_spec(),
        "integrity": integrity,
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": decision.get("VERDICT"),
            "PRIMARY_NEXT_EXIT_TARGET": None,
        },
        "decision": decision,
        "development": dev_pack,
        "forward": fwd_pack,
        "residual_source_verdict": dict(residual_body.get("required") or {}).get("VERDICT"),
        "source_thesis_verdict": SOURCE_THESIS_VERDICT,
        "trade_audit": trade_audit_rows(dev + fwd),
        "leak": leak,
        "preflight": pre,
        "postflight": post,
        "reporting_semantics": reporting,
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))
    print(f"VERDICT={decision.get('VERDICT')} CASE={decision.get('CASE')}", flush=True)
    print(f"out={ENTRY_ANCHORED_PULLBACK_RCA_OUT}", flush=True)
    print("STOP.", flush=True)
    return 0 if harvested_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
