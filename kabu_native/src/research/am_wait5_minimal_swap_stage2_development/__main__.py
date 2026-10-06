"""Offline AM WAIT5 minimal-swap Stage2 development. Frozen OOF only. No Runtime write."""
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

from research.am_wait5_minimal_swap_stage2_development import (
    ANALYSIS_ID,
    BEST_REPRESENTATION_ADOPTED,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    DEV_WAIT_SEC,
    ELIGIBLE_DAYS,
    ENTRY_RETRAIN_STARTED,
    EXACT_RAN,
    FEATURE_SEARCH,
    FILL_LOSS_TOLERANCE,
    FINAL_MODEL_ADOPTED,
    FROZEN_OOF_REPLAY_ONLY,
    HYPERPARAMETER_TUNING,
    LOCKED_FILL_SLOTS,
    LOCK_SLOT_SEARCH,
    MAX_MEMBERSHIP_SWAP_PER_COHORT,
    MODEL_REFIT_N,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    OLD_TWO_STAGE_ALLOWED,
    PAPER_OPERATED,
    PARITY_ABS_TOL,
    PNL_USED,
    PRIOR_PRECOMMIT_ID,
    PRIOR_PRECOMMIT_VERDICT,
    QUALITY_COMBINATION,
    QUALITY_SLOT_POOL,
    REPRESENTATION_N,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    SESSION,
    STRATEGY_CREATED,
    THRESHOLD_SEARCH,
    TOPK_SEARCH,
    TRUE_OOS,
    WAIT_POLICY_ADOPTED,
    WEIGHT_SEARCH,
)
from research.am_wait5_minimal_swap_stage2_development.analyze import (
    cond_sanity,
    decide,
    fill_edge_gate,
    freeze_parity,
    quality_gate,
    spec_rows,
)
from research.am_wait5_minimal_swap_stage2_development.eval_oof import evaluate_scored
from research.am_wait5_minimal_swap_stage2_development.publish import (
    OUT,
    build_markdown,
    json_sanitize,
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
PRECOMMIT = NATIVE / "results" / "research" / "am_wait5_stage2_fill_edge_precommit" / "report.json"
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
                "LOCKED_FILL_SLOTS": LOCKED_FILL_SLOTS,
                "QUALITY_SLOT_POOL": list(QUALITY_SLOT_POOL),
                "MAX_MEMBERSHIP_SWAP_PER_COHORT": MAX_MEMBERSHIP_SWAP_PER_COHORT,
                "QUALITY_COMBINATION": QUALITY_COMBINATION,
                "REPRESENTATION_N": REPRESENTATION_N,
                "OLD_TWO_STAGE_ALLOWED": OLD_TWO_STAGE_ALLOWED,
                "FROZEN_OOF_REPLAY_ONLY": FROZEN_OOF_REPLAY_ONLY,
                "MODEL_REFIT_N": MODEL_REFIT_N,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "PNL_USED": PNL_USED,
                "EXACT_RAN": EXACT_RAN,
            }
        ),
        "Parity": [{"empty": True}],
        "Arms": [{"empty": True}],
        "Distinctness": [{"empty": True}],
        "SwapOnly": [{"empty": True}],
        "Fillability": [{"empty": True}],
        "ExecQuality": [{"empty": True}],
        "Conditional": [{"empty": True}],
        "ConsensusDays": [{"empty": True}],
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
                "ENTRY_RETRAIN_STARTED": ENTRY_RETRAIN_STARTED,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "FEATURE_SEARCH": FEATURE_SEARCH,
                "PNL_USED": PNL_USED,
                "BEST_REPRESENTATION_ADOPTED": BEST_REPRESENTATION_ADOPTED,
                "HYPERPARAMETER_TUNING": HYPERPARAMETER_TUNING,
                "TOPK_SEARCH": TOPK_SEARCH,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
                "WEIGHT_SEARCH": WEIGHT_SEARCH,
                "LOCK_SLOT_SEARCH": LOCK_SLOT_SEARCH,
                "OLD_TWO_STAGE_ALLOWED": OLD_TWO_STAGE_ALLOWED,
                "FILL_LOSS_TOLERANCE": FILL_LOSS_TOLERANCE,
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
        "STOP. No Exact. No PnL. No refit. No Stage2 micro-search. Runtime WAIT_SEC=1.0. "
        "W5 not adopted. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "AM_WAIT5_MINIMAL_SWAP_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str, extra: dict | None = None) -> int:
    body = {
        "BASE_PARITY": False,
        "REPRESENTATION_N": 9,
        "CONTROL_FILL_RATE": None,
        "FILL_ONLY_FILL_RATE": None,
        "MINIMAL_SWAP_FILL_RATE": None,
        "DELTA_FILL_VS_FILL_ONLY": None,
        "FILL_NONNEG_REP_N": None,
        "FILL_POS_DAYS": None,
        "FILL_ZERO_DAYS": None,
        "FILL_NEG_DAYS": None,
        "FILL_ONLY_EXEC_U": None,
        "FILL_ONLY_EXEC_D": None,
        "MINIMAL_SWAP_EXEC_U": None,
        "MINIMAL_SWAP_EXEC_D": None,
        "DELTA_EXEC_U_VS_FILL_ONLY": None,
        "DELTA_EXEC_D_VS_FILL_ONLY": None,
        "U_POSITIVE_REP_N": None,
        "D_POSITIVE_REP_N": None,
        "U_POS_DAYS": None,
        "U_NEG_DAYS": None,
        "D_POS_DAYS": None,
        "D_NEG_DAYS": None,
        "U_EX_BEST_DAY": None,
        "U_EX_TOP3_DAYS": None,
        "D_EX_BEST_DAY": None,
        "D_EX_TOP3_DAYS": None,
        "MINIMAL_SWAP_NE_FILL_ONLY_COHORT_RATE": None,
        "SWAP_OUT_FILL_RATE": None,
        "SWAP_IN_FILL_RATE": None,
        "DELTA_SWAP_COND_U": None,
        "DELTA_SWAP_COND_D": None,
        "FILL_EDGE_PASS": None,
        "QUALITY_PASS": None,
        "MINIMAL_SWAP_STAGE2_PASS": False,
        "MODEL_REFIT_N": MODEL_REFIT_N,
        "PM_ROWS_USED_N": None,
        "FUTURE_EVENT_USE_N": None,
        "TARGET_CONTAMINATION_N": None,
        "HELDOUT_FIT_LEAK_N": None,
        "MAX_SWAP_VIOLATION_N": None,
        "PRIMARY_FINDING": note,
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "AM_WAIT5_MINIMAL_SWAP_INTEGRITY_FAILED",
    }
    if extra:
        body.update(extra)
    return write_report(
        body,
        decision={
            "CASE": "F",
            "MINIMAL_SWAP_STAGE2_PASS": False,
            "VERDICT": "AM_WAIT5_MINIMAL_SWAP_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": note,
            "note": "CASE F. STOP. Integrity failed.",
        },
        extra=extra,
    )


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM WAIT5 MINIMAL-SWAP STAGE2 DEVELOPMENT V1", flush=True)
    print("Frozen OOF only. CONTROL / FILL_ONLY / MINIMAL_SWAP. No refit. No Exact. No PnL.", flush=True)

    if abs(float(WAIT_SEC) - 1.0) > 1e-12:
        print("STOP WAIT_SEC drift", flush=True)
        return 2
    if abs(float(DEV_WAIT_SEC) - 5.0) > 1e-12:
        print("STOP DEV_WAIT_SEC drift", flush=True)
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
    pre = _load(PRECOMMIT)
    preg = pre.get("required") or {}
    if str(pre.get("ANALYSIS_ID") or "") != PRIOR_PRECOMMIT_ID or str(preg.get("VERDICT") or "") != PRIOR_PRECOMMIT_VERDICT:
        return _integrity(
            f"STOP. Prior {PRIOR_PRECOMMIT_ID} / {PRIOR_PRECOMMIT_VERDICT} required.",
            extra={"prior_id": pre.get("ANALYSIS_ID"), "prior_verdict": preg.get("VERDICT")},
        )
    if int(preg.get("LOCKED_FILL_SLOTS") or -1) != int(LOCKED_FILL_SLOTS):
        return _integrity("STOP. LOCKED_FILL_SLOTS mismatch.")
    if int(preg.get("MAX_MEMBERSHIP_SWAP_PER_COHORT") or -1) != int(MAX_MEMBERSHIP_SWAP_PER_COHORT):
        return _integrity("STOP. MAX_MEMBERSHIP_SWAP_PER_COHORT mismatch.")
    if str(preg.get("QUALITY_COMBINATION") or "") != QUALITY_COMBINATION:
        return _integrity("STOP. QUALITY_COMBINATION mismatch.")
    if preg.get("OLD_TWO_STAGE_INTERFACE_ALLOWED") is not False:
        return _integrity("STOP. Old TWO_STAGE interface must remain closed.")
    grid = representation_grid()
    if len(grid) != 9:
        return _integrity("STOP. Representation grid is not 9.")

    got = []
    leak = {
        "PM_ROWS_USED_N": 0,
        "FUTURE_EVENT_USE_N": 0,
        "TARGET_CONTAMINATION_N": 0,
        "HELDOUT_FIT_LEAK_N": 0,
        "STAGE2_NONFILL_TARGET_TRAIN_N": 0,
        "MAX_SWAP_VIOLATION_N": 0,
        "MODEL_REFIT_N": int(MODEL_REFIT_N),
    }
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
        leak["MAX_SWAP_VIOLATION_N"] += int((body.get("distinctness") or {}).get("MAX_SWAP_VIOLATION_N") or 0)
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
        print(
            f"eval {rid} fo={fo} ms={(body.get('MINIMAL_SWAP') or {}).get('SELECTED_FILL_RATE')} "
            f"ne={(body.get('distinctness') or {}).get('MINIMAL_SWAP_NE_FILL_ONLY_COHORT_RATE')} "
            f"viol={(body.get('distinctness') or {}).get('MAX_SWAP_VIOLATION_N')}",
            flush=True,
        )
    if len(got) != 9:
        return _integrity("STOP. Did not evaluate 9 representations.")

    specs = spec_rows(got)
    arm_obs = {
        "FILL_ONLY_FILL_RATE": _med_key(specs, "FILL_ONLY_FILL_RATE"),
        "FILL_ONLY_EXEC_U": _med_key(specs, "FILL_ONLY_EXEC_U"),
        "FILL_ONLY_EXEC_D": _med_key(specs, "FILL_ONLY_EXEC_D"),
        "CONTROL_FILL_RATE": _med_key(specs, "CONTROL_FILL_RATE"),
    }
    par = freeze_parity(arm_obs)
    print("parity", par.get("ok"), par.get("checks"), flush=True)
    if not par.get("ok"):
        return _integrity("STOP. Frozen CONTROL / FILL_ONLY headline parity did not reproduce.", extra={"parity": par})
    print("integrity", leak, flush=True)
    if any(int(leak.get(k) or 0) != 0 for k in leak):
        return _integrity("STOP. Isolation or max-swap integrity failed.", extra={"integrity": leak})

    fill_g = fill_edge_gate(specs, got)
    qual_g = quality_gate(specs, got)
    cond = cond_sanity(specs)
    ne_n = _med_key(specs, "MINIMAL_SWAP_NE_FILL_ONLY_COHORT_N")
    decision = decide(
        fill_gate=fill_g,
        qual_gate=qual_g,
        leak=leak,
        ne_cohort_n=int(ne_n or 0),
    )
    required = {
        "BASE_PARITY": True,
        "REPRESENTATION_N": 9,
        "CONTROL_FILL_RATE": arm_obs.get("CONTROL_FILL_RATE"),
        "FILL_ONLY_FILL_RATE": arm_obs.get("FILL_ONLY_FILL_RATE"),
        "MINIMAL_SWAP_FILL_RATE": _med_key(specs, "MINIMAL_SWAP_FILL_RATE"),
        "DELTA_FILL_VS_FILL_ONLY": _med_key(specs, "DELTA_FILL_VS_FILL_ONLY"),
        "FILL_NONNEG_REP_N": fill_g.get("FILL_NONNEG_REP_N"),
        "FILL_POS_DAYS": fill_g.get("FILL_POS_DAYS"),
        "FILL_ZERO_DAYS": fill_g.get("FILL_ZERO_DAYS"),
        "FILL_NEG_DAYS": fill_g.get("FILL_NEG_DAYS"),
        "FILL_ONLY_EXEC_U": arm_obs.get("FILL_ONLY_EXEC_U"),
        "FILL_ONLY_EXEC_D": arm_obs.get("FILL_ONLY_EXEC_D"),
        "MINIMAL_SWAP_EXEC_U": _med_key(specs, "MINIMAL_SWAP_EXEC_U"),
        "MINIMAL_SWAP_EXEC_D": _med_key(specs, "MINIMAL_SWAP_EXEC_D"),
        "DELTA_EXEC_U_VS_FILL_ONLY": _med_key(specs, "DELTA_EXEC_U_VS_FILL_ONLY"),
        "DELTA_EXEC_D_VS_FILL_ONLY": _med_key(specs, "DELTA_EXEC_D_VS_FILL_ONLY"),
        "U_POSITIVE_REP_N": qual_g.get("U_POSITIVE_REP_N"),
        "D_POSITIVE_REP_N": qual_g.get("D_POSITIVE_REP_N"),
        "U_POS_DAYS": qual_g.get("U_POS_DAYS"),
        "U_NEG_DAYS": qual_g.get("U_NEG_DAYS"),
        "D_POS_DAYS": qual_g.get("D_POS_DAYS"),
        "D_NEG_DAYS": qual_g.get("D_NEG_DAYS"),
        "U_EX_BEST_DAY": qual_g.get("U_EX_BEST_DAY"),
        "U_EX_TOP3_DAYS": qual_g.get("U_EX_TOP3_DAYS"),
        "D_EX_BEST_DAY": qual_g.get("D_EX_BEST_DAY"),
        "D_EX_TOP3_DAYS": qual_g.get("D_EX_TOP3_DAYS"),
        "MINIMAL_SWAP_NE_FILL_ONLY_COHORT_RATE": _med_key(specs, "MINIMAL_SWAP_NE_FILL_ONLY_COHORT_RATE"),
        "SWAP_OUT_FILL_RATE": _med_key(specs, "SWAP_OUT_FILL_RATE"),
        "SWAP_IN_FILL_RATE": _med_key(specs, "SWAP_IN_FILL_RATE"),
        "DELTA_SWAP_COND_U": _med_key(specs, "DELTA_SWAP_COND_U"),
        "DELTA_SWAP_COND_D": _med_key(specs, "DELTA_SWAP_COND_D"),
        "FILL_EDGE_PASS": fill_g.get("PASS"),
        "QUALITY_PASS": qual_g.get("PASS"),
        "MINIMAL_SWAP_STAGE2_PASS": decision.get("MINIMAL_SWAP_STAGE2_PASS"),
        "MODEL_REFIT_N": leak.get("MODEL_REFIT_N"),
        "PM_ROWS_USED_N": leak.get("PM_ROWS_USED_N"),
        "FUTURE_EVENT_USE_N": leak.get("FUTURE_EVENT_USE_N"),
        "TARGET_CONTAMINATION_N": leak.get("TARGET_CONTAMINATION_N"),
        "HELDOUT_FIT_LEAK_N": leak.get("HELDOUT_FIT_LEAK_N"),
        "MAX_SWAP_VIOLATION_N": leak.get("MAX_SWAP_VIOLATION_N"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "COND_SANITY_PASS": cond.get("PASS"),
        "STAGE2_NONFILL_TARGET_TRAIN_N": leak.get("STAGE2_NONFILL_TARGET_TRAIN_N"),
    }
    print(
        f"CASE={decision.get('CASE')} fill={fill_g.get('PASS')} qual={qual_g.get('PASS')} "
        f"VERDICT={decision.get('VERDICT')}",
        flush=True,
    )
    return write_report(
        required,
        decision=decision,
        extra={
            "parity": par,
            "fill_edge": {k: v for k, v in fill_g.items() if k != "daily"},
            "quality": {k: v for k, v in qual_g.items() if k not in {"daily_u", "daily_d"}},
            "conditional_sanity": cond,
            "integrity": leak,
        },
        sheets_extra={
            "Parity": kv_rows({**par.get("checks"), "LOCKED_FILL_SLOTS": LOCKED_FILL_SLOTS, "QUALITY_SLOT_POOL": list(QUALITY_SLOT_POOL), "MAX_MEMBERSHIP_SWAP_PER_COHORT": MAX_MEMBERSHIP_SWAP_PER_COHORT, "QUALITY_COMBINATION": QUALITY_COMBINATION, "BASE_PARITY": True}),
            "Arms": specs,
            "Distinctness": [
                {
                    "representation_id": r.get("representation_id"),
                    "MINIMAL_SWAP_EQ_FILL_ONLY_COHORT_N": r.get("MINIMAL_SWAP_EQ_FILL_ONLY_COHORT_N"),
                    "MINIMAL_SWAP_NE_FILL_ONLY_COHORT_N": r.get("MINIMAL_SWAP_NE_FILL_ONLY_COHORT_N"),
                    "MINIMAL_SWAP_NE_FILL_ONLY_COHORT_RATE": r.get("MINIMAL_SWAP_NE_FILL_ONLY_COHORT_RATE"),
                    "SWAPPED_IN_N": r.get("SWAPPED_IN_N"),
                    "SWAPPED_OUT_N": r.get("SWAPPED_OUT_N"),
                    "R3_RETAINED_N": r.get("R3_RETAINED_N"),
                    "R4_SELECTED_N": r.get("R4_SELECTED_N"),
                    "R5_SELECTED_N": r.get("R5_SELECTED_N"),
                    "MAX_SWAP_VIOLATION_N": r.get("MAX_SWAP_VIOLATION_N"),
                }
                for r in specs
            ],
            "SwapOnly": [
                {
                    "representation_id": r.get("representation_id"),
                    "SWAP_OUT_N": r.get("SWAP_OUT_N"),
                    "SWAP_IN_N": r.get("SWAP_IN_N"),
                    "SWAP_OUT_FILL_RATE": r.get("SWAP_OUT_FILL_RATE"),
                    "SWAP_IN_FILL_RATE": r.get("SWAP_IN_FILL_RATE"),
                    "SWAP_FILL_DELTA": r.get("SWAP_FILL_DELTA"),
                    "SWAP_OUT_COND_U": r.get("SWAP_OUT_COND_U"),
                    "SWAP_IN_COND_U": r.get("SWAP_IN_COND_U"),
                    "DELTA_SWAP_COND_U": r.get("DELTA_SWAP_COND_U"),
                    "SWAP_OUT_COND_D": r.get("SWAP_OUT_COND_D"),
                    "SWAP_IN_COND_D": r.get("SWAP_IN_COND_D"),
                    "DELTA_SWAP_COND_D": r.get("DELTA_SWAP_COND_D"),
                }
                for r in specs
            ],
            "Fillability": kv_rows({k: v for k, v in fill_g.items() if k not in {"daily", "gates"}})
            + [{"scope": "GATE", **(fill_g.get("gates") or {})}],
            "ExecQuality": kv_rows({k: v for k, v in qual_g.items() if k not in {"daily_u", "daily_d", "gates"}}),
            "Conditional": kv_rows(cond),
            "ConsensusDays": [{"scope": "FILL_VS_FILL_ONLY", **r} for r in (fill_g.get("daily") or [])]
            + [{"scope": "EXEC_U_VS_FILL_ONLY", **r} for r in (qual_g.get("daily_u") or [])]
            + [{"scope": "EXEC_D_VS_FILL_ONLY", **r} for r in (qual_g.get("daily_d") or [])],
            "Gates": kv_rows(
                {
                    **{f"FILL_{k}": v for k, v in (fill_g.get("gates") or {}).items()},
                    **{f"QUAL_{k}": v for k, v in (qual_g.get("gates") or {}).items()},
                    "COND_SANITY_PASS": cond.get("PASS"),
                    "FILL_EDGE_PASS": fill_g.get("PASS"),
                    "QUALITY_PASS": qual_g.get("PASS"),
                    "MINIMAL_SWAP_STAGE2_PASS": decision.get("MINIMAL_SWAP_STAGE2_PASS"),
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
                    "OLD_TWO_STAGE_ALLOWED": OLD_TWO_STAGE_ALLOWED,
                    "PNL_USED": PNL_USED,
                    "EXACT_RAN": EXACT_RAN,
                    "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                    "FROZEN_OOF_REPLAY_ONLY": FROZEN_OOF_REPLAY_ONLY,
                    "FINAL_MODEL_ADOPTED": FINAL_MODEL_ADOPTED,
                }
            ),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
