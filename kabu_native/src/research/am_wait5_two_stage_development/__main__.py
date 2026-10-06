"""Offline AM WAIT5 two-stage development. No Runtime write. No Paper. No Exact."""
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

from research.am_wait5_two_stage_development import (
    ANALYSIS_ID,
    BEST_REPRESENTATION_ADOPTED,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    DEV_WAIT_SEC,
    ELIGIBLE_DAYS,
    EXACT_RAN,
    FEATURE_SEARCH,
    FINAL_MODEL_ADOPTED,
    HYPERPARAMETER_TUNING,
    MAX_WORKERS,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    PAPER_OPERATED,
    PARITY_ABS_TOL,
    PNL_USED,
    PRIOR_INTERFACE_VERDICT,
    PRIOR_OBJECTIVE_VERDICT,
    REPRESENTATION_N,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    SESSION,
    SHORTLIST_SEARCH,
    STAGE1_SHORTLIST_N,
    STRATEGY_CREATED,
    THRESHOLD_SEARCH,
    TOPK_SEARCH,
    TRUE_OOS,
    WAIT_POLICY_ADOPTED,
)
from research.am_wait5_two_stage_development.analyze import (
    am_population,
    cond_sanity,
    decide,
    fillability_gate,
    freeze_population,
    freeze_priors,
    quality_increment_gate,
    spec_rows,
    sum_integrity,
    _med_key,
)
from research.am_wait5_two_stage_development.oof import process_am_dev
from research.am_wait5_two_stage_development.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from research.canonical_entry_performance_rebase.analyze import row_key, session_of, wf_index
from research.direct_joint_objective.oof import attach_joint_labels, representation_grid
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.entry_execution_feasibility.analyze import labeled_rows
from research.passive_wait_policy_reassessment.analyze import _close
from research.wait5_execution_aware_rebase.analyze import attach_w5
from research.wait5_session_target_learnability.oof import slim_row
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import WAIT_SEC

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
LEARN = NATIVE / "results" / "research" / "wait5_session_target_learnability" / "report.json"
OBJECTIVE = NATIVE / "results" / "research" / "am_wait5_two_stage_objective_precommit" / "report.json"
INTERFACE = NATIVE / "results" / "research" / "am_wait5_two_stage_interface_precommit" / "report.json"
ROWS_PATH = NATIVE / "results" / "research" / "_work_cache" / "entry_target_architecture" / "path_rows.json"
WAIT_CACHE = NATIVE / "results" / "research" / "_work_cache" / "passive_wait_policy_reassessment"
LEARN_CACHE = NATIVE / "results" / "research" / "_work_cache" / "wait5_session_target_learnability"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_wait5_two_stage_development"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str), encoding="utf-8")


def _cache_name(rid: str) -> str:
    return "AM_" + rid.replace("|", "_").replace(" ", "") + ".json"


