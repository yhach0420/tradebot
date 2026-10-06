"""Offline AM ENTRY / execution-policy coupling. Frozen OOF. No Runtime write. No Paper."""
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

from research.am_direct_exec_u_development.oof import process_am_direct
from research.am_entry_execution_policy_coupling import (
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
    MODEL_REFIT_N,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    ORACLE_SELECTION_USE_N,
    PAPER_OPERATED,
    PARITY_ABS_TOL,
    PNL_USED,
    PRIOR_DEVELOPMENT_ID,
    PRIOR_DEVELOPMENT_VERDICT,
    REPRESENTATION_N,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    SELECTION_REOPTIMIZATION_N,
    SESSION,
    STAGE1_ALLOWED,
    STAGE2_ALLOWED,
    STRATEGY_CREATED,
    TRUE_OOS,
    WAIT_POLICY_ADOPTED,
    WAIT_SEARCH_N,
    W5_RUNTIME_ADOPTED,
    W10_ADOPTED,
)
from research.am_entry_execution_policy_coupling.analyze import daily_consensus, decide, freeze_parity, spec_rows
from research.am_entry_execution_policy_coupling.eval_wait import attach_waits, evaluate_rep
from research.am_entry_execution_policy_coupling.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from research.am_wait5_two_stage_development.analyze import _med_key
from research.canonical_entry_performance_rebase.analyze import session_of, wf_index
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
PRIOR = NATIVE / "results" / "research" / "am_direct_exec_u_development" / "report.json"
DEV_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_wait5_two_stage_development"
OOF_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_wait5_stage2_reassessment"
DIRECT_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_direct_exec_u_development"
WAIT_CACHE = NATIVE / "results" / "research" / "_work_cache" / "passive_wait_policy_reassessment"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_entry_execution_policy_coupling"


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
        futs = {ex.submit(process_am_direct, job): job.get("representation_id") for job in jobs}
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, "representation_id": key, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            print(
                f"done RECOVER {body.get('representation_id') or key} ok={body.get('ok')} "
                f"fo={(body.get('FILL_ONLY') or {}).get('SELECTED_FILL_RATE')} "
                f"du={(body.get('DIRECT_EXEC_U') or {}).get('SELECTED_FILL_RATE')} "
                f"sel={bool((body.get('selected') or {}).get('DIRECT_EXEC_U'))} blocker={body.get('blocker')}",
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
                "FROZEN_OOF_REPLAY_ONLY": FROZEN_OOF_REPLAY_ONLY,
                "MODEL_REFIT_N": MODEL_REFIT_N,
                "W10_ADOPTED": W10_ADOPTED,
                "WAIT_SEARCH_N": WAIT_SEARCH_N,
                "STAGE1_ALLOWED": STAGE1_ALLOWED,
                "STAGE2_ALLOWED": STAGE2_ALLOWED,
                "PNL_USED": PNL_USED,
                "EXACT_RAN": EXACT_RAN,
            }
        ),
        "Parity": [{"empty": True}],
        "SelectionSets": [{"empty": True}],
        "PFill": [{"empty": True}],
        "W5Cause": [{"empty": True}],
        "Recovery": [{"empty": True}],
        "WaitCounterfactual": [{"empty": True}],
        "RecoveredQuality": [{"empty": True}],
        "FillLoss": [{"empty": True}],
        "Coupling": [{"empty": True}],
        "DayRobustness": [{"empty": True}],
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
                "FEATURE_SEARCH": FEATURE_SEARCH,
                "PNL_USED": PNL_USED,
                "BEST_REPRESENTATION_ADOPTED": BEST_REPRESENTATION_ADOPTED,
                "HYPERPARAMETER_TUNING": HYPERPARAMETER_TUNING,
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
        "STOP. No wait change. No model change. No Exact. No PnL. Runtime WAIT_SEC=1.0. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "AM_ENTRY_EXECUTION_COUPLING_INTEGRITY_FAILED"
    return 2 if fail else 0


