"""Offline joint-opportunity execution feasibility. No Runtime write. No Paper. No Exact."""
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

from research.anchor_timing_robustness.inventory import build_inventory
from research.canonical_entry_performance_rebase.analyze import row_key, wf_index
from research.direct_joint_objective.oof import attach_joint_labels
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.entry_execution_feasibility import (
    ANALYSIS_ID,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    ELIGIBLE_DAYS,
    EXACT_RAN,
    EXECUTION_REDESIGN_STARTED,
    FEATURE_SEARCH,
    FILL_RULE_CHANGED,
    LIMIT_CHANGED,
    MARKETABLE_ASSUMED,
    MAX_WORKERS,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    NEW_TARGET_CREATED,
    OCCUPANCY_APPLIED,
    PAPER_OPERATED,
    PNL_USED,
    POSITION_CAP_APPLIED,
    PRIOR_VERDICT_REQUIRED,
    REENTRY_APPLIED,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    STRATEGY_CREATED,
    TAKER_FILL_ADDED,
    THRESHOLD_SEARCH,
    TOPK_SEARCH,
    TRUE_OOS,
    WAIT_CHANGED,
)
from research.entry_execution_feasibility.analyze import (
    actual_joint_pareto_fill,
    base_parity,
    decide,
    early_path_compare,
    fill_counts,
    fillable_joint_availability,
    labeled_rows,
    nonfill_mechanism,
    oracle_availability,
    survival_after_fill,
)
from research.entry_execution_feasibility.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from research.entry_execution_feasibility.replay import process_day
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import WAIT_SEC

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
PRIOR = NATIVE / "results" / "research" / "raw_event_prediction_probe" / "report.json"
ROWS_PATH = NATIVE / "results" / "research" / "_work_cache" / "entry_target_architecture" / "path_rows.json"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "entry_execution_feasibility"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str), encoding="utf-8")


