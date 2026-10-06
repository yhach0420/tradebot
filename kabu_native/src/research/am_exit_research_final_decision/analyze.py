"""Verify locked RCA / PTL / continuation evidence. No new EXIT. No fit."""
from __future__ import annotations

from typing import Any

from research.am_exit_research_final_decision import (
    C0_AUGMENT_LOCKED,
    C0_C14_LOCKED,
    CONT_LOCKED,
    CURRENT_LOCKED,
    ENTRY_PARENT_SHA256,
    PTL_LOCKED,
)
from research.canonical_entry_performance_rebase.analyze import _f
from research.passive_wait_policy_reassessment.analyze import _close


def _req(body: dict[str, Any]) -> dict[str, Any]:
    return dict(body.get("required") or {})


def _check(fails: list[str], got: Any, exp: Any, tol: float, name: str) -> None:
    if isinstance(exp, bool):
        if got is not exp:
            fails.append(f"{name} got={got} expected={exp}")
        return
    if isinstance(exp, int) and not isinstance(exp, bool) and abs(float(tol)) < 1e-18:
        if int(got if got is not None else -10**9) != int(exp):
            fails.append(f"{name} got={got} expected={exp}")
        return
    if isinstance(exp, str):
        if str(got or "") != str(exp):
            fails.append(f"{name} got={got} expected={exp}")
        return
    if got is None or not _close(_f(got), exp, tol):
        fails.append(f"{name} got={got} expected={exp}")


