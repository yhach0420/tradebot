"""Offline AM direct EXEC_U target precommit. No Runtime write. No training. No Exact."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_direct_exec_u_target_precommit import (
    ALLOWED_DEVELOPMENT_ARMS,
    ANALYSIS_ID,
    ARCHITECTURE,
    BEST_REPRESENTATION_ADOPTED,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    D_ROLE,
    D_TRAINING_TARGET_ALLOWED,
    D_WEIGHT_ALLOWED,
    DEVELOPMENT_RUN_STARTED,
    DEV_WAIT_SEC,
    DIRECT_TARGET,
    DIRECT_TARGET_FORMULA,
    ENTRY_RETRAIN_STARTED,
    EXACT_RAN,
    FEATURE_SEARCH,
    FILL_LOSS_ALLOWED_IN_PASS_GATE,
    FINAL_SELECTION_N,
    HYPERPARAMETER_TUNING,
    JOINT_BINARY_TARGET,
    LOCK_SLOT_SEARCH,
    MINIMAL_SWAP_STAGE2_ALLOWED,
    MODEL_FAMILY,
    MODEL_HYPERPARAMETERS_FROZEN,
    MODEL_REFIT_N,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    NEXT_RESEARCH,
    NONFILL_TARGET_VALUE,
    OLD_TWO_STAGE_ALLOWED,
    P_FILL_TIMES_U_PRED,
    PAPER_OPERATED,
    PNL_USED,
    PRIMARY_REFERENCE,
    PRIOR_FILLABILITY_ID,
    PRIOR_GEOMETRY_ID,
    PRIOR_GEOMETRY_VERDICT,
    REPRESENTATION_N,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    SELECTION_RULE,
    SESSION,
    SHORTLIST_SEARCH,
    STAGE1_ALLOWED,
    STAGE2_ALLOWED,
    STRATEGY_CREATED,
    THRESHOLD_SEARCH,
    TOPK_SEARCH,
    TRUE_OOS,
    VERDICT_OK,
    WAIT_POLICY_ADOPTED,
    WEIGHT_SEARCH,
    W5_RUNTIME_ADOPTED,
)
from research.am_direct_exec_u_target_precommit.analyze import (
    decide,
    development_pass_gate,
    d_nonworse_gate,
    fill_hard_gate,
    freeze_fill_only_exec,
    freeze_geometry,
    freeze_parity,
    objective_contract,
    rf_reg_frozen,
    target_examples,
    upside_gate,
)
from research.am_direct_exec_u_target_precommit.publish import (
    OUT,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.wait5_session_target_learnability import RF_REG_PARAMS
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import WAIT_SEC

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
GEOMETRY = NATIVE / "results" / "research" / "am_wait5_fill_upside_geometry" / "report.json"
FILLABILITY = NATIVE / "results" / "research" / "am_wait5_fillability_first_reassessment" / "report.json"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_report(
    required: dict,
    *,
    decision: dict,
    extra: dict | None = None,
    sheets_extra: dict | None = None,
) -> int:
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": required,
        "decision": decision,
        **(extra or {}),
    }
    report["_markdown"] = build_markdown(report)
    sheets = {
        "Summary": kv_rows(required),
        "Manifest": kv_rows(
            {
                "ANALYSIS_ID": ANALYSIS_ID,
                "SESSION": SESSION,
                "ARCHITECTURE": ARCHITECTURE,
                "DEV_WAIT_SEC": DEV_WAIT_SEC,
                "RUNTIME_WAIT_SEC": WAIT_SEC,
                "DIRECT_TARGET": DIRECT_TARGET,
                "DIRECT_TARGET_FORMULA": DIRECT_TARGET_FORMULA,
                "MODEL_FAMILY": MODEL_FAMILY,
                "REPRESENTATION_N": REPRESENTATION_N,
                "FINAL_SELECTION_N": FINAL_SELECTION_N,
                "SELECTION_RULE": SELECTION_RULE,
                "PRIMARY_REFERENCE": PRIMARY_REFERENCE,
                "STAGE1_ALLOWED": STAGE1_ALLOWED,
                "STAGE2_ALLOWED": STAGE2_ALLOWED,
                "D_TRAINING_TARGET_ALLOWED": D_TRAINING_TARGET_ALLOWED,
                "DEVELOPMENT_RUN_STARTED": DEVELOPMENT_RUN_STARTED,
                "MODEL_REFIT_N": MODEL_REFIT_N,
                "PNL_USED": PNL_USED,
                "EXACT_RAN": EXACT_RAN,
            }
        ),
        "Parity": [{"empty": True}],
        "Target": [{"empty": True}],
        "Model": [{"empty": True}],
        "Selection": [{"empty": True}],
        "DevelopmentArms": [{"empty": True}],
        "FutureGates": [{"empty": True}],
        "TargetExamples": [{"empty": True}],
        "Integrity": [{"empty": True}],
        "Decision": kv_rows(decision),
        "Safety": kv_rows(
            {
                "submit_cancel_live": "0/0/0",
                "Paper": 0,
                "OPVAL": 0,
                "C14_CHANGED": C14_CHANGED,
                "RUNTIME_CHANGED": RUNTIME_CHANGED,
                "C4_STARTED": C4_STARTED,
                "EXACT_RAN": EXACT_RAN,
                "WAIT_POLICY_ADOPTED": WAIT_POLICY_ADOPTED,
                "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
                "ENTRY_RETRAIN_STARTED": ENTRY_RETRAIN_STARTED,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "FEATURE_SEARCH": FEATURE_SEARCH,
                "PNL_USED": PNL_USED,
                "BEST_REPRESENTATION_ADOPTED": BEST_REPRESENTATION_ADOPTED,
                "HYPERPARAMETER_TUNING": HYPERPARAMETER_TUNING,
                "TOPK_SEARCH": TOPK_SEARCH,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
                "WEIGHT_SEARCH": WEIGHT_SEARCH,
                "D_WEIGHT_ALLOWED": D_WEIGHT_ALLOWED,
                "P_FILL_TIMES_U_PRED": P_FILL_TIMES_U_PRED,
                "JOINT_BINARY_TARGET": JOINT_BINARY_TARGET,
                "SHORTLIST_SEARCH": SHORTLIST_SEARCH,
                "LOCK_SLOT_SEARCH": LOCK_SLOT_SEARCH,
                "OLD_TWO_STAGE_ALLOWED": OLD_TWO_STAGE_ALLOWED,
                "MINIMAL_SWAP_STAGE2_ALLOWED": MINIMAL_SWAP_STAGE2_ALLOWED,
                "STAGE1_ALLOWED": STAGE1_ALLOWED,
                "STAGE2_ALLOWED": STAGE2_ALLOWED,
                "FILL_LOSS_ALLOWED_IN_PASS_GATE": FILL_LOSS_ALLOWED_IN_PASS_GATE,
                "RUNTIME_CANDIDATE_CREATED": RUNTIME_CANDIDATE_CREATED,
                "STRATEGY_CREATED": STRATEGY_CREATED,
                "DEVELOPMENT_RUN_STARTED": DEVELOPMENT_RUN_STARTED,
                "MODEL_REFIT_N": MODEL_REFIT_N,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"VERDICT={required.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(
        "STOP. No EXEC_U training. No development performance. Runtime WAIT_SEC=1.0. "
        "W5 not adopted. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "AM_DIRECT_EXEC_U_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str, extra: dict | None = None) -> int:
    body = {
        "BASE_PARITY": False,
        "SESSION": SESSION,
        "DEV_WAIT_SEC": DEV_WAIT_SEC,
        "RUNTIME_WAIT_SEC": WAIT_SEC,
        "DIRECT_TARGET": DIRECT_TARGET,
        "DIRECT_TARGET_FORMULA": DIRECT_TARGET_FORMULA,
        "NONFILL_TARGET_VALUE": NONFILL_TARGET_VALUE,
        "MODEL_FAMILY": MODEL_FAMILY,
        "MODEL_HYPERPARAMETERS_FROZEN": MODEL_HYPERPARAMETERS_FROZEN,
        "REPRESENTATION_N": REPRESENTATION_N,
        "FINAL_SELECTION_N": FINAL_SELECTION_N,
        "SELECTION_RULE": SELECTION_RULE,
        "STAGE1_ALLOWED": STAGE1_ALLOWED,
        "STAGE2_ALLOWED": STAGE2_ALLOWED,
        "D_TRAINING_TARGET_ALLOWED": D_TRAINING_TARGET_ALLOWED,
        "D_ROLE": D_ROLE,
        "FILL_LOSS_ALLOWED_IN_PASS_GATE": FILL_LOSS_ALLOWED_IN_PASS_GATE,
        "ALLOWED_DEVELOPMENT_ARMS": list(ALLOWED_DEVELOPMENT_ARMS),
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "AM_DIRECT_EXEC_U_INTEGRITY_FAILED",
        "PRIMARY_FINDING": note,
        "MODEL_REFIT_N": MODEL_REFIT_N,
    }
    if extra:
        body.update(extra)
    return write_report(
        body,
        decision={
            "CASE": "E",
            "VERDICT": "AM_DIRECT_EXEC_U_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": note,
            "note": "STOP. Integrity failed.",
        },
        extra=extra,
    )


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM DIRECT EXEC_U TARGET PRECOMMIT V1", flush=True)
    print("AM only. No training. No PnL. No Exact. Freeze EXEC_U_TARGET + pred_EXEC_U Top3.", flush=True)

    if abs(float(WAIT_SEC) - 1.0) > 1e-12:
        print("STOP WAIT_SEC drift", flush=True)
        return 2
    if abs(float(DEV_WAIT_SEC) - 5.0) > 1e-12:
        print("STOP DEV_WAIT_SEC drift", flush=True)
        return 2
    if STAGE1_ALLOWED or STAGE2_ALLOWED or OLD_TWO_STAGE_ALLOWED or MINIMAL_SWAP_STAGE2_ALLOWED:
        print("STOP Stage1/Stage2 must remain closed", flush=True)
        return 2
    if D_TRAINING_TARGET_ALLOWED or D_WEIGHT_ALLOWED or P_FILL_TIMES_U_PRED:
        print("STOP D-training / D-weight / P_FILL×U flags must remain false", flush=True)
        return 2
    if DEVELOPMENT_RUN_STARTED or int(MODEL_REFIT_N) != 0:
        print("STOP this precommit must not start development or refit", flush=True)
        return 2
    if list(FEATURE_ORDER) != [
        "spread_bps",
        "imbalance",
        "mid_ret_60s",
        "mid_ret_180s",
        "event_rate_60s",
        "log_bid_qty",
    ]:
        print("STOP FEATURE_ORDER drift", flush=True)
        return 2
    if ENTRY_BINDING.get("rank_pass_gate") is not None:
        print("STOP rank_pass_gate drift", flush=True)
        return 2
    c14 = _load(C14)
    if str(c14.get("candidate_id") or "") != C14_ID:
        print("STOP: C14 identity mismatch", flush=True)
        return 2
    if int(RF_REG_PARAMS["n_estimators"]) != 500 or int(RF_REG_PARAMS["max_depth"]) != 6:
        return _integrity("STOP. Frozen RF_REG_PARAMS drifted from wait5 session-target learnability.")

    geo = _load(GEOMETRY)
    preg = geo.get("required") or {}
    geo_pack = freeze_geometry(preg, str(geo.get("ANALYSIS_ID") or ""), geo.get("decision") or {})
    print("geometry", geo_pack.get("ok"), geo_pack.get("checks"), flush=True)
    if not geo_pack.get("ok"):
        return _integrity(
            f"STOP. Prior {PRIOR_GEOMETRY_ID} / {PRIOR_GEOMETRY_VERDICT} required.",
            extra={"geometry": geo_pack},
        )

    fill = _load(FILLABILITY)
    if str(fill.get("ANALYSIS_ID") or "") != PRIOR_FILLABILITY_ID:
        return _integrity(
            f"STOP. Prior {PRIOR_FILLABILITY_ID} required for FILL_ONLY EXEC parity.",
            extra={"fillability_id": fill.get("ANALYSIS_ID")},
        )
    exec_pack = freeze_fill_only_exec(fill.get("required") or {})
    print("fill_only_exec", exec_pack.get("ok"), exec_pack.get("checks"), flush=True)
    if not exec_pack.get("ok"):
        return _integrity("STOP. Frozen FILL_ONLY EXEC_U/D did not reproduce.", extra={"fill_only_exec": exec_pack})

    obs = {
        "FILL_ONLY_FILL_RATE": preg.get("FILL_ONLY_FILL_RATE"),
        "FILL_ONLY_EXEC_U": (fill.get("required") or {}).get("FILL_ONLY_EXEC_U"),
        "FILL_ONLY_EXEC_D": (fill.get("required") or {}).get("FILL_ONLY_EXEC_D"),
        "FILL_NONWORSE_U_IMPROVE_RATE": preg.get("FILL_NONWORSE_U_IMPROVE_RATE"),
        "SAME_FILL_U_IMPROVE_RATE": preg.get("SAME_FILL_U_IMPROVE_RATE"),
        "FILL_NONWORSE_U_D_NONWORSE_RATE": preg.get("FILL_NONWORSE_U_D_NONWORSE_RATE"),
        "FILL_ONLY_DOMINATED_IN_FILL_U_RATE": preg.get("FILL_ONLY_DOMINATED_IN_FILL_U_RATE"),
    }
    par = freeze_parity(obs)
    print("parity", par.get("ok"), par.get("checks"), flush=True)
    if not par.get("ok"):
        return _integrity("STOP. Frozen geometry / FILL_ONLY headline parity did not reproduce.", extra={"parity": par})

    contract = objective_contract()
    examples = target_examples()
    decision = decide(parity_ok=True, geometry_ok=True, exec_ok=True)
    required = {
        "BASE_PARITY": True,
        "SESSION": SESSION,
        "DEV_WAIT_SEC": DEV_WAIT_SEC,
        "RUNTIME_WAIT_SEC": WAIT_SEC,
        "DIRECT_TARGET": DIRECT_TARGET,
        "DIRECT_TARGET_FORMULA": DIRECT_TARGET_FORMULA,
        "NONFILL_TARGET_VALUE": NONFILL_TARGET_VALUE,
        "MODEL_FAMILY": MODEL_FAMILY,
        "MODEL_HYPERPARAMETERS_FROZEN": MODEL_HYPERPARAMETERS_FROZEN,
        "REPRESENTATION_N": REPRESENTATION_N,
        "FINAL_SELECTION_N": FINAL_SELECTION_N,
        "SELECTION_RULE": SELECTION_RULE,
        "STAGE1_ALLOWED": STAGE1_ALLOWED,
        "STAGE2_ALLOWED": STAGE2_ALLOWED,
        "D_TRAINING_TARGET_ALLOWED": D_TRAINING_TARGET_ALLOWED,
        "D_ROLE": D_ROLE,
        "FILL_LOSS_ALLOWED_IN_PASS_GATE": FILL_LOSS_ALLOWED_IN_PASS_GATE,
        "ALLOWED_DEVELOPMENT_ARMS": list(ALLOWED_DEVELOPMENT_ARMS),
        "NEXT_RESEARCH": NEXT_RESEARCH,
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": VERDICT_OK,
        "PRIMARY_REFERENCE": PRIMARY_REFERENCE,
        "ARCHITECTURE": ARCHITECTURE,
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
        "MODEL_REFIT_N": MODEL_REFIT_N,
        "DEVELOPMENT_RUN_STARTED": DEVELOPMENT_RUN_STARTED,
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "FILL_ONLY_FILL_RATE": obs.get("FILL_ONLY_FILL_RATE"),
        "FILL_ONLY_EXEC_U": obs.get("FILL_ONLY_EXEC_U"),
        "FILL_ONLY_EXEC_D": obs.get("FILL_ONLY_EXEC_D"),
        "FILL_NONWORSE_U_IMPROVE_RATE": obs.get("FILL_NONWORSE_U_IMPROVE_RATE"),
        "SAME_FILL_U_IMPROVE_RATE": obs.get("SAME_FILL_U_IMPROVE_RATE"),
        "FILL_NONWORSE_U_D_NONWORSE_RATE": obs.get("FILL_NONWORSE_U_D_NONWORSE_RATE"),
        "FILL_ONLY_DOMINATED_IN_FILL_U_RATE": obs.get("FILL_ONLY_DOMINATED_IN_FILL_U_RATE"),
    }
    print(f"CASE={decision.get('CASE')} VERDICT={decision.get('VERDICT')}", flush=True)
    return write_report(
        required,
        decision=decision,
        extra={
            "parity": par,
            "geometry": geo_pack,
            "fill_only_exec": exec_pack,
            "contract": contract,
            "rf_reg": rf_reg_frozen(),
        },
        sheets_extra={
            "Parity": kv_rows({**par.get("checks"), **{f"OBS_{k}": v for k, v in (par.get("observed") or {}).items()}}),
            "Target": kv_rows(
                {
                    "DIRECT_TARGET": DIRECT_TARGET,
                    "DIRECT_TARGET_FORMULA": DIRECT_TARGET_FORMULA,
                    "TARGET_DEFINITION": "TARGET_EXEC_U = I(Y_FILL5=1) * U_FILL",
                    "U_FILL_SOURCE": "POSTFILL_MFE_600",
                    "Y_FILL5_SOURCE": "W5 corrected Passive Fill",
                    "NONFILL_TARGET_VALUE": NONFILL_TARGET_VALUE,
                    "NONFILL_IS_PENALTY": False,
                    "PNL": False,
                    "D_ROLE": D_ROLE,
                    "D_TRAINING_TARGET_ALLOWED": D_TRAINING_TARGET_ALLOWED,
                    "STAGE1_STAGE2_SCALAR_COMBINATION": False,
                }
            ),
            "Model": kv_rows(
                {
                    "MODEL_FAMILY": MODEL_FAMILY,
                    **rf_reg_frozen(),
                    "MODEL_HYPERPARAMETERS_FROZEN": MODEL_HYPERPARAMETERS_FROZEN,
                    "SOURCE": "WAIT5_SESSION_TARGET_LEARNABILITY_V1 RF_REG_PARAMS",
                    "FIT_THIS_RUN": False,
                    "REPRESENTATION_N": REPRESENTATION_N,
                    "BEST_REPRESENTATION_ADOPTED": BEST_REPRESENTATION_ADOPTED,
                    "HYPERPARAMETER_TUNING": HYPERPARAMETER_TUNING,
                    "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                }
            ),
            "Selection": kv_rows(
                {
                    "FINAL_SELECTION_N": FINAL_SELECTION_N,
                    "SELECTION_RULE": SELECTION_RULE,
                    "ORDER": "predicted EXEC_U descending, tie symbol ASC",
                    "THRESHOLD": False,
                    "SHORTLIST": False,
                    "STAGE1_ALLOWED": STAGE1_ALLOWED,
                    "STAGE2_ALLOWED": STAGE2_ALLOWED,
                    "TOPK_SEARCH": TOPK_SEARCH,
                    "PRIMARY_REFERENCE": PRIMARY_REFERENCE,
                }
            ),
            "DevelopmentArms": [
                {"arm": "CONTROL", "definition": "CURRENT Top3", "role": "baseline"},
                {"arm": "FILL_ONLY", "definition": "P_FILL5 Top3", "role": "primary reference"},
                {"arm": "DIRECT_EXEC_U", "definition": "pred_EXEC_U Top3", "role": "candidate architecture"},
            ],
            "FutureGates": kv_rows(
                {
                    **{f"FILL_{k}": v for k, v in fill_hard_gate().items()},
                    **{f"UPSIDE_{k}": v for k, v in upside_gate().items()},
                    **{f"D_{k}": v for k, v in d_nonworse_gate().items()},
                    **{f"PASS_{k}": v for k, v in development_pass_gate().items()},
                }
            ),
            "TargetExamples": examples,
            "Integrity": kv_rows(
                {
                    "MODEL_REFIT_N": MODEL_REFIT_N,
                    "DEVELOPMENT_RUN_STARTED": DEVELOPMENT_RUN_STARTED,
                    "SESSION": SESSION,
                    "RUNTIME_WAIT_SEC": WAIT_SEC,
                    "DEV_WAIT_SEC": DEV_WAIT_SEC,
                    "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
                    "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
                    "STAGE1_ALLOWED": STAGE1_ALLOWED,
                    "STAGE2_ALLOWED": STAGE2_ALLOWED,
                    "D_TRAINING_TARGET_ALLOWED": D_TRAINING_TARGET_ALLOWED,
                    "PNL_USED": PNL_USED,
                    "EXACT_RAN": EXACT_RAN,
                    "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                    "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
                    "RF_N_ESTIMATORS": RF_REG_PARAMS["n_estimators"],
                    "RF_MAX_DEPTH": RF_REG_PARAMS["max_depth"],
                    "RF_MIN_SAMPLES_LEAF": RF_REG_PARAMS["min_samples_leaf"],
                }
            ),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
