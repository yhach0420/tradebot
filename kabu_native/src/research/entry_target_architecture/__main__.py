"""Offline Canonical ENTRY target learnability. No Runtime write. No Paper. No Exact. No C4."""
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
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.entry_objective_redesign_c3.oof import spec_grid, spec_label
from research.entry_target_architecture import (
    ANALYSIS_ID,
    BEST_SPEC_ADOPTED,
    C14_CHANGED,
    C14_ID,
    C3_RETRAIN_VERDICT_MAINTAINED,
    C4_STARTED,
    ELIGIBLE_DAYS,
    EXACT_RAN,
    EXECUTION_AWARE_MODEL_CREATED,
    GBM_USED,
    HORIZON_SEARCH,
    MAX_WORKERS,
    NESTED_SPEC_SELECTOR,
    NEW_FEATURE_CREATED,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    NN_USED,
    OPVAL_OPERATED,
    PAIRWISE_USED,
    PAPER_OPERATED,
    PARITY_VERDICT_MAINTAINED,
    PNL_USED,
    RANK_SHAPE_VERDICT_MAINTAINED,
    RF_USED,
    RUNTIME_CHANGED,
    TARGET_IDS,
    TOP1_OBJECTIVE_RESUMED,
    TOP1_VERDICT_MAINTAINED,
    TRUE_OOS,
    WEIGHT_CHANGED,
)
from research.entry_target_architecture.analyze import (
    aggregate_target,
    cohort_counts,
    decide,
    flatten_spec_rows,
    path_integrity,
)
from research.entry_target_architecture.oof import process_fixed_spec
from research.entry_target_architecture.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from research.entry_target_architecture.replay import process_day as process_path
from small_paper.v1r_native_entry_live import FEATURE_ORDER

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
CACHE = NATIVE / "results" / "research" / "_work_cache" / "entry_target_architecture"


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
        futs = {ex.submit(fn, job): job.get("date") or job.get("spec_id") for job in jobs}
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, "date": key, "spec_id": key, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            print(
                f"done {label} {body.get('spec_id') or body.get('date') or key} ok={body.get('ok')} "
                f"n={len(body.get('rows') or [])} sec={body.get('elapsed_sec')} blocker={body.get('blocker')}",
                flush=True,
            )
    return out


def _path_cache(day: str) -> Path:
    return CACHE / f"{day}_PATH.json"


def _load_path_cache(day: str) -> dict | None:
    fp = _path_cache(day)
    if not fp.is_file():
        return None
    try:
        body = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    if body.get("ok") and body.get("date") == day and body.get("stage") == "PATH" and body.get("rows"):
        return body
    return None


def _fixed_cache_name(spec_id: str) -> str:
    return "fixed_" + spec_id.replace("|", "_").replace(" ", "") + ".json"


def _spec_sheet_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        rec = dict(r)
        rec.pop("days", None)
        out.append(rec)
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
                "SOURCE": "CanonicalEngine only",
                "CLOCK": "CURRENT IRREGULAR",
                "ELIGIBILITY": "Exact executable-at-decision",
                "FEATURES": "F0_CURRENT6 / F1_C2_6 / F2_UNION",
                "MODEL": "Ridge 27 fixed specs. No nested selector. No RF/GBM/NN/pairwise.",
                "NORMALIZATION": "none / cross_sectional_z / robust_cross_sectional_rank",
                "ALPHA": "0.1 / 1 / 10",
                "TARGETS": "T0 terminal 600 / T1 MFE 600 / T2 min 600 / T3 MFE+MAE 1:1 / T4 r(120)",
                "RANK": "eligible → feature → score → rank → Target join. NO TARGET BACKFILL.",
                "NESTED_SPEC_SELECTOR": NESTED_SPEC_SELECTOR,
                "BEST_SPEC_ADOPTED": BEST_SPEC_ADOPTED,
                "EXACT_RAN": EXACT_RAN,
                "C3_RETRAIN_VERDICT_MAINTAINED": C3_RETRAIN_VERDICT_MAINTAINED,
                "TOP1_VERDICT_MAINTAINED": TOP1_VERDICT_MAINTAINED,
                "RANK_SHAPE_VERDICT_MAINTAINED": RANK_SHAPE_VERDICT_MAINTAINED,
                "PARITY_VERDICT_MAINTAINED": PARITY_VERDICT_MAINTAINED,
            }
        ),
        "Integrity": [{"empty": True}],
        "Cohort": [{"empty": True}],
        "T0Specs": [{"empty": True}],
        "T1Specs": [{"empty": True}],
        "T2Specs": [{"empty": True}],
        "T3Specs": [{"empty": True}],
        "T4Specs": [{"empty": True}],
        "TargetAgg": [{"empty": True}],
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
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "RF_USED": RF_USED,
                "GBM_USED": GBM_USED,
                "NN_USED": NN_USED,
                "PAIRWISE_USED": PAIRWISE_USED,
                "NESTED_SPEC_SELECTOR": NESTED_SPEC_SELECTOR,
                "BEST_SPEC_ADOPTED": BEST_SPEC_ADOPTED,
                "PNL_USED": PNL_USED,
                "TOP1_OBJECTIVE_RESUMED": TOP1_OBJECTIVE_RESUMED,
                "HORIZON_SEARCH": HORIZON_SEARCH,
                "WEIGHT_CHANGED": WEIGHT_CHANGED,
                "EXECUTION_AWARE_MODEL_CREATED": EXECUTION_AWARE_MODEL_CREATED,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"VERDICT={required.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print("STOP. No Exact. No new model. No C4. Runtime unchanged. submit/cancel/live=0/0/0.", flush=True)
    fail = required.get("VERDICT") == "ENTRY_TARGET_AUDIT_INTEGRITY_FAILED"
    return 2 if fail else 0


