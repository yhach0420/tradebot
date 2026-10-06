"""Offline AM WAIT5 fillability-first reassessment. Frozen OOF only. No Runtime write."""
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

from research.am_wait5_fillability_first_reassessment import (
    ANALYSIS_ID,
    BEST_REPRESENTATION_ADOPTED,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    DEV_WAIT_SEC,
    ELIGIBLE_DAYS,
    EXACT_RAN,
    FEATURE_SEARCH,
    FINAL_MODEL_ADOPTED,
    FROZEN_OOF_REPLAY_ONLY,
    HYPERPARAMETER_TUNING,
    LOCK_SLOT_SEARCH,
    MINIMAL_SWAP_STAGE2_ALLOWED,
    MODEL_REFIT_N,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    NEW_OBJECTIVE_CREATED,
    OLD_TWO_STAGE_ALLOWED,
    PAPER_OPERATED,
    PARITY_ABS_TOL,
    PNL_USED,
    PRIOR_DEVELOPMENT_ID,
    PRIOR_DEVELOPMENT_VERDICT,
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
from research.am_wait5_fillability_first_reassessment.analyze import (
    decide,
    freeze_parity,
    quality_pack,
    recon_violations,
    spec_rows,
    _fill_edge,
)
from research.am_wait5_fillability_first_reassessment.eval_oof import attach_ttf, evaluate_scored
from research.am_wait5_fillability_first_reassessment.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from research.am_wait5_two_stage_development.analyze import _med_key
from research.canonical_entry_performance_rebase.analyze import row_key, session_of, wf_index
from research.direct_joint_objective.oof import representation_grid
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.passive_wait_policy_reassessment.analyze import _close, wait_body
from research.wait5_execution_aware_rebase import DEV_WAIT_ID
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import WAIT_SEC

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
PRIOR = NATIVE / "results" / "research" / "am_wait5_minimal_swap_stage2_development" / "report.json"
DEV_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_wait5_two_stage_development"
OOF_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_wait5_stage2_reassessment"
WAIT_CACHE = NATIVE / "results" / "research" / "_work_cache" / "passive_wait_policy_reassessment"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _cache_name(rid: str) -> str:
    return "AM_" + rid.replace("|", "_").replace(" ", "") + "_scored.json"


def _dev_name(rid: str) -> str:
    return "AM_" + rid.replace("|", "_").replace(" ", "") + ".json"


def _drop_gates(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in {"gates", "daily", "PASS"}}


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
                "OLD_TWO_STAGE_ALLOWED": OLD_TWO_STAGE_ALLOWED,
                "MINIMAL_SWAP_STAGE2_ALLOWED": MINIMAL_SWAP_STAGE2_ALLOWED,
                "STAGE2_ARM_ALLOWED": STAGE2_ARM_ALLOWED,
                "FROZEN_OOF_REPLAY_ONLY": FROZEN_OOF_REPLAY_ONLY,
                "MODEL_REFIT_N": MODEL_REFIT_N,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "NEW_OBJECTIVE_CREATED": NEW_OBJECTIVE_CREATED,
                "PNL_USED": PNL_USED,
                "EXACT_RAN": EXACT_RAN,
                "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
            }
        ),
        "Parity": [{"empty": True}],
        "Selection": [{"empty": True}],
        "FillDecomp": [{"empty": True}],
        "Conditional": [{"empty": True}],
        "SwapFilled": [{"empty": True}],
        "Exposure": [{"empty": True}],
        "FillTime": [{"empty": True}],
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
        "STOP. No Exact. No PnL. No Stage2 restart. No new model. Runtime WAIT_SEC=1.0. "
        "W5 not adopted. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "AM_WAIT5_FILLABILITY_REASSESSMENT_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str, extra: dict | None = None) -> int:
    body = {
        "BASE_PARITY": False,
        "CONTROL_FILL_RATE": None,
        "FILL_ONLY_FILL_RATE": None,
        "NET_ADDITIONAL_FILL_N": None,
        "SELECTION_CHANGED_COHORT_RATE": None,
        "CONTROL_ONLY_FILL_RATE": None,
        "FILL_ONLY_ONLY_FILL_RATE": None,
        "CONTROL_COND_U": None,
        "FILL_ONLY_COND_U": None,
        "DELTA_COND_U": None,
        "CONTROL_COND_D": None,
        "FILL_ONLY_COND_D": None,
        "DELTA_COND_D": None,
        "DELTA_SWAP_COND_U": None,
        "DELTA_SWAP_COND_D": None,
        "EXEC_U_GAIN_FROM_EXTRA_FILL": None,
        "EXEC_U_GAIN_FROM_SELECTION_QUALITY": None,
        "EXEC_D_CHANGE_FROM_EXTRA_FILL": None,
        "EXEC_D_CHANGE_FROM_SELECTION_QUALITY": None,
        "RECONSTRUCTION_ERROR_U": None,
        "RECONSTRUCTION_ERROR_D": None,
        "FILL_POS_REP_N": None,
        "COND_U_NONNEG_REP_N": None,
        "COND_D_NONNEG_REP_N": None,
        "PRIMARY_MECHANISM": "INTEGRITY_FAIL",
        "AM_ARCHITECTURE": "NONE",
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "AM_WAIT5_FILLABILITY_REASSESSMENT_INTEGRITY_FAILED",
        "PRIMARY_FINDING": note,
        "MODEL_REFIT_N": MODEL_REFIT_N,
        "PM_ROWS_USED_N": None,
        "FUTURE_EVENT_USE_N": None,
        "TARGET_CONTAMINATION_N": None,
        "HELDOUT_FIT_LEAK_N": None,
    }
    if extra:
        body.update(extra)
    return write_report(
        body,
        decision={
            "CASE": "E",
            "PRIMARY_MECHANISM": "INTEGRITY_FAIL",
            "AM_ARCHITECTURE": "NONE",
            "NEXT_RESEARCH": "NONE",
            "VERDICT": "AM_WAIT5_FILLABILITY_REASSESSMENT_INTEGRITY_FAILED",
            "PRIMARY_FINDING": note,
            "note": "CASE E. STOP. Integrity failed.",
        },
        extra=extra,
    )


