"""Offline direct joint-improvement objective probe. No Runtime write. No Paper. No Exact."""
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

from research.direct_joint_objective import (
    ANALYSIS_ID,
    BEST_REPRESENTATION_ADOPTED,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    CLASS_WEIGHT_TUNING,
    CONTROL,
    CURRENT_TOPK_CHANGED,
    ELIGIBLE_DAYS,
    EXACT_RAN,
    FEATURE_REDESIGN_STARTED,
    HYPERPARAMETER_TUNING,
    LABEL_THRESHOLD_CHANGED,
    MAX_WORKERS,
    NEW_FEATURE_CREATED,
    NEW_FORWARD_N,
    PAPER_OPERATED,
    PARETO_LABEL_USED,
    PNL_USED,
    PRIOR_VERDICT_REQUIRED,
    RF_PARAMS,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    STRATEGY_CREATED,
    THRESHOLD_SEARCH,
    TOPK_SEARCH,
    TRUE_OOS,
    UD_MARGIN_ADDED,
    WEIGHT_SEARCH,
)
from research.direct_joint_objective.analyze import aggregate, decide, spec_rows
from research.direct_joint_objective.oof import process_representation, representation_grid
from research.direct_joint_objective.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from small_paper.v1r_native_entry_live import FEATURE_ORDER

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
PRIOR = NATIVE / "results" / "research" / "multioutput_model_probe" / "report.json"
ROWS_PATH = NATIVE / "results" / "research" / "_work_cache" / "entry_target_architecture" / "path_rows.json"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "direct_joint_objective"


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
                f"mfe={body.get('TOP3_MFE_DELTA')} dn={body.get('TOP3_DOWNSIDE_DELTA')} "
                f"blocker={body.get('blocker')}",
                flush=True,
            )
    return out