def required_from_targets(
    *,
    cohort: dict[str, Any],
    by_tid: dict[str, dict[str, Any]],
    decision: dict[str, Any],
    pass_ids: list[str],
) -> dict[str, Any]:
    req: dict[str, Any] = {
        "COMMON_TARGET_COHORT_N": cohort.get("COMMON_TARGET_COHORT_N"),
        "COMMON_TARGET_COHORT_EXECUTABLE_N": cohort.get("COMMON_TARGET_COHORT_EXECUTABLE_N"),
    }
    for tid in TARGET_IDS:
        short = tid.split("_", 1)[0]
        agg = by_tid.get(tid) or {}
        req[f"{short}_MEDIAN_TOP3_DELTA"] = agg.get("MEDIAN_SPEC_TOP3_DELTA")
        req[f"{short}_TOP3_GT_CURRENT_SPEC_N"] = agg.get("TOP3_GT_CURRENT_SPEC_N")
        req[f"{short}_CONSENSUS_POS_DAYS"] = agg.get("CONSENSUS_POSITIVE_DAYS")
        req[f"{short}_TARGET_PASS"] = bool(agg.get("TARGET_PASS"))
        req[f"{short}_MEDIAN_SPEC_TOP3_ABSOLUTE_UPLIFT"] = agg.get("MEDIAN_SPEC_TOP3_ABSOLUTE_UPLIFT")
        req[f"{short}_MEDIAN_SPEC_TOP5_DELTA"] = agg.get("MEDIAN_SPEC_TOP5_DELTA")
        req[f"{short}_MEDIAN_SPEC_SPEARMAN"] = agg.get("MEDIAN_SPEC_SPEARMAN")
        req[f"{short}_TOP3_POSITIVE_ABSOLUTE_SPEC_N"] = agg.get("TOP3_POSITIVE_ABSOLUTE_SPEC_N")
        req[f"{short}_CONSENSUS_NEGATIVE_DAYS"] = agg.get("CONSENSUS_NEGATIVE_DAYS")
        req[f"{short}_CONSENSUS_EX_BEST_DAY"] = agg.get("CONSENSUS_EX_BEST_DAY")
        req[f"{short}_CONSENSUS_EX_TOP3_DAYS"] = agg.get("CONSENSUS_EX_TOP3_DAYS")
        req[f"{short}_GATE_FAIL"] = agg.get("gate_fail")
    t0_pass = bool((by_tid.get("T0_TERMINAL_600") or {}).get("TARGET_PASS"))
    req["LEARNABLE_TARGETS"] = list(pass_ids)
    req["LEARNABLE_TARGET_N"] = len(pass_ids)
    req["TERMINAL_RETURN_FAILURE_CONFIRMED"] = not t0_pass
    req["RISK_ONLY_STRUCTURE"] = pass_ids == ["T2_DOWNSIDE_AVOID_600"]
    req["PRIMARY_FINDING"] = decision.get("PRIMARY_FINDING")
    req["NEXT_RESEARCH"] = decision.get("NEXT_RESEARCH")
    req["TRUE_OOS"] = TRUE_OOS
    req["NEW_FORWARD_N"] = NEW_FORWARD_N
    req["VERDICT"] = decision.get("VERDICT")
    return req


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE CANONICAL ENTRY TARGET LEARNABILITY AUDIT V2", flush=True)
    print("27 fixed specs × 5 targets. Common 600s cohort. No nested selector. No Exact.", flush=True)

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
        return 2
    by_date = {r["date"]: r for r in elig}

    jobs = []
    got = []
    for day in ELIGIBLE_DAYS:
        cached = _load_path_cache(day)
        if cached:
            got.append(cached)
            print(f"PATH {day} cache-hit rows={len(cached.get('rows') or [])}", flush=True)
            continue
        r = by_date[day]
        jobs.append({"date": day, "capture_path": r["capture_path"], "universe": r["universe_symbols"]})
    print(f"path harvest jobs={len(jobs)}", flush=True)
    for body in _pool(process_path, jobs, "PATH"):
        if body.get("ok"):
            _save_json(_path_cache(str(body.get("date"))), body)
        got.append(body)
    fail = [b for b in got if not b.get("ok")]
    if fail or len(got) != len(ELIGIBLE_DAYS):
        print("STOP path harvest failed", [(b.get("date"), b.get("blocker")) for b in fail], flush=True)
        return write_report(
            {
                "COMMON_TARGET_COHORT_N": None,
                "VERDICT": "ENTRY_TARGET_AUDIT_INTEGRITY_FAILED",
                "PRIMARY_FINDING": "CanonicalEngine path harvest failed.",
                "NEXT_RESEARCH": "NONE",
                "TRUE_OOS": TRUE_OOS,
                "NEW_FORWARD_N": NEW_FORWARD_N,
            },
            decision={
                "CASE": None,
                "VERDICT": "ENTRY_TARGET_AUDIT_INTEGRITY_FAILED",
                "NEXT_RESEARCH": "NONE",
                "PRIMARY_FINDING": "CanonicalEngine path harvest failed.",
                "note": "STOP. Harvest failed.",
            },
        )

    rows: list[dict] = []
    for b in got:
        rows.extend(b.get("rows") or [])
    integ = path_integrity(rows)
    cohort = cohort_counts(rows)
    print(
        f"harvest={len(rows)} common={cohort.get('COMMON_TARGET_COHORT_N')} "
        f"exec_common={cohort.get('COMMON_TARGET_COHORT_EXECUTABLE_N')} "
        f"future={integ.get('FUTURE_EVENT_USE_N')} unexpected={integ.get('UNEXPECTED_TARGET_MISSING_N')} "
        f"itayose={integ.get('ITAYOSE_CONTAMINATION_N')} special={integ.get('SPECIAL_CONTAMINATION_N')} "
        f"close={integ.get('CLOSING_AUCTION_CONTAMINATION_N')} carry={integ.get('SESSION_CARRY_N')}",
        flush=True,
    )
    if not integ.get("ok"):
        return write_report(
            {
                **cohort,
                **integ,
                "VERDICT": "ENTRY_TARGET_AUDIT_INTEGRITY_FAILED",
                "PRIMARY_FINDING": "Target integrity failed. STOP before LODO.",
                "NEXT_RESEARCH": "NONE",
                "TRUE_OOS": TRUE_OOS,
                "NEW_FORWARD_N": NEW_FORWARD_N,
                "LEARNABLE_TARGETS": [],
                "LEARNABLE_TARGET_N": 0,
            },
            decision={
                "CASE": None,
                "VERDICT": "ENTRY_TARGET_AUDIT_INTEGRITY_FAILED",
                "NEXT_RESEARCH": "NONE",
                "PRIMARY_FINDING": "Target integrity failed. STOP before LODO.",
                "note": "STOP. Integrity fail.",
            },
            extra={"integrity": integ, "cohort": cohort},
            sheets_extra={"Integrity": kv_rows(integ), "Cohort": kv_rows(cohort)},
        )

    lodo_rows = []
    for r in rows:
        if not r.get("executable_at_t0"):
            continue
        if not r.get("common_cohort"):
            continue
        rec = dict(r)
        rec["modeling_bucket"] = "PRIMARY_MODELING_ELIGIBLE"
        lodo_rows.append(rec)
    rows_path = CACHE / "path_rows.json"
    _save_json(rows_path, {"ok": True, "n": len(lodo_rows), "rows": lodo_rows})
    print(f"LODO rows={len(lodo_rows)}", flush=True)

    spec_jobs = []
    spec_got = []
    for spec in grid:
        sid = spec_label(spec)
        fp = CACHE / _fixed_cache_name(sid)
        saved = _load(fp)
        if saved.get("ok") and saved.get("spec_id") == sid and saved.get("by_target"):
            spec_got.append(saved)
            print(f"fixed cache-hit {sid}", flush=True)
            continue
        spec_jobs.append(
            {
                "spec": spec,
                "spec_id": sid,
                "rows_path": str(rows_path),
                "days": list(ELIGIBLE_DAYS),
            }
        )
    print(f"fixed-spec jobs={len(spec_jobs)}", flush=True)
    for body in _pool(process_fixed_spec, spec_jobs, "SPEC"):
        if body.get("ok"):
            _save_json(CACHE / _fixed_cache_name(str(body.get("spec_id"))), body)
        spec_got.append(body)
    spec_fail = [b for b in spec_got if not b.get("ok")]
    if spec_fail or len(spec_got) != 27:
        print("STOP spec LODO failed", [(b.get("spec_id"), b.get("blocker")) for b in spec_fail], flush=True)
        return write_report(
            {
                **cohort,
                **integ,
                "VERDICT": "ENTRY_TARGET_AUDIT_INTEGRITY_FAILED",
                "PRIMARY_FINDING": "Fixed-spec LODO failed.",
                "NEXT_RESEARCH": "NONE",
                "TRUE_OOS": TRUE_OOS,
                "NEW_FORWARD_N": NEW_FORWARD_N,
            },
            decision={
                "CASE": None,
                "VERDICT": "ENTRY_TARGET_AUDIT_INTEGRITY_FAILED",
                "NEXT_RESEARCH": "NONE",
                "PRIMARY_FINDING": "Fixed-spec LODO failed.",
                "note": "STOP. LODO fail.",
            },
            extra={"integrity": integ, "cohort": cohort},
            sheets_extra={"Integrity": kv_rows(integ), "Cohort": kv_rows(cohort)},
        )

    by_spec = flatten_spec_rows(spec_got)
    by_tid = {}
    pass_ids = []
    consensus_rows = []
    agg_rows = []
    for tid in TARGET_IDS:
        agg = aggregate_target(by_spec[tid], list(ELIGIBLE_DAYS))
        slim = {k: v for k, v in agg.items() if k != "CONSENSUS_DAILY_TOP3_DELTA"}
        slim["target_id"] = tid
        by_tid[tid] = agg
        agg_rows.append(slim)
        if agg.get("TARGET_PASS"):
            pass_ids.append(tid)
        for rec in agg.get("CONSENSUS_DAILY_TOP3_DELTA") or []:
            consensus_rows.append({"target_id": tid, **rec})
        print(
            f"{tid} pass={agg.get('TARGET_PASS')} med_top3_delta={agg.get('MEDIAN_SPEC_TOP3_DELTA')} "
            f"gt={agg.get('TOP3_GT_CURRENT_SPEC_N')} pos_days={agg.get('CONSENSUS_POSITIVE_DAYS')} "
            f"fail={agg.get('gate_fail')}",
            flush=True,
        )

    decision = decide(pass_ids)
    required = required_from_targets(cohort=cohort, by_tid=by_tid, decision=decision, pass_ids=pass_ids)
    required.update(
        {
            "FUTURE_EVENT_USE_N": integ.get("FUTURE_EVENT_USE_N"),
            "UNEXPECTED_TARGET_MISSING_N": integ.get("UNEXPECTED_TARGET_MISSING_N"),
            "ITAYOSE_CONTAMINATION_N": integ.get("ITAYOSE_CONTAMINATION_N"),
            "SPECIAL_CONTAMINATION_N": integ.get("SPECIAL_CONTAMINATION_N"),
            "CLOSING_AUCTION_CONTAMINATION_N": integ.get("CLOSING_AUCTION_CONTAMINATION_N"),
            "SESSION_CARRY_N": integ.get("SESSION_CARRY_N"),
        }
    )
    sheet_map = {
        "T0Specs": _spec_sheet_rows(by_spec["T0_TERMINAL_600"]),
        "T1Specs": _spec_sheet_rows(by_spec["T1_MFE_600"]),
        "T2Specs": _spec_sheet_rows(by_spec["T2_DOWNSIDE_AVOID_600"]),
        "T3Specs": _spec_sheet_rows(by_spec["T3_PATH_QUALITY_600"]),
        "T4Specs": _spec_sheet_rows(by_spec["T4_EARLY_120"]),
    }
    return write_report(
        required,
        decision=decision,
        extra={
            "integrity": integ,
            "cohort": cohort,
            "targets": {tid: {k: v for k, v in agg.items() if k != "CONSENSUS_DAILY_TOP3_DELTA"} for tid, agg in by_tid.items()},
            "native_coverage": {k: v for k, v in cohort.items() if "NATIVE" in k},
        },
        sheets_extra={
            "Integrity": kv_rows(integ),
            "Cohort": kv_rows(cohort),
            **sheet_map,
            "TargetAgg": agg_rows,
            "ConsensusDays": consensus_rows or [{"empty": True}],
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
