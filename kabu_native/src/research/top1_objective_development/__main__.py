"""Offline Top1 objective development. RANK1_ONLY Exact only if OOF gate passes. No Runtime write."""
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
from research.canonical_entry_performance_rebase.analyze import (
    attach_would_fill,
    pack_line,
    parity_against,
    ranking_no_backfill,
    target_integrity,
    topk_exec_block,
    wf_index,
)
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.entry_objective_redesign_c3.analyze import paired_daily
from research.entry_objective_redesign_c3.oof import spec_grid, spec_label
from research.entry_panel_exact_reconciliation.analyze import causality_from_snaps
from research.entry_rank_shape_audit.oof import rank_position_targets, top1_delta_stats
from research.executable_target_v2_b_threshold.analyze import pack_metrics
from research.top1_objective_development import (
    ANALYSIS_ID,
    AUDIT_BEST_SPEC_ADOPTED,
    B2_FORMAL,
    C14_CHANGED,
    C14_ID,
    C2_STATUS_MAINTAINED,
    C3_AUDIT_VERDICT_MAINTAINED,
    C3_RETRAIN_VERDICT_MAINTAINED,
    C3_VERDICT_MAINTAINED,
    C4_STARTED,
    CONTRACT_VERDICT_MAINTAINED,
    ELIGIBLE_DAYS,
    EXECUTION_AWARE_MODEL_CREATED,
    EXPECTED_A0,
    EXPECTED_A2,
    FIT_KEYS,
    LEGACY_COEFFICIENTS_REUSED,
    MAX_WORKERS,
    NEW_FEATURE_CREATED,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    OPVAL_OPERATED,
    PAPER_OPERATED,
    PARITY_VERDICT_MAINTAINED,
    PNL_MODEL_SELECTION,
    RANK_SHAPE_VERDICT_MAINTAINED,
    REBASE_VERDICT_MAINTAINED,
    REENTRY_V2_FORMAL,
    RF_USED,
    RUNTIME_CHANGED,
    SCORE_KEY,
    THRESHOLD_USED,
    TOP3_USED_IN_SELECTION,
    TRUE_OOS,
)
from research.top1_objective_development.analyze import (
    decide,
    fill_edge_vs_current,
    rank1_exact_success,
    top1_oof_gate,
)
from research.top1_objective_development.oof import (
    inner_select,
    matched_rows,
    process_outer_fold,
    slim_fit,
    slim_rank,
    spec_stability,
    stitch_oof,
)
from research.top1_objective_development.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from research.top1_objective_development.replay import process_day as process_exact
from research.entry_objective_redesign_c3.oof import fit_on_universe
from small_paper.v1r_native_entry_live import FEATURE_ORDER

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
TRAIN_CACHE = NATIVE / "results" / "research" / "_work_cache" / "c3_canonical_retrain"
ROWS_PATH = TRAIN_CACHE / "train_rows.json"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "top1_objective_development"
A_CACHE = NATIVE / "results" / "research" / "current_entry_nonexec_mechanism" / "_work_cache"
COUPLE_CACHE = NATIVE / "results" / "research" / "c3_execution_coupling_audit" / "_work_cache"


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
        futs = {ex.submit(fn, job): job.get("date") or job.get("hold") for job in jobs}
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, "date": key, "hold": key, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            print(
                f"done {label} {body.get('date') or body.get('hold') or key} ok={body.get('ok')} "
                f"n={len(body.get('trades') or body.get('scored') or [])} "
                f"spec={(body.get('spec') or {}).get('spec_id')} blocker={body.get('blocker')}",
                flush=True,
            )
    return out


def _load_a_trades(stage: str) -> tuple[list[dict], list[str]]:
    trades: list[dict] = []
    missing: list[str] = []
    for day in ELIGIBLE_DAYS:
        fp = A_CACHE / f"{day}_{stage}.json"
        if not fp.is_file():
            missing.append(day)
            continue
        body = json.loads(fp.read_text(encoding="utf-8"))
        if not body.get("ok"):
            missing.append(day)
            continue
        trades.extend(body.get("trades") or [])
    return trades, missing


