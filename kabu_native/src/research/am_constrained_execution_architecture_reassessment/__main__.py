"""Offline AM constrained dual-signal geometry. Frozen OOF. Pareto diagnostic only."""
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

from research.am_constrained_execution_architecture_reassessment import (
    ANALYSIS_ID,
    BEST_REPRESENTATION_ADOPTED,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    D_TRAINING_TARGET_ALLOWED,
    DEV_WAIT_SEC,
    ELIGIBLE_DAYS,
    EXACT_RAN,
    FEATURE_SEARCH,
    FINAL_MODEL_ADOPTED,
    FROZEN_OOF_REPLAY_ONLY,
    HYPERPARAMETER_TUNING,
    MAJORITY_RATE,
    MAX_WORKERS,
    MODEL_REFIT_N,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    NEW_TARGET_CREATED,
    ORACLE_SELECTION_USE_N,
    P_FILL_TIMES_U_PRED,
    PAPER_OPERATED,
    PARITY_ABS_TOL,
    PARETO_AS_STRATEGY,
    PNL_USED,
    POLICY_FROM_PARETO_N,
    PRIOR_COUPLING_ID,
    PRIOR_COUPLING_VERDICT,
    PRIOR_DEVELOPMENT_ID,
    PRIOR_DEVELOPMENT_VERDICT,
    PRIOR_GEOMETRY_ID,
    REPRESENTATION_N,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    SELECTION_REOPTIMIZATION_N,
    SESSION,
    SHORTLIST_SEARCH,
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
    WEIGHT_SEARCH,
)
from research.am_constrained_execution_architecture_reassessment.analyze import (
    daily_consensus,
    decide,
    freeze_parity,
    process_constrained,
    spec_rows,
)
from research.am_constrained_execution_architecture_reassessment.publish import (
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
COUPLING = NATIVE / "results" / "research" / "am_entry_execution_policy_coupling" / "report.json"
DEVELOPMENT = NATIVE / "results" / "research" / "am_direct_exec_u_development" / "report.json"
GEOMETRY = NATIVE / "results" / "research" / "am_wait5_fill_upside_geometry" / "report.json"
DIRECT_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_direct_exec_u_development"
OOF_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_wait5_stage2_reassessment"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_constrained_execution_architecture_reassessment"


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
        futs = {ex.submit(process_constrained, job): job.get("representation_id") for job in jobs}
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, "representation_id": key, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            geo = body.get("geometry") or {}
            print(
                f"done GEO {body.get('representation_id') or key} ok={body.get('ok')} "
                f"any_fu={geo.get('ANY_FU_GOOD_RATE')} fr_fu={geo.get('FRONTIER_FU_GOOD_RATE')} "
                f"fr_fud={geo.get('FRONTIER_FUD_GOOD_RATE')} blocker={body.get('blocker')}",
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
                "REPRESENTATION_N": REPRESENTATION_N,
                "MAJORITY_RATE": MAJORITY_RATE,
                "FROZEN_OOF_REPLAY_ONLY": FROZEN_OOF_REPLAY_ONLY,
                "PARETO_AS_STRATEGY": PARETO_AS_STRATEGY,
                "POLICY_FROM_PARETO_N": POLICY_FROM_PARETO_N,
                "ORACLE_SELECTION_USE_N": ORACLE_SELECTION_USE_N,
                "NEW_TARGET_CREATED": NEW_TARGET_CREATED,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "STAGE1_ALLOWED": STAGE1_ALLOWED,
                "STAGE2_ALLOWED": STAGE2_ALLOWED,
                "PNL_USED": PNL_USED,
                "EXACT_RAN": EXACT_RAN,
            }
        ),
        "Parity": [{"empty": True}],
        "AllSpaceOracle": [{"empty": True}],
        "PredictedFrontier": [{"empty": True}],
        "FrontierCapture": [{"empty": True}],
        "FrontierSize": [{"empty": True}],
        "Endpoints": [{"empty": True}],
        "GoodSetLocation": [{"empty": True}],
        "Spearman": [{"empty": True}],
        "DayRobustness": [{"empty": True}],
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
                "WEIGHT_SEARCH": WEIGHT_SEARCH,
                "SHORTLIST_SEARCH": SHORTLIST_SEARCH,
                "P_FILL_TIMES_U_PRED": P_FILL_TIMES_U_PRED,
                "D_TRAINING_TARGET_ALLOWED": D_TRAINING_TARGET_ALLOWED,
                "PARETO_AS_STRATEGY": PARETO_AS_STRATEGY,
                "POLICY_FROM_PARETO_N": POLICY_FROM_PARETO_N,
                "ORACLE_SELECTION_USE_N": ORACLE_SELECTION_USE_N,
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
        "STOP. Pareto diagnostic only. No selection rule. No new target. No Exact. No PnL. "
        "Runtime WAIT_SEC=1.0. W5/W10 not adopted. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "AM_CONSTRAINED_GEOMETRY_INTEGRITY_FAILED"
    return 2 if fail else 0


