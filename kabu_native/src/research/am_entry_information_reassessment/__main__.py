"""Offline AM ENTRY information reassessment. Frozen OOF. No Runtime write. No Paper."""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_entry_information_reassessment import (
    ANALYSIS_ID,
    BEST_REPRESENTATION_ADOPTED,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    CANONICAL_FEATURES,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    D_TRAINING_TARGET_ALLOWED,
    DEV_WAIT_SEC,
    ELIGIBLE_DAYS,
    EXACT_RAN,
    FEATURE_SEARCH,
    FEATURE_SEARCH_N,
    FINAL_MODEL_ADOPTED,
    FROZEN_OOF_REPLAY_ONLY,
    HYPERPARAMETER_TUNING,
    MAX_WORKERS,
    MODEL_REFIT_N,
    NEW_FEATURE_N,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    NEW_TARGET_CREATED,
    P_FILL_TIMES_U_PRED,
    PAPER_OPERATED,
    PARITY_ABS_TOL,
    PARETO_AS_STRATEGY,
    PNL_USED,
    POLICY_FROM_ACTUAL_N,
    POLICY_FROM_PARETO_N,
    PRIOR_ANALYSIS_ID,
    PRIOR_VERDICT,
    REPRESENTATION_N,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    SELECTION_REOPTIMIZATION_N,
    SESSION,
    STAGE1_ALLOWED,
    STAGE2_ALLOWED,
    STRATEGY_CREATED,
    THRESHOLD_SEARCH,
    TOPK_SEARCH,
    TRUE_OOS,
    WAIT_POLICY_ADOPTED,
    WAIT_SEARCH_N,
    W5_RUNTIME_ADOPTED,
    W10_ADOPTED,
)
from research.am_entry_information_reassessment.analyze import (
    decide,
    feature_consensus,
    freeze_parity,
    primary_failure,
    process_information,
    residual_consensus,
    spec_rows,
)
from research.am_entry_information_reassessment.publish import (
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
PRIOR = NATIVE / "results" / "research" / "am_constrained_execution_architecture_reassessment" / "report.json"
DIRECT_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_direct_exec_u_development"
OOF_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_wait5_stage2_reassessment"
GEO_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_constrained_execution_architecture_reassessment"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_entry_information_reassessment"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str), encoding="utf-8")


def _cache_name(rid: str) -> str:
    return "AM_" + rid.replace("|", "_").replace(" ", "") + ".json"


def _scored_name(rid: str) -> str:
    return "AM_" + rid.replace("|", "_").replace(" ", "") + "_scored.json"


