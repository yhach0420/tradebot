"""Parity, 9-rep median/consensus gates, CASE A-E. No arm search."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.am_wait5_two_stage_development import PARITY_ABS_TOL, PARITY_EXPECTED, POS_REP_MIN
from research.am_wait5_two_stage_interface_precommit import (
    FINAL_SELECTION_N as IF_FINAL_N,
    STAGE1_SHORTLIST_N as IF_SHORT_N,
)
from research.canonical_entry_performance_rebase.analyze import _f
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.entry_rank_shape_audit.oof import delta_series_stats
from research.passive_wait_policy_reassessment.analyze import _close, _median, _rate
from research.wait5_session_architecture_precommit.analyze import sess_rows
from research.wait5_session_target_learnability import AM_TOPK
from research.wait5_session_target_learnability.oof import admission_fill


def freeze_population(am: list[dict[str, Any]], am_top3: dict[str, Any]) -> dict[str, Any]:
    am_n = len(am)
    am_pos = sum(1 for r in am if int(r.get("Y_FILL5") or 0) == 1)
    obs = {
        "AM_LABELED_N": am_n,
        "AM_Y_FILL5_POS_N": am_pos,
        "AM_CURRENT_TOP3_FILL5_RATE": am_top3.get("FILL_RATE"),
    }
    checks = {
        "AM_LABELED_N": int(obs["AM_LABELED_N"]) == int(PARITY_EXPECTED["AM_LABELED_N"]),
        "AM_Y_FILL5_POS_N": int(obs["AM_Y_FILL5_POS_N"]) == int(PARITY_EXPECTED["AM_Y_FILL5_POS_N"]),
        "AM_CURRENT_TOP3_FILL5_RATE": _close(
            obs["AM_CURRENT_TOP3_FILL5_RATE"], PARITY_EXPECTED["AM_CURRENT_TOP3_FILL5_RATE"], PARITY_ABS_TOL
        ),
    }
    return {"ok": all(checks.values()), "checks": checks, "observed": obs}


def freeze_priors(*, learn: dict[str, Any], objective: dict[str, Any], interface: dict[str, Any]) -> dict[str, Any]:
    obs = {
        "AM_FILLABILITY_TOP3_DELTA": learn.get("AM_FILLABILITY_TOP3_DELTA"),
        "AM_U_FILL_SPEARMAN": learn.get("AM_U_FILL_SPEARMAN"),
        "AM_D_FILL_SPEARMAN": learn.get("AM_D_FILL_SPEARMAN"),
        "AM_FILLABILITY_LEARNABLE": learn.get("AM_FILLABILITY_LEARNABLE"),
        "AM_U_FILL_LEARNABLE": learn.get("AM_U_FILL_LEARNABLE"),
        "AM_D_FILL_LEARNABLE": learn.get("AM_D_FILL_LEARNABLE"),
        "OBJECTIVE_VERDICT": objective.get("VERDICT"),
        "INTERFACE_VERDICT": interface.get("VERDICT"),
        "STAGE1_SHORTLIST_N": interface.get("STAGE1_SHORTLIST_N"),
        "FINAL_SELECTION_N": interface.get("FINAL_SELECTION_N"),
    }
    checks = {
        "AM_FILLABILITY_TOP3_DELTA": _close(
            obs["AM_FILLABILITY_TOP3_DELTA"], PARITY_EXPECTED["AM_FILLABILITY_TOP3_DELTA"], PARITY_ABS_TOL
        ),
        "AM_U_FILL_SPEARMAN": _close(
            obs["AM_U_FILL_SPEARMAN"], PARITY_EXPECTED["AM_U_FILL_SPEARMAN"], PARITY_ABS_TOL
        ),
        "AM_D_FILL_SPEARMAN": _close(
            obs["AM_D_FILL_SPEARMAN"], PARITY_EXPECTED["AM_D_FILL_SPEARMAN"], PARITY_ABS_TOL
        ),
        "AM_FILLABILITY_LEARNABLE": obs["AM_FILLABILITY_LEARNABLE"] is True,
        "AM_U_FILL_LEARNABLE": obs["AM_U_FILL_LEARNABLE"] is True,
        "AM_D_FILL_LEARNABLE": obs["AM_D_FILL_LEARNABLE"] is True,
        "OBJECTIVE_VERDICT": obs["OBJECTIVE_VERDICT"] == "AM_WAIT5_TWO_STAGE_OBJECTIVE_PRECOMMITTED",
        "INTERFACE_VERDICT": obs["INTERFACE_VERDICT"] == "AM_WAIT5_TWO_STAGE_INTERFACE_PRECOMMITTED",
        "STAGE1_SHORTLIST_N": int(obs["STAGE1_SHORTLIST_N"] or -1) == int(PARITY_EXPECTED["STAGE1_SHORTLIST_N"])
        and int(IF_SHORT_N) == 5,
        "FINAL_SELECTION_N": int(obs["FINAL_SELECTION_N"] or -1) == int(PARITY_EXPECTED["FINAL_SELECTION_N"])
        and int(IF_FINAL_N) == 3,
    }
    return {"ok": all(checks.values()), "checks": checks, "observed": obs}


def am_population(rows: list[dict[str, Any]]) -> dict[str, Any]:
    am = sess_rows(rows, "AM")
    return {"am": am, "am_top3": admission_fill(am, "current_score", int(AM_TOPK))}


def _daily_map(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for rec in rows or []:
        v = _f(rec.get(key))
        if v is None:
            continue
        out[str(rec.get("date"))] = float(v)
    return out


def _delta_stats(xs: list[float]) -> dict[str, Any]:
    st = delta_series_stats(xs) if xs else {
        "mean": None,
        "median": None,
        "positive_days": 0,
        "negative_days": 0,
        "ex_best_day": None,
        "ex_top3_days": None,
        "n_days": 0,
    }
    st["zero_days"] = sum(1 for v in xs if v == 0)
    return st


def _consensus_delta(bodies: list[dict[str, Any]], arm_a: str, arm_b: str, metric: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by: dict[str, list[float]] = defaultdict(list)
    for b in bodies:
        ma = _daily_map((b.get(arm_a) or {}).get("daily") or [], metric)
        mb = _daily_map((b.get(arm_b) or {}).get("daily") or [], metric)
        for d in ELIGIBLE_DAYS:
            if d not in ma or d not in mb:
                continue
            by[d].append(float(ma[d]) - float(mb[d]))
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


def spec_rows(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for b in bodies:
        c = b.get("CONTROL") or {}
        fo = b.get("FILL_ONLY") or {}
        ts = b.get("TWO_STAGE") or {}
        dist = b.get("distinctness") or {}
        rec = {
            "representation_id": b.get("representation_id"),
            "feature_set": b.get("feature_set"),
            "normalization": b.get("normalization"),
            "CONTROL_FILL_RATE": c.get("SELECTED_FILL_RATE"),
            "FILL_ONLY_FILL_RATE": fo.get("SELECTED_FILL_RATE"),
            "TWO_STAGE_FILL_RATE": ts.get("SELECTED_FILL_RATE"),
            "DELTA_FILL_RATE_VS_CONTROL": None
            if _f(ts.get("SELECTED_FILL_RATE")) is None or _f(c.get("SELECTED_FILL_RATE")) is None
            else float(ts["SELECTED_FILL_RATE"]) - float(c["SELECTED_FILL_RATE"]),
            "DELTA_FILL_RATE_VS_FILL_ONLY": None
            if _f(ts.get("SELECTED_FILL_RATE")) is None or _f(fo.get("SELECTED_FILL_RATE")) is None
            else float(ts["SELECTED_FILL_RATE"]) - float(fo["SELECTED_FILL_RATE"]),
            "CONTROL_EXEC_U": c.get("EXEC_U"),
            "CONTROL_EXEC_D": c.get("EXEC_D"),
            "FILL_ONLY_EXEC_U": fo.get("EXEC_U"),
            "FILL_ONLY_EXEC_D": fo.get("EXEC_D"),
            "TWO_STAGE_EXEC_U": ts.get("EXEC_U"),
            "TWO_STAGE_EXEC_D": ts.get("EXEC_D"),
            "DELTA_EXEC_U_VS_CONTROL": None
            if _f(ts.get("EXEC_U")) is None or _f(c.get("EXEC_U")) is None
            else float(ts["EXEC_U"]) - float(c["EXEC_U"]),
            "DELTA_EXEC_D_VS_CONTROL": None
            if _f(ts.get("EXEC_D")) is None or _f(c.get("EXEC_D")) is None
            else float(ts["EXEC_D"]) - float(c["EXEC_D"]),
            "DELTA_EXEC_U_VS_FILL_ONLY": None
            if _f(ts.get("EXEC_U")) is None or _f(fo.get("EXEC_U")) is None
            else float(ts["EXEC_U"]) - float(fo["EXEC_U"]),
            "DELTA_EXEC_D_VS_FILL_ONLY": None
            if _f(ts.get("EXEC_D")) is None or _f(fo.get("EXEC_D")) is None
            else float(ts["EXEC_D"]) - float(fo["EXEC_D"]),
            "FILL_ONLY_COND_U": fo.get("COND_U_MEAN"),
            "FILL_ONLY_COND_D": fo.get("COND_D_MEAN"),
            "TWO_STAGE_COND_U": ts.get("COND_U_MEAN"),
            "TWO_STAGE_COND_D": ts.get("COND_D_MEAN"),
            **dist,
        }
        out.append(rec)
    return out


def _med_key(rows: list[dict[str, Any]], key: str) -> Optional[float]:
    return _median([float(v) for r in rows if (v := _f(r.get(key))) is not None])


def fillability_gate(spec: list[dict[str, Any]], bodies: list[dict[str, Any]]) -> dict[str, Any]:
    deltas = [float(v) for r in spec if (v := _f(r.get("DELTA_FILL_RATE_VS_CONTROL"))) is not None]
    pos_rep = sum(1 for v in deltas if v > 0)
    daily, st = _consensus_delta(bodies, "TWO_STAGE", "CONTROL", "SELECTED_FILL_RATE")
    med = _median(deltas)
    pos_days = int(st.get("positive_days") or 0)
    neg_days = int(st.get("negative_days") or 0)
    gates = {
        "A_MEDIAN_TWO_STAGE_FILL_GT_CONTROL": bool(med is not None and med > 0),
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


def quality_increment_gate(spec: list[dict[str, Any]], bodies: list[dict[str, Any]]) -> dict[str, Any]:
    du = [float(v) for r in spec if (v := _f(r.get("DELTA_EXEC_U_VS_FILL_ONLY"))) is not None]
    dd = [float(v) for r in spec if (v := _f(r.get("DELTA_EXEC_D_VS_FILL_ONLY"))) is not None]
    u_pos_rep = sum(1 for v in du if v > 0)
    d_pos_rep = sum(1 for v in dd if v > 0)
    u_daily, ust = _consensus_delta(bodies, "TWO_STAGE", "FILL_ONLY", "EXEC_U")
    d_daily, dst = _consensus_delta(bodies, "TWO_STAGE", "FILL_ONLY", "EXEC_D")
    med_u = _median(du)
    med_d = _median(dd)
    u_pos_days = int(ust.get("positive_days") or 0)
    u_neg_days = int(ust.get("negative_days") or 0)
    d_pos_days = int(dst.get("positive_days") or 0)
    d_neg_days = int(dst.get("negative_days") or 0)
    gates = {
        "A_MEDIAN_DELTA_EXEC_U_GT_0": bool(med_u is not None and med_u > 0),
        "B_MEDIAN_DELTA_EXEC_D_GT_0": bool(med_d is not None and med_d > 0),
        "C_U_POSITIVE_REP_GE_6": bool(u_pos_rep >= int(POS_REP_MIN)),
        "D_D_POSITIVE_REP_GE_6": bool(d_pos_rep >= int(POS_REP_MIN)),
        "E_U_POS_GT_NEG": bool(u_pos_days > u_neg_days),
        "F_D_POS_GT_NEG": bool(d_pos_days > d_neg_days),
        "G_U_EX_BEST_GT_0": bool(ust.get("ex_best_day") is not None and float(ust["ex_best_day"]) > 0),
        "H_U_EX_TOP3_GE_0": bool(ust.get("ex_top3_days") is not None and float(ust["ex_top3_days"]) + 1e-12 >= 0.0),
        "I_D_EX_BEST_GT_0": bool(dst.get("ex_best_day") is not None and float(dst["ex_best_day"]) > 0),
        "J_D_EX_TOP3_GE_0": bool(dst.get("ex_top3_days") is not None and float(dst["ex_top3_days"]) + 1e-12 >= 0.0),
    }
    return {
        "MEDIAN_DELTA_EXEC_U": med_u,
        "MEDIAN_DELTA_EXEC_D": med_d,
        "U_POSITIVE_REP_N": u_pos_rep,
        "D_POSITIVE_REP_N": d_pos_rep,
        "U_POSITIVE_DAYS": u_pos_days,
        "U_NEGATIVE_DAYS": u_neg_days,
        "U_ZERO_DAYS": ust.get("zero_days"),
        "D_POSITIVE_DAYS": d_pos_days,
        "D_NEGATIVE_DAYS": d_neg_days,
        "D_ZERO_DAYS": dst.get("zero_days"),
        "U_EX_BEST_DAY": ust.get("ex_best_day"),
        "U_EX_TOP3_DAYS": ust.get("ex_top3_days"),
        "D_EX_BEST_DAY": dst.get("ex_best_day"),
        "D_EX_TOP3_DAYS": dst.get("ex_top3_days"),
        "gates": gates,
        "PASS": all(gates.values()),
        "daily_u": u_daily,
        "daily_d": d_daily,
    }


def cond_sanity(spec: list[dict[str, Any]]) -> dict[str, Any]:
    tu = _med_key(spec, "TWO_STAGE_COND_U")
    fu = _med_key(spec, "FILL_ONLY_COND_U")
    td = _med_key(spec, "TWO_STAGE_COND_D")
    fd = _med_key(spec, "FILL_ONLY_COND_D")
    u_ok = tu is not None and fu is not None and float(tu) + 1e-12 >= float(fu)
    d_ok = td is not None and fd is not None and float(td) + 1e-12 >= float(fd)
    return {
        "TWO_STAGE_COND_U": tu,
        "FILL_ONLY_COND_U": fu,
        "TWO_STAGE_COND_D": td,
        "FILL_ONLY_COND_D": fd,
        "U_GE": u_ok,
        "D_GE": d_ok,
        "PASS": bool(u_ok and d_ok),
    }


def sum_integrity(bodies: list[dict[str, Any]]) -> dict[str, int]:
    keys = (
        "PM_ROWS_USED_N",
        "FUTURE_EVENT_USE_N",
        "TARGET_CONTAMINATION_N",
        "HELDOUT_FIT_LEAK_N",
        "AM_NORMALIZER_HELDOUT_ROW_N",
        "STAGE2_NONFILL_TARGET_TRAIN_N",
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
    qual_gate: dict[str, Any],
    leak: dict[str, int],
) -> dict[str, Any]:
    if any(int(leak.get(k) or 0) != 0 for k in leak):
        return {
            "CASE": "E",
            "AM_TWO_STAGE_DEVELOPMENT_PASS": False,
            "VERDICT": "AM_WAIT5_TWO_STAGE_DEVELOPMENT_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": "Session isolation or Stage2 train-population integrity failed.",
            "note": "CASE E. STOP.",
        }
    fill_ok = bool(fill_gate.get("PASS"))
    qual_ok = bool(qual_gate.get("PASS"))
    u_med = _f(qual_gate.get("MEDIAN_DELTA_EXEC_U"))
    d_med = _f(qual_gate.get("MEDIAN_DELTA_EXEC_D"))
    u_up = u_med is not None and float(u_med) > 0
    d_up = d_med is not None and float(d_med) > 0
    if fill_ok and qual_ok:
        return {
            "CASE": "A",
            "AM_TWO_STAGE_DEVELOPMENT_PASS": True,
            "VERDICT": "AM_WAIT5_TWO_STAGE_DEVELOPMENT_SUPPORTED",
            "NEXT_RESEARCH": "AM_WAIT5_TWO_STAGE_FINAL_SPEC_PRECOMMIT",
            "PRIMARY_FINDING": (
                "TWO_STAGE keeps a robust fillability edge vs CONTROL and adds robust "
                "execution-adjusted U and D vs FILL_ONLY under the frozen Top5→Top3 interface."
            ),
            "note": "CASE A. STOP. No Exact. No PnL. No final model adopt.",
        }
    if u_up != d_up:
        return {
            "CASE": "D",
            "AM_TWO_STAGE_DEVELOPMENT_PASS": False,
            "VERDICT": "AM_WAIT5_STAGE2_SINGLE_COMPONENT_ONLY",
            "NEXT_RESEARCH": "AM_QUALITY_OBJECTIVE_REASSESSMENT",
            "PRIMARY_FINDING": (
                "Stage2 execution-adjusted quality improves only one of U or D vs FILL_ONLY. "
                "The two sides may not compensate each other."
            ),
            "note": "CASE D. STOP. No Exact. No PnL.",
        }
    if (not fill_ok) and (qual_ok or (u_up and d_up)):
        return {
            "CASE": "C",
            "AM_TWO_STAGE_DEVELOPMENT_PASS": False,
            "VERDICT": "AM_WAIT5_TWO_STAGE_LOSES_EXECUTION_EDGE",
            "NEXT_RESEARCH": "AM_WAIT5_INTERFACE_ARCHITECTURE_REASSESSMENT",
            "PRIMARY_FINDING": (
                "Stage2 quality vs FILL_ONLY improves, but TWO_STAGE does not keep a robust "
                "fillability edge vs CONTROL."
            ),
            "note": "CASE C. STOP. No Exact. No PnL.",
        }
    if fill_ok and (not qual_ok):
        return {
            "CASE": "B",
            "AM_TWO_STAGE_DEVELOPMENT_PASS": False,
            "VERDICT": "AM_WAIT5_STAGE2_ADDS_NO_ROBUST_VALUE",
            "NEXT_RESEARCH": "AM_WAIT5_FILLABILITY_FIRST_REASSESSMENT",
            "PRIMARY_FINDING": (
                "Fillability vs CONTROL holds, but the Top5→Top3 joint-quality interface does "
                "not convert learnable U/D into robust incremental selection value vs FILL_ONLY."
            ),
            "note": "CASE B. STOP. No Exact. No PnL.",
        }
    return {
        "CASE": "C",
        "AM_TWO_STAGE_DEVELOPMENT_PASS": False,
        "VERDICT": "AM_WAIT5_TWO_STAGE_LOSES_EXECUTION_EDGE",
        "NEXT_RESEARCH": "AM_WAIT5_INTERFACE_ARCHITECTURE_REASSESSMENT",
        "PRIMARY_FINDING": (
            "TWO_STAGE does not keep a robust fillability edge vs CONTROL and does not add "
            "robust execution-adjusted U and D vs FILL_ONLY."
        ),
        "note": "CASE C. STOP. No Exact. No PnL.",
    }
