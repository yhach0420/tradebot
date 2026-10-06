"""Offline Primary Target precommit. No Runtime write. No Paper. No Exact. No C4."""
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
from research.target_architecture_selection import (
    ANALYSIS_ID,
    BEST_SPEC_ADOPTED,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    ELIGIBLE_DAYS,
    EXACT_RAN,
    EXECUTION_AWARE_MODEL_CREATED,
    HORIZON_CHANGED,
    LEARNABILITY_VERDICT_MAINTAINED,
    MAX_WORKERS,
    NESTED_SPEC_SELECTOR,
    NEW_FEATURE_CREATED,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    OPVAL_OPERATED,
    PAPER_OPERATED,
    PATH_ORDER_USED,
    PNL_USED,
    PRIOR_LEARNABLE,
    PRIOR_VERDICT_REQUIRED,
    RUNTIME_CHANGED,
    T0_RESCUED,
    T4_RESCUED,
    TARGET_SPECIFIC_MODEL_STARTED,
    TRUE_OOS,
    WEIGHT_CHANGED,
)
from research.target_architecture_selection.analyze import (
    aggregate_component,
    decide,
    slim_component,
    spec_component_rows,
    t1_clean,
    t1_upside_predictable,
    t2_clean,
    t2_downside_predictable,
    t3_alignment,
)
from research.target_architecture_selection.oof import process_fixed_spec
from research.target_architecture_selection.publish import (
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
PRIOR = NATIVE / "results" / "research" / "entry_target_architecture" / "report.json"
ROWS_PATH = NATIVE / "results" / "research" / "_work_cache" / "entry_target_architecture" / "path_rows.json"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "target_architecture_selection"


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
    return "comp_" + spec_id.replace("|", "_").replace(" ", "") + ".json"


def _spec_sheet(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
                "SOURCE": "CanonicalEngine. Reuse common 600s cohort path_rows.",
                "CLOCK": "CURRENT IRREGULAR",
                "SPECS": "existing 27 Ridge. No nested selector. No best-spec adoption.",
                "T1": "OPPORTUNITY_ONLY = MFE_600",
                "T2": "RISK_ONLY = MAE_600 higher=less downside",
                "T3": "BALANCED_PATH_UTILITY = T1+T2 scalar. Not an independent alpha family.",
                "RANK": "score rank fixed, then join MFE/DOWNSIDE/PATH. NO BACKFILL.",
                "SELECTION": "cross-component alignment only. No raw magnitude.",
                "NESTED_SPEC_SELECTOR": NESTED_SPEC_SELECTOR,
                "BEST_SPEC_ADOPTED": BEST_SPEC_ADOPTED,
                "EXACT_RAN": EXACT_RAN,
                "PATH_ORDER_USED": PATH_ORDER_USED,
                "T0_RESCUED": T0_RESCUED,
                "T4_RESCUED": T4_RESCUED,
                "LEARNABILITY_VERDICT_MAINTAINED": LEARNABILITY_VERDICT_MAINTAINED,
                "TARGET_SPECIFIC_MODEL_STARTED": TARGET_SPECIFIC_MODEL_STARTED,
            }
        ),
        "T3Components": [{"empty": True}],
        "T1Cross": [{"empty": True}],
        "T2Cross": [{"empty": True}],
        "T3Gates": [{"empty": True}],
        "T1Gates": [{"empty": True}],
        "T2Gates": [{"empty": True}],
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
                "NESTED_SPEC_SELECTOR": NESTED_SPEC_SELECTOR,
                "BEST_SPEC_ADOPTED": BEST_SPEC_ADOPTED,
                "PNL_USED": PNL_USED,
                "T0_RESCUED": T0_RESCUED,
                "T4_RESCUED": T4_RESCUED,
                "HORIZON_CHANGED": HORIZON_CHANGED,
                "WEIGHT_CHANGED": WEIGHT_CHANGED,
                "PATH_ORDER_USED": PATH_ORDER_USED,
                "EXECUTION_AWARE_MODEL_CREATED": EXECUTION_AWARE_MODEL_CREATED,
                "TARGET_SPECIFIC_MODEL_STARTED": TARGET_SPECIFIC_MODEL_STARTED,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"VERDICT={required.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(
        "STOP. No target-specific model. No Exact. No C4. Runtime unchanged. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "TARGET_ARCHITECTURE_SELECTION_FAILED"
    return 2 if fail else 0


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE TARGET ARCHITECTURE PRECOMMIT SELECTION V2", flush=True)
    print("27 fixed specs. Rank then join MFE/DOWNSIDE. No nested selector. No Exact.", flush=True)

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
        print("STOP prior learnability verdict mismatch", preg.get("VERDICT"), flush=True)
        return write_report(
            {
                "VERDICT": "TARGET_ARCHITECTURE_SELECTION_FAILED",
                "PRIMARY_TARGET": "NONE",
                "NEXT_RESEARCH": "NONE",
                "TRUE_OOS": TRUE_OOS,
                "NEW_FORWARD_N": NEW_FORWARD_N,
                "note": f"Prior verdict {preg.get('VERDICT')} != {PRIOR_VERDICT_REQUIRED}",
            },
            decision={
                "CASE": None,
                "VERDICT": "TARGET_ARCHITECTURE_SELECTION_FAILED",
                "PRIMARY_TARGET": "NONE",
                "NEXT_RESEARCH": "NONE",
                "note": "STOP. Prior learnability audit not usable.",
            },
        )
    learnable = list(preg.get("LEARNABLE_TARGETS") or [])
    if learnable != list(PRIOR_LEARNABLE):
        print("STOP prior LEARNABLE_TARGETS mismatch", learnable, flush=True)
        return write_report(
            {
                "VERDICT": "TARGET_ARCHITECTURE_SELECTION_FAILED",
                "PRIMARY_TARGET": "NONE",
                "NEXT_RESEARCH": "NONE",
                "TRUE_OOS": TRUE_OOS,
                "NEW_FORWARD_N": NEW_FORWARD_N,
            },
            decision={
                "CASE": None,
                "VERDICT": "TARGET_ARCHITECTURE_SELECTION_FAILED",
                "PRIMARY_TARGET": "NONE",
                "NEXT_RESEARCH": "NONE",
                "note": "STOP. Prior LEARNABLE_TARGETS mismatch.",
            },
        )
    t1_aj = bool(preg.get("T1_TARGET_PASS"))
    t2_aj = bool(preg.get("T2_TARGET_PASS"))
    t3_aj = bool(preg.get("T3_TARGET_PASS"))
    if not (t1_aj and t2_aj and t3_aj):
        print("STOP frozen A-J PASS flags missing", t1_aj, t2_aj, t3_aj, flush=True)
        return write_report(
            {
                "VERDICT": "TARGET_ARCHITECTURE_SELECTION_FAILED",
                "PRIMARY_TARGET": "NONE",
                "NEXT_RESEARCH": "NONE",
                "TRUE_OOS": TRUE_OOS,
                "NEW_FORWARD_N": NEW_FORWARD_N,
            },
            decision={
                "CASE": None,
                "VERDICT": "TARGET_ARCHITECTURE_SELECTION_FAILED",
                "PRIMARY_TARGET": "NONE",
                "NEXT_RESEARCH": "NONE",
                "note": "STOP. Frozen T1/T2/T3 A-J PASS required.",
            },
        )
    if not ROWS_PATH.is_file():
        print("STOP missing path_rows.json", flush=True)
        return write_report(
            {
                "VERDICT": "TARGET_ARCHITECTURE_SELECTION_FAILED",
                "PRIMARY_TARGET": "NONE",
                "NEXT_RESEARCH": "NONE",
                "TRUE_OOS": TRUE_OOS,
                "NEW_FORWARD_N": NEW_FORWARD_N,
            },
            decision={
                "CASE": None,
                "VERDICT": "TARGET_ARCHITECTURE_SELECTION_FAILED",
                "PRIMARY_TARGET": "NONE",
                "NEXT_RESEARCH": "NONE",
                "note": "STOP. Common-cohort path_rows missing.",
            },
        )

    jobs = []
    got = []
    for spec in grid:
        sid = spec_label(spec)
        fp = CACHE / _fixed_cache_name(sid)
        saved = _load(fp)
        if saved.get("ok") and saved.get("spec_id") == sid and saved.get("by_train"):
            got.append(saved)
            print(f"comp cache-hit {sid}", flush=True)
            continue
        jobs.append({"spec": spec, "spec_id": sid, "rows_path": str(ROWS_PATH), "days": list(ELIGIBLE_DAYS)})
    print(f"component-spec jobs={len(jobs)}", flush=True)
    for body in _pool(process_fixed_spec, jobs, "COMP"):
        if body.get("ok"):
            _save_json(CACHE / _fixed_cache_name(str(body.get("spec_id"))), body)
        got.append(body)
    fail = [b for b in got if not b.get("ok")]
    if fail or len(got) != 27:
        print("STOP component LODO failed", [(b.get("spec_id"), b.get("blocker")) for b in fail], flush=True)
        return write_report(
            {
                "VERDICT": "TARGET_ARCHITECTURE_SELECTION_FAILED",
                "PRIMARY_TARGET": "NONE",
                "NEXT_RESEARCH": "NONE",
                "TRUE_OOS": TRUE_OOS,
                "NEW_FORWARD_N": NEW_FORWARD_N,
            },
            decision={
                "CASE": None,
                "VERDICT": "TARGET_ARCHITECTURE_SELECTION_FAILED",
                "PRIMARY_TARGET": "NONE",
                "NEXT_RESEARCH": "NONE",
                "note": "STOP. Component LODO failed.",
            },
        )

    t3_mfe_rows = spec_component_rows(got, "T3_PATH_QUALITY_600", "MFE")
    t3_dn_rows = spec_component_rows(got, "T3_PATH_QUALITY_600", "DOWNSIDE")
    t1_mfe_rows = spec_component_rows(got, "T1_MFE_600", "MFE")
    t1_dn_rows = spec_component_rows(got, "T1_MFE_600", "DOWNSIDE")
    t2_dn_rows = spec_component_rows(got, "T2_DOWNSIDE_AVOID_600", "DOWNSIDE")
    t2_mfe_rows = spec_component_rows(got, "T2_DOWNSIDE_AVOID_600", "MFE")

    t3_mfe = aggregate_component(t3_mfe_rows)
    t3_dn = aggregate_component(t3_dn_rows)
    t1_mfe = aggregate_component(t1_mfe_rows)
    t1_dn = aggregate_component(t1_dn_rows)
    t2_dn = aggregate_component(t2_dn_rows)
    t2_mfe = aggregate_component(t2_mfe_rows)

    t3g = t3_alignment(t3_aj_pass=t3_aj, mfe=t3_mfe, down=t3_dn)
    t1g = t1_clean(t1_aj_pass=t1_aj, mfe=t1_mfe, down=t1_dn)
    t2g = t2_clean(t2_aj_pass=t2_aj, down=t2_dn, mfe=t2_mfe)
    t1_up = t1_upside_predictable(t1_aj_pass=t1_aj, mfe=t1_mfe)
    t2_dn_ok = t2_downside_predictable(t2_aj_pass=t2_aj, down=t2_dn)
    decision = decide(
        t3_pass=bool(t3g.get("T3_COMPONENT_ALIGNMENT_PASS")),
        t1_clean_flag=bool(t1g.get("T1_CLEAN_OPPORTUNITY")),
        t2_clean_flag=bool(t2g.get("T2_CLEAN_RISK")),
        t1_upside=t1_up,
        t2_downside=t2_dn_ok,
    )
    print(
        f"T3_ALIGN={t3g.get('T3_COMPONENT_ALIGNMENT_PASS')} fail={t3g.get('gate_fail')} "
        f"T1_CLEAN={t1g.get('T1_CLEAN_OPPORTUNITY')} T2_CLEAN={t2g.get('T2_CLEAN_RISK')}",
        flush=True,
    )

    consensus_rows = []
    for label, agg in (
        ("T3_MFE", t3_mfe),
        ("T3_DOWNSIDE", t3_dn),
        ("T1_MFE", t1_mfe),
        ("T1_DOWNSIDE", t1_dn),
        ("T2_DOWNSIDE", t2_dn),
        ("T2_MFE", t2_mfe),
    ):
        for rec in agg.get("CONSENSUS_DAILY") or []:
            consensus_rows.append({"series": label, **rec})

    required = {
        "T3_MFE_MEDIAN_SPEC_DELTA": t3_mfe.get("MEDIAN_SPEC_DELTA"),
        "T3_DOWNSIDE_MEDIAN_SPEC_DELTA": t3_dn.get("MEDIAN_SPEC_DELTA"),
        "T3_MFE_POSITIVE_SPEC_N": t3_mfe.get("POSITIVE_SPEC_N"),
        "T3_DOWNSIDE_POSITIVE_SPEC_N": t3_dn.get("POSITIVE_SPEC_N"),
        "T3_MFE_CONSENSUS_POS_DAYS": t3_mfe.get("CONSENSUS_POSITIVE_DAYS"),
        "T3_MFE_CONSENSUS_NEG_DAYS": t3_mfe.get("CONSENSUS_NEGATIVE_DAYS"),
        "T3_DOWNSIDE_CONSENSUS_POS_DAYS": t3_dn.get("CONSENSUS_POSITIVE_DAYS"),
        "T3_DOWNSIDE_CONSENSUS_NEG_DAYS": t3_dn.get("CONSENSUS_NEGATIVE_DAYS"),
        "T3_MFE_EX_BEST_DAY": t3_mfe.get("CONSENSUS_EX_BEST_DAY"),
        "T3_MFE_EX_TOP3_DAYS": t3_mfe.get("CONSENSUS_EX_TOP3_DAYS"),
        "T3_DOWNSIDE_EX_BEST_DAY": t3_dn.get("CONSENSUS_EX_BEST_DAY"),
        "T3_DOWNSIDE_EX_TOP3_DAYS": t3_dn.get("CONSENSUS_EX_TOP3_DAYS"),
        "T3_COMPONENT_ALIGNMENT_PASS": t3g.get("T3_COMPONENT_ALIGNMENT_PASS"),
        "T3_GATE_FAIL": t3g.get("gate_fail"),
        "T1_MFE_MEDIAN_DELTA": t1_mfe.get("MEDIAN_SPEC_DELTA"),
        "T1_DOWNSIDE_MEDIAN_DELTA": t1_dn.get("MEDIAN_SPEC_DELTA"),
        "T1_DOWNSIDE_POSITIVE_SPEC_N": t1_dn.get("POSITIVE_SPEC_N"),
        "T1_DOWNSIDE_CONSENSUS_POS_DAYS": t1_dn.get("CONSENSUS_POSITIVE_DAYS"),
        "T1_DOWNSIDE_EX_BEST_DAY": t1_dn.get("CONSENSUS_EX_BEST_DAY"),
        "T1_DOWNSIDE_EX_TOP3_DAYS": t1_dn.get("CONSENSUS_EX_TOP3_DAYS"),
        "T1_CLEAN_OPPORTUNITY": t1g.get("T1_CLEAN_OPPORTUNITY"),
        "T1_GATE_FAIL": t1g.get("gate_fail"),
        "T2_DOWNSIDE_MEDIAN_DELTA": t2_dn.get("MEDIAN_SPEC_DELTA"),
        "T2_MFE_MEDIAN_DELTA": t2_mfe.get("MEDIAN_SPEC_DELTA"),
        "T2_MFE_POSITIVE_SPEC_N": t2_mfe.get("POSITIVE_SPEC_N"),
        "T2_MFE_CONSENSUS_POS_DAYS": t2_mfe.get("CONSENSUS_POSITIVE_DAYS"),
        "T2_MFE_EX_BEST_DAY": t2_mfe.get("CONSENSUS_EX_BEST_DAY"),
        "T2_MFE_EX_TOP3_DAYS": t2_mfe.get("CONSENSUS_EX_TOP3_DAYS"),
        "T2_CLEAN_RISK": t2g.get("T2_CLEAN_RISK"),
        "T2_GATE_FAIL": t2g.get("gate_fail"),
        "PRIMARY_TARGET": decision.get("PRIMARY_TARGET"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "T1_TARGET_AJ_PASS_FROZEN": t1_aj,
        "T2_TARGET_AJ_PASS_FROZEN": t2_aj,
        "T3_TARGET_AJ_PASS_FROZEN": t3_aj,
        "COMMON_TARGET_COHORT_N": preg.get("COMMON_TARGET_COHORT_N"),
    }
    t3_sheet = []
    for a, b in zip(t3_mfe_rows, t3_dn_rows):
        t3_sheet.append(
            {
                "spec_id": a.get("spec_id"),
                "MFE_TOP3_DELTA": a.get("TOP3_DELTA"),
                "DOWNSIDE_TOP3_DELTA": b.get("TOP3_DELTA"),
                "MATCHED_RANKING_ROWS": a.get("MATCHED_RANKING_ROWS"),
            }
        )
    t1_sheet = []
    for a, b in zip(t1_mfe_rows, t1_dn_rows):
        t1_sheet.append(
            {
                "spec_id": a.get("spec_id"),
                "MFE_TOP3_DELTA": a.get("TOP3_DELTA"),
                "DOWNSIDE_TOP3_DELTA": b.get("TOP3_DELTA"),
                "MATCHED_RANKING_ROWS": a.get("MATCHED_RANKING_ROWS"),
            }
        )
    t2_sheet = []
    for a, b in zip(t2_dn_rows, t2_mfe_rows):
        t2_sheet.append(
            {
                "spec_id": a.get("spec_id"),
                "DOWNSIDE_TOP3_DELTA": a.get("TOP3_DELTA"),
                "MFE_TOP3_DELTA": b.get("TOP3_DELTA"),
                "MATCHED_RANKING_ROWS": a.get("MATCHED_RANKING_ROWS"),
            }
        )
    return write_report(
        required,
        decision=decision,
        extra={
            "t3_mfe": slim_component(t3_mfe),
            "t3_downside": slim_component(t3_dn),
            "t1_mfe": slim_component(t1_mfe),
            "t1_downside": slim_component(t1_dn),
            "t2_downside": slim_component(t2_dn),
            "t2_mfe": slim_component(t2_mfe),
            "t3_alignment": t3g,
            "t1_clean": t1g,
            "t2_clean": t2g,
        },
        sheets_extra={
            "T3Components": t3_sheet,
            "T1Cross": t1_sheet,
            "T2Cross": t2_sheet,
            "T3Gates": kv_rows(t3g.get("gates")),
            "T1Gates": kv_rows(t1g.get("gates")),
            "T2Gates": kv_rows(t2g.get("gates")),
            "ConsensusDays": consensus_rows or [{"empty": True}],
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
