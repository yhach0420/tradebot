"""Offline W5 precommit. Reuse WAIT harvest. No Runtime write. No Paper. No Exact."""
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
from research.entry_execution_feasibility.analyze import labeled_rows
from research.passive_wait5_precommit import (
    ANALYSIS_ID,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    ELIGIBLE_DAYS,
    ENTRY_RETRAIN_STARTED,
    EXACT_RAN,
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
    PRICE_POLICY_STARTED,
    PRIOR_VERDICT_REQUIRED,
    REENTRY_APPLIED,
    REFERENCE_WAIT_IDS,
    REPRICING,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    STRATEGY_CREATED,
    TAKER_FILL_ADDED,
    TRUE_OOS,
    W10_RESELECTED,
    W2_RESELECTED,
    WAIT_INTERPOLATION,
    WAIT_POLICY_ADOPTED,
    WAIT_POLICY_CANDIDATE,
)
from research.passive_wait5_precommit.analyze import (
    clock_robustness,
    day_new_quality,
    day_robustness,
    decide,
    delay_geometry,
    freeze_parity,
    gates,
    nested_inclusion,
    session_robustness,
    slice_metrics,
)
from research.passive_wait5_precommit.publish import (
    OUT,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.passive_wait_policy_reassessment.analyze import fill_quality_group, incremental, wait_metrics
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import WAIT_SEC

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
PRIOR = NATIVE / "results" / "research" / "passive_wait_policy_reassessment" / "report.json"
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
                "RUNTIME_WAIT_SEC": WAIT_SEC,
                "WAIT_POLICY_CANDIDATE": WAIT_POLICY_CANDIDATE,
                "CONTROL": "W1=1s",
                "CANDIDATE": "W5=5s",
                "REFERENCE": list(REFERENCE_WAIT_IDS),
                "WAIT_POLICY_ADOPTED": WAIT_POLICY_ADOPTED,
                "WAIT_INTERPOLATION": WAIT_INTERPOLATION,
                "REPRICING": REPRICING,
                "LIMIT_CHANGED": LIMIT_CHANGED,
                "FILL_RULE_CHANGED": FILL_RULE_CHANGED,
                "W2_RESELECTED": W2_RESELECTED,
                "W10_RESELECTED": W10_RESELECTED,
                "PNL_USED": PNL_USED,
                "EXACT_RAN": EXACT_RAN,
                "ENTRY_RETRAIN_STARTED": ENTRY_RETRAIN_STARTED,
            }
        ),
        "Parity": [{"empty": True}],
        "Days": [{"empty": True}],
        "Sessions": [{"empty": True}],
        "Clocks": [{"empty": True}],
        "Delay": [{"empty": True}],
        "Quality": [{"empty": True}],
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
                "WAIT_INTERPOLATION": WAIT_INTERPOLATION,
                "REPRICING": REPRICING,
                "LIMIT_CHANGED": LIMIT_CHANGED,
                "FILL_RULE_CHANGED": FILL_RULE_CHANGED,
                "MARKETABLE_ASSUMED": MARKETABLE_ASSUMED,
                "TAKER_FILL_ADDED": TAKER_FILL_ADDED,
                "POSITION_CAP_APPLIED": POSITION_CAP_APPLIED,
                "OCCUPANCY_APPLIED": OCCUPANCY_APPLIED,
                "REENTRY_APPLIED": REENTRY_APPLIED,
                "RUNTIME_CANDIDATE_CREATED": RUNTIME_CANDIDATE_CREATED,
                "STRATEGY_CREATED": STRATEGY_CREATED,
                "PRICE_POLICY_STARTED": PRICE_POLICY_STARTED,
                "NEW_TARGET_CREATED": NEW_TARGET_CREATED,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "FEATURE_SEARCH": FEATURE_SEARCH,
                "PNL_USED": PNL_USED,
                "ENTRY_RETRAIN_STARTED": ENTRY_RETRAIN_STARTED,
                "W2_RESELECTED": W2_RESELECTED,
                "W10_RESELECTED": W10_RESELECTED,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"VERDICT={required.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(
        "STOP. Runtime WAIT_SEC=1.0. W5 not adopted. No ENTRY retrain. No Exact. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "WAIT5_PRECOMMIT_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str, extra: dict | None = None) -> int:
    body = {
        "BASE_PARITY": False,
        "WAIT_POLICY_CANDIDATE": WAIT_POLICY_CANDIDATE,
        "W5_ANY_RATE": None,
        "W5_MULTI_RATE": None,
        "AM_W1_ANY": None,
        "AM_W5_ANY": None,
        "PM_W1_ANY": None,
        "PM_W5_ANY": None,
        "AM_W1_MULTI": None,
        "AM_W5_MULTI": None,
        "PM_W1_MULTI": None,
        "PM_W5_MULTI": None,
        "CLOCKS_ANY_IMPROVED_N": None,
        "CLOCKS_MULTI_IMPROVED_N": None,
        "W5_NEW_FILL_N": None,
        "W5_NEW_POSTFILL_MFE": None,
        "W5_NEW_POSTFILL_DOWNSIDE": None,
        "W5_JOINT_SURVIVAL": None,
        "W5_PRECOMMIT_PASS": False,
        "PRIMARY_FINDING": note,
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "WAIT5_PRECOMMIT_INTEGRITY_FAILED",
    }
    if extra:
        body.update(extra)
    return write_report(
        body,
        decision={
            "CASE": "D",
            "VERDICT": "WAIT5_PRECOMMIT_INTEGRITY_FAILED",
            "WAIT_POLICY_CANDIDATE": WAIT_POLICY_CANDIDATE,
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
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE PASSIVE WAIT5 PRECOMMIT V1", flush=True)
    print("Reuse frozen WAIT harvest. Candidate W5. Runtime WAIT unchanged.", flush=True)

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
        print("STOP prior verdict mismatch", preg.get("VERDICT"), flush=True)
        return _integrity("STOP. Prior PASSIVE_WAIT_EXTENSION_FEASIBLE required.")
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

    w1 = slice_metrics(rows, "W1")
    w5 = slice_metrics(rows, "W5")
    w1_full = wait_metrics(rows, "W1")
    w5_full = wait_metrics(rows, "W5")
    ref = {wid: slice_metrics(rows, wid) for wid in REFERENCE_WAIT_IDS}
    nested = nested_inclusion(rows)
    inc5 = incremental(rows, "W5")
    parity = freeze_parity(w1=w1, w5=w5, w5_new_n=int(inc5.get("NEW_FILL_VS_W1_N") or -1))
    print("parity", parity.get("BASE_PARITY"), parity.get("checks"), flush=True)
    if not parity.get("BASE_PARITY") or not nested.get("NESTED_INCLUSION_OK"):
        return _integrity(
            "STOP. Frozen W1/W5 harvest control or nested WAIT inclusion failed.",
            extra={"parity": parity, "nested": nested, "JOIN_MISS_N": miss},
        )
    print("BASE_PARITY true", flush=True)

    day = day_robustness(rows)
    sess = session_robustness(rows)
    clocks = clock_robustness(rows)
    delay = delay_geometry(rows)
    w1_fill = fill_quality_group(rows, "W1")
    day_q = day_new_quality(rows)
    gbody = gates(
        w5=w5,
        sess=sess,
        clocks=clocks,
        w1_fill=w1_fill,
        w5_new=inc5,
        nested_ok=bool(nested.get("NESTED_INCLUSION_OK")),
        day=day,
        delay=delay,
        parity_ok=True,
    )
    decision = decide(gates_body=gbody)
    am = sess.get("AM") or {}
    pm = sess.get("PM") or {}
    required = {
        "BASE_PARITY": True,
        "WAIT_POLICY_CANDIDATE": WAIT_POLICY_CANDIDATE,
        "W5_ANY_RATE": w5.get("FILLABLE_ANY_COHORT_RATE"),
        "W5_MULTI_RATE": w5.get("MULTI_FILLABLE_COHORT_RATE"),
        "AM_W1_ANY": am.get("W1_ANY"),
        "AM_W5_ANY": am.get("W5_ANY"),
        "PM_W1_ANY": pm.get("W1_ANY"),
        "PM_W5_ANY": pm.get("W5_ANY"),
        "AM_W1_MULTI": am.get("W1_MULTI"),
        "AM_W5_MULTI": am.get("W5_MULTI"),
        "PM_W1_MULTI": pm.get("W1_MULTI"),
        "PM_W5_MULTI": pm.get("W5_MULTI"),
        "CLOCKS_ANY_IMPROVED_N": clocks.get("CLOCKS_ANY_IMPROVED_N"),
        "CLOCKS_MULTI_IMPROVED_N": clocks.get("CLOCKS_MULTI_IMPROVED_N"),
        "W5_NEW_FILL_N": inc5.get("NEW_FILL_VS_W1_N"),
        "W5_NEW_POSTFILL_MFE": inc5.get("POSTFILL_MFE_MEDIAN"),
        "W5_NEW_POSTFILL_DOWNSIDE": inc5.get("POSTFILL_DOWNSIDE_MEDIAN"),
        "W5_JOINT_SURVIVAL": w5.get("JOINT_LABEL_SURVIVAL_AFTER_FILL_RATE"),
        "W5_PRECOMMIT_PASS": gbody.get("W5_PRECOMMIT_PASS"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "W1_FILL_POSTFILL_MFE": w1_fill.get("POSTFILL_MFE_MEDIAN"),
        "W1_FILL_POSTFILL_DOWNSIDE": w1_fill.get("POSTFILL_DOWNSIDE_MEDIAN"),
        "W5_NEW_MFE_POSITIVE_DAYS": day_q.get("W5_NEW_MFE_POSITIVE_DAYS"),
        "W5_NEW_DOWNSIDE_POSITIVE_DAYS": day_q.get("W5_NEW_DOWNSIDE_POSITIVE_DAYS"),
        "W5_ANY_GT_W1_DAYS": day.get("W5_ANY_GT_W1_DAYS"),
        "W5_ANY_LT_W1_DAYS": day.get("W5_ANY_LT_W1_DAYS"),
        "W5_MULTI_GT_W1_DAYS": day.get("W5_MULTI_GT_W1_DAYS"),
        "W5_MULTI_LT_W1_DAYS": day.get("W5_MULTI_LT_W1_DAYS"),
        "W5_FILL_N_GT_W1_DAYS": day.get("W5_FILL_N_GT_W1_DAYS"),
        "CLOCKS_ANY_UNCHANGED_N": clocks.get("CLOCKS_ANY_UNCHANGED_N"),
        "CLOCKS_ANY_WORSENED_N": clocks.get("CLOCKS_ANY_WORSENED_N"),
        "CLOCKS_MULTI_UNCHANGED_N": clocks.get("CLOCKS_MULTI_UNCHANGED_N"),
        "CLOCKS_MULTI_WORSENED_N": clocks.get("CLOCKS_MULTI_WORSENED_N"),
        "RUNTIME_WAIT_SEC": WAIT_SEC,
        "JOIN_MISS_N": miss,
        "parity": parity,
    }
    print(
        f"CASE={decision.get('CASE')} pass={gbody.get('W5_PRECOMMIT_PASS')} "
        f"any={w5.get('FILLABLE_ANY_COHORT_RATE')} multi={w5.get('MULTI_FILLABLE_COHORT_RATE')}",
        flush=True,
    )
    return write_report(
        required,
        decision=decision,
        extra={
            "w1": w1,
            "w5": w5,
            "w1_full": w1_full,
            "w5_full": w5_full,
            "reference": ref,
            "nested": nested,
            "incremental_w5": inc5,
            "w1_fill_group": w1_fill,
            "day": {k: v for k, v in day.items() if k != "daily"},
            "session": sess,
            "clock": {k: v for k, v in clocks.items() if k != "clocks"},
            "delay": delay,
            "day_new_quality": {k: v for k, v in day_q.items() if k != "daily"},
            "gates": gbody,
        },
        sheets_extra={
            "Parity": kv_rows(parity),
            "Days": day.get("daily") or [{"empty": True}],
            "Sessions": [sess.get("AM") or {}, sess.get("PM") or {}],
            "Clocks": clocks.get("clocks") or [{"empty": True}],
            "Delay": delay.get("buckets") or [{"empty": True}],
            "Quality": [
                {"group": "W1_FILL", **w1_fill},
                {"group": "W5_NEW", **inc5},
                {k: v for k, v in day_q.items() if k != "daily"},
            ],
            "Gates": kv_rows(gbody),
            "Integrity": kv_rows(
                {
                    "JOIN_MISS_N": miss,
                    "RUNTIME_WAIT_SEC": WAIT_SEC,
                    "BASE_PARITY": True,
                    "NESTED_INCLUSION_OK": nested.get("NESTED_INCLUSION_OK"),
                    "WAIT_POLICY_ADOPTED": WAIT_POLICY_ADOPTED,
                    "W2_RESELECTED": W2_RESELECTED,
                    "W10_RESELECTED": W10_RESELECTED,
                    "ENTRY_RETRAIN_STARTED": ENTRY_RETRAIN_STARTED,
                }
            ),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