def evidence_ok(
    *,
    rca: dict[str, Any],
    ptl: dict[str, Any],
    cont: dict[str, Any],
    abs_tol: float,
) -> tuple[bool, list[str]]:
    fails: list[str] = []
    r = _req(rca)
    p = _req(ptl)
    c = _req(cont)
    _check(fails, r.get("BASE_PARITY"), True, 0, "RCA_BASE_PARITY")
    _check(fails, r.get("CURRENT_TRADE_N"), CURRENT_LOCKED["TRADE_N"], 0, "RCA_CURRENT_TRADE_N")
    _check(fails, r.get("C0_AUGMENT_TRADE_N"), C0_AUGMENT_LOCKED["TRADE_N"], 0, "RCA_AUG_TRADE_N")
    _check(fails, r.get("C0_AUGMENT_WIN_N"), C0_AUGMENT_LOCKED["WIN_N"], 0, "RCA_AUG_WIN_N")
    _check(fails, r.get("C0_AUGMENT_LOSS_N"), C0_AUGMENT_LOCKED["LOSS_N"], 0, "RCA_AUG_LOSS_N")
    _check(fails, r.get("C0_AUGMENT_FLAT_N"), C0_AUGMENT_LOCKED["FLAT_N"], 0, "RCA_AUG_FLAT_N")
    _check(fails, r.get("C0_AUGMENT_NET"), C0_AUGMENT_LOCKED["NET"], abs_tol, "RCA_AUG_NET")
    _check(fails, r.get("C0_LOSS_ENTRY_NEVER_PROFITABLE_N"), C0_AUGMENT_LOCKED["L1_N"], 0, "RCA_L1_N")
    _check(fails, r.get("C0_LOSS_PROFIT_TO_LOSS_N"), C0_AUGMENT_LOCKED["L2_N"], 0, "RCA_L2_N")
    _check(fails, r.get("C0_LOSS_RECOVERED_AFTER_EXIT_N"), C0_AUGMENT_LOCKED["L3_N"], 0, "RCA_L3_N")
    _check(fails, r.get("C0_LOSS_PARTIAL_RECOVERY_N"), C0_AUGMENT_LOCKED["L4_N"], 0, "RCA_L4_N")
    _check(fails, r.get("C0_LOSS_GROSS_LOSS"), C0_AUGMENT_LOCKED["GROSS_LOSS"], abs_tol, "RCA_GROSS_LOSS")
    _check(fails, r.get("L1_GROSS_LOSS_SHARE"), C0_AUGMENT_LOCKED["L1_GROSS_LOSS_SHARE"], 1e-12, "RCA_L1_SHARE")
    _check(fails, r.get("L2_GROSS_LOSS_SHARE"), C0_AUGMENT_LOCKED["L2_GROSS_LOSS_SHARE"], 1e-12, "RCA_L2_SHARE")
    _check(fails, r.get("L3_GROSS_LOSS_SHARE"), C0_AUGMENT_LOCKED["L3_GROSS_LOSS_SHARE"], 1e-12, "RCA_L3_SHARE")
    _check(fails, r.get("PRIMARY_LOSS_MECHANISM"), "L2_PROFIT_TO_LOSS_BEFORE_EXIT", 0, "RCA_PRIMARY")
    _check(fails, r.get("EXIT_CONTRIBUTION_SUPPORTED"), True, 0, "RCA_EXIT_CONTRIBUTION")
    _check(fails, r.get("Q8"), "EXIT_DOMINANT", 0, "RCA_Q8")
    _check(fails, r.get("C0_PROSPECTIVE_SPEC_SHA256"), ENTRY_PARENT_SHA256, 0, "RCA_C0_SHA")
    _check(fails, r.get("VERDICT"), "AM_C0_EXIT_CONTRIBUTION_SUPPORTED", 0, "RCA_VERDICT")

    _check(fails, p.get("BASE_PARITY"), True, 0, "PTL_BASE_PARITY")
    _check(fails, p.get("EXIT_ARCHITECTURE"), PTL_LOCKED["ARCHITECTURE_ID"], 0, "PTL_ARCH")
    _check(fails, p.get("EXIT_PRECOMMIT_SPEC_SHA256"), PTL_LOCKED["PRECOMMIT_SHA256"], 0, "PTL_SHA")
    _check(fails, p.get("PTL_TRIGGER_N"), PTL_LOCKED["PTL_TRIGGER_N"], 0, "PTL_TRIGGER_N")
    _check(fails, p.get("L2_PTL_TRIGGER_N"), PTL_LOCKED["L2_PTL_TRIGGER_N"], 0, "PTL_L2_TRIGGER")
    _check(fails, p.get("L2_CONVERTED_TO_NONLOSS_N"), PTL_LOCKED["L2_CONVERTED_TO_NONLOSS_N"], 0, "PTL_L2_CONV")
    _check(fails, p.get("WINNER_EARLY_CUT_N"), PTL_LOCKED["WINNER_EARLY_CUT_N"], 0, "PTL_WIN_CUT")
    _check(fails, p.get("LOSER_PNL_SAVED_BY_GUARD"), PTL_LOCKED["LOSER_PNL_SAVED"], abs_tol, "PTL_SAVED")
    _check(fails, p.get("WINNER_PNL_LOST_BY_GUARD"), PTL_LOCKED["WINNER_PNL_LOST"], abs_tol, "PTL_LOST")
    _check(fails, p.get("FIXED_DELTA_NET"), PTL_LOCKED["FIXED_DELTA_NET"], abs_tol, "PTL_FIXED_DELTA")
    _check(fails, p.get("E1_OVERLAY_NET"), PTL_LOCKED["OVERLAY_NET"], abs_tol, "PTL_NET")
    _check(fails, p.get("E1_OVERLAY_PF"), PTL_LOCKED["OVERLAY_PF"], 1e-12, "PTL_PF")
    _check(fails, p.get("E1_OVERLAY_DD"), PTL_LOCKED["OVERLAY_DD"], abs_tol, "PTL_DD")
    _check(fails, p.get("VERDICT"), PTL_LOCKED["VERDICT"], 0, "PTL_VERDICT")
    _check(fails, p.get("E1_FULL_PASS"), False, 0, "PTL_FULL_PASS")

    _check(fails, c.get("BASE_PARITY"), True, 0, "CONT_BASE_PARITY")
    _check(fails, c.get("E0_NET"), C0_C14_LOCKED["NET"], abs_tol, "CONT_E0_NET")
    _check(fails, c.get("E0_PF"), C0_C14_LOCKED["PF"], 1e-12, "CONT_E0_PF")
    _check(fails, c.get("E0_DD"), C0_C14_LOCKED["DD"], abs_tol, "CONT_E0_DD")
    _check(fails, c.get("E1_FORCED_EXTEND_N"), CONT_LOCKED["FORCED_EXTEND_N"], 0, "CONT_E1_FORCE")
    _check(fails, c.get("E2_FORCED_EXTEND_N"), CONT_LOCKED["FORCED_EXTEND_N"], 0, "CONT_E2_FORCE")
    _check(fails, c.get("E1_FIXED_DELTA_NET"), CONT_LOCKED["FIXED_DELTA_NET"], abs_tol, "CONT_E1_FIXED")
    _check(fails, c.get("E2_FIXED_DELTA_NET"), CONT_LOCKED["FIXED_DELTA_NET"], abs_tol, "CONT_E2_FIXED")
    _check(fails, c.get("E1_OVERLAY_NET"), CONT_LOCKED["OVERLAY_NET"], abs_tol, "CONT_E1_NET")
    _check(fails, c.get("E2_OVERLAY_NET"), CONT_LOCKED["OVERLAY_NET"], abs_tol, "CONT_E2_NET")
    _check(fails, c.get("E1_OVERLAY_PF"), CONT_LOCKED["OVERLAY_PF"], 1e-12, "CONT_E1_PF")
    _check(fails, c.get("E2_OVERLAY_PF"), CONT_LOCKED["OVERLAY_PF"], 1e-12, "CONT_E2_PF")
    _check(fails, c.get("E1_OVERLAY_DD"), CONT_LOCKED["OVERLAY_DD"], abs_tol, "CONT_E1_DD")
    _check(fails, c.get("E2_OVERLAY_DD"), CONT_LOCKED["OVERLAY_DD"], abs_tol, "CONT_E2_DD")
    _check(fails, c.get("E1_PAIRED_MEDIAN"), CONT_LOCKED["PAIRED_MEDIAN"], abs_tol, "CONT_E1_MED")
    _check(fails, c.get("E1_POS_DAYS"), CONT_LOCKED["POS_DAYS"], 0, "CONT_E1_POS")
    _check(fails, c.get("E1_NEG_DAYS"), CONT_LOCKED["NEG_DAYS"], 0, "CONT_E1_NEG")
    _check(fails, c.get("E1_EX_BEST"), CONT_LOCKED["EX_BEST"], abs_tol, "CONT_E1_EX_BEST")
    _check(fails, c.get("E1_EX_TOP3"), CONT_LOCKED["EX_TOP3"], abs_tol, "CONT_E1_EX_TOP3")
    _check(fails, c.get("PRECOMMIT_SPEC_SHA256"), CONT_LOCKED["PRECOMMIT_SHA256"], 0, "CONT_SHA")
    _check(fails, c.get("VERDICT"), CONT_LOCKED["VERDICT"], 0, "CONT_VERDICT")
    _check(fails, c.get("PASS_ARM_N"), 0, 0, "CONT_PASS_ARM_N")
    _check(fails, c.get("ENTRY_PARENT_SHA256"), ENTRY_PARENT_SHA256, 0, "CONT_C0_SHA")
    return (not fails), fails
