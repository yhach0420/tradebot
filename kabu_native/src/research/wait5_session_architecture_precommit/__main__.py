"""Offline WAIT5 session architecture precommit. No Runtime write. No Paper. No Exact."""
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
from research.wait5_execution_aware_rebase.analyze import attach_w5, current_fillability, session_capacity
from research.wait5_session_architecture_precommit import (
    AM_NEXT_TASK,
    ANALYSIS_ID,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    CURRENT_SCORE_ROLE,
    DEV_WAIT_SEC,
    ELIGIBLE_DAYS,
    ENTRY_RETRAIN_STARTED,
    EXACT_RAN,
    FEATURE_SEARCH,
    FILL_RULE_CHANGED,
    JOINT_BINARY_TARGET,
    LIMIT_CHANGED,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    PAPER_OPERATED,
    PARETO_TARGET_ADOPTED,
    PNL_USED,
    PM_NEXT_TASK,
    PRIOR_VERDICT_REQUIRED,
    QUALITY_SCALAR_WEIGHT,
    RATE_MIN,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    SESSION_INDICATOR_COMMON_MODEL,
    TOPK_SEARCH,
    TRUE_OOS,
    WAIT_POLICY_ADOPTED,
)
from research.wait5_session_architecture_precommit.analyze import (
    capacity_flags,
    decide,
    freeze_parity,
    pm_top1_fill,
    postfill_geometry,
    sess_rows,
    y_support,
)
from research.wait5_session_architecture_precommit.publish import (
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
PRIOR = NATIVE / "results" / "research" / "wait5_execution_aware_rebase" / "report.json"
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
                "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
                "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
                "SESSION_INDICATOR_COMMON_MODEL": SESSION_INDICATOR_COMMON_MODEL,
                "QUALITY_SCALAR_WEIGHT": QUALITY_SCALAR_WEIGHT,
                "JOINT_BINARY_TARGET": JOINT_BINARY_TARGET,
                "PARETO_TARGET_ADOPTED": PARETO_TARGET_ADOPTED,
                "ENTRY_RETRAIN_STARTED": ENTRY_RETRAIN_STARTED,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "FEATURE_SEARCH": FEATURE_SEARCH,
                "PNL_USED": PNL_USED,
                "EXACT_RAN": EXACT_RAN,
                "TOPK_SEARCH": TOPK_SEARCH,
            }
        ),
        "Parity": [{"empty": True}],
        "Capacity": [{"empty": True}],
        "Support": [{"empty": True}],
        "AMQuality": [{"empty": True}],
        "PMAdmission": [{"empty": True}],
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
                "SESSION_INDICATOR_COMMON_MODEL": SESSION_INDICATOR_COMMON_MODEL,
                "QUALITY_SCALAR_WEIGHT": QUALITY_SCALAR_WEIGHT,
                "JOINT_BINARY_TARGET": JOINT_BINARY_TARGET,
                "PARETO_TARGET_ADOPTED": PARETO_TARGET_ADOPTED,
                "TOPK_SEARCH": TOPK_SEARCH,
                "FILL_RULE_CHANGED": FILL_RULE_CHANGED,
                "LIMIT_CHANGED": LIMIT_CHANGED,
                "RUNTIME_CANDIDATE_CREATED": RUNTIME_CANDIDATE_CREATED,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"VERDICT={required.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(
        "STOP. No ENTRY learning. Runtime WAIT_SEC=1.0. W5 not adopted. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "WAIT5_SESSION_ARCHITECTURE_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str, extra: dict | None = None) -> int:
    body = {
        "BASE_PARITY": False,
        "DEV_WAIT_SEC": DEV_WAIT_SEC,
        "RUNTIME_WAIT_SEC": WAIT_SEC,
        "AM_FILLABILITY_CAPABLE": None,
        "AM_RANKING_CAPABLE": None,
        "AM_FULL_TOP3_CAPABLE": None,
        "PM_FILLABILITY_CAPABLE": None,
        "PM_RANKING_CAPABLE": None,
        "PM_FULL_TOP3_CAPABLE": None,
        "AM_ARCHITECTURE": None,
        "PM_ARCHITECTURE": None,
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
        "AM_Y_FILL5_POS_N": None,
        "PM_Y_FILL5_POS_N": None,
        "AM_MULTI_WITH_DOMINANCE_RATE": None,
        "AM_THREEPLUS_WITH_DOMINANCE_RATE": None,
        "PM_CURRENT_TOP1_FILL_RATE": None,
        "CURRENT_SCORE_ROLE": CURRENT_SCORE_ROLE,
        "AM_NEXT_TASK": AM_NEXT_TASK,
        "PM_NEXT_TASK": PM_NEXT_TASK,
        "PRIMARY_FINDING": note,
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "WAIT5_SESSION_ARCHITECTURE_INTEGRITY_FAILED",
    }
    if extra:
        body.update(extra)
    return write_report(
        body,
        decision={
            "CASE": "C",
            "VERDICT": "WAIT5_SESSION_ARCHITECTURE_INTEGRITY_FAILED",
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
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE WAIT5 SESSION ARCHITECTURE PRECOMMIT V1", flush=True)
    print("DEV WAIT=5s. Runtime WAIT=1.0. No model. No common AM/PM target.", flush=True)

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
        return _integrity("STOP. Prior WAIT5_SELECTION_CAPACITY_SESSION_ASYMMETRIC required.")
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
    sess = session_capacity(rows)
    cur = current_fillability(rows)
    parity = freeze_parity(sess=sess, cur=cur)
    print("parity", parity.get("BASE_PARITY"), parity.get("checks"), flush=True)
    if not parity.get("BASE_PARITY"):
        return _integrity(
            "STOP. Frozen AM/PM W5 capacity or CURRENT fill enrichment did not reproduce.",
            extra={"parity": parity, "JOIN_MISS_N": miss},
        )
    print("BASE_PARITY true", flush=True)

    am = sess.get("AM") or {}
    pm = sess.get("PM") or {}
    am_flags = capacity_flags(am)
    pm_flags = capacity_flags(pm)
    am_geom = postfill_geometry(sess_rows(rows, "AM"))
    am_dom_ok = bool(
        am_geom.get("MULTI_FILLABLE_WITH_DOMINANCE_RATE") is not None
        and float(am_geom["MULTI_FILLABLE_WITH_DOMINANCE_RATE"]) + 1e-12 >= float(RATE_MIN)
    )
    am_y = y_support(sess_rows(rows, "AM"))
    pm_y = y_support(sess_rows(rows, "PM"))
    am_cond = y_support(sess_rows(rows, "AM"), filled_only=True)
    pm_adm = pm_top1_fill(rows)
    decision = decide(parity_ok=True, am_flags=am_flags, pm_flags=pm_flags, am_dom_ok=am_dom_ok)

    required = {
        "BASE_PARITY": True,
        "DEV_WAIT_SEC": DEV_WAIT_SEC,
        "RUNTIME_WAIT_SEC": WAIT_SEC,
        "AM_FILLABILITY_CAPABLE": am_flags.get("FILLABILITY_CAPABLE"),
        "AM_RANKING_CAPABLE": am_flags.get("RANKING_CAPABLE"),
        "AM_FULL_TOP3_CAPABLE": am_flags.get("FULL_TOP3_CAPABLE"),
        "PM_FILLABILITY_CAPABLE": pm_flags.get("FILLABILITY_CAPABLE"),
        "PM_RANKING_CAPABLE": pm_flags.get("RANKING_CAPABLE"),
        "PM_FULL_TOP3_CAPABLE": pm_flags.get("FULL_TOP3_CAPABLE"),
        "AM_ARCHITECTURE": decision.get("AM_ARCHITECTURE"),
        "PM_ARCHITECTURE": decision.get("PM_ARCHITECTURE"),
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
        "AM_Y_FILL5_POS_N": am.get("Y_FILL5_POS_N"),
        "PM_Y_FILL5_POS_N": pm.get("Y_FILL5_POS_N"),
        "AM_MULTI_WITH_DOMINANCE_RATE": am_geom.get("MULTI_FILLABLE_WITH_DOMINANCE_RATE"),
        "AM_THREEPLUS_WITH_DOMINANCE_RATE": am_geom.get("THREEPLUS_WITH_DOMINANCE_RATE"),
        "PM_CURRENT_TOP1_FILL_RATE": pm_adm.get("PM_CURRENT_TOP1_FILL_RATE"),
        "CURRENT_SCORE_ROLE": CURRENT_SCORE_ROLE,
        "AM_NEXT_TASK": AM_NEXT_TASK,
        "PM_NEXT_TASK": PM_NEXT_TASK,
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "JOIN_MISS_N": miss,
        "parity": parity,
    }
    print(
        f"CASE={decision.get('CASE')} AM={decision.get('AM_ARCHITECTURE')} "
        f"PM={decision.get('PM_ARCHITECTURE')} am_dom={am_geom.get('MULTI_FILLABLE_WITH_DOMINANCE_RATE')}",
        flush=True,
    )
    return write_report(
        required,
        decision=decision,
        extra={
            "session": sess,
            "am_flags": am_flags,
            "pm_flags": pm_flags,
            "am_quality": am_geom,
            "am_y_fill5": {k: v for k, v in am_y.items() if k != "daily"},
            "pm_y_fill5": {k: v for k, v in pm_y.items() if k != "daily"},
            "am_stage2_support": {k: v for k, v in am_cond.items() if k != "daily"},
            "pm_admission": {
                **pm_adm,
                "PM_FILLABLE_0_COHORT_N": pm.get("FILLABLE_0_COHORT_N"),
                "PM_FILLABLE_1_COHORT_N": pm.get("FILLABLE_1_COHORT_N"),
                "PM_FILLABLE_2PLUS_COHORT_N": int(pm.get("FILLABLE_2_COHORT_N") or 0)
                + int(pm.get("FILLABLE_3PLUS_COHORT_N") or 0),
            },
            "current_fill": {k: v for k, v in cur.items() if k != "daily"},
        },
        sheets_extra={
            "Parity": kv_rows(parity),
            "Capacity": [
                {"session": "AM", **am, **am_flags},
                {"session": "PM", **pm, **pm_flags},
            ],
            "Support": [
                {"scope": "AM_Y_FILL5", **{k: v for k, v in am_y.items() if k != "daily"}},
                {"scope": "PM_Y_FILL5", **{k: v for k, v in pm_y.items() if k != "daily"}},
                {"scope": "AM_STAGE2_CONDITIONAL", **{k: v for k, v in am_cond.items() if k != "daily"}},
            ]
            + [{"scope": "AM_DAY", **r} for r in (am_y.get("daily") or [])]
            + [{"scope": "PM_DAY", **r} for r in (pm_y.get("daily") or [])],
            "AMQuality": kv_rows(am_geom),
            "PMAdmission": kv_rows(
                {
                    **pm_adm,
                    "PM_FILLABLE_0_COHORT_N": pm.get("FILLABLE_0_COHORT_N"),
                    "PM_FILLABLE_1_COHORT_N": pm.get("FILLABLE_1_COHORT_N"),
                    "PM_FILLABLE_2_COHORT_N": pm.get("FILLABLE_2_COHORT_N"),
                    "PM_FILLABLE_3PLUS_COHORT_N": pm.get("FILLABLE_3PLUS_COHORT_N"),
                    "PM_FILLABLE_2PLUS_COHORT_N": int(pm.get("FILLABLE_2_COHORT_N") or 0)
                    + int(pm.get("FILLABLE_3PLUS_COHORT_N") or 0),
                }
            ),
            "Integrity": kv_rows(
                {
                    "JOIN_MISS_N": miss,
                    "RUNTIME_WAIT_SEC": WAIT_SEC,
                    "DEV_WAIT_SEC": DEV_WAIT_SEC,
                    "BASE_PARITY": True,
                    "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
                    "SESSION_INDICATOR_COMMON_MODEL": SESSION_INDICATOR_COMMON_MODEL,
                    "ENTRY_RETRAIN_STARTED": ENTRY_RETRAIN_STARTED,
                    "WAIT_POLICY_ADOPTED": WAIT_POLICY_ADOPTED,
                }
            ),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
