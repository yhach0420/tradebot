"""Direct EXEC_U target contract. No training. No selection search."""
from __future__ import annotations

from typing import Any

from research.am_direct_exec_u_target_precommit import (
    ALLOWED_DEVELOPMENT_ARMS,
    ARCHITECTURE,
    CONTROL_ARM,
    D_ROLE,
    D_TRAINING_TARGET_ALLOWED,
    DIRECT_ARM,
    DIRECT_TARGET,
    DIRECT_TARGET_FORMULA,
    FILL_LOSS_ALLOWED_IN_PASS_GATE,
    FILL_ONLY_ARM,
    FINAL_SELECTION_N,
    MODEL_FAMILY,
    NEXT_RESEARCH,
    NONFILL_TARGET_VALUE,
    PARITY_ABS_TOL,
    PARITY_EXPECTED,
    PRIMARY_REFERENCE,
    PRIOR_GEOMETRY_ID,
    PRIOR_GEOMETRY_MECHANISM,
    PRIOR_GEOMETRY_VERDICT,
    REPRESENTATION_N,
    SELECTION_RULE,
    STAGE1_ALLOWED,
    STAGE2_ALLOWED,
    VERDICT_OK,
)
from research.canonical_entry_performance_rebase.analyze import _f
from research.passive_wait_policy_reassessment.analyze import _close
from research.wait5_session_target_learnability import POS_REP_MIN, RF_REG_PARAMS


def target_exec_u(r: dict[str, Any]) -> float:
    """I(Y_FILL5=1) * U_FILL. Nonfill is 0, not a penalty. Missing U on a fill is 0 (EXEC contract)."""
    if int(r.get("Y_FILL5") or 0) != 1:
        return float(NONFILL_TARGET_VALUE)
    v = _f(r.get("U_FILL"))
    return float(v) if v is not None else float(NONFILL_TARGET_VALUE)


def target_exec_d_diagnostic(r: dict[str, Any]) -> float:
    """Diagnostic only. Not a training target."""
    if int(r.get("Y_FILL5") or 0) != 1:
        return 0.0
    v = _f(r.get("D_FILL"))
    return float(v) if v is not None else 0.0


def direct_sort_key(pred_exec_u: float, symbol: str) -> tuple:
    return (-float(pred_exec_u), str(symbol or ""))


def target_examples() -> list[dict[str, Any]]:
    rows = [
        {"symbol": "FILL_U", "Y_FILL5": 1, "U_FILL": 0.012, "D_FILL": -0.008},
        {"symbol": "FILL_U_MISSING", "Y_FILL5": 1, "U_FILL": None, "D_FILL": -0.004},
        {"symbol": "NONFILL", "Y_FILL5": 0, "U_FILL": 0.040, "D_FILL": -0.020},
    ]
    out = []
    for r in rows:
        rec = dict(r)
        rec["TARGET_EXEC_U"] = target_exec_u(r)
        rec["TARGET_EXEC_D_DIAGNOSTIC"] = target_exec_d_diagnostic(r)
        rec["NONFILL_IS_PENALTY"] = False
        out.append(rec)
    return out


def freeze_parity(obs: dict[str, Any]) -> dict[str, Any]:
    checks = {k: _close(obs.get(k), exp, PARITY_ABS_TOL) for k, exp in PARITY_EXPECTED.items()}
    return {"ok": all(checks.values()), "checks": checks, "observed": obs, "expected": dict(PARITY_EXPECTED)}


