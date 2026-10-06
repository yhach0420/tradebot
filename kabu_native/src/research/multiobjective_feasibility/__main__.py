"""Offline joint U/D Pareto feasibility. No Runtime write. No Paper. No Exact. No strategy."""
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

from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.entry_objective_redesign_c3.oof import spec_grid, spec_label
from research.multiobjective_feasibility import (
    ANALYSIS_ID,
    BEST_SPEC_ADOPTED,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    ELIGIBLE_DAYS,
    EXACT_RAN,
    EXECUTION_AWARE_MODEL_CREATED,
    JOINT_FEASIBLE_MIN_RATE,
    KNEE_CREATED,
    LEARNABILITY_VERDICT_MAINTAINED,
    MAX_WORKERS,
    NESTED_SPEC_SELECTOR,
    NEW_FEATURE_CREATED,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    NEW_TARGET_CREATED,
    OPVAL_OPERATED,
    PAPER_OPERATED,
    PARETO_SELECTION_RULE,
    PNL_USED,
    PRIOR_VERDICT_REQUIRED,
    RUNTIME_CHANGED,
    SELECTION_VERDICT_MAINTAINED,
    STRATEGY_CREATED,
    T3_WEIGHT_CHANGED,
    THRESHOLD_CREATED,
    TRUE_OOS,
    WEIGHT_SEARCH,
)
from research.multiobjective_feasibility.analyze import (
    actual_outcome_tradeoff,
    actual_pareto_and_current,
    aggregate_predicted,
    decide,
    predicted_joint_enrich,
)
from research.multiobjective_feasibility.oof import process_fixed_spec
from research.multiobjective_feasibility.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from small_paper.v1r_native_entry_live import FEATURE_ORDER

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
PRIOR = NATIVE / "results" / "research" / "target_architecture_selection" / "report.json"
ROWS_PATH = NATIVE / "results" / "research" / "_work_cache" / "entry_target_architecture" / "path_rows.json"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "multiobjective_feasibility"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str), encoding="utf-8")


def _pool(fn, jobs: list[dict], label: str) -> list[dict]:
    if not jobs:
        return []
    out = []
    workers = min(MAX_WORKERS, len(jobs))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, job): job.get("spec_id") for job in jobs}
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, "spec_id": key, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            print(
                f"done {label} {body.get('spec_id') or key} ok={body.get('ok')} blocker={body.get('blocker')}",
                flush=True,
            )
    return out


