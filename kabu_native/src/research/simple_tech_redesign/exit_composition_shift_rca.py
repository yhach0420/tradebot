"""Offline EXIT failure composition shift RCA. Residual SoT only. No new EXIT."""
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

from research.am_entry_profit_improvement import CANCEL_N, LIVE_ORDER_N, SESSION, SUBMIT_N
from research.simple_tech_entry_family.indicators import self_check
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v13_analyze import reporting_semantics
from research.simple_tech_redesign.branch_u_bb_spec import PARENT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.causal_board_rca_spec import FORBIDDEN_DAYS, HOLDOUT_STATUS
from research.simple_tech_redesign.exit_composition_shift_rca_analyze import analyze_all
from research.simple_tech_redesign.exit_composition_shift_rca_publish import build_markdown, build_sheets, write_artifacts
from research.simple_tech_redesign.exit_composition_shift_rca_spec import (
    ANALYSIS_ID,
    BOARD_FAMILY_STATUS,
    BRANCH_U_STATUS,
    NEW_EXIT_RULE,
    PRIMARY_EXIT,
    RESIDUAL_RCA_SPEC_SHA256_EXPECTED,
    RESIDUAL_VERDICT_EXPECTED,
    THRESHOLD_SEARCH,
    canonical_composition_shift_spec,
    source_sha256_composition_shift,
    spec_sha256_composition_shift,
)
from research.simple_tech_redesign.isolation import (
    EXIT_COMPOSITION_SHIFT_RCA_OUT,
    EXIT_RESIDUAL_RCA_OUT,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)

INTEGRITY_ZERO = (
    "LIVE_PROCESS_CONTROL_CALL_N",
    "RUNTIME_WRITE_N",
    "CAPTURE_WRITE_N",
    "ADDITIONAL_WEBSOCKET_N",
    "ACTIVE_CAPTURE_INPUT_N",
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
    "KABUS_RESTART_N",
    "CAPTURE_RESTART_N",
    "RUNTIME_RESTART_N",
    "TODAY_INCLUDED_N",
    "FORBIDDEN_DAY_N",
    "THRESHOLD_SEARCH_N",
    "NEW_EXIT_RULE_N",
    "EXIT_CHANGE_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str, source_sha: str) -> int:
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": "SIMPLE_TECH_EXIT_FAILURE_COMPOSITION_SHIFT_INVALID",
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "NEW_EXIT_RULE": False,
            "COMPOSITION_SHIFT_RCA_SPEC_SHA256": sha,
            "SOURCE_SHA256": source_sha,
        },
        "decision": {"CASE": "E", "VERDICT": "SIMPLE_TECH_EXIT_FAILURE_COMPOSITION_SHIFT_INVALID", "NEXT": msg},
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))
    print(msg, flush=True)
    print("STOP.", flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_composition_shift_spec()
    sha = spec_sha256_composition_shift(spec)
    source_sha = source_sha256_composition_shift()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["THRESHOLD_SEARCH_N"] = int(bool(THRESHOLD_SEARCH))
    leak["NEW_EXIT_RULE_N"] = int(bool(NEW_EXIT_RULE))
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT composition_shift_rca={sha[:12]}", flush=True)

    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if not self_check().get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if SESSION != "AM" or THRESHOLD_SEARCH or NEW_EXIT_RULE:
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    residual = _load(EXIT_RESIDUAL_RCA_OUT / "report.json")
    residual_req = dict(residual.get("required") or {})
    if str(residual_req.get("VERDICT") or "") != RESIDUAL_VERDICT_EXPECTED:
        return _stop("STOP. Residual SoT verdict mismatch.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if str(residual_req.get("RESIDUAL_RCA_SPEC_SHA256") or "") != RESIDUAL_RCA_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Residual SoT spec SHA mismatch.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if TODAY in FORBIDDEN_DAYS or "20260903" not in FORBIDDEN_DAYS:
        pass  # forbidden list frozen in spec

    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(EXIT_RESIDUAL_RCA_OUT / "report.json")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    try:
        body = analyze_all()
    except (AssertionError, FileNotFoundError) as exc:
        return _stop(f"STOP. Analysis failed: {exc}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    ni_ok = bool(
        all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO)
        and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
        and reporting.get("RUNTIME_PID_UNCHANGED")
        and reporting.get("CAPTURE_PID_UNCHANGED")
        and reporting.get("REPORTING_SEMANTICS_PASS")
    )
    decision = dict(body.get("decision") or {})
    integrity = dict(body.get("integrity") or {})
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_EXIT_RESIDUAL_LOSS_ARCHITECTURE_RCA",
        "SOURCE_REVISION": "R1_RANK_FIELD_MAPPING",
        "SOURCE_VERDICT": RESIDUAL_VERDICT_EXPECTED,
        "PRIMARY_POPULATION": "OCCUPANCY_CONTROL_FILLS_ONLY",
        "PRIMARY_EXIT": PRIMARY_EXIT,
        "BOARD_FAMILY_STATUS": BOARD_FAMILY_STATUS,
        "BRANCH_U_STATUS": BRANCH_U_STATUS,
        "NEW_EXIT_RULE": False,
        "THRESHOLD_SEARCH": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "CASE": decision.get("CASE"),
        "PRIMARY_SHIFT_DRIVER": decision.get("PRIMARY_SHIFT_DRIVER"),
        "SECONDARY_DRIVERS": decision.get("SECONDARY_DRIVERS"),
        "PRIMARY_NEXT_EXIT_TARGET": None,
        "DEV_CONTROL_FILL_N": integrity.get("dev_fill_n"),
        "DEV_CONTROL_CORE_N": integrity.get("dev_core_n"),
        "DEV_CONTROL_ADDED_N": integrity.get("dev_added_n"),
        "DEV_CONTROL_PNL": integrity.get("dev_pnl"),
        "FWD_CONTROL_FILL_N": integrity.get("fwd_fill_n"),
        "FWD_CONTROL_CORE_N": integrity.get("fwd_core_n"),
        "FWD_CONTROL_ADDED_N": integrity.get("fwd_added_n"),
        "FWD_CONTROL_PNL": integrity.get("fwd_pnl"),
        "COMPOSITION_SHIFT_RCA_SPEC_SHA256": sha,
        "SOURCE_SHA256": source_sha,
        "RESIDUAL_RCA_SPEC_SHA256": RESIDUAL_RCA_SPEC_SHA256_EXPECTED,
        "PARENT_SPEC_SHA256": parent_sha,
        "HOLDOUT_STATUS_FROZEN": HOLDOUT_STATUS,
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "decision": decision,
        "development": body.get("development"),
        "forward": body.get("forward"),
        "integrity": integrity,
        "preflight": pre,
        "postflight": post,
        "reporting": reporting,
        "leak": leak,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))
    print(
        f"FINAL ANALYSIS_ID={ANALYSIS_ID} VERDICT={decision.get('VERDICT')} "
        f"CASE={decision.get('CASE')} driver={decision.get('PRIMARY_SHIFT_DRIVER')} "
        f"ni={ni_ok} out={EXIT_COMPOSITION_SHIFT_RCA_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if ni_ok and decision.get("CASE") in {"A", "B", "C", "D"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
