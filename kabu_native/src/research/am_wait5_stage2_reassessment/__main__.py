"""Offline AM WAIT5 Stage2 quality-objective reassessment. No Runtime write. No Paper. No Exact."""
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

from research.am_wait5_stage2_reassessment import (
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
    FROZEN_OOF_REPLAY_ONLY,
    HYPERPARAMETER_TUNING,
    MAX_WORKERS,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    PAPER_OPERATED,
    PARITY_ABS_TOL,
    PNL_USED,
    PRIOR_ANALYSIS_ID_REQUIRED,
    PRIOR_VERDICT_REQUIRED,
    REPRESENTATION_N,
    RETRAINING_NEW_FAMILY,
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
    WEIGHT_SEARCH,
)
from research.am_wait5_stage2_reassessment.analyze import aggregate, analyze_representation, decide, freeze_parity
from research.am_wait5_stage2_reassessment.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from research.am_wait5_stage2_reassessment.replay import replay_frozen_oof
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
DEV_REPORT = NATIVE / "results" / "research" / "am_wait5_two_stage_development" / "report.json"
DEV_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_wait5_two_stage_development"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_wait5_stage2_reassessment"
AM_ROWS = DEV_CACHE / "am_rows.json"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str), encoding="utf-8")


def _cache_name(rid: str) -> str:
    return "AM_" + rid.replace("|", "_").replace(" ", "") + "_scored.json"


def _dev_cache_name(rid: str) -> str:
    return "AM_" + rid.replace("|", "_").replace(" ", "") + ".json"


def _pool(jobs: list[dict]) -> list[dict]:
    if not jobs:
        return []
    out = []
    workers = min(MAX_WORKERS, len(jobs))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(replay_frozen_oof, job): job.get("representation_id") for job in jobs}
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, "representation_id": key, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            print(
                f"done replay {body.get('representation_id') or key} ok={body.get('ok')} "
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
                "STAGE1_SHORTLIST_N": STAGE1_SHORTLIST_N,
                "REPRESENTATION_N": REPRESENTATION_N,
                "FROZEN_OOF_REPLAY_ONLY": FROZEN_OOF_REPLAY_ONLY,
                "RETRAINING_NEW_FAMILY": RETRAINING_NEW_FAMILY,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "HYPERPARAMETER_TUNING": HYPERPARAMETER_TUNING,
                "SHORTLIST_SEARCH": SHORTLIST_SEARCH,
                "TOPK_SEARCH": TOPK_SEARCH,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
                "WEIGHT_SEARCH": WEIGHT_SEARCH,
                "PNL_USED": PNL_USED,
                "EXACT_RAN": EXACT_RAN,
                "FINAL_MODEL_ADOPTED": FINAL_MODEL_ADOPTED,
            }
        ),
        "Parity": [{"empty": True}],
        "Swap": [{"empty": True}],
        "SwapQuality": [{"empty": True}],
        "ExecContribution": [{"empty": True}],
        "FillStatus": [{"empty": True}],
        "PredGeometry": [{"empty": True}],
        "ActualGeometry": [{"empty": True}],
        "Oracle": [{"empty": True}],
        "DailyRobustness": [{"empty": True}],
        "Mechanism": [{"empty": True}],
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
                "WEIGHT_SEARCH": WEIGHT_SEARCH,
                "RUNTIME_CANDIDATE_CREATED": RUNTIME_CANDIDATE_CREATED,
                "STRATEGY_CREATED": STRATEGY_CREATED,
                "FINAL_MODEL_ADOPTED": FINAL_MODEL_ADOPTED,
                "FROZEN_OOF_REPLAY_ONLY": FROZEN_OOF_REPLAY_ONLY,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"VERDICT={required.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(
        "STOP. No new objective. No new model. No Exact. No PnL. Runtime WAIT_SEC=1.0. "
        "W5 not adopted. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "AM_STAGE2_REASSESSMENT_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str, extra: dict | None = None) -> int:
    body = {
        "BASE_PARITY": False,
        "SWAPPED_OUT_N": None,
        "SWAPPED_IN_N": None,
        "SWAPPED_OUT_FILL_RATE": None,
        "SWAPPED_IN_FILL_RATE": None,
        "SWAP_FILL_DELTA": None,
        "SWAP_OUT_COND_U": None,
        "SWAP_IN_COND_U": None,
        "DELTA_SWAP_COND_U": None,
        "SWAP_OUT_COND_D": None,
        "SWAP_IN_COND_D": None,
        "DELTA_SWAP_COND_D": None,
        "PRED_U_D_SPEARMAN_MEDIAN": None,
        "ACTUAL_U_D_SPEARMAN_MEDIAN": None,
        "ORACLE_JOINT_NONWORSE_AVAILABLE_RATE": None,
        "SAME_FILL_JOINT_IMPROVEMENT_RATE": None,
        "D_GAIN_MAINLY_FROM_LOWER_FILL_EXPOSURE": None,
        "GENUINE_U_D_SELECTION_TRADEOFF": None,
        "MINRANK_OBJECTIVE_MISIDENTIFIES_JOINT_GOOD_SET": None,
        "STAGE1_SHORTLIST_LIMITS_JOINT_QUALITY": None,
        "PRIMARY_MECHANISM": "INTEGRITY_FAIL",
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "AM_STAGE2_REASSESSMENT_INTEGRITY_FAILED",
        "PRIMARY_FINDING": note,
    }
    if extra:
        body.update(extra)
    return write_report(
        body,
        decision={
            "VERDICT": "AM_STAGE2_REASSESSMENT_INTEGRITY_FAILED",
            "PRIMARY_MECHANISM": "INTEGRITY_FAIL",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": note,
            "note": "STOP. Integrity failed.",
        },
        extra=extra,
    )


