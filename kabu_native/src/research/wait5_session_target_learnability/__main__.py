"""Offline WAIT5 session target learnability. No Runtime write. No Paper. No Exact."""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.canonical_entry_performance_rebase.analyze import row_key, wf_index
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.direct_joint_objective.oof import attach_joint_labels
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.entry_execution_feasibility.analyze import labeled_rows
from research.wait5_execution_aware_rebase.analyze import attach_w5
from research.wait5_session_architecture_precommit.analyze import sess_rows
from research.wait5_session_target_learnability import (
    ANALYSIS_ID,
    BEST_REPRESENTATION_ADOPTED,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    CLASS_WEIGHT_TUNING,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    DEV_WAIT_SEC,
    EXACT_RAN,
    FEATURE_SEARCH,
    HYPERPARAMETER_TUNING,
    JOINT_BINARY_TARGET,
    MAX_WORKERS,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    PAPER_OPERATED,
    PARETO_TARGET_ADOPTED,
    PNL_USED,
    PRIOR_VERDICT_REQUIRED,
    QUALITY_SCALAR_WEIGHT,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    SESSION_INDICATOR_COMMON_MODEL,
    STRATEGY_CREATED,
    THRESHOLD_SEARCH,
    TOPK_SEARCH,
    TRUE_OOS,
    WAIT_POLICY_ADOPTED,
)
from research.wait5_session_target_learnability.analyze import (
    current_refs,
    decide,
    fill_aggregate,
    fill_spec_rows,
    freeze_parity,
    quality_aggregate,
    quality_spec_rows,
    sum_integrity,
)
from research.wait5_session_target_learnability.oof import (
    process_am_rep,
    process_pm_rep,
    representation_grid,
    slim_row,
)
from research.wait5_session_target_learnability.publish import (
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
PRIOR = NATIVE / "results" / "research" / "wait5_session_architecture_precommit" / "report.json"
ROWS_PATH = NATIVE / "results" / "research" / "_work_cache" / "entry_target_architecture" / "path_rows.json"
WAIT_CACHE = NATIVE / "results" / "research" / "_work_cache" / "passive_wait_policy_reassessment"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "wait5_session_target_learnability"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str), encoding="utf-8")


def _cache_name(session: str, rid: str) -> str:
    return f"{session}_" + rid.replace("|", "_").replace(" ", "") + ".json"


def _pool(fn, jobs: list[dict], label: str) -> list[dict]:
    if not jobs:
        return []
    out = []
    workers = min(MAX_WORKERS, len(jobs))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, job): job.get("representation_id") for job in jobs}
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, "representation_id": key, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            print(
                f"done {label} {body.get('representation_id') or key} ok={body.get('ok')} "
                f"fill={body.get('MODEL_FILL_RATE')} blocker={body.get('blocker')}",
                flush=True,
            )
    return out


