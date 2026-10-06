"""Offline AM EXIT development close. Retain original C0+C14. No Runtime write. No Paper. No new fit."""
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

from research.am_entry_profit_improvement import (
    C14_CHANGED,
    C14_ID,
    CANCEL_N,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    DEV_WAIT_SEC,
    LIVE_ORDER_N,
    NEW_FORWARD_N,
    PAPER_OPERATED,
    PARITY_ABS_TOL,
    RUNTIME_CHANGED,
    SESSION,
    SUBMIT_N,
    TRUE_OOS,
    W5_RUNTIME_ADOPTED,
)
from research.am_entry_research_final_decision import (
    PAPER_STRATEGY_ADOPTION_ALLOWED,
    PROSPECTIVE_CHALLENGER_ID,
    PROSPECTIVE_CHALLENGER_NAME,
    PROSPECTIVE_STATUS,
    RUNTIME_ADOPTION_ALLOWED,
)
from research.am_entry_research_final_decision.spec import canonical_c0_spec, spec_sha256
from research.am_exit_research_final_decision import (
    ANALYSIS_ID,
    BEST_TESTED_EXIT_FOR_C0,
    C0_AUGMENT_LOCKED,
    C0_C14_LOCKED,
    CLOSED_LINES,
    CONT_LOCKED,
    CURRENT_LOCKED,
    ENTRY_PARENT_SHA256,
    FUTURE_OOS_FORBIDDEN_EXIT,
    NEXT,
    PAPER_OPERATION_N,
    PRIMARY_EXIT_WEAKNESS,
    PTL_LOCKED,
    RUNTIME_CHANGE_N,
    RUNTIME_EXIT_CHANGE_ALLOWED,
    STRATEGY_REPLAY_N,
    VERDICT,
)
from research.am_exit_research_final_decision.analyze import evidence_ok
from research.am_exit_research_final_decision.publish import OUT, build_markdown, kv_rows, write_artifacts
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import WAIT_SEC

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
RCA = NATIVE / "results" / "research" / "am_exit_contribution_rca" / "report.json"
PTL = NATIVE / "results" / "research" / "am_c0_exit_ptl_guard" / "report.json"
CONT = NATIVE / "results" / "research" / "am_c0_exit_continuation_reassessment" / "report.json"

INTEGRITY_ZERO_KEYS = (
    "NEW_MODEL_N",
    "NEW_FEATURE_N",
    "NEW_TARGET_N",
    "EXIT_THRESHOLD_SEARCH_N",
    "CONTINUATION_THRESHOLD_SEARCH_N",
    "EXIT_HORIZON_SEARCH_N",
    "PTL_REUSE_N",
    "WAIT_CHANGE_N",
    "CURRENT_EXIT_CHANGE_N",
    "RUNTIME_CHANGE_N",
    "PAPER_OPERATION_N",
    "STRATEGY_REPLAY_N",
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
)


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _empty_required(*, verdict: str, nxt: str, sha: str | None = None) -> dict[str, Any]:
    return {
        "AM_EXIT_MECHANISM_WEAKNESS_FOUND": False,
        "AM_EXIT_FULL_GATE_POLICY_FOUND": False,
        "AM_EXIT_SUPPORTED_REPLACEMENT_FOUND": False,
        "PRIMARY_EXIT_WEAKNESS": None,
        "C0_L2_GROSS_LOSS_SHARE": None,
        "PTL_ARCHITECTURE_SUPPORTED": False,
        "CONTINUATION_ARCHITECTURE_SUPPORTED": False,
        "BEST_TESTED_EXIT_FOR_C0": None,
        "ORIGINAL_C0_C14_RETAINED": False,
        "C0_PROSPECTIVE_SPEC_SHA256": sha,
        "AM_EXISTING_18D_EXIT_SEARCH_CLOSED": False,
        "RUNTIME_EXIT_CHANGE_ALLOWED": False,
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": verdict,
        "NEXT": nxt,
    }


