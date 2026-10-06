"""Verify locked C0 evidence. No new fit. No rule change."""
from __future__ import annotations

from typing import Any

from research.am_entry_research_final_decision import (
    BLOCKED_B0_COND_PNL,
    C0_LOCKED,
    CURRENT_LOCKED,
    RETAINED_B0_COND_PNL,
)
from research.canonical_entry_performance_rebase.analyze import _f
from research.passive_wait_policy_reassessment.analyze import _close


def _req(prior: dict[str, Any]) -> dict[str, Any]:
    return dict(prior.get("required") or {})


def _c0_arm(prior: dict[str, Any]) -> dict[str, Any]:
    for a in list(prior.get("arms") or []):
        if str(a.get("architecture_id") or "") == str(C0_LOCKED["ARCHITECTURE_ID"]):
            return a
    return {}


def evidence_ok(prior: dict[str, Any], *, abs_tol: float) -> tuple[bool, list[str]]:
    req = _req(prior)
    arm = _c0_arm(prior)
    cur = dict(prior.get("current") or {})
    diag = dict(prior.get("selection_change") or {})
    fails: list[str] = []
    checks = (
        (int(cur.get("trade_count") or -1), int(CURRENT_LOCKED["TRADE_N"]), 0, "CURRENT_TRADE_N"),
        (_f(cur.get("net_pnl_yen_100")), CURRENT_LOCKED["NET"], abs_tol, "CURRENT_NET"),
        (_f(cur.get("profit_factor")), CURRENT_LOCKED["PF"], 1e-12, "CURRENT_PF"),
        (_f(cur.get("max_drawdown_yen_100")), CURRENT_LOCKED["DD"], abs_tol, "CURRENT_DD"),
        (_f(req.get("C0_NET") if req.get("C0_NET") is not None else arm.get("OVERLAY_NET_PNL")), C0_LOCKED["NET"], abs_tol, "C0_NET"),
        (_f(req.get("C0_PF") if req.get("C0_PF") is not None else arm.get("OVERLAY_PF")), C0_LOCKED["PF"], 1e-12, "C0_PF"),
        (_f(req.get("C0_DD") if req.get("C0_DD") is not None else arm.get("OVERLAY_MAX_DD")), C0_LOCKED["MAX_DD"], abs_tol, "C0_MAX_DD"),
        (_f(req.get("C0_PAIRED_MEDIAN") if req.get("C0_PAIRED_MEDIAN") is not None else arm.get("PAIRED_MEDIAN_DAILY_DELTA")), C0_LOCKED["PAIRED_MEDIAN"], abs_tol, "C0_PAIRED_MEDIAN"),
        (int(req.get("C0_POS_DAYS") if req.get("C0_POS_DAYS") is not None else arm.get("PAIRED_POS_DAYS") or -1), int(C0_LOCKED["PAIRED_POS_DAYS"]), 0, "C0_POS_DAYS"),
        (int(req.get("C0_NEG_DAYS") if req.get("C0_NEG_DAYS") is not None else arm.get("PAIRED_NEG_DAYS") or -1), int(C0_LOCKED["PAIRED_NEG_DAYS"]), 0, "C0_NEG_DAYS"),
        (int(arm.get("PAIRED_ZERO_DAYS") or -1), int(C0_LOCKED["PAIRED_ZERO_DAYS"]), 0, "C0_ZERO_DAYS"),
        (_f(req.get("C0_EX_BEST") if req.get("C0_EX_BEST") is not None else arm.get("EX_BEST_DAY_PNL_DELTA")), C0_LOCKED["EX_BEST"], abs_tol, "C0_EX_BEST"),
        (_f(req.get("C0_EX_TOP3") if req.get("C0_EX_TOP3") is not None else arm.get("EX_TOP3_DAYS_PNL_DELTA")), C0_LOCKED["EX_TOP3"], abs_tol, "C0_EX_TOP3"),
        (_f(arm.get("DELTA_PNL_VS_CURRENT")), C0_LOCKED["DELTA_NET"], abs_tol, "C0_DELTA_NET"),
        (_f(diag.get("BLOCKED_B0_COND_PNL")), BLOCKED_B0_COND_PNL, 1e-6, "BLOCKED_B0_COND_PNL"),
        (_f(diag.get("RETAINED_B0_COND_PNL")), RETAINED_B0_COND_PNL, abs_tol, "RETAINED_B0_COND_PNL"),
    )
    for got, exp, tol, name in checks:
        if isinstance(exp, int) and tol == 0:
            if int(got) != int(exp):
                fails.append(f"{name} got={got} expected={exp}")
            continue
        if got is None or not _close(got, exp, tol):
            fails.append(f"{name} got={got} expected={exp}")
    if arm.get("CURRENT_PRESERVATION_PASS") is not True:
        fails.append("CURRENT_PRESERVATION_PASS")
    if arm.get("ARM_PASS") is True:
        fails.append("ARM_PASS_MUST_BE_FALSE")
    pass_n = req.get("PASS_ARM_N")
    if pass_n is None or int(pass_n) != 0:
        fails.append("PASS_ARM_N")
    return (not fails), fails
