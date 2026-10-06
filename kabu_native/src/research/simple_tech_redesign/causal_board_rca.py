"""Offline SIMPLE_TECH causal board / Pre-CAP RCA. No policy. No 20260903. No live control."""
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
from research.simple_tech_redesign.branch_u_bb_spec import (
    BRANCH_P_TECHNICAL_EXIT,
    LIFECYCLE_SPEC_SHA256_EXPECTED,
    LIFECYCLE_VERDICT_EXPECTED,
    PARENT_SPEC_SHA256_EXPECTED,
    TESTED_BRANCH_U_EXIT_MECHANISMS,
)
from research.simple_tech_redesign.branch_u_causal_spec import BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED, BRANCH_U_ONE_SHOT_VERDICT_EXPECTED
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.branch_u_holdout_publish import terminate_holdout_series
from research.simple_tech_redesign.branch_u_holdout_spec import CAUSAL_SPEC_SHA256_EXPECTED, CAUSAL_VERDICT_EXPECTED
from research.simple_tech_redesign.causal_board_rca_analyze import answers, cohort_pack, decide, field_availability
from research.simple_tech_redesign.causal_board_rca_harvest import harvest_days, merge_inventory
from research.simple_tech_redesign.causal_board_rca_publish import build_markdown, build_sheets, write_artifacts
from research.simple_tech_redesign.causal_board_rca_spec import (
    ANALYSIS_ID,
    BOARD_OK_REUSED,
    CAP_ROLE,
    EVOLUTION_WINDOW_SEC,
    EVOLUTION_WINDOW_SOURCE,
    FORBIDDEN_DAYS,
    HOLDOUT_STATUS,
    HOLDOUT_TERMINATION_REASON,
    HOLDOUT_VERDICT_KEPT,
    POLICY_CREATED,
    PRIMARY_QUESTION,
    SECONDARY_QUESTION,
    THRESHOLD_SEARCH,
    canonical_board_rca_spec,
    spec_sha256_board_rca,
)
from research.simple_tech_redesign.exit_lifecycle_spec import spec_sha256_lifecycle
from research.simple_tech_redesign.isolation import (
    BRANCH_U_CAUSAL_OUT,
    BRANCH_U_HOLDOUT_OUT,
    BRANCH_U_OUT,
    CAUSAL_BOARD_RCA_OUT,
    LIFECYCLE_OUT,
    TODAY,
    V22_OUT,
    V26_OUT,
    V27_OUT,
    V28_OUT,
    V29_OUT,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.v29_spec import (
    V26_SPEC_SHA256_FROZEN,
    V26_VERDICT_FROZEN,
    V27_PRIMARY_EXPECTED,
    V27_SPEC_SHA256_FROZEN,
    V27_VERDICT_FROZEN,
    V28_SPEC_SHA256_EXPECTED,
    V28_VERDICT_EXPECTED,
)
from research.simple_tech_redesign.exit_lifecycle_spec import V29_SPEC_SHA256_EXPECTED, V29_VERDICT_EXPECTED

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
    "COMBINATION_SEARCH_N",
    "PNL_WINDOW_SEARCH_N",
    "BOARD_OK_REUSED_N",
    "POLICY_CREATED_N",
    "CAP_CHANGE_N",
    "SIZING_CHANGE_N",
    "ENTRY_CHANGE_N",
    "EXIT_CHANGE_N",
    "V22_WRITE_N",
    "V26_WRITE_N",
    "V27_WRITE_N",
    "V28_WRITE_N",
    "V29_WRITE_N",
    "LIFECYCLE_WRITE_N",
    "BRANCH_U_WRITE_N",
    "BRANCH_U_CAUSAL_WRITE_N",
)