def _load_would_fill() -> list[dict]:
    rows: list[dict] = []
    for day in ELIGIBLE_DAYS:
        oof = _load(COUPLE_CACHE / f"{day}_OOF_EXACT.json")
        fin = _load(COUPLE_CACHE / f"{day}_C3_FINAL_TRACE.json")
        wf = list(oof.get("would_fill") or []) or list(fin.get("would_fill") or [])
        rows.extend(wf)
    return rows


def _slice_n_pnl(pack: dict | None, key: str) -> tuple[Any, Any]:
    if not pack:
        return None, None
    sl = pack.get(key) or {}
    n = sl.get("trades") if sl.get("trades") is not None else sl.get("n")
    pnl = sl.get("pnl") if sl.get("pnl") is not None else sl.get("PnL")
    return n, pnl


def write_report(required: dict, decision: dict, extra: dict | None = None, sheets_extra: dict | None = None) -> int:
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
                "Fill": "Corrected Passive Fill",
                "ADMISSION": "RANK1_ONLY",
                "PRIMARY": "mean daily Top1 TARGET V4 uplift",
                "MODEL": "Ridge 27 specs. Nested OOF. No RF. No pairwise. No Top3 selection.",
                "AUDIT_BEST_SPEC_ADOPTED": AUDIT_BEST_SPEC_ADOPTED,
            }
        ),
        "Integrity": [{"empty": True}],
        "OOFGate": [{"empty": True}],
        "PairedDays": [{"empty": True}],
        "Folds": [{"empty": True}],
        "RankShape": [{"empty": True}],
        "SpecStability": [{"empty": True}],
        "Parity": [{"empty": True}],
        "Exact": [{"empty": True}],
        "ExecCheck": [{"empty": True}],
        "Decision": kv_rows(decision),
        "Safety": kv_rows(
            {
                "submit_cancel_live": "0/0/0",
                "Paper": 0,
                "OPVAL": 0,
                "C14_CHANGED": C14_CHANGED,
                "RUNTIME_CHANGED": RUNTIME_CHANGED,
                "C4_STARTED": C4_STARTED,
                "AUDIT_BEST_SPEC_ADOPTED": AUDIT_BEST_SPEC_ADOPTED,
                "TOP3_USED_IN_SELECTION": TOP3_USED_IN_SELECTION,
                "PNL_MODEL_SELECTION": PNL_MODEL_SELECTION,
                "EXECUTION_AWARE_MODEL_CREATED": EXECUTION_AWARE_MODEL_CREATED,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"VERDICT={required.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print("STOP. No C4. Runtime unchanged. submit/cancel/live=0/0/0.", flush=True)
    failed = str(required.get("VERDICT") or "") in {"TOP1_DEVELOPMENT_INTEGRITY_FAILED"}
    return 2 if failed else 0


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE TOP1 OBJECTIVE PRECOMMITTED DEVELOPMENT V1", flush=True)
    print("Primary=mean daily Top1 uplift. RANK1_ONLY. 27 Ridge specs. No audit-best adoption.", flush=True)

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
    if len(spec_grid()) != 27:
        print("STOP spec grid is not 27", flush=True)
        return 2
    if not ROWS_PATH.is_file():
        print("STOP missing Canonical train_rows.json", flush=True)
        return 2

    snaps: list[dict] = []
    for day in ELIGIBLE_DAYS:
        body = _load(TRAIN_CACHE / f"{day}_TRAIN.json")
        if body.get("ok"):
            snaps.extend(body.get("snaps") or [])
    cas = causality_from_snaps(snaps) if snaps else {}
    future_n = int(cas.get("FUTURE_EVENT_USE_N") or 0)
    exec_rows = list((_load(ROWS_PATH).get("rows") or []))
    integ = target_integrity(exec_rows)
    integrity_ok = bool(future_n == 0 and integ.get("ok") and len(exec_rows) > 0)
    print(
        f"train_rows={len(exec_rows)} future={future_n} "
        f"unexpected={integ.get('TARGET_UNEXPECTED_MISSING_N')} contam={integ.get('TARGET_CONTAMINATION_N')} "
        f"integrity={integrity_ok}",
        flush=True,
    )
    if not integrity_ok:
        dec = decide(integrity_ok=False, gate_pass=False, exact_ran=False, success=None, fill_remains=None)
        return write_report(
            {
                "CANONICAL_TRAINING_ROWS": len(exec_rows),
                "TOP1_OOF_GATE_PASS": False,
                "OOF_EXACT_RAN": False,
                "FINAL_EXACT_RAN": False,
                "TRUE_OOS": TRUE_OOS,
                "NEW_FORWARD_N": NEW_FORWARD_N,
                "VERDICT": "TOP1_DEVELOPMENT_INTEGRITY_FAILED",
                "NEXT_RESEARCH": "NONE",
                "FUTURE_EVENT_USE_N": future_n,
                "TARGET_UNEXPECTED_MISSING_N": integ.get("TARGET_UNEXPECTED_MISSING_N"),
                "TARGET_CONTAMINATION_N": integ.get("TARGET_CONTAMINATION_N"),
            },
            dec,
            extra={"causality": cas, "target_integrity": {k: v for k, v in integ.items() if k != "counts"}},
            sheets_extra={"Integrity": kv_rows({"FUTURE_EVENT_USE_N": future_n, **{k: v for k, v in integ.items() if k != "counts"}})},
        )

    CACHE.mkdir(parents=True, exist_ok=True)
    fold_bodies = []
    fold_jobs = []
    for day in ELIGIBLE_DAYS:
        fp = CACHE / f"oof_fold_{day}.json"
        saved = _load(fp)
        if saved.get("ok") and saved.get("hold") == day and saved.get("scored") and (saved.get("spec") or {}).get("mean_daily_top1_uplift") is not None:
            fold_bodies.append(saved)
            print(f"OOF fold cache-hit {day} spec={((saved.get('spec') or {}).get('spec_id'))}", flush=True)
            continue
        fold_jobs.append({"hold": day, "rows_path": str(ROWS_PATH)})
    print(f"outer fold jobs={len(fold_jobs)}", flush=True)
    for body in _pool(process_outer_fold, fold_jobs, "OOF"):
        if body.get("ok"):
            _save_json(CACHE / f"oof_fold_{body.get('hold')}.json", body)
        fold_bodies.append(body)
    fold_fail = [b for b in fold_bodies if not b.get("ok")]
    if fold_fail or len(fold_bodies) != len(ELIGIBLE_DAYS):
        print("STOP nested OOF failed", [(b.get("hold"), b.get("blocker")) for b in fold_fail], flush=True)
        dec = decide(integrity_ok=False, gate_pass=False, exact_ran=False, success=None, fill_remains=None)
        return write_report(
            {
                "VERDICT": "TOP1_DEVELOPMENT_INTEGRITY_FAILED",
                "NEXT_RESEARCH": "NONE",
                "TOP1_OOF_GATE_PASS": False,
                "OOF_EXACT_RAN": False,
                "FINAL_EXACT_RAN": False,
                "TRUE_OOS": TRUE_OOS,
                "NEW_FORWARD_N": 0,
            },
            dec,
        )

    stitched = stitch_oof(fold_bodies, exec_rows)
    attached = stitched.get("attached") or []
    matched = matched_rows(attached)
    rank_cur = ranking_no_backfill(matched, "current_score")
    rank_m = ranking_no_backfill(matched, SCORE_KEY)
    paired = paired_daily(rank_cur, rank_m)
    t1s = top1_delta_stats(paired)
    gate = top1_oof_gate(model=rank_m, current=rank_cur, paired=paired, integrity_ok=True)
    shape = rank_position_targets(matched, SCORE_KEY, kmax=3)
    stab = spec_stability(stitched.get("folds") or [])
    print(
        f"MATCHED={len(matched)} CUR_TOP1={rank_cur.get('TOP1_UPLIFT')} "
        f"MODEL_TOP1={rank_m.get('TOP1_UPLIFT')} delta={t1s.get('mean')} "
        f"pos={t1s.get('positive_days')}/{t1s.get('negative_days')} "
        f"GATE={gate.get('TOP1_OOF_GATE_PASS')} fail={gate.get('fail')}",
        flush=True,
    )

    a0_trades, a0_miss = _load_a_trades("A0")
    a2_trades, a2_miss = _load_a_trades("A2")
    days = list(ELIGIBLE_DAYS)
    a0_pack = pack_metrics(a0_trades, days) if not a0_miss else {}
    a2_pack = pack_metrics(a2_trades, days) if not a2_miss else {}
    p0 = parity_against(a0_pack, EXPECTED_A0) if a0_pack else {"ok": False}
    p2 = parity_against(a2_pack, EXPECTED_A2) if a2_pack else {"ok": False}
    print(f"A0_PARITY={p0.get('ok')} {pack_line(a0_pack) if a0_pack else 'n/a'}", flush=True)
    print(f"A2_PARITY={p2.get('ok')} {pack_line(a2_pack) if a2_pack else 'n/a'}", flush=True)

    required_base = {
        "CANONICAL_TRAINING_ROWS": len(exec_rows),
        "MATCHED_RANKING_ROWS": len(matched),
        "CURRENT_TOP1_UPLIFT": rank_cur.get("TOP1_UPLIFT"),
        "TOP1_MODEL_UPLIFT": rank_m.get("TOP1_UPLIFT"),
        "TOP1_DELTA_MEAN": t1s.get("mean"),
        "TOP1_DELTA_MEDIAN": t1s.get("median"),
        "TOP1_POSITIVE_DAYS": t1s.get("positive_days"),
        "TOP1_NEGATIVE_DAYS": t1s.get("negative_days"),
        "TOP1_EX_BEST_DAY": t1s.get("ex_best_day"),
        "TOP1_EX_TOP3_DAYS": t1s.get("ex_top3_days"),
        "TOP1_MAX_DAY_CONTRIBUTION": gate.get("TOP1_MAX_DAY_CONTRIBUTION"),
        "TOP1_OOF_GATE_PASS": bool(gate.get("TOP1_OOF_GATE_PASS")),
        "RANK1_TARGET": shape.get("RANK1_TARGET"),
        "RANK2_TARGET": shape.get("RANK2_TARGET"),
        "RANK3_TARGET": shape.get("RANK3_TARGET"),
        "MOST_COMMON_SPEC": stab.get("MOST_COMMON_SPEC"),
        "MOST_COMMON_SPEC_SHARE": stab.get("MOST_COMMON_SPEC_SHARE"),
        "FINAL_SPEC": None,
        "OOF_EXACT_RAN": False,
        "OOF_TOP1_EXACT": "n/a",
        "FINAL_EXACT_RAN": False,
        "FINAL_TOP1_EXACT": "n/a",
        "CURRENT_RANK1_WOULD_FILL": None,
        "TOP1_MODEL_RANK1_WOULD_FILL": None,
        "CURRENT_RANK1_FILL_TO_600": None,
        "TOP1_MODEL_RANK1_FILL_TO_600": None,
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "OOF_GATE_FAIL": gate.get("fail"),
        "feature_set_counts": stab.get("feature_set_counts"),
        "normalization_counts": stab.get("normalization_counts"),
        "alpha_counts": stab.get("alpha_counts"),
        "A0_PARITY": bool(p0.get("ok")),
        "A2_PARITY": bool(p2.get("ok")),
        "FUTURE_EVENT_USE_N": future_n,
        "TARGET_UNEXPECTED_MISSING_N": integ.get("TARGET_UNEXPECTED_MISSING_N"),
        "TARGET_CONTAMINATION_N": integ.get("TARGET_CONTAMINATION_N"),
        "AUDIT_BEST_SPEC_ADOPTED": AUDIT_BEST_SPEC_ADOPTED,
        "ADMISSION": "RANK1_ONLY",
    }

    extra_common = {
        "gate": gate,
        "ranking_matched": {"CURRENT": slim_rank(rank_cur), "TOP1": slim_rank(rank_m), "paired": {k: v for k, v in paired.items() if k != "days"}},
        "folds": stitched.get("folds"),
        "spec_stability": stab,
        "rank_shape": shape,
        "parity": {"A0": p0, "A2": p2},
        "A0": a0_pack,
        "A2": a2_pack,
        "causality": cas,
        "target_integrity": {k: v for k, v in integ.items() if k != "counts"},
        "frozen": {
            "C3_RETRAIN": C3_RETRAIN_VERDICT_MAINTAINED,
            "RANK_SHAPE": RANK_SHAPE_VERDICT_MAINTAINED,
            "C3": C3_VERDICT_MAINTAINED,
            "C3_AUDIT": C3_AUDIT_VERDICT_MAINTAINED,
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
            "NEW_FEATURE_CREATED": NEW_FEATURE_CREATED,
            "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
            "RF_USED": RF_USED,
            "PNL_MODEL_SELECTION": PNL_MODEL_SELECTION,
            "TOP3_USED_IN_SELECTION": TOP3_USED_IN_SELECTION,
            "THRESHOLD_USED": THRESHOLD_USED,
            "EXECUTION_AWARE_MODEL_CREATED": EXECUTION_AWARE_MODEL_CREATED,
            "LEGACY_COEFFICIENTS_REUSED": LEGACY_COEFFICIENTS_REUSED,
            "AUDIT_BEST_SPEC_ADOPTED": AUDIT_BEST_SPEC_ADOPTED,
            "TRUE_OOS": TRUE_OOS,
            "NEW_FORWARD_N": NEW_FORWARD_N,
        },
    }
    sheets_common = {
        "Integrity": kv_rows(
            {
                "FUTURE_EVENT_USE_N": future_n,
                "TARGET_UNEXPECTED_MISSING_N": integ.get("TARGET_UNEXPECTED_MISSING_N"),
                "TARGET_CONTAMINATION_N": integ.get("TARGET_CONTAMINATION_N"),
                **{k: v for k, v in integ.items() if k != "counts"},
            }
        ),
        "OOFGate": kv_rows(gate),
        "PairedDays": paired.get("days") or [{"empty": True}],
        "Folds": stitched.get("folds") or [{"empty": True}],
        "RankShape": [shape],
        "SpecStability": kv_rows(stab),
        "Parity": kv_rows({"A0": p0, "A2": p2}),
    }

    if not gate.get("TOP1_OOF_GATE_PASS"):
        dec = decide(integrity_ok=True, gate_pass=False, exact_ran=False, success=None, fill_remains=None)
        required = {**required_base, "VERDICT": dec.get("VERDICT"), "NEXT_RESEARCH": dec.get("NEXT_RESEARCH"), "CASE": dec.get("CASE")}
        return write_report(required, dec, extra=extra_common, sheets_extra=sheets_common)

    if not p0.get("ok") or not p2.get("ok"):
        dec = decide(integrity_ok=False, gate_pass=True, exact_ran=False, success=None, fill_remains=None)
        required = {
            **required_base,
            "VERDICT": "TOP1_DEVELOPMENT_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "note": "A0/A2 Exact parity anchor missed before RANK1_ONLY Exact.",
        }
        return write_report(required, dec, extra=extra_common, sheets_extra=sheets_common)

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
        dec = decide(integrity_ok=False, gate_pass=True, exact_ran=False, success=None, fill_remains=None)
        return write_report(
            {**required_base, "VERDICT": "TOP1_DEVELOPMENT_INTEGRITY_FAILED", "NEXT_RESEARCH": "NONE"},
            dec,
            extra=extra_common,
            sheets_extra=sheets_common,
        )
    by_date = {r["date"]: r for r in elig}

    print("final spec select on all development days (Top1 objective / same tie-break)", flush=True)
    inner = inner_select(exec_rows)
    final_spec = inner.get("selected") or {}
    final_fit = fit_on_universe(exec_rows, final_spec)
    print(f"FINAL_SPEC={spec_label(final_spec)} kind={final_fit.get('kind')} train_n={final_fit.get('train_n')}", flush=True)

    oof_jobs = []
    oof_got = []
    final_jobs = []
    final_got = []
    slim_final = slim_fit(final_fit)
    fits = stitched.get("fits") or {}
    for day in ELIGIBLE_DAYS:
        cached = _load(CACHE / f"{day}_OOF_EXACT.json")
        if cached.get("ok") and cached.get("date") == day:
            oof_got.append(cached)
            print(f"OOF_EXACT {day} cache-hit trades={len(cached.get('trades') or [])}", flush=True)
        else:
            r = by_date[day]
            oof_jobs.append(
                {
                    "date": day,
                    "capture_path": r["capture_path"],
                    "universe": r["universe_symbols"],
                    "fit": {k: (fits.get(day) or {}).get(k) for k in FIT_KEYS},
                    "stage": "OOF_EXACT",
                }
            )
        cached_f = _load(CACHE / f"{day}_FINAL_EXACT.json")
        if cached_f.get("ok") and cached_f.get("date") == day:
            final_got.append(cached_f)
            print(f"FINAL_EXACT {day} cache-hit trades={len(cached_f.get('trades') or [])}", flush=True)
        else:
            r = by_date[day]
            final_jobs.append(
                {
                    "date": day,
                    "capture_path": r["capture_path"],
                    "universe": r["universe_symbols"],
                    "fit": slim_final,
                    "stage": "FINAL_EXACT",
                }
            )
    print(f"exact jobs OOF={len(oof_jobs)} FINAL={len(final_jobs)}", flush=True)
    for body in _pool(process_exact, oof_jobs, "OOF_EXACT"):
        if body.get("ok"):
            _save_json(CACHE / f"{body.get('date')}_OOF_EXACT.json", body)
        oof_got.append(body)
    for body in _pool(process_exact, final_jobs, "FINAL_EXACT"):
        if body.get("ok"):
            _save_json(CACHE / f"{body.get('date')}_FINAL_EXACT.json", body)
        final_got.append(body)
    oof_fail = [b for b in oof_got if not b.get("ok")]
    final_fail = [b for b in final_got if not b.get("ok")]
    if oof_fail or final_fail or len(oof_got) != 18 or len(final_got) != 18:
        print("STOP Exact failed", [(b.get("date"), b.get("blocker")) for b in oof_fail + final_fail], flush=True)
        dec = decide(integrity_ok=False, gate_pass=True, exact_ran=False, success=None, fill_remains=None)
        return write_report(
            {
                **required_base,
                "FINAL_SPEC": spec_label(final_spec),
                "VERDICT": "TOP1_DEVELOPMENT_INTEGRITY_FAILED",
                "NEXT_RESEARCH": "NONE",
            },
            dec,
            extra=extra_common,
            sheets_extra=sheets_common,
        )

    oof_trades = []
    final_trades = []
    for b in oof_got:
        oof_trades.extend(b.get("trades") or [])
    for b in final_got:
        final_trades.extend(b.get("trades") or [])
    oof_pack = pack_metrics(oof_trades, days)
    final_pack = pack_metrics(final_trades, days)
    print(f"OOF_EXACT {pack_line(oof_pack)}", flush=True)
    print(f"FINAL_EXACT {pack_line(final_pack)}", flush=True)

    wf_by = wf_index(_load_would_fill())
    attach_would_fill(matched, wf_by)
    cur1 = topk_exec_block(matched, "current_score", 1)
    m1 = topk_exec_block(matched, SCORE_KEY, 1)
    fill_remains = fill_edge_vs_current(m1.get("FILL_TO_600S_RETURN_MEAN"), cur1.get("FILL_TO_600S_RETURN_MEAN"))
    success = rank1_exact_success(oof_pack, a0_pack, a2_pack, gate_pass=True)
    oof_first_n, oof_first_pnl = _slice_n_pnl(oof_pack, "first_entry")
    oof_re_n, oof_re_pnl = _slice_n_pnl(oof_pack, "re_entry")

    dec = decide(integrity_ok=True, gate_pass=True, exact_ran=True, success=success, fill_remains=fill_remains)
    required = {
        **required_base,
        "FINAL_SPEC": spec_label(final_spec),
        "OOF_EXACT_RAN": True,
        "OOF_TOP1_EXACT": pack_line(oof_pack),
        "OOF_TOP1_EXACT_TRADES": oof_pack.get("trades"),
        "OOF_TOP1_EXACT_PNL": oof_pack.get("PnL"),
        "OOF_TOP1_EXACT_PF": oof_pack.get("PF"),
        "OOF_TOP1_EXACT_DD": oof_pack.get("maxDD"),
        "OOF_TOP1_EXACT_FIRST_N": oof_first_n,
        "OOF_TOP1_EXACT_FIRST_PNL": oof_first_pnl,
        "OOF_TOP1_EXACT_REENTRY_N": oof_re_n,
        "OOF_TOP1_EXACT_REENTRY_PNL": oof_re_pnl,
        "FINAL_EXACT_RAN": True,
        "FINAL_TOP1_EXACT": pack_line(final_pack),
        "CURRENT_RANK1_WOULD_FILL": cur1.get("WOULD_FILL_1S_RATE"),
        "TOP1_MODEL_RANK1_WOULD_FILL": m1.get("WOULD_FILL_1S_RATE"),
        "CURRENT_RANK1_FILL_TO_600": cur1.get("FILL_TO_600S_RETURN_MEAN"),
        "TOP1_MODEL_RANK1_FILL_TO_600": m1.get("FILL_TO_600S_RETURN_MEAN"),
        "VERDICT": dec.get("VERDICT"),
        "NEXT_RESEARCH": dec.get("NEXT_RESEARCH"),
        "CASE": dec.get("CASE"),
    }
    extra_common["success"] = success
    extra_common["oof_pack"] = oof_pack
    extra_common["final_pack"] = final_pack
    extra_common["final_spec"] = final_spec
    extra_common["exec_check"] = {"CURRENT_RANK1": cur1, "TOP1_MODEL_RANK1": m1, "fill_remains_vs_current": fill_remains}
    sheets_common["Exact"] = kv_rows(
        {
            "OOF_TOP1_EXACT": pack_line(oof_pack),
            "FINAL_TOP1_EXACT": pack_line(final_pack),
            "FINAL_SPEC": spec_label(final_spec),
            "success": success,
        }
    )
    sheets_common["ExecCheck"] = [
        {"lane": "CURRENT_RANK1", **cur1},
        {"lane": "TOP1_MODEL_RANK1", **m1},
        {"lane": "fill_remains_vs_current", "value": fill_remains},
    ]
    return write_report(required, dec, extra=extra_common, sheets_extra=sheets_common)


if __name__ == "__main__":
    raise SystemExit(main())