def _strip_daily(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        rec = dict(r)
        rec.pop("daily", None)
        rec.pop("daily_delta", None)
        out.append(rec)
    return out


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
                "DEV_WAIT_SEC": DEV_WAIT_SEC,
                "RUNTIME_WAIT_SEC": WAIT_SEC,
                "AM_STAGE1": "RandomForestClassifier P(Y_FILL5=1) Top3",
                "AM_STAGE2": "separate RandomForestRegressor U_FILL and D_FILL",
                "PM_STAGE1": "RandomForestClassifier P(Y_FILL5=1) Top1",
                "REPRESENTATIONS": 9,
                "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
                "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
                "SESSION_INDICATOR_COMMON_MODEL": SESSION_INDICATOR_COMMON_MODEL,
                "BEST_REPRESENTATION_ADOPTED": BEST_REPRESENTATION_ADOPTED,
                "HYPERPARAMETER_TUNING": HYPERPARAMETER_TUNING,
                "CLASS_WEIGHT_TUNING": CLASS_WEIGHT_TUNING,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
                "TOPK_SEARCH": TOPK_SEARCH,
                "QUALITY_SCALAR_WEIGHT": QUALITY_SCALAR_WEIGHT,
                "JOINT_BINARY_TARGET": JOINT_BINARY_TARGET,
                "PARETO_TARGET_ADOPTED": PARETO_TARGET_ADOPTED,
                "FEATURE_SEARCH": FEATURE_SEARCH,
                "PNL_USED": PNL_USED,
                "EXACT_RAN": EXACT_RAN,
            }
        ),
        "Parity": [{"empty": True}],
        "AMFillability": [{"empty": True}],
        "AMU": [{"empty": True}],
        "AMD": [{"empty": True}],
        "PMFillability": [{"empty": True}],
        "Representations": [{"empty": True}],
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
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "FEATURE_SEARCH": FEATURE_SEARCH,
                "PNL_USED": PNL_USED,
                "BEST_REPRESENTATION_ADOPTED": BEST_REPRESENTATION_ADOPTED,
                "HYPERPARAMETER_TUNING": HYPERPARAMETER_TUNING,
                "CLASS_WEIGHT_TUNING": CLASS_WEIGHT_TUNING,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
                "TOPK_SEARCH": TOPK_SEARCH,
                "QUALITY_SCALAR_WEIGHT": QUALITY_SCALAR_WEIGHT,
                "JOINT_BINARY_TARGET": JOINT_BINARY_TARGET,
                "PARETO_TARGET_ADOPTED": PARETO_TARGET_ADOPTED,
                "SESSION_INDICATOR_COMMON_MODEL": SESSION_INDICATOR_COMMON_MODEL,
                "RUNTIME_CANDIDATE_CREATED": RUNTIME_CANDIDATE_CREATED,
                "STRATEGY_CREATED": STRATEGY_CREATED,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"OVERALL_VERDICT={required.get('OVERALL_VERDICT')}", flush=True)
    print(f"AM_VERDICT={required.get('AM_VERDICT')} PM_VERDICT={required.get('PM_VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(
        "STOP. No next model. No AM/PM commonization. W5 not adopted. "
        "Runtime WAIT_SEC=1.0. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("OVERALL_VERDICT") == "WAIT5_SESSION_TARGET_LEARNABILITY_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str, extra: dict | None = None) -> int:
    body = {
        "BASE_PARITY": False,
        "REPRESENTATION_N": 9,
        "AM_FILLABILITY_TOP3_DELTA": None,
        "AM_FILLABILITY_POSITIVE_REP_N": None,
        "AM_FILLABILITY_LEARNABLE": None,
        "AM_U_FILL_SPEARMAN": None,
        "AM_U_POSITIVE_REP_N": None,
        "AM_U_FILL_LEARNABLE": None,
        "AM_D_FILL_SPEARMAN": None,
        "AM_D_POSITIVE_REP_N": None,
        "AM_D_FILL_LEARNABLE": None,
        "AM_TARGET_STATUS": None,
        "AM_VERDICT": "WAIT5_SESSION_TARGET_LEARNABILITY_INTEGRITY_FAILED",
        "PM_FILLABILITY_TOP1_DELTA": None,
        "PM_FILLABILITY_POSITIVE_REP_N": None,
        "PM_FILLABILITY_LEARNABLE": None,
        "PM_TARGET_STATUS": None,
        "PM_VERDICT": "WAIT5_SESSION_TARGET_LEARNABILITY_INTEGRITY_FAILED",
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
        "AM_TRAIN_PM_ROW_N": None,
        "PM_TRAIN_AM_ROW_N": None,
        "FUTURE_EVENT_USE_N": None,
        "TARGET_CONTAMINATION_N": None,
        "HELDOUT_FIT_LEAK_N": None,
        "AM_NEXT": "NONE",
        "PM_NEXT": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "OVERALL_VERDICT": "WAIT5_SESSION_TARGET_LEARNABILITY_INTEGRITY_FAILED",
        "PRIMARY_FINDING": note,
    }
    if extra:
        body.update(extra)
    return write_report(
        body,
        decision={
            "AM_CASE": None,
            "PM_CASE": None,
            "AM_VERDICT": "WAIT5_SESSION_TARGET_LEARNABILITY_INTEGRITY_FAILED",
            "PM_VERDICT": "WAIT5_SESSION_TARGET_LEARNABILITY_INTEGRITY_FAILED",
            "OVERALL_VERDICT": "WAIT5_SESSION_TARGET_LEARNABILITY_INTEGRITY_FAILED",
            "AM_NEXT": "NONE",
            "PM_NEXT": "NONE",
            "PRIMARY_FINDING": note,
            "note": "STOP. Integrity failed.",
        },
    )


