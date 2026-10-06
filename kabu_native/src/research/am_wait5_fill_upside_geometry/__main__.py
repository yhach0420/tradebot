"""Offline AM WAIT5 fill-upside geometry audit. Frozen OOF. Oracle diagnostic only."""
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

from research.am_wait5_fill_upside_geometry import (
    ANALYSIS_ID,
    BEST_REPRESENTATION_ADOPTED,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    DEV_WAIT_SEC,
    DIRECT_EXEC_U_TRAINING,
    ELIGIBLE_DAYS,
    EXACT_RAN,
    FEATURE_SEARCH,
    FINAL_MODEL_ADOPTED,
    FROZEN_OOF_REPLAY_ONLY,
    HYPERPARAMETER_TUNING,
    LOCK_SLOT_SEARCH,
    MAJORITY_RATE,
    MINIMAL_SWAP_STAGE2_ALLOWED,
    MODEL_REFIT_N,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    NEW_OBJECTIVE_CREATED,
    OLD_TWO_STAGE_ALLOWED,
    ORACLE_USED_AS_STRATEGY,
    PAPER_OPERATED,
    PARITY_ABS_TOL,
    P_FILL_TIMES_U_PRED,
    PNL_USED,
    PRIOR_ANALYSIS_ID,
    PRIOR_VERDICT,
    RANK_BUCKETS,
    REPRESENTATION_N,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    SESSION,
    SHORTLIST_SEARCH,
    STAGE2_ARM_ALLOWED,
    STRATEGY_CREATED,
    THRESHOLD_SEARCH,
    TOPK_SEARCH,
    TRUE_OOS,
    WAIT_POLICY_ADOPTED,
    W5_RUNTIME_ADOPTED,
    WEIGHT_SEARCH,
)
from research.am_wait5_fill_upside_geometry.analyze import (
    decide,
    freeze_parity,
    oracle_day_robust,
    spec_rows,
    _consensus_spearman,
)
from research.am_wait5_fill_upside_geometry.geometry import evaluate_scored
from research.am_wait5_fill_upside_geometry.publish import (
    OUT,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.am_wait5_two_stage_development.analyze import _med_key
from research.canonical_entry_performance_rebase.analyze import session_of
from research.direct_joint_objective.oof import representation_grid
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.passive_wait_policy_reassessment.analyze import _close
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import WAIT_SEC

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
PRIOR = NATIVE / "results" / "research" / "am_wait5_fillability_first_reassessment" / "report.json"
DEV_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_wait5_two_stage_development"
OOF_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_wait5_stage2_reassessment"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _cache_name(rid: str) -> str:
    return "AM_" + rid.replace("|", "_").replace(" ", "") + "_scored.json"


def _dev_name(rid: str) -> str:
    return "AM_" + rid.replace("|", "_").replace(" ", "") + ".json"


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
                "DEV_WAIT_SEC": DEV_WAIT_SEC,
                "RUNTIME_WAIT_SEC": WAIT_SEC,
                "REPRESENTATION_N": REPRESENTATION_N,
                "MAJORITY_RATE": MAJORITY_RATE,
                "RANK_BUCKETS": list(RANK_BUCKETS),
                "ORACLE_USED_AS_STRATEGY": ORACLE_USED_AS_STRATEGY,
                "DIRECT_EXEC_U_TRAINING": DIRECT_EXEC_U_TRAINING,
                "OLD_TWO_STAGE_ALLOWED": OLD_TWO_STAGE_ALLOWED,
                "STAGE2_ARM_ALLOWED": STAGE2_ARM_ALLOWED,
                "FROZEN_OOF_REPLAY_ONLY": FROZEN_OOF_REPLAY_ONLY,
                "MODEL_REFIT_N": MODEL_REFIT_N,
                "PNL_USED": PNL_USED,
                "EXACT_RAN": EXACT_RAN,
            }
        ),
        "Parity": [{"empty": True}],
        "Spearman": [{"empty": True}],
        "RankBuckets": [{"empty": True}],
        "Swap": [{"empty": True}],
        "Oracle": [{"empty": True}],
        "Frontier": [{"empty": True}],
        "DayRobustness": [{"empty": True}],
        "Representations": [{"empty": True}],
        "Gates": [{"empty": True}],
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
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "NEW_OBJECTIVE_CREATED": NEW_OBJECTIVE_CREATED,
                "DIRECT_EXEC_U_TRAINING": DIRECT_EXEC_U_TRAINING,
                "P_FILL_TIMES_U_PRED": P_FILL_TIMES_U_PRED,
                "ORACLE_USED_AS_STRATEGY": ORACLE_USED_AS_STRATEGY,
                "FEATURE_SEARCH": FEATURE_SEARCH,
                "PNL_USED": PNL_USED,
                "BEST_REPRESENTATION_ADOPTED": BEST_REPRESENTATION_ADOPTED,
                "HYPERPARAMETER_TUNING": HYPERPARAMETER_TUNING,
                "TOPK_SEARCH": TOPK_SEARCH,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
                "WEIGHT_SEARCH": WEIGHT_SEARCH,
                "LOCK_SLOT_SEARCH": LOCK_SLOT_SEARCH,
                "SHORTLIST_SEARCH": SHORTLIST_SEARCH,
                "OLD_TWO_STAGE_ALLOWED": OLD_TWO_STAGE_ALLOWED,
                "MINIMAL_SWAP_STAGE2_ALLOWED": MINIMAL_SWAP_STAGE2_ALLOWED,
                "STAGE2_ARM_ALLOWED": STAGE2_ARM_ALLOWED,
                "RUNTIME_CANDIDATE_CREATED": RUNTIME_CANDIDATE_CREATED,
                "STRATEGY_CREATED": STRATEGY_CREATED,
                "FINAL_MODEL_ADOPTED": FINAL_MODEL_ADOPTED,
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
        "STOP. Oracle diagnostic only. No Exact. No PnL. No Stage2. No EXEC_U training. "
        "Runtime WAIT_SEC=1.0. W5 not adopted. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "AM_FILL_UPSIDE_GEOMETRY_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str, extra: dict | None = None) -> int:
    body = {
        "BASE_PARITY": False,
        "P_FILL_U_SPEARMAN_MEDIAN": None,
        "P_FILL_D_SPEARMAN_MEDIAN": None,
        "R1_3_FILL_RATE": None,
        "R1_3_COND_U": None,
        "R4_5_FILL_RATE": None,
        "R4_5_COND_U": None,
        "FILL_NONWORSE_U_IMPROVE_COHORT_N": None,
        "FILL_NONWORSE_U_IMPROVE_RATE": None,
        "SAME_FILL_U_IMPROVE_COHORT_N": None,
        "SAME_FILL_U_IMPROVE_RATE": None,
        "FILL_NONWORSE_U_D_NONWORSE_RATE": None,
        "SAME_FILL_U_D_NONWORSE_RATE": None,
        "ORACLE_EXEC_U_DELTA_MEDIAN": None,
        "ORACLE_EXEC_U_EX_BEST_DAY": None,
        "ORACLE_EXEC_U_EX_TOP3_DAYS": None,
        "FILL_ONLY_DOMINATED_IN_FILL_U_RATE": None,
        "PRIMARY_MECHANISM": "INTEGRITY_FAIL",
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "AM_FILL_UPSIDE_GEOMETRY_INTEGRITY_FAILED",
        "PRIMARY_FINDING": note,
        "MODEL_REFIT_N": MODEL_REFIT_N,
        "PM_ROWS_USED_N": None,
        "FUTURE_EVENT_USE_N": None,
        "TARGET_CONTAMINATION_N": None,
        "HELDOUT_FIT_LEAK_N": None,
        "ORACLE_SUBSET_ENUMERATION_ERROR_N": None,
    }
    if extra:
        body.update(extra)
    return write_report(
        body,
        decision={
            "CASE": "E",
            "PRIMARY_MECHANISM": "INTEGRITY_FAIL",
            "NEXT_RESEARCH": "NONE",
            "VERDICT": "AM_FILL_UPSIDE_GEOMETRY_INTEGRITY_FAILED",
            "PRIMARY_FINDING": note,
            "note": "CASE E. STOP. Integrity failed.",
        },
        extra=extra,
    )


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM WAIT5 FILL-UPSIDE GEOMETRY AUDIT V1", flush=True)
    print("Frozen OOF. Oracle Top3 diagnostic. No Stage2. No refit. No Exact. No PnL.", flush=True)

    if abs(float(WAIT_SEC) - 1.0) > 1e-12:
        print("STOP WAIT_SEC drift", flush=True)
        return 2
    if abs(float(DEV_WAIT_SEC) - 5.0) > 1e-12:
        print("STOP DEV_WAIT_SEC drift", flush=True)
        return 2
    if OLD_TWO_STAGE_ALLOWED or MINIMAL_SWAP_STAGE2_ALLOWED or STAGE2_ARM_ALLOWED:
        print("STOP Stage2 arm must remain closed", flush=True)
        return 2
    if DIRECT_EXEC_U_TRAINING or ORACLE_USED_AS_STRATEGY or P_FILL_TIMES_U_PRED:
        print("STOP training / oracle-as-strategy flags must remain false", flush=True)
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
    prior = _load(PRIOR)
    preg = prior.get("required") or {}
    if str(prior.get("ANALYSIS_ID") or "") != PRIOR_ANALYSIS_ID or str(preg.get("VERDICT") or "") != PRIOR_VERDICT:
        return _integrity(
            f"STOP. Prior {PRIOR_ANALYSIS_ID} / {PRIOR_VERDICT} required.",
            extra={"prior_id": prior.get("ANALYSIS_ID"), "prior_verdict": preg.get("VERDICT")},
        )
    grid = representation_grid()
    if len(grid) != 9:
        return _integrity("STOP. Representation grid is not 9.")

    leak = {
        "MODEL_REFIT_N": int(MODEL_REFIT_N),
        "PM_ROWS_USED_N": 0,
        "FUTURE_EVENT_USE_N": 0,
        "TARGET_CONTAMINATION_N": 0,
        "HELDOUT_FIT_LEAK_N": 0,
        "ORACLE_SUBSET_ENUMERATION_ERROR_N": 0,
        "STAGE2_NONFILL_TARGET_TRAIN_N": 0,
    }
    got = []
    for spec in grid:
        rid = str(spec.get("representation_id"))
        saved = _load(OOF_CACHE / _cache_name(rid))
        if not (saved.get("ok") and saved.get("representation_id") == rid and saved.get("rows")):
            return _integrity(f"STOP. Frozen OOF scored cache missing for {rid}.")
        rec_leak = saved.get("integrity") or {}
        for k in ("TARGET_CONTAMINATION_N", "HELDOUT_FIT_LEAK_N", "STAGE2_NONFILL_TARGET_TRAIN_N"):
            leak[k] += int(rec_leak.get(k) or 0)
        rows = list(saved.get("rows") or [])
        leak["PM_ROWS_USED_N"] += sum(1 for r in rows if session_of(r) != "AM")
        leak["FUTURE_EVENT_USE_N"] += sum(1 for r in rows if r.get("future_event_use"))
        body = evaluate_scored(rows, list(ELIGIBLE_DAYS))
        body["ok"] = True
        body["representation_id"] = rid
        body["feature_set"] = spec.get("feature_set")
        body["normalization"] = spec.get("normalization")
        leak["ORACLE_SUBSET_ENUMERATION_ERROR_N"] += int(
            (body.get("oracle") or {}).get("ORACLE_SUBSET_ENUMERATION_ERROR_N") or 0
        )
        prior_arm = _load(DEV_CACHE / _dev_name(rid))
        fo = (body.get("FILL_ONLY") or {}).get("SELECTED_FILL_RATE")
        ctrl = (body.get("CONTROL") or {}).get("SELECTED_FILL_RATE")
        pfo = (prior_arm.get("FILL_ONLY") or {}).get("SELECTED_FILL_RATE")
        pctrl = (prior_arm.get("CONTROL") or {}).get("SELECTED_FILL_RATE")
        if not _close(fo, pfo, PARITY_ABS_TOL) or not _close(ctrl, pctrl, PARITY_ABS_TOL):
            return _integrity(
                "STOP. Replayed CONTROL / FILL_ONLY did not match frozen development cache.",
                extra={"representation_id": rid, "fo": fo, "dev_fo": pfo, "ctrl": ctrl, "dev_ctrl": pctrl},
            )
        got.append(body)
        oc = body.get("oracle") or {}
        print(
            f"eval {rid} ctrl={ctrl} fo={fo} "
            f"fn_u={oc.get('FILL_NONWORSE_U_IMPROVE_RATE')} "
            f"same_u={oc.get('SAME_FILL_U_IMPROVE_RATE')} "
            f"dom={oc.get('FILL_ONLY_DOMINATED_IN_FILL_U_RATE')} "
            f"enum_err={oc.get('ORACLE_SUBSET_ENUMERATION_ERROR_N')}",
            flush=True,
        )
    if len(got) != 9:
        return _integrity("STOP. Did not evaluate 9 representations.")

    specs = spec_rows(got)
    arm_obs = {
        "CONTROL_FILL_RATE": _med_key(specs, "CONTROL_FILL_RATE"),
        "FILL_ONLY_FILL_RATE": _med_key(specs, "FILL_ONLY_FILL_RATE"),
        "NET_ADDITIONAL_FILL_N": _med_key(specs, "NET_ADDITIONAL_FILL_N"),
        "CONTROL_COND_U": _med_key(specs, "CONTROL_COND_U"),
        "FILL_ONLY_COND_U": _med_key(specs, "FILL_ONLY_COND_U"),
        "DELTA_COND_U": _med_key(specs, "DELTA_COND_U"),
        "CONTROL_COND_D": _med_key(specs, "CONTROL_COND_D"),
        "FILL_ONLY_COND_D": _med_key(specs, "FILL_ONLY_COND_D"),
        "DELTA_COND_D": _med_key(specs, "DELTA_COND_D"),
    }
    par = freeze_parity(arm_obs)
    print("parity", par.get("ok"), par.get("checks"), flush=True)
    if not par.get("ok"):
        return _integrity("STOP. Frozen CONTROL / FILL_ONLY / COND parity did not reproduce.", extra={"parity": par})
    print("integrity", leak, flush=True)
    if any(
        int(leak.get(k) or 0) != 0
        for k in (
            "MODEL_REFIT_N",
            "PM_ROWS_USED_N",
            "FUTURE_EVENT_USE_N",
            "TARGET_CONTAMINATION_N",
            "HELDOUT_FIT_LEAK_N",
            "ORACLE_SUBSET_ENUMERATION_ERROR_N",
        )
    ):
        return _integrity("STOP. Isolation or oracle-enumeration integrity failed.", extra={"integrity": leak})

    day_rob = oracle_day_robust(got)
    u_sp_days, u_sp_st = _consensus_spearman(got, "U")
    d_sp_days, d_sp_st = _consensus_spearman(got, "D")
    fn_rate = _med_key(specs, "FILL_NONWORSE_U_IMPROVE_RATE")
    sm_rate = _med_key(specs, "SAME_FILL_U_IMPROVE_RATE")
    ud_rate = _med_key(specs, "FILL_NONWORSE_U_D_NONWORSE_RATE")
    decision = decide(
        parity_ok=True,
        leak={
            "MODEL_REFIT_N": leak.get("MODEL_REFIT_N"),
            "PM_ROWS_USED_N": leak.get("PM_ROWS_USED_N"),
            "FUTURE_EVENT_USE_N": leak.get("FUTURE_EVENT_USE_N"),
            "TARGET_CONTAMINATION_N": leak.get("TARGET_CONTAMINATION_N"),
            "HELDOUT_FIT_LEAK_N": leak.get("HELDOUT_FIT_LEAK_N"),
            "ORACLE_SUBSET_ENUMERATION_ERROR_N": leak.get("ORACLE_SUBSET_ENUMERATION_ERROR_N"),
        },
        fill_nonworse_rate=fn_rate,
        same_fill_rate=sm_rate,
        fill_nonworse_ud_rate=ud_rate,
        day_robust=day_rob,
    )
    required = {
        "BASE_PARITY": True,
        "P_FILL_U_SPEARMAN_MEDIAN": _med_key(specs, "P_FILL_U_SPEARMAN"),
        "P_FILL_D_SPEARMAN_MEDIAN": _med_key(specs, "P_FILL_D_SPEARMAN"),
        "R1_3_FILL_RATE": _med_key(specs, "R1_3_FILL_RATE"),
        "R1_3_COND_U": _med_key(specs, "R1_3_COND_U"),
        "R4_5_FILL_RATE": _med_key(specs, "R4_5_FILL_RATE"),
        "R4_5_COND_U": _med_key(specs, "R4_5_COND_U"),
        "FILL_NONWORSE_U_IMPROVE_COHORT_N": _med_key(specs, "FILL_NONWORSE_U_IMPROVE_COHORT_N"),
        "FILL_NONWORSE_U_IMPROVE_RATE": fn_rate,
        "SAME_FILL_U_IMPROVE_COHORT_N": _med_key(specs, "SAME_FILL_U_IMPROVE_COHORT_N"),
        "SAME_FILL_U_IMPROVE_RATE": sm_rate,
        "FILL_NONWORSE_U_D_NONWORSE_RATE": ud_rate,
        "SAME_FILL_U_D_NONWORSE_RATE": _med_key(specs, "SAME_FILL_U_D_NONWORSE_RATE"),
        "ORACLE_EXEC_U_DELTA_MEDIAN": _med_key(specs, "ORACLE_EXEC_U_DELTA_MEDIAN"),
        "ORACLE_EXEC_U_EX_BEST_DAY": day_rob.get("EX_BEST_DAY"),
        "ORACLE_EXEC_U_EX_TOP3_DAYS": day_rob.get("EX_TOP3_DAYS"),
        "FILL_ONLY_DOMINATED_IN_FILL_U_RATE": _med_key(specs, "FILL_ONLY_DOMINATED_IN_FILL_U_RATE"),
        "PRIMARY_MECHANISM": decision.get("PRIMARY_MECHANISM"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "CONTROL_FILL_RATE": arm_obs.get("CONTROL_FILL_RATE"),
        "FILL_ONLY_FILL_RATE": arm_obs.get("FILL_ONLY_FILL_RATE"),
        "NET_ADDITIONAL_FILL_N": arm_obs.get("NET_ADDITIONAL_FILL_N"),
        "CONTROL_COND_U": arm_obs.get("CONTROL_COND_U"),
        "FILL_ONLY_COND_U": arm_obs.get("FILL_ONLY_COND_U"),
        "DELTA_COND_U": arm_obs.get("DELTA_COND_U"),
        "CONTROL_COND_D": arm_obs.get("CONTROL_COND_D"),
        "FILL_ONLY_COND_D": arm_obs.get("FILL_ONLY_COND_D"),
        "DELTA_COND_D": arm_obs.get("DELTA_COND_D"),
        "R6_10_FILL_RATE": _med_key(specs, "R6_10_FILL_RATE"),
        "R6_10_COND_U": _med_key(specs, "R6_10_COND_U"),
        "R11_PLUS_FILL_RATE": _med_key(specs, "R11_PLUS_FILL_RATE"),
        "R11_PLUS_COND_U": _med_key(specs, "R11_PLUS_COND_U"),
        "P_FILL_U_DAILY_MEDIAN": _med_key(specs, "P_FILL_U_DAILY_MEDIAN"),
        "P_FILL_D_DAILY_MEDIAN": _med_key(specs, "P_FILL_D_DAILY_MEDIAN"),
        "P_FILL_U_COHORT_MEDIAN": _med_key(specs, "P_FILL_U_COHORT_MEDIAN"),
        "P_FILL_D_COHORT_MEDIAN": _med_key(specs, "P_FILL_D_COHORT_MEDIAN"),
        "P_FILL_U_POS_DAYS": int(u_sp_st.get("positive_days") or 0),
        "P_FILL_U_NEG_DAYS": int(u_sp_st.get("negative_days") or 0),
        "P_FILL_U_ZERO_DAYS": int(u_sp_st.get("zero_days") or 0),
        "P_FILL_D_POS_DAYS": int(d_sp_st.get("positive_days") or 0),
        "P_FILL_D_NEG_DAYS": int(d_sp_st.get("negative_days") or 0),
        "P_FILL_D_ZERO_DAYS": int(d_sp_st.get("zero_days") or 0),
        "CONTROL_ONLY_FILL_RATE": _med_key(specs, "CONTROL_ONLY_FILL_RATE"),
        "FILL_ONLY_ONLY_FILL_RATE": _med_key(specs, "FILL_ONLY_ONLY_FILL_RATE"),
        "CONTROL_ONLY_COND_U": _med_key(specs, "CONTROL_ONLY_COND_U"),
        "FILL_ONLY_ONLY_COND_U": _med_key(specs, "FILL_ONLY_ONLY_COND_U"),
        "ORACLE_EXEC_U_DELTA_MEAN": _med_key(specs, "ORACLE_EXEC_U_DELTA_MEAN"),
        "ORACLE_DAY_ROBUST_PASS": day_rob.get("PASS"),
        "FILL_NONWORSE_U_D_NONWORSE_COHORT_N": _med_key(specs, "FILL_NONWORSE_U_D_NONWORSE_COHORT_N"),
        "SAME_FILL_U_D_NONWORSE_COHORT_N": _med_key(specs, "SAME_FILL_U_D_NONWORSE_COHORT_N"),
        "MODEL_REFIT_N": leak.get("MODEL_REFIT_N"),
        "PM_ROWS_USED_N": leak.get("PM_ROWS_USED_N"),
        "FUTURE_EVENT_USE_N": leak.get("FUTURE_EVENT_USE_N"),
        "TARGET_CONTAMINATION_N": leak.get("TARGET_CONTAMINATION_N"),
        "HELDOUT_FIT_LEAK_N": leak.get("HELDOUT_FIT_LEAK_N"),
        "ORACLE_SUBSET_ENUMERATION_ERROR_N": leak.get("ORACLE_SUBSET_ENUMERATION_ERROR_N"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
    }
    print(
        f"CASE={decision.get('CASE')} fn_u={fn_rate} same_u={sm_rate} ud={ud_rate} "
        f"day_robust={day_rob.get('PASS')} VERDICT={decision.get('VERDICT')}",
        flush=True,
    )
    return write_report(
        required,
        decision=decision,
        extra={
            "parity": par,
            "oracle_day_robust": {k: v for k, v in day_rob.items() if k != "daily"},
            "integrity": leak,
        },
        sheets_extra={
            "Parity": kv_rows({**par.get("checks"), **{f"OBS_{k}": v for k, v in (par.get("observed") or {}).items()}}),
            "Spearman": [
                {
                    "representation_id": r.get("representation_id"),
                    "P_FILL_U_SPEARMAN": r.get("P_FILL_U_SPEARMAN"),
                    "P_FILL_D_SPEARMAN": r.get("P_FILL_D_SPEARMAN"),
                    "P_FILL_U_DAILY_MEDIAN": r.get("P_FILL_U_DAILY_MEDIAN"),
                    "P_FILL_D_DAILY_MEDIAN": r.get("P_FILL_D_DAILY_MEDIAN"),
                    "P_FILL_U_COHORT_MEDIAN": r.get("P_FILL_U_COHORT_MEDIAN"),
                    "P_FILL_D_COHORT_MEDIAN": r.get("P_FILL_D_COHORT_MEDIAN"),
                    "U_POS_DAYS": r.get("U_POS_DAYS"),
                    "U_NEG_DAYS": r.get("U_NEG_DAYS"),
                    "U_ZERO_DAYS": r.get("U_ZERO_DAYS"),
                    "D_POS_DAYS": r.get("D_POS_DAYS"),
                    "D_NEG_DAYS": r.get("D_NEG_DAYS"),
                    "D_ZERO_DAYS": r.get("D_ZERO_DAYS"),
                }
                for r in specs
            ],
            "RankBuckets": [
                {
                    "representation_id": r.get("representation_id"),
                    "R1_3_FILL_RATE": r.get("R1_3_FILL_RATE"),
                    "R1_3_COND_U": r.get("R1_3_COND_U"),
                    "R1_3_COND_U_MEDIAN": r.get("R1_3_COND_U_MEDIAN"),
                    "R1_3_COND_D": r.get("R1_3_COND_D"),
                    "R1_3_EXEC_U": r.get("R1_3_EXEC_U"),
                    "R1_3_EXEC_D": r.get("R1_3_EXEC_D"),
                    "R4_5_FILL_RATE": r.get("R4_5_FILL_RATE"),
                    "R4_5_COND_U": r.get("R4_5_COND_U"),
                    "R4_5_COND_D": r.get("R4_5_COND_D"),
                    "R6_10_FILL_RATE": r.get("R6_10_FILL_RATE"),
                    "R6_10_COND_U": r.get("R6_10_COND_U"),
                    "R11_PLUS_FILL_RATE": r.get("R11_PLUS_FILL_RATE"),
                    "R11_PLUS_COND_U": r.get("R11_PLUS_COND_U"),
                }
                for r in specs
            ],
            "Swap": [
                {
                    "representation_id": r.get("representation_id"),
                    "CONTROL_ONLY_FILL_RATE": r.get("CONTROL_ONLY_FILL_RATE"),
                    "FILL_ONLY_ONLY_FILL_RATE": r.get("FILL_ONLY_ONLY_FILL_RATE"),
                    "CONTROL_ONLY_COND_U": r.get("CONTROL_ONLY_COND_U"),
                    "FILL_ONLY_ONLY_COND_U": r.get("FILL_ONLY_ONLY_COND_U"),
                    "CONTROL_ONLY_COND_D": r.get("CONTROL_ONLY_COND_D"),
                    "FILL_ONLY_ONLY_COND_D": r.get("FILL_ONLY_ONLY_COND_D"),
                    "CONTROL_ONLY_EXEC_U": r.get("CONTROL_ONLY_EXEC_U"),
                    "FILL_ONLY_ONLY_EXEC_U": r.get("FILL_ONLY_ONLY_EXEC_U"),
                    "CONTROL_ONLY_EXEC_D": r.get("CONTROL_ONLY_EXEC_D"),
                    "FILL_ONLY_ONLY_EXEC_D": r.get("FILL_ONLY_ONLY_EXEC_D"),
                }
                for r in specs
            ],
            "Oracle": [
                {
                    "representation_id": r.get("representation_id"),
                    "FILL_NONWORSE_U_IMPROVE_COHORT_N": r.get("FILL_NONWORSE_U_IMPROVE_COHORT_N"),
                    "FILL_NONWORSE_U_IMPROVE_RATE": r.get("FILL_NONWORSE_U_IMPROVE_RATE"),
                    "SAME_FILL_U_IMPROVE_COHORT_N": r.get("SAME_FILL_U_IMPROVE_COHORT_N"),
                    "SAME_FILL_U_IMPROVE_RATE": r.get("SAME_FILL_U_IMPROVE_RATE"),
                    "FILL_NONWORSE_U_D_NONWORSE_RATE": r.get("FILL_NONWORSE_U_D_NONWORSE_RATE"),
                    "SAME_FILL_U_D_NONWORSE_RATE": r.get("SAME_FILL_U_D_NONWORSE_RATE"),
                    "ORACLE_EXEC_U_DELTA_MEDIAN": r.get("ORACLE_EXEC_U_DELTA_MEDIAN"),
                    "ORACLE_EXEC_U_DELTA_MEAN": r.get("ORACLE_EXEC_U_DELTA_MEAN"),
                    "ORACLE_SUBSET_ENUMERATION_ERROR_N": r.get("ORACLE_SUBSET_ENUMERATION_ERROR_N"),
                }
                for r in specs
            ],
            "Frontier": [
                {
                    "representation_id": r.get("representation_id"),
                    "FILL_ONLY_DOMINATED_IN_FILL_U_COHORT_N": r.get("FILL_ONLY_DOMINATED_IN_FILL_U_COHORT_N"),
                    "FILL_ONLY_DOMINATED_IN_FILL_U_RATE": r.get("FILL_ONLY_DOMINATED_IN_FILL_U_RATE"),
                }
                for r in specs
            ],
            "DayRobustness": (
                [{"scope": "ORACLE_EXEC_U_DELTA", **r} for r in (day_rob.get("daily") or [])]
                + [{"scope": "P_FILL_U_SPEARMAN", **r} for r in u_sp_days]
                + [{"scope": "P_FILL_D_SPEARMAN", **r} for r in d_sp_days]
            ),
            "Representations": specs,
            "Gates": kv_rows(
                {
                    **{f"ORACLE_DAY_{k}": v for k, v in (day_rob.get("gates") or {}).items()},
                    "ORACLE_DAY_ROBUST_PASS": day_rob.get("PASS"),
                    "FILL_NONWORSE_U_IMPROVE_RATE": fn_rate,
                    "SAME_FILL_U_IMPROVE_RATE": sm_rate,
                    "FILL_NONWORSE_U_D_NONWORSE_RATE": ud_rate,
                    "MAJORITY_RATE": MAJORITY_RATE,
                }
            ),
            "Integrity": kv_rows(
                {
                    **leak,
                    "SESSION": SESSION,
                    "RUNTIME_WAIT_SEC": WAIT_SEC,
                    "DEV_WAIT_SEC": DEV_WAIT_SEC,
                    "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
                    "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
                    "ORACLE_USED_AS_STRATEGY": ORACLE_USED_AS_STRATEGY,
                    "DIRECT_EXEC_U_TRAINING": DIRECT_EXEC_U_TRAINING,
                    "PNL_USED": PNL_USED,
                    "EXACT_RAN": EXACT_RAN,
                    "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                    "FROZEN_OOF_REPLAY_ONLY": FROZEN_OOF_REPLAY_ONLY,
                    "FINAL_MODEL_ADOPTED": FINAL_MODEL_ADOPTED,
                    "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
                }
            ),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