def freeze_geometry(preg: dict[str, Any], analysis_id: str, decision: dict[str, Any] | None = None) -> dict[str, Any]:
    dec = decision or {}
    obs = {
        "PRIOR_ANALYSIS_ID": analysis_id,
        "VERDICT": preg.get("VERDICT"),
        "PRIMARY_MECHANISM": preg.get("PRIMARY_MECHANISM") or dec.get("PRIMARY_MECHANISM"),
        "NEXT_RESEARCH": preg.get("NEXT_RESEARCH") or dec.get("NEXT_RESEARCH"),
        "FILL_ONLY_FILL_RATE": preg.get("FILL_ONLY_FILL_RATE"),
        "FILL_NONWORSE_U_IMPROVE_RATE": preg.get("FILL_NONWORSE_U_IMPROVE_RATE"),
        "SAME_FILL_U_IMPROVE_RATE": preg.get("SAME_FILL_U_IMPROVE_RATE"),
        "FILL_NONWORSE_U_D_NONWORSE_RATE": preg.get("FILL_NONWORSE_U_D_NONWORSE_RATE"),
        "FILL_ONLY_DOMINATED_IN_FILL_U_RATE": preg.get("FILL_ONLY_DOMINATED_IN_FILL_U_RATE"),
    }
    checks = {
        "PRIOR_ANALYSIS_ID": str(obs["PRIOR_ANALYSIS_ID"] or "") == PRIOR_GEOMETRY_ID,
        "VERDICT": obs["VERDICT"] == PRIOR_GEOMETRY_VERDICT,
        "PRIMARY_MECHANISM": obs["PRIMARY_MECHANISM"] == PRIOR_GEOMETRY_MECHANISM,
        "NEXT_RESEARCH": str(obs["NEXT_RESEARCH"] or "") == "AM_DIRECT_EXECUTION_ADJUSTED_UPSIDE_TARGET_PRECOMMIT",
        "FILL_ONLY_FILL_RATE": _close(
            obs["FILL_ONLY_FILL_RATE"], PARITY_EXPECTED["FILL_ONLY_FILL_RATE"], PARITY_ABS_TOL
        ),
        "FILL_NONWORSE_U_IMPROVE_RATE": _close(
            obs["FILL_NONWORSE_U_IMPROVE_RATE"], PARITY_EXPECTED["FILL_NONWORSE_U_IMPROVE_RATE"], PARITY_ABS_TOL
        ),
        "SAME_FILL_U_IMPROVE_RATE": _close(
            obs["SAME_FILL_U_IMPROVE_RATE"], PARITY_EXPECTED["SAME_FILL_U_IMPROVE_RATE"], PARITY_ABS_TOL
        ),
        "FILL_NONWORSE_U_D_NONWORSE_RATE": _close(
            obs["FILL_NONWORSE_U_D_NONWORSE_RATE"], PARITY_EXPECTED["FILL_NONWORSE_U_D_NONWORSE_RATE"], PARITY_ABS_TOL
        ),
        "FILL_ONLY_DOMINATED_IN_FILL_U_RATE": _close(
            obs["FILL_ONLY_DOMINATED_IN_FILL_U_RATE"],
            PARITY_EXPECTED["FILL_ONLY_DOMINATED_IN_FILL_U_RATE"],
            PARITY_ABS_TOL,
        ),
    }
    return {"ok": all(checks.values()), "checks": checks, "observed": obs}


def freeze_fill_only_exec(preg: dict[str, Any]) -> dict[str, Any]:
    obs = {
        "FILL_ONLY_FILL_RATE": preg.get("FILL_ONLY_FILL_RATE"),
        "FILL_ONLY_EXEC_U": preg.get("FILL_ONLY_EXEC_U"),
        "FILL_ONLY_EXEC_D": preg.get("FILL_ONLY_EXEC_D"),
    }
    checks = {
        "FILL_ONLY_FILL_RATE": _close(
            obs["FILL_ONLY_FILL_RATE"], PARITY_EXPECTED["FILL_ONLY_FILL_RATE"], PARITY_ABS_TOL
        ),
        "FILL_ONLY_EXEC_U": _close(obs["FILL_ONLY_EXEC_U"], PARITY_EXPECTED["FILL_ONLY_EXEC_U"], PARITY_ABS_TOL),
        "FILL_ONLY_EXEC_D": _close(obs["FILL_ONLY_EXEC_D"], PARITY_EXPECTED["FILL_ONLY_EXEC_D"], PARITY_ABS_TOL),
    }
    return {"ok": all(checks.values()), "checks": checks, "observed": obs}