def _arm_parity_obs(got: list[dict]) -> dict:
    specs = []
    for b in got:
        fo = b.get("FILL_ONLY") or {}
        ts = b.get("TWO_STAGE") or {}
        dist = b.get("distinctness") or {}
        specs.append(
            {
                "FILL_ONLY_FILL_RATE": fo.get("SELECTED_FILL_RATE"),
                "TWO_STAGE_FILL_RATE": ts.get("SELECTED_FILL_RATE"),
                "DELTA_EXEC_U_VS_FILL_ONLY": None
                if fo.get("EXEC_U") is None or ts.get("EXEC_U") is None
                else float(ts["EXEC_U"]) - float(fo["EXEC_U"]),
                "DELTA_EXEC_D_VS_FILL_ONLY": None
                if fo.get("EXEC_D") is None or ts.get("EXEC_D") is None
                else float(ts["EXEC_D"]) - float(fo["EXEC_D"]),
                "TWO_STAGE_NE_FILL_ONLY_COHORT_RATE": dist.get("TWO_STAGE_NE_FILL_ONLY_COHORT_RATE"),
                "FILL_ONLY_COND_U": fo.get("COND_U_MEAN"),
                "TWO_STAGE_COND_U": ts.get("COND_U_MEAN"),
                "FILL_ONLY_COND_D": fo.get("COND_D_MEAN"),
                "TWO_STAGE_COND_D": ts.get("COND_D_MEAN"),
            }
        )
    return {
        "FILL_ONLY_FILL_RATE": _med_key(specs, "FILL_ONLY_FILL_RATE"),
        "TWO_STAGE_FILL_RATE": _med_key(specs, "TWO_STAGE_FILL_RATE"),
        "DELTA_EXEC_U_VS_FILL_ONLY": _med_key(specs, "DELTA_EXEC_U_VS_FILL_ONLY"),
        "DELTA_EXEC_D_VS_FILL_ONLY": _med_key(specs, "DELTA_EXEC_D_VS_FILL_ONLY"),
        "TWO_STAGE_NE_FILL_ONLY_COHORT_RATE": _med_key(specs, "TWO_STAGE_NE_FILL_ONLY_COHORT_RATE"),
        "FILL_ONLY_COND_U": _med_key(specs, "FILL_ONLY_COND_U"),
        "TWO_STAGE_COND_U": _med_key(specs, "TWO_STAGE_COND_U"),
        "FILL_ONLY_COND_D": _med_key(specs, "FILL_ONLY_COND_D"),
        "TWO_STAGE_COND_D": _med_key(specs, "TWO_STAGE_COND_D"),
    }


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM WAIT5 STAGE2 REASSESSMENT V1", flush=True)
    print("Frozen OOF replay. CONTROL / FILL_ONLY / TWO_STAGE fixed. No Exact. No PnL.", flush=True)

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
    prior = _load(DEV_REPORT)
    prior_req = prior.get("required") or {}
    if str(prior.get("ANALYSIS_ID") or "") != PRIOR_ANALYSIS_ID_REQUIRED:
        return _integrity("STOP. Prior development ANALYSIS_ID mismatch.")
    if str(prior_req.get("VERDICT") or "") != PRIOR_VERDICT_REQUIRED:
        return _integrity(
            f"STOP. Prior development VERDICT must be {PRIOR_VERDICT_REQUIRED}.",
            extra={"prior_verdict": prior_req.get("VERDICT")},
        )
    grid = representation_grid()
    if len(grid) != 9:
        return _integrity("STOP. Representation grid is not 9.")
    if not AM_ROWS.is_file():
        return _integrity("STOP. Frozen development am_rows.json missing.")

    CACHE.mkdir(parents=True, exist_ok=True)
    jobs = []
    got = []
    for spec in grid:
        rid = str(spec.get("representation_id"))
        fp = CACHE / _cache_name(rid)
        saved = _load(fp)
        if (
            saved.get("ok")
            and saved.get("representation_id") == rid
            and saved.get("rows")
            and saved.get("TWO_STAGE")
        ):
            got.append(saved)
            print(f"replay cache-hit {rid} n={len(saved.get('rows') or [])}", flush=True)
            continue
        jobs.append(
            {
                "spec": spec,
                "representation_id": rid,
                "rows_path": str(AM_ROWS),
                "days": list(ELIGIBLE_DAYS),
            }
        )
    print(f"replay jobs={len(jobs)}", flush=True)
    for body in _pool(jobs):
        if body.get("ok"):
            _save_json(CACHE / _cache_name(str(body.get("representation_id"))), body)
        got.append(body)
    fail = [b for b in got if not b.get("ok")]
    if fail or len(got) != 9:
        return _integrity(
            "STOP. Frozen OOF replay failed.",
            extra={"fail": [(b.get("representation_id"), b.get("blocker")) for b in fail]},
        )

    for b in got:
        rid = str(b.get("representation_id") or "")
        prior_arm = _load(DEV_CACHE / _dev_cache_name(rid))
        fo = (b.get("FILL_ONLY") or {}).get("SELECTED_FILL_RATE")
        ts = (b.get("TWO_STAGE") or {}).get("SELECTED_FILL_RATE")
        pfo = (prior_arm.get("FILL_ONLY") or {}).get("SELECTED_FILL_RATE")
        pts = (prior_arm.get("TWO_STAGE") or {}).get("SELECTED_FILL_RATE")
        if not _close(fo, pfo, PARITY_ABS_TOL) or not _close(ts, pts, PARITY_ABS_TOL):
            print("STOP replay vs development arm fill", rid, fo, pfo, ts, pts, flush=True)
            return _integrity(
                "STOP. Replayed FILL_ONLY / TWO_STAGE fill rates did not match frozen development cache.",
                extra={"representation_id": rid, "replay_fo": fo, "dev_fo": pfo, "replay_ts": ts, "dev_ts": pts},
            )

    arm_obs = _arm_parity_obs(got)
    par = freeze_parity(arm_obs)
    print("parity", par.get("ok"), par.get("checks"), flush=True)
    if not par.get("ok"):
        return _integrity(
            "STOP. Frozen development headline parity did not reproduce.",
            extra={"parity": par},
        )

    leak = {
        "PM_ROWS_USED_N": 0,
        "FUTURE_EVENT_USE_N": 0,
        "TARGET_CONTAMINATION_N": 0,
        "HELDOUT_FIT_LEAK_N": 0,
        "AM_NORMALIZER_HELDOUT_ROW_N": 0,
        "STAGE2_NONFILL_TARGET_TRAIN_N": 0,
    }
    for b in got:
        rec = b.get("integrity") or {}
        for k in leak:
            leak[k] += int(rec.get(k) or 0)
    am_rows = list((_load(AM_ROWS).get("rows") or []))
    leak["PM_ROWS_USED_N"] = sum(1 for r in am_rows if session_of(r) != "AM")
    leak["FUTURE_EVENT_USE_N"] = sum(1 for r in am_rows if r.get("future_event_use"))
    print("integrity", leak, flush=True)
    if any(int(leak.get(k) or 0) != 0 for k in leak):
        return _integrity("STOP. Isolation integrity failed.", extra={"integrity": leak})

    diag_rows = []
    for b in got:
        rec = analyze_representation(list(b.get("rows") or []), list(ELIGIBLE_DAYS))
        rec["representation_id"] = b.get("representation_id")
        rec["feature_set"] = b.get("feature_set")
        rec["normalization"] = b.get("normalization")
        diag_rows.append(rec)
        print(
            f"diag {rec.get('representation_id')} "
            f"out_fr={rec.get('SWAPPED_OUT_FILL_RATE')} in_fr={rec.get('SWAPPED_IN_FILL_RATE')} "
            f"same_fill={rec.get('SAME_FILL_JOINT_IMPROVEMENT_RATE')} "
            f"oracle={rec.get('ORACLE_JOINT_NONWORSE_AVAILABLE_RATE')}",
            flush=True,
        )

    agg = aggregate(diag_rows)
    decision = decide(agg, leak=leak, parity_ok=True)
    flags = agg.get("flags") or {}
    h = agg.get("headline") or {}
    required = {
        "BASE_PARITY": True,
        "SWAPPED_OUT_N": h.get("SWAPPED_OUT_N"),
        "SWAPPED_IN_N": h.get("SWAPPED_IN_N"),
        "SWAPPED_OUT_FILL_RATE": h.get("SWAPPED_OUT_FILL_RATE"),
        "SWAPPED_IN_FILL_RATE": h.get("SWAPPED_IN_FILL_RATE"),
        "SWAP_FILL_DELTA": h.get("SWAP_FILL_DELTA"),
        "SWAP_OUT_COND_U": h.get("SWAP_OUT_COND_U"),
        "SWAP_IN_COND_U": h.get("SWAP_IN_COND_U"),
        "DELTA_SWAP_COND_U": h.get("DELTA_SWAP_COND_U"),
        "SWAP_OUT_COND_D": h.get("SWAP_OUT_COND_D"),
        "SWAP_IN_COND_D": h.get("SWAP_IN_COND_D"),
        "DELTA_SWAP_COND_D": h.get("DELTA_SWAP_COND_D"),
        "PRED_U_D_SPEARMAN_MEDIAN": h.get("PRED_U_D_SPEARMAN_MEDIAN"),
        "ACTUAL_U_D_SPEARMAN_MEDIAN": h.get("ACTUAL_U_D_SPEARMAN_MEDIAN"),
        "ORACLE_JOINT_NONWORSE_AVAILABLE_RATE": h.get("ORACLE_JOINT_NONWORSE_AVAILABLE_RATE"),
        "SAME_FILL_JOINT_IMPROVEMENT_RATE": h.get("SAME_FILL_JOINT_IMPROVEMENT_RATE"),
        "D_GAIN_MAINLY_FROM_LOWER_FILL_EXPOSURE": flags.get("D_GAIN_MAINLY_FROM_LOWER_FILL_EXPOSURE"),
        "GENUINE_U_D_SELECTION_TRADEOFF": flags.get("GENUINE_U_D_SELECTION_TRADEOFF"),
        "MINRANK_OBJECTIVE_MISIDENTIFIES_JOINT_GOOD_SET": flags.get("MINRANK_OBJECTIVE_MISIDENTIFIES_JOINT_GOOD_SET"),
        "STAGE1_SHORTLIST_LIMITS_JOINT_QUALITY": flags.get("STAGE1_SHORTLIST_LIMITS_JOINT_QUALITY"),
        "PRIMARY_MECHANISM": decision.get("PRIMARY_MECHANISM"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "PM_ROWS_USED_N": leak.get("PM_ROWS_USED_N"),
        "FUTURE_EVENT_USE_N": leak.get("FUTURE_EVENT_USE_N"),
        "TARGET_CONTAMINATION_N": leak.get("TARGET_CONTAMINATION_N"),
        "HELDOUT_FIT_LEAK_N": leak.get("HELDOUT_FIT_LEAK_N"),
        "STAGE2_NONFILL_TARGET_TRAIN_N": leak.get("STAGE2_NONFILL_TARGET_TRAIN_N"),
        "EXEC_D_GAIN_WITH_NONFILL": h.get("EXEC_D_GAIN_WITH_NONFILL"),
        "FILLED_ONLY_D_GAIN": h.get("FILLED_ONLY_D_GAIN"),
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
    }
    slim_diag = []
    for r in diag_rows:
        rec = {k: v for k, v in r.items() if k != "daily"}
        slim_diag.append(rec)
    daily = agg.get("daily") or {}
    print(
        f"VERDICT={decision.get('VERDICT')} mech={decision.get('PRIMARY_MECHANISM')} "
        f"same_fill={h.get('SAME_FILL_JOINT_IMPROVEMENT_RATE')} "
        f"in_fr={h.get('SWAPPED_IN_FILL_RATE')} out_fr={h.get('SWAPPED_OUT_FILL_RATE')}",
        flush=True,
    )
    return write_report(
        required,
        decision=decision,
        extra={
            "parity": par,
            "integrity": leak,
            "aggregate": {
                k: v
                for k, v in agg.items()
                if k != "daily"
            },
            "daily_stats": {
                "DELTA_SWAP_COND_U_STATS": daily.get("DELTA_SWAP_COND_U_STATS"),
                "DELTA_SWAP_COND_D_STATS": daily.get("DELTA_SWAP_COND_D_STATS"),
                "SWAP_FILL_DELTA_STATS": daily.get("SWAP_FILL_DELTA_STATS"),
            },
        },
        sheets_extra={
            "Parity": kv_rows(par.get("checks") or {}) + kv_rows({f"OBS_{k}": v for k, v in (par.get("observed") or {}).items()}),
            "Swap": slim_diag,
            "SwapQuality": [
                {
                    "representation_id": r.get("representation_id"),
                    "SWAP_OUT_COND_U": r.get("SWAP_OUT_COND_U"),
                    "SWAP_IN_COND_U": r.get("SWAP_IN_COND_U"),
                    "DELTA_SWAP_COND_U": r.get("DELTA_SWAP_COND_U"),
                    "SWAP_OUT_COND_D": r.get("SWAP_OUT_COND_D"),
                    "SWAP_IN_COND_D": r.get("SWAP_IN_COND_D"),
                    "DELTA_SWAP_COND_D": r.get("DELTA_SWAP_COND_D"),
                }
                for r in slim_diag
            ],
            "ExecContribution": [
                {
                    "representation_id": r.get("representation_id"),
                    "SWAP_EXEC_U_OUT": r.get("SWAP_EXEC_U_OUT"),
                    "SWAP_EXEC_U_IN": r.get("SWAP_EXEC_U_IN"),
                    "SWAP_EXEC_D_OUT": r.get("SWAP_EXEC_D_OUT"),
                    "SWAP_EXEC_D_IN": r.get("SWAP_EXEC_D_IN"),
                    "EXEC_D_GAIN_WITH_NONFILL": r.get("EXEC_D_GAIN_WITH_NONFILL"),
                    "FILLED_ONLY_D_GAIN": r.get("FILLED_ONLY_D_GAIN"),
                    "SWAPPED_OUT_FILL_RATE": r.get("SWAPPED_OUT_FILL_RATE"),
                    "SWAPPED_IN_FILL_RATE": r.get("SWAPPED_IN_FILL_RATE"),
                }
                for r in slim_diag
            ],
            "FillStatus": [
                {
                    "representation_id": r.get("representation_id"),
                    "COHORT_SWAP_FILL_LOST_N": r.get("COHORT_SWAP_FILL_LOST_N"),
                    "COHORT_SWAP_FILL_GAINED_N": r.get("COHORT_SWAP_FILL_GAINED_N"),
                    "COHORT_SWAP_FILL_EQUAL_N": r.get("COHORT_SWAP_FILL_EQUAL_N"),
                    "PATTERN_OUT_FILL_IN_FILL_N": r.get("PATTERN_OUT_FILL_IN_FILL_N"),
                    "PATTERN_OUT_FILL_IN_NONFILL_N": r.get("PATTERN_OUT_FILL_IN_NONFILL_N"),
                    "PATTERN_OUT_NONFILL_IN_FILL_N": r.get("PATTERN_OUT_NONFILL_IN_FILL_N"),
                    "PATTERN_OUT_NONFILL_IN_NONFILL_N": r.get("PATTERN_OUT_NONFILL_IN_NONFILL_N"),
                    "SWAP_FILL_DELTA": r.get("SWAP_FILL_DELTA"),
                }
                for r in slim_diag
            ],
            "PredGeometry": [
                {
                    "representation_id": r.get("representation_id"),
                    "PRED_U_D_SPEARMAN_MEAN": r.get("PRED_U_D_SPEARMAN_MEAN"),
                    "PRED_U_D_SPEARMAN_MEDIAN": r.get("PRED_U_D_SPEARMAN_MEDIAN"),
                    "PRED_U_D_POS_COHORT_N": r.get("PRED_U_D_POS_COHORT_N"),
                    "PRED_U_D_NEG_COHORT_N": r.get("PRED_U_D_NEG_COHORT_N"),
                    "PRED_U_D_EVAL_COHORT_N": r.get("PRED_U_D_EVAL_COHORT_N"),
                }
                for r in slim_diag
            ],
            "ActualGeometry": [
                {
                    "representation_id": r.get("representation_id"),
                    "ACTUAL_U_D_SPEARMAN_MEAN": r.get("ACTUAL_U_D_SPEARMAN_MEAN"),
                    "ACTUAL_U_D_SPEARMAN_MEDIAN": r.get("ACTUAL_U_D_SPEARMAN_MEDIAN"),
                    "ACTUAL_U_D_POS_COHORT_N": r.get("ACTUAL_U_D_POS_COHORT_N"),
                    "ACTUAL_U_D_NEG_COHORT_N": r.get("ACTUAL_U_D_NEG_COHORT_N"),
                    "ACTUAL_U_D_EVAL_COHORT_N": r.get("ACTUAL_U_D_EVAL_COHORT_N"),
                }
                for r in slim_diag
            ],
            "Oracle": [
                {
                    "representation_id": r.get("representation_id"),
                    "ORACLE_JOINT_NONWORSE_AVAILABLE_COHORT_N": r.get("ORACLE_JOINT_NONWORSE_AVAILABLE_COHORT_N"),
                    "ORACLE_JOINT_NONWORSE_AVAILABLE_RATE": r.get("ORACLE_JOINT_NONWORSE_AVAILABLE_RATE"),
                    "SAME_FILL_JOINT_IMPROVEMENT_COHORT_N": r.get("SAME_FILL_JOINT_IMPROVEMENT_COHORT_N"),
                    "SAME_FILL_JOINT_IMPROVEMENT_RATE": r.get("SAME_FILL_JOINT_IMPROVEMENT_RATE"),
                    "diagnostic_only": True,
                }
                for r in slim_diag
            ],
            "DailyRobustness": (
                [{"scope": "DELTA_SWAP_COND_U", **r} for r in (daily.get("DELTA_SWAP_COND_U") or [])]
                + [{"scope": "DELTA_SWAP_COND_D", **r} for r in (daily.get("DELTA_SWAP_COND_D") or [])]
                + [{"scope": "SWAP_FILL_DELTA", **r} for r in (daily.get("SWAP_FILL_DELTA") or [])]
            ),
            "Mechanism": kv_rows(
                {
                    **flags,
                    **{f"D_ROBUST_{k}": v for k, v in ((agg.get("filled_only_d_robust") or {}).get("gates") or {}).items()},
                    **{f"U_DOWN_{k}": v for k, v in ((agg.get("filled_only_u_worsens_robust") or {}).get("gates") or {}).items()},
                    "swap_in_fill_lt_out": agg.get("swap_in_fill_lt_out"),
                    "same_fill_frequent": agg.get("same_fill_frequent"),
                    "same_fill_rare": agg.get("same_fill_rare"),
                    "PRIMARY_MECHANISM": decision.get("PRIMARY_MECHANISM"),
                    "VERDICT": decision.get("VERDICT"),
                    "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
                }
            ),
            "Integrity": kv_rows(
                {
                    **leak,
                    "SESSION": SESSION,
                    "RUNTIME_WAIT_SEC": WAIT_SEC,
                    "DEV_WAIT_SEC": DEV_WAIT_SEC,
                    "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
                    "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
                    "PNL_USED": PNL_USED,
                    "EXACT_RAN": EXACT_RAN,
                    "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                    "FROZEN_OOF_REPLAY_ONLY": FROZEN_OOF_REPLAY_ONLY,
                    "FINAL_MODEL_ADOPTED": FINAL_MODEL_ADOPTED,
                }
            ),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
