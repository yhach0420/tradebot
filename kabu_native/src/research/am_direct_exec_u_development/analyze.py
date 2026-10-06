"""Paired representation median, daily consensus, fill/U/D gates, CASE A-E."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.am_direct_exec_u_development import PARITY_ABS_TOL, PARITY_EXPECTED
from research.am_wait5_two_stage_development.analyze import _consensus_delta, _med_key
from research.canonical_entry_performance_rebase.analyze import _f
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.passive_wait_policy_reassessment.analyze import _close, _median
from research.wait5_session_target_learnability import POS_REP_MIN


def freeze_parity(obs: dict[str, Any]) -> dict[str, Any]:
    checks = {k: _close(obs.get(k), exp, PARITY_ABS_TOL) for k, exp in PARITY_EXPECTED.items()}
    return {"ok": all(checks.values()), "checks": checks, "observed": obs, "expected": dict(PARITY_EXPECTED)}


def _delta(a: Any, b: Any) -> Optional[float]:
    va = _f(a)
    vb = _f(b)
    if va is None or vb is None:
        return None
    return float(va) - float(vb)


def spec_rows(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for b in bodies:
        c = b.get("CONTROL") or {}
        fo = b.get("FILL_ONLY") or {}
        du = b.get("DIRECT_EXEC_U") or {}
        dist = b.get("distinctness") or {}
        sp = b.get("spearman") or {}
        rec = {
            "representation_id": b.get("representation_id"),
            "feature_set": b.get("feature_set"),
            "normalization": b.get("normalization"),
            "outer_folds": b.get("outer_folds"),
            "CONTROL_FILL_RATE": c.get("SELECTED_FILL_RATE"),
            "FILL_ONLY_FILL_RATE": fo.get("SELECTED_FILL_RATE"),
            "DIRECT_EXEC_U_FILL_RATE": du.get("SELECTED_FILL_RATE"),
            "CONTROL_SELECTED_N": c.get("SELECTED_N"),
            "FILL_ONLY_SELECTED_N": fo.get("SELECTED_N"),
            "DIRECT_SELECTED_N": du.get("SELECTED_N"),
            "CONTROL_WOULD_FILL_N": c.get("WOULD_FILL_N"),
            "FILL_ONLY_WOULD_FILL_N": fo.get("WOULD_FILL_N"),
            "DIRECT_WOULD_FILL_N": du.get("WOULD_FILL_N"),
            "CONTROL_ANY_FILL_COHORT_RATE": c.get("ANY_SELECTED_FILL_COHORT_RATE"),
            "FILL_ONLY_ANY_FILL_COHORT_RATE": fo.get("ANY_SELECTED_FILL_COHORT_RATE"),
            "DIRECT_ANY_FILL_COHORT_RATE": du.get("ANY_SELECTED_FILL_COHORT_RATE"),
            "CONTROL_MEAN_FILLS_PER_COHORT": c.get("MEAN_FILLS_PER_COHORT"),
            "FILL_ONLY_MEAN_FILLS_PER_COHORT": fo.get("MEAN_FILLS_PER_COHORT"),
            "DIRECT_MEAN_FILLS_PER_COHORT": du.get("MEAN_FILLS_PER_COHORT"),
            "DELTA_FILL_VS_FILL_ONLY": _delta(du.get("SELECTED_FILL_RATE"), fo.get("SELECTED_FILL_RATE")),
            "DELTA_FILL_VS_CONTROL": _delta(du.get("SELECTED_FILL_RATE"), c.get("SELECTED_FILL_RATE")),
            "CONTROL_EXEC_U": c.get("EXEC_U"),
            "FILL_ONLY_EXEC_U": fo.get("EXEC_U"),
            "DIRECT_EXEC_U_EXEC_U": du.get("EXEC_U"),
            "CONTROL_EXEC_D": c.get("EXEC_D"),
            "FILL_ONLY_EXEC_D": fo.get("EXEC_D"),
            "DIRECT_EXEC_U_EXEC_D": du.get("EXEC_D"),
            "DELTA_EXEC_U_VS_FILL_ONLY": _delta(du.get("EXEC_U"), fo.get("EXEC_U")),
            "DELTA_EXEC_U_VS_CONTROL": _delta(du.get("EXEC_U"), c.get("EXEC_U")),
            "DELTA_EXEC_D_VS_FILL_ONLY": _delta(du.get("EXEC_D"), fo.get("EXEC_D")),
            "CONTROL_COND_U": c.get("COND_U_MEAN"),
            "FILL_ONLY_COND_U": fo.get("COND_U_MEAN"),
            "DIRECT_COND_U": du.get("COND_U_MEAN"),
            "CONTROL_COND_D": c.get("COND_D_MEAN"),
            "FILL_ONLY_COND_D": fo.get("COND_D_MEAN"),
            "DIRECT_COND_D": du.get("COND_D_MEAN"),
            "DELTA_COND_U_VS_FILL_ONLY": _delta(du.get("COND_U_MEAN"), fo.get("COND_U_MEAN")),
            "DELTA_COND_D_VS_FILL_ONLY": _delta(du.get("COND_D_MEAN"), fo.get("COND_D_MEAN")),
            "OVERALL_OOF_SPEARMAN": sp.get("OVERALL_OOF_SPEARMAN"),
            "DAILY_MEDIAN_SPEARMAN": sp.get("DAILY_MEDIAN_SPEARMAN"),
            **dist,
        }
        out.append(rec)
    return out


def spearman_consensus(bodies: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by: dict[str, list[float]] = defaultdict(list)
    for b in bodies:
        for rec in b.get("spearman_daily") or []:
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
        daily.append({"date": d, "CONSENSUS_SPEARMAN": med, "REP_N": len(vals)})
    return daily, {
        "DAILY_MEDIAN_SPEARMAN": _median(xs),
        "POSITIVE_SPEARMAN_DAYS": sum(1 for v in xs if v > 0),
        "NEGATIVE_SPEARMAN_DAYS": sum(1 for v in xs if v < 0),
        "ZERO_SPEARMAN_DAYS": sum(1 for v in xs if v == 0),
    }


def fill_edge_gate(spec: list[dict[str, Any]], bodies: list[dict[str, Any]]) -> dict[str, Any]:
    direct = _med_key(spec, "DIRECT_EXEC_U_FILL_RATE")
    fo = _med_key(spec, "FILL_ONLY_FILL_RATE")
    deltas = [float(v) for r in spec if (v := _f(r.get("DELTA_FILL_VS_FILL_ONLY"))) is not None]
    paired = _median(deltas)
    nonneg = sum(1 for v in deltas if v + 1e-15 >= 0.0)
    daily, st = _consensus_delta(bodies, "DIRECT_EXEC_U", "FILL_ONLY", "SELECTED_FILL_RATE")
    pos = int(st.get("positive_days") or 0)
    neg = int(st.get("negative_days") or 0)
    zero = int(st.get("zero_days") or 0)
    ex_best = st.get("ex_best_day")
    ex_top3 = st.get("ex_top3_days")
    gates = {
        "A_DIRECT_HEADLINE_FILL_GE_FILL_ONLY": bool(
            direct is not None and fo is not None and float(direct) + 1e-15 >= float(fo)
        ),
        "B_PAIRED_MEDIAN_DELTA_FILL_GE_0": bool(paired is not None and float(paired) + 1e-15 >= 0.0),
        "C_FILL_NONNEG_REP_GE_6": bool(nonneg >= int(POS_REP_MIN)),
        "D_POS_OR_ZERO_GT_NEG": bool((pos + zero) > neg),
        "E_EX_BEST_GE_0": bool(ex_best is not None and float(ex_best) + 1e-15 >= 0.0),
        "F_EX_TOP3_GE_0": bool(ex_top3 is not None and float(ex_top3) + 1e-15 >= 0.0),
    }
    return {
        "DIRECT_EXEC_U_FILL_RATE": direct,
        "FILL_ONLY_FILL_RATE": fo,
        "DELTA_FILL_VS_FILL_ONLY": paired,
        "FILL_NONNEG_REP_N": nonneg,
        "FILL_POS_DAYS": pos,
        "FILL_ZERO_DAYS": zero,
        "FILL_NEG_DAYS": neg,
        "FILL_EX_BEST_DAY": ex_best,
        "FILL_EX_TOP3_DAYS": ex_top3,
        "gates": gates,
        "PASS": all(gates.values()),
        "daily": daily,
    }


def upside_gate(spec: list[dict[str, Any]], bodies: list[dict[str, Any]]) -> dict[str, Any]:
    deltas = [float(v) for r in spec if (v := _f(r.get("DELTA_EXEC_U_VS_FILL_ONLY"))) is not None]
    paired = _median(deltas)
    pos_rep = sum(1 for v in deltas if v > 0)
    daily, st = _consensus_delta(bodies, "DIRECT_EXEC_U", "FILL_ONLY", "EXEC_U")
    pos = int(st.get("positive_days") or 0)
    neg = int(st.get("negative_days") or 0)
    zero = int(st.get("zero_days") or 0)
    ex_best = st.get("ex_best_day")
    ex_top3 = st.get("ex_top3_days")
    gates = {
        "A_PAIRED_MEDIAN_DELTA_EXEC_U_GT_0": bool(paired is not None and float(paired) > 0),
        "B_EXEC_U_POS_REP_GE_6": bool(pos_rep >= int(POS_REP_MIN)),
        "C_POS_GT_NEG": bool(pos > neg),
        "D_EX_BEST_GT_0": bool(ex_best is not None and float(ex_best) > 0),
        "E_EX_TOP3_GE_0": bool(ex_top3 is not None and float(ex_top3) + 1e-12 >= 0.0),
    }
    return {
        "DELTA_EXEC_U_VS_FILL_ONLY": paired,
        "EXEC_U_POS_REP_N": pos_rep,
        "EXEC_U_POS_DAYS": pos,
        "EXEC_U_ZERO_DAYS": zero,
        "EXEC_U_NEG_DAYS": neg,
        "EXEC_U_EX_BEST_DAY": ex_best,
        "EXEC_U_EX_TOP3_DAYS": ex_top3,
        "gates": gates,
        "PASS": all(gates.values()),
        "daily": daily,
    }


def d_nonworse_gate(spec: list[dict[str, Any]], bodies: list[dict[str, Any]]) -> dict[str, Any]:
    deltas = [float(v) for r in spec if (v := _f(r.get("DELTA_EXEC_D_VS_FILL_ONLY"))) is not None]
    paired = _median(deltas)
    nonneg = sum(1 for v in deltas if v + 1e-15 >= 0.0)
    daily, st = _consensus_delta(bodies, "DIRECT_EXEC_U", "FILL_ONLY", "EXEC_D")
    pos = int(st.get("positive_days") or 0)
    neg = int(st.get("negative_days") or 0)
    zero = int(st.get("zero_days") or 0)
    ex_best = st.get("ex_best_day")
    ex_top3 = st.get("ex_top3_days")
    gates = {
        "A_PAIRED_MEDIAN_DELTA_EXEC_D_GE_0": bool(paired is not None and float(paired) + 1e-15 >= 0.0),
        "B_EXEC_D_NONNEG_REP_GE_6": bool(nonneg >= int(POS_REP_MIN)),
        "C_POS_OR_ZERO_GE_NEG": bool((pos + zero) >= neg),
        "D_EX_BEST_GE_0": bool(ex_best is not None and float(ex_best) + 1e-15 >= 0.0),
        "E_EX_TOP3_GE_0": bool(ex_top3 is not None and float(ex_top3) + 1e-15 >= 0.0),
    }
    return {
        "DELTA_EXEC_D_VS_FILL_ONLY": paired,
        "EXEC_D_NONNEG_REP_N": nonneg,
        "EXEC_D_POS_DAYS": pos,
        "EXEC_D_ZERO_DAYS": zero,
        "EXEC_D_NEG_DAYS": neg,
        "EXEC_D_EX_BEST_DAY": ex_best,
        "EXEC_D_EX_TOP3_DAYS": ex_top3,
        "gates": gates,
        "PASS": all(gates.values()),
        "daily": daily,
    }


def fill_vs_control_pack(spec: list[dict[str, Any]], bodies: list[dict[str, Any]]) -> dict[str, Any]:
    deltas = [float(v) for r in spec if (v := _f(r.get("DELTA_FILL_VS_CONTROL"))) is not None]
    daily, st = _consensus_delta(bodies, "DIRECT_EXEC_U", "CONTROL", "SELECTED_FILL_RATE")
    return {
        "DELTA_FILL_VS_CONTROL": _median(deltas),
        "daily": daily,
        "stats": st,
    }


def sum_integrity(bodies: list[dict[str, Any]]) -> dict[str, int]:
    keys = (
        "PM_ROWS_USED_N",
        "FUTURE_EVENT_USE_N",
        "TARGET_CONTAMINATION_N",
        "HELDOUT_FIT_LEAK_N",
        "NORMALIZER_HELDOUT_ROW_N",
        "TARGET_MISSING_N",
        "MODEL_HYPERPARAMETER_SEARCH_N",
        "STAGE1_USE_N",
        "STAGE2_USE_N",
        "ORACLE_SELECTION_USE_N",
        "JOIN_FILL_SCORE_MISS_N",
    )
    tot = {k: 0 for k in keys}
    for b in bodies:
        leak = b.get("integrity") or {}
        for k in keys:
            tot[k] += int(leak.get(k) or 0)
    return tot


def decide(
    *,
    fill_gate: dict[str, Any],
    u_gate: dict[str, Any],
    d_gate: dict[str, Any],
    leak: dict[str, int],
) -> dict[str, Any]:
    must0 = (
        "PM_ROWS_USED_N",
        "FUTURE_EVENT_USE_N",
        "TARGET_CONTAMINATION_N",
        "HELDOUT_FIT_LEAK_N",
        "NORMALIZER_HELDOUT_ROW_N",
        "TARGET_MISSING_N",
        "MODEL_HYPERPARAMETER_SEARCH_N",
        "STAGE1_USE_N",
        "STAGE2_USE_N",
        "ORACLE_SELECTION_USE_N",
        "JOIN_FILL_SCORE_MISS_N",
    )
    if any(int(leak.get(k) or 0) != 0 for k in must0):
        return {
            "CASE": "E",
            "DIRECT_FILL_EDGE_PASS": False,
            "DIRECT_EXEC_U_UPSIDE_PASS": False,
            "DIRECT_EXEC_D_NONWORSE": False,
            "DIRECT_EXEC_U_DEVELOPMENT_PASS": False,
            "VERDICT": "AM_DIRECT_EXEC_U_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": "Session isolation, target construction, or held-out-fit integrity failed.",
            "note": "CASE E. STOP.",
        }
    fill_ok = bool(fill_gate.get("PASS"))
    u_ok = bool(u_gate.get("PASS"))
    d_ok = bool(d_gate.get("PASS"))
    if fill_ok and u_ok and d_ok:
        return {
            "CASE": "A",
            "DIRECT_FILL_EDGE_PASS": True,
            "DIRECT_EXEC_U_UPSIDE_PASS": True,
            "DIRECT_EXEC_D_NONWORSE": True,
            "DIRECT_EXEC_U_DEVELOPMENT_PASS": True,
            "VERDICT": "AM_DIRECT_EXEC_U_DEVELOPMENT_SUPPORTED",
            "NEXT_RESEARCH": "AM_DIRECT_EXEC_U_FINAL_SPEC_PRECOMMIT",
            "PRIMARY_FINDING": (
                "DIRECT pred_EXEC_U Top3 keeps the FILL_ONLY fill edge and adds robust "
                "execution-adjusted upside without worsening EXEC_D under 9-rep paired median "
                "and daily consensus."
            ),
            "note": "CASE A. STOP. No Exact. No PnL. No final model adopt.",
        }
    if u_ok and (not fill_ok):
        return {
            "CASE": "B",
            "DIRECT_FILL_EDGE_PASS": False,
            "DIRECT_EXEC_U_UPSIDE_PASS": True,
            "DIRECT_EXEC_D_NONWORSE": d_ok,
            "DIRECT_EXEC_U_DEVELOPMENT_PASS": False,
            "VERDICT": "AM_DIRECT_EXEC_U_SACRIFICES_FILL",
            "NEXT_RESEARCH": "AM_ENTRY_EXECUTION_POLICY_COUPLING_REASSESSMENT",
            "PRIMARY_FINDING": (
                "DIRECT improves EXEC_U vs FILL_ONLY but fails the fill hard gate. "
                "Fill-loss tolerance is forbidden."
            ),
            "note": "CASE B. STOP. No fill-loss tolerance. No Exact. No PnL.",
        }
    if fill_ok and (not u_ok):
        return {
            "CASE": "C",
            "DIRECT_FILL_EDGE_PASS": True,
            "DIRECT_EXEC_U_UPSIDE_PASS": False,
            "DIRECT_EXEC_D_NONWORSE": d_ok,
            "DIRECT_EXEC_U_DEVELOPMENT_PASS": False,
            "VERDICT": "AM_DIRECT_EXEC_U_NOT_LEARNABLE_AS_SELECTION_OBJECTIVE",
            "NEXT_RESEARCH": "AM_ENTRY_ARCHITECTURE_REASSESSMENT",
            "PRIMARY_FINDING": (
                "DIRECT keeps fill vs FILL_ONLY but EXEC_U_TARGET is not a robust selection "
                "objective under paired median and daily consensus."
            ),
            "note": "CASE C. STOP. No Exact. No PnL. No Stage2 restart.",
        }
    if fill_ok and u_ok and (not d_ok):
        return {
            "CASE": "D",
            "DIRECT_FILL_EDGE_PASS": True,
            "DIRECT_EXEC_U_UPSIDE_PASS": True,
            "DIRECT_EXEC_D_NONWORSE": False,
            "DIRECT_EXEC_U_DEVELOPMENT_PASS": False,
            "VERDICT": "AM_DIRECT_EXEC_U_DOWNSIDE_TAX",
            "NEXT_RESEARCH": "AM_ENTRY_EXIT_COUPLING_REASSESSMENT",
            "PRIMARY_FINDING": (
                "DIRECT keeps fill and EXEC_U vs FILL_ONLY but EXEC_D non-worsening fails. "
                "D model addition is forbidden."
            ),
            "note": "CASE D. STOP. No D model. No Exact. No PnL.",
        }
    return {
        "CASE": "C",
        "DIRECT_FILL_EDGE_PASS": fill_ok,
        "DIRECT_EXEC_U_UPSIDE_PASS": u_ok,
        "DIRECT_EXEC_D_NONWORSE": d_ok,
        "DIRECT_EXEC_U_DEVELOPMENT_PASS": False,
        "VERDICT": "AM_DIRECT_EXEC_U_NOT_LEARNABLE_AS_SELECTION_OBJECTIVE",
        "NEXT_RESEARCH": "AM_ENTRY_ARCHITECTURE_REASSESSMENT",
        "PRIMARY_FINDING": (
            "DIRECT fails both the fill hard gate and the EXEC_U upside gate vs FILL_ONLY. "
            "EXEC_U_TARGET is not a robust selection objective under this contract."
        ),
        "note": "CASE C fallback (fill and U both fail). STOP. No Exact. No PnL.",
    }
