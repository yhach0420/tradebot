"""Offline AM DIRECT EXEC_U precommitted development. No Runtime write. No Paper. No Exact."""
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

from research.am_direct_exec_u_development import (
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
    MODEL_HYPERPARAMETER_SEARCH_N,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    ORACLE_SELECTION_USE_N,
    PAPER_OPERATED,
    PARITY_ABS_TOL,
    PNL_USED,
    PRIOR_PRECOMMIT_ID,
    PRIOR_PRECOMMIT_VERDICT,
    REPRESENTATION_N,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    SESSION,
    SHORTLIST_SEARCH,
    STAGE1_ALLOWED,
    STAGE2_ALLOWED,
    STRATEGY_CREATED,
    THRESHOLD_SEARCH,
    TOPK_SEARCH,
    TRUE_OOS,
    WAIT_POLICY_ADOPTED,
    W5_RUNTIME_ADOPTED,
)
from research.am_direct_exec_u_development.analyze import (
    d_nonworse_gate,
    decide,
    fill_edge_gate,
    fill_vs_control_pack,
    freeze_parity,
    spec_rows,
    spearman_consensus,
    sum_integrity,
    upside_gate,
)
from research.am_direct_exec_u_development.oof import attach_targets, process_am_direct
from research.am_direct_exec_u_development.publish import (
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
PRECOMMIT = NATIVE / "results" / "research" / "am_direct_exec_u_target_precommit" / "report.json"
GEOMETRY = NATIVE / "results" / "research" / "am_wait5_fill_upside_geometry" / "report.json"
FILLABILITY = NATIVE / "results" / "research" / "am_wait5_fillability_first_reassessment" / "report.json"
DEV_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_wait5_two_stage_development"
OOF_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_wait5_stage2_reassessment"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_direct_exec_u_development"


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
                f"done AM-DIRECT {body.get('representation_id') or key} ok={body.get('ok')} "
                f"fo={(body.get('FILL_ONLY') or {}).get('SELECTED_FILL_RATE')} "
                f"du={(body.get('DIRECT_EXEC_U') or {}).get('SELECTED_FILL_RATE')} "
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
                "ARMS": "CONTROL, FILL_ONLY, DIRECT_EXEC_U",
                "REPRESENTATION_N": REPRESENTATION_N,
                "PRIMARY_REFERENCE": "FILL_ONLY",
                "STAGE1_ALLOWED": STAGE1_ALLOWED,
                "STAGE2_ALLOWED": STAGE2_ALLOWED,
                "BEST_REPRESENTATION_ADOPTED": BEST_REPRESENTATION_ADOPTED,
                "HYPERPARAMETER_TUNING": HYPERPARAMETER_TUNING,
                "PNL_USED": PNL_USED,
                "EXACT_RAN": EXACT_RAN,
                "FINAL_MODEL_ADOPTED": FINAL_MODEL_ADOPTED,
                "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
            }
        ),
        "Parity": [{"empty": True}],
        "Target": [{"empty": True}],
        "Arms": [{"empty": True}],
        "Distinctness": [{"empty": True}],
        "Fillability": [{"empty": True}],
        "ExecU": [{"empty": True}],
        "ExecD": [{"empty": True}],
        "Conditional": [{"empty": True}],
        "Spearman": [{"empty": True}],
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
                "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
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
    fail = required.get("VERDICT") == "AM_DIRECT_EXEC_U_INTEGRITY_FAILED"
    return 2 if fail else 0


