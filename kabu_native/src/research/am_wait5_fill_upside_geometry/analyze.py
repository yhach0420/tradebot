"""Parity, 9-rep median, day-robust oracle gates, CASE A-E. No training."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.am_wait5_fill_upside_geometry import MAJORITY_RATE, PARITY_ABS_TOL, PARITY_EXPECTED, RANK_BUCKETS
from research.am_wait5_two_stage_development.analyze import _delta_stats, _med_key
from research.canonical_entry_performance_rebase.analyze import _f
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.passive_wait_policy_reassessment.analyze import _close


def freeze_parity(obs: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    for k, exp in PARITY_EXPECTED.items():
        if k == "NET_ADDITIONAL_FILL_N":
            v = _f(obs.get(k))
            checks[k] = v is not None and abs(float(v) - float(exp)) < float(PARITY_ABS_TOL)
        else:
            checks[k] = _close(obs.get(k), exp, PARITY_ABS_TOL)
    return {"ok": all(checks.values()), "checks": checks, "observed": obs, "expected": dict(PARITY_EXPECTED)}


def spec_rows(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for b in bodies:
        c = b.get("CONTROL") or {}
        fo = b.get("FILL_ONLY") or {}
        sp = b.get("spearman") or {}
        bk = b.get("buckets") or {}
        sw = b.get("swap") or {}
        oc = b.get("oracle") or {}
        cout = sw.get("CONTROL_ONLY") or {}
        cin = sw.get("FILL_ONLY_ONLY") or {}
        rec = {
            "representation_id": b.get("representation_id"),
            "feature_set": b.get("feature_set"),
            "normalization": b.get("normalization"),
            "CONTROL_FILL_RATE": c.get("SELECTED_FILL_RATE"),
            "FILL_ONLY_FILL_RATE": fo.get("SELECTED_FILL_RATE"),
            "NET_ADDITIONAL_FILL_N": b.get("NET_ADDITIONAL_FILL_N"),
            "CONTROL_COND_U": b.get("CONTROL_COND_U"),
            "FILL_ONLY_COND_U": b.get("FILL_ONLY_COND_U"),
            "DELTA_COND_U": b.get("DELTA_COND_U"),
            "CONTROL_COND_D": b.get("CONTROL_COND_D"),
            "FILL_ONLY_COND_D": b.get("FILL_ONLY_COND_D"),
            "DELTA_COND_D": b.get("DELTA_COND_D"),
            "P_FILL_U_SPEARMAN": sp.get("P_FILL_U_OVERALL"),
            "P_FILL_D_SPEARMAN": sp.get("P_FILL_D_OVERALL"),
            "P_FILL_U_DAILY_MEDIAN": sp.get("P_FILL_U_DAILY_MEDIAN"),
            "P_FILL_D_DAILY_MEDIAN": sp.get("P_FILL_D_DAILY_MEDIAN"),
            "P_FILL_U_COHORT_MEDIAN": sp.get("P_FILL_U_COHORT_MEDIAN"),
            "P_FILL_D_COHORT_MEDIAN": sp.get("P_FILL_D_COHORT_MEDIAN"),
            "U_POS_DAYS": sp.get("U_POS_DAYS"),
            "U_NEG_DAYS": sp.get("U_NEG_DAYS"),
            "U_ZERO_DAYS": sp.get("U_ZERO_DAYS"),
            "D_POS_DAYS": sp.get("D_POS_DAYS"),
            "D_NEG_DAYS": sp.get("D_NEG_DAYS"),
            "D_ZERO_DAYS": sp.get("D_ZERO_DAYS"),
            "CONTROL_ONLY_FILL_RATE": cout.get("FILL_RATE"),
            "FILL_ONLY_ONLY_FILL_RATE": cin.get("FILL_RATE"),
            "CONTROL_ONLY_COND_U": cout.get("COND_U"),
            "FILL_ONLY_ONLY_COND_U": cin.get("COND_U"),
            "CONTROL_ONLY_COND_D": cout.get("COND_D"),
            "FILL_ONLY_ONLY_COND_D": cin.get("COND_D"),
            "CONTROL_ONLY_EXEC_U": cout.get("EXEC_U"),
            "FILL_ONLY_ONLY_EXEC_U": cin.get("EXEC_U"),
            "CONTROL_ONLY_EXEC_D": cout.get("EXEC_D"),
            "FILL_ONLY_ONLY_EXEC_D": cin.get("EXEC_D"),
            "FILL_NONWORSE_U_IMPROVE_COHORT_N": oc.get("FILL_NONWORSE_U_IMPROVE_COHORT_N"),
            "FILL_NONWORSE_U_IMPROVE_RATE": oc.get("FILL_NONWORSE_U_IMPROVE_RATE"),
            "SAME_FILL_U_IMPROVE_COHORT_N": oc.get("SAME_FILL_U_IMPROVE_COHORT_N"),
            "SAME_FILL_U_IMPROVE_RATE": oc.get("SAME_FILL_U_IMPROVE_RATE"),
            "FILL_NONWORSE_U_D_NONWORSE_COHORT_N": oc.get("FILL_NONWORSE_U_D_NONWORSE_COHORT_N"),
            "FILL_NONWORSE_U_D_NONWORSE_RATE": oc.get("FILL_NONWORSE_U_D_NONWORSE_RATE"),
            "SAME_FILL_U_D_NONWORSE_COHORT_N": oc.get("SAME_FILL_U_D_NONWORSE_COHORT_N"),
            "SAME_FILL_U_D_NONWORSE_RATE": oc.get("SAME_FILL_U_D_NONWORSE_RATE"),
            "ORACLE_EXEC_U_DELTA_MEDIAN": oc.get("ORACLE_EXEC_U_DELTA_MEDIAN"),
            "ORACLE_EXEC_U_DELTA_MEAN": oc.get("ORACLE_EXEC_U_DELTA_MEAN"),
            "FILL_ONLY_DOMINATED_IN_FILL_U_COHORT_N": oc.get("FILL_ONLY_DOMINATED_IN_FILL_U_COHORT_N"),
            "FILL_ONLY_DOMINATED_IN_FILL_U_RATE": oc.get("FILL_ONLY_DOMINATED_IN_FILL_U_RATE"),
            "ORACLE_SUBSET_ENUMERATION_ERROR_N": oc.get("ORACLE_SUBSET_ENUMERATION_ERROR_N"),
            "COHORT_N": oc.get("COHORT_N"),
        }
        for name in RANK_BUCKETS:
            pack = bk.get(name) or {}
            rec[f"{name}_FILL_RATE"] = pack.get("FILL_RATE")
            rec[f"{name}_COND_U"] = pack.get("COND_U_MEAN")
            rec[f"{name}_COND_U_MEDIAN"] = pack.get("COND_U_MEDIAN")
            rec[f"{name}_COND_D"] = pack.get("COND_D_MEAN")
            rec[f"{name}_EXEC_U"] = pack.get("EXEC_U_MEAN")
            rec[f"{name}_EXEC_D"] = pack.get("EXEC_D_MEAN")
            rec[f"{name}_CANDIDATE_N"] = pack.get("CANDIDATE_N")
        out.append(rec)
    return out


def _consensus_from_oracle_daily(bodies: list[dict[str, Any]], key: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by: dict[str, list[float]] = defaultdict(list)
    for b in bodies:
        for rec in (b.get("oracle") or {}).get("daily") or []:
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
        daily.append({"date": d, "CONSENSUS": med, "REP_N": len(vals)})
    return daily, _delta_stats(xs)


def _consensus_spearman(bodies: list[dict[str, Any]], which: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    key = "daily_u" if which == "U" else "daily_d"
    by: dict[str, list[float]] = defaultdict(list)
    for b in bodies:
        for rec in (b.get("spearman") or {}).get(key) or []:
            v = _f(rec.get("SPEARMAN"))
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
        daily.append({"date": d, "CONSENSUS": med, "REP_N": len(vals)})
    st = _delta_stats(xs)
    return daily, st


def oracle_day_robust(bodies: list[dict[str, Any]]) -> dict[str, Any]:
    daily, st = _consensus_from_oracle_daily(bodies, "ORACLE_EXEC_U_DELTA")
    med = st.get("median")
    pos = int(st.get("positive_days") or 0)
    neg = int(st.get("negative_days") or 0)
    gates = {
        "MEDIAN_GT_0": bool(med is not None and float(med) > 0),
        "POS_DAYS_GT_NEG": bool(pos > neg),
        "EX_BEST_GT_0": bool(st.get("ex_best_day") is not None and float(st["ex_best_day"]) > 0),
        "EX_TOP3_GE_0": bool(st.get("ex_top3_days") is not None and float(st["ex_top3_days"]) + 1e-12 >= 0.0),
    }
    return {
        "MEDIAN": med,
        "MEAN": st.get("mean"),
        "POSITIVE_DAYS": pos,
        "NEGATIVE_DAYS": neg,
        "ZERO_DAYS": st.get("zero_days"),
        "EX_BEST_DAY": st.get("ex_best_day"),
        "EX_TOP3_DAYS": st.get("ex_top3_days"),
        "gates": gates,
        "PASS": all(gates.values()),
        "daily": daily,
    }


def decide(
    *,
    parity_ok: bool,
    leak: dict[str, Any],
    fill_nonworse_rate: Optional[float],
    same_fill_rate: Optional[float],
    fill_nonworse_ud_rate: Optional[float],
    day_robust: dict[str, Any],
) -> dict[str, Any]:
    if (not parity_ok) or any(int(leak.get(k) or 0) != 0 for k in leak):
        return {
            "CASE": "E",
            "PRIMARY_MECHANISM": "INTEGRITY_FAIL",
            "NEXT_RESEARCH": "NONE",
            "VERDICT": "AM_FILL_UPSIDE_GEOMETRY_INTEGRITY_FAILED",
            "PRIMARY_FINDING": "Parity or isolation/oracle-enumeration integrity failed.",
            "note": "CASE E. STOP.",
        }
    fn = float(fill_nonworse_rate) if fill_nonworse_rate is not None else None
    sm = float(same_fill_rate) if same_fill_rate is not None else None
    ud = float(fill_nonworse_ud_rate) if fill_nonworse_ud_rate is not None else None
    maj = float(MAJORITY_RATE)
    u_maj = fn is not None and fn + 1e-15 >= maj
    same_maj = sm is not None and sm + 1e-15 >= maj
    ud_maj = ud is not None and ud + 1e-15 >= maj
    robust = bool(day_robust.get("PASS"))
    if u_maj and robust:
        return {
            "CASE": "A",
            "PRIMARY_MECHANISM": "JOINT_FILL_UPSIDE_OPPORTUNITY_EXISTS_OBJECTIVE_MISALIGNED",
            "NEXT_RESEARCH": "AM_DIRECT_EXECUTION_ADJUSTED_UPSIDE_TARGET_PRECOMMIT",
            "VERDICT": "AM_FILL_UPSIDE_JOINT_OPPORTUNITY_EXISTS",
            "PRIMARY_FINDING": (
                "A fill-preserving EXEC_U-improving Top3 exists in a majority of AM cohorts "
                "and the oracle U delta is day-robust. P_FILL5 is not identifying that set."
            ),
            "note": "CASE A. STOP. Oracle is diagnostic only. No EXEC_U training this run.",
        }
    if u_maj and (not ud_maj):
        return {
            "CASE": "C",
            "PRIMARY_MECHANISM": "UPSIDE_OPPORTUNITY_EXISTS_WITH_EXPOSURE_TRADEOFF",
            "NEXT_RESEARCH": "AM_ENTRY_EXIT_COUPLING_REASSESSMENT",
            "VERDICT": "AM_FILL_UPSIDE_EXIT_COUPLING_REQUIRED",
            "PRIMARY_FINDING": (
                "Fill-preserving U improvement is majority-available, but requiring EXEC_D "
                "non-worsening loses majority."
            ),
            "note": "CASE C. STOP. D is sanity only. No new model.",
        }
    if (fn is not None and fn < maj) and (sm is not None and sm < maj):
        return {
            "CASE": "B",
            "PRIMARY_MECHANISM": "FILL_UPSIDE_STRUCTURAL_FRONTIER",
            "NEXT_RESEARCH": "AM_ENTRY_EXECUTION_POLICY_COUPLING_REASSESSMENT",
            "VERDICT": "AM_FILL_UPSIDE_STRUCTURAL_CONFLICT",
            "PRIMARY_FINDING": (
                "Under current W5 execution, fill-preserving and same-fill EXEC_U-improving "
                "Top3 sets are both below majority. This is a structural frontier, not a model gap."
            ),
            "note": "CASE B. STOP. No new model. No Stage2 restart.",
        }
    return {
        "CASE": "D",
        "PRIMARY_MECHANISM": "FILL_UPSIDE_GEOMETRY_MIXED",
        "NEXT_RESEARCH": "AM_ENTRY_ARCHITECTURE_HOLD",
        "VERDICT": "AM_FILL_UPSIDE_ARCHITECTURE_INCONCLUSIVE",
        "PRIMARY_FINDING": "Oracle geometry is mixed. Post-hoc model start is forbidden.",
        "note": "CASE D. STOP. No new model. No Stage2 restart.",
    }