def rf_reg_frozen() -> dict[str, Any]:
    return {
        "n_estimators": int(RF_REG_PARAMS["n_estimators"]),
        "max_depth": int(RF_REG_PARAMS["max_depth"]),
        "min_samples_leaf": int(RF_REG_PARAMS["min_samples_leaf"]),
        "max_features": float(RF_REG_PARAMS["max_features"]),
        "bootstrap": bool(RF_REG_PARAMS["bootstrap"]),
        "random_state": int(RF_REG_PARAMS["random_state"]),
        "n_jobs": int(RF_REG_PARAMS["n_jobs"]),
    }


def fill_hard_gate() -> dict[str, Any]:
    return {
        "NAME": "DIRECT_FILL_EDGE_PASS",
        "PRIMARY_REFERENCE": PRIMARY_REFERENCE,
        "A": "DIRECT_EXEC_U fill rate >= FILL_ONLY fill rate",
        "B": f"fill delta nonnegative representations >= {int(POS_REP_MIN)} / 9",
        "C": "daily (positive + zero) > negative",
        "D": "ex-best-day fill delta >= 0",
        "E": "ex-top3-days fill delta >= 0",
        "FILL_LOSS_ALLOWED_IN_PASS_GATE": FILL_LOSS_ALLOWED_IN_PASS_GATE,
        "U_MAY_COMPENSATE_FILL_LOSS": False,
    }


def upside_gate() -> dict[str, Any]:
    return {
        "NAME": "DIRECT_EXEC_U_UPSIDE_PASS",
        "PRIMARY_REFERENCE": PRIMARY_REFERENCE,
        "A": "median EXEC_U delta vs FILL_ONLY > 0",
        "B": f"positive representations >= {int(POS_REP_MIN)} / 9",
        "C": "daily positive > negative",
        "D": "ex-best-day > 0",
        "E": "ex-top3-days >= 0",
        "ALL_REQUIRED": True,
    }


def d_nonworse_gate() -> dict[str, Any]:
    return {
        "NAME": "DIRECT_EXEC_D_NONWORSE",
        "PRIMARY_REFERENCE": PRIMARY_REFERENCE,
        "A": "median EXEC_D delta vs FILL_ONLY >= 0",
        "B": f"nonnegative representations >= {int(POS_REP_MIN)} / 9",
        "C": "daily (positive + zero) >= negative",
        "D": "ex-best-day >= 0",
        "E": "ex-top3-days >= 0",
        "D_IMPROVEMENT_REQUIRED": False,
        "D_TRAINING_TARGET_ALLOWED": D_TRAINING_TARGET_ALLOWED,
    }


def development_pass_gate() -> dict[str, Any]:
    return {
        "NAME": "DIRECT_EXEC_U_DEVELOPMENT_PASS",
        "REQUIRES": [
            "DIRECT_FILL_EDGE_PASS",
            "DIRECT_EXEC_U_UPSIDE_PASS",
            "DIRECT_EXEC_D_NONWORSE",
            "integrity pass",
        ],
        "ALL_REQUIRED": True,
        "CASE_A": "AM_DIRECT_EXEC_U_DEVELOPMENT_SUPPORTED",
        "CASE_B": "AM_DIRECT_EXEC_U_SACRIFICES_FILL",
        "CASE_C": "AM_DIRECT_EXEC_U_NOT_LEARNABLE_AS_SELECTION_OBJECTIVE",
        "CASE_D": "AM_DIRECT_EXEC_U_DOWNSIDE_TAX",
        "CASE_E": "AM_DIRECT_EXEC_U_INTEGRITY_FAILED",
    }


