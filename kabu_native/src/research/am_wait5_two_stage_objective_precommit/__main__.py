"""Offline AM WAIT5 two-stage objective precommit. No Runtime write. No Paper. No Exact."""
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

from research.am_wait5_two_stage_objective_precommit import (
    ANALYSIS_ID,
    ARCHITECTURE,
    BEST_REPRESENTATION_ADOPTED,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    DEVELOPMENT_RUN_STARTED,
    DEV_WAIT_SEC,
    ELIGIBLE_DAYS,
    ENTRY_RETRAIN_STARTED,
    EXACT_RAN,
    FEATURE_SEARCH,
    JOINT_BINARY_TARGET,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    NEXT_RESEARCH,
    PAPER_OPERATED,
    PARETO_KNEE_SEARCH,
    PARETO_TARGET_ADOPTED,
    PNL_USED,
    PRIOR_AM_TARGET_STATUS_REQUIRED,
    PRIOR_ANALYSIS_ID_REQUIRED,
    PROBABILITY_CUTOFF_CREATED,
    QUALITY_COMBINATION,
    QUALITY_SCALAR_WEIGHT,
    REPRESENTATION_N,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    SESSION,
    SESSION_INDICATOR_COMMON_MODEL,
    STAGE1_STAGE2_SINGLE_SCALAR_ALLOWED,
    STAGE1_TARGET,
    STAGE1_THRESHOLD_CREATED,
    STAGE2_D_TARGET,
    STAGE2_U_TARGET,
    STRATEGY_CREATED,
    THRESHOLD_SEARCH,
    TOPK_SEARCH,
    TOPN_SHORTLIST_CREATED,
    TRUE_OOS,
    U_D_WEIGHT_SEARCH_ALLOWED,
    WAIT_POLICY_ADOPTED,
    WEIGHT_SEARCH,
)
from research.am_wait5_two_stage_objective_precommit.analyze import (
    am_population,
    decide,
    freeze_population,
    freeze_prior,
    maximin_example,
    objective_contract,
)
from research.am_wait5_two_stage_objective_precommit.publish import (
    OUT,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.canonical_entry_performance_rebase.analyze import row_key, session_of, wf_index
from research.direct_joint_objective.oof import attach_joint_labels
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.entry_execution_feasibility.analyze import labeled_rows
from research.wait5_execution_aware_rebase.analyze import attach_w5
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import WAIT_SEC

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
PRIOR = NATIVE / "results" / "research" / "wait5_session_target_learnability" / "report.json"
ROWS_PATH = NATIVE / "results" / "research" / "_work_cache" / "entry_target_architecture" / "path_rows.json"
WAIT_CACHE = NATIVE / "results" / "research" / "_work_cache" / "passive_wait_policy_reassessment"


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
                "QUALITY_COMBINATION": QUALITY_COMBINATION,
                "U_D_WEIGHT_SEARCH_ALLOWED": U_D_WEIGHT_SEARCH_ALLOWED,
                "STAGE1_STAGE2_SINGLE_SCALAR_ALLOWED": STAGE1_STAGE2_SINGLE_SCALAR_ALLOWED,
                "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
                "SESSION_INDICATOR_COMMON_MODEL": SESSION_INDICATOR_COMMON_MODEL,
                "BEST_REPRESENTATION_ADOPTED": BEST_REPRESENTATION_ADOPTED,
                "REPRESENTATION_N": REPRESENTATION_N,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "PNL_USED": PNL_USED,
                "EXACT_RAN": EXACT_RAN,
                "DEVELOPMENT_RUN_STARTED": DEVELOPMENT_RUN_STARTED,
            }
        ),
        "Parity": [{"empty": True}],
        "Objective": [{"empty": True}],
        "Ordering": [{"empty": True}],
        "DevelopmentArms": [{"empty": True}],
        "MaximinExample": [{"empty": True}],
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
                "WEIGHT_SEARCH": WEIGHT_SEARCH,
                "QUALITY_SCALAR_WEIGHT": QUALITY_SCALAR_WEIGHT,
                "JOINT_BINARY_TARGET": JOINT_BINARY_TARGET,
                "PARETO_TARGET_ADOPTED": PARETO_TARGET_ADOPTED,
                "PARETO_KNEE_SEARCH": PARETO_KNEE_SEARCH,
                "TOPK_SEARCH": TOPK_SEARCH,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
                "STAGE1_THRESHOLD_CREATED": STAGE1_THRESHOLD_CREATED,
                "TOPN_SHORTLIST_CREATED": TOPN_SHORTLIST_CREATED,
                "PROBABILITY_CUTOFF_CREATED": PROBABILITY_CUTOFF_CREATED,
                "SESSION_INDICATOR_COMMON_MODEL": SESSION_INDICATOR_COMMON_MODEL,
                "RUNTIME_CANDIDATE_CREATED": RUNTIME_CANDIDATE_CREATED,
                "STRATEGY_CREATED": STRATEGY_CREATED,
                "DEVELOPMENT_RUN_STARTED": DEVELOPMENT_RUN_STARTED,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"VERDICT={required.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(
        "STOP. No development performance run. Runtime WAIT_SEC=1.0. W5 not adopted. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "AM_WAIT5_TWO_STAGE_OBJECTIVE_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str, extra: dict | None = None) -> int:
    body = {
        "BASE_PARITY": False,
        "SESSION": SESSION,
        "DEV_WAIT_SEC": DEV_WAIT_SEC,
        "RUNTIME_WAIT_SEC": WAIT_SEC,
        "STAGE1_TARGET": STAGE1_TARGET,
        "STAGE2_U_TARGET": STAGE2_U_TARGET,
        "STAGE2_D_TARGET": STAGE2_D_TARGET,
        "QUALITY_COMBINATION": QUALITY_COMBINATION,
        "U_D_WEIGHT_SEARCH_ALLOWED": U_D_WEIGHT_SEARCH_ALLOWED,
        "STAGE1_STAGE2_SINGLE_SCALAR_ALLOWED": STAGE1_STAGE2_SINGLE_SCALAR_ALLOWED,
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "AM_WAIT5_TWO_STAGE_OBJECTIVE_INTEGRITY_FAILED",
        "PRIMARY_FINDING": note,
    }
    if extra:
        body.update(extra)
    return write_report(
        body,
        decision={
            "CASE": "FAIL",
            "VERDICT": "AM_WAIT5_TWO_STAGE_OBJECTIVE_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": note,
            "note": "STOP. Integrity failed.",
        },
    )


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM WAIT5 TWO-STAGE OBJECTIVE PRECOMMIT V1", flush=True)
    print("AM only. No training. No PnL. No Exact. Min-percentile-rank joint quality freeze.", flush=True)

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
    prior = _load(PRIOR)
    preg = prior.get("required") or {}
    prior_pack = freeze_prior(preg, str(prior.get("ANALYSIS_ID") or ""))
    print("prior", prior_pack.get("ok"), prior_pack.get("checks"), flush=True)
    if not prior_pack.get("ok"):
        return _integrity(
            f"STOP. Prior {PRIOR_ANALYSIS_ID_REQUIRED} / {PRIOR_AM_TARGET_STATUS_REQUIRED} required.",
            extra={"prior": prior_pack},
        )
    if not ROWS_PATH.is_file():
        return _integrity("STOP. Common-cohort path_rows missing.")

    path_body = json.loads(ROWS_PATH.read_text(encoding="utf-8"))
    rows = list(path_body.get("rows") or [])
    attach_joint_labels(rows)
    lab = labeled_rows(rows)

    harvest = []
    for day in ELIGIBLE_DAYS:
        fp = WAIT_CACHE / f"{day}_WAIT.json"
        saved = _load(fp)
        if not (saved.get("ok") and saved.get("date") == day and saved.get("rows")):
            print("STOP missing WAIT cache", day, flush=True)
            return _integrity(f"STOP. Missing WAIT harvest cache for {day}.")
        harvest.extend(list(saved.get("rows") or []))
    wf_by = wf_index(harvest)
    miss = 0
    for r in lab:
        h = wf_by.get(row_key(r))
        if h is None:
            miss += 1
            r["waits"] = {}
            continue
        r["waits"] = dict(h.get("waits") or {})
        r["limit"] = h.get("limit")
    print(f"join miss={miss} harvest={len(harvest)} labeled={len(lab)}", flush=True)
    if miss != 0:
        return _integrity("STOP. JOIN_MISS_N != 0.", extra={"JOIN_MISS_N": miss})

    attach_w5(rows)
    pop = am_population(rows)
    am = pop["am"]
    pm_used = sum(1 for r in am if session_of(r) != "AM")
    pop_pack = freeze_population(am=am, am_top3=pop["am_top3"])
    print("population", pop_pack.get("ok"), pop_pack.get("checks"), flush=True)
    if not pop_pack.get("ok"):
        return _integrity(
            "STOP. Frozen AM W5 labeled population or CURRENT Top3 fill rate did not reproduce.",
            extra={"population": pop_pack, "JOIN_MISS_N": miss},
        )
    if pm_used != 0:
        return _integrity("STOP. AM analysis set contained PM rows.", extra={"PM_ROWS_USED_N": pm_used})

    example = maximin_example()
    contract = objective_contract()
    decision = decide(pop_ok=True, prior_ok=True)
    base_parity = True
    required = {
        "BASE_PARITY": base_parity,
        "SESSION": SESSION,
        "DEV_WAIT_SEC": DEV_WAIT_SEC,
        "RUNTIME_WAIT_SEC": WAIT_SEC,
        "STAGE1_TARGET": STAGE1_TARGET,
        "STAGE2_U_TARGET": STAGE2_U_TARGET,
        "STAGE2_D_TARGET": STAGE2_D_TARGET,
        "QUALITY_COMBINATION": QUALITY_COMBINATION,
        "U_D_WEIGHT_SEARCH_ALLOWED": U_D_WEIGHT_SEARCH_ALLOWED,
        "STAGE1_STAGE2_SINGLE_SCALAR_ALLOWED": STAGE1_STAGE2_SINGLE_SCALAR_ALLOWED,
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "JOIN_MISS_N": miss,
        "PM_ROWS_USED_N": pm_used,
    }
    print(f"CASE={decision.get('CASE')} VERDICT={decision.get('VERDICT')}", flush=True)
    return write_report(
        required,
        decision=decision,
        extra={
            "population": pop_pack,
            "prior": prior_pack,
            "objective": contract,
            "maximin_example": example,
        },
        sheets_extra={
            "Parity": kv_rows(
                {
                    **{f"POP_{k}": v for k, v in (pop_pack.get("checks") or {}).items()},
                    **{f"PRIOR_{k}": v for k, v in (prior_pack.get("checks") or {}).items()},
                    "BASE_PARITY": True,
                }
            ),
            "Objective": kv_rows(contract),
            "Ordering": kv_rows(
                {
                    "PRIMARY_KEY": contract.get("ORDERING_PRIMARY"),
                    "SECONDARY_QUALITY_KEY": contract.get("ORDERING_SECONDARY"),
                    "QUALITY_TIE_1": contract.get("QUALITY_TIE_1"),
                    "QUALITY_TIE_2": contract.get("QUALITY_TIE_2"),
                    "STAGE1_THRESHOLD_CREATED": False,
                    "TOPN_SHORTLIST_CREATED": False,
                    "PROBABILITY_CUTOFF_CREATED": False,
                    "NOTE": "Final admission rule is evaluated in the next development run only.",
                }
            ),
            "DevelopmentArms": [
                {"arm": "CONTROL", "definition": contract.get("CONTROL_ARM"), "allowed": True},
                {"arm": "FILL_ONLY", "definition": contract.get("FILL_ONLY_ARM"), "allowed": True},
                {"arm": "TWO_STAGE", "definition": contract.get("TWO_STAGE_ARM"), "allowed": True},
                {"arm": "U_ONLY", "definition": "diagnostic reference only", "allowed": False},
                {"arm": "D_ONLY", "definition": "diagnostic reference only", "allowed": False},
                {"arm": "WEIGHTED_U_D", "definition": "forbidden", "allowed": False},
                {"arm": "BEST_REPRESENTATION", "definition": "forbidden", "allowed": False},
            ],
            "MaximinExample": example,
            "Integrity": kv_rows(
                {
                    "JOIN_MISS_N": miss,
                    "PM_ROWS_USED_N": pm_used,
                    "SESSION": SESSION,
                    "RUNTIME_WAIT_SEC": WAIT_SEC,
                    "DEV_WAIT_SEC": DEV_WAIT_SEC,
                    "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
                    "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
                    "U_D_WEIGHT_SEARCH_ALLOWED": U_D_WEIGHT_SEARCH_ALLOWED,
                    "STAGE1_STAGE2_SINGLE_SCALAR_ALLOWED": STAGE1_STAGE2_SINGLE_SCALAR_ALLOWED,
                    "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                    "PNL_USED": PNL_USED,
                    "EXACT_RAN": EXACT_RAN,
                    "DEVELOPMENT_RUN_STARTED": DEVELOPMENT_RUN_STARTED,
                }
            ),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