def _pool(jobs: list[dict]) -> list[dict]:
    if not jobs:
        return []
    out = []
    workers = min(MAX_WORKERS, len(jobs))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(process_information, job): job.get("representation_id") for job in jobs}
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, "representation_id": key, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            info = body.get("info") or {}
            print(
                f"done INFO {body.get('representation_id') or key} ok={body.get('ok')} "
                f"off={info.get('OFF_FRONTIER_FUD_GOOD_COHORT_N')} fd={info.get('FALSE_DOMINATOR_N')} "
                f"blocker={body.get('blocker')}",
                flush=True,
            )
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
                "SESSION": SESSION,
                "DEV_WAIT_SEC": DEV_WAIT_SEC,
                "RUNTIME_WAIT_SEC": WAIT_SEC,
                "CANONICAL_FEATURES": list(CANONICAL_FEATURES),
                "FROZEN_OOF_REPLAY_ONLY": FROZEN_OOF_REPLAY_ONLY,
                "NEW_FEATURE_N": NEW_FEATURE_N,
                "FEATURE_SEARCH_N": FEATURE_SEARCH_N,
                "POLICY_FROM_ACTUAL_N": POLICY_FROM_ACTUAL_N,
                "PNL_USED": PNL_USED,
                "EXACT_RAN": EXACT_RAN,
            }
        ),
        "Parity": [{"empty": True}],
        "FalseDominators": [{"empty": True}],
        "FailureModes": [{"empty": True}],
        "PairedDeltas": [{"empty": True}],
        "Swap": [{"empty": True}],
        "Ranks": [{"empty": True}],
        "HighHigh": [{"empty": True}],
        "Features": [{"empty": True}],
        "Residuals": [{"empty": True}],
        "Representations": [{"empty": True}],
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
                "W10_ADOPTED": W10_ADOPTED,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "NEW_TARGET_CREATED": NEW_TARGET_CREATED,
                "FEATURE_SEARCH": FEATURE_SEARCH,
                "PNL_USED": PNL_USED,
                "BEST_REPRESENTATION_ADOPTED": BEST_REPRESENTATION_ADOPTED,
                "HYPERPARAMETER_TUNING": HYPERPARAMETER_TUNING,
                "TOPK_SEARCH": TOPK_SEARCH,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
                "P_FILL_TIMES_U_PRED": P_FILL_TIMES_U_PRED,
                "D_TRAINING_TARGET_ALLOWED": D_TRAINING_TARGET_ALLOWED,
                "PARETO_AS_STRATEGY": PARETO_AS_STRATEGY,
                "POLICY_FROM_ACTUAL_N": POLICY_FROM_ACTUAL_N,
                "RUNTIME_CANDIDATE_CREATED": RUNTIME_CANDIDATE_CREATED,
                "STRATEGY_CREATED": STRATEGY_CREATED,
                "FINAL_MODEL_ADOPTED": FINAL_MODEL_ADOPTED,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"VERDICT={required.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(
        "STOP. No model. No new feature. No new target. No Exact. No PnL. "
        "Runtime WAIT_SEC=1.0. W5 not adopted. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "AM_ENTRY_INFORMATION_REASSESSMENT_INTEGRITY_FAILED"
    return 2 if fail else 0


def _empty_required(note: str, extra: dict | None = None) -> dict:
    body = {
        "BASE_PARITY": False,
        "OFF_FRONTIER_FUD_GOOD_COHORT_N": None,
        "FALSE_DOMINATOR_N": None,
        "FALSE_DOMINATOR_COHORT_N": None,
        "FD_FILL_FAIL_RATE": None,
        "FD_U_FAIL_RATE": None,
        "FD_D_FAIL_RATE": None,
        "PRIMARY_FALSE_DOMINATOR_FAILURE": None,
        "GOOD_ONLY_FILL_RATE": None,
        "DOMINATOR_ONLY_FILL_RATE": None,
        "GOOD_ONLY_COND_U": None,
        "DOMINATOR_ONLY_COND_U": None,
        "GOOD_ONLY_COND_D": None,
        "DOMINATOR_ONLY_COND_D": None,
        "GOOD_ONLY_P_FILL_MEDIAN": None,
        "DOMINATOR_ONLY_P_FILL_MEDIAN": None,
        "GOOD_ONLY_PRED_EXEC_U_MEDIAN": None,
        "DOMINATOR_ONLY_PRED_EXEC_U_MEDIAN": None,
        "EXISTING_FEATURE_DIFFERENCE_SUPPORTED": False,
        "FILL_RESIDUAL_INFORMATION_SUPPORTED": False,
        "EXEC_U_RESIDUAL_INFORMATION_SUPPORTED": False,
        "PRIMARY_MECHANISM": "INTEGRITY_FAIL",
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "AM_ENTRY_INFORMATION_REASSESSMENT_INTEGRITY_FAILED",
        "PRIMARY_FINDING": note,
        "MODEL_REFIT_N": MODEL_REFIT_N,
        "FEATURE_SEARCH_N": FEATURE_SEARCH_N,
        "NEW_FEATURE_N": NEW_FEATURE_N,
        "PM_ROWS_USED_N": None,
        "POLICY_FROM_ACTUAL_N": POLICY_FROM_ACTUAL_N,
        "JOIN_MISS_N": None,
    }
    if extra:
        body.update(extra)
    return body