def _pool(fn, jobs: list[dict], label: str, key: str) -> list[dict]:
    if not jobs:
        return []
    out = []
    workers = min(MAX_WORKERS, len(jobs))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, job): job.get(key) for job in jobs}
        for fut in as_completed(futs):
            ident = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, key: ident, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            print(
                f"done {label} {body.get(key) or ident} ok={body.get('ok')} blocker={body.get('blocker')}",
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
                "WAIT_SEC": WAIT_SEC,
                "FILL": "Corrected Passive Fill find_ask_cross_fill standalone",
                "POSITION_CAP_APPLIED": POSITION_CAP_APPLIED,
                "OCCUPANCY_APPLIED": OCCUPANCY_APPLIED,
                "REENTRY_APPLIED": REENTRY_APPLIED,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "FEATURE_SEARCH": FEATURE_SEARCH,
                "PNL_USED": PNL_USED,
                "EXACT_RAN": EXACT_RAN,
                "FILL_RULE_CHANGED": FILL_RULE_CHANGED,
                "WAIT_CHANGED": WAIT_CHANGED,
                "LIMIT_CHANGED": LIMIT_CHANGED,
            }
        ),
        "FillRates": [{"empty": True}],
        "Cohorts": [{"empty": True}],
        "Nonfill": [{"empty": True}],
        "EarlyPath": [{"empty": True}],
        "Survival": [{"empty": True}],
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
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "FEATURE_SEARCH": FEATURE_SEARCH,
                "PNL_USED": PNL_USED,
                "TOPK_SEARCH": TOPK_SEARCH,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
                "FILL_RULE_CHANGED": FILL_RULE_CHANGED,
                "WAIT_CHANGED": WAIT_CHANGED,
                "LIMIT_CHANGED": LIMIT_CHANGED,
                "MARKETABLE_ASSUMED": MARKETABLE_ASSUMED,
                "TAKER_FILL_ADDED": TAKER_FILL_ADDED,
                "POSITION_CAP_APPLIED": POSITION_CAP_APPLIED,
                "OCCUPANCY_APPLIED": OCCUPANCY_APPLIED,
                "REENTRY_APPLIED": REENTRY_APPLIED,
                "RUNTIME_CANDIDATE_CREATED": RUNTIME_CANDIDATE_CREATED,
                "STRATEGY_CREATED": STRATEGY_CREATED,
                "EXECUTION_REDESIGN_STARTED": EXECUTION_REDESIGN_STARTED,
                "NEW_TARGET_CREATED": NEW_TARGET_CREATED,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"VERDICT={required.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(
        "STOP. No new model. No execution redesign. No runtime candidate. Runtime unchanged. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "ENTRY_EXECUTION_FEASIBILITY_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str, extra: dict | None = None) -> int:
    body = {
        "VERDICT": "ENTRY_EXECUTION_FEASIBILITY_INTEGRITY_FAILED",
        "FILLABLE_JOINT_FEASIBLE": False,
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "PRIMARY_FINDING": note,
        "BASE_PARITY": False,
        "PRIMARY_MECHANISM": None,
    }
    if extra:
        body.update(extra)
    return write_report(
        body,
        decision={
            "CASE": None,
            "VERDICT": "ENTRY_EXECUTION_FEASIBILITY_INTEGRITY_FAILED",
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
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE JOINT OPPORTUNITY EXECUTION FEASIBILITY V1", flush=True)
    print("Standalone Corrected Passive Fill. Frozen Direct Joint label. No model. No Exact.", flush=True)

    if abs(float(WAIT_SEC) - 1.0) > 1e-12:
        print("STOP WAIT_SEC drift", flush=True)
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
        print("STOP prior prediction verdict mismatch", preg.get("VERDICT"), flush=True)
        return _integrity("STOP. Prior RAW_EVENT_DESCRIPTORS_NOT_PREDICTIVE required.")
    if not ROWS_PATH.is_file():
        print("STOP missing path_rows.json", flush=True)
        return _integrity("STOP. Common-cohort path_rows missing.")

    path_body = json.loads(ROWS_PATH.read_text(encoding="utf-8"))
    rows = list(path_body.get("rows") or [])
    label_stats = attach_joint_labels(rows)
    oracle = oracle_availability(rows)
    parity = base_parity(oracle)
    print(
        f"oracle pos={oracle.get('JOINT_LABEL_POSITIVE_N')} neg={oracle.get('JOINT_LABEL_NEGATIVE_N')} "
        f"avail={oracle.get('JOINT_AVAILABLE_COHORT_N')}/{oracle.get('LABELED_COHORT_N')} "
        f"rate={oracle.get('JOINT_AVAILABLE_RATE')}",
        flush=True,
    )
    if not parity.get("BASE_PARITY"):
        print("STOP BASE_PARITY", parity, flush=True)
        return _integrity(
            "STOP. Oracle joint availability / label counts did not reproduce the frozen control.",
            extra={"parity": parity, "label_stats": label_stats},
        )
    print("BASE_PARITY true", flush=True)

    lab = labeled_rows(rows)
    by_day: dict[str, list[dict]] = {}
    for r in lab:
        by_day.setdefault(str(r.get("date") or ""), []).append(
            {
                "date": r.get("date"),
                "anchor": r.get("anchor"),
                "symbol": r.get("symbol"),
                "session": r.get("session"),
                "t0": r.get("t0"),
            }
        )

    inv = build_inventory()
    elig = [
        r
        for r in inv
        if r.get("date") in set(ELIGIBLE_DAYS)
        and r.get("replay_eligible")
        and r.get("universe_symbols")
        and r.get("capture_path")
    ]
    if len(elig) != len(ELIGIBLE_DAYS):
        print("STOP eligible day mismatch", len(elig), flush=True)
        return _integrity("STOP. Eligible Capture days mismatch.")
    inv_by = {r["date"]: r for r in elig}

    CACHE.mkdir(parents=True, exist_ok=True)
    feat_got = []
    feat_jobs = []
    for day in ELIGIBLE_DAYS:
        fp = CACHE / f"{day}_FILL.json"
        saved = _load(fp)
        if saved.get("ok") and saved.get("date") == day and saved.get("rows"):
            feat_got.append(saved)
            print(f"FILL cache-hit {day} rows={len(saved.get('rows') or [])}", flush=True)
            continue
        r = inv_by[day]
        feat_jobs.append(
            {
                "date": day,
                "capture_path": r["capture_path"],
                "universe": r["universe_symbols"],
                "candidates": by_day.get(day) or [],
            }
        )
    print(f"fill harvest jobs={len(feat_jobs)}", flush=True)
    for body in _pool(process_day, feat_jobs, "FILL", "date"):
        if body.get("ok"):
            _save_json(CACHE / f"{body.get('date')}_FILL.json", body)
        feat_got.append(body)
    fail = [b for b in feat_got if not b.get("ok")]
    if fail or len(feat_got) != len(ELIGIBLE_DAYS):
        print("STOP fill harvest failed", [(b.get("date"), b.get("blocker")) for b in fail], flush=True)
        return _integrity("STOP. Standalone fill harvest failed.")

    harvest = []
    for b in feat_got:
        harvest.extend(list(b.get("rows") or []))
    wf_by = wf_index(harvest)
    miss = 0
    for r in lab:
        h = wf_by.get(row_key(r))
        if h is None:
            miss += 1
            r["WOULD_FILL"] = False
            r["WOULD_EXPIRE"] = True
            r["nonfill_class"] = "OTHER"
            continue
        r["WOULD_FILL"] = bool(h.get("WOULD_FILL"))
        r["WOULD_EXPIRE"] = bool(h.get("WOULD_EXPIRE")) if h.get("WOULD_EXPIRE") is not None else (not r["WOULD_FILL"])
        r["fill_price"] = h.get("fill_price")
        r["fill_t"] = h.get("fill_t")
        r["fill_reason"] = h.get("fill_reason")
        r["nonfill_class"] = h.get("nonfill_class")
        r["ret_1s"] = h.get("ret_1s")
        r["ret_5s"] = h.get("ret_5s")
        r["ret_30s"] = h.get("ret_30s")
        r["MFE_FROM_FILL"] = h.get("MFE_FROM_FILL")
        r["DOWNSIDE_FROM_FILL"] = h.get("DOWNSIDE_FROM_FILL")
        r["fill_path_complete"] = h.get("fill_path_complete")
    print(f"join miss={miss} harvest={len(harvest)} labeled={len(lab)}", flush=True)
    if miss != 0:
        return _integrity("STOP. JOIN_MISS_N != 0.", extra={"JOIN_MISS_N": miss, "BASE_PARITY": True})

    counts = fill_counts(rows)
    fillable = fillable_joint_availability(rows, oracle_avail_n=int(oracle["JOINT_AVAILABLE_COHORT_N"]))
    pareto = actual_joint_pareto_fill(rows)
    nonfill = nonfill_mechanism(rows)
    early = early_path_compare(rows)
    survival = survival_after_fill(rows)
    decision = decide(
        parity_ok=True,
        integ_ok=True,
        integ_note="ok",
        fillable=fillable,
        survival=survival,
        counts=counts,
    )
    required = {
        "BASE_PARITY": True,
        "JOINT_AVAILABLE_COHORT_N": oracle.get("JOINT_AVAILABLE_COHORT_N"),
        "JOINT_AVAILABLE_RATE": oracle.get("JOINT_AVAILABLE_RATE"),
        "ALL_CANDIDATE_N": counts.get("ALL_CANDIDATE_N"),
        "ALL_WOULD_FILL_N": counts.get("ALL_WOULD_FILL_N"),
        "ALL_WOULD_FILL_RATE": counts.get("ALL_WOULD_FILL_RATE"),
        "JOINT_POSITIVE_N": counts.get("JOINT_POSITIVE_N"),
        "JOINT_POSITIVE_WOULD_FILL_N": counts.get("JOINT_POSITIVE_WOULD_FILL_N"),
        "JOINT_POSITIVE_WOULD_FILL_RATE": counts.get("JOINT_POSITIVE_WOULD_FILL_RATE"),
        "JOINT_NEGATIVE_WOULD_FILL_RATE": counts.get("JOINT_NEGATIVE_WOULD_FILL_RATE"),
        "CURRENT_TOP3_N": counts.get("CURRENT_TOP3_N"),
        "CURRENT_TOP3_WOULD_FILL_N": counts.get("CURRENT_TOP3_WOULD_FILL_N"),
        "CURRENT_TOP3_WOULD_FILL_RATE": counts.get("CURRENT_TOP3_WOULD_FILL_RATE"),
        "FILLABLE_JOINT_AVAILABLE_COHORT_N": fillable.get("FILLABLE_JOINT_AVAILABLE_COHORT_N"),
        "FILLABLE_JOINT_AVAILABLE_RATE": fillable.get("FILLABLE_JOINT_AVAILABLE_RATE"),
        "JOINT_AVAILABILITY_LOST_TO_FILL_N": fillable.get("JOINT_AVAILABILITY_LOST_TO_FILL_N"),
        "JOINT_AVAILABILITY_LOST_TO_FILL_RATE": fillable.get("JOINT_AVAILABILITY_LOST_TO_FILL_RATE"),
        "ACTUAL_JOINT_PARETO_N": pareto.get("ACTUAL_JOINT_PARETO_N"),
        "ACTUAL_JOINT_PARETO_WOULD_FILL_N": pareto.get("ACTUAL_JOINT_PARETO_WOULD_FILL_N"),
        "ACTUAL_JOINT_PARETO_WOULD_FILL_RATE": pareto.get("ACTUAL_JOINT_PARETO_WOULD_FILL_RATE"),
        "JOINT_NONFILL_N": nonfill.get("JOINT_NONFILL_N"),
        "JOINT_NONFILL_NO_ASK_CROSS_N": nonfill.get("JOINT_NONFILL_NO_ASK_CROSS_N"),
        "JOINT_NONFILL_NONEXEC_N": nonfill.get("JOINT_NONFILL_NONEXEC_N"),
        "JOINT_NONFILL_NO_VALID_BOARD_N": nonfill.get("JOINT_NONFILL_NO_VALID_BOARD_N"),
        "JOINT_NONFILL_OTHER_N": nonfill.get("JOINT_NONFILL_OTHER_N"),
        "JOINT_LABEL_SURVIVAL_AFTER_FILL_RATE": survival.get("JOINT_LABEL_SURVIVAL_AFTER_FILL_RATE"),
        "FILLABLE_JOINT_FEASIBLE": decision.get("FILLABLE_JOINT_FEASIBLE"),
        "PRIMARY_MECHANISM": decision.get("PRIMARY_MECHANISM"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "parity": parity,
        "WAIT_SEC": WAIT_SEC,
        "JOIN_MISS_N": miss,
    }
    print(
        f"CASE={decision.get('CASE')} fillable_rate={fillable.get('FILLABLE_JOINT_AVAILABLE_RATE')} "
        f"jp_fill={counts.get('JOINT_POSITIVE_WOULD_FILL_RATE')} "
        f"surv={survival.get('JOINT_LABEL_SURVIVAL_AFTER_FILL_RATE')}",
        flush=True,
    )
    return write_report(
        required,
        decision=decision,
        extra={
            "oracle": oracle,
            "counts": counts,
            "fillable": fillable,
            "pareto": pareto,
            "nonfill": nonfill,
            "early_path": early,
            "survival": survival,
            "label_stats": label_stats,
        },
        sheets_extra={
            "FillRates": kv_rows(counts),
            "Cohorts": kv_rows({**oracle, **fillable}),
            "Nonfill": kv_rows(nonfill),
            "EarlyPath": [
                {"side": "fill", **(early.get("fill") or {})},
                {"side": "nonfill", **(early.get("nonfill") or {})},
                {"side": "delta_nonfill_minus_fill_median", **(early.get("delta_nonfill_minus_fill_median") or {})},
            ],
            "Survival": kv_rows(survival),
            "Integrity": kv_rows(
                {
                    "JOIN_MISS_N": miss,
                    "WAIT_SEC": WAIT_SEC,
                    "POSITION_CAP_APPLIED": POSITION_CAP_APPLIED,
                    "OCCUPANCY_APPLIED": OCCUPANCY_APPLIED,
                    "REENTRY_APPLIED": REENTRY_APPLIED,
                    "FILL_RULE_CHANGED": FILL_RULE_CHANGED,
                }
            ),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
