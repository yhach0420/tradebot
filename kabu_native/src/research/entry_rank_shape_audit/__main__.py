"""Offline Canonical C3 failure-mechanism audit. No Runtime write. No Paper. No Exact. No C4."""
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

from research.c3_canonical_retrain.analyze import matched_rows
from research.c3_canonical_retrain.oof import stitch_oof
from research.canonical_entry_performance_rebase.analyze import ranking_no_backfill
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.entry_objective_redesign_c3.analyze import paired_daily, ranking_gate
from research.entry_objective_redesign_c3.oof import SCORE_KEY, spec_grid, spec_label
from research.entry_rank_shape_audit import (
    ANALYSIS_ID,
    B2_FORMAL,
    BEST_SPEC_ADOPTED,
    C14_CHANGED,
    C14_ID,
    C2_STATUS_MAINTAINED,
    C3_AUDIT_VERDICT_MAINTAINED,
    C3_RECON_VERDICT_MAINTAINED,
    C3_RETRAIN_VERDICT_MAINTAINED,
    C3_VERDICT_MAINTAINED,
    C4_STARTED,
    CONTRACT_VERDICT_MAINTAINED,
    ELIGIBLE_DAYS,
    EXACT_RAN,
    EXECUTION_AWARE_MODEL_CREATED,
    LEGACY_COEFFICIENTS_REUSED,
    MAX_WORKERS,
    NEW_FEATURE_CREATED,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    OPVAL_OPERATED,
    PAPER_OPERATED,
    PARITY_VERDICT_MAINTAINED,
    PNL_USED,
    REBASE_VERDICT_MAINTAINED,
    REENTRY_V2_FORMAL,
    RUNTIME_CHANGED,
    THRESHOLD_USED,
    TOP1_STRATEGY_CREATED,
    TRUE_OOS,
)
from research.entry_rank_shape_audit.analyze import (
    decide,
    fixed_matrix_summary,
    inner_outer_corr,
    nested_observed,
    nested_parity,
    spec_stability,
    top1_robust,
)
from research.entry_rank_shape_audit.oof import (
    process_fixed_spec,
    rank_position_targets,
    slim_rank,
    top1_delta_stats,
)
from research.entry_rank_shape_audit.publish import (
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
ROWS_PATH = NATIVE / "results" / "research" / "_work_cache" / "c3_canonical_retrain" / "train_rows.json"
FOLD_DIR = NATIVE / "results" / "research" / "_work_cache" / "c3_canonical_retrain"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "entry_rank_shape_audit"


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
        futs = {ex.submit(fn, job): job.get("spec_id") or job.get("hold") for job in jobs}
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, "spec_id": key, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            print(
                f"done {label} {body.get('spec_id') or key} ok={body.get('ok')} "
                f"delta={body.get('TOP3_DELTA')} ak={body.get('OOF_RANKING_GATE_PASS')} "
                f"blocker={body.get('blocker')}",
                flush=True,
            )
    return out


def _fixed_cache_name(spec_id: str) -> str:
    return "fixed_" + spec_id.replace("|", "_").replace(" ", "") + ".json"


def _spec_row(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "spec_id": body.get("spec_id"),
        "feature_set": body.get("feature_set"),
        "normalization": body.get("normalization"),
        "alpha": body.get("alpha"),
        "n_features": body.get("n_features"),
        "outer_folds": body.get("outer_folds"),
        "MATCHED_RANKING_ROWS": body.get("MATCHED_RANKING_ROWS"),
        "TOP1_UPLIFT": body.get("TOP1_UPLIFT"),
        "TOP3_UPLIFT": body.get("TOP3_UPLIFT"),
        "TOP5_UPLIFT": body.get("TOP5_UPLIFT"),
        "CURRENT_TOP3_UPLIFT": body.get("CURRENT_TOP3_UPLIFT"),
        "TOP3_DELTA": body.get("TOP3_DELTA"),
        "TOP3_DELTA_POSITIVE_DAYS": body.get("TOP3_DELTA_POSITIVE_DAYS"),
        "TOP3_DELTA_NEGATIVE_DAYS": body.get("TOP3_DELTA_NEGATIVE_DAYS"),
        "TOP3_DELTA_EX_BEST_DAY": body.get("TOP3_DELTA_EX_BEST_DAY"),
        "TOP3_DELTA_EX_TOP3_DAYS": body.get("TOP3_DELTA_EX_TOP3_DAYS"),
        "MEAN_DAILY_SPEARMAN": body.get("MEAN_DAILY_SPEARMAN"),
        "OOF_RANKING_GATE_PASS": body.get("OOF_RANKING_GATE_PASS"),
        "gate_fail": body.get("gate_fail"),
    }


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
                "TARGET": "TARGET V4 M4_PERSISTENT 600s",
                "MODEL": "Ridge 27 specs fixed-spec LODO + nested restitch. No RF. No pairwise.",
                "RANK": "eligible → feature → score → rank → TopK fixed → Target join. NO TARGET BACKFILL.",
                "BEST_SPEC_ADOPTED": BEST_SPEC_ADOPTED,
                "TOP1_STRATEGY_CREATED": TOP1_STRATEGY_CREATED,
                "EXACT_RAN": EXACT_RAN,
            }
        ),
        "NestedParity": [{"empty": True}],
        "NestedFolds": [{"empty": True}],
        "FixedSpecs": [{"empty": True}],
        "NestedVsFixed": [{"empty": True}],
        "SpecStability": [{"empty": True}],
        "RankShape": [{"empty": True}],
        "Top1Robust": [{"empty": True}],
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
                "TOP1_STRATEGY_CREATED": TOP1_STRATEGY_CREATED,
                "BEST_SPEC_ADOPTED": BEST_SPEC_ADOPTED,
                "EXECUTION_AWARE_MODEL_CREATED": EXECUTION_AWARE_MODEL_CREATED,
                "NEW_FEATURE_CREATED": NEW_FEATURE_CREATED,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "PNL_USED": PNL_USED,
                "THRESHOLD_USED": THRESHOLD_USED,
                "LEGACY_COEFFICIENTS_REUSED": LEGACY_COEFFICIENTS_REUSED,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"VERDICT={required.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print("STOP. No Top1 strategy. No Exact. No C4. Runtime unchanged. submit/cancel/live=0/0/0.", flush=True)
    return 0 if required.get("VERDICT") not in {"ENTRY_RANK_SHAPE_AUDIT_FAILED"} else 2


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE CANONICAL C3 FAILURE MECHANISM AUDIT V1", flush=True)
    print("27 fixed-spec LODO. Nested restitch. No new strategy. No Exact. No PnL.", flush=True)

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
    if not ROWS_PATH.is_file():
        print("STOP missing train_rows.json", flush=True)
        return 2

    fold_bodies = []
    for day in ELIGIBLE_DAYS:
        fp = FOLD_DIR / f"oof_fold_{day}.json"
        saved = _load(fp)
        if not (saved.get("ok") and saved.get("hold") == day and saved.get("scored")):
            print("STOP missing nested OOF fold", day, flush=True)
            dec = decide(parity_ok=False, nested_gate_pass=False, passing_ak_n=0, top1_robust_flag=False)
            return write_report(
                {
                    "NESTED_OOF_PARITY": False,
                    "VERDICT": "ENTRY_RANK_SHAPE_AUDIT_FAILED",
                    "PRIMARY_FAILURE_MECHANISM": "ENTRY_RANK_SHAPE_AUDIT_FAILED",
                    "NEXT_RESEARCH": "NONE",
                    "note": f"Missing nested OOF fold cache {day}",
                    "FIXED_SPECS_N": 27,
                    "TRUE_OOS": TRUE_OOS,
                    "NEW_FORWARD_N": NEW_FORWARD_N,
                },
                decision=dec,
            )
        fold_bodies.append(saved)

    rows_body = json.loads(ROWS_PATH.read_text(encoding="utf-8"))
    exec_rows = list(rows_body.get("rows") or [])
    stitched = stitch_oof(fold_bodies, exec_rows)
    attached = stitched.get("attached") or []
    matched = matched_rows(attached)
    rank_cur = ranking_no_backfill(matched, "current_score")
    rank_c3 = ranking_no_backfill(matched, SCORE_KEY)
    paired = paired_daily(rank_cur, rank_c3)
    gate = ranking_gate(rank_cur, rank_c3, paired)
    obs = nested_observed(rank_cur, rank_c3, paired)
    par = nested_parity(obs)
    print(
        f"NESTED MATCHED={len(matched)} CUR_TOP3={obs.get('CURRENT_TOP3')} "
        f"C3_TOP3={obs.get('C3_TOP3')} delta={obs.get('TOP3_DELTA')} "
        f"C3_TOP1={obs.get('C3_TOP1')} parity={par.get('ok')} mismatches={par.get('mismatches')}",
        flush=True,
    )
    if not par.get("ok"):
        dec = decide(parity_ok=False, nested_gate_pass=bool(gate.get("OOF_RANKING_GATE_PASS")), passing_ak_n=0, top1_robust_flag=False)
        return write_report(
            {
                "NESTED_OOF_PARITY": False,
                "FIXED_SPECS_N": 27,
                "FIXED_SPECS_PASSING_ALL_AK_N": None,
                "FIXED_SPECS_TOP3_POSITIVE_N": None,
                "FIXED_SPECS_TOP3_GT_CURRENT_N": None,
                "BEST_FIXED_DIAGNOSTIC_SPEC": None,
                "BEST_FIXED_DIAGNOSTIC_TOP3_DELTA": None,
                "MEDIAN_FIXED_TOP3_DELTA": None,
                "INNER_OUTER_TOP3_CORRELATION": None,
                "INNER_OUTER_DELTA_CORRELATION": None,
                "MOST_COMMON_SPEC": stitched.get("MOST_COMMON_SPEC"),
                "MOST_COMMON_SPEC_SHARE": stitched.get("MOST_COMMON_SPEC_SHARE"),
                "SPEC_SELECTION_STABLE": False,
                "PRIMARY_FAILURE_MECHANISM": "ENTRY_RANK_SHAPE_AUDIT_FAILED",
                "NEXT_RESEARCH": "NONE",
                "VERDICT": "ENTRY_RANK_SHAPE_AUDIT_FAILED",
                "TRUE_OOS": TRUE_OOS,
                "NEW_FORWARD_N": NEW_FORWARD_N,
            },
            decision=dec,
            extra={"nested_parity": par, "nested_observed": obs, "gate": gate},
            sheets_extra={"NestedParity": kv_rows({"ok": False, **obs, "mismatches": par.get("mismatches")})},
        )

    folds = stitched.get("folds") or []
    stab = spec_stability(folds)
    ioc = inner_outer_corr(folds)
    t1s = top1_delta_stats(paired)
    t1_ok = top1_robust(t1s)
    c3_shape = rank_position_targets(matched, SCORE_KEY)
    cur_shape = rank_position_targets(matched, "current_score")
    print(
        f"SPEC_STABLE={stab.get('SPEC_SELECTION_STABLE')} share={stab.get('MOST_COMMON_SPEC_SHARE')} "
        f"inner_outer_top3={ioc.get('INNER_OUTER_TOP3_CORRELATION')} "
        f"TOP1_ROBUST={t1_ok} mean={t1s.get('mean')} "
        f"C3_RANK1={c3_shape.get('RANK1_TARGET')}",
        flush=True,
    )

    CACHE.mkdir(parents=True, exist_ok=True)
    spec_bodies: list[dict] = []
    jobs = []
    for spec in grid:
        sid = spec_label(spec)
        fp = CACHE / _fixed_cache_name(sid)
        saved = _load(fp)
        if saved.get("ok") and saved.get("spec_id") == sid and saved.get("TOP3_DELTA") is not None:
            spec_bodies.append(saved)
            print(f"fixed-spec cache-hit {sid} delta={saved.get('TOP3_DELTA')}", flush=True)
            continue
        jobs.append({"spec": spec, "spec_id": sid, "rows_path": str(ROWS_PATH), "days": list(ELIGIBLE_DAYS)})
    print(f"fixed-spec jobs={len(jobs)} cache_hits={len(spec_bodies)}", flush=True)
    for body in _pool(process_fixed_spec, jobs, "FIXED"):
        if body.get("ok"):
            _save_json(CACHE / _fixed_cache_name(str(body.get("spec_id"))), body)
        spec_bodies.append(body)
    fail = [b for b in spec_bodies if not b.get("ok")]
    if fail or len(spec_bodies) != 27:
        print("STOP fixed-spec LODO failed", [(b.get("spec_id"), b.get("blocker")) for b in fail], flush=True)
        dec = decide(parity_ok=True, nested_gate_pass=False, passing_ak_n=0, top1_robust_flag=t1_ok)
        dec = {
            **dec,
            "CASE": None,
            "VERDICT": "ENTRY_RANK_SHAPE_AUDIT_FAILED",
            "PRIMARY_FAILURE_MECHANISM": "ENTRY_RANK_SHAPE_AUDIT_FAILED",
            "NEXT_RESEARCH": "NONE",
            "note": "Fixed-spec LODO worker failed.",
        }
        return write_report(
            {
                "NESTED_OOF_PARITY": True,
                "VERDICT": "ENTRY_RANK_SHAPE_AUDIT_FAILED",
                "PRIMARY_FAILURE_MECHANISM": "ENTRY_RANK_SHAPE_AUDIT_FAILED",
                "NEXT_RESEARCH": "NONE",
                "FIXED_SPECS_N": 27,
            },
            decision=dec,
        )

    spec_bodies = sorted(spec_bodies, key=lambda r: str(r.get("spec_id") or ""))
    spec_rows = [_spec_row(b) for b in spec_bodies]
    fx = fixed_matrix_summary(spec_rows)
    dec = decide(
        parity_ok=True,
        nested_gate_pass=bool(gate.get("OOF_RANKING_GATE_PASS")),
        passing_ak_n=int(fx.get("FIXED_SPECS_PASSING_ALL_AK_N") or 0),
        top1_robust_flag=t1_ok,
    )
    required = {
        "NESTED_OOF_PARITY": True,
        "FIXED_SPECS_N": fx.get("FIXED_SPECS_N"),
        "FIXED_SPECS_PASSING_ALL_AK_N": fx.get("FIXED_SPECS_PASSING_ALL_AK_N"),
        "FIXED_SPECS_TOP3_POSITIVE_N": fx.get("FIXED_SPECS_TOP3_POSITIVE_N"),
        "FIXED_SPECS_TOP3_GT_CURRENT_N": fx.get("FIXED_SPECS_TOP3_GT_CURRENT_N"),
        "BEST_FIXED_DIAGNOSTIC_SPEC": fx.get("BEST_FIXED_DIAGNOSTIC_SPEC"),
        "BEST_FIXED_DIAGNOSTIC_TOP3_DELTA": fx.get("BEST_FIXED_DIAGNOSTIC_TOP3_DELTA"),
        "MEDIAN_FIXED_TOP3_DELTA": fx.get("MEDIAN_FIXED_TOP3_DELTA"),
        "INNER_OUTER_TOP3_CORRELATION": ioc.get("INNER_OUTER_TOP3_CORRELATION"),
        "INNER_OUTER_DELTA_CORRELATION": ioc.get("INNER_OUTER_DELTA_CORRELATION"),
        "MOST_COMMON_SPEC": stab.get("MOST_COMMON_SPEC"),
        "MOST_COMMON_SPEC_SHARE": stab.get("MOST_COMMON_SPEC_SHARE"),
        "SPEC_SELECTION_STABLE": stab.get("SPEC_SELECTION_STABLE"),
        "C3_RANK1_TARGET": c3_shape.get("RANK1_TARGET"),
        "C3_RANK2_TARGET": c3_shape.get("RANK2_TARGET"),
        "C3_RANK3_TARGET": c3_shape.get("RANK3_TARGET"),
        "C3_RANK4_TARGET": c3_shape.get("RANK4_TARGET"),
        "C3_RANK5_TARGET": c3_shape.get("RANK5_TARGET"),
        "C3_RANK2_3_MEAN": c3_shape.get("RANK2_3_MEAN"),
        "C3_RANK4_5_MEAN": c3_shape.get("RANK4_5_MEAN"),
        "CURRENT_RANK1_TARGET": cur_shape.get("RANK1_TARGET"),
        "CURRENT_RANK2_3_MEAN": cur_shape.get("RANK2_3_MEAN"),
        "CURRENT_RANK4_5_MEAN": cur_shape.get("RANK4_5_MEAN"),
        "TOP1_DELTA_MEAN": t1s.get("mean"),
        "TOP1_DELTA_MEDIAN": t1s.get("median"),
        "TOP1_POSITIVE_DAYS": t1s.get("positive_days"),
        "TOP1_NEGATIVE_DAYS": t1s.get("negative_days"),
        "TOP1_EX_BEST_DAY": t1s.get("ex_best_day"),
        "TOP1_EX_TOP3_DAYS": t1s.get("ex_top3_days"),
        "TOP1_EDGE_ROBUST": t1_ok,
        "PRIMARY_FAILURE_MECHANISM": dec.get("PRIMARY_FAILURE_MECHANISM"),
        "NEXT_RESEARCH": dec.get("NEXT_RESEARCH"),
        "VERDICT": dec.get("VERDICT"),
        "CASE": dec.get("CASE"),
        "NESTED_CURRENT_TOP3": obs.get("CURRENT_TOP3"),
        "NESTED_C3_TOP3": obs.get("C3_TOP3"),
        "NESTED_TOP3_DELTA": obs.get("TOP3_DELTA"),
        "NESTED_C3_TOP1": obs.get("C3_TOP1"),
        "NESTED_C3_TOP5": obs.get("C3_TOP5"),
        "NESTED_SPEARMAN": obs.get("SPEARMAN"),
        "NESTED_TOP3_POSITIVE_DAYS": obs.get("TOP3_DELTA_POSITIVE_DAYS"),
        "NESTED_TOP3_NEGATIVE_DAYS": obs.get("TOP3_DELTA_NEGATIVE_DAYS"),
        "MATCHED_RANKING_ROWS": len(matched),
        "CANONICAL_TRAINING_ROWS": len(exec_rows),
        "feature_set_counts": stab.get("feature_set_counts"),
        "normalization_counts": stab.get("normalization_counts"),
        "alpha_counts": stab.get("alpha_counts"),
        "BEST_SPEC_ADOPTED": BEST_SPEC_ADOPTED,
        "TOP1_STRATEGY_CREATED": TOP1_STRATEGY_CREATED,
        "EXACT_RAN": EXACT_RAN,
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
    }
    extra = {
        "nested_parity": par,
        "gate": gate,
        "ranking_matched": {"CURRENT": slim_rank(rank_cur), "C3": slim_rank(rank_c3), "paired": {k: v for k, v in paired.items() if k != "days"}},
        "folds": folds,
        "spec_stability": stab,
        "inner_outer": ioc,
        "fixed_summary": fx,
        "fixed_specs": spec_rows,
        "rank_shape": {"C3": c3_shape, "CURRENT": cur_shape},
        "top1": {**t1s, "TOP1_EDGE_ROBUST": t1_ok},
        "frozen": {
            "C3_RETRAIN": C3_RETRAIN_VERDICT_MAINTAINED,
            "C3": C3_VERDICT_MAINTAINED,
            "C3_AUDIT": C3_AUDIT_VERDICT_MAINTAINED,
            "C3_RECON": C3_RECON_VERDICT_MAINTAINED,
            "CONTRACT": CONTRACT_VERDICT_MAINTAINED,
            "PARITY": PARITY_VERDICT_MAINTAINED,
            "REBASE": REBASE_VERDICT_MAINTAINED,
            "C2": C2_STATUS_MAINTAINED,
            "B2": B2_FORMAL,
            "REENTRY_V2": REENTRY_V2_FORMAL,
            "C14_CHANGED": C14_CHANGED,
            "RUNTIME_CHANGED": RUNTIME_CHANGED,
            "PAPER_OPERATED": PAPER_OPERATED,
            "OPVAL_OPERATED": OPVAL_OPERATED,
            "C4_STARTED": C4_STARTED,
            "EXACT_RAN": EXACT_RAN,
            "TOP1_STRATEGY_CREATED": TOP1_STRATEGY_CREATED,
            "BEST_SPEC_ADOPTED": BEST_SPEC_ADOPTED,
            "NEW_FEATURE_CREATED": NEW_FEATURE_CREATED,
            "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
            "EXECUTION_AWARE_MODEL_CREATED": EXECUTION_AWARE_MODEL_CREATED,
            "PNL_USED": PNL_USED,
            "THRESHOLD_USED": THRESHOLD_USED,
            "LEGACY_COEFFICIENTS_REUSED": LEGACY_COEFFICIENTS_REUSED,
            "TRUE_OOS": TRUE_OOS,
            "NEW_FORWARD_N": NEW_FORWARD_N,
        },
    }
    sheets_extra = {
        "NestedParity": kv_rows({"ok": True, **obs, **{k: gate.get(k) for k in gate}}),
        "NestedFolds": folds or [{"empty": True}],
        "FixedSpecs": spec_rows or [{"empty": True}],
        "NestedVsFixed": kv_rows(
            {
                "NESTED_TOP3_DELTA": obs.get("TOP3_DELTA"),
                "BEST_FIXED_DIAGNOSTIC_SPEC": fx.get("BEST_FIXED_DIAGNOSTIC_SPEC"),
                "BEST_FIXED_DIAGNOSTIC_TOP3_DELTA": fx.get("BEST_FIXED_DIAGNOSTIC_TOP3_DELTA"),
                "MEDIAN_FIXED_TOP3_DELTA": fx.get("MEDIAN_FIXED_TOP3_DELTA"),
                "INNER_OUTER_TOP3_CORRELATION": ioc.get("INNER_OUTER_TOP3_CORRELATION"),
                "INNER_OUTER_DELTA_CORRELATION": ioc.get("INNER_OUTER_DELTA_CORRELATION"),
                "note": ioc.get("note"),
                "best_note": fx.get("best_note"),
            }
        ),
        "SpecStability": kv_rows(stab),
        "RankShape": [
            {"lane": "C3", **c3_shape},
            {"lane": "CURRENT", **cur_shape},
        ],
        "Top1Robust": kv_rows({**t1s, "TOP1_EDGE_ROBUST": t1_ok, "note": "Diagnostic only. Top1 strategy not created."}),
        "Decision": kv_rows(dec),
    }
    return write_report(required, decision=dec, extra=extra, sheets_extra=sheets_extra)


if __name__ == "__main__":
    raise SystemExit(main())