def _integrity(msg: str, extra: dict | None = None, *, sha: str | None = None) -> int:
    required = _empty_required(
        verdict="AM_EXIT_FINAL_DECISION_INTEGRITY_FAILED",
        nxt="STOP",
        sha=sha,
    )
    required["STOP_REASON"] = msg
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": required,
        "closed_lines": list(CLOSED_LINES),
        **(extra or {}),
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(
        report,
        {
            "required": kv_rows(required),
            "integrity": kv_rows({"STOP_REASON": msg}),
            "closed_lines": [{"line": x} for x in CLOSED_LINES],
        },
    )
    print(msg, flush=True)
    return 2


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get(
        "PYTHONPATH", ""
    )
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM EXIT RESEARCH FINAL DECISION V1", flush=True)
    if abs(float(WAIT_SEC) - 1.0) > 1e-12:
        return _integrity("STOP. Runtime WAIT_SEC drifted from 1.0.")
    if abs(float(DEV_WAIT_SEC) - 5.0) > 1e-12:
        return _integrity("STOP. DEV_WAIT_SEC drifted from 5.0.")
    if list(FEATURE_ORDER) != [
        "spread_bps",
        "imbalance",
        "mid_ret_60s",
        "mid_ret_180s",
        "event_rate_60s",
        "log_bid_qty",
    ]:
        return _integrity("STOP. FEATURE_ORDER drift.")
    if ENTRY_BINDING.get("rank_pass_gate") is not None:
        return _integrity("STOP. rank_pass_gate drift.")
    c14 = _load(C14)
    if str(c14.get("candidate_id") or "") != C14_ID:
        return _integrity("STOP. C14 identity mismatch.")
    if TRUE_OOS is not False or int(NEW_FORWARD_N) != 0:
        return _integrity("STOP. TRUE_OOS / NEW_FORWARD_N drifted.")
    if int(SUBMIT_N) != 0 or int(CANCEL_N) != 0 or int(LIVE_ORDER_N) != 0 or PAPER_OPERATED is not False:
        return _integrity("STOP. submit/cancel/live/paper drifted.")
    if RUNTIME_ADOPTION_ALLOWED is not False or PAPER_STRATEGY_ADOPTION_ALLOWED is not False:
        return _integrity("STOP. Runtime/paper adoption drifted.")
    if int(RUNTIME_CHANGE_N) != 0 or int(PAPER_OPERATION_N) != 0 or int(STRATEGY_REPLAY_N) != 0:
        return _integrity("STOP. Runtime/paper/strategy-replay counters drifted.")
    if W5_RUNTIME_ADOPTED is not False or RUNTIME_CHANGED is not False or C14_CHANGED is not False:
        return _integrity("STOP. Runtime/C14/W5 freeze drifted.")
    if RUNTIME_EXIT_CHANGE_ALLOWED is not False:
        return _integrity("STOP. RUNTIME_EXIT_CHANGE_ALLOWED drifted.")

    sha = spec_sha256(canonical_c0_spec())
    print(f"C0_PROSPECTIVE_SPEC_SHA256 {sha}", flush=True)
    if sha != ENTRY_PARENT_SHA256:
        return _integrity("STOP. C0 prospective spec SHA256 mismatch.", extra={"got_sha": sha}, sha=sha)

    missing = [str(p) for p in (RCA, PTL, CONT) if not p.is_file()]
    if missing:
        return _integrity("STOP. Prior official EXIT report missing.", extra={"missing": missing}, sha=sha)
    rca = _load(RCA)
    ptl = _load(PTL)
    cont = _load(CONT)
    ok, fails = evidence_ok(rca=rca, ptl=ptl, cont=cont, abs_tol=PARITY_ABS_TOL)
    print(f"PRIOR_EVIDENCE_OK={ok}", flush=True)
    if not ok:
        return _integrity(
            "STOP. Locked RCA/PTL/continuation evidence did not match official reports.",
            extra={"evidence_fails": fails},
            sha=sha,
        )

    leak = {
        "NEW_MODEL_N": 0,
        "NEW_FEATURE_N": 0,
        "NEW_TARGET_N": 0,
        "EXIT_THRESHOLD_SEARCH_N": 0,
        "CONTINUATION_THRESHOLD_SEARCH_N": 0,
        "EXIT_HORIZON_SEARCH_N": 0,
        "PTL_REUSE_N": 0,
        "WAIT_CHANGE_N": 0,
        "CURRENT_EXIT_CHANGE_N": 0,
        "RUNTIME_CHANGE_N": RUNTIME_CHANGE_N,
        "PAPER_OPERATION_N": PAPER_OPERATION_N,
        "STRATEGY_REPLAY_N": STRATEGY_REPLAY_N,
        "SUBMIT_N": SUBMIT_N,
        "CANCEL_N": CANCEL_N,
        "LIVE_ORDER_N": LIVE_ORDER_N,
        "C14_CHANGED": C14_CHANGED,
        "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
        "PAPER_OPERATED": PAPER_OPERATED,
        "RUNTIME_CHANGED": RUNTIME_CHANGED,
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
        "PRIOR_EVIDENCE_OK": True,
        "C0_SPEC_SHA256_MATCH": True,
    }
    if any(int(leak.get(k) or 0) != 0 for k in INTEGRITY_ZERO_KEYS):
        return _integrity("STOP. Integrity counters non-zero.", extra={"integrity": leak}, sha=sha)

    required = {
        "AM_EXIT_MECHANISM_WEAKNESS_FOUND": True,
        "AM_EXIT_FULL_GATE_POLICY_FOUND": False,
        "AM_EXIT_SUPPORTED_REPLACEMENT_FOUND": False,
        "PRIMARY_EXIT_WEAKNESS": PRIMARY_EXIT_WEAKNESS,
        "C0_L2_GROSS_LOSS_SHARE": C0_AUGMENT_LOCKED["L2_GROSS_LOSS_SHARE"],
        "PTL_ARCHITECTURE_SUPPORTED": False,
        "CONTINUATION_ARCHITECTURE_SUPPORTED": False,
        "BEST_TESTED_EXIT_FOR_C0": BEST_TESTED_EXIT_FOR_C0,
        "ORIGINAL_C0_C14_RETAINED": True,
        "C0_PROSPECTIVE_SPEC_SHA256": sha,
        "AM_EXISTING_18D_EXIT_SEARCH_CLOSED": True,
        "RUNTIME_EXIT_CHANGE_ALLOWED": False,
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": VERDICT,
        "NEXT": NEXT,
        "PROSPECTIVE_CHALLENGER_ID": PROSPECTIVE_CHALLENGER_ID,
        "PROSPECTIVE_CHALLENGER_NAME": PROSPECTIVE_CHALLENGER_NAME,
        "PROSPECTIVE_STATUS": PROSPECTIVE_STATUS,
        "ENTRY_EXIT_CONTRACT": "C0_B0_PRIMARY_B1_CONFIRM + C14_ORIGINAL",
        "SESSION": SESSION,
        "DEV_WAIT_SEC": DEV_WAIT_SEC,
        "RUNTIME_WAIT_SEC": WAIT_SEC,
        "W5_RUNTIME_ADOPTED": False,
    }
    extra = {
        "closed_lines": list(CLOSED_LINES),
        "current_locked": CURRENT_LOCKED,
        "c0_c14_locked": C0_C14_LOCKED,
        "c0_augment_locked": C0_AUGMENT_LOCKED,
        "ptl_locked": PTL_LOCKED,
        "continuation_locked": CONT_LOCKED,
        "integrity": leak,
        "prior_sources": {
            "rca": str(RCA),
            "ptl": str(PTL),
            "continuation": str(CONT),
        },
        "interpretation": {
            "EXIT_MECHANISM_WEAKNESS_FOUND": True,
            "SUPPORTED_EXIT_REPLACEMENT_FOUND": False,
            "C14_CERTIFIED_PERFECT": False,
            "C14_SHOULD_BE_REPLACED_ON_THIS_EVIDENCE": False,
            "BEST_TESTED_ON_18D": BEST_TESTED_EXIT_FOR_C0,
            "note": "C0 has a development economic edge. C14 has a profit-to-loss giveback path. This 18-day set did not identify a causal EXIT replacement that keeps that edge.",
        },
        "same_data_search_forbidden": list(CLOSED_LINES),
        "future_oos_rule": {
            "entry": PROSPECTIVE_CHALLENGER_ID,
            "exit": "C14_ORIGINAL",
            "require_sha256_match": True,
            "sha256_key": "C0_PROSPECTIVE_SPEC_SHA256",
            "do_not_mix": list(FUTURE_OOS_FORBIDDEN_EXIT),
            "do_not_invent_exit_on_oos_days": True,
            "do_not_require_development_net_pf_reproduction": True,
            "one_shot_current_vs_c0_c14": True,
        },
    }
    sheets = {
        "required": kv_rows(required),
        "closed_lines": [{"line": x} for x in CLOSED_LINES],
        "evidence": kv_rows(
            {
                **{f"CURRENT_{k}": v for k, v in CURRENT_LOCKED.items()},
                **{f"C0_C14_{k}": v for k, v in C0_C14_LOCKED.items()},
                **{f"C0_AUG_{k}": v for k, v in C0_AUGMENT_LOCKED.items()},
                **{f"PTL_{k}": v for k, v in PTL_LOCKED.items()},
                **{f"CONT_{k}": v for k, v in CONT_LOCKED.items()},
            }
        ),
        "future_oos": kv_rows(extra["future_oos_rule"]),
        "integrity": kv_rows(leak),
    }
    report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, **extra}
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, sheets)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(f"VERDICT {required.get('VERDICT')}", flush=True)
    print(f"NEXT {required.get('NEXT')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