def _empty_required(note: str, extra: dict | None = None) -> dict:
    body = {
        "BASE_PARITY": False,
        "ANY_FU_GOOD_RATE": None,
        "ANY_FUD_GOOD_RATE": None,
        "FRONTIER_FU_GOOD_RATE": None,
        "FRONTIER_FUD_GOOD_RATE": None,
        "FU_FRONTIER_CAPTURE_RATIO": None,
        "FUD_FRONTIER_CAPTURE_RATIO": None,
        "PRED_FRONTIER_SIZE_MEDIAN": None,
        "FILL_ONLY_ON_FRONTIER_RATE": None,
        "DIRECT_ON_FRONTIER_RATE": None,
        "FUD_GOOD_FILL_PERCENTILE_MEDIAN": None,
        "FUD_GOOD_EXECU_PERCENTILE_MEDIAN": None,
        "PRED_FILL_EXECU_SPEARMAN_MEDIAN": None,
        "PRIMARY_MECHANISM": "INTEGRITY_FAIL",
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "AM_CONSTRAINED_GEOMETRY_INTEGRITY_FAILED",
        "PRIMARY_FINDING": note,
        "MODEL_REFIT_N": MODEL_REFIT_N,
        "PM_ROWS_USED_N": None,
        "FUTURE_EVENT_USE_N": None,
        "TARGET_CONTAMINATION_N": None,
        "HELDOUT_FIT_LEAK_N": None,
        "SELECTION_REOPTIMIZATION_N": SELECTION_REOPTIMIZATION_N,
        "WAIT_SEARCH_N": WAIT_SEARCH_N,
        "ORACLE_SELECTION_USE_N": ORACLE_SELECTION_USE_N,
        "POLICY_FROM_PARETO_N": POLICY_FROM_PARETO_N,
        "SUBSET_ENUMERATION_ERROR_N": None,
        "JOIN_MISS_N": None,
        "DUPLICATE_KEY_N": None,
    }
    if extra:
        body.update(extra)
    return body


