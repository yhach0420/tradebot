"""Offline Pre-CAP entry quality candidate V1. No search. No 20260903. No live control."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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
    BRANCH_U_HOLDOUT_OUT,
    BRANCH_U_OUT,
    CAUSAL_BOARD_RCA_OUT,
    LIFECYCLE_OUT,
    PRE_CAP_CANDIDATE_OUT,
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
from research.simple_tech_redesign.pre_cap_candidate_analyze import answers, cohort_pack, decide
from research.simple_tech_redesign.pre_cap_candidate_harvest import load_cohort
from research.simple_tech_redesign.pre_cap_candidate_publish import build_markdown, build_sheets, write_artifacts
from research.simple_tech_redesign.pre_cap_candidate_spec import (
    ADVERSE_COMPONENT_MIN,
    ANALYSIS_ID,
    BOARD_OK_REUSED,
    BOARD_RCA_SPEC_SHA256_EXPECTED,
    BRANCH_U_USED,
    CANDIDATE_ID,
    COMPONENT_DEFINITIONS,
    COMPONENT_KEYS,
    COMPONENT_NAMES,
    EVOLUTION_WINDOW_SEC,
    EVOLUTION_WINDOW_SOURCE,
    FORCE_TREATMENT_FILL_SET,
    K_OF_N_SEARCH,
    PRIMARY_EXIT,
    REJECT_REASON,
    SOURCE_BOARD_RCA_VERDICT,
    THRESHOLD_SEARCH,
    TRUE_L1_ASK,
    TRUE_L1_BID,
    WINDOW_CHANGED,
    canonical_precap_spec,
    source_sha256_precap,
    spec_sha256_precap,
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
    "K_OF_N_SEARCH_N",
    "BOARD_OK_REUSED_N",
    "BRANCH_U_USED_N",
    "FORCE_TREATMENT_FILL_SET_N",
    "WINDOW_CHANGE_N",
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
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _mtimes(paths: tuple[Path, ...]) -> dict[str, float]:
    return {str(p): float(p.stat().st_mtime) for p in paths if p.is_file()}


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str, source_sha: str) -> int:
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "VERDICT": "SIMPLE_TECH_PRE_CAP_ENTRY_QUALITY_INVALID",
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "CANDIDATE_FROZEN": False,
        "NEXT": msg,
        "PRECAP_SPEC_SHA256": sha,
        "SOURCE_SHA256": source_sha,
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": req,
        "preflight": pre,
        "leak": leak,
        "decision": {"CASE": "E", "VERDICT": "SIMPLE_TECH_PRE_CAP_ENTRY_QUALITY_INVALID", "NEXT": msg, "CANDIDATE_FROZEN": False},
        "answers": {},
        "candidate_manifest": {"CANDIDATE_FROZEN": False, "SPEC_SHA256": sha, "SOURCE_SHA256": source_sha},
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
    spec = canonical_precap_spec()
    sha = spec_sha256_precap(spec)
    source_sha = source_sha256_precap()
    chk = self_check()
    pre = snapshot(phase="PRE")
    frozen_mtime = _mtimes(FROZEN_OFFICIAL)
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["THRESHOLD_SEARCH_N"] = int(bool(THRESHOLD_SEARCH))
    leak["K_OF_N_SEARCH_N"] = int(bool(K_OF_N_SEARCH))
    leak["BOARD_OK_REUSED_N"] = int(bool(BOARD_OK_REUSED))
    leak["BRANCH_U_USED_N"] = int(bool(BRANCH_U_USED))
    leak["FORCE_TREATMENT_FILL_SET_N"] = int(bool(FORCE_TREATMENT_FILL_SET))
    leak["WINDOW_CHANGE_N"] = int(bool(WINDOW_CHANGED))
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} precap={sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if spec_sha256_lifecycle() != LIFECYCLE_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Lifecycle spec SHA drifted.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if spec_sha256_board_rca() != BOARD_RCA_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Board RCA spec SHA drifted.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if SESSION != "AM" or THRESHOLD_SEARCH or BOARD_OK_REUSED or BRANCH_U_USED or FORCE_TREATMENT_FILL_SET:
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if int(ADVERSE_COMPONENT_MIN) != 2:
        return _stop("STOP. 2-of-3 drifted.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
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
    board_req = dict(_load(CAUSAL_BOARD_RCA_OUT / "report.json").get("required") or {})
    if str(board_req.get("VERDICT") or "") != SOURCE_BOARD_RCA_VERDICT:
        return _stop("STOP. Board RCA official verdict mismatch.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if str(board_req.get("BOARD_RCA_SPEC_SHA256") or "") != BOARD_RCA_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Board RCA official spec SHA mismatch.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    dev_days = [str(d) for d in ELIGIBLE_DAYS]
    fwd_days = [str(d) for d in LOCKED_SERIES_DAYS]
    use_days = dev_days + fwd_days
    if TODAY in set(use_days) or any(d in FORBIDDEN_DAYS for d in use_days):
        leak["TODAY_INCLUDED_N"] = 1
        leak["FORBIDDEN_DAY_N"] = 1
        return _stop("STOP. Today or forbidden day in candidate inputs.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

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
        ],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    created_at = datetime.now(ZoneInfo("Asia/Tokyo")).isoformat()
    dev_h = load_cohort(dev_days, cohort="DEVELOPMENT", today=TODAY)
    if not dev_h.get("ok"):
        return _stop(f"STOP. Development harvest failed: {dev_h.get('blocker')}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    fwd_h = load_cohort(fwd_days, cohort="FORWARD_BURNED", today=TODAY)
    if not fwd_h.get("ok"):
        return _stop(f"STOP. Forward harvest failed: {fwd_h.get('blocker')}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    post_frozen = _mtimes(FROZEN_OFFICIAL)
    if post_frozen != frozen_mtime:
        leak["CAUSAL_BOARD_RCA_WRITE_N"] = 1
        return _stop("STOP. Frozen official artifacts changed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    source_sha = source_sha256_precap()
    dev = cohort_pack(dev_h, days=dev_days)
    fwd = cohort_pack(fwd_h, days=fwd_days)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    ni_ok = bool(leak_ok and reporting.get("RUNTIME_PID_UNCHANGED") and reporting.get("CAPTURE_PID_UNCHANGED") and reporting.get("REPORTING_SEMANTICS_PASS"))
    decision = decide(dev, fwd, leak_ok=leak_ok, ni_ok=ni_ok)
    ans = answers(dev, fwd, decision)
    freeze = bool(decision.get("CANDIDATE_FROZEN"))
    manifest = {
        "CANDIDATE_ID": CANDIDATE_ID,
        "CANDIDATE_FROZEN": bool(freeze),
        "CREATION_TIMESTAMP": created_at if freeze else None,
        "ANALYSIS_CREATED_AT": created_at,
        "SPEC_SHA256": sha,
        "SOURCE_SHA256": source_sha,
        "TRUE_L1_BID": TRUE_L1_BID,
        "TRUE_L1_ASK": TRUE_L1_ASK,
        "EVOLUTION_WINDOW_SEC": EVOLUTION_WINDOW_SEC,
        "EVOLUTION_WINDOW_SOURCE": EVOLUTION_WINDOW_SOURCE,
        "COMPONENT_NAMES": list(COMPONENT_NAMES),
        "COMPONENT_KEYS": list(COMPONENT_KEYS),
        "COMPONENT_DEFINITIONS": dict(COMPONENT_DEFINITIONS),
        "ADVERSE_COMPONENT_MIN": int(ADVERSE_COMPONENT_MIN),
        "RULE": "PRE_CAP_REJECT iff adverse_component_count >= 2",
        "PIPELINE": "T3_THEN_PRECAP_THEN_E4_ASK_THEN_SAME_SYMBOL_THEN_CAP5_THEN_FILL_THEN_SESSION_CLOSE",
        "PRIMARY_EXIT": PRIMARY_EXIT,
        "REJECT_REASON": REJECT_REASON,
        "BOARD_OK_REUSED": False,
        "THRESHOLD_SEARCH": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "RUNTIME_IMPLEMENTED": False,
        "PAPER_INTRODUCED": False,
        "FUTURE_VALIDATION_STARTS_ON": "FIRST_COMPLETE_SEALED_CAPTURE_CREATED_AFTER_FREEZE" if freeze else None,
        "QUARANTINE_DAY_NOT_USED": "20260903",
        "canonical_spec": spec,
    }
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "CANDIDATE_ID": CANDIDATE_ID,
        "DEVELOPMENT_DAYS": dev_days,
        "FORWARD_BURNED_DAYS": fwd_days,
        "PRIMARY_EXIT": PRIMARY_EXIT,
        "BOARD_OK_REUSED": False,
        "THRESHOLD_SEARCH": False,
        "K_OF_N_SEARCH": False,
        "BRANCH_U_USED": False,
        "FORCE_TREATMENT_FILL_SET": False,
        "EVOLUTION_WINDOW_SEC": EVOLUTION_WINDOW_SEC,
        "TRUE_L1_BID": TRUE_L1_BID,
        "TRUE_L1_ASK": TRUE_L1_ASK,
        "ADVERSE_COMPONENT_MIN": int(ADVERSE_COMPONENT_MIN),
        "CANDIDATE_FROZEN": bool(freeze),
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "CASE": decision.get("CASE"),
        "PRECAP_SPEC_SHA256": sha,
        "SOURCE_SHA256": source_sha,
        "PARENT_SPEC_SHA256": parent_sha,
        "BOARD_RCA_SPEC_SHA256": BOARD_RCA_SPEC_SHA256_EXPECTED,
        "BRANCH_P_TECHNICAL_EXIT": bool(BRANCH_P_TECHNICAL_EXIT),
        "CREATION_TIMESTAMP": created_at if freeze else None,
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "decision": decision,
        "answers": ans,
        "development": {
            "QUALITY_OVERALL": dev.get("QUALITY_OVERALL"),
            "QUALITY_BY_ROLE": dev.get("QUALITY_BY_ROLE"),
            "CONTROL": dev.get("CONTROL"),
            "TREATMENT": dev.get("TREATMENT"),
            "ATTRIBUTION": dev.get("ATTRIBUTION"),
            "DAY_ROBUSTNESS": dev.get("DAY_ROBUSTNESS"),
            "CONCENTRATION": dev.get("CONCENTRATION"),
            "board_snapshot_rate": dev.get("board_snapshot_rate"),
            "occupancy_sot_ok": dev.get("occupancy_sot_ok"),
        },
        "forward": {
            "QUALITY_OVERALL": fwd.get("QUALITY_OVERALL"),
            "QUALITY_BY_ROLE": fwd.get("QUALITY_BY_ROLE"),
            "CONTROL": fwd.get("CONTROL"),
            "TREATMENT": fwd.get("TREATMENT"),
            "ATTRIBUTION": fwd.get("ATTRIBUTION"),
            "DAY_ROBUSTNESS": fwd.get("DAY_ROBUSTNESS"),
            "CONCENTRATION": fwd.get("CONCENTRATION"),
            "board_snapshot_rate": fwd.get("board_snapshot_rate"),
            "occupancy_sot_ok": fwd.get("occupancy_sot_ok"),
        },
        "candidate_manifest": manifest,
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
        f"CASE={decision.get('CASE')} FROZEN={freeze} ni={ni_ok} out={PRE_CAP_CANDIDATE_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if ni_ok and decision.get("CASE") in {"A", "B", "C", "D"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