def _pool(jobs: list[dict]) -> list[dict]:
    if not jobs:
        return []
    out = []
    workers = min(MAX_WORKERS, len(jobs))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(process_am_dev, job): job.get("representation_id") for job in jobs}
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, "representation_id": key, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            print(
                f"done AM-DEV {body.get('representation_id') or key} ok={body.get('ok')} "
                f"fo={(body.get('FILL_ONLY') or {}).get('SELECTED_FILL_RATE')} "
                f"ts={(body.get('TWO_STAGE') or {}).get('SELECTED_FILL_RATE')} blocker={body.get('blocker')}",
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
                "ARMS": "CONTROL, FILL_ONLY, TWO_STAGE",
                "STAGE1_SHORTLIST_N": STAGE1_SHORTLIST_N,
                "REPRESENTATION_N": REPRESENTATION_N,
                "BEST_REPRESENTATION_ADOPTED": BEST_REPRESENTATION_ADOPTED,
                "HYPERPARAMETER_TUNING": HYPERPARAMETER_TUNING,
                "SHORTLIST_SEARCH": SHORTLIST_SEARCH,
                "TOPK_SEARCH": TOPK_SEARCH,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
                "PNL_USED": PNL_USED,
                "EXACT_RAN": EXACT_RAN,
                "FINAL_MODEL_ADOPTED": FINAL_MODEL_ADOPTED,
            }
        ),
        "Parity": [{"empty": True}],
        "Arms": [{"empty": True}],
        "Distinctness": [{"empty": True}],
        "Fillability": [{"empty": True}],
        "ExecQuality": [{"empty": True}],
        "Conditional": [{"empty": True}],
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
                "TOPK_SEARCH": TOPK_SEARCH,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
                "SHORTLIST_SEARCH": SHORTLIST_SEARCH,
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
        "STOP. No Exact. No PnL. No final model. Runtime WAIT_SEC=1.0. W5 not adopted. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "AM_WAIT5_TWO_STAGE_DEVELOPMENT_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str, extra: dict | None = None) -> int:
    body = {
        "BASE_PARITY": False,
        "REPRESENTATION_N": 9,
        "CONTROL_FILL_RATE": None,
        "FILL_ONLY_FILL_RATE": None,
        "TWO_STAGE_FILL_RATE": None,
        "DELTA_FILL_RATE_VS_CONTROL": None,
        "DELTA_FILL_RATE_VS_FILL_ONLY": None,
        "CONTROL_EXEC_U": None,
        "CONTROL_EXEC_D": None,
        "FILL_ONLY_EXEC_U": None,
        "FILL_ONLY_EXEC_D": None,
        "TWO_STAGE_EXEC_U": None,
        "TWO_STAGE_EXEC_D": None,
        "DELTA_EXEC_U_VS_CONTROL": None,
        "DELTA_EXEC_D_VS_CONTROL": None,
        "DELTA_EXEC_U_VS_FILL_ONLY": None,
        "DELTA_EXEC_D_VS_FILL_ONLY": None,
        "TWO_STAGE_NE_FILL_ONLY_COHORT_RATE": None,
        "TWO_STAGE_FILLABILITY_PASS": None,
        "STAGE2_QUALITY_INCREMENT_PASS": None,
        "AM_TWO_STAGE_DEVELOPMENT_PASS": False,
        "U_POSITIVE_REP_N": None,
        "D_POSITIVE_REP_N": None,
        "U_POSITIVE_DAYS": None,
        "U_NEGATIVE_DAYS": None,
        "D_POSITIVE_DAYS": None,
        "D_NEGATIVE_DAYS": None,
        "U_EX_BEST_DAY": None,
        "U_EX_TOP3_DAYS": None,
        "D_EX_BEST_DAY": None,
        "D_EX_TOP3_DAYS": None,
        "PM_ROWS_USED_N": None,
        "FUTURE_EVENT_USE_N": None,
        "TARGET_CONTAMINATION_N": None,
        "HELDOUT_FIT_LEAK_N": None,
        "STAGE2_NONFILL_TARGET_TRAIN_N": None,
        "PRIMARY_FINDING": note,
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "AM_WAIT5_TWO_STAGE_DEVELOPMENT_INTEGRITY_FAILED",
    }
    if extra:
        body.update(extra)
    return write_report(
        body,
        decision={
            "CASE": "E",
            "AM_TWO_STAGE_DEVELOPMENT_PASS": False,
            "VERDICT": "AM_WAIT5_TWO_STAGE_DEVELOPMENT_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": note,
            "note": "CASE E. STOP. Integrity failed.",
        },
    )


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM WAIT5 TWO-STAGE DEVELOPMENT V1", flush=True)
    print("AM only. CONTROL / FILL_ONLY / TWO_STAGE. Frozen RF. No Exact. No PnL.", flush=True)

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
    learn = _load(LEARN)
    objective = _load(OBJECTIVE)
    interface = _load(INTERFACE)
    prior_pack = freeze_priors(
        learn=learn.get("required") or {},
        objective=objective.get("required") or {},
        interface=interface.get("required") or {},
    )
    print("priors", prior_pack.get("ok"), prior_pack.get("checks"), flush=True)
    if not prior_pack.get("ok"):
        return _integrity(
            f"STOP. Prior learnability / {PRIOR_OBJECTIVE_VERDICT} / {PRIOR_INTERFACE_VERDICT} required.",
            extra={"priors": prior_pack},
        )
    grid = representation_grid()
    if len(grid) != 9:
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
    pop_pack = freeze_population(am, pop["am_top3"])
    print("population", pop_pack.get("ok"), pop_pack.get("checks"), flush=True)
    if not pop_pack.get("ok"):
        return _integrity(
            "STOP. Frozen AM labeled population or CURRENT Top3 fill rate did not reproduce.",
            extra={"population": pop_pack},
        )
    base_parity = True

    CACHE.mkdir(parents=True, exist_ok=True)
    am_path = CACHE / "am_rows.json"
    _save_json(am_path, {"session": "AM", "rows": [slim_row(r) for r in am]})

    jobs = []
    got = []
    for spec in grid:
        rid = str(spec.get("representation_id"))
        fp = CACHE / _cache_name(rid)
        saved = _load(fp)
        if saved.get("ok") and saved.get("representation_id") == rid and saved.get("TWO_STAGE"):
            got.append(saved)
            print(f"AM-DEV cache-hit {rid}", flush=True)
            continue
        jobs.append({"spec": spec, "representation_id": rid, "rows_path": str(am_path), "days": list(ELIGIBLE_DAYS)})
    print(f"AM-DEV jobs={len(jobs)}", flush=True)
    for body in _pool(jobs):
        if body.get("ok"):
            _save_json(CACHE / _cache_name(str(body.get("representation_id"))), body)
        got.append(body)
    fail = [b for b in got if not b.get("ok")]
    if fail or len(got) != 9:
        return _integrity(
            "STOP. AM representation LODO failed.",
            extra={"fail": [(b.get("representation_id"), b.get("blocker")) for b in fail]},
        )

    for b in got:
        rid = str(b.get("representation_id") or "")
        prior_fo = _load(LEARN_CACHE / _cache_name(rid)).get("MODEL_FILL_RATE")
        fo = (b.get("FILL_ONLY") or {}).get("SELECTED_FILL_RATE")
        if not _close(fo, prior_fo, PARITY_ABS_TOL):
            print("STOP FILL_ONLY vs prior MODEL_FILL_RATE", rid, fo, prior_fo, flush=True)
            return _integrity(
                "STOP. FILL_ONLY Top3 fill rate did not reproduce prior AM Stage1 fillability OOF.",
                extra={"representation_id": rid, "FILL_ONLY": fo, "PRIOR_MODEL_FILL_RATE": prior_fo},
            )
        ctrl = (b.get("CONTROL") or {}).get("SELECTED_FILL_RATE")
        if not _close(ctrl, pop["am_top3"].get("FILL_RATE"), PARITY_ABS_TOL):
            return _integrity(
                "STOP. CONTROL Top3 fill rate drifted from CURRENT_TOP3_FILL5_RATE.",
                extra={"CONTROL": ctrl, "CURRENT": pop["am_top3"].get("FILL_RATE")},
            )

    leak = sum_integrity(got)
    leak["FUTURE_EVENT_USE_N"] = sum(1 for r in am if r.get("future_event_use"))
    leak["PM_ROWS_USED_N"] = sum(1 for r in am if session_of(r) != "AM")
    print("integrity", leak, flush=True)

    specs = spec_rows(got)
    fill_g = fillability_gate(specs, got)
    qual_g = quality_increment_gate(specs, got)
    cond = cond_sanity(specs)
    decision = decide(fill_gate=fill_g, qual_gate=qual_g, leak=leak)
    if decision.get("CASE") == "E":
        return _integrity(str(decision.get("PRIMARY_FINDING")), extra={"integrity": leak})

    required = {
        "BASE_PARITY": base_parity,
        "REPRESENTATION_N": 9,
        "CONTROL_FILL_RATE": pop["am_top3"].get("FILL_RATE"),
        "FILL_ONLY_FILL_RATE": _med_key(specs, "FILL_ONLY_FILL_RATE"),
        "TWO_STAGE_FILL_RATE": _med_key(specs, "TWO_STAGE_FILL_RATE"),
        "DELTA_FILL_RATE_VS_CONTROL": _med_key(specs, "DELTA_FILL_RATE_VS_CONTROL"),
        "DELTA_FILL_RATE_VS_FILL_ONLY": _med_key(specs, "DELTA_FILL_RATE_VS_FILL_ONLY"),
        "CONTROL_EXEC_U": _med_key(specs, "CONTROL_EXEC_U"),
        "CONTROL_EXEC_D": _med_key(specs, "CONTROL_EXEC_D"),
        "FILL_ONLY_EXEC_U": _med_key(specs, "FILL_ONLY_EXEC_U"),
        "FILL_ONLY_EXEC_D": _med_key(specs, "FILL_ONLY_EXEC_D"),
        "TWO_STAGE_EXEC_U": _med_key(specs, "TWO_STAGE_EXEC_U"),
        "TWO_STAGE_EXEC_D": _med_key(specs, "TWO_STAGE_EXEC_D"),
        "DELTA_EXEC_U_VS_CONTROL": _med_key(specs, "DELTA_EXEC_U_VS_CONTROL"),
        "DELTA_EXEC_D_VS_CONTROL": _med_key(specs, "DELTA_EXEC_D_VS_CONTROL"),
        "DELTA_EXEC_U_VS_FILL_ONLY": _med_key(specs, "DELTA_EXEC_U_VS_FILL_ONLY"),
        "DELTA_EXEC_D_VS_FILL_ONLY": _med_key(specs, "DELTA_EXEC_D_VS_FILL_ONLY"),
        "TWO_STAGE_NE_FILL_ONLY_COHORT_RATE": _med_key(specs, "TWO_STAGE_NE_FILL_ONLY_COHORT_RATE"),
        "TWO_STAGE_FILLABILITY_PASS": fill_g.get("PASS"),
        "STAGE2_QUALITY_INCREMENT_PASS": qual_g.get("PASS"),
        "AM_TWO_STAGE_DEVELOPMENT_PASS": decision.get("AM_TWO_STAGE_DEVELOPMENT_PASS"),
        "U_POSITIVE_REP_N": qual_g.get("U_POSITIVE_REP_N"),
        "D_POSITIVE_REP_N": qual_g.get("D_POSITIVE_REP_N"),
        "U_POSITIVE_DAYS": qual_g.get("U_POSITIVE_DAYS"),
        "U_NEGATIVE_DAYS": qual_g.get("U_NEGATIVE_DAYS"),
        "D_POSITIVE_DAYS": qual_g.get("D_POSITIVE_DAYS"),
        "D_NEGATIVE_DAYS": qual_g.get("D_NEGATIVE_DAYS"),
        "U_EX_BEST_DAY": qual_g.get("U_EX_BEST_DAY"),
        "U_EX_TOP3_DAYS": qual_g.get("U_EX_TOP3_DAYS"),
        "D_EX_BEST_DAY": qual_g.get("D_EX_BEST_DAY"),
        "D_EX_TOP3_DAYS": qual_g.get("D_EX_TOP3_DAYS"),
        "PM_ROWS_USED_N": leak.get("PM_ROWS_USED_N"),
        "FUTURE_EVENT_USE_N": leak.get("FUTURE_EVENT_USE_N"),
        "TARGET_CONTAMINATION_N": leak.get("TARGET_CONTAMINATION_N"),
        "HELDOUT_FIT_LEAK_N": leak.get("HELDOUT_FIT_LEAK_N"),
        "AM_NORMALIZER_HELDOUT_ROW_N": leak.get("AM_NORMALIZER_HELDOUT_ROW_N"),
        "STAGE2_NONFILL_TARGET_TRAIN_N": leak.get("STAGE2_NONFILL_TARGET_TRAIN_N"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "JOIN_MISS_N": miss,
        "COND_SANITY_PASS": cond.get("PASS"),
    }
    print(
        f"CASE={decision.get('CASE')} fill={fill_g.get('PASS')} qual={qual_g.get('PASS')} "
        f"VERDICT={decision.get('VERDICT')}",
        flush=True,
    )
    return write_report(
        required,
        decision=decision,
        extra={
            "population": pop_pack,
            "priors": prior_pack,
            "fillability": {k: v for k, v in fill_g.items() if k != "daily"},
            "quality_increment": {k: v for k, v in qual_g.items() if k not in {"daily_u", "daily_d"}},
            "conditional_sanity": cond,
            "integrity": leak,
        },
        sheets_extra={
            "Parity": kv_rows({**{f"POP_{k}": v for k, v in (pop_pack.get("checks") or {}).items()}, **{f"PRIOR_{k}": v for k, v in (prior_pack.get("checks") or {}).items()}, "BASE_PARITY": True}),
            "Arms": specs,
            "Distinctness": [
                {
                    "representation_id": r.get("representation_id"),
                    "TWO_STAGE_EQ_FILL_ONLY_COHORT_N": r.get("TWO_STAGE_EQ_FILL_ONLY_COHORT_N"),
                    "TWO_STAGE_NE_FILL_ONLY_COHORT_N": r.get("TWO_STAGE_NE_FILL_ONLY_COHORT_N"),
                    "TWO_STAGE_NE_FILL_ONLY_COHORT_RATE": r.get("TWO_STAGE_NE_FILL_ONLY_COHORT_RATE"),
                    "SWAPPED_IN_N": r.get("SWAPPED_IN_N"),
                    "SWAPPED_OUT_N": r.get("SWAPPED_OUT_N"),
                }
                for r in specs
            ],
            "Fillability": kv_rows({k: v for k, v in fill_g.items() if k not in {"daily", "gates"}})
            + [{"scope": "GATE", **(fill_g.get("gates") or {})}],
            "ExecQuality": kv_rows({k: v for k, v in qual_g.items() if k not in {"daily_u", "daily_d", "gates"}}),
            "Conditional": kv_rows(cond),
            "ConsensusDays": [{"scope": "FILL_VS_CONTROL", **r} for r in (fill_g.get("daily") or [])]
            + [{"scope": "EXEC_U_VS_FILL_ONLY", **r} for r in (qual_g.get("daily_u") or [])]
            + [{"scope": "EXEC_D_VS_FILL_ONLY", **r} for r in (qual_g.get("daily_d") or [])],
            "Gates": kv_rows(
                {
                    **{f"FILL_{k}": v for k, v in (fill_g.get("gates") or {}).items()},
                    **{f"QUAL_{k}": v for k, v in (qual_g.get("gates") or {}).items()},
                    "COND_SANITY_PASS": cond.get("PASS"),
                }
            ),
            "Integrity": kv_rows(
                {
                    **leak,
                    "JOIN_MISS_N": miss,
                    "SESSION": SESSION,
                    "RUNTIME_WAIT_SEC": WAIT_SEC,
                    "DEV_WAIT_SEC": DEV_WAIT_SEC,
                    "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
                    "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
                    "PNL_USED": PNL_USED,
                    "EXACT_RAN": EXACT_RAN,
                    "FINAL_MODEL_ADOPTED": FINAL_MODEL_ADOPTED,
                }
            ),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
