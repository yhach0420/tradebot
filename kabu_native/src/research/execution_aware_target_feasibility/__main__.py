"""Offline execution-aware target feasibility. No Runtime write. No Paper. No Exact."""
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

from research.canonical_entry_performance_rebase.analyze import row_key, wf_index
from research.direct_joint_objective.oof import attach_joint_labels
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.entry_execution_feasibility.analyze import fill_counts, fillable_joint_availability, labeled_rows
from research.execution_aware_target_feasibility import (
    ANALYSIS_ID,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    ELIGIBLE_DAYS,
    EXACT_RAN,
    EXECUTION_REDESIGN_STARTED,
    FEATURE_SEARCH,
    FILL_RULE_CHANGED,
    LIMIT_CHANGED,
    MARKETABLE_ASSUMED,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    NEW_TARGET_CREATED,
    OCCUPANCY_APPLIED,
    PAPER_OPERATED,
    PNL_USED,
    POSITION_CAP_APPLIED,
    PRIOR_VERDICT_REQUIRED,
    REENTRY_APPLIED,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    STRATEGY_CREATED,
    TAKER_FILL_ADDED,
    TARGET_MODEL_STARTED,
    THRESHOLD_SEARCH,
    TOPK_SEARCH,
    TRUE_OOS,
    WAIT_CHANGED,
)
from research.execution_aware_target_feasibility.analyze import (
    attach_postfill,
    current_enrichment,
    decide,
    executed_old_joint,
    fill_parity,
    fillable_geometry,
    fillable_support,
    mechanism,
    postfill_pareto,
)
from research.execution_aware_target_feasibility.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import WAIT_SEC

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
PRIOR = NATIVE / "results" / "research" / "entry_execution_feasibility" / "report.json"
ROWS_PATH = NATIVE / "results" / "research" / "_work_cache" / "entry_target_architecture" / "path_rows.json"
FILL_CACHE = NATIVE / "results" / "research" / "_work_cache" / "entry_execution_feasibility"


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
                "WAIT_SEC": WAIT_SEC,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "TARGET_MODEL_STARTED": TARGET_MODEL_STARTED,
                "FEATURE_SEARCH": FEATURE_SEARCH,
                "FILL_RULE_CHANGED": FILL_RULE_CHANGED,
                "WAIT_CHANGED": WAIT_CHANGED,
                "PNL_USED": PNL_USED,
                "EXACT_RAN": EXACT_RAN,
            }
        ),
        "Geometry": [{"empty": True}],
        "Mechanism": [{"empty": True}],
        "Enrichment": [{"empty": True}],
        "Pareto": [{"empty": True}],
        "Support": [{"empty": True}],
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
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "TARGET_MODEL_STARTED": TARGET_MODEL_STARTED,
                "FEATURE_SEARCH": FEATURE_SEARCH,
                "PNL_USED": PNL_USED,
                "TOPK_SEARCH": TOPK_SEARCH,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
                "FILL_RULE_CHANGED": FILL_RULE_CHANGED,
                "WAIT_CHANGED": WAIT_CHANGED,
                "LIMIT_CHANGED": LIMIT_CHANGED,
                "MARKETABLE_ASSUMED": MARKETABLE_ASSUMED,
                "TAKER_FILL_ADDED": TAKER_FILL_ADDED,
                "POSITION_CAP_APPLIED": POSITION_CAP_APPLIED,
                "OCCUPANCY_APPLIED": OCCUPANCY_APPLIED,
                "REENTRY_APPLIED": REENTRY_APPLIED,
                "RUNTIME_CANDIDATE_CREATED": RUNTIME_CANDIDATE_CREATED,
                "STRATEGY_CREATED": STRATEGY_CREATED,
                "EXECUTION_REDESIGN_STARTED": EXECUTION_REDESIGN_STARTED,
                "NEW_TARGET_CREATED": NEW_TARGET_CREATED,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"VERDICT={required.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(
        "STOP. No target model. No execution policy change. No runtime candidate. Runtime unchanged. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "EXECUTION_AWARE_TARGET_FEASIBILITY_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str, extra: dict | None = None) -> int:
    body = {
        "VERDICT": "EXECUTION_AWARE_TARGET_FEASIBILITY_INTEGRITY_FAILED",
        "TARGET_ARCHITECTURE": None,
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "PRIMARY_FINDING": note,
        "BASE_PARITY": False,
        "PRIMARY_MECHANISM": None,
    }
    if extra:
        body.update(extra)
    return write_report(
        body,
        decision={
            "CASE": None,
            "VERDICT": "EXECUTION_AWARE_TARGET_FEASIBILITY_INTEGRITY_FAILED",
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
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE EXECUTION-AWARE TARGET FEASIBILITY V1", flush=True)
    print("Reuse frozen standalone Passive Fill harvest. No model. No Exact.", flush=True)

    if abs(float(WAIT_SEC) - 1.0) > 1e-12:
        print("STOP WAIT_SEC drift", flush=True)
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
    if preg.get("VERDICT") != PRIOR_VERDICT_REQUIRED:
        print("STOP prior execution-feasibility verdict mismatch", preg.get("VERDICT"), flush=True)
        return _integrity("STOP. Prior JOINT_OPPORTUNITY_NOT_PASSIVELY_ACCESSIBLE required.")
    if not ROWS_PATH.is_file():
        return _integrity("STOP. Common-cohort path_rows missing.")

    path_body = json.loads(ROWS_PATH.read_text(encoding="utf-8"))
    rows = list(path_body.get("rows") or [])
    attach_joint_labels(rows)
    lab = labeled_rows(rows)

    harvest = []
    for day in ELIGIBLE_DAYS:
        fp = FILL_CACHE / f"{day}_FILL.json"
        saved = _load(fp)
        if not (saved.get("ok") and saved.get("date") == day and saved.get("rows")):
            print("STOP missing FILL cache", day, flush=True)
            return _integrity(f"STOP. Missing standalone fill cache for {day}.")
        harvest.extend(list(saved.get("rows") or []))
    wf_by = wf_index(harvest)
    miss = 0
    for r in lab:
        h = wf_by.get(row_key(r))
        if h is None:
            miss += 1
            continue
        r["WOULD_FILL"] = bool(h.get("WOULD_FILL"))
        r["nonfill_class"] = h.get("nonfill_class")
        r["MFE_FROM_FILL"] = h.get("MFE_FROM_FILL")
        r["DOWNSIDE_FROM_FILL"] = h.get("DOWNSIDE_FROM_FILL")
        r["fill_path_complete"] = h.get("fill_path_complete")
        r["fill_t"] = h.get("fill_t")
    if miss != 0:
        return _integrity("STOP. JOIN_MISS_N != 0.", extra={"JOIN_MISS_N": miss})
    attach_postfill(rows)

    counts = fill_counts(rows)
    fillable = fillable_joint_availability(rows, oracle_avail_n=284)
    parity = fill_parity(counts, fillable)
    print("parity", parity.get("BASE_PARITY"), parity.get("checks"), flush=True)
    if not parity.get("BASE_PARITY"):
        return _integrity(
            "STOP. Frozen fill-count parity vs JOINT_OPPORTUNITY_EXECUTION_FEASIBILITY failed.",
            extra={"parity": parity},
        )
    print("BASE_PARITY true", flush=True)

    geom = fillable_geometry(rows)
    mech = mechanism(rows)
    enr = current_enrichment(
        rows,
        all_rate=counts.get("ALL_WOULD_FILL_RATE"),
        top3_rate=counts.get("CURRENT_TOP3_WOULD_FILL_RATE"),
    )
    pareto = postfill_pareto(rows)
    oldj = executed_old_joint(rows)
    support = fillable_support(rows)
    decision = decide(parity_ok=True, geom=geom, support=support)
    required = {
        "BASE_PARITY": True,
        "COHORT_N": geom.get("COHORT_N"),
        "FILLABLE_0_COHORT_N": geom.get("FILLABLE_0_COHORT_N"),
        "FILLABLE_1_COHORT_N": geom.get("FILLABLE_1_COHORT_N"),
        "FILLABLE_2_COHORT_N": geom.get("FILLABLE_2_COHORT_N"),
        "FILLABLE_3PLUS_COHORT_N": geom.get("FILLABLE_3PLUS_COHORT_N"),
        "FILLABLE_ANY_COHORT_RATE": geom.get("FILLABLE_ANY_COHORT_RATE"),
        "MULTI_FILLABLE_COHORT_RATE": geom.get("MULTI_FILLABLE_COHORT_RATE"),
        "FILLABLE_PER_COHORT_MEAN": geom.get("FILLABLE_PER_COHORT_MEAN"),
        "CURRENT_FILL_ENRICHMENT": enr.get("CURRENT_FILL_ENRICHMENT"),
        "VALID_BOARD_WITHIN_WAIT_RATE": mech.get("VALID_BOARD_WITHIN_WAIT_RATE"),
        "ASK_CROSS_GIVEN_VALID_BOARD_RATE": mech.get("ASK_CROSS_GIVEN_VALID_BOARD_RATE"),
        "EXECUTED_OLD_JOINT_N": oldj.get("EXECUTED_OLD_JOINT_N"),
        "EXECUTED_OLD_JOINT_RATE_ALL": oldj.get("EXECUTED_OLD_JOINT_RATE_ALL"),
        "FILLABLE_TRAINING_N": support.get("FILLABLE_TRAINING_N"),
        "MIN_LODO_TRAIN_FILLABLE_N": support.get("MIN_LODO_TRAIN_FILLABLE_N"),
        "FILLABLE_PARETO_SIZE_MEAN": pareto.get("FILLABLE_PARETO_SIZE_MEAN"),
        "MULTI_FILLABLE_WITH_DOMINANCE_RATE": pareto.get("MULTI_FILLABLE_WITH_DOMINANCE_RATE"),
        "TARGET_ARCHITECTURE": decision.get("TARGET_ARCHITECTURE"),
        "PRIMARY_MECHANISM": decision.get("PRIMARY_MECHANISM"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "parity": parity,
        "WAIT_SEC": WAIT_SEC,
        "JOIN_MISS_N": miss,
    }
    print(
        f"CASE={decision.get('CASE')} any={geom.get('FILLABLE_ANY_COHORT_RATE')} "
        f"multi={geom.get('MULTI_FILLABLE_COHORT_RATE')} arch={decision.get('TARGET_ARCHITECTURE')}",
        flush=True,
    )
    return write_report(
        required,
        decision=decision,
        extra={
            "counts": counts,
            "fillable_joint": fillable,
            "geometry": geom,
            "mechanism": mech,
            "enrichment": {k: v for k, v in enr.items() if k != "daily"},
            "pareto": pareto,
            "executed_old_joint": {k: v for k, v in oldj.items() if k != "daily"},
            "support": {k: v for k, v in support.items() if k not in {"daily", "lodo"}},
        },
        sheets_extra={
            "Geometry": kv_rows(geom),
            "Mechanism": kv_rows(mech),
            "Enrichment": enr.get("daily") or [{"empty": True}],
            "Pareto": kv_rows(pareto),
            "Support": kv_rows({k: v for k, v in support.items() if k not in {"daily", "lodo"}}),
            "Integrity": kv_rows({"JOIN_MISS_N": miss, "WAIT_SEC": WAIT_SEC, "BASE_PARITY": True}),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