FROZEN_OFFICIAL = (
    V26_OUT / "report.json",
    V27_OUT / "report.json",
    V28_OUT / "report.json",
    V29_OUT / "report.json",
    LIFECYCLE_OUT / "report.json",
    BRANCH_U_OUT / "report.json",
    BRANCH_U_CAUSAL_OUT / "report.json",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _mtimes(paths: tuple[Path, ...]) -> dict[str, float]:
    return {str(p): float(p.stat().st_mtime) for p in paths if p.is_file()}


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str) -> int:
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "VERDICT": "SIMPLE_TECH_BOARD_RCA_INVALID",
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "NEXT": msg,
        "BOARD_RCA_SPEC_SHA256": sha,
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": req,
        "preflight": pre,
        "leak": leak,
        "decision": {"CASE": "E", "VERDICT": "SIMPLE_TECH_BOARD_RCA_INVALID", "NEXT": msg},
        "_markdown": build_markdown({"required": req, "decision": {"CASE": "E"}}),
    }
    write_artifacts(report, build_sheets(report, leak, {}))
    print(msg, flush=True)
    print("STOP.", flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_board_rca_spec()
    sha = spec_sha256_board_rca(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    frozen_mtime = _mtimes(FROZEN_OFFICIAL)
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} board_rca={sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, sha=sha)
    if spec_sha256_lifecycle() != LIFECYCLE_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Lifecycle spec SHA drifted.", pre=pre, leak=leak, sha=sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, sha=sha)
    if SESSION != "AM" or THRESHOLD_SEARCH or BOARD_OK_REUSED or POLICY_CREATED:
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, sha=sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, sha=sha)

    v26_req = dict(_load(V26_OUT / "report.json").get("required") or {})
    if str(v26_req.get("VERDICT") or "") != V26_VERDICT_FROZEN or str(v26_req.get("V26_SPEC_SHA256") or "") != V26_SPEC_SHA256_FROZEN:
        return _stop("STOP. V26 official freeze failed.", pre=pre, leak=leak, sha=sha)
    v27_req = dict(_load(V27_OUT / "report.json").get("required") or {})
    if str(v27_req.get("VERDICT") or "") != V27_VERDICT_FROZEN or str(v27_req.get("V27_SPEC_SHA256") or "") != V27_SPEC_SHA256_FROZEN:
        return _stop("STOP. V27 official freeze failed.", pre=pre, leak=leak, sha=sha)
    prim = dict(v27_req.get("PRIMARY_EXIT_STATE_MECHANISM") or {})
    if str(prim.get("name") or "") != V27_PRIMARY_EXPECTED:
        return _stop("STOP. V27 primary mechanism freeze failed.", pre=pre, leak=leak, sha=sha)
    v28_req = dict(_load(V28_OUT / "report.json").get("required") or {})
    if str(v28_req.get("VERDICT") or "") != V28_VERDICT_EXPECTED or str(v28_req.get("V28_SPEC_SHA256") or "") != V28_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V28 official freeze failed.", pre=pre, leak=leak, sha=sha)
    v29_req = dict(_load(V29_OUT / "report.json").get("required") or {})
    if str(v29_req.get("VERDICT") or "") != V29_VERDICT_EXPECTED or str(v29_req.get("V29_SPEC_SHA256") or "") != V29_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V29 official freeze failed.", pre=pre, leak=leak, sha=sha)
    life_req = dict(_load(LIFECYCLE_OUT / "report.json").get("required") or {})
    if str(life_req.get("VERDICT") or "") != LIFECYCLE_VERDICT_EXPECTED or str(life_req.get("LIFECYCLE_SPEC_SHA256") or "") != LIFECYCLE_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Lifecycle official freeze failed.", pre=pre, leak=leak, sha=sha)
    u_req = dict(_load(BRANCH_U_OUT / "report.json").get("required") or {})
    if str(u_req.get("VERDICT") or "") != BRANCH_U_ONE_SHOT_VERDICT_EXPECTED:
        return _stop("STOP. Branch U one-shot official freeze failed.", pre=pre, leak=leak, sha=sha)
    if str(u_req.get("BRANCH_U_SPEC_SHA256") or "") != BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Branch U one-shot spec SHA drifted.", pre=pre, leak=leak, sha=sha)
    causal_req = dict(_load(BRANCH_U_CAUSAL_OUT / "report.json").get("required") or {})
    if str(causal_req.get("VERDICT") or "") != CAUSAL_VERDICT_EXPECTED:
        return _stop("STOP. Branch U causal official freeze failed.", pre=pre, leak=leak, sha=sha)
    if str(causal_req.get("CAUSAL_SPEC_SHA256") or "") != CAUSAL_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Branch U causal spec SHA drifted.", pre=pre, leak=leak, sha=sha)
    hold_req = dict(_load(BRANCH_U_HOLDOUT_OUT / "report.json").get("required") or {})
    if str(hold_req.get("VERDICT") or "") != HOLDOUT_VERDICT_KEPT:
        return _stop("STOP. Holdout formal verdict is not ACCUMULATING.", pre=pre, leak=leak, sha=sha)

    term = terminate_holdout_series()
    if not term.get("ok"):
        return _stop(f"STOP. Holdout termination failed: {term.get('blocker')}.", pre=pre, leak=leak, sha=sha)

    dev_days = [str(d) for d in ELIGIBLE_DAYS]
    fwd_days = [str(d) for d in LOCKED_SERIES_DAYS]
    use_days = dev_days + fwd_days
    if TODAY in set(use_days) or any(d in FORBIDDEN_DAYS for d in use_days):
        leak["TODAY_INCLUDED_N"] = 1
        leak["FORBIDDEN_DAY_N"] = 1
        return _stop("STOP. Today or forbidden day in RCA inputs.", pre=pre, leak=leak, sha=sha)

    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [
            str(V22_OUT / "report.json"),
            str(V26_OUT / "report.json"),
            str(V27_OUT / "report.json"),
            str(V28_OUT / "report.json"),
            str(V29_OUT / "report.json"),
            str(LIFECYCLE_OUT / "report.json"),
            str(BRANCH_U_OUT / "report.json"),
            str(BRANCH_U_CAUSAL_OUT / "report.json"),
            str(BRANCH_U_HOLDOUT_OUT / "report.json"),
        ],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, sha=sha)

    dev_h = harvest_days(dev_days, cohort="DEVELOPMENT", spec_sha=sha, today=TODAY)
    if not dev_h.get("ok"):
        return _stop(f"STOP. Development harvest failed: {dev_h.get('blocker')}.", pre=pre, leak=leak, sha=sha)
    fwd_h = harvest_days(fwd_days, cohort="FORWARD_BURNED", spec_sha=sha, today=TODAY)
    if not fwd_h.get("ok"):
        return _stop(f"STOP. Forward harvest failed: {fwd_h.get('blocker')}.", pre=pre, leak=leak, sha=sha)

    post_frozen = _mtimes(FROZEN_OFFICIAL)
    if post_frozen != frozen_mtime:
        leak["BRANCH_U_WRITE_N"] = 1
        return _stop("STOP. Frozen official artifacts changed.", pre=pre, leak=leak, sha=sha)

    avail_dev = field_availability(dev_h.get("inventory") or {})
    avail_fwd = field_availability(fwd_h.get("inventory") or {})
    avail_all = field_availability(merge_inventory(dict(dev_h.get("inventory") or {}), dict(fwd_h.get("inventory") or {})))
    availability = {
        **avail_all,
        "development": avail_dev,
        "forward": avail_fwd,
        "true_l1_buy1_sell1_usable": bool(avail_dev.get("true_l1_buy1_sell1_usable") and avail_fwd.get("true_l1_buy1_sell1_usable")),
        "depth_buy1to10_sell1to10_usable": bool(avail_dev.get("depth_buy1to10_sell1to10_usable") and avail_fwd.get("depth_buy1to10_sell1to10_usable")),
    }
    dev = cohort_pack(list(dev_h.get("rows") or []), cohort="DEVELOPMENT")
    fwd = cohort_pack(list(fwd_h.get("rows") or []), cohort="FORWARD_BURNED")
    decision = decide(dev, fwd, availability)
    ans = answers(dev, fwd, decision)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    ni_ok = bool(leak_ok and reporting.get("RUNTIME_PID_UNCHANGED") and reporting.get("CAPTURE_PID_UNCHANGED") and reporting.get("REPORTING_SEMANTICS_PASS"))
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "DEVELOPMENT_DAYS": dev_days,
        "FORWARD_BURNED_DAYS": fwd_days,
        "HOLDOUT_VERDICT_KEPT": HOLDOUT_VERDICT_KEPT,
        "HOLDOUT_STATUS": HOLDOUT_STATUS,
        "HOLDOUT_TERMINATION_REASON": HOLDOUT_TERMINATION_REASON,
        "PRIMARY_QUESTION": PRIMARY_QUESTION,
        "SECONDARY_QUESTION": SECONDARY_QUESTION,
        "CAP_ROLE": CAP_ROLE,
        "BOARD_OK_REUSED": False,
        "POLICY_CREATED": False,
        "THRESHOLD_SEARCH": False,
        "EVOLUTION_WINDOW_SEC": EVOLUTION_WINDOW_SEC,
        "EVOLUTION_WINDOW_SOURCE": EVOLUTION_WINDOW_SOURCE,
        "TESTED_BRANCH_U_EXIT_MECHANISMS": list(TESTED_BRANCH_U_EXIT_MECHANISMS),
        "BOARD_FIELD_AVAILABILITY": {
            "true_l1": availability.get("true_l1_buy1_sell1_usable"),
            "depth_1to10": availability.get("depth_buy1to10_sell1to10_usable"),
            "sample_events_n": availability.get("sample_events_n"),
        },
        "PRE_CAP_SUPPORTED": decision.get("PRE_CAP_SUPPORTED"),
        "EXIT_BOARD_SUPPORTED": decision.get("EXIT_BOARD_SUPPORTED"),
        "PRIMARY_NEXT_MECHANISM": decision.get("PRIMARY_NEXT_MECHANISM"),
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "CASE": decision.get("CASE"),
        "BOARD_RCA_SPEC_SHA256": sha,
        "PARENT_SPEC_SHA256": parent_sha,
        "BRANCH_U_ONE_SHOT_SPEC_SHA256": BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED,
        "CAUSAL_SPEC_SHA256": CAUSAL_SPEC_SHA256_EXPECTED,
        "BRANCH_P_TECHNICAL_EXIT": bool(BRANCH_P_TECHNICAL_EXIT),
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "decision": decision,
        "answers": ans,
        "availability": availability,
        "development": {"counts": dev.get("counts")},
        "forward": {"counts": fwd.get("counts")},
        "audit": list(fwd_h.get("audit") or []),
        "preflight": pre,
        "postflight": post,
        "reporting": reporting,
        "leak": leak,
        "holdout_termination": term,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report, leak, reporting))
    print(
        f"FINAL ANALYSIS_ID={ANALYSIS_ID} VERDICT={decision.get('VERDICT')} "
        f"PRIMARY={decision.get('PRIMARY_NEXT_MECHANISM')} ni={ni_ok} out={CAUSAL_BOARD_RCA_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if ni_ok and decision.get("CASE") in {"A", "B", "C", "D"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
