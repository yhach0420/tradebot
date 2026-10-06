"""Offline Branch U false-break sequence RCA. No new EXIT. No 20260903. No live control."""
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
)
from research.simple_tech_redesign.branch_u_causal_spec import BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED, BRANCH_U_ONE_SHOT_VERDICT_EXPECTED
from research.simple_tech_redesign.branch_u_false_break_rca_analyze import FWD_U_TRIGGER_N_EXPECTED, cohort_pack, decide
from research.simple_tech_redesign.branch_u_false_break_rca_harvest import harvest_cohort
from research.simple_tech_redesign.branch_u_false_break_rca_publish import build_markdown, build_sheets, write_artifacts
from research.simple_tech_redesign.branch_u_false_break_rca_spec import (
    ANALYSIS_ID,
    BB_PARAM_CHANGED,
    BOARD_FAMILY_STATUS,
    BOARD_USED,
    BPS_THRESHOLD_USED,
    DEV_U_TRIGGER_N_EXPECTED,
    FIXED_WAIT_USED,
    HARD_REJECT_SPEC_SHA256_EXPECTED,
    HARD_REJECT_VERDICT_EXPECTED,
    K_BAR_USED,
    MIN_DEV_GROUP_N,
    MIN_FWD_GROUP_N,
    NEW_EXIT_RULE,
    PRE_CAP_BOARD_USED,
    THRESHOLD_SEARCH,
    canonical_false_break_spec,
    source_sha256_false_break,
    spec_sha256_false_break,
)
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.branch_u_holdout_spec import CAUSAL_SPEC_SHA256_EXPECTED, CAUSAL_VERDICT_EXPECTED
from research.simple_tech_redesign.causal_board_rca_spec import (
    FORBIDDEN_DAYS,
    HOLDOUT_STATUS,
    HOLDOUT_VERDICT_KEPT,
    spec_sha256_board_rca,
)
from research.simple_tech_redesign.exit_lifecycle_spec import V29_SPEC_SHA256_EXPECTED, V29_VERDICT_EXPECTED, spec_sha256_lifecycle
from research.simple_tech_redesign.isolation import (
    BRANCH_U_CAUSAL_OUT,
    BRANCH_U_FALSE_BREAK_RCA_OUT,
    BRANCH_U_HOLDOUT_OUT,
    BRANCH_U_OUT,
    CAUSAL_BOARD_RCA_OUT,
    LIFECYCLE_OUT,
    PRE_CAP_CANDIDATE_OUT,
    PRE_CAP_HARD_REJECT_RCA_OUT,
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
from research.simple_tech_redesign.pre_cap_candidate_spec import BOARD_RCA_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.pre_cap_hard_reject_rca_spec import spec_sha256_hard_reject_rca
from research.simple_tech_redesign.v29_spec import (
    V26_SPEC_SHA256_FROZEN,
    V26_VERDICT_FROZEN,
    V27_PRIMARY_EXPECTED,
    V27_SPEC_SHA256_FROZEN,
    V27_VERDICT_FROZEN,
    V28_SPEC_SHA256_EXPECTED,
    V28_VERDICT_EXPECTED,
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
    "K_BAR_N",
    "FIXED_WAIT_N",
    "BPS_THRESHOLD_N",
    "BB_PARAM_CHANGE_N",
    "BOARD_USED_N",
    "PRE_CAP_BOARD_USED_N",
    "NEW_EXIT_RULE_N",
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
    "BRANCH_U_HOLDOUT_WRITE_N",
    "CAUSAL_BOARD_RCA_WRITE_N",
    "PRE_CAP_CANDIDATE_WRITE_N",
    "PRE_CAP_HARD_REJECT_WRITE_N",
)

FROZEN_OFFICIAL = (
    V26_OUT / "report.json",
    V27_OUT / "report.json",
    V28_OUT / "report.json",
    V29_OUT / "report.json",
    LIFECYCLE_OUT / "report.json",
    BRANCH_U_OUT / "report.json",
    BRANCH_U_CAUSAL_OUT / "report.json",
    BRANCH_U_HOLDOUT_OUT / "report.json",
    CAUSAL_BOARD_RCA_OUT / "report.json",
    PRE_CAP_CANDIDATE_OUT / "report.json",
    PRE_CAP_HARD_REJECT_RCA_OUT / "report.json",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _mtimes(paths: tuple[Path, ...]) -> dict[str, float]:
    return {str(p): float(p.stat().st_mtime) for p in paths if p.is_file()}


def _slim(pack: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "cohort",
        "days",
        "triggered_n",
        "PRIMARY_PATH",
        "primary",
        "slices",
        "bb_recompute_match_n",
        "bb_recompute_fail_n",
        "trigger_low_n",
        "tie_at_first_n",
        "leak",
        "rows",
    )
    return {k: pack.get(k) for k in keep}


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str, source_sha: str) -> int:
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "VERDICT": "SIMPLE_TECH_BRANCH_U_FALSE_BREAK_SEQUENCE_INVALID",
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "NEW_EXIT_RULE": False,
        "NEXT": msg,
        "FALSE_BREAK_RCA_SPEC_SHA256": sha,
        "SOURCE_SHA256": source_sha,
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": req,
        "preflight": pre,
        "leak": leak,
        "decision": {"CASE": "E", "VERDICT": "SIMPLE_TECH_BRANCH_U_FALSE_BREAK_SEQUENCE_INVALID", "NEXT": msg},
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report, leak, {}))
    print(msg, flush=True)
    print("STOP.", flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_false_break_spec()
    sha = spec_sha256_false_break(spec)
    source_sha = source_sha256_false_break()
    chk = self_check()
    pre = snapshot(phase="PRE")
    frozen_mtime = _mtimes(FROZEN_OFFICIAL)
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["THRESHOLD_SEARCH_N"] = int(bool(THRESHOLD_SEARCH))
    leak["K_BAR_N"] = int(bool(K_BAR_USED))
    leak["FIXED_WAIT_N"] = int(bool(FIXED_WAIT_USED))
    leak["BPS_THRESHOLD_N"] = int(bool(BPS_THRESHOLD_USED))
    leak["BB_PARAM_CHANGE_N"] = int(bool(BB_PARAM_CHANGED))
    leak["BOARD_USED_N"] = int(bool(BOARD_USED))
    leak["PRE_CAP_BOARD_USED_N"] = int(bool(PRE_CAP_BOARD_USED))
    leak["NEW_EXIT_RULE_N"] = int(bool(NEW_EXIT_RULE))
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} false_break_rca={sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if spec_sha256_lifecycle() != LIFECYCLE_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Lifecycle spec SHA drifted.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if spec_sha256_board_rca() != BOARD_RCA_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Board RCA spec SHA drifted.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if spec_sha256_hard_reject_rca() != HARD_REJECT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Hard-reject RCA spec SHA drifted.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if SESSION != "AM" or THRESHOLD_SEARCH or NEW_EXIT_RULE or K_BAR_USED or FIXED_WAIT_USED or BPS_THRESHOLD_USED or BB_PARAM_CHANGED or BOARD_USED or PRE_CAP_BOARD_USED:
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    v26_req = dict(_load(V26_OUT / "report.json").get("required") or {})
    if str(v26_req.get("VERDICT") or "") != V26_VERDICT_FROZEN or str(v26_req.get("V26_SPEC_SHA256") or "") != V26_SPEC_SHA256_FROZEN:
        return _stop("STOP. V26 official freeze failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    v27_req = dict(_load(V27_OUT / "report.json").get("required") or {})
    if str(v27_req.get("VERDICT") or "") != V27_VERDICT_FROZEN or str(v27_req.get("V27_SPEC_SHA256") or "") != V27_SPEC_SHA256_FROZEN:
        return _stop("STOP. V27 official freeze failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    prim = dict(v27_req.get("PRIMARY_EXIT_STATE_MECHANISM") or {})
    if str(prim.get("name") or "") != V27_PRIMARY_EXPECTED:
        return _stop("STOP. V27 primary mechanism freeze failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    v28_req = dict(_load(V28_OUT / "report.json").get("required") or {})
    if str(v28_req.get("VERDICT") or "") != V28_VERDICT_EXPECTED or str(v28_req.get("V28_SPEC_SHA256") or "") != V28_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V28 official freeze failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    v29_req = dict(_load(V29_OUT / "report.json").get("required") or {})
    if str(v29_req.get("VERDICT") or "") != V29_VERDICT_EXPECTED or str(v29_req.get("V29_SPEC_SHA256") or "") != V29_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V29 official freeze failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    life_req = dict(_load(LIFECYCLE_OUT / "report.json").get("required") or {})
    if str(life_req.get("VERDICT") or "") != LIFECYCLE_VERDICT_EXPECTED or str(life_req.get("LIFECYCLE_SPEC_SHA256") or "") != LIFECYCLE_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Lifecycle official freeze failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    u_req = dict(_load(BRANCH_U_OUT / "report.json").get("required") or {})
    if str(u_req.get("VERDICT") or "") != BRANCH_U_ONE_SHOT_VERDICT_EXPECTED:
        return _stop("STOP. Branch U one-shot official freeze failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if str(u_req.get("BRANCH_U_SPEC_SHA256") or "") != BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Branch U one-shot spec SHA drifted.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    causal_req = dict(_load(BRANCH_U_CAUSAL_OUT / "report.json").get("required") or {})
    if str(causal_req.get("VERDICT") or "") != CAUSAL_VERDICT_EXPECTED:
        return _stop("STOP. Branch U causal official freeze failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if str(causal_req.get("CAUSAL_SPEC_SHA256") or "") != CAUSAL_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Branch U causal spec SHA drifted.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    hold_req = dict(_load(BRANCH_U_HOLDOUT_OUT / "report.json").get("required") or {})
    if str(hold_req.get("VERDICT") or "") != HOLDOUT_VERDICT_KEPT:
        return _stop("STOP. Holdout formal verdict is not ACCUMULATING.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if str(hold_req.get("HOLDOUT_STATUS") or "") != HOLDOUT_STATUS:
        return _stop("STOP. Holdout TERMINATED_INCONCLUSIVE status missing.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    hr_req = dict(_load(PRE_CAP_HARD_REJECT_RCA_OUT / "report.json").get("required") or {})
    if str(hr_req.get("VERDICT") or "") != HARD_REJECT_VERDICT_EXPECTED:
        return _stop("STOP. Pre-CAP hard-reject official verdict mismatch.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if str(hr_req.get("HARD_REJECT_RCA_SPEC_SHA256") or "") != HARD_REJECT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Pre-CAP hard-reject spec SHA mismatch.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if BOARD_FAMILY_STATUS != HARD_REJECT_VERDICT_EXPECTED:
        return _stop("STOP. Board family status is not closed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    dev_days = [str(d) for d in ELIGIBLE_DAYS]
    fwd_days = [str(d) for d in LOCKED_SERIES_DAYS]
    use_days = dev_days + fwd_days
    if TODAY in set(use_days) or any(d in FORBIDDEN_DAYS for d in use_days):
        leak["TODAY_INCLUDED_N"] = 1
        leak["FORBIDDEN_DAY_N"] = 1
        return _stop("STOP. Today or forbidden day in RCA inputs.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if "20260903" in use_days:
        leak["FORBIDDEN_DAY_N"] = 1
        return _stop("STOP. 20260903 is quarantined.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

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
            str(CAUSAL_BOARD_RCA_OUT / "report.json"),
            str(PRE_CAP_CANDIDATE_OUT / "report.json"),
            str(PRE_CAP_HARD_REJECT_RCA_OUT / "report.json"),
        ],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    print(f"HARVEST development days={len(dev_days)}", flush=True)
    dev_h = harvest_cohort(dev_days, cohort="DEVELOPMENT", spec_sha=sha, today=TODAY)
    if not dev_h.get("ok"):
        return _stop(f"STOP. Development harvest failed: {dev_h.get('blocker')}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    print(f"HARVEST forward days={len(fwd_days)}", flush=True)
    fwd_h = harvest_cohort(fwd_days, cohort="FORWARD_BURNED", spec_sha=sha, today=TODAY)
    if not fwd_h.get("ok"):
        return _stop(f"STOP. Forward harvest failed: {fwd_h.get('blocker')}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    post_frozen = _mtimes(FROZEN_OFFICIAL)
    if post_frozen != frozen_mtime:
        leak["PRE_CAP_HARD_REJECT_WRITE_N"] = 1
        return _stop("STOP. Frozen official artifacts changed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    source_sha = source_sha256_false_break()

    if int(len(list(dev_h.get("rows") or []))) != int(DEV_U_TRIGGER_N_EXPECTED):
        return _stop(
            f"STOP. DEVELOPMENT U-trigger n={len(list(dev_h.get('rows') or []))} != {DEV_U_TRIGGER_N_EXPECTED}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
        )
    if int(len(list(fwd_h.get("rows") or []))) != int(FWD_U_TRIGGER_N_EXPECTED):
        return _stop(
            f"STOP. FORWARD_BURNED U-trigger n={len(list(fwd_h.get('rows') or []))} != {FWD_U_TRIGGER_N_EXPECTED}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
        )

    dev = cohort_pack(dev_h, min_n=int(MIN_DEV_GROUP_N))
    fwd = cohort_pack(fwd_h, min_n=int(MIN_FWD_GROUP_N))
    for k, v in dict(dev_h.get("leak") or {}).items():
        leak[f"DEV_{k}"] = int(v or 0)
    for k, v in dict(fwd_h.get("leak") or {}).items():
        leak[f"FWD_{k}"] = int(v or 0)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    ni_ok = bool(leak_ok and reporting.get("RUNTIME_PID_UNCHANGED") and reporting.get("CAPTURE_PID_UNCHANGED") and reporting.get("REPORTING_SEMANTICS_PASS"))
    decision = decide(dev, fwd, leak_ok=leak_ok, ni_ok=ni_ok)
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "DEVELOPMENT_DAYS": dev_days,
        "FORWARD_BURNED_DAYS": fwd_days,
        "PRIMARY_PATH": "EARLY_FAILURE",
        "BOARD_FAMILY_STATUS": BOARD_FAMILY_STATUS,
        "NEW_EXIT_RULE": False,
        "THRESHOLD_SEARCH": False,
        "K_BAR_USED": False,
        "FIXED_WAIT_USED": False,
        "BPS_THRESHOLD_USED": False,
        "BB_PARAM_CHANGED": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "CASE": decision.get("CASE"),
        "DEV_U_TRIGGER_N": decision.get("DEV_U_TRIGGER_N"),
        "FWD_U_TRIGGER_N": decision.get("FWD_U_TRIGGER_N"),
        "DEV_U_TRIGGER_N_EXPECTED": DEV_U_TRIGGER_N_EXPECTED,
        "FWD_U_TRIGGER_N_EXPECTED": FWD_U_TRIGGER_N_EXPECTED,
        "DEVELOPMENT_direction": decision.get("DEVELOPMENT_direction"),
        "FORWARD_BURNED_direction": decision.get("FORWARD_BURNED_direction"),
        "FALSE_BREAK_RCA_SPEC_SHA256": sha,
        "SOURCE_SHA256": source_sha,
        "PARENT_SPEC_SHA256": parent_sha,
        "BRANCH_U_ONE_SHOT_SPEC_SHA256": BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED,
        "HARD_REJECT_RCA_SPEC_SHA256": HARD_REJECT_SPEC_SHA256_EXPECTED,
        "BRANCH_P_TECHNICAL_EXIT": bool(BRANCH_P_TECHNICAL_EXIT),
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "decision": decision,
        "development": _slim(dev),
        "forward": _slim(fwd),
        "preflight": pre,
        "postflight": post,
        "reporting": reporting,
        "leak": leak,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report, leak, reporting))
    print(
        f"FINAL ANALYSIS_ID={ANALYSIS_ID} VERDICT={decision.get('VERDICT')} "
        f"CASE={decision.get('CASE')} ni={ni_ok} "
        f"DEV={decision.get('DEVELOPMENT_direction')} FWD={decision.get('FORWARD_BURNED_direction')} "
        f"out={BRANCH_U_FALSE_BREAK_RCA_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if ni_ok and decision.get("CASE") in {"A", "B", "C", "D"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