def _fixed_cache_name(spec_id: str) -> str:
    return "pred_" + spec_id.replace("|", "_").replace(" ", "") + ".json"


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
                "SOURCE": "CanonicalEngine common 600s cohort. Existing T1/T2 Ridge OOF only.",
                "U": "MFE_600 higher=better",
                "D": "DOWNSIDE_AVOID_600 higher=better",
                "JOINT_FEASIBLE_MIN_RATE": JOINT_FEASIBLE_MIN_RATE,
                "ORACLE": "Actual Pareto / joint-improvement are diagnostics. Not a runtime rule.",
                "NO_KNEE": True,
                "NO_WEIGHT": True,
                "NESTED_SPEC_SELECTOR": NESTED_SPEC_SELECTOR,
                "BEST_SPEC_ADOPTED": BEST_SPEC_ADOPTED,
                "STRATEGY_CREATED": STRATEGY_CREATED,
                "PARETO_SELECTION_RULE": PARETO_SELECTION_RULE,
                "EXACT_RAN": EXACT_RAN,
                "LEARNABILITY_VERDICT_MAINTAINED": LEARNABILITY_VERDICT_MAINTAINED,
                "SELECTION_VERDICT_MAINTAINED": SELECTION_VERDICT_MAINTAINED,
            }
        ),
        "OutcomeCorr": [{"empty": True}],
        "DailyCorr": [{"empty": True}],
        "ParetoCurrent": [{"empty": True}],
        "SpecConflict": [{"empty": True}],
        "EnrichmentDays": [{"empty": True}],
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
                "NEW_FEATURE_CREATED": NEW_FEATURE_CREATED,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "NEW_TARGET_CREATED": NEW_TARGET_CREATED,
                "WEIGHT_SEARCH": WEIGHT_SEARCH,
                "T3_WEIGHT_CHANGED": T3_WEIGHT_CHANGED,
                "PNL_USED": PNL_USED,
                "STRATEGY_CREATED": STRATEGY_CREATED,
                "KNEE_CREATED": KNEE_CREATED,
                "THRESHOLD_CREATED": THRESHOLD_CREATED,
                "PARETO_SELECTION_RULE": PARETO_SELECTION_RULE,
                "EXECUTION_AWARE_MODEL_CREATED": EXECUTION_AWARE_MODEL_CREATED,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"VERDICT={required.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(
        "STOP. No multi-objective strategy. No Exact. No C4. Runtime unchanged. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "MULTI_OBJECTIVE_FEASIBILITY_FAILED"
    return 2 if fail else 0


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE MULTI-OBJECTIVE ENTRY PARETO FEASIBILITY AUDIT V1", flush=True)
    print("U=MFE D=DOWNSIDE. Existing T1/T2 OOF only. No strategy. No Exact.", flush=True)

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
    grid = spec_grid()
    if len(grid) != 27:
        print("STOP spec grid is not 27", flush=True)
        return 2
    prior = _load(PRIOR)
    preg = prior.get("required") or {}
    if preg.get("VERDICT") != PRIOR_VERDICT_REQUIRED:
        print("STOP prior selection verdict mismatch", preg.get("VERDICT"), flush=True)
        return write_report(
            {
                "VERDICT": "MULTI_OBJECTIVE_FEASIBILITY_FAILED",
                "JOINT_OBJECTIVE_FEASIBLE": False,
                "NEXT_RESEARCH": "NONE",
                "TRUE_OOS": TRUE_OOS,
                "NEW_FORWARD_N": NEW_FORWARD_N,
            },
            decision={
                "CASE": None,
                "VERDICT": "MULTI_OBJECTIVE_FEASIBILITY_FAILED",
                "JOINT_OBJECTIVE_FEASIBLE": False,
                "NEXT_RESEARCH": "NONE",
                "note": "STOP. Prior MULTI_OBJECTIVE_TARGET_REQUIRED required.",
            },
        )
    if not ROWS_PATH.is_file():
        print("STOP missing path_rows.json", flush=True)
        return write_report(
            {
                "VERDICT": "MULTI_OBJECTIVE_FEASIBILITY_FAILED",
                "JOINT_OBJECTIVE_FEASIBLE": False,
                "NEXT_RESEARCH": "NONE",
                "TRUE_OOS": TRUE_OOS,
                "NEW_FORWARD_N": NEW_FORWARD_N,
            },
            decision={
                "CASE": None,
                "VERDICT": "MULTI_OBJECTIVE_FEASIBILITY_FAILED",
                "JOINT_OBJECTIVE_FEASIBLE": False,
                "NEXT_RESEARCH": "NONE",
                "note": "STOP. Common-cohort path_rows missing.",
            },
        )

    rows = list((_load(ROWS_PATH).get("rows") or []))
    corr = actual_outcome_tradeoff(rows)
    pc = actual_pareto_and_current(rows)
    print(
        f"cohorts={pc.get('n_cohorts')} spearman_med={corr.get('OUTCOME_UD_SPEARMAN_MEDIAN')} "
        f"neg_corr={corr.get('NEGATIVE_CORR_COHORT_N')} pareto_size={pc.get('PARETO_FRONT_SIZE_MEAN')} "
        f"joint_rate={pc.get('JOINT_IMPROVEMENT_AVAILABLE_RATE')} "
        f"top3_dom_rate={pc.get('CURRENT_TOP3_DOMINATED_RATE')}",
        flush=True,
    )

    jobs = []
    got = []
    for spec in grid:
        sid = spec_label(spec)
        fp = CACHE / _fixed_cache_name(sid)
        saved = _load(fp)
        if saved.get("ok") and saved.get("spec_id") == sid and saved.get("cohorts"):
            got.append(saved)
            print(f"pred cache-hit {sid}", flush=True)
            continue
        jobs.append({"spec": spec, "spec_id": sid, "rows_path": str(ROWS_PATH), "days": list(ELIGIBLE_DAYS)})
    print(f"pred-spec jobs={len(jobs)}", flush=True)
    for body in _pool(process_fixed_spec, jobs, "PRED"):
        if body.get("ok"):
            slim = dict(body)
            slim.pop("cohorts", None)
            _save_json(CACHE / _fixed_cache_name(str(body.get("spec_id"))), body)
        got.append(body)
    fail = [b for b in got if not b.get("ok")]
    if fail or len(got) != 27:
        print("STOP predicted OOF failed", [(b.get("spec_id"), b.get("blocker")) for b in fail], flush=True)
        return write_report(
            {
                **{k: v for k, v in corr.items() if k not in {"cohorts", "daily_median"}},
                **{k: v for k, v in pc.items() if k != "cohorts"},
                "VERDICT": "MULTI_OBJECTIVE_FEASIBILITY_FAILED",
                "JOINT_OBJECTIVE_FEASIBLE": False,
                "NEXT_RESEARCH": "NONE",
                "TRUE_OOS": TRUE_OOS,
                "NEW_FORWARD_N": NEW_FORWARD_N,
            },
            decision={
                "CASE": None,
                "VERDICT": "MULTI_OBJECTIVE_FEASIBILITY_FAILED",
                "JOINT_OBJECTIVE_FEASIBLE": False,
                "NEXT_RESEARCH": "NONE",
                "note": "STOP. T1/T2 predicted OOF failed.",
            },
        )

    pred = aggregate_predicted(got)
    pred_joint = predicted_joint_enrich(pred)
    decision = decide(joint_rate=pc.get("JOINT_IMPROVEMENT_AVAILABLE_RATE"), pred_joint=pred_joint)
    required = {
        "OUTCOME_UD_SPEARMAN_MEAN": corr.get("OUTCOME_UD_SPEARMAN_MEAN"),
        "OUTCOME_UD_SPEARMAN_MEDIAN": corr.get("OUTCOME_UD_SPEARMAN_MEDIAN"),
        "NEGATIVE_CORR_COHORT_N": corr.get("NEGATIVE_CORR_COHORT_N"),
        "POSITIVE_CORR_COHORT_N": corr.get("POSITIVE_CORR_COHORT_N"),
        "PARETO_FRONT_SIZE_MEAN": pc.get("PARETO_FRONT_SIZE_MEAN"),
        "PARETO_FRONT_SIZE_MEDIAN": pc.get("PARETO_FRONT_SIZE_MEDIAN"),
        "PARETO_FRONT_SHARE_MEAN": pc.get("PARETO_FRONT_SHARE_MEAN"),
        "CURRENT_TOP3_N": pc.get("CURRENT_TOP3_N"),
        "CURRENT_TOP3_DOMINATED_N": pc.get("CURRENT_TOP3_DOMINATED_N"),
        "CURRENT_TOP3_DOMINATED_RATE": pc.get("CURRENT_TOP3_DOMINATED_RATE"),
        "COHORTS_WITH_ANY_CURRENT_TOP3_DOMINATED": pc.get("COHORTS_WITH_ANY_CURRENT_TOP3_DOMINATED"),
        "COHORTS_WITH_ALL_CURRENT_TOP3_DOMINATED": pc.get("COHORTS_WITH_ALL_CURRENT_TOP3_DOMINATED"),
        "JOINT_IMPROVEMENT_AVAILABLE_COHORT_N": pc.get("JOINT_IMPROVEMENT_AVAILABLE_COHORT_N"),
        "JOINT_IMPROVEMENT_AVAILABLE_RATE": pc.get("JOINT_IMPROVEMENT_AVAILABLE_RATE"),
        "JOINT_IMPROVEMENT_CANDIDATE_N": pc.get("JOINT_IMPROVEMENT_CANDIDATE_N"),
        "JOINT_FEASIBLE_MIN_RATE": JOINT_FEASIBLE_MIN_RATE,
        "MEDIAN_T1_T2_PREDICTED_SPEARMAN": pred.get("MEDIAN_T1_T2_PREDICTED_SPEARMAN"),
        "T1_T2_PREDICTED_SPEARMAN_RANGE_MIN": pred.get("T1_T2_PREDICTED_SPEARMAN_MIN"),
        "T1_T2_PREDICTED_SPEARMAN_RANGE_MAX": pred.get("T1_T2_PREDICTED_SPEARMAN_MAX"),
        "MEDIAN_T1_T2_TOP3_OVERLAP_RATE": pred.get("MEDIAN_T1_T2_TOP3_OVERLAP_RATE"),
        "PREDICTED_PARETO_SIZE_MEAN": pred.get("PREDICTED_PARETO_SIZE_MEAN"),
        "PREDICTED_PARETO_SHARE_MEAN": pred.get("PREDICTED_PARETO_SHARE_MEAN"),
        "PARETO_MFE_DELTA_VS_CURRENT": pred.get("PARETO_MFE_DELTA_VS_CURRENT"),
        "PARETO_DOWNSIDE_DELTA_VS_CURRENT": pred.get("PARETO_DOWNSIDE_DELTA_VS_CURRENT"),
        "PARETO_MFE_POSITIVE_DAYS": pred.get("PARETO_MFE_POSITIVE_DAYS"),
        "PARETO_DOWNSIDE_POSITIVE_DAYS": pred.get("PARETO_DOWNSIDE_POSITIVE_DAYS"),
        "JOINT_OBJECTIVE_FEASIBLE": decision.get("JOINT_OBJECTIVE_FEASIBLE"),
        "PRIMARY_MECHANISM": decision.get("PRIMARY_MECHANISM"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "n_cohorts": pc.get("n_cohorts"),
    }
    print(
        f"pred_spearman={pred.get('MEDIAN_T1_T2_PREDICTED_SPEARMAN')} "
        f"overlap={pred.get('MEDIAN_T1_T2_TOP3_OVERLAP_RATE')} "
        f"pareto_mfe_d={pred.get('PARETO_MFE_DELTA_VS_CURRENT')} "
        f"pareto_dn_d={pred.get('PARETO_DOWNSIDE_DELTA_VS_CURRENT')} "
        f"CASE={decision.get('CASE')}",
        flush=True,
    )
    return write_report(
        required,
        decision=decision,
        extra={
            "outcome": {k: v for k, v in corr.items() if k != "cohorts"},
            "pareto_current": {k: v for k, v in pc.items() if k != "cohorts"},
            "predicted": {k: v for k, v in pred.items() if k not in {"spec_rows", "daily"}},
        },
        sheets_extra={
            "OutcomeCorr": corr.get("cohorts") or [{"empty": True}],
            "DailyCorr": corr.get("daily_median") or [{"empty": True}],
            "ParetoCurrent": pc.get("cohorts") or [{"empty": True}],
            "SpecConflict": pred.get("spec_rows") or [{"empty": True}],
            "EnrichmentDays": pred.get("daily") or [{"empty": True}],
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
