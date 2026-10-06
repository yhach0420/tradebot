"""Parity, success/fail verdict. No outer chasing. No spec addition."""
from __future__ import annotations

from collections import Counter
from typing import Any

from research.am_entry_profit_improvement import PARITY_ABS_TOL, PARITY_EXPECTED
from research.am_wait5_two_stage_development.oof import eval_arm, select_control, select_fill_only
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.passive_wait_policy_reassessment.analyze import _close
from research.wait5_session_target_learnability.oof import admission_fill
from research.wait5_session_target_learnability import AM_TOPK


def freeze_parity(obs: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "AM_LABELED_N": int(obs.get("AM_LABELED_N") or -1) == int(PARITY_EXPECTED["AM_LABELED_N"]),
        "AM_Y_FILL5_POS_N": int(obs.get("AM_Y_FILL5_POS_N") or -1) == int(PARITY_EXPECTED["AM_Y_FILL5_POS_N"]),
        "AM_CURRENT_TOP3_FILL5_RATE": _close(
            obs.get("AM_CURRENT_TOP3_FILL5_RATE"),
            PARITY_EXPECTED["AM_CURRENT_TOP3_FILL5_RATE"],
            PARITY_ABS_TOL,
        ),
    }
    return {"ok": all(checks.values()), "checks": checks, "observed": obs, "expected": dict(PARITY_EXPECTED)}


def independent_top3(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cur = admission_fill(rows, "current_score", int(AM_TOPK), list(ELIGIBLE_DAYS))
    fo = eval_arm(rows, select_fill_only, list(ELIGIBLE_DAYS))
    ctrl = eval_arm(rows, select_control, list(ELIGIBLE_DAYS))
    return {
        "CURRENT": cur,
        "CURRENT_EVAL": ctrl,
        "FILL_ONLY_INDEPENDENT": fo,
        "AM_CURRENT_TOP3_FILL5_RATE": cur.get("FILL_RATE") if "FILL_RATE" in cur else cur.get("SELECTED_FILL_RATE"),
    }


def stability(selected: list[dict[str, Any]]) -> dict[str, Any]:
    arch = Counter(str(s.get("architecture_id") or "") for s in selected)
    rep = Counter(str(s.get("representation_id") or "") for s in selected)
    n = max(len(selected), 1)
    most_arch = arch.most_common(1)[0] if arch else ("", 0)
    most_rep = rep.most_common(1)[0] if rep else ("", 0)
    return {
        "SELECTED_ARCH_COUNTS": dict(arch),
        "SELECTED_REP_COUNTS": dict(rep),
        "MOST_COMMON_ARCH_SHARE": float(most_arch[1]) / float(n) if n else None,
        "MOST_COMMON_REP_SHARE": float(most_rep[1]) / float(n) if n else None,
        "MOST_COMMON_ARCH": most_arch[0],
        "MOST_COMMON_REP": most_rep[0],
    }


def decide(*, passed: bool, integrity_ok: bool) -> dict[str, Any]:
    if not integrity_ok:
        return {
            "VERDICT": "AM_ENTRY_PROFIT_IMPROVEMENT_INTEGRITY_FAILED",
            "NEXT": "AM_ENTRY_PROFIT_FAILURE_DECOMPOSITION",
            "CASE": "INTEGRITY",
        }
    if passed:
        return {
            "VERDICT": "AM_ENTRY_PROFIT_IMPROVEMENT_SUPPORTED",
            "NEXT": "AM_ENTRY_PROFIT_POLICY_FREEZE_REVIEW",
            "CASE": "SUCCESS",
        }
    return {
        "VERDICT": "AM_ENTRY_PROFIT_IMPROVEMENT_NOT_FOUND_IN_PRECOMMITTED_SPACE",
        "NEXT": "AM_ENTRY_PROFIT_FAILURE_DECOMPOSITION",
        "CASE": "FAIL",
    }