def _run_session(session: str, fn, grid: list[dict], rows_path: Path) -> list[dict]:
    jobs = []
    got = []
    for spec in grid:
        rid = str(spec.get("representation_id"))
        fp = CACHE / _cache_name(session, rid)
        saved = _load(fp)
        if saved.get("ok") and saved.get("representation_id") == rid and saved.get("session") == session:
            got.append(saved)
            print(f"{session} cache-hit {rid}", flush=True)
            continue
        jobs.append(
            {
                "spec": spec,
                "representation_id": rid,
                "rows_path": str(rows_path),
                "days": list(ELIGIBLE_DAYS),
            }
        )
    print(f"{session} jobs={len(jobs)}", flush=True)
    for body in _pool(fn, jobs, session):
        if body.get("ok"):
            _save_json(CACHE / _cache_name(session, str(body.get("representation_id"))), body)
        got.append(body)
    return got


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE WAIT5 SESSION TARGET LEARNABILITY V1", flush=True)
    print("AM fillability+U+D. PM fillability only. 9-rep median/consensus. No strategy.", flush=True)

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
        return _integrity("STOP. Prior WAIT5_SESSION_SPLIT_ARCHITECTURE_PRECOMMITTED required.")
    grid = representation_grid()
    if len(grid) != 9:
        print("STOP representation grid is not 9", len(grid), flush=True)
        return _integrity("STOP. Representation grid is not 9.")
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
    refs = current_refs(rows)
    parity = freeze_parity(
        am=refs["am"],
        pm=refs["pm"],
        am_top3=refs["am_top3"],
        pm_top1=refs["pm_top1_pack"],
    )
    print("parity", parity.get("BASE_PARITY"), parity.get("checks"), flush=True)
    if not parity.get("BASE_PARITY"):
        return _integrity(
            "STOP. Frozen AM/PM W5 labeled population or CURRENT admission rates did not reproduce.",
            extra={"parity": parity, "JOIN_MISS_N": miss},
        )
    print("BASE_PARITY true", flush=True)

    CACHE.mkdir(parents=True, exist_ok=True)
    am_path = CACHE / "am_rows.json"
    pm_path = CACHE / "pm_rows.json"
    _save_json(am_path, {"session": "AM", "rows": [slim_row(r) for r in sess_rows(rows, "AM")]})
    _save_json(pm_path, {"session": "PM", "rows": [slim_row(r) for r in sess_rows(rows, "PM")]})

    am_got = _run_session("AM", process_am_rep, grid, am_path)
    pm_got = _run_session("PM", process_pm_rep, grid, pm_path)
    am_fail = [b for b in am_got if not b.get("ok")]
    pm_fail = [b for b in pm_got if not b.get("ok")]
    if am_fail or len(am_got) != 9:
        print("STOP AM LODO failed", [(b.get("representation_id"), b.get("blocker")) for b in am_fail], flush=True)
        return _integrity("STOP. AM representation LODO failed.")
    if pm_fail or len(pm_got) != 9:
        print("STOP PM LODO failed", [(b.get("representation_id"), b.get("blocker")) for b in pm_fail], flush=True)
        return _integrity("STOP. PM representation LODO failed.")

    leak = sum_integrity(am_got + pm_got)
    leak["FUTURE_EVENT_USE_N"] = sum(1 for r in lab if r.get("future_event_use"))
    print("integrity", leak, flush=True)
    if any(int(leak.get(k) or 0) != 0 for k in leak):
        return _integrity("STOP. Session isolation or contamination integrity failed.", extra={"integrity": leak})

    am_cur_daily = {str(r["date"]): float(r["FILL_RATE"]) for r in (refs["am_top3"].get("daily") or []) if r.get("FILL_RATE") is not None}
    pm_cur_daily = {str(r["date"]): float(r["FILL_RATE"]) for r in (refs["pm_top1"].get("daily") or []) if r.get("FILL_RATE") is not None}
    am_fill_specs = fill_spec_rows(
        am_got,
        current_rate=refs["am_top3"].get("FILL_RATE"),
        current_daily=am_cur_daily,
        k_label="TOP3",
    )
    pm_fill_specs = fill_spec_rows(
        pm_got,
        current_rate=refs["pm_top1_pack"].get("PM_CURRENT_TOP1_FILL_RATE"),
        current_daily=pm_cur_daily,
        k_label="TOP1",
    )
    am_fill = fill_aggregate(am_fill_specs, k_label="TOP3")
    pm_fill = fill_aggregate(pm_fill_specs, k_label="TOP1")
    am_u = quality_aggregate(quality_spec_rows(am_got, which="U"), current=refs["am_u"])
    am_d = quality_aggregate(quality_spec_rows(am_got, which="D"), current=refs["am_d"])
    decision = decide(am_fill=am_fill, am_u=am_u, am_d=am_d, pm_fill=pm_fill)

    required = {
        "BASE_PARITY": True,
        "REPRESENTATION_N": 9,
        "AM_FILLABILITY_TOP3_DELTA": am_fill.get("MEDIAN_DELTA"),
        "AM_FILLABILITY_POSITIVE_REP_N": am_fill.get("POSITIVE_REP_N"),
        "AM_FILLABILITY_LEARNABLE": am_fill.get("LEARNABLE"),
        "AM_U_FILL_SPEARMAN": am_u.get("MEDIAN_SPEARMAN"),
        "AM_U_POSITIVE_REP_N": am_u.get("POSITIVE_REP_N"),
        "AM_U_FILL_LEARNABLE": am_u.get("LEARNABLE"),
        "AM_D_FILL_SPEARMAN": am_d.get("MEDIAN_SPEARMAN"),
        "AM_D_POSITIVE_REP_N": am_d.get("POSITIVE_REP_N"),
        "AM_D_FILL_LEARNABLE": am_d.get("LEARNABLE"),
        "AM_TARGET_STATUS": decision.get("AM_TARGET_STATUS"),
        "AM_VERDICT": decision.get("AM_VERDICT"),
        "PM_FILLABILITY_TOP1_DELTA": pm_fill.get("MEDIAN_DELTA"),
        "PM_FILLABILITY_POSITIVE_REP_N": pm_fill.get("POSITIVE_REP_N"),
        "PM_FILLABILITY_LEARNABLE": pm_fill.get("LEARNABLE"),
        "PM_TARGET_STATUS": decision.get("PM_TARGET_STATUS"),
        "PM_VERDICT": decision.get("PM_VERDICT"),
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
        "AM_TRAIN_PM_ROW_N": leak.get("AM_TRAIN_PM_ROW_N"),
        "PM_TRAIN_AM_ROW_N": leak.get("PM_TRAIN_AM_ROW_N"),
        "AM_NORMALIZER_PM_ROW_N": leak.get("AM_NORMALIZER_PM_ROW_N"),
        "PM_NORMALIZER_AM_ROW_N": leak.get("PM_NORMALIZER_AM_ROW_N"),
        "FUTURE_EVENT_USE_N": leak.get("FUTURE_EVENT_USE_N"),
        "TARGET_CONTAMINATION_N": leak.get("TARGET_CONTAMINATION_N"),
        "HELDOUT_FIT_LEAK_N": leak.get("HELDOUT_FIT_LEAK_N"),
        "AM_NEXT": decision.get("AM_NEXT"),
        "PM_NEXT": decision.get("PM_NEXT"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "OVERALL_VERDICT": decision.get("OVERALL_VERDICT"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "JOIN_MISS_N": miss,
    }
    print(
        f"AM fill={am_fill.get('LEARNABLE')} U={am_u.get('LEARNABLE')} D={am_d.get('LEARNABLE')} "
        f"PM fill={pm_fill.get('LEARNABLE')} OVERALL={decision.get('OVERALL_VERDICT')}",
        flush=True,
    )
    return write_report(
        required,
        decision=decision,
        extra={
            "parity": parity,
            "am_fillability": {k: v for k, v in am_fill.items() if k != "spec_rows"},
            "am_u": {k: v for k, v in am_u.items() if k != "spec_rows"},
            "am_d": {k: v for k, v in am_d.items() if k != "spec_rows"},
            "pm_fillability": {k: v for k, v in pm_fill.items() if k != "spec_rows"},
            "current": {
                "AM_CURRENT_TOP3_FILL5_RATE": refs["am_top3"].get("FILL_RATE"),
                "PM_CURRENT_TOP1_FILL_RATE": refs["pm_top1_pack"].get("PM_CURRENT_TOP1_FILL_RATE"),
                "AM_CURRENT_U_SPEARMAN": refs["am_u"].get("OVERALL_SPEARMAN"),
                "AM_CURRENT_D_SPEARMAN": refs["am_d"].get("OVERALL_SPEARMAN"),
                "AM_CURRENT_U_TOP1_UPLIFT": refs["am_u"].get("TOP1_UPLIFT"),
                "AM_CURRENT_D_TOP1_UPLIFT": refs["am_d"].get("TOP1_UPLIFT"),
            },
            "integrity": leak,
        },
        sheets_extra={
            "Parity": kv_rows(parity),
            "AMFillability": kv_rows({k: v for k, v in am_fill.items() if k not in {"spec_rows", "daily", "gates"}})
            + _strip_daily(am_fill_specs),
            "AMU": kv_rows({k: v for k, v in am_u.items() if k not in {"spec_rows", "daily", "gates"}})
            + _strip_daily(am_u.get("spec_rows") or []),
            "AMD": kv_rows({k: v for k, v in am_d.items() if k not in {"spec_rows", "daily", "gates"}})
            + _strip_daily(am_d.get("spec_rows") or []),
            "PMFillability": kv_rows({k: v for k, v in pm_fill.items() if k not in {"spec_rows", "daily", "gates"}})
            + _strip_daily(pm_fill_specs),
            "Representations": [{"session": "AM", **r} for r in _strip_daily(am_fill_specs)]
            + [{"session": "PM", **r} for r in _strip_daily(pm_fill_specs)],
            "ConsensusDays": [{"scope": "AM_FILL", **r} for r in (am_fill.get("daily") or [])]
            + [{"scope": "AM_U", **r} for r in (am_u.get("daily") or [])]
            + [{"scope": "AM_D", **r} for r in (am_d.get("daily") or [])]
            + [{"scope": "PM_FILL", **r} for r in (pm_fill.get("daily") or [])],
            "Gates": kv_rows(
                {
                    **{f"AM_FILL_{k}": v for k, v in (am_fill.get("gates") or {}).items()},
                    **{f"AM_U_{k}": v for k, v in (am_u.get("gates") or {}).items()},
                    **{f"AM_D_{k}": v for k, v in (am_d.get("gates") or {}).items()},
                    **{f"PM_FILL_{k}": v for k, v in (pm_fill.get("gates") or {}).items()},
                }
            ),
            "Integrity": kv_rows(
                {
                    **leak,
                    "AM_NORMALIZER_PM_ROW_N": leak.get("AM_NORMALIZER_PM_ROW_N"),
                    "PM_NORMALIZER_AM_ROW_N": leak.get("PM_NORMALIZER_AM_ROW_N"),
                    "JOIN_MISS_N": miss,
                    "RUNTIME_WAIT_SEC": WAIT_SEC,
                    "DEV_WAIT_SEC": DEV_WAIT_SEC,
                    "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
                    "SESSION_INDICATOR_COMMON_MODEL": SESSION_INDICATOR_COMMON_MODEL,
                }
            ),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
