"""Offline AM WAIT5 two-stage interface precommit. No Runtime write. No Paper. No Exact."""
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

from research.am_wait5_two_stage_interface_precommit import (
    ALLOWED_DEVELOPMENT_ARMS,
    ANALYSIS_ID,
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
    FILL_ONLY_AND_TWO_STAGE_DISTINCT_BY_CONSTRUCTION,
    FINAL_SELECTION_N,
    JOINT_BINARY_TARGET,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    NEXT_RESEARCH,
    PAPER_OPERATED,
    PARETO_KNEE_SEARCH,
    PARETO_TARGET_ADOPTED,
    PERFORMANCE_EVAL_STARTED,
    PNL_USED,
    PRIOR_ANALYSIS_ID_REQUIRED,
    PRIOR_VERDICT_REQUIRED,
    PROBABILITY_THRESHOLD_ALLOWED,
    QUALITY_SCALAR_WEIGHT,
    REPRESENTATION_N,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    SESSION,
    SESSION_INDICATOR_COMMON_MODEL,
    SHORTLIST_SEARCH_ALLOWED,
    STAGE1_SHORTLIST_N,
    STAGE1_SHORTLIST_RULE,
    STAGE2_SELECTION_RULE,
    STRATEGY_CREATED,
    THRESHOLD_SEARCH,
    TOPK_SEARCH,
    TRUE_OOS,
    U_D_WEIGHT_SEARCH_ALLOWED,
    WAIT_POLICY_ADOPTED,
    WEIGHT_SEARCH,
)
from research.am_wait5_two_stage_interface_precommit.analyze import (
    decide,
    distinctness_example,
    freeze_prior,
    interface_contract,
)
from research.am_wait5_two_stage_interface_precommit.publish import (
    OUT,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.am_wait5_two_stage_objective_precommit.analyze import am_population, freeze_population
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
PRIOR = NATIVE / "results" / "research" / "am_wait5_two_stage_objective_precommit" / "report.json"
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
                "DEV_WAIT_SEC": DEV_WAIT_SEC,
                "RUNTIME_WAIT_SEC": WAIT_SEC,
                "STAGE1_SHORTLIST_N": STAGE1_SHORTLIST_N,
                "STAGE1_SHORTLIST_RULE": STAGE1_SHORTLIST_RULE,
                "STAGE2_SELECTION_RULE": STAGE2_SELECTION_RULE,
                "FINAL_SELECTION_N": FINAL_SELECTION_N,
                "REPRESENTATION_N": REPRESENTATION_N,
                "SHORTLIST_SEARCH_ALLOWED": SHORTLIST_SEARCH_ALLOWED,
                "PROBABILITY_THRESHOLD_ALLOWED": PROBABILITY_THRESHOLD_ALLOWED,
                "U_D_WEIGHT_SEARCH_ALLOWED": U_D_WEIGHT_SEARCH_ALLOWED,
                "BEST_REPRESENTATION_ADOPTED": BEST_REPRESENTATION_ADOPTED,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "PNL_USED": PNL_USED,
                "EXACT_RAN": EXACT_RAN,
                "PERFORMANCE_EVAL_STARTED": PERFORMANCE_EVAL_STARTED,
                "DEVELOPMENT_RUN_STARTED": DEVELOPMENT_RUN_STARTED,
            }
        ),
        "Parity": [{"empty": True}],
        "Interface": [{"empty": True}],
        "DevelopmentArms": [{"empty": True}],
        "DistinctnessExample": [{"empty": True}],
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
                "SHORTLIST_SEARCH_ALLOWED": SHORTLIST_SEARCH_ALLOWED,
                "PROBABILITY_THRESHOLD_ALLOWED": PROBABILITY_THRESHOLD_ALLOWED,
                "SESSION_INDICATOR_COMMON_MODEL": SESSION_INDICATOR_COMMON_MODEL,
                "RUNTIME_CANDIDATE_CREATED": RUNTIME_CANDIDATE_CREATED,
                "STRATEGY_CREATED": STRATEGY_CREATED,
                "PERFORMANCE_EVAL_STARTED": PERFORMANCE_EVAL_STARTED,
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
        "STOP. No performance run. Runtime WAIT_SEC=1.0. W5 not adopted. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "AM_WAIT5_TWO_STAGE_INTERFACE_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str, extra: dict | None = None) -> int:
    body = {
        "BASE_PARITY": False,
        "SESSION": SESSION,
        "STAGE1_SHORTLIST_N": STAGE1_SHORTLIST_N,
        "STAGE1_SHORTLIST_RULE": STAGE1_SHORTLIST_RULE,
        "STAGE2_SELECTION_RULE": STAGE2_SELECTION_RULE,
        "FINAL_SELECTION_N": FINAL_SELECTION_N,
        "FILL_ONLY_AND_TWO_STAGE_DISTINCT_BY_CONSTRUCTION": False,
        "ALLOWED_DEVELOPMENT_ARMS": list(ALLOWED_DEVELOPMENT_ARMS),
        "SHORTLIST_SEARCH_ALLOWED": SHORTLIST_SEARCH_ALLOWED,
        "PROBABILITY_THRESHOLD_ALLOWED": PROBABILITY_THRESHOLD_ALLOWED,
        "U_D_WEIGHT_SEARCH_ALLOWED": U_D_WEIGHT_SEARCH_ALLOWED,
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "AM_WAIT5_TWO_STAGE_INTERFACE_INTEGRITY_FAILED",
        "PRIMARY_FINDING": note,
    }
    if extra:
        body.update(extra)
    return write_report(
        body,
        decision={
            "CASE": "FAIL",
            "VERDICT": "AM_WAIT5_TWO_STAGE_INTERFACE_INTEGRITY_FAILED",
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
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM WAIT5 TWO-STAGE INTERFACE PRECOMMIT V1", flush=True)
    print("AM only. Top5 shortlist then min-rank Top3. No training. No performance.", flush=True)

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
            f"STOP. Prior {PRIOR_ANALYSIS_ID_REQUIRED} / {PRIOR_VERDICT_REQUIRED} required.",
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

    demo = distinctness_example()
    if not demo.get("DISTINCT_BY_CONSTRUCTION"):
        return _integrity(
            "STOP. FILL_ONLY and TWO_STAGE were not distinct on the construction example.",
            extra={"distinctness": demo},
        )
    contract = interface_contract()
    decision = decide(pop_ok=True, prior_ok=True, distinct_ok=True)
    required = {
        "BASE_PARITY": True,
        "SESSION": SESSION,
        "STAGE1_SHORTLIST_N": STAGE1_SHORTLIST_N,
        "STAGE1_SHORTLIST_RULE": STAGE1_SHORTLIST_RULE,
        "STAGE2_SELECTION_RULE": STAGE2_SELECTION_RULE,
        "FINAL_SELECTION_N": FINAL_SELECTION_N,
        "FILL_ONLY_AND_TWO_STAGE_DISTINCT_BY_CONSTRUCTION": FILL_ONLY_AND_TWO_STAGE_DISTINCT_BY_CONSTRUCTION,
        "ALLOWED_DEVELOPMENT_ARMS": list(ALLOWED_DEVELOPMENT_ARMS),
        "SHORTLIST_SEARCH_ALLOWED": SHORTLIST_SEARCH_ALLOWED,
        "PROBABILITY_THRESHOLD_ALLOWED": PROBABILITY_THRESHOLD_ALLOWED,
        "U_D_WEIGHT_SEARCH_ALLOWED": U_D_WEIGHT_SEARCH_ALLOWED,
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "JOIN_MISS_N": miss,
        "PM_ROWS_USED_N": pm_used,
    }
    print(
        f"CASE={decision.get('CASE')} VERDICT={decision.get('VERDICT')} "
        f"fill_only={demo.get('FILL_ONLY_SYMBOLS')} two_stage={demo.get('TWO_STAGE_SYMBOLS')}",
        flush=True,
    )
    return write_report(
        required,
        decision=decision,
        extra={
            "population": pop_pack,
            "prior": prior_pack,
            "interface": contract,
            "distinctness_example": {
                k: v
                for k, v in demo.items()
                if k not in {"fill_only", "two_stage_shortlist", "two_stage_selected"}
            },
        },
        sheets_extra={
            "Parity": kv_rows(
                {
                    **{f"POP_{k}": v for k, v in (pop_pack.get("checks") or {}).items()},
                    **{f"PRIOR_{k}": v for k, v in (prior_pack.get("checks") or {}).items()},
                    "BASE_PARITY": True,
                }
            ),
            "Interface": kv_rows(contract),
            "DevelopmentArms": [
                {"arm": "CONTROL", "definition": contract.get("CONTROL_ARM"), "allowed": True},
                {"arm": "FILL_ONLY", "definition": contract.get("FILL_ONLY_ARM"), "allowed": True},
                {"arm": "TWO_STAGE", "definition": contract.get("TWO_STAGE_ARM"), "allowed": True},
                {"arm": "U_ONLY", "definition": "forbidden", "allowed": False},
                {"arm": "D_ONLY", "definition": "forbidden", "allowed": False},
                {"arm": "WEIGHTED_U_D", "definition": "forbidden", "allowed": False},
                {"arm": "P_FILL5_TIMES_QUALITY", "definition": "forbidden", "allowed": False},
                {"arm": "PARETO", "definition": "forbidden", "allowed": False},
                {"arm": "BEST_REPRESENTATION", "definition": "forbidden", "allowed": False},
            ],
            "DistinctnessExample": [
                {"role": "FILL_ONLY", **r} for r in (demo.get("fill_only") or [])
            ]
            + [{"role": "TWO_STAGE_SHORTLIST", **r} for r in (demo.get("two_stage_shortlist") or [])]
            + [{"role": "TWO_STAGE_SELECTED", **r} for r in (demo.get("two_stage_selected") or [])],
            "Integrity": kv_rows(
                {
                    "JOIN_MISS_N": miss,
                    "PM_ROWS_USED_N": pm_used,
                    "SESSION": SESSION,
                    "RUNTIME_WAIT_SEC": WAIT_SEC,
                    "DEV_WAIT_SEC": DEV_WAIT_SEC,
                    "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
                    "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
                    "SHORTLIST_SEARCH_ALLOWED": SHORTLIST_SEARCH_ALLOWED,
                    "PROBABILITY_THRESHOLD_ALLOWED": PROBABILITY_THRESHOLD_ALLOWED,
                    "U_D_WEIGHT_SEARCH_ALLOWED": U_D_WEIGHT_SEARCH_ALLOWED,
                    "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                    "PNL_USED": PNL_USED,
                    "EXACT_RAN": EXACT_RAN,
                    "PERFORMANCE_EVAL_STARTED": PERFORMANCE_EVAL_STARTED,
                    "DEVELOPMENT_RUN_STARTED": DEVELOPMENT_RUN_STARTED,
                }
            ),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