def _empty_required(note: str, extra: dict | None = None) -> dict:
    body = {
        "BASE_PARITY": False,
        "REPRESENTATION_N": 9,
        "TARGET_ROW_N": None,
        "TARGET_NONZERO_N": None,
        "TARGET_ZERO_N": None,
        "CONTROL_FILL_RATE": None,
        "FILL_ONLY_FILL_RATE": None,
        "DIRECT_EXEC_U_FILL_RATE": None,
        "DELTA_FILL_VS_FILL_ONLY": None,
        "FILL_NONNEG_REP_N": None,
        "FILL_POS_DAYS": None,
        "FILL_ZERO_DAYS": None,
        "FILL_NEG_DAYS": None,
        "FILL_ONLY_EXEC_U": None,
        "DIRECT_EXEC_U_EXEC_U": None,
        "DELTA_EXEC_U_VS_FILL_ONLY": None,
        "EXEC_U_POS_REP_N": None,
        "EXEC_U_POS_DAYS": None,
        "EXEC_U_NEG_DAYS": None,
        "EXEC_U_EX_BEST_DAY": None,
        "EXEC_U_EX_TOP3_DAYS": None,
        "FILL_ONLY_EXEC_D": None,
        "DIRECT_EXEC_U_EXEC_D": None,
        "DELTA_EXEC_D_VS_FILL_ONLY": None,
        "EXEC_D_NONNEG_REP_N": None,
        "EXEC_D_POS_DAYS": None,
        "EXEC_D_ZERO_DAYS": None,
        "EXEC_D_NEG_DAYS": None,
        "EXEC_D_EX_BEST_DAY": None,
        "EXEC_D_EX_TOP3_DAYS": None,
        "DIRECT_COND_U": None,
        "DIRECT_COND_D": None,
        "OVERALL_OOF_SPEARMAN": None,
        "DAILY_MEDIAN_SPEARMAN": None,
        "DIRECT_NE_FILL_ONLY_COHORT_RATE": None,
        "DIRECT_FILL_EDGE_PASS": False,
        "DIRECT_EXEC_U_UPSIDE_PASS": False,
        "DIRECT_EXEC_D_NONWORSE": False,
        "DIRECT_EXEC_U_DEVELOPMENT_PASS": False,
        "ORACLE_SELECTION_USE_N": 0,
        "PM_ROWS_USED_N": None,
        "FUTURE_EVENT_USE_N": None,
        "TARGET_CONTAMINATION_N": None,
        "HELDOUT_FIT_LEAK_N": None,
        "PRIMARY_FINDING": note,
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "AM_DIRECT_EXEC_U_INTEGRITY_FAILED",
    }
    if extra:
        body.update(extra)
    return body


