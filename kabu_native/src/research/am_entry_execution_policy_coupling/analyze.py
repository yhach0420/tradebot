"""Parity, 9-rep median, mechanism CASE A-E. No wait search. No re-rank."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.am_entry_execution_policy_coupling import PARITY_ABS_TOL, PARITY_EXPECTED
from research.am_wait5_two_stage_development.analyze import _med_key
from research.canonical_entry_performance_rebase.analyze import _f
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.passive_wait_policy_reassessment import WAIT_IDS
from research.passive_wait_policy_reassessment.analyze import _close, _median


def freeze_parity(obs: dict[str, Any]) -> dict[str, Any]:
    checks = {k: _close(obs.get(k), exp, PARITY_ABS_TOL) for k, exp in PARITY_EXPECTED.items()}
    return {"ok": all(checks.values()), "checks": checks, "observed": obs, "expected": dict(PARITY_EXPECTED)}


def spec_rows(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for b in bodies:
        ev = b.get("eval") or {}
        waits = ev.get("waits") or {}
        p = ev.get("pfill") or {}
        fo_p = p.get("FILL_ONLY_ONLY") or {}
        du_p = p.get("DIRECT_ONLY") or {}
        cause_du = (ev.get("w5_cause") or {}).get("DIRECT_ONLY") or {}
        rec_du = (ev.get("recover") or {}).get("DIRECT_ONLY") or {}
        rec_fo_only = (ev.get("recover") or {}).get("FILL_ONLY_ONLY") or {}
        rec_fo = (ev.get("recover") or {}).get("FILL_ONLY") or {}
        rec_all = (ev.get("recover") or {}).get("DIRECT_EXEC_U") or {}
        recovered = ev.get("recovered") or {}
        loss = ev.get("fill_loss") or {}
        couple = ev.get("coupling") or {}
        dist = ev.get("distinctness") or {}
        w5 = waits.get("W5") or {}
        w10 = waits.get("W10") or {}
        rec = {
            "representation_id": b.get("representation_id"),
            "feature_set": b.get("feature_set"),
            "normalization": b.get("normalization"),
            "COMMON_N": ev.get("COMMON_N"),
            "FILL_ONLY_ONLY_N": ev.get("FILL_ONLY_ONLY_N"),
            "DIRECT_ONLY_N": ev.get("DIRECT_ONLY_N"),
            "DIRECT_NE_FILL_ONLY_COHORT_RATE": dist.get("DIRECT_NE_FILL_ONLY_COHORT_RATE"),
            "FILL_ONLY_ONLY_P_FILL_MEAN": fo_p.get("P_FILL_MEAN"),
            "FILL_ONLY_ONLY_P_FILL_MEDIAN": fo_p.get("P_FILL_MEDIAN"),
            "DIRECT_ONLY_P_FILL_MEAN": du_p.get("P_FILL_MEAN"),
            "DIRECT_ONLY_P_FILL_MEDIAN": du_p.get("P_FILL_MEDIAN"),
            "DELTA_P_FILL": p.get("DELTA_P_FILL_MEDIAN"),
            "DIRECT_ONLY_W5_NONFILL_N": rec_du.get("W5_NONFILL_N"),
            "DIRECT_ONLY_NO_VALID_BOARD_N": cause_du.get("NO_VALID_BOARD_WITHIN_5"),
            "DIRECT_ONLY_NO_CROSS_N": cause_du.get("VALID_BOARD_BUT_NO_ASK_CROSS_WITHIN_5"),
            "FILL_ONLY_W5_NONFILL_W10_RECOVER_N": rec_fo.get("W10_RECOVER_N"),
            "DIRECT_W5_NONFILL_W10_RECOVER_N": rec_all.get("W10_RECOVER_N"),
            "FILL_ONLY_ONLY_RECOVER_RATE": rec_fo_only.get("RECOVER_RATE"),
            "DIRECT_ONLY_RECOVER_RATE_W10": rec_du.get("RECOVER_RATE"),
            "DIRECT_ONLY_NEVER_CROSS_10S_RATE": rec_du.get("NEVER_CROSS_10S_RATE"),
            "DIRECT_ONLY_NO_VALID_BOARD_10S_RATE": rec_du.get("NO_VALID_BOARD_10S_RATE"),
            "DIRECT_RECOVERED_N": recovered.get("DIRECT_RECOVERED_N"),
            "RECOVERED_COND_U": recovered.get("RECOVERED_COND_U"),
            "RECOVERED_COND_D": recovered.get("RECOVERED_COND_D"),
            "RECOVERED_TIME_TO_FILL_MEDIAN": recovered.get("RECOVERED_TIME_TO_FILL_MEDIAN"),
            "RECOVERED_TIME_TO_FILL_P90": recovered.get("RECOVERED_TIME_TO_FILL_P90"),
            "FILL_LOSS_LATE_RECOVERABLE_N": loss.get("FILL_LOSS_LATE_RECOVERABLE_N"),
            "FILL_LOSS_NEVER_CROSS_N": loss.get("FILL_LOSS_NEVER_CROSS_N"),
            "FILL_LOSS_NO_VALID_BOARD_N": loss.get("FILL_LOSS_NO_VALID_BOARD_N"),
            "DIRECT_TTF_VS_U_SPEARMAN": couple.get("DIRECT_TTF_VS_U_SPEARMAN"),
            "DIRECT_TTF_VS_D_SPEARMAN": couple.get("DIRECT_TTF_VS_D_SPEARMAN"),
            "FILL_ONLY_ONLY_COND_U": couple.get("FILL_ONLY_ONLY_COND_U"),
            "DIRECT_ONLY_COND_U": couple.get("DIRECT_ONLY_COND_U"),
            "FILL_ONLY_ONLY_COND_D": couple.get("FILL_ONLY_ONLY_COND_D"),
            "DIRECT_ONLY_COND_D": couple.get("DIRECT_ONLY_COND_D"),
            "Y_W5_MISMATCH_N": ev.get("Y_W5_MISMATCH_N"),
        }
        for wid in WAIT_IDS:
            blk = waits.get(wid) or {}
            rec[f"FILL_ONLY_FILL_RATE_{wid}"] = (blk.get("FILL_ONLY") or {}).get("SELECTED_FILL_RATE")
            rec[f"DIRECT_FILL_RATE_{wid}"] = (blk.get("DIRECT_EXEC_U") or {}).get("SELECTED_FILL_RATE")
            rec[f"FILL_ONLY_EXEC_U_{wid}"] = (blk.get("FILL_ONLY") or {}).get("EXEC_U")
            rec[f"DIRECT_EXEC_U_{wid}"] = (blk.get("DIRECT_EXEC_U") or {}).get("EXEC_U")
            rec[f"FILL_ONLY_EXEC_D_{wid}"] = (blk.get("FILL_ONLY") or {}).get("EXEC_D")
            rec[f"DIRECT_EXEC_D_{wid}"] = (blk.get("DIRECT_EXEC_U") or {}).get("EXEC_D")
            rec[f"DIRECT_MINUS_FILL_ONLY_{wid}"] = blk.get("DELTA_FILL")
            rec[f"DIRECT_EXEC_U_DELTA_{wid}"] = blk.get("DELTA_EXEC_U")
            rec[f"DIRECT_EXEC_D_DELTA_{wid}"] = blk.get("DELTA_EXEC_D")
        rec["FILL_ONLY_COND_U_W5"] = (w5.get("FILL_ONLY") or {}).get("COND_U_MEAN")
        rec["DIRECT_COND_U_W5"] = (w5.get("DIRECT_EXEC_U") or {}).get("COND_U_MEAN")
        rec["FILL_ONLY_COND_D_W5"] = (w5.get("FILL_ONLY") or {}).get("COND_D_MEAN")
        rec["DIRECT_COND_D_W5"] = (w5.get("DIRECT_EXEC_U") or {}).get("COND_D_MEAN")
        rec["DELTA_FILL_W5"] = w5.get("DELTA_FILL")
        rec["DELTA_EXEC_U_W5"] = w5.get("DELTA_EXEC_U")
        rec["DELTA_EXEC_D_W5"] = w5.get("DELTA_EXEC_D")
        rec["DIRECT_MINUS_FILL_ONLY_W10"] = w10.get("DELTA_FILL")
        rec["DIRECT_EXEC_U_DELTA_W10"] = w10.get("DELTA_EXEC_U")
        rec["DIRECT_EXEC_D_DELTA_W10"] = w10.get("DELTA_EXEC_D")
        out.append(rec)
    return out


def _day_sign_stats(xs: list[float]) -> dict[str, Any]:
    return {
        "positive_days": sum(1 for v in xs if v > 0),
        "negative_days": sum(1 for v in xs if v < 0),
        "zero_days": sum(1 for v in xs if v == 0),
        "median": _median(xs),
        "n_days": len(xs),
    }


def daily_consensus(bodies: list[dict[str, Any]], key: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by: dict[str, list[float]] = defaultdict(list)
    for b in bodies:
        for rec in (b.get("eval") or {}).get("daily") or []:
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
    return daily, _day_sign_stats(xs)


def decide(spec: list[dict[str, Any]], leak: dict[str, int]) -> dict[str, Any]:
    must0 = (
        "MODEL_REFIT_N",
        "PM_ROWS_USED_N",
        "TARGET_CONTAMINATION_N",
        "HELDOUT_FIT_LEAK_N",
        "SELECTION_REOPTIMIZATION_N",
        "WAIT_SEARCH_N",
        "ORACLE_SELECTION_USE_N",
        "Y_W5_MISMATCH_N",
        "WAIT_JOIN_MISS_N",
    )
    if any(int(leak.get(k) or 0) != 0 for k in must0):
        return {
            "CASE": "E",
            "PRIMARY_MECHANISM": "INTEGRITY_FAIL",
            "NEXT_RESEARCH": "NONE",
            "VERDICT": "AM_ENTRY_EXECUTION_COUPLING_INTEGRITY_FAILED",
            "PRIMARY_FINDING": "Frozen-selection, harvest-join, or wait-integrity failed.",
            "note": "CASE E. STOP.",
        }
    recover = _med_key(spec, "DIRECT_ONLY_RECOVER_RATE_W10")
    never = _med_key(spec, "DIRECT_ONLY_NEVER_CROSS_10S_RATE")
    noboard = _med_key(spec, "DIRECT_ONLY_NO_VALID_BOARD_10S_RATE")
    rec_u = _med_key(spec, "RECOVERED_COND_U")
    rec_d = _med_key(spec, "RECOVERED_COND_D")
    fo_u = _med_key(spec, "FILL_ONLY_COND_U_W5")
    fo_d = _med_key(spec, "FILL_ONLY_COND_D_W5")
    du_u = _med_key(spec, "DIRECT_COND_U_W5")
    du_d = _med_key(spec, "DIRECT_COND_D_W5")
    rec_n = _med_key(spec, "DIRECT_RECOVERED_N")
    inaccessible = None
    if never is not None or noboard is not None:
        inaccessible = float(never or 0.0) + float(noboard or 0.0)
    u_held = rec_u is not None and fo_u is not None and float(rec_u) + 1e-12 >= float(fo_u)
    d_decay = (
        rec_d is not None
        and fo_d is not None
        and du_d is not None
        and float(rec_d) < float(fo_d)
        and float(rec_d) < float(du_d)
    )
    late_majority = recover is not None and float(recover) > 0.5
    inacc_majority = inaccessible is not None and float(inaccessible) > 0.5
    quality_ok = bool(u_held) and (not d_decay)
    if late_majority and (rec_n is None or float(rec_n) <= 0):
        late_majority = False
    if late_majority and quality_ok:
        return {
            "CASE": "A",
            "PRIMARY_MECHANISM": "WAIT5_HORIZON_MISMATCH_WITH_DIRECT_UPSIDE_SELECTION",
            "NEXT_RESEARCH": "AM_EXECUTION_WAIT_ARCHITECTURE_PRECOMMIT",
            "VERDICT": "AM_DIRECT_EXEC_U_FILL_LOSS_IS_LATE_RECOVERABLE",
            "PRIMARY_FINDING": (
                "Most DIRECT_ONLY W5 nonfills ask-cross in 5-10s and recovered fills keep the U edge. "
                "This is not a W10 adoption."
            ),
            "note": "CASE A. STOP. W10 not adopted. No wait search.",
            "u_held": u_held,
            "d_decay": d_decay,
            "recover_rate": recover,
            "inaccessible_rate": inaccessible,
        }
    if late_majority and (not quality_ok):
        return {
            "CASE": "C",
            "PRIMARY_MECHANISM": "LATE_FILL_QUALITY_DECAY",
            "NEXT_RESEARCH": "AM_ENTRY_EXECUTION_TIMING_REASSESSMENT",
            "VERDICT": "AM_DIRECT_EXEC_U_LATE_FILL_QUALITY_DECAY",
            "PRIMARY_FINDING": (
                "DIRECT W5 fill loss is mostly late-recoverable, but recovered fills lose the U edge "
                "or worsen D."
            ),
            "note": "CASE C. STOP. No W10 adoption.",
            "u_held": u_held,
            "d_decay": d_decay,
            "recover_rate": recover,
            "inaccessible_rate": inaccessible,
        }
    if inacc_majority:
        return {
            "CASE": "B",
            "PRIMARY_MECHANISM": "DIRECT_OBJECTIVE_SELECTS_PASSIVELY_INACCESSIBLE_NAMES",
            "NEXT_RESEARCH": "AM_CONSTRAINED_EXECUTION_AWARE_TARGET_ARCHITECTURE_REASSESSMENT",
            "VERDICT": "AM_DIRECT_EXEC_U_PASSIVE_ACCESS_CONFLICT",
            "PRIMARY_FINDING": (
                "Most DIRECT_ONLY W5 nonfills still have no ask-cross within 10s under the frozen "
                "passive limit."
            ),
            "note": "CASE B. STOP. No W10 adoption. No new target.",
            "u_held": u_held,
            "d_decay": d_decay,
            "recover_rate": recover,
            "inaccessible_rate": inaccessible,
        }
    return {
        "CASE": "D",
        "PRIMARY_MECHANISM": "ENTRY_EXECUTION_COUPLING_MIXED",
        "NEXT_RESEARCH": "AM_ENTRY_EXECUTION_ARCHITECTURE_HOLD",
        "VERDICT": "AM_DIRECT_EXEC_U_EXECUTION_COUPLING_MIXED",
        "PRIMARY_FINDING": (
            "Late 5-10s recovery and 10s inaccessibility both contribute to DIRECT W5 fill loss; "
            "neither is a majority mechanism."
        ),
        "note": "CASE D. STOP. No W10 adoption.",
        "u_held": u_held,
        "d_decay": d_decay,
        "recover_rate": recover,
        "inaccessible_rate": inaccessible,
    }
