"""S0 parity, S1 frozen A-I gate, incremental vs S0, CASE A-D."""
from __future__ import annotations

from typing import Any, Optional

from research.canonical_entry_performance_rebase.analyze import _f
from research.entry_sequence_representation import (
    JOINT_RATE_MIN,
    N_MARKS,
    SEQUENCE_FEATURE_N,
    S0_EXPECTED,
    S0_PARITY_ABS_TOL,
)
from research.joint_feature_architecture.analyze import gates as _gates


def gates(body: dict[str, Any]) -> dict[str, Any]:
    return _gates(body)


def _close(a: Any, b: Any, tol: float) -> bool:
    x, y = _f(a), _f(b)
    if x is None or y is None:
        return False
    return abs(float(x) - float(y)) <= float(tol)


def base_parity(s0: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "MFE": _close(s0.get("TOP3_MFE_DELTA"), S0_EXPECTED["TOP3_MFE_DELTA"], S0_PARITY_ABS_TOL),
        "DOWNSIDE": _close(s0.get("TOP3_DOWNSIDE_DELTA"), S0_EXPECTED["TOP3_DOWNSIDE_DELTA"], S0_PARITY_ABS_TOL),
        "JOINT": _close(s0.get("JOINT_COHORT_SUCCESS_RATE"), S0_EXPECTED["JOINT_COHORT_SUCCESS_RATE"], S0_PARITY_ABS_TOL),
    }
    return {
        "BASE_PARITY": all(checks.values()),
        "checks": checks,
        "expected": dict(S0_EXPECTED),
        "observed": {
            "TOP3_MFE_DELTA": s0.get("TOP3_MFE_DELTA"),
            "TOP3_DOWNSIDE_DELTA": s0.get("TOP3_DOWNSIDE_DELTA"),
            "JOINT_COHORT_SUCCESS_RATE": s0.get("JOINT_COHORT_SUCCESS_RATE"),
        },
        "forbidden_previous_9rep_median": {
            "MFE": 0.0015119228,
            "DOWNSIDE": -0.0005569130,
            "JOINT_RATE": 0.3240418,
            "used_as_A0": False,
        },
    }


def _sub(a: Any, b: Any) -> Optional[float]:
    x, y = _f(a), _f(b)
    if x is None or y is None:
        return None
    return float(x) - float(y)


def arch_summary(body: dict[str, Any]) -> dict[str, Any]:
    g = gates(body)
    return {
        "architecture_id": body.get("architecture_id") or body.get("representation_id"),
        "name": body.get("name"),
        "n_features": body.get("n_features") or len(body.get("features") or []),
        "n_cohorts": body.get("n_cohorts"),
        "MFE_DELTA": g["MFE_DELTA"],
        "DOWNSIDE_DELTA": g["DOWNSIDE_DELTA"],
        "JOINT_RATE": g["JOINT_RATE"],
        "MFE_POSITIVE_DAYS": body.get("MFE_POSITIVE_DAYS"),
        "MFE_NEGATIVE_DAYS": body.get("MFE_NEGATIVE_DAYS"),
        "DOWNSIDE_POSITIVE_DAYS": body.get("DOWNSIDE_POSITIVE_DAYS"),
        "DOWNSIDE_NEGATIVE_DAYS": body.get("DOWNSIDE_NEGATIVE_DAYS"),
        "MFE_EX_BEST_DAY": body.get("MFE_EX_BEST_DAY"),
        "MFE_EX_TOP3_DAYS": body.get("MFE_EX_TOP3_DAYS"),
        "DOWNSIDE_EX_BEST_DAY": body.get("DOWNSIDE_EX_BEST_DAY"),
        "DOWNSIDE_EX_TOP3_DAYS": body.get("DOWNSIDE_EX_TOP3_DAYS"),
        "ROC_AUC": body.get("ROC_AUC"),
        "AVERAGE_PRECISION": body.get("AVERAGE_PRECISION"),
        "PASS": g["PASS"],
        "gate_fail": g["gate_fail"],
        "gates": g["gates"],
    }


def integrity_ok(
    *,
    future_n: int,
    session_carry_n: int,
    itayose_n: int,
    special_n: int,
    after_t0_n: int,
    grid_marks: int,
    sequence_feature_n: int,
    contamination_n: int,
    grid_bad_n: int,
    join_miss_n: int,
) -> tuple[bool, str]:
    if int(future_n) != 0:
        return False, f"FUTURE_EVENT_USE_N={future_n}"
    if int(session_carry_n) != 0:
        return False, f"SESSION_CARRY_N={session_carry_n}"
    if int(itayose_n) != 0:
        return False, f"ITAYOSE_STATE_USE_N={itayose_n}"
    if int(special_n) != 0:
        return False, f"SPECIAL_STATE_USE_N={special_n}"
    if int(after_t0_n) != 0:
        return False, f"SEQUENCE_AFTER_T0_N={after_t0_n}"
    if int(grid_marks) != int(N_MARKS):
        return False, f"GRID_MARKS_PER_ROW={grid_marks}"
    if int(sequence_feature_n) != int(SEQUENCE_FEATURE_N):
        return False, f"SEQUENCE_FEATURE_N={sequence_feature_n}"
    if int(contamination_n) != 0:
        return False, f"TARGET_CONTAMINATION_N={contamination_n}"
    if int(grid_bad_n) != 0:
        return False, f"GRID_BAD_N={grid_bad_n}"
    if int(join_miss_n) != 0:
        return False, f"JOIN_MISS_N={join_miss_n}"
    return True, "ok"


