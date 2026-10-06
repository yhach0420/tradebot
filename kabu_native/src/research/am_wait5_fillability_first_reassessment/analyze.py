"""Parity, fill-edge, conditional quality, exposure reconstruction, CASE A-E."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.am_wait5_fillability_first_reassessment import (
    PARITY_ABS_TOL,
    PARITY_EXPECTED,
    POS_REP_MIN,
    RECON_ABS_TOL,
)
from research.am_wait5_two_stage_development.analyze import _delta_stats, _med_key
from research.canonical_entry_performance_rebase.analyze import _f
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.passive_wait_policy_reassessment.analyze import _close, _median


def freeze_parity(obs: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    for k, exp in PARITY_EXPECTED.items():
        if k == "AM_FILLABILITY_POSITIVE_REP_N":
            checks[k] = int(obs.get(k) or -1) == int(exp)
        else:
            checks[k] = _close(obs.get(k), exp, PARITY_ABS_TOL)
    return {"ok": all(checks.values()), "checks": checks, "observed": obs, "expected": dict(PARITY_EXPECTED)}


def spec_rows(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for b in bodies:
        c = b.get("CONTROL") or {}
        fo = b.get("FILL_ONLY") or {}
        d = b.get("decomp") or {}
        ttf_c = d.get("TIME_TO_FILL_CONTROL") or {}
        ttf_fo = d.get("TIME_TO_FILL_FILL_ONLY") or {}
        ttf_in = d.get("TIME_TO_FILL_FILL_ONLY_ONLY") or {}
        rec = {
            "representation_id": b.get("representation_id"),
            "feature_set": b.get("feature_set"),
            "normalization": b.get("normalization"),
            "CONTROL_FILL_RATE": c.get("SELECTED_FILL_RATE"),
            "FILL_ONLY_FILL_RATE": fo.get("SELECTED_FILL_RATE"),
            "DELTA_FILL": None
            if _f(fo.get("SELECTED_FILL_RATE")) is None or _f(c.get("SELECTED_FILL_RATE")) is None
            else float(fo["SELECTED_FILL_RATE"]) - float(c["SELECTED_FILL_RATE"]),
            "CONTROL_EXEC_U": c.get("EXEC_U"),
            "CONTROL_EXEC_D": c.get("EXEC_D"),
            "FILL_ONLY_EXEC_U": fo.get("EXEC_U"),
            "FILL_ONLY_EXEC_D": fo.get("EXEC_D"),
            "DELTA_EXEC_U": d.get("DELTA_EXEC_U"),
            "DELTA_EXEC_D": d.get("DELTA_EXEC_D"),
            "CONTROL_COND_U": c.get("COND_U_MEAN"),
            "FILL_ONLY_COND_U": fo.get("COND_U_MEAN"),
            "DELTA_COND_U": None
            if _f(fo.get("COND_U_MEAN")) is None or _f(c.get("COND_U_MEAN")) is None
            else float(fo["COND_U_MEAN"]) - float(c["COND_U_MEAN"]),
            "CONTROL_COND_D": c.get("COND_D_MEAN"),
            "FILL_ONLY_COND_D": fo.get("COND_D_MEAN"),
            "DELTA_COND_D": None
            if _f(fo.get("COND_D_MEAN")) is None or _f(c.get("COND_D_MEAN")) is None
            else float(fo["COND_D_MEAN"]) - float(c["COND_D_MEAN"]),
            "COMMON_SELECTED_N": d.get("COMMON_SELECTED_N"),
            "CONTROL_ONLY_N": d.get("CONTROL_ONLY_N"),
            "FILL_ONLY_ONLY_N": d.get("FILL_ONLY_ONLY_N"),
            "SELECTION_CHANGED_COHORT_N": d.get("SELECTION_CHANGED_COHORT_N"),
            "SELECTION_CHANGED_COHORT_RATE": d.get("SELECTION_CHANGED_COHORT_RATE"),
            "COMMON_FILL_N": d.get("COMMON_FILL_N"),
            "CONTROL_ONLY_FILL_N": d.get("CONTROL_ONLY_FILL_N"),
            "FILL_ONLY_ONLY_FILL_N": d.get("FILL_ONLY_ONLY_FILL_N"),
            "CONTROL_ONLY_FILL_RATE": d.get("CONTROL_ONLY_FILL_RATE"),
            "FILL_ONLY_ONLY_FILL_RATE": d.get("FILL_ONLY_ONLY_FILL_RATE"),
            "NET_ADDITIONAL_FILL_N": d.get("NET_ADDITIONAL_FILL_N"),
            "CONTROL_ONLY_COND_U": d.get("CONTROL_ONLY_COND_U"),
            "FILL_ONLY_ONLY_COND_U": d.get("FILL_ONLY_ONLY_COND_U"),
            "DELTA_SWAP_COND_U": d.get("DELTA_SWAP_COND_U"),
            "CONTROL_ONLY_COND_D": d.get("CONTROL_ONLY_COND_D"),
            "FILL_ONLY_ONLY_COND_D": d.get("FILL_ONLY_ONLY_COND_D"),
            "DELTA_SWAP_COND_D": d.get("DELTA_SWAP_COND_D"),
            "EXEC_U_GAIN_FROM_EXTRA_FILL": d.get("EXEC_U_GAIN_FROM_EXTRA_FILL"),
            "EXEC_U_GAIN_FROM_SELECTION_QUALITY": d.get("EXEC_U_GAIN_FROM_SELECTION_QUALITY"),
            "EXEC_D_CHANGE_FROM_EXTRA_FILL": d.get("EXEC_D_CHANGE_FROM_EXTRA_FILL"),
            "EXEC_D_CHANGE_FROM_SELECTION_QUALITY": d.get("EXEC_D_CHANGE_FROM_SELECTION_QUALITY"),
            "RECONSTRUCTION_ERROR_U": d.get("RECONSTRUCTION_ERROR_U"),
            "RECONSTRUCTION_ERROR_D": d.get("RECONSTRUCTION_ERROR_D"),
            "TTF_CONTROL_MEDIAN": ttf_c.get("MEDIAN"),
            "TTF_CONTROL_P75": ttf_c.get("P75"),
            "TTF_CONTROL_P90": ttf_c.get("P90"),
            "TTF_FILL_ONLY_MEDIAN": ttf_fo.get("MEDIAN"),
            "TTF_FILL_ONLY_P75": ttf_fo.get("P75"),
            "TTF_FILL_ONLY_P90": ttf_fo.get("P90"),
            "TTF_FILL_ONLY_ONLY_MEDIAN": ttf_in.get("MEDIAN"),
            "TTF_FILL_ONLY_ONLY_P75": ttf_in.get("P75"),
            "TTF_FILL_ONLY_ONLY_P90": ttf_in.get("P90"),
            "TTF_FILLED_MISS_N": b.get("TTF_FILLED_MISS_N"),
        }
        out.append(rec)
    return out


def _consensus_from_daily(bodies: list[dict[str, Any]], key: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by: dict[str, list[float]] = defaultdict(list)
    for b in bodies:
        for rec in (b.get("decomp") or {}).get("daily") or []:
            v = _f(rec.get(key))
            if v is None:
                continue
            by[str(rec.get("date"))].append(float(v))
    daily = []
    xs: list[float] = []
    for d in ELIGIBLE_DAYS:
        vals = by.get(d) or []
        if not vals:
            continue
        med = float(np.median(vals))
        xs.append(med)
        daily.append({"date": d, "CONSENSUS_DELTA": med, "REP_N": len(vals)})
    return daily, _delta_stats(xs)


def _nonworse_robust(rep_vals: list[float], day_st: dict[str, Any]) -> dict[str, Any]:
    med = _median(rep_vals)
    nonneg = sum(1 for v in rep_vals if v + 1e-15 >= 0.0)
    pos_days = int(day_st.get("positive_days") or 0)
    neg_days = int(day_st.get("negative_days") or 0)
    zero_days = int(day_st.get("zero_days") or 0)
    gates = {
        "MEDIAN_GE_0": bool(med is not None and med + 1e-15 >= 0.0),
        "NONNEG_REP_GE_6": bool(nonneg >= int(POS_REP_MIN)),
        "POS_OR_ZERO_GE_NEG": bool((pos_days + zero_days) >= neg_days),
        "EX_BEST_GE_0": bool(day_st.get("ex_best_day") is not None and float(day_st["ex_best_day"]) + 1e-12 >= 0.0),
        "EX_TOP3_GE_0": bool(day_st.get("ex_top3_days") is not None and float(day_st["ex_top3_days"]) + 1e-12 >= 0.0),
    }
    return {
        "MEDIAN": med,
        "NONNEG_REP_N": nonneg,
        "POSITIVE_DAYS": pos_days,
        "NEGATIVE_DAYS": neg_days,
        "ZERO_DAYS": zero_days,
        "EX_BEST_DAY": day_st.get("ex_best_day"),
        "EX_TOP3_DAYS": day_st.get("ex_top3_days"),
        "gates": gates,
        "PASS": all(gates.values()),
    }


def _worse_robust(rep_vals: list[float], day_st: dict[str, Any]) -> dict[str, Any]:
    med = _median(rep_vals)
    neg_rep = sum(1 for v in rep_vals if v < 0)
    pos_days = int(day_st.get("positive_days") or 0)
    neg_days = int(day_st.get("negative_days") or 0)
    zero_days = int(day_st.get("zero_days") or 0)
    gates = {
        "MEDIAN_LT_0": bool(med is not None and med < 0),
        "NEG_REP_GE_6": bool(neg_rep >= int(POS_REP_MIN)),
        "NEG_DAYS_GT_POS": bool(neg_days > pos_days),
        "EX_BEST_LT_0": bool(day_st.get("ex_best_day") is not None and float(day_st["ex_best_day"]) < 0),
        "EX_TOP3_LE_0": bool(day_st.get("ex_top3_days") is not None and float(day_st["ex_top3_days"]) - 1e-12 <= 0.0),
    }
    return {
        "MEDIAN": med,
        "NEGATIVE_REP_N": neg_rep,
        "POSITIVE_DAYS": pos_days,
        "NEGATIVE_DAYS": neg_days,
        "ZERO_DAYS": zero_days,
        "EX_BEST_DAY": day_st.get("ex_best_day"),
        "EX_TOP3_DAYS": day_st.get("ex_top3_days"),
        "gates": gates,
        "PASS": all(gates.values()),
    }


def _fill_edge(spec: list[dict[str, Any]], bodies: list[dict[str, Any]]) -> dict[str, Any]:
    deltas = [float(v) for r in spec if (v := _f(r.get("DELTA_FILL"))) is not None]
    pos_rep = sum(1 for v in deltas if v > 0)
    daily, st = _consensus_from_daily(bodies, "DELTA_FILL")
    med = _median(deltas)
    pos_days = int(st.get("positive_days") or 0)
    neg_days = int(st.get("negative_days") or 0)
    gates = {
        "A_MEDIAN_DELTA_GT_0": bool(med is not None and med > 0),
        "B_POSITIVE_REP_GE_6": bool(pos_rep >= int(POS_REP_MIN)),
        "C_CONSENSUS_POS_GT_NEG": bool(pos_days > neg_days),
        "D_EX_BEST_GT_0": bool(st.get("ex_best_day") is not None and float(st["ex_best_day"]) > 0),
        "E_EX_TOP3_GE_0": bool(st.get("ex_top3_days") is not None and float(st["ex_top3_days"]) + 1e-12 >= 0.0),
    }
    return {
        "MEDIAN_DELTA": med,
        "POSITIVE_REP_N": pos_rep,
        "POSITIVE_DAYS": pos_days,
        "NEGATIVE_DAYS": neg_days,
        "ZERO_DAYS": st.get("zero_days"),
        "EX_BEST_DAY": st.get("ex_best_day"),
        "EX_TOP3_DAYS": st.get("ex_top3_days"),
        "gates": gates,
        "PASS": all(gates.values()),
        "daily": daily,
    }


def recon_violations(spec: list[dict[str, Any]]) -> dict[str, Any]:
    n_u = n_d = 0
    for r in spec:
        eu = _f(r.get("RECONSTRUCTION_ERROR_U"))
        ed = _f(r.get("RECONSTRUCTION_ERROR_D"))
        if eu is None or abs(float(eu)) > float(RECON_ABS_TOL):
            n_u += 1
        if ed is None or abs(float(ed)) > float(RECON_ABS_TOL):
            n_d += 1
    return {
        "RECONSTRUCTION_ERROR_U_VIOLATION_N": n_u,
        "RECONSTRUCTION_ERROR_D_VIOLATION_N": n_d,
        "RECONSTRUCTION_ERROR_VIOLATION_N": n_u + n_d,
        "PASS": n_u == 0 and n_d == 0,
    }


def quality_pack(spec: list[dict[str, Any]], bodies: list[dict[str, Any]]) -> dict[str, Any]:
    du = [float(v) for r in spec if (v := _f(r.get("DELTA_COND_U"))) is not None]
    dd = [float(v) for r in spec if (v := _f(r.get("DELTA_COND_D"))) is not None]
    su = [float(v) for r in spec if (v := _f(r.get("DELTA_SWAP_COND_U"))) is not None]
    sd = [float(v) for r in spec if (v := _f(r.get("DELTA_SWAP_COND_D"))) is not None]
    u_daily, ust = _consensus_from_daily(bodies, "DELTA_COND_U")
    d_daily, dst = _consensus_from_daily(bodies, "DELTA_COND_D")
    su_daily, sust = _consensus_from_daily(bodies, "DELTA_SWAP_COND_U")
    sd_daily, sdst = _consensus_from_daily(bodies, "DELTA_SWAP_COND_D")
    return {
        "COND_U_NONNEG_REP_N": sum(1 for v in du if v + 1e-15 >= 0.0),
        "COND_D_NONNEG_REP_N": sum(1 for v in dd if v + 1e-15 >= 0.0),
        "U_NONWORSE": _nonworse_robust(du, ust),
        "D_NONWORSE": _nonworse_robust(dd, dst),
        "U_WORSE": _worse_robust(du, ust),
        "D_WORSE": _worse_robust(dd, dst),
        "SWAP_U_NONWORSE": _nonworse_robust(su, sust),
        "SWAP_D_NONWORSE": _nonworse_robust(sd, sdst),
        "SWAP_U_WORSE": _worse_robust(su, sust),
        "SWAP_D_WORSE": _worse_robust(sd, sdst),
        "daily_fill": None,
        "daily_cond_u": u_daily,
        "daily_cond_d": d_daily,
        "daily_swap_u": su_daily,
        "daily_swap_d": sd_daily,
        "swap_u_stats": sust,
        "swap_d_stats": sdst,
    }


def decide(
    *,
    parity_ok: bool,
    fill_edge: dict[str, Any],
    quality: dict[str, Any],
    recon: dict[str, Any],
    leak: dict[str, Any],
) -> dict[str, Any]:
    leak_bad = any(int(leak.get(k) or 0) != 0 for k in leak)
    if (not parity_ok) or leak_bad or (not recon.get("PASS")):
        return {
            "CASE": "E",
            "PRIMARY_MECHANISM": "INTEGRITY_FAIL",
            "AM_ARCHITECTURE": "NONE",
            "NEXT_RESEARCH": "NONE",
            "VERDICT": "AM_WAIT5_FILLABILITY_REASSESSMENT_INTEGRITY_FAILED",
            "PRIMARY_FINDING": "Parity, isolation, or reconstruction-error integrity failed.",
            "note": "CASE E. STOP.",
        }
    if not fill_edge.get("PASS"):
        return {
            "CASE": "D",
            "PRIMARY_MECHANISM": "FILL_EDGE_NOT_REPRODUCED",
            "AM_ARCHITECTURE": "NONE",
            "NEXT_RESEARCH": "AM_ENTRY_INFORMATION_REASSESSMENT",
            "VERDICT": "AM_WAIT5_FILLABILITY_EDGE_NOT_REPRODUCED",
            "PRIMARY_FINDING": "CONTROL→FILL_ONLY fill edge did not reproduce prior frozen robustness.",
            "note": "CASE D. STOP. No Stage2 restart. No new model.",
        }
    u_worse = bool((quality.get("U_WORSE") or {}).get("PASS"))
    d_worse = bool((quality.get("D_WORSE") or {}).get("PASS"))
    u_ok = bool((quality.get("U_NONWORSE") or {}).get("PASS"))
    d_ok = bool((quality.get("D_NONWORSE") or {}).get("PASS"))
    swap_u_worse = bool((quality.get("SWAP_U_WORSE") or {}).get("PASS"))
    swap_d_worse = bool((quality.get("SWAP_D_WORSE") or {}).get("PASS"))
    u_med = _f((quality.get("U_NONWORSE") or {}).get("MEDIAN"))
    d_med = _f((quality.get("D_NONWORSE") or {}).get("MEDIAN"))
    u_tax = bool(u_worse or (swap_u_worse and u_med is not None and float(u_med) < 0))
    d_tax = bool(d_worse or (swap_d_worse and d_med is not None and float(d_med) < 0))
    if u_tax:
        return {
            "CASE": "C",
            "PRIMARY_MECHANISM": "FILLABILITY_GAIN_CARRIES_GENERAL_QUALITY_TAX",
            "AM_ARCHITECTURE": "NONE",
            "NEXT_RESEARCH": "AM_ENTRY_ARCHITECTURE_REASSESSMENT",
            "VERDICT": "AM_WAIT5_FILLABILITY_QUALITY_TAX",
            "PRIMARY_FINDING": (
                "Fill edge is robust, but filled-candidate conditional U worsens vs CONTROL "
                "and swapped-in fills carry a robust U quality tax."
            ),
            "note": "CASE C. STOP. Stage2 restart forbidden. No new model.",
        }
    if d_tax and not u_tax:
        return {
            "CASE": "B",
            "PRIMARY_MECHANISM": "FILLABILITY_GAIN_CARRIES_DOWNSIDE_QUALITY_TAX",
            "AM_ARCHITECTURE": "NONE",
            "NEXT_RESEARCH": "AM_ENTRY_EXIT_COUPLING_REASSESSMENT",
            "VERDICT": "AM_WAIT5_FILLABILITY_DOWNSIDE_TAX",
            "PRIMARY_FINDING": (
                "Fill edge is robust and conditional U is maintained, but filled-candidate "
                "conditional D worsens robustly vs CONTROL. This is not extra-fill exposure alone."
            ),
            "note": "CASE B. STOP. Stage2 restart forbidden. No new model.",
        }
    if u_ok and d_ok:
        return {
            "CASE": "A",
            "PRIMARY_MECHANISM": "MORE_FILL_WITHOUT_FILLED_QUALITY_TAX",
            "AM_ARCHITECTURE": "FILLABILITY_FIRST",
            "NEXT_RESEARCH": "AM_WAIT5_FILLABILITY_FINAL_SPEC_PRECOMMIT",
            "VERDICT": "AM_WAIT5_FILLABILITY_FIRST_SUPPORTED",
            "PRIMARY_FINDING": (
                "FILL_ONLY keeps a robust fill edge vs CONTROL without a robust filled-candidate "
                "U/D quality tax. EXEC_D movement is extra-fill exposure, not worse filled names."
            ),
            "note": "CASE A. STOP. No Exact. No PnL. No W5 runtime adoption.",
        }
    return {
        "CASE": "C",
        "PRIMARY_MECHANISM": "FILLABILITY_GAIN_CARRIES_GENERAL_QUALITY_TAX",
        "AM_ARCHITECTURE": "NONE",
        "NEXT_RESEARCH": "AM_ENTRY_ARCHITECTURE_REASSESSMENT",
        "VERDICT": "AM_WAIT5_FILLABILITY_QUALITY_TAX",
        "PRIMARY_FINDING": (
            "Fill edge is robust, but filled-candidate conditional U is not non-worse vs CONTROL. "
            "FILL_ONLY is not supported as a standalone ENTRY architecture."
        ),
        "note": "CASE C. STOP. Stage2 restart forbidden. No new model.",
    }