def objective_contract() -> dict[str, Any]:
    return {
        "ARCHITECTURE": ARCHITECTURE,
        "DIRECT_TARGET": DIRECT_TARGET,
        "DIRECT_TARGET_FORMULA": DIRECT_TARGET_FORMULA,
        "TARGET_DEFINITION": "TARGET_EXEC_U = I(Y_FILL5=1) * U_FILL",
        "U_FILL_SOURCE": "POSTFILL_MFE_600",
        "Y_FILL5_SOURCE": "W5 corrected Passive Fill",
        "NONFILL_TARGET_VALUE": NONFILL_TARGET_VALUE,
        "NONFILL_IS_PENALTY": False,
        "NONFILL_MEANING": "Selected slot produced no post-fill upside exposure, so EXEC_U=0.",
        "INTEGRATES_FILLABILITY": True,
        "INTEGRATES_POSTFILL_UPSIDE": True,
        "STAGE1_STAGE2_SCALAR_COMBINATION": False,
        "PNL": False,
        "FUTURE_BACKFILL": False,
        "SESSION_CARRY": False,
        "D_ROLE": D_ROLE,
        "D_TRAINING_TARGET_ALLOWED": D_TRAINING_TARGET_ALLOWED,
        "TARGET_EXEC_D_DIAGNOSTIC": "I(Y_FILL5=1) * D_FILL. Not trained. Not mixed into score.",
        "MODEL_FAMILY": MODEL_FAMILY,
        "MODEL_HYPERPARAMETERS": rf_reg_frozen(),
        "MODEL_HYPERPARAMETERS_SOURCE": "WAIT5_SESSION_TARGET_LEARNABILITY_V1 RF_REG_PARAMS",
        "REPRESENTATION_N": REPRESENTATION_N,
        "REPRESENTATION_EVAL": "median across 9 + daily consensus. No best-representation adopt.",
        "FINAL_SELECTION_N": FINAL_SELECTION_N,
        "SELECTION_RULE": SELECTION_RULE,
        "SELECTION_ORDER": "predicted EXEC_U descending, tie symbol ASC",
        "THRESHOLD": False,
        "SHORTLIST": False,
        "STAGE1_ALLOWED": STAGE1_ALLOWED,
        "STAGE2_ALLOWED": STAGE2_ALLOWED,
        "PRIMARY_REFERENCE": PRIMARY_REFERENCE,
        "ALLOWED_DEVELOPMENT_ARMS": list(ALLOWED_DEVELOPMENT_ARMS),
        "CONTROL_ARM": CONTROL_ARM,
        "FILL_ONLY_ARM": FILL_ONLY_ARM,
        "DIRECT_ARM": DIRECT_ARM,
        "FILL_LOSS_ALLOWED_IN_PASS_GATE": FILL_LOSS_ALLOWED_IN_PASS_GATE,
        "FILL_HARD_GATE": fill_hard_gate(),
        "UPSIDE_GATE": upside_gate(),
        "D_NONWORSE_GATE": d_nonworse_gate(),
        "DEVELOPMENT_PASS_GATE": development_pass_gate(),
        "CONDITIONAL_QUALITY_DIAGNOSTIC": "Report COND_U / COND_D on actual fills. Primary remains EXEC_U.",
    }


def decide(*, parity_ok: bool, geometry_ok: bool, exec_ok: bool) -> dict[str, Any]:
    if not parity_ok or not geometry_ok or not exec_ok:
        return {
            "CASE": "E",
            "VERDICT": "AM_DIRECT_EXEC_U_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": "Frozen geometry or FILL_ONLY EXEC parity did not reproduce.",
            "note": "STOP. Integrity failed. Target not precommitted.",
        }
    return {
        "CASE": "PRECOMMIT",
        "VERDICT": VERDICT_OK,
        "NEXT_RESEARCH": NEXT_RESEARCH,
        "PRIMARY_FINDING": (
            "AM W5 ENTRY is precommitted as candidate-level TARGET_EXEC_U = I(fill5)*U_FILL, "
            "RandomForestRegressor with frozen learnability hyperparameters, pred_EXEC_U Top3. "
            "Primary reference is FILL_ONLY. Stage2 remains closed. D is diagnostic/non-worsening only. "
            "This run does not train."
        ),
        "note": "STOP. No development performance run. No EXEC_U fit. No W5 runtime adoption.",
    }