def decide(
    s1: dict[str, Any],
    *,
    s0: dict[str, Any],
    parity_ok: bool,
    integ_ok: bool,
    integ_note: str,
) -> dict[str, Any]:
    if not parity_ok:
        return {
            "CASE": None,
            "VERDICT": "SEQUENCE_REPRESENTATION_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": "S0 F2_UNION|none did not reproduce the Direct Joint A0 control.",
            "SEQUENCE_REPRESENTATION_PASS": False,
            "note": "STOP. BASE_PARITY failed.",
        }
    if not integ_ok:
        return {
            "CASE": None,
            "VERDICT": "SEQUENCE_REPRESENTATION_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": f"Sequence causality/integrity fail: {integ_note}.",
            "SEQUENCE_REPRESENTATION_PASS": False,
            "note": "STOP. SEQUENCE_REPRESENTATION_INTEGRITY_FAILED.",
        }
    g = gates(s1)
    mfe = g["MFE_DELTA"]
    dn = g["DOWNSIDE_DELTA"]
    mfe_up = bool(mfe is not None and float(mfe) > 0)
    dn_up = bool(dn is not None and float(dn) > 0)
    d_mfe = _sub(s1.get("TOP3_MFE_DELTA"), s0.get("TOP3_MFE_DELTA"))
    d_dn = _sub(s1.get("TOP3_DOWNSIDE_DELTA"), s0.get("TOP3_DOWNSIDE_DELTA"))
    d_jt = _sub(s1.get("JOINT_COHORT_SUCCESS_RATE"), s0.get("JOINT_COHORT_SUCCESS_RATE"))
    if g["PASS"]:
        return {
            "CASE": "A",
            "VERDICT": "SEQUENCE_REPRESENTATION_EDGE_FOUND",
            "NEXT_RESEARCH": "SEQUENCE_ARCHITECTURE_PRECOMMITTED_DEVELOPMENT",
            "PRIMARY_FINDING": (
                "Flattened causal 180s/5s PRICE+BOARD sequence plus F2_UNION passes frozen A-I "
                "vs CURRENT Top3 on 18-day OOF. Sequence not adopted. Exact not run."
            ),
            "SEQUENCE_REPRESENTATION_PASS": True,
            "DELTA_MFE_VS_S0": d_mfe,
            "DELTA_DOWNSIDE_VS_S0": d_dn,
            "DELTA_JOINT_RATE_VS_S0": d_jt,
            "note": "CASE A. STOP. Do not start precommitted sequence development this run.",
        }
    if mfe_up and dn_up:
        return {
            "CASE": "B",
            "VERDICT": "SEQUENCE_SIGNAL_NOT_ROBUST",
            "NEXT_RESEARCH": "TEMPORAL_MODEL_ARCHITECTURE_PROBE",
            "PRIMARY_FINDING": (
                "S1 improves both MFE and downside-avoid means vs CURRENT Top3, but day robustness "
                f"or joint rate fails frozen A-I: {g['gate_fail']}"
            ),
            "SEQUENCE_REPRESENTATION_PASS": False,
            "DELTA_MFE_VS_S0": d_mfe,
            "DELTA_DOWNSIDE_VS_S0": d_dn,
            "DELTA_JOINT_RATE_VS_S0": d_jt,
            "note": "CASE B. STOP. Temporal model not started this run.",
        }
    if (mfe_up and not dn_up) or (dn_up and not mfe_up):
        return {
            "CASE": "C",
            "VERDICT": "FLATTENED_SEQUENCE_STILL_SINGLE_OBJECTIVE",
            "NEXT_RESEARCH": "TEMPORAL_MODEL_ARCHITECTURE_PROBE",
            "PRIMARY_FINDING": (
                "Flattened 37x7 sequence still moves only one of MFE / downside-avoid vs CURRENT Top3."
            ),
            "SEQUENCE_REPRESENTATION_PASS": False,
            "DELTA_MFE_VS_S0": d_mfe,
            "DELTA_DOWNSIDE_VS_S0": d_dn,
            "DELTA_JOINT_RATE_VS_S0": d_jt,
            "note": "CASE C. STOP. Temporal model not started this run.",
        }
    return {
        "CASE": "D",
        "VERDICT": "FIXED_GRID_SEQUENCE_INSUFFICIENT",
        "NEXT_RESEARCH": "EVENT_LEVEL_OR_TEMPORAL_MODEL_REDESIGN",
        "PRIMARY_FINDING": (
            "Fixed-grid flattened sequence does not improve joint identification vs S0 / CURRENT Top3."
        ),
        "SEQUENCE_REPRESENTATION_PASS": False,
        "DELTA_MFE_VS_S0": d_mfe,
        "DELTA_DOWNSIDE_VS_S0": d_dn,
        "DELTA_JOINT_RATE_VS_S0": d_jt,
        "note": "CASE D. STOP. Event-level redesign not started this run.",
    }