def _empty_required(note: str, extra: dict | None = None) -> dict:
    body = {
        "BASE_PARITY": False,
        "DIRECT_ONLY_P_FILL_MEDIAN": None,
        "FILL_ONLY_ONLY_P_FILL_MEDIAN": None,
        "DIRECT_ONLY_W5_NONFILL_N": None,
        "DIRECT_ONLY_NO_VALID_BOARD_N": None,
        "DIRECT_ONLY_NO_CROSS_N": None,
        "DIRECT_ONLY_RECOVER_RATE_W10": None,
        "DIRECT_ONLY_NEVER_CROSS_10S_RATE": None,
        "FILL_ONLY_FILL_RATE_W1": None,
        "FILL_ONLY_FILL_RATE_W2": None,
        "FILL_ONLY_FILL_RATE_W5": None,
        "FILL_ONLY_FILL_RATE_W10": None,
        "DIRECT_FILL_RATE_W1": None,
        "DIRECT_FILL_RATE_W2": None,
        "DIRECT_FILL_RATE_W5": None,
        "DIRECT_FILL_RATE_W10": None,
        "DIRECT_MINUS_FILL_ONLY_W10": None,
        "DIRECT_EXEC_U_DELTA_W10": None,
        "DIRECT_EXEC_D_DELTA_W10": None,
        "DIRECT_RECOVERED_N": None,
        "RECOVERED_COND_U": None,
        "RECOVERED_COND_D": None,
        "RECOVERED_TIME_TO_FILL_MEDIAN": None,
        "FILL_LOSS_LATE_RECOVERABLE_N": None,
        "FILL_LOSS_NEVER_CROSS_N": None,
        "FILL_LOSS_NO_VALID_BOARD_N": None,
        "PRIMARY_MECHANISM": "INTEGRITY_FAIL",
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "AM_ENTRY_EXECUTION_COUPLING_INTEGRITY_FAILED",
        "PRIMARY_FINDING": note,
        "MODEL_REFIT_N": MODEL_REFIT_N,
        "PM_ROWS_USED_N": None,
        "TARGET_CONTAMINATION_N": None,
        "HELDOUT_FIT_LEAK_N": None,
        "SELECTION_REOPTIMIZATION_N": SELECTION_REOPTIMIZATION_N,
        "WAIT_SEARCH_N": WAIT_SEARCH_N,
        "ORACLE_SELECTION_USE_N": ORACLE_SELECTION_USE_N,
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
            "VERDICT": "AM_ENTRY_EXECUTION_COUPLING_INTEGRITY_FAILED",
            "PRIMARY_FINDING": note,
            "note": "CASE E. STOP. Integrity failed.",
        },
        extra=extra,
    )