def _ttf_map() -> tuple[dict[str, Any], int]:
    harvest = []
    miss_days = 0
    for day in ELIGIBLE_DAYS:
        fp = WAIT_CACHE / f"{day}_WAIT.json"
        saved = _load(fp)
        if not (saved.get("ok") and saved.get("date") == day and saved.get("rows")):
            miss_days += 1
            continue
        harvest.extend(list(saved.get("rows") or []))
    by = wf_index(harvest)
    out: dict[str, Any] = {}
    for k, h in by.items():
        w = wait_body(h, DEV_WAIT_ID)
        out[k] = w.get("TIME_TO_FILL_SEC")
    return out, miss_days


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM WAIT5 FILLABILITY-FIRST REASSESSMENT V1", flush=True)
    print("Frozen OOF. CONTROL vs FILL_ONLY. No Stage2. No refit. No Exact. No PnL.", flush=True)

    if abs(float(WAIT_SEC) - 1.0) > 1e-12:
        print("STOP WAIT_SEC drift", flush=True)
        return 2
    if abs(float(DEV_WAIT_SEC) - 5.0) > 1e-12:
        print("STOP DEV_WAIT_SEC drift", flush=True)
        return 2
    if OLD_TWO_STAGE_ALLOWED or MINIMAL_SWAP_STAGE2_ALLOWED or STAGE2_ARM_ALLOWED:
        print("STOP Stage2 arm must remain closed", flush=True)
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
    if str(prior.get("ANALYSIS_ID") or "") != PRIOR_DEVELOPMENT_ID or str(preg.get("VERDICT") or "") != PRIOR_DEVELOPMENT_VERDICT:
        return _integrity(
            f"STOP. Prior {PRIOR_DEVELOPMENT_ID} / {PRIOR_DEVELOPMENT_VERDICT} required.",
            extra={"prior_id": prior.get("ANALYSIS_ID"), "prior_verdict": preg.get("VERDICT")},
        )
    grid = representation_grid()
    if len(grid) != 9:
        return _integrity("STOP. Representation grid is not 9.")

    ttf_map, miss_days = _ttf_map()
    if miss_days != 0 or not ttf_map:
        return _integrity("STOP. WAIT harvest cache missing for TIME_TO_FILL join.", extra={"WAIT_MISS_DAYS": miss_days})

    leak = {
        "MODEL_REFIT_N": int(MODEL_REFIT_N),
        "PM_ROWS_USED_N": 0,
        "FUTURE_EVENT_USE_N": 0,
        "TARGET_CONTAMINATION_N": 0,
        "HELDOUT_FIT_LEAK_N": 0,
        "JOIN_MISS_N": 0,
        "TTF_FILLED_MISS_N": 0,
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
        leak["JOIN_MISS_N"] += attach_ttf(rows, ttf_map)
        body = evaluate_scored(rows, list(ELIGIBLE_DAYS))
        body["ok"] = True
        body["representation_id"] = rid
        body["feature_set"] = spec.get("feature_set")
        body["normalization"] = spec.get("normalization")
        leak["TTF_FILLED_MISS_N"] += int(body.get("TTF_FILLED_MISS_N") or 0)
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
        de = body.get("decomp") or {}
        print(
            f"eval {rid} ctrl={ctrl} fo={fo} net_fill={de.get('NET_ADDITIONAL_FILL_N')} "
            f"changed={de.get('SELECTION_CHANGED_COHORT_RATE')} "
            f"recon_u={de.get('RECONSTRUCTION_ERROR_U')} recon_d={de.get('RECONSTRUCTION_ERROR_D')}",
            flush=True,
        )
    if len(got) != 9:
        return _integrity("STOP. Did not evaluate 9 representations.")

    specs = spec_rows(got)
    fill_deltas = [float(r["DELTA_FILL"]) for r in specs if r.get("DELTA_FILL") is not None]
    pos_rep = sum(1 for v in fill_deltas if v > 0)
    arm_obs = {
        "CONTROL_FILL_RATE": _med_key(specs, "CONTROL_FILL_RATE"),
        "FILL_ONLY_FILL_RATE": _med_key(specs, "FILL_ONLY_FILL_RATE"),
        "AM_FILLABILITY_TOP3_DELTA": _med_key(specs, "DELTA_FILL"),
        "AM_FILLABILITY_POSITIVE_REP_N": pos_rep,
        "CONTROL_EXEC_U": _med_key(specs, "CONTROL_EXEC_U"),
        "CONTROL_EXEC_D": _med_key(specs, "CONTROL_EXEC_D"),
        "FILL_ONLY_EXEC_U": _med_key(specs, "FILL_ONLY_EXEC_U"),
        "FILL_ONLY_EXEC_D": _med_key(specs, "FILL_ONLY_EXEC_D"),
    }
    par = freeze_parity(arm_obs)
    print("parity", par.get("ok"), par.get("checks"), flush=True)
    if not par.get("ok"):
        return _integrity("STOP. Frozen CONTROL / FILL_ONLY headline parity did not reproduce.", extra={"parity": par})

    recon = recon_violations(specs)
    print("integrity", leak, "recon", recon, flush=True)
    if leak.get("JOIN_MISS_N"):
        return _integrity("STOP. TIME_TO_FILL join miss != 0.", extra={"integrity": leak})
    if any(
        int(leak.get(k) or 0) != 0
        for k in (
            "MODEL_REFIT_N",
            "PM_ROWS_USED_N",
            "FUTURE_EVENT_USE_N",
            "TARGET_CONTAMINATION_N",
            "HELDOUT_FIT_LEAK_N",
        )
    ):
        return _integrity("STOP. Isolation integrity failed.", extra={"integrity": leak})
    if not recon.get("PASS"):
        return _integrity("STOP. Reconstruction-error violation.", extra={"integrity": leak, "recon": recon})

    fill_g = _fill_edge(specs, got)
    qual = quality_pack(specs, got)
    decision = decide(
        parity_ok=True,
        fill_edge=fill_g,
        quality=qual,
        recon=recon,
        leak={
            "MODEL_REFIT_N": leak.get("MODEL_REFIT_N"),
            "PM_ROWS_USED_N": leak.get("PM_ROWS_USED_N"),
            "FUTURE_EVENT_USE_N": leak.get("FUTURE_EVENT_USE_N"),
            "TARGET_CONTAMINATION_N": leak.get("TARGET_CONTAMINATION_N"),
            "HELDOUT_FIT_LEAK_N": leak.get("HELDOUT_FIT_LEAK_N"),
            "RECONSTRUCTION_ERROR_VIOLATION_N": recon.get("RECONSTRUCTION_ERROR_VIOLATION_N"),
        },
    )
    required = {
        "BASE_PARITY": True,
        "CONTROL_FILL_RATE": arm_obs.get("CONTROL_FILL_RATE"),
        "FILL_ONLY_FILL_RATE": arm_obs.get("FILL_ONLY_FILL_RATE"),
        "NET_ADDITIONAL_FILL_N": _med_key(specs, "NET_ADDITIONAL_FILL_N"),
        "SELECTION_CHANGED_COHORT_RATE": _med_key(specs, "SELECTION_CHANGED_COHORT_RATE"),
        "CONTROL_ONLY_FILL_RATE": _med_key(specs, "CONTROL_ONLY_FILL_RATE"),
        "FILL_ONLY_ONLY_FILL_RATE": _med_key(specs, "FILL_ONLY_ONLY_FILL_RATE"),
        "CONTROL_COND_U": _med_key(specs, "CONTROL_COND_U"),
        "FILL_ONLY_COND_U": _med_key(specs, "FILL_ONLY_COND_U"),
        "DELTA_COND_U": _med_key(specs, "DELTA_COND_U"),
        "CONTROL_COND_D": _med_key(specs, "CONTROL_COND_D"),
        "FILL_ONLY_COND_D": _med_key(specs, "FILL_ONLY_COND_D"),
        "DELTA_COND_D": _med_key(specs, "DELTA_COND_D"),
        "DELTA_SWAP_COND_U": _med_key(specs, "DELTA_SWAP_COND_U"),
        "DELTA_SWAP_COND_D": _med_key(specs, "DELTA_SWAP_COND_D"),
        "EXEC_U_GAIN_FROM_EXTRA_FILL": _med_key(specs, "EXEC_U_GAIN_FROM_EXTRA_FILL"),
        "EXEC_U_GAIN_FROM_SELECTION_QUALITY": _med_key(specs, "EXEC_U_GAIN_FROM_SELECTION_QUALITY"),
        "EXEC_D_CHANGE_FROM_EXTRA_FILL": _med_key(specs, "EXEC_D_CHANGE_FROM_EXTRA_FILL"),
        "EXEC_D_CHANGE_FROM_SELECTION_QUALITY": _med_key(specs, "EXEC_D_CHANGE_FROM_SELECTION_QUALITY"),
        "RECONSTRUCTION_ERROR_U": _med_key(specs, "RECONSTRUCTION_ERROR_U"),
        "RECONSTRUCTION_ERROR_D": _med_key(specs, "RECONSTRUCTION_ERROR_D"),
        "FILL_POS_REP_N": fill_g.get("POSITIVE_REP_N"),
        "COND_U_NONNEG_REP_N": qual.get("COND_U_NONNEG_REP_N"),
        "COND_D_NONNEG_REP_N": qual.get("COND_D_NONNEG_REP_N"),
        "PRIMARY_MECHANISM": decision.get("PRIMARY_MECHANISM"),
        "AM_ARCHITECTURE": decision.get("AM_ARCHITECTURE"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "COMMON_SELECTED_N": _med_key(specs, "COMMON_SELECTED_N"),
        "CONTROL_ONLY_N": _med_key(specs, "CONTROL_ONLY_N"),
        "FILL_ONLY_ONLY_N": _med_key(specs, "FILL_ONLY_ONLY_N"),
        "SELECTION_CHANGED_COHORT_N": _med_key(specs, "SELECTION_CHANGED_COHORT_N"),
        "COMMON_FILL_N": _med_key(specs, "COMMON_FILL_N"),
        "CONTROL_ONLY_FILL_N": _med_key(specs, "CONTROL_ONLY_FILL_N"),
        "FILL_ONLY_ONLY_FILL_N": _med_key(specs, "FILL_ONLY_ONLY_FILL_N"),
        "AM_FILLABILITY_TOP3_DELTA": arm_obs.get("AM_FILLABILITY_TOP3_DELTA"),
        "CONTROL_EXEC_U": arm_obs.get("CONTROL_EXEC_U"),
        "CONTROL_EXEC_D": arm_obs.get("CONTROL_EXEC_D"),
        "FILL_ONLY_EXEC_U": arm_obs.get("FILL_ONLY_EXEC_U"),
        "FILL_ONLY_EXEC_D": arm_obs.get("FILL_ONLY_EXEC_D"),
        "TTF_CONTROL_MEDIAN": _med_key(specs, "TTF_CONTROL_MEDIAN"),
        "TTF_CONTROL_P75": _med_key(specs, "TTF_CONTROL_P75"),
        "TTF_CONTROL_P90": _med_key(specs, "TTF_CONTROL_P90"),
        "TTF_FILL_ONLY_MEDIAN": _med_key(specs, "TTF_FILL_ONLY_MEDIAN"),
        "TTF_FILL_ONLY_P75": _med_key(specs, "TTF_FILL_ONLY_P75"),
        "TTF_FILL_ONLY_P90": _med_key(specs, "TTF_FILL_ONLY_P90"),
        "TTF_FILL_ONLY_ONLY_MEDIAN": _med_key(specs, "TTF_FILL_ONLY_ONLY_MEDIAN"),
        "TTF_FILL_ONLY_ONLY_P75": _med_key(specs, "TTF_FILL_ONLY_ONLY_P75"),
        "TTF_FILL_ONLY_ONLY_P90": _med_key(specs, "TTF_FILL_ONLY_ONLY_P90"),
        "MODEL_REFIT_N": leak.get("MODEL_REFIT_N"),
        "PM_ROWS_USED_N": leak.get("PM_ROWS_USED_N"),
        "FUTURE_EVENT_USE_N": leak.get("FUTURE_EVENT_USE_N"),
        "TARGET_CONTAMINATION_N": leak.get("TARGET_CONTAMINATION_N"),
        "HELDOUT_FIT_LEAK_N": leak.get("HELDOUT_FIT_LEAK_N"),
        "RECONSTRUCTION_ERROR_VIOLATION_N": recon.get("RECONSTRUCTION_ERROR_VIOLATION_N"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
    }
    print(
        f"CASE={decision.get('CASE')} fill={fill_g.get('PASS')} "
        f"u_nonworse={(qual.get('U_NONWORSE') or {}).get('PASS')} "
        f"d_nonworse={(qual.get('D_NONWORSE') or {}).get('PASS')} "
        f"u_worse={(qual.get('U_WORSE') or {}).get('PASS')} "
        f"d_worse={(qual.get('D_WORSE') or {}).get('PASS')} "
        f"VERDICT={decision.get('VERDICT')}",
        flush=True,
    )
    fill_days = fill_g.get("daily") or []
    u_days = qual.get("daily_cond_u") or []
    d_days = qual.get("daily_cond_d") or []
    su_days = qual.get("daily_swap_u") or []
    sd_days = qual.get("daily_swap_d") or []
    return write_report(
        required,
        decision=decision,
        extra={
            "parity": par,
            "fill_edge": {k: v for k, v in fill_g.items() if k != "daily"},
            "conditional": {
                "U_NONWORSE": _drop_gates(qual.get("U_NONWORSE") or {}),
                "D_NONWORSE": _drop_gates(qual.get("D_NONWORSE") or {}),
                "U_WORSE": _drop_gates(qual.get("U_WORSE") or {}),
                "D_WORSE": _drop_gates(qual.get("D_WORSE") or {}),
                "U_NONWORSE_GATES": (qual.get("U_NONWORSE") or {}).get("gates"),
                "D_NONWORSE_GATES": (qual.get("D_NONWORSE") or {}).get("gates"),
                "U_WORSE_GATES": (qual.get("U_WORSE") or {}).get("gates"),
                "D_WORSE_GATES": (qual.get("D_WORSE") or {}).get("gates"),
            },
            "swap_filled": {
                "SWAP_U_NONWORSE": _drop_gates(qual.get("SWAP_U_NONWORSE") or {}),
                "SWAP_D_NONWORSE": _drop_gates(qual.get("SWAP_D_NONWORSE") or {}),
                "SWAP_U_WORSE": _drop_gates(qual.get("SWAP_U_WORSE") or {}),
                "SWAP_D_WORSE": _drop_gates(qual.get("SWAP_D_WORSE") or {}),
                "SWAP_U_STATS": qual.get("swap_u_stats"),
                "SWAP_D_STATS": qual.get("swap_d_stats"),
            },
            "recon": recon,
            "integrity": leak,
        },
        sheets_extra={
            "Parity": kv_rows({**par.get("checks"), **{f"OBS_{k}": v for k, v in (par.get("observed") or {}).items()}}),
            "Selection": [
                {
                    "representation_id": r.get("representation_id"),
                    "COMMON_SELECTED_N": r.get("COMMON_SELECTED_N"),
                    "CONTROL_ONLY_N": r.get("CONTROL_ONLY_N"),
                    "FILL_ONLY_ONLY_N": r.get("FILL_ONLY_ONLY_N"),
                    "SELECTION_CHANGED_COHORT_N": r.get("SELECTION_CHANGED_COHORT_N"),
                    "SELECTION_CHANGED_COHORT_RATE": r.get("SELECTION_CHANGED_COHORT_RATE"),
                }
                for r in specs
            ],
            "FillDecomp": [
                {
                    "representation_id": r.get("representation_id"),
                    "COMMON_FILL_N": r.get("COMMON_FILL_N"),
                    "CONTROL_ONLY_FILL_N": r.get("CONTROL_ONLY_FILL_N"),
                    "FILL_ONLY_ONLY_FILL_N": r.get("FILL_ONLY_ONLY_FILL_N"),
                    "CONTROL_ONLY_FILL_RATE": r.get("CONTROL_ONLY_FILL_RATE"),
                    "FILL_ONLY_ONLY_FILL_RATE": r.get("FILL_ONLY_ONLY_FILL_RATE"),
                    "NET_ADDITIONAL_FILL_N": r.get("NET_ADDITIONAL_FILL_N"),
                    "DELTA_FILL": r.get("DELTA_FILL"),
                }
                for r in specs
            ],
            "Conditional": [
                {
                    "representation_id": r.get("representation_id"),
                    "CONTROL_COND_U": r.get("CONTROL_COND_U"),
                    "FILL_ONLY_COND_U": r.get("FILL_ONLY_COND_U"),
                    "DELTA_COND_U": r.get("DELTA_COND_U"),
                    "CONTROL_COND_D": r.get("CONTROL_COND_D"),
                    "FILL_ONLY_COND_D": r.get("FILL_ONLY_COND_D"),
                    "DELTA_COND_D": r.get("DELTA_COND_D"),
                }
                for r in specs
            ],
            "SwapFilled": [
                {
                    "representation_id": r.get("representation_id"),
                    "CONTROL_ONLY_COND_U": r.get("CONTROL_ONLY_COND_U"),
                    "FILL_ONLY_ONLY_COND_U": r.get("FILL_ONLY_ONLY_COND_U"),
                    "DELTA_SWAP_COND_U": r.get("DELTA_SWAP_COND_U"),
                    "CONTROL_ONLY_COND_D": r.get("CONTROL_ONLY_COND_D"),
                    "FILL_ONLY_ONLY_COND_D": r.get("FILL_ONLY_ONLY_COND_D"),
                    "DELTA_SWAP_COND_D": r.get("DELTA_SWAP_COND_D"),
                }
                for r in specs
            ],
            "Exposure": [
                {
                    "representation_id": r.get("representation_id"),
                    "DELTA_EXEC_U": r.get("DELTA_EXEC_U"),
                    "DELTA_EXEC_D": r.get("DELTA_EXEC_D"),
                    "EXEC_U_GAIN_FROM_EXTRA_FILL": r.get("EXEC_U_GAIN_FROM_EXTRA_FILL"),
                    "EXEC_U_GAIN_FROM_SELECTION_QUALITY": r.get("EXEC_U_GAIN_FROM_SELECTION_QUALITY"),
                    "EXEC_D_CHANGE_FROM_EXTRA_FILL": r.get("EXEC_D_CHANGE_FROM_EXTRA_FILL"),
                    "EXEC_D_CHANGE_FROM_SELECTION_QUALITY": r.get("EXEC_D_CHANGE_FROM_SELECTION_QUALITY"),
                    "RECONSTRUCTION_ERROR_U": r.get("RECONSTRUCTION_ERROR_U"),
                    "RECONSTRUCTION_ERROR_D": r.get("RECONSTRUCTION_ERROR_D"),
                }
                for r in specs
            ],
            "FillTime": [
                {
                    "representation_id": r.get("representation_id"),
                    "TTF_CONTROL_MEDIAN": r.get("TTF_CONTROL_MEDIAN"),
                    "TTF_CONTROL_P75": r.get("TTF_CONTROL_P75"),
                    "TTF_CONTROL_P90": r.get("TTF_CONTROL_P90"),
                    "TTF_FILL_ONLY_MEDIAN": r.get("TTF_FILL_ONLY_MEDIAN"),
                    "TTF_FILL_ONLY_P75": r.get("TTF_FILL_ONLY_P75"),
                    "TTF_FILL_ONLY_P90": r.get("TTF_FILL_ONLY_P90"),
                    "TTF_FILL_ONLY_ONLY_MEDIAN": r.get("TTF_FILL_ONLY_ONLY_MEDIAN"),
                    "TTF_FILL_ONLY_ONLY_P75": r.get("TTF_FILL_ONLY_ONLY_P75"),
                    "TTF_FILL_ONLY_ONLY_P90": r.get("TTF_FILL_ONLY_ONLY_P90"),
                }
                for r in specs
            ],
            "DayRobustness": (
                [{"scope": "DELTA_FILL", **r} for r in fill_days]
                + [{"scope": "DELTA_COND_U", **r} for r in u_days]
                + [{"scope": "DELTA_COND_D", **r} for r in d_days]
                + [{"scope": "DELTA_SWAP_COND_U", **r} for r in su_days]
                + [{"scope": "DELTA_SWAP_COND_D", **r} for r in sd_days]
            ),
            "Representations": specs,
            "Gates": kv_rows(
                {
                    **{f"FILL_{k}": v for k, v in (fill_g.get("gates") or {}).items()},
                    **{f"U_NONWORSE_{k}": v for k, v in ((qual.get("U_NONWORSE") or {}).get("gates") or {}).items()},
                    **{f"D_NONWORSE_{k}": v for k, v in ((qual.get("D_NONWORSE") or {}).get("gates") or {}).items()},
                    **{f"U_WORSE_{k}": v for k, v in ((qual.get("U_WORSE") or {}).get("gates") or {}).items()},
                    **{f"D_WORSE_{k}": v for k, v in ((qual.get("D_WORSE") or {}).get("gates") or {}).items()},
                    "FILL_EDGE_PASS": fill_g.get("PASS"),
                    "U_NONWORSE_PASS": (qual.get("U_NONWORSE") or {}).get("PASS"),
                    "D_NONWORSE_PASS": (qual.get("D_NONWORSE") or {}).get("PASS"),
                    "U_WORSE_PASS": (qual.get("U_WORSE") or {}).get("PASS"),
                    "D_WORSE_PASS": (qual.get("D_WORSE") or {}).get("PASS"),
                    "RECON_PASS": recon.get("PASS"),
                }
            ),
            "Integrity": kv_rows(
                {
                    **leak,
                    **recon,
                    "SESSION": SESSION,
                    "RUNTIME_WAIT_SEC": WAIT_SEC,
                    "DEV_WAIT_SEC": DEV_WAIT_SEC,
                    "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
                    "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
                    "OLD_TWO_STAGE_ALLOWED": OLD_TWO_STAGE_ALLOWED,
                    "MINIMAL_SWAP_STAGE2_ALLOWED": MINIMAL_SWAP_STAGE2_ALLOWED,
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