def _integrity(note: str, extra: dict | None = None) -> int:
    return write_report(
        _empty_required(note, extra),
        decision={
            "CASE": "F",
            "PRIMARY_MECHANISM": "INTEGRITY_FAIL",
            "NEXT_RESEARCH": "NONE",
            "VERDICT": "AM_ENTRY_INFORMATION_REASSESSMENT_INTEGRITY_FAILED",
            "PRIMARY_FINDING": note,
            "note": "CASE F. STOP. Integrity failed.",
            "EXISTING_FEATURE_DIFFERENCE_SUPPORTED": False,
            "FILL_RESIDUAL_INFORMATION_SUPPORTED": False,
            "EXEC_U_RESIDUAL_INFORMATION_SUPPORTED": False,
        },
        extra=extra,
    )


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM ENTRY INFORMATION REASSESSMENT V1", flush=True)
    print("Frozen P_FILL5 + pred_EXEC_U. False-dominator diagnostic. No new model.", flush=True)

    if abs(float(WAIT_SEC) - 1.0) > 1e-12 or abs(float(DEV_WAIT_SEC) - 5.0) > 1e-12:
        print("STOP WAIT_SEC drift", flush=True)
        return 2
    if STAGE1_ALLOWED or STAGE2_ALLOWED or NEW_TARGET_CREATED or NEW_MODEL_CREATED or FEATURE_SEARCH:
        print("STOP forbidden flags", flush=True)
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
    prior_obs = {k: preg.get(k) for k in (
        "ANY_FU_GOOD_RATE",
        "ANY_FUD_GOOD_RATE",
        "FRONTIER_FU_GOOD_RATE",
        "FRONTIER_FUD_GOOD_RATE",
        "FUD_GOOD_FILL_PERCENTILE_MEDIAN",
        "FUD_GOOD_EXECU_PERCENTILE_MEDIAN",
        "PRED_FILL_EXECU_SPEARMAN_MEDIAN",
        "FILL_ONLY_ON_FRONTIER_RATE",
        "DIRECT_ON_FRONTIER_RATE",
    )}
    prior_pack = freeze_parity(prior_obs)
    print("prior parity", prior_pack.get("ok"), prior_pack.get("checks"), flush=True)
    if not prior_pack.get("ok"):
        return _integrity("STOP. Frozen constrained-geometry parity did not reproduce.", extra={"parity": prior_pack})

    grid = representation_grid()
    if len(grid) != 9:
        return _integrity("STOP. Representation grid is not 9.")
    rows_path = DIRECT_CACHE / "am_rows.json"
    if not rows_path.is_file():
        return _integrity("STOP. Frozen AM DIRECT rows cache missing.")

    CACHE.mkdir(parents=True, exist_ok=True)
    jobs = []
    got = []
    for spec in grid:
        rid = str(spec.get("representation_id"))
        fp = CACHE / _cache_name(rid)
        saved = _load(fp)
        if saved.get("ok") and saved.get("representation_id") == rid and (saved.get("info") or {}).get("ANY_FUD_GOOD_RATE") is not None:
            got.append(saved)
            print(f"INFO cache-hit {rid}", flush=True)
            continue
        scored_fp = OOF_CACHE / _scored_name(rid)
        if not (scored_fp.is_file() and _load(scored_fp).get("rows")):
            return _integrity(f"STOP. Frozen P_FILL5 scored cache missing for {rid}.")
        jobs.append(
            {
                "spec": spec,
                "representation_id": rid,
                "rows_path": str(rows_path),
                "fill_score_path": str(scored_fp),
                "scored_cache_path": str(CACHE / _scored_name(rid)),
                "days": list(ELIGIBLE_DAYS),
            }
        )
    print(f"INFO jobs={len(jobs)}", flush=True)
    for body in _pool(jobs):
        if body.get("ok") and (body.get("info") or {}).get("ANY_FUD_GOOD_RATE") is not None:
            slim = dict(body)
            slim.pop("scored", None)
            _save_json(CACHE / _cache_name(str(body.get("representation_id"))), slim)
        got.append(body)
    fail = [b for b in got if not b.get("ok") or (b.get("info") or {}).get("ANY_FUD_GOOD_RATE") is None]
    if fail or len(got) != 9:
        return _integrity(
            "STOP. Frozen dual-signal information diagnostic failed.",
            extra={"fail": [(b.get("representation_id"), b.get("blocker")) for b in fail]},
        )
    got.sort(key=lambda b: str(b.get("representation_id") or ""))

    leak = {
        "MODEL_REFIT_N": int(MODEL_REFIT_N),
        "FEATURE_SEARCH_N": int(FEATURE_SEARCH_N),
        "NEW_FEATURE_N": int(NEW_FEATURE_N),
        "PM_ROWS_USED_N": 0,
        "FUTURE_EVENT_USE_N": 0,
        "TARGET_CONTAMINATION_N": 0,
        "HELDOUT_FIT_LEAK_N": 0,
        "SELECTION_REOPTIMIZATION_N": int(SELECTION_REOPTIMIZATION_N),
        "WAIT_SEARCH_N": int(WAIT_SEARCH_N),
        "POLICY_FROM_ACTUAL_N": int(POLICY_FROM_ACTUAL_N),
        "POLICY_FROM_PARETO_N": int(POLICY_FROM_PARETO_N),
        "SUBSET_ENUMERATION_ERROR_N": 0,
        "JOIN_MISS_N": 0,
        "DUPLICATE_KEY_N": 0,
    }
    am_rows = list((_load(rows_path).get("rows") or []))
    leak["FUTURE_EVENT_USE_N"] = sum(1 for r in am_rows if r.get("future_event_use"))
    leak["PM_ROWS_USED_N"] = sum(1 for r in am_rows if session_of(r) != "AM")
    for b in got:
        rid = str(b.get("representation_id") or "")
        geo_prior = _load(GEO_CACHE / _cache_name(rid)).get("geometry") or {}
        info = b.get("info") or {}
        if not _close(info.get("ANY_FU_GOOD_RATE"), geo_prior.get("ANY_FU_GOOD_RATE"), PARITY_ABS_TOL):
            return _integrity(
                "STOP. This-run ANY_FU_GOOD_RATE did not match frozen constrained geometry cache.",
                extra={"representation_id": rid, "got": info.get("ANY_FU_GOOD_RATE"), "prior": geo_prior.get("ANY_FU_GOOD_RATE")},
            )
        if not _close(info.get("FRONTIER_FUD_GOOD_RATE"), geo_prior.get("FRONTIER_FUD_GOOD_RATE"), PARITY_ABS_TOL):
            return _integrity(
                "STOP. This-run FRONTIER_FUD_GOOD_RATE did not match frozen constrained geometry cache.",
                extra={"representation_id": rid, "got": info.get("FRONTIER_FUD_GOOD_RATE"), "prior": geo_prior.get("FRONTIER_FUD_GOOD_RATE")},
            )
        rec_leak = b.get("integrity") or {}
        leak["TARGET_CONTAMINATION_N"] += int(rec_leak.get("TARGET_CONTAMINATION_N") or 0)
        leak["HELDOUT_FIT_LEAK_N"] += int(rec_leak.get("HELDOUT_FIT_LEAK_N") or 0)
        leak["SUBSET_ENUMERATION_ERROR_N"] += int(info.get("SUBSET_ENUMERATION_ERROR_N") or 0)
        leak["JOIN_MISS_N"] += int((b.get("join") or {}).get("JOIN_MISS_N") or 0)
        leak["DUPLICATE_KEY_N"] += int((b.get("join") or {}).get("DUPLICATE_KEY_N") or 0)

    specs = spec_rows(got)
    run_obs = {
        "ANY_FU_GOOD_RATE": _med_key(specs, "ANY_FU_GOOD_RATE"),
        "ANY_FUD_GOOD_RATE": _med_key(specs, "ANY_FUD_GOOD_RATE"),
        "FRONTIER_FU_GOOD_RATE": _med_key(specs, "FRONTIER_FU_GOOD_RATE"),
        "FRONTIER_FUD_GOOD_RATE": _med_key(specs, "FRONTIER_FUD_GOOD_RATE"),
        "FUD_GOOD_FILL_PERCENTILE_MEDIAN": _med_key(specs, "FUD_GOOD_FILL_PERCENTILE_MEDIAN"),
        "FUD_GOOD_EXECU_PERCENTILE_MEDIAN": _med_key(specs, "FUD_GOOD_EXECU_PERCENTILE_MEDIAN"),
        "PRED_FILL_EXECU_SPEARMAN_MEDIAN": _med_key(specs, "PRED_FILL_EXECU_SPEARMAN_OVERALL"),
        "FILL_ONLY_ON_FRONTIER_RATE": _med_key(specs, "FILL_ONLY_ON_FRONTIER_RATE"),
        "DIRECT_ON_FRONTIER_RATE": _med_key(specs, "DIRECT_ON_FRONTIER_RATE"),
    }
    run_pack = freeze_parity(run_obs)
    print("run parity", run_pack.get("ok"), run_pack.get("checks"), flush=True)
    if not run_pack.get("ok"):
        return _integrity("STOP. This-run geometry headlines did not match frozen parity.", extra={"parity": run_pack})
    print("integrity", leak, flush=True)

    feat_cons = feature_consensus(got)
    fill_cons = residual_consensus(got, "FILL")
    exec_cons = residual_consensus(got, "EXEC_U")
    fail_name = primary_failure(specs)
    decision = decide(
        parity_ok=True,
        leak=leak,
        feat_cons=feat_cons,
        fill_cons=fill_cons,
        exec_cons=exec_cons,
    )
    if decision.get("CASE") == "F":
        return _integrity(str(decision.get("PRIMARY_FINDING")), extra={"integrity": leak})

    required = {
        "BASE_PARITY": True,
        "OFF_FRONTIER_FUD_GOOD_COHORT_N": _med_key(specs, "OFF_FRONTIER_FUD_GOOD_COHORT_N"),
        "FALSE_DOMINATOR_N": _med_key(specs, "FALSE_DOMINATOR_N"),
        "FALSE_DOMINATOR_COHORT_N": _med_key(specs, "FALSE_DOMINATOR_COHORT_N"),
        "FD_FILL_FAIL_RATE": _med_key(specs, "FD_FILL_FAIL_RATE"),
        "FD_U_FAIL_RATE": _med_key(specs, "FD_U_FAIL_RATE"),
        "FD_D_FAIL_RATE": _med_key(specs, "FD_D_FAIL_RATE"),
        "PRIMARY_FALSE_DOMINATOR_FAILURE": fail_name,
        "GOOD_ONLY_FILL_RATE": _med_key(specs, "GOOD_ONLY_FILL_RATE"),
        "DOMINATOR_ONLY_FILL_RATE": _med_key(specs, "DOMINATOR_ONLY_FILL_RATE"),
        "GOOD_ONLY_COND_U": _med_key(specs, "GOOD_ONLY_COND_U"),
        "DOMINATOR_ONLY_COND_U": _med_key(specs, "DOMINATOR_ONLY_COND_U"),
        "GOOD_ONLY_COND_D": _med_key(specs, "GOOD_ONLY_COND_D"),
        "DOMINATOR_ONLY_COND_D": _med_key(specs, "DOMINATOR_ONLY_COND_D"),
        "GOOD_ONLY_P_FILL_MEDIAN": _med_key(specs, "GOOD_ONLY_P_FILL_MEDIAN"),
        "DOMINATOR_ONLY_P_FILL_MEDIAN": _med_key(specs, "DOMINATOR_ONLY_P_FILL_MEDIAN"),
        "GOOD_ONLY_PRED_EXEC_U_MEDIAN": _med_key(specs, "GOOD_ONLY_PRED_EXEC_U_MEDIAN"),
        "DOMINATOR_ONLY_PRED_EXEC_U_MEDIAN": _med_key(specs, "DOMINATOR_ONLY_PRED_EXEC_U_MEDIAN"),
        "EXISTING_FEATURE_DIFFERENCE_SUPPORTED": decision.get("EXISTING_FEATURE_DIFFERENCE_SUPPORTED"),
        "FILL_RESIDUAL_INFORMATION_SUPPORTED": decision.get("FILL_RESIDUAL_INFORMATION_SUPPORTED"),
        "EXEC_U_RESIDUAL_INFORMATION_SUPPORTED": decision.get("EXEC_U_RESIDUAL_INFORMATION_SUPPORTED"),
        "PRIMARY_MECHANISM": decision.get("PRIMARY_MECHANISM"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "FD_MULTIPLE_FAIL_RATE": _med_key(specs, "FD_MULTIPLE_FAIL_RATE"),
        "FALSE_DOMINATOR_PER_COHORT_MEDIAN": _med_key(specs, "FALSE_DOMINATOR_PER_COHORT_MEDIAN"),
        "OFF_FRONTIER_FUD_GOOD_SUBSET_N": _med_key(specs, "OFF_FRONTIER_FUD_GOOD_SUBSET_N"),
        "DELTA_PRED_FILL_MEDIAN": _med_key(specs, "DELTA_PRED_FILL_MEDIAN"),
        "DELTA_PRED_EXEC_U_MEDIAN": _med_key(specs, "DELTA_PRED_EXEC_U_MEDIAN"),
        "DELTA_ACT_FILL_MEDIAN": _med_key(specs, "DELTA_ACT_FILL_MEDIAN"),
        "DELTA_ACT_EXEC_U_MEDIAN": _med_key(specs, "DELTA_ACT_EXEC_U_MEDIAN"),
        "DELTA_ACT_EXEC_D_MEDIAN": _med_key(specs, "DELTA_ACT_EXEC_D_MEDIAN"),
        "GOOD_ONLY_PRED_EXECU_RANK_MEDIAN": _med_key(specs, "GOOD_ONLY_PRED_EXECU_RANK_MEDIAN"),
        "DOMINATOR_ONLY_PRED_EXECU_RANK_MEDIAN": _med_key(specs, "DOMINATOR_ONLY_PRED_EXECU_RANK_MEDIAN"),
        "GOOD_ONLY_PFILL_RANK_MEDIAN": _med_key(specs, "GOOD_ONLY_PFILL_RANK_MEDIAN"),
        "DOMINATOR_ONLY_PFILL_RANK_MEDIAN": _med_key(specs, "DOMINATOR_ONLY_PFILL_RANK_MEDIAN"),
        "TOP_PRED_FALSE_POSITIVE_RATE": _med_key(specs, "TOP_PRED_FALSE_POSITIVE_RATE"),
        "HIGH_HIGH_FILL_RATE": _med_key(specs, "HIGH_HIGH_FILL_RATE"),
        "HIGH_HIGH_FUD_CONTRIBUTION_RATE": _med_key(specs, "HIGH_HIGH_FUD_CONTRIBUTION_RATE"),
        "MODEL_REFIT_N": leak.get("MODEL_REFIT_N"),
        "FEATURE_SEARCH_N": leak.get("FEATURE_SEARCH_N"),
        "NEW_FEATURE_N": leak.get("NEW_FEATURE_N"),
        "PM_ROWS_USED_N": leak.get("PM_ROWS_USED_N"),
        "FUTURE_EVENT_USE_N": leak.get("FUTURE_EVENT_USE_N"),
        "TARGET_CONTAMINATION_N": leak.get("TARGET_CONTAMINATION_N"),
        "HELDOUT_FIT_LEAK_N": leak.get("HELDOUT_FIT_LEAK_N"),
        "SELECTION_REOPTIMIZATION_N": leak.get("SELECTION_REOPTIMIZATION_N"),
        "WAIT_SEARCH_N": leak.get("WAIT_SEARCH_N"),
        "POLICY_FROM_ACTUAL_N": leak.get("POLICY_FROM_ACTUAL_N"),
        "JOIN_MISS_N": leak.get("JOIN_MISS_N"),
        "SUBSET_ENUMERATION_ERROR_N": leak.get("SUBSET_ENUMERATION_ERROR_N"),
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
        "REPRESENTATION_N": REPRESENTATION_N,
        "FROZEN_OOF_REPLAY_ONLY": FROZEN_OOF_REPLAY_ONLY,
    }
    print(
        f"CASE={decision.get('CASE')} feat={decision.get('EXISTING_FEATURE_DIFFERENCE_SUPPORTED')} "
        f"fill_res={decision.get('FILL_RESIDUAL_INFORMATION_SUPPORTED')} "
        f"exec_res={decision.get('EXEC_U_RESIDUAL_INFORMATION_SUPPORTED')} "
        f"fd_fail={fail_name} VERDICT={decision.get('VERDICT')}",
        flush=True,
    )
    feat_sheet = []
    for b in got:
        for rec in (b.get("info") or {}).get("features") or []:
            feat_sheet.append({"representation_id": b.get("representation_id"), "scope": "PAIR", **rec})
        for rec in (b.get("info") or {}).get("residuals") or []:
            feat_sheet.append({"representation_id": b.get("representation_id"), "scope": "RESIDUAL_RAW", **rec})
    return write_report(
        required,
        decision=decision,
        extra={"parity": run_pack, "prior_parity": prior_pack, "integrity": leak},
        sheets_extra={
            "Parity": kv_rows(
                {
                    **{f"PRIOR_{k}": v for k, v in (prior_pack.get("checks") or {}).items()},
                    **{f"RUN_{k}": v for k, v in (run_pack.get("checks") or {}).items()},
                    "BASE_PARITY": True,
                }
            ),
            "FalseDominators": [
                {
                    "representation_id": r.get("representation_id"),
                    "OFF_FRONTIER_FUD_GOOD_COHORT_N": r.get("OFF_FRONTIER_FUD_GOOD_COHORT_N"),
                    "OFF_FRONTIER_FUD_GOOD_SUBSET_N": r.get("OFF_FRONTIER_FUD_GOOD_SUBSET_N"),
                    "FALSE_DOMINATOR_N": r.get("FALSE_DOMINATOR_N"),
                    "FALSE_DOMINATOR_COHORT_N": r.get("FALSE_DOMINATOR_COHORT_N"),
                    "FALSE_DOMINATOR_PER_COHORT_MEDIAN": r.get("FALSE_DOMINATOR_PER_COHORT_MEDIAN"),
                }
                for r in specs
            ],
            "FailureModes": [
                {
                    "representation_id": r.get("representation_id"),
                    "FD_FILL_FAIL_RATE": r.get("FD_FILL_FAIL_RATE"),
                    "FD_U_FAIL_RATE": r.get("FD_U_FAIL_RATE"),
                    "FD_D_FAIL_RATE": r.get("FD_D_FAIL_RATE"),
                    "FD_MULTIPLE_FAIL_RATE": r.get("FD_MULTIPLE_FAIL_RATE"),
                }
                for r in specs
            ],
            "PairedDeltas": [
                {
                    "representation_id": r.get("representation_id"),
                    "DELTA_PRED_FILL_MEDIAN": r.get("DELTA_PRED_FILL_MEDIAN"),
                    "DELTA_PRED_FILL_MEAN": r.get("DELTA_PRED_FILL_MEAN"),
                    "DELTA_PRED_EXEC_U_MEDIAN": r.get("DELTA_PRED_EXEC_U_MEDIAN"),
                    "DELTA_PRED_EXEC_U_MEAN": r.get("DELTA_PRED_EXEC_U_MEAN"),
                    "DELTA_ACT_FILL_MEDIAN": r.get("DELTA_ACT_FILL_MEDIAN"),
                    "DELTA_ACT_EXEC_U_MEDIAN": r.get("DELTA_ACT_EXEC_U_MEDIAN"),
                    "DELTA_ACT_EXEC_D_MEDIAN": r.get("DELTA_ACT_EXEC_D_MEDIAN"),
                }
                for r in specs
            ],
            "Swap": [
                {
                    "representation_id": r.get("representation_id"),
                    "GOOD_ONLY_N": r.get("GOOD_ONLY_N"),
                    "DOMINATOR_ONLY_N": r.get("DOMINATOR_ONLY_N"),
                    "GOOD_ONLY_FILL_RATE": r.get("GOOD_ONLY_FILL_RATE"),
                    "DOMINATOR_ONLY_FILL_RATE": r.get("DOMINATOR_ONLY_FILL_RATE"),
                    "GOOD_ONLY_COND_U": r.get("GOOD_ONLY_COND_U"),
                    "DOMINATOR_ONLY_COND_U": r.get("DOMINATOR_ONLY_COND_U"),
                    "GOOD_ONLY_COND_D": r.get("GOOD_ONLY_COND_D"),
                    "DOMINATOR_ONLY_COND_D": r.get("DOMINATOR_ONLY_COND_D"),
                    "GOOD_ONLY_P_FILL_MEDIAN": r.get("GOOD_ONLY_P_FILL_MEDIAN"),
                    "DOMINATOR_ONLY_P_FILL_MEDIAN": r.get("DOMINATOR_ONLY_P_FILL_MEDIAN"),
                    "GOOD_ONLY_PRED_EXEC_U_MEDIAN": r.get("GOOD_ONLY_PRED_EXEC_U_MEDIAN"),
                    "DOMINATOR_ONLY_PRED_EXEC_U_MEDIAN": r.get("DOMINATOR_ONLY_PRED_EXEC_U_MEDIAN"),
                }
                for r in specs
            ],
            "Ranks": [
                {
                    "representation_id": r.get("representation_id"),
                    "GOOD_ONLY_PRED_EXECU_RANK_MEDIAN": r.get("GOOD_ONLY_PRED_EXECU_RANK_MEDIAN"),
                    "DOMINATOR_ONLY_PRED_EXECU_RANK_MEDIAN": r.get("DOMINATOR_ONLY_PRED_EXECU_RANK_MEDIAN"),
                    "GOOD_ONLY_PFILL_RANK_MEDIAN": r.get("GOOD_ONLY_PFILL_RANK_MEDIAN"),
                    "DOMINATOR_ONLY_PFILL_RANK_MEDIAN": r.get("DOMINATOR_ONLY_PFILL_RANK_MEDIAN"),
                    "TOP_PRED_FALSE_POSITIVE_RATE": r.get("TOP_PRED_FALSE_POSITIVE_RATE"),
                }
                for r in specs
            ],
            "HighHigh": [
                {
                    "representation_id": r.get("representation_id"),
                    "HIGH_HIGH_FILL_RATE": r.get("HIGH_HIGH_FILL_RATE"),
                    "HIGH_HIGH_EXEC_U": r.get("HIGH_HIGH_EXEC_U"),
                    "HIGH_HIGH_EXEC_D": r.get("HIGH_HIGH_EXEC_D"),
                    "HIGH_HIGH_FUD_CONTRIBUTION_RATE": r.get("HIGH_HIGH_FUD_CONTRIBUTION_RATE"),
                }
                for r in specs
            ],
            "Features": feat_cons + feat_sheet,
            "Residuals": fill_cons + [{"scope": "EXEC_U", **r} for r in exec_cons],
            "Representations": specs,
            "Integrity": kv_rows(
                {
                    **leak,
                    "SESSION": SESSION,
                    "RUNTIME_WAIT_SEC": WAIT_SEC,
                    "DEV_WAIT_SEC": DEV_WAIT_SEC,
                    "FROZEN_OOF_REPLAY_ONLY": FROZEN_OOF_REPLAY_ONLY,
                    "NEW_TARGET_CREATED": NEW_TARGET_CREATED,
                    "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                    "PNL_USED": PNL_USED,
                    "EXACT_RAN": EXACT_RAN,
                }
            ),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
