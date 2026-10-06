"""Offline Passive WAIT policy reassessment. No Runtime write. No Paper. No Exact."""
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
from research.entry_execution_feasibility.analyze import labeled_rows
from research.passive_wait_policy_reassessment import (
    ANALYSIS_ID,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    ELIGIBLE_DAYS,
    EXACT_RAN,
    EXTENDED_IDS,
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
    PRICE_POLICY_STARTED,
    PRIOR_VERDICT_REQUIRED,
    REENTRY_APPLIED,
    REPRICING,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    STRATEGY_CREATED,
    TAKER_FILL_ADDED,
    TRUE_OOS,
    WAIT_IDS,
    WAIT_INTERPOLATION,
    WAIT_POLICY_ADOPTED,
    WAIT_SEC_BY_ID,
)
from research.passive_wait_policy_reassessment.analyze import (
    decide,
    fill_quality_group,
    incremental,
    incremental_source_mechanism,
    time_to_fill,
    w1_parity,
    wait_metrics,
)
from research.passive_wait_policy_reassessment.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from research.passive_wait_policy_reassessment.replay import process_day
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import WAIT_SEC

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
PRIOR = NATIVE / "results" / "research" / "execution_aware_target_feasibility" / "report.json"
ROWS_PATH = NATIVE / "results" / "research" / "_work_cache" / "entry_target_architecture" / "path_rows.json"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "passive_wait_policy_reassessment"


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
                "RUNTIME_WAIT_SEC": WAIT_SEC,
                "WAIT_IDS": list(WAIT_IDS),
                "WAIT_SEC_BY_ID": dict(WAIT_SEC_BY_ID),
                "FILL": "Corrected Passive Fill find_ask_cross_fill standalone",
                "POSITION_CAP_APPLIED": POSITION_CAP_APPLIED,
                "OCCUPANCY_APPLIED": OCCUPANCY_APPLIED,
                "REENTRY_APPLIED": REENTRY_APPLIED,
                "WAIT_POLICY_ADOPTED": WAIT_POLICY_ADOPTED,
                "WAIT_INTERPOLATION": WAIT_INTERPOLATION,
                "REPRICING": REPRICING,
                "LIMIT_CHANGED": LIMIT_CHANGED,
                "FILL_RULE_CHANGED": FILL_RULE_CHANGED,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "FEATURE_SEARCH": FEATURE_SEARCH,
                "PNL_USED": PNL_USED,
                "EXACT_RAN": EXACT_RAN,
            }
        ),
        "W1Parity": [{"empty": True}],
        "Waits": [{"empty": True}],
        "Incremental": [{"empty": True}],
        "QualityGroups": [{"empty": True}],
        "TimeToFill": [{"empty": True}],
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
                "FILL_RULE_CHANGED": FILL_RULE_CHANGED,
                "LIMIT_CHANGED": LIMIT_CHANGED,
                "MARKETABLE_ASSUMED": MARKETABLE_ASSUMED,
                "TAKER_FILL_ADDED": TAKER_FILL_ADDED,
                "POSITION_CAP_APPLIED": POSITION_CAP_APPLIED,
                "OCCUPANCY_APPLIED": OCCUPANCY_APPLIED,
                "REENTRY_APPLIED": REENTRY_APPLIED,
                "REPRICING": REPRICING,
                "WAIT_POLICY_ADOPTED": WAIT_POLICY_ADOPTED,
                "WAIT_INTERPOLATION": WAIT_INTERPOLATION,
                "RUNTIME_CANDIDATE_CREATED": RUNTIME_CANDIDATE_CREATED,
                "STRATEGY_CREATED": STRATEGY_CREATED,
                "PRICE_POLICY_STARTED": PRICE_POLICY_STARTED,
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
        "STOP. No WAIT adopted. No price-policy test. No runtime candidate. Runtime unchanged. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "PASSIVE_WAIT_POLICY_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str, extra: dict | None = None) -> int:
    body = {
        "BASE_PARITY": False,
        "W1_ANY_RATE": None,
        "W2_ANY_RATE": None,
        "W5_ANY_RATE": None,
        "W10_ANY_RATE": None,
        "W1_MULTI_RATE": None,
        "W2_MULTI_RATE": None,
        "W5_MULTI_RATE": None,
        "W10_MULTI_RATE": None,
        "W1_VALID_BOARD_RATE": None,
        "W2_VALID_BOARD_RATE": None,
        "W5_VALID_BOARD_RATE": None,
        "W10_VALID_BOARD_RATE": None,
        "TIME_TO_FILL_MEDIAN_SEC": None,
        "TIME_TO_FILL_P90_SEC": None,
        "W2_NEW_FILL_N": None,
        "W5_NEW_FILL_N": None,
        "W10_NEW_FILL_N": None,
        "W2_NEW_POSTFILL_MFE": None,
        "W2_NEW_POSTFILL_DOWNSIDE": None,
        "W5_NEW_POSTFILL_MFE": None,
        "W5_NEW_POSTFILL_DOWNSIDE": None,
        "W10_NEW_POSTFILL_MFE": None,
        "W10_NEW_POSTFILL_DOWNSIDE": None,
        "W1_FILLABLE_JOINT_RATE": None,
        "W2_FILLABLE_JOINT_RATE": None,
        "W5_FILLABLE_JOINT_RATE": None,
        "W10_FILLABLE_JOINT_RATE": None,
        "W1_JOINT_SURVIVAL": None,
        "W2_JOINT_SURVIVAL": None,
        "W5_JOINT_SURVIVAL": None,
        "W10_JOINT_SURVIVAL": None,
        "PRIMARY_MECHANISM": None,
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "PASSIVE_WAIT_POLICY_INTEGRITY_FAILED",
        "PRIMARY_FINDING": note,
    }
    if extra:
        body.update(extra)
    return write_report(
        body,
        decision={
            "CASE": None,
            "VERDICT": "PASSIVE_WAIT_POLICY_INTEGRITY_FAILED",
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
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE PASSIVE WAIT POLICY REASSESSMENT V1", flush=True)
    print("WAIT horizon only. Frozen Corrected Passive Fill. No model. No Exact.", flush=True)

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
        print("STOP prior verdict mismatch", preg.get("VERDICT"), flush=True)
        return _integrity("STOP. Prior PASSIVE_EXECUTION_OPPORTUNITY_TOO_SPARSE required.")
    if not ROWS_PATH.is_file():
        print("STOP missing path_rows.json", flush=True)
        return _integrity("STOP. Common-cohort path_rows missing.")

    path_body = json.loads(ROWS_PATH.read_text(encoding="utf-8"))
    rows = list(path_body.get("rows") or [])
    attach_joint_labels(rows)
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
        fp = CACHE / f"{day}_WAIT.json"
        saved = _load(fp)
        if saved.get("ok") and saved.get("date") == day and saved.get("rows"):
            feat_got.append(saved)
            print(f"WAIT cache-hit {day} rows={len(saved.get('rows') or [])}", flush=True)
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
    print(f"wait harvest jobs={len(feat_jobs)}", flush=True)
    for body in _pool(process_day, feat_jobs, "WAIT", "date"):
        if body.get("ok"):
            _save_json(CACHE / f"{body.get('date')}_WAIT.json", body)
        feat_got.append(body)
    fail = [b for b in feat_got if not b.get("ok")]
    if fail or len(feat_got) != len(ELIGIBLE_DAYS):
        print("STOP wait harvest failed", [(b.get("date"), b.get("blocker")) for b in fail], flush=True)
        return _integrity("STOP. Multi-WAIT standalone fill harvest failed.")

    harvest = []
    for b in feat_got:
        harvest.extend(list(b.get("rows") or []))
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

    by_wait = {wid: wait_metrics(rows, wid) for wid in WAIT_IDS}
    parity = w1_parity(by_wait["W1"])
    print("parity", parity.get("BASE_PARITY"), parity.get("checks"), flush=True)
    if not parity.get("BASE_PARITY"):
        return _integrity(
            "STOP. W1=1s did not reproduce the frozen Passive Fill control.",
            extra={"parity": parity, "JOIN_MISS_N": miss},
        )
    print("BASE_PARITY true", flush=True)

    by_inc = {wid: incremental(rows, wid) for wid in EXTENDED_IDS}
    w1_group = fill_quality_group(rows, "W1")
    ttf = time_to_fill(rows)
    source = incremental_source_mechanism(by_inc)
    decision = decide(parity_ok=True, by_wait=by_wait, by_inc=by_inc)
    if decision.get("PRIMARY_MECHANISM") is None and decision.get("VERDICT") != "PASSIVE_WAIT_POLICY_INTEGRITY_FAILED":
        decision["PRIMARY_MECHANISM"] = source
    elif decision.get("VERDICT") not in {None, "PASSIVE_WAIT_POLICY_INTEGRITY_FAILED"}:
        decision["INCREMENTAL_SOURCE"] = source

    required = {
        "BASE_PARITY": True,
        "W1_ANY_RATE": by_wait["W1"].get("FILLABLE_ANY_COHORT_RATE"),
        "W2_ANY_RATE": by_wait["W2"].get("FILLABLE_ANY_COHORT_RATE"),
        "W5_ANY_RATE": by_wait["W5"].get("FILLABLE_ANY_COHORT_RATE"),
        "W10_ANY_RATE": by_wait["W10"].get("FILLABLE_ANY_COHORT_RATE"),
        "W1_MULTI_RATE": by_wait["W1"].get("MULTI_FILLABLE_COHORT_RATE"),
        "W2_MULTI_RATE": by_wait["W2"].get("MULTI_FILLABLE_COHORT_RATE"),
        "W5_MULTI_RATE": by_wait["W5"].get("MULTI_FILLABLE_COHORT_RATE"),
        "W10_MULTI_RATE": by_wait["W10"].get("MULTI_FILLABLE_COHORT_RATE"),
        "W1_VALID_BOARD_RATE": by_wait["W1"].get("VALID_BOARD_WITHIN_WAIT_RATE"),
        "W2_VALID_BOARD_RATE": by_wait["W2"].get("VALID_BOARD_WITHIN_WAIT_RATE"),
        "W5_VALID_BOARD_RATE": by_wait["W5"].get("VALID_BOARD_WITHIN_WAIT_RATE"),
        "W10_VALID_BOARD_RATE": by_wait["W10"].get("VALID_BOARD_WITHIN_WAIT_RATE"),
        "TIME_TO_FILL_MEDIAN_SEC": ttf.get("TIME_TO_FILL_MEDIAN_SEC"),
        "TIME_TO_FILL_P90_SEC": ttf.get("TIME_TO_FILL_P90_SEC"),
        "W2_NEW_FILL_N": by_inc["W2"].get("NEW_FILL_VS_W1_N"),
        "W5_NEW_FILL_N": by_inc["W5"].get("NEW_FILL_VS_W1_N"),
        "W10_NEW_FILL_N": by_inc["W10"].get("NEW_FILL_VS_W1_N"),
        "W2_NEW_POSTFILL_MFE": by_inc["W2"].get("POSTFILL_MFE_MEDIAN"),
        "W2_NEW_POSTFILL_DOWNSIDE": by_inc["W2"].get("POSTFILL_DOWNSIDE_MEDIAN"),
        "W5_NEW_POSTFILL_MFE": by_inc["W5"].get("POSTFILL_MFE_MEDIAN"),
        "W5_NEW_POSTFILL_DOWNSIDE": by_inc["W5"].get("POSTFILL_DOWNSIDE_MEDIAN"),
        "W10_NEW_POSTFILL_MFE": by_inc["W10"].get("POSTFILL_MFE_MEDIAN"),
        "W10_NEW_POSTFILL_DOWNSIDE": by_inc["W10"].get("POSTFILL_DOWNSIDE_MEDIAN"),
        "W1_FILLABLE_JOINT_RATE": by_wait["W1"].get("FILLABLE_JOINT_AVAILABLE_RATE"),
        "W2_FILLABLE_JOINT_RATE": by_wait["W2"].get("FILLABLE_JOINT_AVAILABLE_RATE"),
        "W5_FILLABLE_JOINT_RATE": by_wait["W5"].get("FILLABLE_JOINT_AVAILABLE_RATE"),
        "W10_FILLABLE_JOINT_RATE": by_wait["W10"].get("FILLABLE_JOINT_AVAILABLE_RATE"),
        "W1_JOINT_SURVIVAL": by_wait["W1"].get("JOINT_LABEL_SURVIVAL_AFTER_FILL_RATE"),
        "W2_JOINT_SURVIVAL": by_wait["W2"].get("JOINT_LABEL_SURVIVAL_AFTER_FILL_RATE"),
        "W5_JOINT_SURVIVAL": by_wait["W5"].get("JOINT_LABEL_SURVIVAL_AFTER_FILL_RATE"),
        "W10_JOINT_SURVIVAL": by_wait["W10"].get("JOINT_LABEL_SURVIVAL_AFTER_FILL_RATE"),
        "PRIMARY_MECHANISM": decision.get("PRIMARY_MECHANISM"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "INCREMENTAL_SOURCE": source,
        "parity": parity,
        "RUNTIME_WAIT_SEC": WAIT_SEC,
        "JOIN_MISS_N": miss,
    }
    print(
        f"CASE={decision.get('CASE')} any={ {wid: by_wait[wid].get('FILLABLE_ANY_COHORT_RATE') for wid in WAIT_IDS} } "
        f"multi={ {wid: by_wait[wid].get('MULTI_FILLABLE_COHORT_RATE') for wid in WAIT_IDS} }",
        flush=True,
    )
    quality_rows = [w1_group] + [
        {
            "group_id": f"{wid}_NEW",
            "N": by_inc[wid].get("NEW_FILL_VS_W1_N"),
            "OLD_JOINT_POSITIVE_N": by_inc[wid].get("OLD_JOINT_POSITIVE_N"),
            "OLD_JOINT_POSITIVE_RATE": by_inc[wid].get("OLD_JOINT_POSITIVE_RATE"),
            "POSTFILL_MFE_MEDIAN": by_inc[wid].get("POSTFILL_MFE_MEDIAN"),
            "POSTFILL_MFE_MEAN": by_inc[wid].get("POSTFILL_MFE_MEAN"),
            "POSTFILL_DOWNSIDE_MEDIAN": by_inc[wid].get("POSTFILL_DOWNSIDE_MEDIAN"),
            "POSTFILL_DOWNSIDE_MEAN": by_inc[wid].get("POSTFILL_DOWNSIDE_MEAN"),
            "POSTFILL_MFE_N": by_inc[wid].get("POSTFILL_MFE_N"),
            "POSTFILL_DOWNSIDE_N": by_inc[wid].get("POSTFILL_DOWNSIDE_N"),
            "NEW_FILL_FROM_NO_VALID_BOARD_N": by_inc[wid].get("NEW_FILL_FROM_NO_VALID_BOARD_N"),
            "NEW_FILL_FROM_PRIOR_NO_ASK_CROSS_N": by_inc[wid].get("NEW_FILL_FROM_PRIOR_NO_ASK_CROSS_N"),
        }
        for wid in EXTENDED_IDS
    ]
    return write_report(
        required,
        decision=decision,
        extra={
            "waits": by_wait,
            "incremental": by_inc,
            "w1_fill_group": w1_group,
            "time_to_fill": ttf,
            "incremental_source": source,
        },
        sheets_extra={
            "W1Parity": kv_rows(parity),
            "Waits": [by_wait[wid] for wid in WAIT_IDS],
            "Incremental": [by_inc[wid] for wid in EXTENDED_IDS],
            "QualityGroups": quality_rows,
            "TimeToFill": kv_rows(ttf),
            "Integrity": kv_rows(
                {
                    "JOIN_MISS_N": miss,
                    "RUNTIME_WAIT_SEC": WAIT_SEC,
                    "BASE_PARITY": True,
                    "WAIT_POLICY_ADOPTED": WAIT_POLICY_ADOPTED,
                    "REPRICING": REPRICING,
                    "LIMIT_CHANGED": LIMIT_CHANGED,
                    "FILL_RULE_CHANGED": FILL_RULE_CHANGED,
                    "POSITION_CAP_APPLIED": POSITION_CAP_APPLIED,
                    "OCCUPANCY_APPLIED": OCCUPANCY_APPLIED,
                    "REENTRY_APPLIED": REENTRY_APPLIED,
                }
            ),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