def _integrity(note: str, extra: dict | None = None) -> int:
    return write_report(
        _empty_required(note, extra),
        decision={
            "CASE": "E",
            "PRIMARY_MECHANISM": "INTEGRITY_FAIL",
            "NEXT_RESEARCH": "NONE",
            "VERDICT": "AM_CONSTRAINED_GEOMETRY_INTEGRITY_FAILED",
            "PRIMARY_FINDING": note,
            "note": "CASE E. STOP. Integrity failed.",
        },
        extra=extra,
    )


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM CONSTRAINED EXECUTION ARCHITECTURE V1", flush=True)
    print("Frozen P_FILL5 + pred_EXEC_U. Predicted Pareto diagnostic only. No selection rule.", flush=True)

    if abs(float(WAIT_SEC) - 1.0) > 1e-12:
        print("STOP WAIT_SEC drift", flush=True)
        return 2
    if abs(float(DEV_WAIT_SEC) - 5.0) > 1e-12:
        print("STOP DEV_WAIT_SEC drift", flush=True)
        return 2
    if STAGE1_ALLOWED or STAGE2_ALLOWED or W10_ADOPTED or int(WAIT_SEARCH_N) != 0:
        print("STOP forbidden search/adopt flags", flush=True)
        return 2
    if NEW_TARGET_CREATED or NEW_MODEL_CREATED or PARETO_AS_STRATEGY or P_FILL_TIMES_U_PRED:
        print("STOP forbidden architecture flags", flush=True)
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

    coupling = _load(COUPLING)
    creg = coupling.get("required") or {}
    if str(coupling.get("ANALYSIS_ID") or "") != PRIOR_COUPLING_ID or str(creg.get("VERDICT") or "") != PRIOR_COUPLING_VERDICT:
        return _integrity(
            f"STOP. Prior {PRIOR_COUPLING_ID} / {PRIOR_COUPLING_VERDICT} required.",
            extra={"prior_id": coupling.get("ANALYSIS_ID"), "prior_verdict": creg.get("VERDICT")},
        )
    development = _load(DEVELOPMENT)
    dreg = development.get("required") or {}
    if str(development.get("ANALYSIS_ID") or "") != PRIOR_DEVELOPMENT_ID or str(dreg.get("VERDICT") or "") != PRIOR_DEVELOPMENT_VERDICT:
        return _integrity(
            f"STOP. Prior {PRIOR_DEVELOPMENT_ID} / {PRIOR_DEVELOPMENT_VERDICT} required.",
            extra={"prior_id": development.get("ANALYSIS_ID"), "prior_verdict": dreg.get("VERDICT")},
        )
    geo_prior = _load(GEOMETRY)
    greg = geo_prior.get("required") or {}
    if str(geo_prior.get("ANALYSIS_ID") or "") != PRIOR_GEOMETRY_ID:
        return _integrity(
            f"STOP. Prior {PRIOR_GEOMETRY_ID} required.",
            extra={"prior_id": geo_prior.get("ANALYSIS_ID")},
        )

    prior_obs = {
        "FILL_ONLY_FILL_RATE_W5": creg.get("FILL_ONLY_FILL_RATE_W5"),
        "DIRECT_FILL_RATE_W5": creg.get("DIRECT_FILL_RATE_W5"),
        "DIRECT_MINUS_FILL_ONLY_W5": creg.get("DIRECT_MINUS_FILL_ONLY_W5"),
        "DIRECT_EXEC_U_DELTA_W5": creg.get("DIRECT_EXEC_U_DELTA_W5"),
        "DIRECT_EXEC_D_DELTA_W5": creg.get("DIRECT_EXEC_D_DELTA_W5"),
        "DIRECT_ONLY_P_FILL_MEDIAN": creg.get("DIRECT_ONLY_P_FILL_MEDIAN"),
        "FILL_ONLY_ONLY_P_FILL_MEDIAN": creg.get("FILL_ONLY_ONLY_P_FILL_MEDIAN"),
        "DIRECT_ONLY_RECOVER_RATE_W10": creg.get("DIRECT_ONLY_RECOVER_RATE_W10"),
        "DIRECT_ONLY_NEVER_CROSS_10S_RATE": creg.get("DIRECT_ONLY_NEVER_CROSS_10S_RATE"),
        "FILL_NONWORSE_U_IMPROVE_RATE": greg.get("FILL_NONWORSE_U_IMPROVE_RATE"),
        "SAME_FILL_U_IMPROVE_RATE": greg.get("SAME_FILL_U_IMPROVE_RATE"),
        "FILL_NONWORSE_U_D_NONWORSE_RATE": greg.get("FILL_NONWORSE_U_D_NONWORSE_RATE"),
    }
    prior_pack = freeze_parity(prior_obs)
    print("prior parity", prior_pack.get("ok"), prior_pack.get("checks"), flush=True)
    if not prior_pack.get("ok"):
        return _integrity("STOP. Frozen coupling / geometry parity did not reproduce.", extra={"parity": prior_pack})

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
        if saved.get("ok") and saved.get("representation_id") == rid and (saved.get("geometry") or {}).get("ANY_FU_GOOD_RATE") is not None:
            got.append(saved)
            print(f"GEO cache-hit {rid}", flush=True)
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
                "days": list(ELIGIBLE_DAYS),
            }
        )
    print(f"GEO jobs={len(jobs)}", flush=True)
    for body in _pool(jobs):
        if body.get("ok") and (body.get("geometry") or {}).get("ANY_FU_GOOD_RATE") is not None:
            _save_json(CACHE / _cache_name(str(body.get("representation_id"))), body)
        got.append(body)
    fail = [b for b in got if not b.get("ok") or (b.get("geometry") or {}).get("ANY_FU_GOOD_RATE") is None]
    if fail or len(got) != 9:
        return _integrity(
            "STOP. Frozen dual-signal OOF geometry replay failed.",
            extra={"fail": [(b.get("representation_id"), b.get("blocker")) for b in fail]},
        )
    got.sort(key=lambda b: str(b.get("representation_id") or ""))

    leak = {
        "MODEL_REFIT_N": int(MODEL_REFIT_N),
        "PM_ROWS_USED_N": 0,
        "FUTURE_EVENT_USE_N": 0,
        "TARGET_CONTAMINATION_N": 0,
        "HELDOUT_FIT_LEAK_N": 0,
        "SELECTION_REOPTIMIZATION_N": int(SELECTION_REOPTIMIZATION_N),
        "WAIT_SEARCH_N": int(WAIT_SEARCH_N),
        "ORACLE_SELECTION_USE_N": int(ORACLE_SELECTION_USE_N),
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
        if int(b.get("outer_folds") or 0) != 18:
            return _integrity(
                "STOP. Held-out fold count is not 18.",
                extra={"representation_id": rid, "outer_folds": b.get("outer_folds")},
            )
        prior_arm = _load(DIRECT_CACHE / _cache_name(rid))
        fo = (b.get("FILL_ONLY") or {}).get("SELECTED_FILL_RATE")
        du = (b.get("DIRECT_EXEC_U") or {}).get("SELECTED_FILL_RATE")
        pfo = (prior_arm.get("FILL_ONLY") or {}).get("SELECTED_FILL_RATE")
        pdu = (prior_arm.get("DIRECT_EXEC_U") or {}).get("SELECTED_FILL_RATE")
        if not _close(fo, pfo, PARITY_ABS_TOL) or not _close(du, pdu, PARITY_ABS_TOL):
            return _integrity(
                "STOP. Replayed FILL_ONLY / DIRECT W5 fill did not match frozen development cache.",
                extra={"representation_id": rid, "fo": fo, "dev_fo": pfo, "du": du, "dev_du": pdu},
            )
        rec_leak = b.get("integrity") or {}
        leak["TARGET_CONTAMINATION_N"] += int(rec_leak.get("TARGET_CONTAMINATION_N") or 0)
        leak["HELDOUT_FIT_LEAK_N"] += int(rec_leak.get("HELDOUT_FIT_LEAK_N") or 0)
        geo = b.get("geometry") or {}
        leak["SUBSET_ENUMERATION_ERROR_N"] += int(geo.get("SUBSET_ENUMERATION_ERROR_N") or 0)
        leak["JOIN_MISS_N"] += int((b.get("join") or {}).get("JOIN_MISS_N") or 0)
        leak["DUPLICATE_KEY_N"] += int((b.get("join") or {}).get("DUPLICATE_KEY_N") or 0)

    specs = spec_rows(got)
    run_obs = {
        "FILL_ONLY_FILL_RATE_W5": _med_key(specs, "FILL_ONLY_FILL_RATE_W5"),
        "DIRECT_FILL_RATE_W5": _med_key(specs, "DIRECT_FILL_RATE_W5"),
        "DIRECT_MINUS_FILL_ONLY_W5": _med_key(specs, "DIRECT_MINUS_FILL_ONLY_W5"),
        "DIRECT_EXEC_U_DELTA_W5": _med_key(specs, "DIRECT_EXEC_U_DELTA_W5"),
        "DIRECT_EXEC_D_DELTA_W5": _med_key(specs, "DIRECT_EXEC_D_DELTA_W5"),
        "DIRECT_ONLY_P_FILL_MEDIAN": creg.get("DIRECT_ONLY_P_FILL_MEDIAN"),
        "FILL_ONLY_ONLY_P_FILL_MEDIAN": creg.get("FILL_ONLY_ONLY_P_FILL_MEDIAN"),
        "DIRECT_ONLY_RECOVER_RATE_W10": creg.get("DIRECT_ONLY_RECOVER_RATE_W10"),
        "DIRECT_ONLY_NEVER_CROSS_10S_RATE": creg.get("DIRECT_ONLY_NEVER_CROSS_10S_RATE"),
        "FILL_NONWORSE_U_IMPROVE_RATE": _med_key(specs, "ANY_FU_GOOD_RATE"),
        "SAME_FILL_U_IMPROVE_RATE": _med_key(specs, "SAME_FILL_U_IMPROVE_RATE"),
        "FILL_NONWORSE_U_D_NONWORSE_RATE": _med_key(specs, "ANY_FUD_GOOD_RATE"),
    }
    run_pack = freeze_parity(run_obs)
    print("run parity", run_pack.get("ok"), run_pack.get("checks"), flush=True)
    if not run_pack.get("ok"):
        return _integrity(
            "STOP. This-run FILL_ONLY/DIRECT/all-space oracle did not match frozen parity.",
            extra={"parity": run_pack},
        )
    print("integrity", leak, flush=True)

    any_fu = _med_key(specs, "ANY_FU_GOOD_RATE")
    any_fud = _med_key(specs, "ANY_FUD_GOOD_RATE")
    fr_fu = _med_key(specs, "FRONTIER_FU_GOOD_RATE")
    fr_fud = _med_key(specs, "FRONTIER_FUD_GOOD_RATE")
    decision = decide(
        parity_ok=True,
        leak=leak,
        any_fu=any_fu,
        any_fud=any_fud,
        frontier_fu=fr_fu,
        frontier_fud=fr_fud,
    )
    if decision.get("CASE") == "E":
        return _integrity(str(decision.get("PRIMARY_FINDING")), extra={"integrity": leak})

    fu_days, fu_st = daily_consensus(got, "FRONTIER_FU_GOOD_RATE")
    fud_days, fud_st = daily_consensus(got, "FRONTIER_FUD_GOOD_RATE")
    any_fu_days, any_fu_st = daily_consensus(got, "ANY_FU_GOOD_RATE")
    any_fud_days, any_fud_st = daily_consensus(got, "ANY_FUD_GOOD_RATE")

    required = {
        "BASE_PARITY": True,
        "ANY_FU_GOOD_RATE": any_fu,
        "ANY_FUD_GOOD_RATE": any_fud,
        "FRONTIER_FU_GOOD_RATE": fr_fu,
        "FRONTIER_FUD_GOOD_RATE": fr_fud,
        "FU_FRONTIER_CAPTURE_RATIO": _med_key(specs, "FU_FRONTIER_CAPTURE_RATIO"),
        "FUD_FRONTIER_CAPTURE_RATIO": _med_key(specs, "FUD_FRONTIER_CAPTURE_RATIO"),
        "PRED_FRONTIER_SIZE_MEDIAN": _med_key(specs, "PRED_FRONTIER_SIZE_MEDIAN"),
        "FILL_ONLY_ON_FRONTIER_RATE": _med_key(specs, "FILL_ONLY_ON_FRONTIER_RATE"),
        "DIRECT_ON_FRONTIER_RATE": _med_key(specs, "DIRECT_ON_FRONTIER_RATE"),
        "FUD_GOOD_FILL_PERCENTILE_MEDIAN": _med_key(specs, "FUD_GOOD_FILL_PERCENTILE_MEDIAN"),
        "FUD_GOOD_EXECU_PERCENTILE_MEDIAN": _med_key(specs, "FUD_GOOD_EXECU_PERCENTILE_MEDIAN"),
        "PRED_FILL_EXECU_SPEARMAN_MEDIAN": _med_key(specs, "PRED_FILL_EXECU_SPEARMAN_OVERALL"),
        "PRIMARY_MECHANISM": decision.get("PRIMARY_MECHANISM"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "ANY_FU_GOOD_COHORT_N": _med_key(specs, "ANY_FU_GOOD_COHORT_N"),
        "ANY_FUD_GOOD_COHORT_N": _med_key(specs, "ANY_FUD_GOOD_COHORT_N"),
        "FRONTIER_FU_GOOD_COHORT_N": _med_key(specs, "FRONTIER_FU_GOOD_COHORT_N"),
        "FRONTIER_FUD_GOOD_COHORT_N": _med_key(specs, "FRONTIER_FUD_GOOD_COHORT_N"),
        "PRED_FRONTIER_SIZE_MEAN": _med_key(specs, "PRED_FRONTIER_SIZE_MEAN"),
        "PRED_FRONTIER_SIZE_P75": _med_key(specs, "PRED_FRONTIER_SIZE_P75"),
        "PRED_FRONTIER_SIZE_P90": _med_key(specs, "PRED_FRONTIER_SIZE_P90"),
        "FRONTIER_TOO_LARGE_RATE": _med_key(specs, "FRONTIER_TOO_LARGE_RATE"),
        "FU_GOOD_FILL_PERCENTILE_MEDIAN": _med_key(specs, "FU_GOOD_FILL_PERCENTILE_MEDIAN"),
        "FU_GOOD_EXECU_PERCENTILE_MEDIAN": _med_key(specs, "FU_GOOD_EXECU_PERCENTILE_MEDIAN"),
        "PRED_FILL_EXECU_SPEARMAN_DAILY_MEDIAN": _med_key(specs, "PRED_FILL_EXECU_SPEARMAN_DAILY_MEDIAN"),
        "PRED_FILL_EXECU_SPEARMAN_COHORT_MEDIAN": _med_key(specs, "PRED_FILL_EXECU_SPEARMAN_COHORT_MEDIAN"),
        "FILL_ONLY_FILL_RATE_W5": run_obs.get("FILL_ONLY_FILL_RATE_W5"),
        "DIRECT_FILL_RATE_W5": run_obs.get("DIRECT_FILL_RATE_W5"),
        "DIRECT_MINUS_FILL_ONLY_W5": run_obs.get("DIRECT_MINUS_FILL_ONLY_W5"),
        "DIRECT_EXEC_U_DELTA_W5": run_obs.get("DIRECT_EXEC_U_DELTA_W5"),
        "DIRECT_EXEC_D_DELTA_W5": run_obs.get("DIRECT_EXEC_D_DELTA_W5"),
        "DIRECT_ONLY_P_FILL_MEDIAN": run_obs.get("DIRECT_ONLY_P_FILL_MEDIAN"),
        "FILL_ONLY_ONLY_P_FILL_MEDIAN": run_obs.get("FILL_ONLY_ONLY_P_FILL_MEDIAN"),
        "DIRECT_ONLY_RECOVER_RATE_W10": run_obs.get("DIRECT_ONLY_RECOVER_RATE_W10"),
        "DIRECT_ONLY_NEVER_CROSS_10S_RATE": run_obs.get("DIRECT_ONLY_NEVER_CROSS_10S_RATE"),
        "SAME_FILL_U_IMPROVE_RATE": run_obs.get("SAME_FILL_U_IMPROVE_RATE"),
        "COHORT_N": _med_key(specs, "COHORT_N"),
        "MODEL_REFIT_N": leak.get("MODEL_REFIT_N"),
        "PM_ROWS_USED_N": leak.get("PM_ROWS_USED_N"),
        "FUTURE_EVENT_USE_N": leak.get("FUTURE_EVENT_USE_N"),
        "TARGET_CONTAMINATION_N": leak.get("TARGET_CONTAMINATION_N"),
        "HELDOUT_FIT_LEAK_N": leak.get("HELDOUT_FIT_LEAK_N"),
        "SELECTION_REOPTIMIZATION_N": leak.get("SELECTION_REOPTIMIZATION_N"),
        "WAIT_SEARCH_N": leak.get("WAIT_SEARCH_N"),
        "ORACLE_SELECTION_USE_N": leak.get("ORACLE_SELECTION_USE_N"),
        "POLICY_FROM_PARETO_N": leak.get("POLICY_FROM_PARETO_N"),
        "SUBSET_ENUMERATION_ERROR_N": leak.get("SUBSET_ENUMERATION_ERROR_N"),
        "JOIN_MISS_N": leak.get("JOIN_MISS_N"),
        "DUPLICATE_KEY_N": leak.get("DUPLICATE_KEY_N"),
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
        "REPRESENTATION_N": REPRESENTATION_N,
        "MAJORITY_RATE": MAJORITY_RATE,
        "FROZEN_OOF_REPLAY_ONLY": FROZEN_OOF_REPLAY_ONLY,
    }
    print(
        f"CASE={decision.get('CASE')} any_fu={any_fu} any_fud={any_fud} "
        f"fr_fu={fr_fu} fr_fud={fr_fud} VERDICT={decision.get('VERDICT')}",
        flush=True,
    )
    return write_report(
        required,
        decision=decision,
        extra={
            "parity": run_pack,
            "prior_parity": prior_pack,
            "integrity": leak,
        },
        sheets_extra={
            "Parity": kv_rows(
                {
                    **{f"PRIOR_{k}": v for k, v in (prior_pack.get("checks") or {}).items()},
                    **{f"RUN_{k}": v for k, v in (run_pack.get("checks") or {}).items()},
                    "BASE_PARITY": True,
                }
            ),
            "AllSpaceOracle": [
                {
                    "representation_id": r.get("representation_id"),
                    "COHORT_N": r.get("COHORT_N"),
                    "ANY_FU_GOOD_COHORT_N": r.get("ANY_FU_GOOD_COHORT_N"),
                    "ANY_FU_GOOD_RATE": r.get("ANY_FU_GOOD_RATE"),
                    "ANY_FUD_GOOD_COHORT_N": r.get("ANY_FUD_GOOD_COHORT_N"),
                    "ANY_FUD_GOOD_RATE": r.get("ANY_FUD_GOOD_RATE"),
                    "SAME_FILL_U_IMPROVE_RATE": r.get("SAME_FILL_U_IMPROVE_RATE"),
                    "SUBSET_ENUMERATION_ERROR_N": r.get("SUBSET_ENUMERATION_ERROR_N"),
                }
                for r in specs
            ],
            "PredictedFrontier": [
                {
                    "representation_id": r.get("representation_id"),
                    "FRONTIER_FU_GOOD_COHORT_N": r.get("FRONTIER_FU_GOOD_COHORT_N"),
                    "FRONTIER_FU_GOOD_RATE": r.get("FRONTIER_FU_GOOD_RATE"),
                    "FRONTIER_FUD_GOOD_COHORT_N": r.get("FRONTIER_FUD_GOOD_COHORT_N"),
                    "FRONTIER_FUD_GOOD_RATE": r.get("FRONTIER_FUD_GOOD_RATE"),
                    "FILL_ONLY_ON_FRONTIER_RATE": r.get("FILL_ONLY_ON_FRONTIER_RATE"),
                    "DIRECT_ON_FRONTIER_RATE": r.get("DIRECT_ON_FRONTIER_RATE"),
                }
                for r in specs
            ],
            "FrontierCapture": [
                {
                    "representation_id": r.get("representation_id"),
                    "FU_FRONTIER_CAPTURE_RATIO": r.get("FU_FRONTIER_CAPTURE_RATIO"),
                    "FUD_FRONTIER_CAPTURE_RATIO": r.get("FUD_FRONTIER_CAPTURE_RATIO"),
                    "ANY_FU_GOOD_RATE": r.get("ANY_FU_GOOD_RATE"),
                    "FRONTIER_FU_GOOD_RATE": r.get("FRONTIER_FU_GOOD_RATE"),
                    "ANY_FUD_GOOD_RATE": r.get("ANY_FUD_GOOD_RATE"),
                    "FRONTIER_FUD_GOOD_RATE": r.get("FRONTIER_FUD_GOOD_RATE"),
                }
                for r in specs
            ],
            "FrontierSize": [
                {
                    "representation_id": r.get("representation_id"),
                    "PRED_FRONTIER_SIZE_MEAN": r.get("PRED_FRONTIER_SIZE_MEAN"),
                    "PRED_FRONTIER_SIZE_MEDIAN": r.get("PRED_FRONTIER_SIZE_MEDIAN"),
                    "PRED_FRONTIER_SIZE_P75": r.get("PRED_FRONTIER_SIZE_P75"),
                    "PRED_FRONTIER_SIZE_P90": r.get("PRED_FRONTIER_SIZE_P90"),
                    "FRONTIER_TOO_LARGE_RATE": r.get("FRONTIER_TOO_LARGE_RATE"),
                }
                for r in specs
            ],
            "Endpoints": [
                {
                    "representation_id": r.get("representation_id"),
                    "FILL_ONLY_ON_FRONTIER_RATE": r.get("FILL_ONLY_ON_FRONTIER_RATE"),
                    "DIRECT_ON_FRONTIER_RATE": r.get("DIRECT_ON_FRONTIER_RATE"),
                    "FILL_ONLY_FILL_RATE_W5": r.get("FILL_ONLY_FILL_RATE_W5"),
                    "DIRECT_FILL_RATE_W5": r.get("DIRECT_FILL_RATE_W5"),
                }
                for r in specs
            ],
            "GoodSetLocation": [
                {
                    "representation_id": r.get("representation_id"),
                    "FU_GOOD_FILL_PERCENTILE_MEDIAN": r.get("FU_GOOD_FILL_PERCENTILE_MEDIAN"),
                    "FU_GOOD_EXECU_PERCENTILE_MEDIAN": r.get("FU_GOOD_EXECU_PERCENTILE_MEDIAN"),
                    "FUD_GOOD_FILL_PERCENTILE_MEDIAN": r.get("FUD_GOOD_FILL_PERCENTILE_MEDIAN"),
                    "FUD_GOOD_EXECU_PERCENTILE_MEDIAN": r.get("FUD_GOOD_EXECU_PERCENTILE_MEDIAN"),
                }
                for r in specs
            ],
            "Spearman": [
                {
                    "representation_id": r.get("representation_id"),
                    "PRED_FILL_EXECU_SPEARMAN_OVERALL": r.get("PRED_FILL_EXECU_SPEARMAN_OVERALL"),
                    "PRED_FILL_EXECU_SPEARMAN_DAILY_MEDIAN": r.get("PRED_FILL_EXECU_SPEARMAN_DAILY_MEDIAN"),
                    "PRED_FILL_EXECU_SPEARMAN_COHORT_MEDIAN": r.get("PRED_FILL_EXECU_SPEARMAN_COHORT_MEDIAN"),
                }
                for r in specs
            ],
            "DayRobustness": (
                [{"scope": "ANY_FU_GOOD_RATE", **r} for r in any_fu_days]
                + [{"scope": "ANY_FUD_GOOD_RATE", **r} for r in any_fud_days]
                + [{"scope": "FRONTIER_FU_GOOD_RATE", **r} for r in fu_days]
                + [{"scope": "FRONTIER_FUD_GOOD_RATE", **r} for r in fud_days]
                + [
                    {
                        "scope": "DAY_STATS",
                        "ANY_FU_MEDIAN": any_fu_st.get("median"),
                        "ANY_FUD_MEDIAN": any_fud_st.get("median"),
                        "FRONTIER_FU_MEDIAN": fu_st.get("median"),
                        "FRONTIER_FUD_MEDIAN": fud_st.get("median"),
                    }
                ]
            ),
            "Representations": specs,
            "Integrity": kv_rows(
                {
                    **leak,
                    "SESSION": SESSION,
                    "RUNTIME_WAIT_SEC": WAIT_SEC,
                    "DEV_WAIT_SEC": DEV_WAIT_SEC,
                    "FROZEN_OOF_REPLAY_ONLY": FROZEN_OOF_REPLAY_ONLY,
                    "PARETO_AS_STRATEGY": PARETO_AS_STRATEGY,
                    "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
                    "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
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