def _integrity(note: str, extra: dict | None = None) -> int:
    return write_report(
        _empty_required(note, extra),
        decision={
            "CASE": "E",
            "DIRECT_FILL_EDGE_PASS": False,
            "DIRECT_EXEC_U_UPSIDE_PASS": False,
            "DIRECT_EXEC_D_NONWORSE": False,
            "DIRECT_EXEC_U_DEVELOPMENT_PASS": False,
            "VERDICT": "AM_DIRECT_EXEC_U_INTEGRITY_FAILED",
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
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM DIRECT EXEC_U DEVELOPMENT V1", flush=True)
    print("AM only. CONTROL / FILL_ONLY / DIRECT_EXEC_U. Frozen RF. No Exact. No PnL.", flush=True)

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

    pre = _load(PRECOMMIT)
    preg = pre.get("required") or {}
    if str(pre.get("ANALYSIS_ID") or "") != PRIOR_PRECOMMIT_ID or str(preg.get("VERDICT") or "") != PRIOR_PRECOMMIT_VERDICT:
        return _integrity(
            f"STOP. Prior {PRIOR_PRECOMMIT_ID} / {PRIOR_PRECOMMIT_VERDICT} required.",
            extra={"prior_id": pre.get("ANALYSIS_ID"), "prior_verdict": preg.get("VERDICT")},
        )

    geo = (_load(GEOMETRY).get("required") or {})
    fillab = (_load(FILLABILITY).get("required") or {})
    prior_obs = {
        "FILL_ONLY_FILL_RATE": fillab.get("FILL_ONLY_FILL_RATE"),
        "FILL_ONLY_EXEC_U": fillab.get("FILL_ONLY_EXEC_U") if fillab.get("FILL_ONLY_EXEC_U") is not None else geo.get("FILL_ONLY_EXEC_U"),
        "FILL_ONLY_EXEC_D": fillab.get("FILL_ONLY_EXEC_D") if fillab.get("FILL_ONLY_EXEC_D") is not None else geo.get("FILL_ONLY_EXEC_D"),
        "FILL_NONWORSE_U_IMPROVE_RATE": geo.get("FILL_NONWORSE_U_IMPROVE_RATE"),
        "SAME_FILL_U_IMPROVE_RATE": geo.get("SAME_FILL_U_IMPROVE_RATE"),
        "FILL_NONWORSE_U_D_NONWORSE_RATE": geo.get("FILL_NONWORSE_U_D_NONWORSE_RATE"),
        "FILL_ONLY_DOMINATED_IN_FILL_U_RATE": geo.get("FILL_ONLY_DOMINATED_IN_FILL_U_RATE"),
    }
    if prior_obs["FILL_ONLY_EXEC_U"] is None:
        prior_obs["FILL_ONLY_EXEC_U"] = preg.get("FILL_ONLY_EXEC_U")
    if prior_obs["FILL_ONLY_EXEC_D"] is None:
        prior_obs["FILL_ONLY_EXEC_D"] = preg.get("FILL_ONLY_EXEC_D")
    prior_pack = freeze_parity(prior_obs)
    print("prior parity", prior_pack.get("ok"), prior_pack.get("checks"), flush=True)
    if not prior_pack.get("ok"):
        return _integrity("STOP. Frozen FILL_ONLY / geometry parity did not reproduce.", extra={"parity": prior_pack})

    grid = representation_grid()
    if len(grid) != 9:
        return _integrity("STOP. Representation grid is not 9.")
    am_src = DEV_CACHE / "am_rows.json"
    if not am_src.is_file():
        return _integrity("STOP. Frozen AM feature rows missing.")

    raw = json.loads(am_src.read_text(encoding="utf-8"))
    am = list(raw.get("rows") or [])
    target_pack = attach_targets(am)
    print("target", target_pack, flush=True)
    if int(target_pack.get("TARGET_MISSING_N") or 0) != 0:
        return _integrity(
            "STOP. TARGET_MISSING_N != 0. Drop/backfill of missing TARGET_EXEC_U is forbidden.",
            extra={"target": target_pack},
        )
    if int(target_pack.get("TARGET_ROW_N") or 0) != 6441:
        return _integrity("STOP. TARGET_ROW_N is not the frozen AM labeled population.", extra={"target": target_pack})

    CACHE.mkdir(parents=True, exist_ok=True)
    rows_path = CACHE / "am_rows.json"
    _save_json(rows_path, {"session": "AM", "rows": am, "target": target_pack})

    jobs = []
    got = []
    for spec in grid:
        rid = str(spec.get("representation_id"))
        fp = CACHE / _cache_name(rid)
        scored_fp = OOF_CACHE / _scored_name(rid)
        saved = _load(fp)
        if saved.get("ok") and saved.get("representation_id") == rid and saved.get("DIRECT_EXEC_U"):
            got.append(saved)
            print(f"AM-DIRECT cache-hit {rid}", flush=True)
            continue
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
    print(f"AM-DIRECT jobs={len(jobs)}", flush=True)
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
    got.sort(key=lambda b: str(b.get("representation_id") or ""))

    for b in got:
        rid = str(b.get("representation_id") or "")
        if int(b.get("outer_folds") or 0) != 18:
            return _integrity(
                "STOP. Held-out fold count is not 18.",
                extra={"representation_id": rid, "outer_folds": b.get("outer_folds")},
            )
        prior_arm = _load(DEV_CACHE / _cache_name(rid))
        fo = (b.get("FILL_ONLY") or {}).get("SELECTED_FILL_RATE")
        ctrl = (b.get("CONTROL") or {}).get("SELECTED_FILL_RATE")
        pfo = (prior_arm.get("FILL_ONLY") or {}).get("SELECTED_FILL_RATE")
        pctrl = (prior_arm.get("CONTROL") or {}).get("SELECTED_FILL_RATE")
        if not _close(fo, pfo, PARITY_ABS_TOL) or not _close(ctrl, pctrl, PARITY_ABS_TOL):
            return _integrity(
                "STOP. CONTROL / FILL_ONLY fill rate did not match frozen development cache.",
                extra={"representation_id": rid, "fo": fo, "dev_fo": pfo, "ctrl": ctrl, "dev_ctrl": pctrl},
            )
        pfo_u = (prior_arm.get("FILL_ONLY") or {}).get("EXEC_U")
        pfo_d = (prior_arm.get("FILL_ONLY") or {}).get("EXEC_D")
        fo_u = (b.get("FILL_ONLY") or {}).get("EXEC_U")
        fo_d = (b.get("FILL_ONLY") or {}).get("EXEC_D")
        if not _close(fo_u, pfo_u, PARITY_ABS_TOL) or not _close(fo_d, pfo_d, PARITY_ABS_TOL):
            return _integrity(
                "STOP. FILL_ONLY EXEC_U / EXEC_D did not match frozen development cache.",
                extra={"representation_id": rid, "fo_u": fo_u, "dev_u": pfo_u, "fo_d": fo_d, "dev_d": pfo_d},
            )

    leak = sum_integrity(got)
    leak["FUTURE_EVENT_USE_N"] = sum(1 for r in am if r.get("future_event_use"))
    leak["PM_ROWS_USED_N"] = sum(1 for r in am if session_of(r) != "AM")
    leak["TARGET_MISSING_N"] = int(target_pack.get("TARGET_MISSING_N") or 0)
    leak["ORACLE_SELECTION_USE_N"] = int(ORACLE_SELECTION_USE_N)
    leak["STAGE1_USE_N"] = 0
    leak["STAGE2_USE_N"] = 0
    leak["MODEL_HYPERPARAMETER_SEARCH_N"] = int(MODEL_HYPERPARAMETER_SEARCH_N)
    print("integrity", leak, flush=True)

    specs = spec_rows(got)
    fill_g = fill_edge_gate(specs, got)
    u_g = upside_gate(specs, got)
    d_g = d_nonworse_gate(specs, got)
    vs_ctrl = fill_vs_control_pack(specs, got)
    sp_daily, sp_cons = spearman_consensus(got)
    run_obs = {
        "FILL_ONLY_FILL_RATE": _med_key(specs, "FILL_ONLY_FILL_RATE"),
        "FILL_ONLY_EXEC_U": _med_key(specs, "FILL_ONLY_EXEC_U"),
        "FILL_ONLY_EXEC_D": _med_key(specs, "FILL_ONLY_EXEC_D"),
        "FILL_NONWORSE_U_IMPROVE_RATE": prior_obs["FILL_NONWORSE_U_IMPROVE_RATE"],
        "SAME_FILL_U_IMPROVE_RATE": prior_obs["SAME_FILL_U_IMPROVE_RATE"],
        "FILL_NONWORSE_U_D_NONWORSE_RATE": prior_obs["FILL_NONWORSE_U_D_NONWORSE_RATE"],
        "FILL_ONLY_DOMINATED_IN_FILL_U_RATE": prior_obs["FILL_ONLY_DOMINATED_IN_FILL_U_RATE"],
    }
    run_pack = freeze_parity(run_obs)
    print("run FILL_ONLY parity", run_pack.get("ok"), run_pack.get("checks"), flush=True)
    if not run_pack.get("ok"):
        return _integrity("STOP. This-run FILL_ONLY headline did not match frozen parity.", extra={"parity": run_pack})

    decision = decide(fill_gate=fill_g, u_gate=u_g, d_gate=d_g, leak=leak)
    if decision.get("CASE") == "E":
        return _integrity(str(decision.get("PRIMARY_FINDING")), extra={"integrity": leak})

    required = {
        "BASE_PARITY": True,
        "REPRESENTATION_N": 9,
        "TARGET_ROW_N": target_pack.get("TARGET_ROW_N"),
        "TARGET_NONZERO_N": target_pack.get("TARGET_NONZERO_N"),
        "TARGET_ZERO_N": target_pack.get("TARGET_ZERO_N"),
        "CONTROL_FILL_RATE": _med_key(specs, "CONTROL_FILL_RATE"),
        "FILL_ONLY_FILL_RATE": _med_key(specs, "FILL_ONLY_FILL_RATE"),
        "DIRECT_EXEC_U_FILL_RATE": _med_key(specs, "DIRECT_EXEC_U_FILL_RATE"),
        "DELTA_FILL_VS_FILL_ONLY": fill_g.get("DELTA_FILL_VS_FILL_ONLY"),
        "FILL_NONNEG_REP_N": fill_g.get("FILL_NONNEG_REP_N"),
        "FILL_POS_DAYS": fill_g.get("FILL_POS_DAYS"),
        "FILL_ZERO_DAYS": fill_g.get("FILL_ZERO_DAYS"),
        "FILL_NEG_DAYS": fill_g.get("FILL_NEG_DAYS"),
        "FILL_ONLY_EXEC_U": _med_key(specs, "FILL_ONLY_EXEC_U"),
        "DIRECT_EXEC_U_EXEC_U": _med_key(specs, "DIRECT_EXEC_U_EXEC_U"),
        "DELTA_EXEC_U_VS_FILL_ONLY": u_g.get("DELTA_EXEC_U_VS_FILL_ONLY"),
        "EXEC_U_POS_REP_N": u_g.get("EXEC_U_POS_REP_N"),
        "EXEC_U_POS_DAYS": u_g.get("EXEC_U_POS_DAYS"),
        "EXEC_U_NEG_DAYS": u_g.get("EXEC_U_NEG_DAYS"),
        "EXEC_U_EX_BEST_DAY": u_g.get("EXEC_U_EX_BEST_DAY"),
        "EXEC_U_EX_TOP3_DAYS": u_g.get("EXEC_U_EX_TOP3_DAYS"),
        "FILL_ONLY_EXEC_D": _med_key(specs, "FILL_ONLY_EXEC_D"),
        "DIRECT_EXEC_U_EXEC_D": _med_key(specs, "DIRECT_EXEC_U_EXEC_D"),
        "DELTA_EXEC_D_VS_FILL_ONLY": d_g.get("DELTA_EXEC_D_VS_FILL_ONLY"),
        "EXEC_D_NONNEG_REP_N": d_g.get("EXEC_D_NONNEG_REP_N"),
        "EXEC_D_POS_DAYS": d_g.get("EXEC_D_POS_DAYS"),
        "EXEC_D_ZERO_DAYS": d_g.get("EXEC_D_ZERO_DAYS"),
        "EXEC_D_NEG_DAYS": d_g.get("EXEC_D_NEG_DAYS"),
        "EXEC_D_EX_BEST_DAY": d_g.get("EXEC_D_EX_BEST_DAY"),
        "EXEC_D_EX_TOP3_DAYS": d_g.get("EXEC_D_EX_TOP3_DAYS"),
        "DIRECT_COND_U": _med_key(specs, "DIRECT_COND_U"),
        "DIRECT_COND_D": _med_key(specs, "DIRECT_COND_D"),
        "OVERALL_OOF_SPEARMAN": _med_key(specs, "OVERALL_OOF_SPEARMAN"),
        "DAILY_MEDIAN_SPEARMAN": sp_cons.get("DAILY_MEDIAN_SPEARMAN"),
        "DIRECT_NE_FILL_ONLY_COHORT_RATE": _med_key(specs, "DIRECT_NE_FILL_ONLY_COHORT_RATE"),
        "DIRECT_FILL_EDGE_PASS": decision.get("DIRECT_FILL_EDGE_PASS"),
        "DIRECT_EXEC_U_UPSIDE_PASS": decision.get("DIRECT_EXEC_U_UPSIDE_PASS"),
        "DIRECT_EXEC_D_NONWORSE": decision.get("DIRECT_EXEC_D_NONWORSE"),
        "DIRECT_EXEC_U_DEVELOPMENT_PASS": decision.get("DIRECT_EXEC_U_DEVELOPMENT_PASS"),
        "ORACLE_SELECTION_USE_N": leak.get("ORACLE_SELECTION_USE_N"),
        "PM_ROWS_USED_N": leak.get("PM_ROWS_USED_N"),
        "FUTURE_EVENT_USE_N": leak.get("FUTURE_EVENT_USE_N"),
        "TARGET_CONTAMINATION_N": leak.get("TARGET_CONTAMINATION_N"),
        "HELDOUT_FIT_LEAK_N": leak.get("HELDOUT_FIT_LEAK_N"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "DELTA_FILL_VS_CONTROL": vs_ctrl.get("DELTA_FILL_VS_CONTROL"),
        "DELTA_EXEC_U_VS_CONTROL": _med_key(specs, "DELTA_EXEC_U_VS_CONTROL"),
        "CONTROL_EXEC_U": _med_key(specs, "CONTROL_EXEC_U"),
        "CONTROL_EXEC_D": _med_key(specs, "CONTROL_EXEC_D"),
        "CONTROL_COND_U": _med_key(specs, "CONTROL_COND_U"),
        "FILL_ONLY_COND_U": _med_key(specs, "FILL_ONLY_COND_U"),
        "CONTROL_COND_D": _med_key(specs, "CONTROL_COND_D"),
        "FILL_ONLY_COND_D": _med_key(specs, "FILL_ONLY_COND_D"),
        "DELTA_COND_U_VS_FILL_ONLY": _med_key(specs, "DELTA_COND_U_VS_FILL_ONLY"),
        "DELTA_COND_D_VS_FILL_ONLY": _med_key(specs, "DELTA_COND_D_VS_FILL_ONLY"),
        "TARGET_MISSING_N": leak.get("TARGET_MISSING_N"),
        "NORMALIZER_HELDOUT_ROW_N": leak.get("NORMALIZER_HELDOUT_ROW_N"),
        "MODEL_HYPERPARAMETER_SEARCH_N": leak.get("MODEL_HYPERPARAMETER_SEARCH_N"),
        "STAGE1_USE_N": leak.get("STAGE1_USE_N"),
        "STAGE2_USE_N": leak.get("STAGE2_USE_N"),
    }
    print(
        f"CASE={decision.get('CASE')} fill={fill_g.get('PASS')} u={u_g.get('PASS')} d={d_g.get('PASS')} "
        f"VERDICT={decision.get('VERDICT')}",
        flush=True,
    )
    return write_report(
        required,
        decision=decision,
        extra={
            "target": target_pack,
            "parity": run_pack,
            "fill_edge": {k: v for k, v in fill_g.items() if k != "daily"},
            "upside": {k: v for k, v in u_g.items() if k != "daily"},
            "downside": {k: v for k, v in d_g.items() if k != "daily"},
            "spearman_consensus": sp_cons,
            "integrity": leak,
            "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
            "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
        },
        sheets_extra={
            "Parity": kv_rows({**{f"PRIOR_{k}": v for k, v in (prior_pack.get("checks") or {}).items()}, **{f"RUN_{k}": v for k, v in (run_pack.get("checks") or {}).items()}, "BASE_PARITY": True}),
            "Target": kv_rows({**target_pack, "TARGET_CONTAMINATION_N": leak.get("TARGET_CONTAMINATION_N")}),
            "Arms": specs,
            "Distinctness": [
                {
                    "representation_id": r.get("representation_id"),
                    "DIRECT_EQ_FILL_ONLY_COHORT_N": r.get("DIRECT_EQ_FILL_ONLY_COHORT_N"),
                    "DIRECT_NE_FILL_ONLY_COHORT_N": r.get("DIRECT_NE_FILL_ONLY_COHORT_N"),
                    "DIRECT_NE_FILL_ONLY_COHORT_RATE": r.get("DIRECT_NE_FILL_ONLY_COHORT_RATE"),
                    "SWAPPED_IN_N": r.get("SWAPPED_IN_N"),
                    "SWAPPED_OUT_N": r.get("SWAPPED_OUT_N"),
                }
                for r in specs
            ],
            "Fillability": kv_rows({k: v for k, v in fill_g.items() if k not in {"daily", "gates"}})
            + [{"scope": "GATE", **(fill_g.get("gates") or {})}],
            "ExecU": kv_rows({k: v for k, v in u_g.items() if k not in {"daily", "gates"}}),
            "ExecD": kv_rows({k: v for k, v in d_g.items() if k not in {"daily", "gates"}}),
            "Conditional": kv_rows(
                {
                    "CONTROL_COND_U": _med_key(specs, "CONTROL_COND_U"),
                    "FILL_ONLY_COND_U": _med_key(specs, "FILL_ONLY_COND_U"),
                    "DIRECT_COND_U": _med_key(specs, "DIRECT_COND_U"),
                    "DELTA_COND_U_VS_FILL_ONLY": _med_key(specs, "DELTA_COND_U_VS_FILL_ONLY"),
                    "CONTROL_COND_D": _med_key(specs, "CONTROL_COND_D"),
                    "FILL_ONLY_COND_D": _med_key(specs, "FILL_ONLY_COND_D"),
                    "DIRECT_COND_D": _med_key(specs, "DIRECT_COND_D"),
                    "DELTA_COND_D_VS_FILL_ONLY": _med_key(specs, "DELTA_COND_D_VS_FILL_ONLY"),
                }
            ),
            "Spearman": kv_rows(
                {
                    "OVERALL_OOF_SPEARMAN": _med_key(specs, "OVERALL_OOF_SPEARMAN"),
                    **sp_cons,
                }
            ),
            "ConsensusDays": [{"scope": "FILL_VS_FILL_ONLY", **r} for r in (fill_g.get("daily") or [])]
            + [{"scope": "EXEC_U_VS_FILL_ONLY", **r} for r in (u_g.get("daily") or [])]
            + [{"scope": "EXEC_D_VS_FILL_ONLY", **r} for r in (d_g.get("daily") or [])]
            + [{"scope": "SPEARMAN", **r} for r in sp_daily],
            "Gates": kv_rows(
                {
                    **{f"FILL_{k}": v for k, v in (fill_g.get("gates") or {}).items()},
                    **{f"U_{k}": v for k, v in (u_g.get("gates") or {}).items()},
                    **{f"D_{k}": v for k, v in (d_g.get("gates") or {}).items()},
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
                    "FINAL_MODEL_ADOPTED": FINAL_MODEL_ADOPTED,
                    "ORACLE_SELECTION_USE_N": 0,
                }
            ),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