def _cache_name(rid: str) -> str:
    return "rf_" + rid.replace("|", "_").replace(" ", "") + ".json"


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
                "MODEL": "RandomForestClassifier native P(JOINT_IMPROVEMENT_LABEL=1)",
                "RF_PARAMS": RF_PARAMS,
                "JOINT_SCORE": "P(JOINT_IMPROVEMENT_LABEL=1)",
                "TIE_BREAK": "score descending, symbol ASC",
                "LABEL": "1 iff U>CURRENT_U_BASELINE AND D>CURRENT_D_BASELINE",
                "REPRESENTATIONS": 9,
                "BEST_REPRESENTATION_ADOPTED": BEST_REPRESENTATION_ADOPTED,
                "HYPERPARAMETER_TUNING": HYPERPARAMETER_TUNING,
                "CLASS_WEIGHT_TUNING": CLASS_WEIGHT_TUNING,
                "EXACT_RAN": EXACT_RAN,
                "RUNTIME_CANDIDATE_CREATED": RUNTIME_CANDIDATE_CREATED,
                "FEATURE_REDESIGN_STARTED": FEATURE_REDESIGN_STARTED,
            }
        ),
        "Control": kv_rows(dict(CONTROL)),
        "LabelPrevalence": [{"empty": True}],
        "Representations": [{"empty": True}],
        "Gates": [{"empty": True}],
        "ConsensusDays": [{"empty": True}],
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
                "HYPERPARAMETER_TUNING": HYPERPARAMETER_TUNING,
                "BEST_REPRESENTATION_ADOPTED": BEST_REPRESENTATION_ADOPTED,
                "WEIGHT_SEARCH": WEIGHT_SEARCH,
                "LABEL_THRESHOLD_CHANGED": LABEL_THRESHOLD_CHANGED,
                "CURRENT_TOPK_CHANGED": CURRENT_TOPK_CHANGED,
                "UD_MARGIN_ADDED": UD_MARGIN_ADDED,
                "PARETO_LABEL_USED": PARETO_LABEL_USED,
                "CLASS_WEIGHT_TUNING": CLASS_WEIGHT_TUNING,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
                "TOPK_SEARCH": TOPK_SEARCH,
                "PNL_USED": PNL_USED,
                "RUNTIME_CANDIDATE_CREATED": RUNTIME_CANDIDATE_CREATED,
                "STRATEGY_CREATED": STRATEGY_CREATED,
                "FEATURE_REDESIGN_STARTED": FEATURE_REDESIGN_STARTED,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"VERDICT={required.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(
        "STOP. No Exact. No runtime candidate. No tuning. Feature redesign not started. "
        "Runtime unchanged. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "DIRECT_JOINT_OBJECTIVE_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str) -> int:
    return write_report(
        {
            "VERDICT": "DIRECT_JOINT_OBJECTIVE_INTEGRITY_FAILED",
            "DIRECT_JOINT_OBJECTIVE_PASS": False,
            "NEXT_RESEARCH": "NONE",
            "TRUE_OOS": TRUE_OOS,
            "NEW_FORWARD_N": NEW_FORWARD_N,
            "PRIMARY_FINDING": note,
        },
        decision={
            "CASE": None,
            "VERDICT": "DIRECT_JOINT_OBJECTIVE_INTEGRITY_FAILED",
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
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE DIRECT JOINT IMPROVEMENT OBJECTIVE PROBE V1", flush=True)
    print("RF classifier P(joint improvement). Frozen Top3. 9 representations. No Exact.", flush=True)

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
    grid = representation_grid()
    if len(grid) != 9:
        print("STOP representation grid is not 9", len(grid), flush=True)
        return 2
    prior = _load(PRIOR)
    preg = prior.get("required") or {}
    if preg.get("VERDICT") != PRIOR_VERDICT_REQUIRED:
        print("STOP prior multioutput verdict mismatch", preg.get("VERDICT"), flush=True)
        return _integrity("STOP. Prior NONLINEAR_MODEL_STILL_SINGLE_OBJECTIVE required.")
    if not ROWS_PATH.is_file():
        print("STOP missing path_rows.json", flush=True)
        return _integrity("STOP. Common-cohort path_rows missing.")

    jobs = []
    got = []
    for spec in grid:
        rid = str(spec.get("representation_id"))
        fp = CACHE / _cache_name(rid)
        saved = _load(fp)
        if saved.get("ok") and saved.get("representation_id") == rid and saved.get("daily_mfe"):
            got.append(saved)
            print(f"rf cache-hit {rid}", flush=True)
            continue
        jobs.append(
            {
                "spec": spec,
                "representation_id": rid,
                "rows_path": str(ROWS_PATH),
                "days": list(ELIGIBLE_DAYS),
            }
        )
    print(f"rf-clf jobs={len(jobs)}", flush=True)
    for body in _pool(process_representation, jobs, "RF-CLF"):
        if body.get("ok"):
            _save_json(CACHE / _cache_name(str(body.get("representation_id"))), body)
        got.append(body)
    fail = [b for b in got if not b.get("ok")]
    if fail or len(got) != 9:
        print("STOP RF classifier LODO failed", [(b.get("representation_id"), b.get("blocker")) for b in fail], flush=True)
        return _integrity("STOP. RF classifier representation LODO failed.")

    label_keys = ("JOINT_LABEL_POSITIVE_N", "JOINT_LABEL_NEGATIVE_N", "JOINT_LABEL_POSITIVE_RATE")
    base_lab = {k: got[0].get(k) for k in label_keys}
    if any({k: b.get(k) for k in label_keys} != base_lab for b in got):
        print("STOP label prevalence mismatch across representations", flush=True)
        return _integrity("STOP. Joint label prevalence is not identical across representations.")

    agg = aggregate(got)
    decision = decide(agg)
    required = {
        "JOINT_LABEL_POSITIVE_N": agg.get("JOINT_LABEL_POSITIVE_N"),
        "JOINT_LABEL_NEGATIVE_N": agg.get("JOINT_LABEL_NEGATIVE_N"),
        "JOINT_LABEL_POSITIVE_RATE": agg.get("JOINT_LABEL_POSITIVE_RATE"),
        "REPRESENTATION_N": agg.get("REPRESENTATION_N"),
        "MEDIAN_DIRECT_MFE_DELTA": agg.get("MEDIAN_DIRECT_MFE_DELTA"),
        "MEDIAN_DIRECT_DOWNSIDE_DELTA": agg.get("MEDIAN_DIRECT_DOWNSIDE_DELTA"),
        "MFE_POSITIVE_REP_N": agg.get("MFE_POSITIVE_REP_N"),
        "DOWNSIDE_POSITIVE_REP_N": agg.get("DOWNSIDE_POSITIVE_REP_N"),
        "MEDIAN_DIRECT_JOINT_COHORT_SUCCESS_RATE": agg.get("MEDIAN_DIRECT_JOINT_COHORT_SUCCESS_RATE"),
        "CONSENSUS_MFE_POS_DAYS": agg.get("CONSENSUS_MFE_POS_DAYS"),
        "CONSENSUS_MFE_NEG_DAYS": agg.get("CONSENSUS_MFE_NEG_DAYS"),
        "CONSENSUS_DOWNSIDE_POS_DAYS": agg.get("CONSENSUS_DOWNSIDE_POS_DAYS"),
        "CONSENSUS_DOWNSIDE_NEG_DAYS": agg.get("CONSENSUS_DOWNSIDE_NEG_DAYS"),
        "CONSENSUS_MFE_EX_BEST_DAY": agg.get("CONSENSUS_MFE_EX_BEST_DAY"),
        "CONSENSUS_MFE_EX_TOP3_DAYS": agg.get("CONSENSUS_MFE_EX_TOP3_DAYS"),
        "CONSENSUS_DOWNSIDE_EX_BEST_DAY": agg.get("CONSENSUS_DOWNSIDE_EX_BEST_DAY"),
        "CONSENSUS_DOWNSIDE_EX_TOP3_DAYS": agg.get("CONSENSUS_DOWNSIDE_EX_TOP3_DAYS"),
        "MEDIAN_ROC_AUC": agg.get("MEDIAN_ROC_AUC"),
        "MEDIAN_AVERAGE_PRECISION": agg.get("MEDIAN_AVERAGE_PRECISION"),
        "CONTROL_RF_MFE_DELTA": CONTROL["CONTROL_RF_MFE_DELTA"],
        "CONTROL_RF_DOWNSIDE_DELTA": CONTROL["CONTROL_RF_DOWNSIDE_DELTA"],
        "CONTROL_RF_JOINT_RATE": CONTROL["CONTROL_RF_JOINT_RATE"],
        "ACTUAL_PARETO_MEMBER_RATE": agg.get("ACTUAL_PARETO_MEMBER_RATE"),
        "ACTUAL_DOMINATED_RATE": agg.get("ACTUAL_DOMINATED_RATE"),
        "DIRECT_JOINT_OBJECTIVE_PASS": agg.get("DIRECT_JOINT_OBJECTIVE_PASS"),
        "GATE_FAIL": agg.get("gate_fail"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
    }
    print(
        f"pass={agg.get('DIRECT_JOINT_OBJECTIVE_PASS')} fail={agg.get('gate_fail')} "
        f"mfe={agg.get('MEDIAN_DIRECT_MFE_DELTA')} dn={agg.get('MEDIAN_DIRECT_DOWNSIDE_DELTA')} "
        f"joint_rate={agg.get('MEDIAN_DIRECT_JOINT_COHORT_SUCCESS_RATE')} CASE={decision.get('CASE')}",
        flush=True,
    )
    return write_report(
        required,
        decision=decision,
        extra={"aggregate": {k: v for k, v in agg.items() if k != "daily"}, "control": dict(CONTROL)},
        sheets_extra={
            "LabelPrevalence": agg.get("daily_label_rate") or [{"empty": True}],
            "Representations": spec_rows(got),
            "Gates": kv_rows(agg.get("gates")),
            "ConsensusDays": agg.get("daily") or [{"empty": True}],
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
