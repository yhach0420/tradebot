"""Parity, fill hard gate, quality gate, CASE A-F. No arm search."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.am_wait5_minimal_swap_stage2_development import PARITY_ABS_TOL, PARITY_EXPECTED, POS_REP_MIN
from research.am_wait5_two_stage_development.analyze import _daily_map, _delta_stats, _med_key
from research.canonical_entry_performance_rebase.analyze import _f
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.passive_wait_policy_reassessment.analyze import _close, _median


def freeze_parity(obs: dict[str, Any]) -> dict[str, Any]:
    checks = {k: _close(obs.get(k), exp, PARITY_ABS_TOL) for k, exp in PARITY_EXPECTED.items()}
    return {"ok": all(checks.values()), "checks": checks, "observed": obs}


def spec_rows(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for b in bodies:
        c = b.get("CONTROL") or {}
        fo = b.get("FILL_ONLY") or {}
        ms = b.get("MINIMAL_SWAP") or {}
        dist = b.get("distinctness") or {}
        sw = b.get("swap_only") or {}
        rec = {
            "representation_id": b.get("representation_id"),
            "feature_set": b.get("feature_set"),
            "normalization": b.get("normalization"),
            "CONTROL_FILL_RATE": c.get("SELECTED_FILL_RATE"),
            "FILL_ONLY_FILL_RATE": fo.get("SELECTED_FILL_RATE"),
            "MINIMAL_SWAP_FILL_RATE": ms.get("SELECTED_FILL_RATE"),
            "DELTA_FILL_VS_CONTROL": None
            if _f(ms.get("SELECTED_FILL_RATE")) is None or _f(c.get("SELECTED_FILL_RATE")) is None
            else float(ms["SELECTED_FILL_RATE"]) - float(c["SELECTED_FILL_RATE"]),
            "DELTA_FILL_VS_FILL_ONLY": None
            if _f(ms.get("SELECTED_FILL_RATE")) is None or _f(fo.get("SELECTED_FILL_RATE")) is None
            else float(ms["SELECTED_FILL_RATE"]) - float(fo["SELECTED_FILL_RATE"]),
            "CONTROL_EXEC_U": c.get("EXEC_U"),
            "CONTROL_EXEC_D": c.get("EXEC_D"),
            "FILL_ONLY_EXEC_U": fo.get("EXEC_U"),
            "FILL_ONLY_EXEC_D": fo.get("EXEC_D"),
            "MINIMAL_SWAP_EXEC_U": ms.get("EXEC_U"),
            "MINIMAL_SWAP_EXEC_D": ms.get("EXEC_D"),
            "DELTA_EXEC_U_VS_CONTROL": None
            if _f(ms.get("EXEC_U")) is None or _f(c.get("EXEC_U")) is None
            else float(ms["EXEC_U"]) - float(c["EXEC_U"]),
            "DELTA_EXEC_D_VS_CONTROL": None
            if _f(ms.get("EXEC_D")) is None or _f(c.get("EXEC_D")) is None
            else float(ms["EXEC_D"]) - float(c["EXEC_D"]),
            "DELTA_EXEC_U_VS_FILL_ONLY": None
            if _f(ms.get("EXEC_U")) is None or _f(fo.get("EXEC_U")) is None
            else float(ms["EXEC_U"]) - float(fo["EXEC_U"]),
            "DELTA_EXEC_D_VS_FILL_ONLY": None
            if _f(ms.get("EXEC_D")) is None or _f(fo.get("EXEC_D")) is None
            else float(ms["EXEC_D"]) - float(fo["EXEC_D"]),
            "FILL_ONLY_COND_U": fo.get("COND_U_MEAN"),
            "FILL_ONLY_COND_D": fo.get("COND_D_MEAN"),
            "MINIMAL_SWAP_COND_U": ms.get("COND_U_MEAN"),
            "MINIMAL_SWAP_COND_D": ms.get("COND_D_MEAN"),
            **dist,
        }
        rec.update(sw)
        out.append(rec)
    return out


def _consensus(bodies: list[dict[str, Any]], arm_a: str, arm_b: str, metric: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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


def fill_edge_gate(spec: list[dict[str, Any]], bodies: list[dict[str, Any]]) -> dict[str, Any]:
    ms = _med_key(spec, "MINIMAL_SWAP_FILL_RATE")
    fo = _med_key(spec, "FILL_ONLY_FILL_RATE")
    deltas = [float(v) for r in spec if (v := _f(r.get("DELTA_FILL_VS_FILL_ONLY"))) is not None]
    nonneg = sum(1 for v in deltas if v + 1e-15 >= 0.0)
    daily, st = _consensus(bodies, "MINIMAL_SWAP", "FILL_ONLY", "SELECTED_FILL_RATE")
    pos = int(st.get("positive_days") or 0)
    neg = int(st.get("negative_days") or 0)
    zero = int(st.get("zero_days") or 0)
    gates = {
        "A_MS_FILL_GE_FILL_ONLY": bool(ms is not None and fo is not None and float(ms) + 1e-15 >= float(fo)),
        "B_NONNEG_REP_GE_6": bool(nonneg >= int(POS_REP_MIN)),
        "C_POS_OR_ZERO_GT_NEG": bool((pos + zero) > neg),
    }
    return {
        "MINIMAL_SWAP_FILL_RATE": ms,
        "FILL_ONLY_FILL_RATE": fo,
        "MEDIAN_DELTA": _median(deltas),
        "FILL_NONNEG_REP_N": nonneg,
        "FILL_POS_DAYS": pos,
        "FILL_ZERO_DAYS": zero,
        "FILL_NEG_DAYS": neg,
        "EX_BEST_DAY": st.get("ex_best_day"),
        "EX_TOP3_DAYS": st.get("ex_top3_days"),
        "gates": gates,
        "PASS": all(gates.values()),
        "daily": daily,
    }


def quality_gate(spec: list[dict[str, Any]], bodies: list[dict[str, Any]]) -> dict[str, Any]:
    du = [float(v) for r in spec if (v := _f(r.get("DELTA_EXEC_U_VS_FILL_ONLY"))) is not None]
    dd = [float(v) for r in spec if (v := _f(r.get("DELTA_EXEC_D_VS_FILL_ONLY"))) is not None]
    u_pos_rep = sum(1 for v in du if v > 0)
    d_pos_rep = sum(1 for v in dd if v > 0)
    u_daily, ust = _consensus(bodies, "MINIMAL_SWAP", "FILL_ONLY", "EXEC_U")
    d_daily, dst = _consensus(bodies, "MINIMAL_SWAP", "FILL_ONLY", "EXEC_D")
    med_u = _median(du)
    med_d = _median(dd)
    u_pos = int(ust.get("positive_days") or 0)
    u_neg = int(ust.get("negative_days") or 0)
    d_pos = int(dst.get("positive_days") or 0)
    d_neg = int(dst.get("negative_days") or 0)
    gates = {
        "A_MEDIAN_DELTA_EXEC_U_GT_0": bool(med_u is not None and med_u > 0),
        "B_MEDIAN_DELTA_EXEC_D_GT_0": bool(med_d is not None and med_d > 0),
        "C_U_POSITIVE_REP_GE_6": bool(u_pos_rep >= int(POS_REP_MIN)),
        "D_D_POSITIVE_REP_GE_6": bool(d_pos_rep >= int(POS_REP_MIN)),
        "E_U_POS_GT_NEG": bool(u_pos > u_neg),
        "F_D_POS_GT_NEG": bool(d_pos > d_neg),
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
        "U_POS_DAYS": u_pos,
        "U_NEG_DAYS": u_neg,
        "U_ZERO_DAYS": ust.get("zero_days"),
        "D_POS_DAYS": d_pos,
        "D_NEG_DAYS": d_neg,
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
    tu = _med_key(spec, "MINIMAL_SWAP_COND_U")
    fu = _med_key(spec, "FILL_ONLY_COND_U")
    td = _med_key(spec, "MINIMAL_SWAP_COND_D")
    fd = _med_key(spec, "FILL_ONLY_COND_D")
    u_ok = tu is not None and fu is not None and float(tu) + 1e-12 >= float(fu)
    d_ok = td is not None and fd is not None and float(td) + 1e-12 >= float(fd)
    return {
        "MINIMAL_SWAP_COND_U": tu,
        "FILL_ONLY_COND_U": fu,
        "MINIMAL_SWAP_COND_D": td,
        "FILL_ONLY_COND_D": fd,
        "DELTA_COND_U": None if tu is None or fu is None else float(tu) - float(fu),
        "DELTA_COND_D": None if td is None or fd is None else float(td) - float(fd),
        "U_GE": u_ok,
        "D_GE": d_ok,
        "PASS": bool(u_ok and d_ok),
    }


def decide(
    *,
    fill_gate: dict[str, Any],
    qual_gate: dict[str, Any],
    leak: dict[str, int],
    ne_cohort_n: int,
) -> dict[str, Any]:
    if any(int(leak.get(k) or 0) != 0 for k in leak):
        return {
            "CASE": "F",
            "MINIMAL_SWAP_STAGE2_PASS": False,
            "VERDICT": "AM_WAIT5_MINIMAL_SWAP_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": "Isolation, max-swap, or frozen-OOF integrity failed.",
            "note": "CASE F. STOP.",
        }
    if int(ne_cohort_n or 0) <= 0:
        return {
            "CASE": "E",
            "MINIMAL_SWAP_STAGE2_PASS": False,
            "VERDICT": "AM_WAIT5_STAGE2_NO_INCREMENTAL_SELECTION",
            "NEXT_RESEARCH": "AM_WAIT5_FILLABILITY_FIRST_REASSESSMENT",
            "PRIMARY_FINDING": "Minimal-swap never changed membership vs FILL_ONLY. Stage2 has no incremental selection.",
            "note": "CASE E. STOP. No Stage2 micro-search.",
        }
    fill_ok = bool(fill_gate.get("PASS"))
    qual_ok = bool(qual_gate.get("PASS"))
    u_med = _f(qual_gate.get("MEDIAN_DELTA_EXEC_U"))
    d_med = _f(qual_gate.get("MEDIAN_DELTA_EXEC_D"))
    u_up = u_med is not None and float(u_med) > 0
    d_up = d_med is not None and float(d_med) > 0
    if u_up != d_up:
        return {
            "CASE": "D",
            "MINIMAL_SWAP_STAGE2_PASS": False,
            "VERDICT": "AM_WAIT5_MINIMAL_SWAP_SINGLE_COMPONENT_ONLY",
            "NEXT_RESEARCH": "AM_WAIT5_FILLABILITY_FIRST_REASSESSMENT",
            "PRIMARY_FINDING": "Minimal-swap improves only one of EXEC_U or EXEC_D vs FILL_ONLY. The sides may not compensate.",
            "note": "CASE D. STOP. Current min-rank Stage2 line CLOSE. No Stage2 micro-search.",
        }
    if fill_ok and qual_ok:
        return {
            "CASE": "A",
            "MINIMAL_SWAP_STAGE2_PASS": True,
            "VERDICT": "AM_WAIT5_MINIMAL_SWAP_STAGE2_SUPPORTED",
            "NEXT_RESEARCH": "AM_WAIT5_STAGE2_FINAL_SPEC_PRECOMMIT",
            "PRIMARY_FINDING": (
                "Minimal-swap keeps the FILL_ONLY fill edge and adds robust execution-adjusted "
                "U and D vs FILL_ONLY."
            ),
            "note": "CASE A. STOP. No Exact. No PnL. No final model adopt.",
        }
    if (not fill_ok) and u_up and d_up:
        return {
            "CASE": "C",
            "MINIMAL_SWAP_STAGE2_PASS": False,
            "VERDICT": "AM_WAIT5_STAGE2_STILL_SACRIFICES_FILL",
            "NEXT_RESEARCH": "AM_WAIT5_FILLABILITY_FIRST_REASSESSMENT",
            "PRIMARY_FINDING": "U/D improve vs FILL_ONLY but the fill hard gate fails. Fill-loss tolerance is forbidden.",
            "note": "CASE C. STOP. No fill-loss tolerance. No Stage2 micro-search.",
        }
    if fill_ok and (not qual_ok):
        return {
            "CASE": "B",
            "MINIMAL_SWAP_STAGE2_PASS": False,
            "VERDICT": "AM_WAIT5_FILL_PRESERVING_STAGE2_NO_JOINT_VALUE",
            "NEXT_RESEARCH": "AM_WAIT5_FILLABILITY_FIRST_REASSESSMENT",
            "PRIMARY_FINDING": (
                "Fill edge vs FILL_ONLY is kept, but min-rank on the R3-R5 slot does not add "
                "joint EXEC_U and EXEC_D value. Current min-rank Stage2 line is CLOSED."
            ),
            "note": "CASE B. STOP. Current min-rank Stage2 line CLOSE. No Stage2 micro-search.",
        }
    return {
        "CASE": "C",
        "MINIMAL_SWAP_STAGE2_PASS": False,
        "VERDICT": "AM_WAIT5_STAGE2_STILL_SACRIFICES_FILL",
        "NEXT_RESEARCH": "AM_WAIT5_FILLABILITY_FIRST_REASSESSMENT",
        "PRIMARY_FINDING": "Minimal-swap does not keep the FILL_ONLY fill edge and does not add joint U/D value.",
        "note": "CASE C. STOP. No fill-loss tolerance. No Stage2 micro-search.",
    }
