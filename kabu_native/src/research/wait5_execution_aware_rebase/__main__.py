"""Offline WAIT5 execution-aware ENTRY rebase. No Runtime write. No Paper. No Exact."""
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
from research.execution_aware_target_feasibility.analyze import fillable_geometry
from research.passive_wait_policy_reassessment.analyze import joint_access, survival
from research.wait5_execution_aware_rebase import (
    ANALYSIS_ID,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    DEV_WAIT_SEC,
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
    OLD_JOINT_ADOPTED,
    PAPER_OPERATED,
    PNL_USED,
    POSITION_CAP_APPLIED,
    PRICE_POLICY_STARTED,
    PRIOR_VERDICT_REQUIRED,
    REENTRY_APPLIED,
    REPRICING,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    STRATEGY_CREATED,
    TAKER_FILL_ADDED,
    THRESHOLD_SEARCH,
    TOPK_SEARCH,
    TRUE_OOS,
    W10_RESELECTED,
    WAIT_INTERPOLATION,
    WAIT_POLICY_ADOPTED,
)
from research.wait5_execution_aware_rebase.analyze import (
    attach_w5,
    current_fillability,
    current_quality,
    decide,
    delay_effect,
    fill_aligned,
    freeze_parity,
    postfill_geometry,
    session_capacity,
    threeplus_rate,
    y_fill5_pack,
)
from research.wait5_execution_aware_rebase.publish import (
    OUT,
    build_markdown,
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
PRIOR = NATIVE / "results" / "research" / "passive_wait5_precommit" / "report.json"
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
                "DEV_WAIT_SEC": DEV_WAIT_SEC,
                "WAIT_POLICY_ADOPTED": WAIT_POLICY_ADOPTED,
                "OLD_JOINT_ADOPTED": OLD_JOINT_ADOPTED,
                "ENTRY_RETRAIN_STARTED": ENTRY_RETRAIN_STARTED,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "FEATURE_SEARCH": FEATURE_SEARCH,
                "PNL_USED": PNL_USED,
                "EXACT_RAN": EXACT_RAN,
                "TOPK_SEARCH": TOPK_SEARCH,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
            }
        ),
        "Parity": [{"empty": True}],
        "Capacity": [{"empty": True}],
        "Sessions": [{"empty": True}],
        "YFill5": [{"empty": True}],
        "FillAligned": [{"empty": True}],
        "Pareto": [{"empty": True}],
        "CurrentFill": [{"empty": True}],
        "CurrentQuality": [{"empty": True}],
        "Delay": [{"empty": True}],
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
                "OLD_JOINT_ADOPTED": OLD_JOINT_ADOPTED,
                "ENTRY_RETRAIN_STARTED": ENTRY_RETRAIN_STARTED,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "FEATURE_SEARCH": FEATURE_SEARCH,
                "PNL_USED": PNL_USED,
                "TOPK_SEARCH": TOPK_SEARCH,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
                "MARKETABLE_ASSUMED": MARKETABLE_ASSUMED,
                "TAKER_FILL_ADDED": TAKER_FILL_ADDED,
                "POSITION_CAP_APPLIED": POSITION_CAP_APPLIED,
                "OCCUPANCY_APPLIED": OCCUPANCY_APPLIED,
                "REENTRY_APPLIED": REENTRY_APPLIED,
                "RUNTIME_CANDIDATE_CREATED": RUNTIME_CANDIDATE_CREATED,
                "STRATEGY_CREATED": STRATEGY_CREATED,
                "PRICE_POLICY_STARTED": PRICE_POLICY_STARTED,
                "NEW_TARGET_CREATED": NEW_TARGET_CREATED,
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
        "STOP. No ENTRY model. Runtime WAIT_SEC=1.0. W5 not adopted. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "WAIT5_EXECUTION_REBASE_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str, extra: dict | None = None) -> int:
    body = {
        "BASE_PARITY": False,
        "W5_ANY_RATE": None,
        "W5_MULTI_RATE": None,
        "W5_THREEPLUS_RATE": None,
        "AM_ANY_RATE": None,
        "AM_MULTI_RATE": None,
        "AM_THREEPLUS_RATE": None,
        "PM_ANY_RATE": None,
        "PM_MULTI_RATE": None,
        "PM_THREEPLUS_RATE": None,
        "Y_FILL5_POS_N": None,
        "CURRENT_TOP3_FILL5_RATE": None,
        "CURRENT_FILL5_ENRICHMENT": None,
        "CURRENT_SCORE_U_FILL_SPEARMAN": None,
        "CURRENT_SCORE_D_FILL_SPEARMAN": None,
        "AM_CURRENT_SCORE_U_FILL_SPEARMAN": None,
        "AM_CURRENT_SCORE_D_FILL_SPEARMAN": None,
        "PM_CURRENT_SCORE_U_FILL_SPEARMAN": None,
        "PM_CURRENT_SCORE_D_FILL_SPEARMAN": None,
        "FILLABLE_PARETO_SIZE_MEAN": None,
        "MULTI_FILLABLE_WITH_DOMINANCE_RATE": None,
        "THREEPLUS_WITH_DOMINANCE_RATE": None,
        "FILL_DELAY_U_SPEARMAN": None,
        "FILL_DELAY_D_SPEARMAN": None,
        "TARGET_ARCHITECTURE": None,
        "PRIMARY_MECHANISM": None,
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "WAIT5_EXECUTION_REBASE_INTEGRITY_FAILED",
        "PRIMARY_FINDING": note,
    }
    if extra:
        body.update(extra)
    return write_report(
        body,
        decision={
            "CASE": None,
            "VERDICT": "WAIT5_EXECUTION_REBASE_INTEGRITY_FAILED",
            "TARGET_ARCHITECTURE": None,
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
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE WAIT5 EXECUTION-AWARE ENTRY REBASE V1", flush=True)
    print("DEV WAIT=5s. Runtime WAIT=1.0. No model. Old joint diagnostic only.", flush=True)

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
    if preg.get("VERDICT") != PRIOR_VERDICT_REQUIRED:
        print("STOP prior verdict mismatch", preg.get("VERDICT"), flush=True)
        return _integrity("STOP. Prior PASSIVE_WAIT5_PRECOMMIT_SUPPORTED required.")
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
    geom = fillable_geometry(rows)
    surv = survival(rows)
    sess = session_capacity(rows)
    pm_multi = (sess.get("PM") or {}).get("MULTI_FILLABLE_COHORT_RATE")
    ypack = y_fill5_pack(rows)
    parity = freeze_parity(
        geom,
        fill_n=int(ypack.get("Y_FILL5_POS_N") or -1),
        labeled_n=len(lab),
        survival_rate=surv.get("JOINT_LABEL_SURVIVAL_AFTER_FILL_RATE"),
        pm_multi=pm_multi,
    )
    print("parity", parity.get("BASE_PARITY"), parity.get("checks"), flush=True)
    if not parity.get("BASE_PARITY"):
        return _integrity(
            "STOP. Frozen W5 harvest control did not reproduce.",
            extra={"parity": parity, "JOIN_MISS_N": miss},
        )
    print("BASE_PARITY true", flush=True)

    aligned = fill_aligned(rows)
    pareto = postfill_geometry(rows)
    cur_fill = current_fillability(rows)
    cur_q = current_quality(rows)
    delay = delay_effect(rows)
    old_joint = joint_access(rows)
    decision = decide(parity_ok=True, geom=geom, sess=sess, pareto=pareto, cur_fill=cur_fill, cur_q=cur_q)
    am = sess.get("AM") or {}
    pm = sess.get("PM") or {}
    amq = (cur_q.get("by_session") or {}).get("AM") or {}
    pmq = (cur_q.get("by_session") or {}).get("PM") or {}
    required = {
        "BASE_PARITY": True,
        "W5_ANY_RATE": geom.get("FILLABLE_ANY_COHORT_RATE"),
        "W5_MULTI_RATE": geom.get("MULTI_FILLABLE_COHORT_RATE"),
        "W5_THREEPLUS_RATE": threeplus_rate(geom),
        "AM_ANY_RATE": am.get("FILLABLE_ANY_COHORT_RATE"),
        "AM_MULTI_RATE": am.get("MULTI_FILLABLE_COHORT_RATE"),
        "AM_THREEPLUS_RATE": am.get("THREEPLUS_RATE"),
        "PM_ANY_RATE": pm.get("FILLABLE_ANY_COHORT_RATE"),
        "PM_MULTI_RATE": pm.get("MULTI_FILLABLE_COHORT_RATE"),
        "PM_THREEPLUS_RATE": pm.get("THREEPLUS_RATE"),
        "Y_FILL5_POS_N": ypack.get("Y_FILL5_POS_N"),
        "CURRENT_TOP3_FILL5_RATE": cur_fill.get("CURRENT_TOP3_FILL5_RATE"),
        "CURRENT_FILL5_ENRICHMENT": cur_fill.get("CURRENT_FILL5_ENRICHMENT"),
        "CURRENT_SCORE_U_FILL_SPEARMAN": cur_q.get("CURRENT_SCORE_U_FILL_SPEARMAN"),
        "CURRENT_SCORE_D_FILL_SPEARMAN": cur_q.get("CURRENT_SCORE_D_FILL_SPEARMAN"),
        "AM_CURRENT_SCORE_U_FILL_SPEARMAN": amq.get("SPEARMAN_U"),
        "AM_CURRENT_SCORE_D_FILL_SPEARMAN": amq.get("SPEARMAN_D"),
        "PM_CURRENT_SCORE_U_FILL_SPEARMAN": pmq.get("SPEARMAN_U"),
        "PM_CURRENT_SCORE_D_FILL_SPEARMAN": pmq.get("SPEARMAN_D"),
        "FILLABLE_PARETO_SIZE_MEAN": pareto.get("FILLABLE_PARETO_SIZE_MEAN"),
        "MULTI_FILLABLE_WITH_DOMINANCE_RATE": pareto.get("MULTI_FILLABLE_WITH_DOMINANCE_RATE"),
        "THREEPLUS_WITH_DOMINANCE_RATE": pareto.get("THREEPLUS_WITH_DOMINANCE_RATE"),
        "FILL_DELAY_U_SPEARMAN": delay.get("FILL_DELAY_U_SPEARMAN"),
        "FILL_DELAY_D_SPEARMAN": delay.get("FILL_DELAY_D_SPEARMAN"),
        "TARGET_ARCHITECTURE": decision.get("TARGET_ARCHITECTURE"),
        "PRIMARY_MECHANISM": decision.get("PRIMARY_MECHANISM"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "OLD_JOINT_FILLABLE_RATE_DIAGNOSTIC": old_joint.get("FILLABLE_JOINT_AVAILABLE_RATE"),
        "CURRENT_TOP1_FILL5_RATE": cur_fill.get("CURRENT_TOP1_FILL5_RATE"),
        "CURRENT_TOP5_FILL5_RATE": cur_fill.get("CURRENT_TOP5_FILL5_RATE"),
        "ALL_FILL5_RATE": cur_fill.get("ALL_FILL5_RATE"),
        "RUNTIME_WAIT_SEC": WAIT_SEC,
        "DEV_WAIT_SEC": DEV_WAIT_SEC,
        "JOIN_MISS_N": miss,
        "parity": parity,
    }
    print(
        f"CASE={decision.get('CASE')} arch={decision.get('TARGET_ARCHITECTURE')} "
        f"any={geom.get('FILLABLE_ANY_COHORT_RATE')} multi={geom.get('MULTI_FILLABLE_COHORT_RATE')} "
        f"pm_multi={pm.get('MULTI_FILLABLE_COHORT_RATE')}",
        flush=True,
    )
    return write_report(
        required,
        decision=decision,
        extra={
            "geometry": geom,
            "survival": surv,
            "session": sess,
            "y_fill5": {k: v for k, v in ypack.items() if k != "daily"},
            "fill_aligned": aligned,
            "pareto": pareto,
            "current_fill": {k: v for k, v in cur_fill.items() if k != "daily"},
            "current_quality": {k: v for k, v in cur_q.items() if k != "daily"},
            "delay": delay,
            "old_joint_diagnostic": old_joint,
        },
        sheets_extra={
            "Parity": kv_rows(parity),
            "Capacity": kv_rows({**geom, "THREEPLUS_RATE": threeplus_rate(geom)}),
            "Sessions": [sess.get("AM") or {}, sess.get("PM") or {}],
            "YFill5": ypack.get("daily") or [{"empty": True}],
            "FillAligned": [
                {"scope": "overall_U", **(aligned.get("U_FILL") or {})},
                {"scope": "overall_D", **(aligned.get("D_FILL") or {})},
                {"scope": "AM_U", **(((aligned.get("by_session") or {}).get("AM") or {}).get("U_FILL") or {})},
                {"scope": "AM_D", **(((aligned.get("by_session") or {}).get("AM") or {}).get("D_FILL") or {})},
                {"scope": "PM_U", **(((aligned.get("by_session") or {}).get("PM") or {}).get("U_FILL") or {})},
                {"scope": "PM_D", **(((aligned.get("by_session") or {}).get("PM") or {}).get("D_FILL") or {})},
            ],
            "Pareto": kv_rows(pareto),
            "CurrentFill": (cur_fill.get("daily") or [])
            + [
                {"scope": "AM", **((cur_fill.get("by_session") or {}).get("AM") or {})},
                {"scope": "PM", **((cur_fill.get("by_session") or {}).get("PM") or {})},
            ],
            "CurrentQuality": (cur_q.get("daily") or [])
            + [
                {"scope": "AM", **((cur_q.get("by_session") or {}).get("AM") or {})},
                {"scope": "PM", **((cur_q.get("by_session") or {}).get("PM") or {})},
            ],
            "Delay": delay.get("buckets") or [{"empty": True}],
            "Integrity": kv_rows(
                {
                    "JOIN_MISS_N": miss,
                    "RUNTIME_WAIT_SEC": WAIT_SEC,
                    "DEV_WAIT_SEC": DEV_WAIT_SEC,
                    "BASE_PARITY": True,
                    "OLD_JOINT_ADOPTED": OLD_JOINT_ADOPTED,
                    "ENTRY_RETRAIN_STARTED": ENTRY_RETRAIN_STARTED,
                    "WAIT_POLICY_ADOPTED": WAIT_POLICY_ADOPTED,
                }
            ),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