def _harvest_index() -> tuple[dict[str, dict], int]:
    harvest = []
    miss_days = 0
    for day in ELIGIBLE_DAYS:
        fp = WAIT_CACHE / f"{day}_WAIT.json"
        saved = _load(fp)
        if not (saved.get("ok") and saved.get("date") == day and saved.get("rows")):
            miss_days += 1
            continue
        harvest.extend(list(saved.get("rows") or []))
    return wf_index(harvest), miss_days


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM ENTRY EXECUTION POLICY COUPLING V1", flush=True)
    print("Frozen DIRECT/FILL_ONLY selection. Frozen W1/W2/W5/W10. No new model. No Exact.", flush=True)

    if abs(float(WAIT_SEC) - 1.0) > 1e-12:
        print("STOP WAIT_SEC drift", flush=True)
        return 2
    if abs(float(DEV_WAIT_SEC) - 5.0) > 1e-12:
        print("STOP DEV_WAIT_SEC drift", flush=True)
        return 2
    if W10_ADOPTED or int(WAIT_SEARCH_N) != 0 or STAGE1_ALLOWED or STAGE2_ALLOWED:
        print("STOP forbidden search/adopt flags", flush=True)
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
    if str(prior.get("ANALYSIS_ID") or "") != PRIOR_DEVELOPMENT_ID or str(preg.get("VERDICT") or "") != PRIOR_DEVELOPMENT_VERDICT:
        return _integrity(
            f"STOP. Prior {PRIOR_DEVELOPMENT_ID} / {PRIOR_DEVELOPMENT_VERDICT} required.",
            extra={"prior_id": prior.get("ANALYSIS_ID"), "prior_verdict": preg.get("VERDICT")},
        )

    grid = representation_grid()
    if len(grid) != 9:
        return _integrity("STOP. Representation grid is not 9.")
    rows_path = DIRECT_CACHE / "am_rows.json"
    if not rows_path.is_file():
        return _integrity("STOP. Frozen AM DIRECT rows cache missing.")

    harvest_by, miss_days = _harvest_index()
    if miss_days != 0 or not harvest_by:
        return _integrity("STOP. Frozen WAIT harvest cache missing.", extra={"WAIT_MISS_DAYS": miss_days})

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
            and (saved.get("selected") or {}).get("FILL_ONLY")
            and (saved.get("selected") or {}).get("DIRECT_EXEC_U")
        ):
            got.append(saved)
            print(f"RECOVER cache-hit {rid}", flush=True)
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
                "keep_selected": True,
            }
        )
    print(f"RECOVER jobs={len(jobs)}", flush=True)
    for body in _pool(jobs):
        if body.get("ok") and (body.get("selected") or {}).get("DIRECT_EXEC_U"):
            _save_json(CACHE / _cache_name(str(body.get("representation_id"))), body)
        got.append(body)
    fail = [b for b in got if not b.get("ok") or not (b.get("selected") or {}).get("DIRECT_EXEC_U")]
    if fail or len(got) != 9:
        return _integrity(
            "STOP. Frozen DIRECT OOF selection recover failed.",
            extra={"fail": [(b.get("representation_id"), b.get("blocker")) for b in fail]},
        )
    got.sort(key=lambda b: str(b.get("representation_id") or ""))

    leak = {
        "MODEL_REFIT_N": int(MODEL_REFIT_N),
        "PM_ROWS_USED_N": 0,
        "TARGET_CONTAMINATION_N": 0,
        "HELDOUT_FIT_LEAK_N": 0,
        "SELECTION_REOPTIMIZATION_N": int(SELECTION_REOPTIMIZATION_N),
        "WAIT_SEARCH_N": int(WAIT_SEARCH_N),
        "ORACLE_SELECTION_USE_N": int(ORACLE_SELECTION_USE_N),
        "Y_W5_MISMATCH_N": 0,
        "WAIT_JOIN_MISS_N": 0,
        "FUTURE_EVENT_USE_N": 0,
    }
    bodies = []
    for b in got:
        rid = str(b.get("representation_id") or "")
        prior_arm = _load(DIRECT_CACHE / _cache_name(rid))
        fo = (b.get("FILL_ONLY") or {}).get("SELECTED_FILL_RATE")
        du = (b.get("DIRECT_EXEC_U") or {}).get("SELECTED_FILL_RATE")
        pfo = (prior_arm.get("FILL_ONLY") or {}).get("SELECTED_FILL_RATE")
        pdu = (prior_arm.get("DIRECT_EXEC_U") or {}).get("SELECTED_FILL_RATE")
        if not _close(fo, pfo, PARITY_ABS_TOL) or not _close(du, pdu, PARITY_ABS_TOL):
            return _integrity(
                "STOP. Recovered FILL_ONLY / DIRECT W5 fill did not match frozen development cache.",
                extra={"representation_id": rid, "fo": fo, "dev_fo": pfo, "du": du, "dev_du": pdu},
            )
        rec_leak = b.get("integrity") or {}
        leak["TARGET_CONTAMINATION_N"] += int(rec_leak.get("TARGET_CONTAMINATION_N") or 0)
        leak["HELDOUT_FIT_LEAK_N"] += int(rec_leak.get("HELDOUT_FIT_LEAK_N") or 0)
        leak["PM_ROWS_USED_N"] += int(rec_leak.get("PM_ROWS_USED_N") or 0)
        leak["FUTURE_EVENT_USE_N"] += int(rec_leak.get("FUTURE_EVENT_USE_N") or 0)
        sel = b.get("selected") or {}
        fo_rows = list(sel.get("FILL_ONLY") or [])
        du_rows = list(sel.get("DIRECT_EXEC_U") or [])
        leak["WAIT_JOIN_MISS_N"] += attach_waits(fo_rows, harvest_by)
        leak["WAIT_JOIN_MISS_N"] += attach_waits(du_rows, harvest_by)
        leak["PM_ROWS_USED_N"] += sum(1 for r in fo_rows + du_rows if session_of(r) != "AM")
        ev = evaluate_rep(fo_rows, du_rows, list(ELIGIBLE_DAYS))
        leak["Y_W5_MISMATCH_N"] += int(ev.get("Y_W5_MISMATCH_N") or 0)
        w5_fo = ((ev.get("waits") or {}).get("W5") or {}).get("FILL_ONLY") or {}
        w5_du = ((ev.get("waits") or {}).get("W5") or {}).get("DIRECT_EXEC_U") or {}
        if not _close(w5_fo.get("SELECTED_FILL_RATE"), fo, PARITY_ABS_TOL) or not _close(
            w5_du.get("SELECTED_FILL_RATE"), du, PARITY_ABS_TOL
        ):
            return _integrity(
                "STOP. Harvest W5 WOULD_FILL did not match frozen Y_FILL5 selection fill.",
                extra={
                    "representation_id": rid,
                    "harvest_fo": w5_fo.get("SELECTED_FILL_RATE"),
                    "oof_fo": fo,
                    "harvest_du": w5_du.get("SELECTED_FILL_RATE"),
                    "oof_du": du,
                },
            )
        bodies.append(
            {
                "ok": True,
                "representation_id": rid,
                "feature_set": b.get("feature_set"),
                "normalization": b.get("normalization"),
                "eval": ev,
                "FILL_ONLY": b.get("FILL_ONLY"),
                "DIRECT_EXEC_U": b.get("DIRECT_EXEC_U"),
            }
        )
        print(
            f"eval {rid} fo5={w5_fo.get('SELECTED_FILL_RATE')} du5={w5_du.get('SELECTED_FILL_RATE')} "
            f"donly={ev.get('DIRECT_ONLY_N')} rec={((ev.get('recover') or {}).get('DIRECT_ONLY') or {}).get('RECOVER_RATE')}",
            flush=True,
        )

    specs = spec_rows(bodies)
    obs = {
        "FILL_ONLY_FILL_RATE_W5": _med_key(specs, "FILL_ONLY_FILL_RATE_W5"),
        "DIRECT_FILL_RATE_W5": _med_key(specs, "DIRECT_FILL_RATE_W5"),
        "DELTA_FILL_W5": _med_key(specs, "DELTA_FILL_W5"),
        "FILL_ONLY_EXEC_U_W5": _med_key(specs, "FILL_ONLY_EXEC_U_W5"),
        "DIRECT_EXEC_U_W5": _med_key(specs, "DIRECT_EXEC_U_W5"),
        "DELTA_EXEC_U_W5": _med_key(specs, "DELTA_EXEC_U_W5"),
        "FILL_ONLY_EXEC_D_W5": _med_key(specs, "FILL_ONLY_EXEC_D_W5"),
        "DIRECT_EXEC_D_W5": _med_key(specs, "DIRECT_EXEC_D_W5"),
        "DIRECT_NE_FILL_ONLY_COHORT_RATE": _med_key(specs, "DIRECT_NE_FILL_ONLY_COHORT_RATE"),
    }
    pack = freeze_parity(obs)
    print("parity", pack.get("ok"), pack.get("checks"), flush=True)
    if not pack.get("ok"):
        return _integrity("STOP. Frozen DIRECT / FILL_ONLY W5 parity did not reproduce.", extra={"parity": pack})

    rec_daily, rec_st = daily_consensus(bodies, "DIRECT_FILL_RECOVERY_W5_W10")
    fd5_daily, fd5_st = daily_consensus(bodies, "FILL_DELTA_W5")
    fd10_daily, fd10_st = daily_consensus(bodies, "FILL_DELTA_W10")
    eu5_daily, eu5_st = daily_consensus(bodies, "EXEC_U_DELTA_W5")
    eu10_daily, eu10_st = daily_consensus(bodies, "EXEC_U_DELTA_W10")
    ed5_daily, ed5_st = daily_consensus(bodies, "EXEC_D_DELTA_W5")
    ed10_daily, ed10_st = daily_consensus(bodies, "EXEC_D_DELTA_W10")

    decision = decide(specs, leak)
    if decision.get("CASE") == "E":
        return _integrity(str(decision.get("PRIMARY_FINDING")), extra={"integrity": leak})

    required = {
        "BASE_PARITY": True,
        "DIRECT_ONLY_P_FILL_MEDIAN": _med_key(specs, "DIRECT_ONLY_P_FILL_MEDIAN"),
        "FILL_ONLY_ONLY_P_FILL_MEDIAN": _med_key(specs, "FILL_ONLY_ONLY_P_FILL_MEDIAN"),
        "DIRECT_ONLY_W5_NONFILL_N": _med_key(specs, "DIRECT_ONLY_W5_NONFILL_N"),
        "DIRECT_ONLY_NO_VALID_BOARD_N": _med_key(specs, "DIRECT_ONLY_NO_VALID_BOARD_N"),
        "DIRECT_ONLY_NO_CROSS_N": _med_key(specs, "DIRECT_ONLY_NO_CROSS_N"),
        "DIRECT_ONLY_RECOVER_RATE_W10": _med_key(specs, "DIRECT_ONLY_RECOVER_RATE_W10"),
        "DIRECT_ONLY_NEVER_CROSS_10S_RATE": _med_key(specs, "DIRECT_ONLY_NEVER_CROSS_10S_RATE"),
        "FILL_ONLY_FILL_RATE_W1": _med_key(specs, "FILL_ONLY_FILL_RATE_W1"),
        "FILL_ONLY_FILL_RATE_W2": _med_key(specs, "FILL_ONLY_FILL_RATE_W2"),
        "FILL_ONLY_FILL_RATE_W5": _med_key(specs, "FILL_ONLY_FILL_RATE_W5"),
        "FILL_ONLY_FILL_RATE_W10": _med_key(specs, "FILL_ONLY_FILL_RATE_W10"),
        "DIRECT_FILL_RATE_W1": _med_key(specs, "DIRECT_FILL_RATE_W1"),
        "DIRECT_FILL_RATE_W2": _med_key(specs, "DIRECT_FILL_RATE_W2"),
        "DIRECT_FILL_RATE_W5": _med_key(specs, "DIRECT_FILL_RATE_W5"),
        "DIRECT_FILL_RATE_W10": _med_key(specs, "DIRECT_FILL_RATE_W10"),
        "DIRECT_MINUS_FILL_ONLY_W10": _med_key(specs, "DIRECT_MINUS_FILL_ONLY_W10"),
        "DIRECT_EXEC_U_DELTA_W10": _med_key(specs, "DIRECT_EXEC_U_DELTA_W10"),
        "DIRECT_EXEC_D_DELTA_W10": _med_key(specs, "DIRECT_EXEC_D_DELTA_W10"),
        "DIRECT_RECOVERED_N": _med_key(specs, "DIRECT_RECOVERED_N"),
        "RECOVERED_COND_U": _med_key(specs, "RECOVERED_COND_U"),
        "RECOVERED_COND_D": _med_key(specs, "RECOVERED_COND_D"),
        "RECOVERED_TIME_TO_FILL_MEDIAN": _med_key(specs, "RECOVERED_TIME_TO_FILL_MEDIAN"),
        "FILL_LOSS_LATE_RECOVERABLE_N": _med_key(specs, "FILL_LOSS_LATE_RECOVERABLE_N"),
        "FILL_LOSS_NEVER_CROSS_N": _med_key(specs, "FILL_LOSS_NEVER_CROSS_N"),
        "FILL_LOSS_NO_VALID_BOARD_N": _med_key(specs, "FILL_LOSS_NO_VALID_BOARD_N"),
        "PRIMARY_MECHANISM": decision.get("PRIMARY_MECHANISM"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "DELTA_P_FILL": _med_key(specs, "DELTA_P_FILL"),
        "COMMON_N": _med_key(specs, "COMMON_N"),
        "FILL_ONLY_ONLY_N": _med_key(specs, "FILL_ONLY_ONLY_N"),
        "DIRECT_ONLY_N": _med_key(specs, "DIRECT_ONLY_N"),
        "DIRECT_MINUS_FILL_ONLY_W1": _med_key(specs, "DIRECT_MINUS_FILL_ONLY_W1"),
        "DIRECT_MINUS_FILL_ONLY_W2": _med_key(specs, "DIRECT_MINUS_FILL_ONLY_W2"),
        "DIRECT_MINUS_FILL_ONLY_W5": _med_key(specs, "DIRECT_MINUS_FILL_ONLY_W5"),
        "DIRECT_EXEC_U_DELTA_W5": _med_key(specs, "DIRECT_EXEC_U_DELTA_W5"),
        "DIRECT_EXEC_D_DELTA_W5": _med_key(specs, "DIRECT_EXEC_D_DELTA_W5"),
        "RECOVERED_TIME_TO_FILL_P90": _med_key(specs, "RECOVERED_TIME_TO_FILL_P90"),
        "FILL_ONLY_ONLY_RECOVER_RATE": _med_key(specs, "FILL_ONLY_ONLY_RECOVER_RATE"),
        "MODEL_REFIT_N": leak.get("MODEL_REFIT_N"),
        "PM_ROWS_USED_N": leak.get("PM_ROWS_USED_N"),
        "TARGET_CONTAMINATION_N": leak.get("TARGET_CONTAMINATION_N"),
        "HELDOUT_FIT_LEAK_N": leak.get("HELDOUT_FIT_LEAK_N"),
        "SELECTION_REOPTIMIZATION_N": leak.get("SELECTION_REOPTIMIZATION_N"),
        "WAIT_SEARCH_N": leak.get("WAIT_SEARCH_N"),
        "ORACLE_SELECTION_USE_N": leak.get("ORACLE_SELECTION_USE_N"),
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
        "REPRESENTATION_N": REPRESENTATION_N,
    }
    print(
        f"CASE={decision.get('CASE')} recover={decision.get('recover_rate')} "
        f"inacc={decision.get('inaccessible_rate')} VERDICT={decision.get('VERDICT')}",
        flush=True,
    )
    day_sheet = (
        [{"scope": "DIRECT_FILL_RECOVERY_W5_W10", **r} for r in rec_daily]
        + [{"scope": "FILL_DELTA_W5", **r} for r in fd5_daily]
        + [{"scope": "FILL_DELTA_W10", **r} for r in fd10_daily]
        + [{"scope": "EXEC_U_DELTA_W5", **r} for r in eu5_daily]
        + [{"scope": "EXEC_U_DELTA_W10", **r} for r in eu10_daily]
        + [{"scope": "EXEC_D_DELTA_W5", **r} for r in ed5_daily]
        + [{"scope": "EXEC_D_DELTA_W10", **r} for r in ed10_daily]
    )
    return write_report(
        required,
        decision=decision,
        extra={
            "parity": pack,
            "integrity": leak,
            "daily_stats": {
                "DIRECT_FILL_RECOVERY_W5_W10": rec_st,
                "FILL_DELTA_W5": fd5_st,
                "FILL_DELTA_W10": fd10_st,
                "EXEC_U_DELTA_W5": eu5_st,
                "EXEC_U_DELTA_W10": eu10_st,
                "EXEC_D_DELTA_W5": ed5_st,
                "EXEC_D_DELTA_W10": ed10_st,
            },
        },
        sheets_extra={
            "Parity": kv_rows({**{f"CHECK_{k}": v for k, v in (pack.get("checks") or {}).items()}, "BASE_PARITY": True}),
            "SelectionSets": [
                {
                    "representation_id": r.get("representation_id"),
                    "COMMON_N": r.get("COMMON_N"),
                    "FILL_ONLY_ONLY_N": r.get("FILL_ONLY_ONLY_N"),
                    "DIRECT_ONLY_N": r.get("DIRECT_ONLY_N"),
                    "DIRECT_NE_FILL_ONLY_COHORT_RATE": r.get("DIRECT_NE_FILL_ONLY_COHORT_RATE"),
                }
                for r in specs
            ],
            "PFill": [
                {
                    "representation_id": r.get("representation_id"),
                    "FILL_ONLY_ONLY_P_FILL_MEAN": r.get("FILL_ONLY_ONLY_P_FILL_MEAN"),
                    "FILL_ONLY_ONLY_P_FILL_MEDIAN": r.get("FILL_ONLY_ONLY_P_FILL_MEDIAN"),
                    "DIRECT_ONLY_P_FILL_MEAN": r.get("DIRECT_ONLY_P_FILL_MEAN"),
                    "DIRECT_ONLY_P_FILL_MEDIAN": r.get("DIRECT_ONLY_P_FILL_MEDIAN"),
                    "DELTA_P_FILL": r.get("DELTA_P_FILL"),
                }
                for r in specs
            ],
            "W5Cause": [
                {
                    "representation_id": r.get("representation_id"),
                    "DIRECT_ONLY_W5_NONFILL_N": r.get("DIRECT_ONLY_W5_NONFILL_N"),
                    "DIRECT_ONLY_NO_VALID_BOARD_N": r.get("DIRECT_ONLY_NO_VALID_BOARD_N"),
                    "DIRECT_ONLY_NO_CROSS_N": r.get("DIRECT_ONLY_NO_CROSS_N"),
                }
                for r in specs
            ],
            "Recovery": [
                {
                    "representation_id": r.get("representation_id"),
                    "FILL_ONLY_ONLY_RECOVER_RATE": r.get("FILL_ONLY_ONLY_RECOVER_RATE"),
                    "DIRECT_ONLY_RECOVER_RATE_W10": r.get("DIRECT_ONLY_RECOVER_RATE_W10"),
                    "DIRECT_ONLY_NEVER_CROSS_10S_RATE": r.get("DIRECT_ONLY_NEVER_CROSS_10S_RATE"),
                    "DIRECT_ONLY_NO_VALID_BOARD_10S_RATE": r.get("DIRECT_ONLY_NO_VALID_BOARD_10S_RATE"),
                    "FILL_ONLY_W5_NONFILL_W10_RECOVER_N": r.get("FILL_ONLY_W5_NONFILL_W10_RECOVER_N"),
                    "DIRECT_W5_NONFILL_W10_RECOVER_N": r.get("DIRECT_W5_NONFILL_W10_RECOVER_N"),
                }
                for r in specs
            ],
            "WaitCounterfactual": specs,
            "RecoveredQuality": [
                {
                    "representation_id": r.get("representation_id"),
                    "DIRECT_RECOVERED_N": r.get("DIRECT_RECOVERED_N"),
                    "RECOVERED_COND_U": r.get("RECOVERED_COND_U"),
                    "RECOVERED_COND_D": r.get("RECOVERED_COND_D"),
                    "RECOVERED_TIME_TO_FILL_MEDIAN": r.get("RECOVERED_TIME_TO_FILL_MEDIAN"),
                    "RECOVERED_TIME_TO_FILL_P90": r.get("RECOVERED_TIME_TO_FILL_P90"),
                    "FILL_ONLY_COND_U_W5": r.get("FILL_ONLY_COND_U_W5"),
                    "DIRECT_COND_U_W5": r.get("DIRECT_COND_U_W5"),
                    "FILL_ONLY_COND_D_W5": r.get("FILL_ONLY_COND_D_W5"),
                    "DIRECT_COND_D_W5": r.get("DIRECT_COND_D_W5"),
                }
                for r in specs
            ],
            "FillLoss": [
                {
                    "representation_id": r.get("representation_id"),
                    "FILL_LOSS_LATE_RECOVERABLE_N": r.get("FILL_LOSS_LATE_RECOVERABLE_N"),
                    "FILL_LOSS_NEVER_CROSS_N": r.get("FILL_LOSS_NEVER_CROSS_N"),
                    "FILL_LOSS_NO_VALID_BOARD_N": r.get("FILL_LOSS_NO_VALID_BOARD_N"),
                }
                for r in specs
            ],
            "Coupling": [
                {
                    "representation_id": r.get("representation_id"),
                    "DIRECT_TTF_VS_U_SPEARMAN": r.get("DIRECT_TTF_VS_U_SPEARMAN"),
                    "DIRECT_TTF_VS_D_SPEARMAN": r.get("DIRECT_TTF_VS_D_SPEARMAN"),
                    "FILL_ONLY_ONLY_COND_U": r.get("FILL_ONLY_ONLY_COND_U"),
                    "DIRECT_ONLY_COND_U": r.get("DIRECT_ONLY_COND_U"),
                    "FILL_ONLY_ONLY_COND_D": r.get("FILL_ONLY_ONLY_COND_D"),
                    "DIRECT_ONLY_COND_D": r.get("DIRECT_ONLY_COND_D"),
                }
                for r in specs
            ],
            "DayRobustness": kv_rows(
                {
                    "RECOVERY_POS": rec_st.get("positive_days"),
                    "RECOVERY_NEG": rec_st.get("negative_days"),
                    "RECOVERY_ZERO": rec_st.get("zero_days"),
                    "FILL_DELTA_W5_POS": fd5_st.get("positive_days"),
                    "FILL_DELTA_W5_NEG": fd5_st.get("negative_days"),
                    "FILL_DELTA_W5_ZERO": fd5_st.get("zero_days"),
                    "EXEC_U_DELTA_W5_POS": eu5_st.get("positive_days"),
                    "EXEC_U_DELTA_W5_NEG": eu5_st.get("negative_days"),
                    "EXEC_D_DELTA_W5_POS": ed5_st.get("positive_days"),
                    "EXEC_D_DELTA_W5_NEG": ed5_st.get("negative_days"),
                    "FILL_DELTA_W10_POS": fd10_st.get("positive_days"),
                    "FILL_DELTA_W10_NEG": fd10_st.get("negative_days"),
                }
            )
            + day_sheet,
            "Integrity": kv_rows(
                {
                    **leak,
                    "SESSION": SESSION,
                    "RUNTIME_WAIT_SEC": WAIT_SEC,
                    "DEV_WAIT_SEC": DEV_WAIT_SEC,
                    "FROZEN_OOF_REPLAY_ONLY": FROZEN_OOF_REPLAY_ONLY,
                    "W10_ADOPTED": W10_ADOPTED,
                    "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
                    "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
                }
            ),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
