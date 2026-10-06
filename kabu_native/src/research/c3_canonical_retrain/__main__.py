"""Offline C3 Canonical retrain. Same protocol. No Runtime write. No Paper. No C4 auto-start."""
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
from research.anchor_vs_event_driven.run_comparison import _bare
from research.c3_canonical_retrain import (
    ANALYSIS_ID,
    B2_FORMAL,
    C14_CHANGED,
    C14_ID,
    C2_STATUS_MAINTAINED,
    C3_AUDIT_VERDICT_MAINTAINED,
    C3_RECON_VERDICT_MAINTAINED,
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
    OPVAL_OPERATED,
    PAPER_OPERATED,
    PARITY_VERDICT_MAINTAINED,
    REBASE_VERDICT_MAINTAINED,
    REENTRY_V2_FORMAL,
    RUNTIME_CHANGED,
    TRUE_OOS,
)
from research.c3_canonical_retrain.analyze import (
    c3_actual,
    common_diagnostic,
    current_actual,
    decide,
    legacy_compare,
    matched_rows,
    oof_exact_converts,
)
from research.c3_canonical_retrain.oof import (
    inner_select,
    process_outer_fold,
    slim_fit,
    stitch_oof,
)
from research.c3_canonical_retrain.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from research.c3_canonical_retrain.replay import process_day as process_train
from research.canonical_entry_performance_rebase.analyze import (
    attach_would_fill,
    join_target,
    pack_line,
    ranking_no_backfill,
    row_key,
    slim_rank,
    target_integrity,
    topk_exec_block,
    wf_index,
)
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.entry_decision_population_contract.analyze import snap_key
from research.entry_objective_redesign_c3.analyze import exact_success, paired_daily, ranking_gate
from research.entry_objective_redesign_c3.oof import SCORE_KEY, fit_on_universe, spec_grid, spec_label
from research.entry_objective_redesign_c3.replay import process_day as process_exact
from research.entry_panel_exact_reconciliation.analyze import causality_from_snaps
from research.executable_target_v2_b_threshold.analyze import pack_metrics
from small_paper.v1r_native_entry_live import FEATURE_ORDER

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
CACHE = NATIVE / "results" / "research" / "_work_cache" / "c3_canonical_retrain"
C3_PANEL_CACHE = NATIVE / "results" / "research" / "entry_objective_redesign_c3" / "_work_cache"
A_CACHE = NATIVE / "results" / "research" / "current_entry_nonexec_mechanism" / "_work_cache"
COUPLE_CACHE = NATIVE / "results" / "research" / "c3_execution_coupling_audit" / "_work_cache"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _cache_fp(day: str, stage: str) -> Path:
    return CACHE / f"{day}_{stage}.json"


def _load_train_cache(day: str) -> dict | None:
    fp = _cache_fp(day, "TRAIN")
    if not fp.is_file():
        return None
    try:
        body = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    if body.get("ok") and body.get("date") == day and body.get("train_features") == "causal_f0_f1":
        return body
    return None


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
                f"done {label} {key} ok={body.get('ok')} "
                f"n={len(body.get('snaps') or body.get('trades') or body.get('scored') or [])} "
                f"sec={body.get('elapsed_sec')} blocker={body.get('blocker')}",
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


def materialize(snaps: list[dict], panel_by: dict[str, dict]) -> list[dict]:
    out = []
    for s in snaps:
        rec = join_target(s, panel_by.get(row_key(s)))
        rec["symbol"] = _bare(rec.get("symbol"))
        rec["executable_at_t0"] = bool(rec.get("exact_executable") or rec.get("canonical_executable"))
        out.append(rec)
    return out


def write_stop(required: dict, extra: dict | None = None, sheets_extra: dict | None = None) -> int:
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": required,
        "decision": {
            "VERDICT": required.get("VERDICT"),
            "NEXT_RESEARCH": required.get("NEXT_RESEARCH"),
            "note": required.get("note") or "STOP.",
        },
        **(extra or {}),
    }
    report["_markdown"] = build_markdown(report)
    sheets = {
        "Summary": kv_rows(required),
        "Manifest": kv_rows({"ANALYSIS_ID": ANALYSIS_ID, "STOP": True}),
        "Integrity": kv_rows(required),
        "OOFGate": [{"empty": True}],
        "PairedDays": [{"empty": True}],
        "Folds": [{"empty": True}],
        "Coverage": [{"empty": True}],
        "Parity": [{"empty": True}],
        "Exact": [{"empty": True}],
        "ExecCoupling": [{"empty": True}],
        "Decision": kv_rows(report["decision"]),
        "Safety": kv_rows(
            {
                "submit_cancel_live": "0/0/0",
                "Paper": 0,
                "OPVAL": 0,
                "C14_CHANGED": C14_CHANGED,
                "RUNTIME_CHANGED": RUNTIME_CHANGED,
                "C4_STARTED": C4_STARTED,
                "LEGACY_COEFFICIENTS_REUSED": LEGACY_COEFFICIENTS_REUSED,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print("STOP. No C4. Runtime unchanged. submit/cancel/live=0/0/0.", flush=True)
    return 2


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE C3 CANONICAL RETRAIN SAME PROTOCOL V2", flush=True)
    print("Ridge 27 specs. Nested OOF. Rank then join TARGET. No legacy coefficients.", flush=True)

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

    panel: list[dict] = []
    for day in ELIGIBLE_DAYS:
        body = _load(C3_PANEL_CACHE / f"{day}_C3_PANEL.json")
        if not body.get("ok"):
            print("STOP panel missing", day, flush=True)
            return 2
        panel.extend(body.get("rows") or [])
    panel_by = {snap_key(r): r for r in panel}

    jobs = []
    got = []
    for day in ELIGIBLE_DAYS:
        cached = _load_train_cache(day)
        if cached:
            got.append(cached)
            print(f"TRAIN {day} cache-hit snaps={len(cached.get('snaps') or [])}", flush=True)
            continue
        r = by_date[day]
        jobs.append({"date": day, "capture_path": r["capture_path"], "universe": r["universe_symbols"]})
    print(f"train harvest jobs={len(jobs)}", flush=True)
    for body in _pool(process_train, jobs, "TRAIN"):
        if body.get("ok"):
            _save_json(_cache_fp(str(body.get("date")), "TRAIN"), body)
        got.append(body)
    fail = [b for b in got if not b.get("ok")]
    if fail or len(got) != len(ELIGIBLE_DAYS):
        print("STOP train harvest failed", [(b.get("date"), b.get("blocker")) for b in fail], flush=True)
        return write_stop(
            {
                "VERDICT": "C3_CANONICAL_RETRAIN_INTEGRITY_FAILED",
                "NEXT_RESEARCH": "NONE",
                "note": "CanonicalEngine training harvest failed.",
            }
        )

    snaps: list[dict] = []
    for b in got:
        snaps.extend(b.get("snaps") or [])
    rows = materialize(snaps, panel_by)
    exec_rows = [r for r in rows if r.get("executable_at_t0")]
    cas = causality_from_snaps(snaps)
    future_n = int(cas.get("FUTURE_EVENT_USE_N") or 0)
    integ_exec = target_integrity(exec_rows)
    print(
        f"train_snaps={len(snaps)} exec={len(exec_rows)} future={future_n} "
        f"unexpected={integ_exec.get('TARGET_UNEXPECTED_MISSING_N')} contam={integ_exec.get('TARGET_CONTAMINATION_N')}",
        flush=True,
    )
    integrity_ok = bool(future_n == 0 and integ_exec.get("ok"))
    if not integrity_ok:
        return write_stop(
            {
                "CANONICAL_TRAINING_ROWS": len(exec_rows),
                "FUTURE_EVENT_USE_N": future_n,
                "TARGET_UNEXPECTED_MISSING_N": integ_exec.get("TARGET_UNEXPECTED_MISSING_N"),
                "TARGET_CONTAMINATION_N": integ_exec.get("TARGET_CONTAMINATION_N"),
                "VERDICT": "C3_CANONICAL_RETRAIN_INTEGRITY_FAILED",
                "NEXT_RESEARCH": "NONE",
                "OOF_RANKING_GATE_PASS": False,
                "FINAL_EXACT_RAN": False,
                "OOF_EXACT_RAN": False,
                "TRUE_OOS": False,
                "NEW_FORWARD_N": 0,
            },
            extra={"causality": cas, "target_integrity": integ_exec},
        )

    CACHE.mkdir(parents=True, exist_ok=True)
    rows_path = CACHE / "train_rows.json"
    _save_json(rows_path, {"rows": exec_rows})
    print(f"wrote train_rows n={len(exec_rows)}", flush=True)

    fold_bodies = []
    fold_jobs = []
    for day in ELIGIBLE_DAYS:
        fp = CACHE / f"oof_fold_{day}.json"
        if fp.is_file():
            saved = json.loads(fp.read_text(encoding="utf-8"))
            if saved.get("ok") and saved.get("hold") == day and saved.get("scored"):
                fold_bodies.append(saved)
                print(f"OOF fold cache-hit {day} spec={((saved.get('spec') or {}).get('spec_id'))}", flush=True)
                continue
        fold_jobs.append({"hold": day, "rows_path": str(rows_path)})
    print(f"outer fold jobs={len(fold_jobs)}", flush=True)
    for body in _pool(process_outer_fold, fold_jobs, "OOF"):
        if body.get("ok"):
            _save_json(CACHE / f"oof_fold_{body.get('hold')}.json", body)
        fold_bodies.append(body)
    fold_fail = [b for b in fold_bodies if not b.get("ok")]
    if fold_fail or len(fold_bodies) != len(ELIGIBLE_DAYS):
        print("STOP nested OOF failed", [(b.get("hold"), b.get("blocker")) for b in fold_fail], flush=True)
        return write_stop(
            {
                "VERDICT": "C3_CANONICAL_RETRAIN_INTEGRITY_FAILED",
                "NEXT_RESEARCH": "NONE",
                "note": "Nested OOF worker failed.",
                "OOF_RANKING_GATE_PASS": False,
                "FINAL_EXACT_RAN": False,
                "OOF_EXACT_RAN": False,
                "TRUE_OOS": False,
                "NEW_FORWARD_N": 0,
            }
        )

    stitched = stitch_oof(fold_bodies, exec_rows)
    attached = stitched.get("attached") or []
    matched = matched_rows(attached)
    rank_cur = ranking_no_backfill(matched, "current_score")
    rank_c3 = ranking_no_backfill(matched, SCORE_KEY)
    paired = paired_daily(rank_cur, rank_c3)
    gate = ranking_gate(rank_cur, rank_c3, paired)
    cov = common_diagnostic(attached, stitched.get("fits") or {})
    leg = legacy_compare(
        delta=_f_delta(paired.get("TOP3_DELTA_MEAN")),
        pos=int(paired.get("TOP3_DELTA_POSITIVE_DAYS") or 0),
        neg=int(paired.get("TOP3_DELTA_NEGATIVE_DAYS") or 0),
        ex3=_f_delta(paired.get("TOP3_DELTA_EX_TOP3_DAYS")),
    )
    print(
        f"MATCHED={len(matched)} C3_TOP3={rank_c3.get('TOP3_UPLIFT')} "
        f"CUR_TOP3={rank_cur.get('TOP3_UPLIFT')} delta={paired.get('TOP3_DELTA_MEAN')} "
        f"GATE={gate.get('OOF_RANKING_GATE_PASS')} fail={gate.get('fail')}",
        flush=True,
    )

    a0_trades, a0_miss = _load_a_trades("A0")
    a2_trades, a2_miss = _load_a_trades("A2")
    days = list(ELIGIBLE_DAYS)
    a0_pack = pack_metrics(a0_trades, days) if not a0_miss else {}
    a2_pack = pack_metrics(a2_trades, days) if not a2_miss else {}
    from research.canonical_entry_performance_rebase.analyze import parity_against

    p0 = parity_against(a0_pack, EXPECTED_A0) if a0_pack else {"ok": False}
    p2 = parity_against(a2_pack, EXPECTED_A2) if a2_pack else {"ok": False}
    print(f"A0_PARITY={p0.get('ok')} {pack_line(a0_pack) if a0_pack else 'n/a'}", flush=True)
    print(f"A2_PARITY={p2.get('ok')} {pack_line(a2_pack) if a2_pack else 'n/a'}", flush=True)

    oof_exact_ran = False
    final_exact_ran = False
    final_spec = None
    oof_pack = None
    c3_pack = None
    success = None
    adverse: bool | None = None
    coupling: dict[str, Any] = {}
    oof_converts = False

    required_base = {
        "CANONICAL_TRAINING_ROWS": len(exec_rows),
        "MATCHED_RANKING_ROWS": len(matched),
        "CURRENT_TOP1_UPLIFT": rank_cur.get("TOP1_UPLIFT"),
        "CURRENT_TOP3_UPLIFT": rank_cur.get("TOP3_UPLIFT"),
        "CURRENT_TOP5_UPLIFT": rank_cur.get("TOP5_UPLIFT"),
        "CANON_C3_TOP1_UPLIFT": rank_c3.get("TOP1_UPLIFT"),
        "CANON_C3_TOP3_UPLIFT": rank_c3.get("TOP3_UPLIFT"),
        "CANON_C3_TOP5_UPLIFT": rank_c3.get("TOP5_UPLIFT"),
        "TOP3_DELTA_MEAN": paired.get("TOP3_DELTA_MEAN"),
        "TOP3_DELTA_MEDIAN": paired.get("TOP3_DELTA_MEDIAN"),
        "TOP3_DELTA_POSITIVE_DAYS": paired.get("TOP3_DELTA_POSITIVE_DAYS"),
        "TOP3_DELTA_NEGATIVE_DAYS": paired.get("TOP3_DELTA_NEGATIVE_DAYS"),
        "TOP3_DELTA_EX_BEST_DAY": paired.get("TOP3_DELTA_EX_BEST_DAY"),
        "TOP3_DELTA_EX_TOP3_DAYS": paired.get("TOP3_DELTA_EX_TOP3_DAYS"),
        "CANON_C3_MEAN_DAILY_SPEARMAN": rank_c3.get("MEAN_DAILY_SPEARMAN"),
        "MOST_COMMON_SPEC": stitched.get("MOST_COMMON_SPEC"),
        "MOST_COMMON_SPEC_SHARE": stitched.get("MOST_COMMON_SPEC_SHARE"),
        "SPEC_NATIVE_TOP3_DELTA": cov.get("SPEC_NATIVE_TOP3_DELTA"),
        "COMMON_POP_TOP3_DELTA": cov.get("COMMON_POP_TOP3_DELTA"),
        "EDGE_DEPENDS_ON_COVERAGE_SELECTION": cov.get("EDGE_DEPENDS_ON_COVERAGE_SELECTION"),
        "OOF_RANKING_GATE_PASS": bool(gate.get("OOF_RANKING_GATE_PASS")),
        "A0_PARITY": bool(p0.get("ok")),
        "A2_PARITY": bool(p2.get("ok")),
        "FINAL_SPEC": None,
        "FINAL_EXACT_RAN": False,
        "OOF_EXACT_RAN": False,
        "CANON_C3_OOF_EXACT": "n/a",
        "CANON_C3_FINAL_EXACT": "n/a",
        "EXACT_SUCCESS_BAR_PASS": False,
        "PASSIVE_FILL_ADVERSE_SELECTION": "not_run",
        "LEGACY_C3_COMPARISON": leg,
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "FUTURE_EVENT_USE_N": future_n,
        "TARGET_UNEXPECTED_MISSING_N": integ_exec.get("TARGET_UNEXPECTED_MISSING_N"),
        "TARGET_CONTAMINATION_N": integ_exec.get("TARGET_CONTAMINATION_N"),
        "SPEC_COUNTS": stitched.get("SPEC_COUNTS"),
        "OOF_GATE_FAIL": gate.get("fail"),
    }

    if not gate.get("OOF_RANKING_GATE_PASS"):
        dec = decide(
            integrity_ok=True,
            gate=gate,
            exact_ran=False,
            oof_exact_ran=False,
            success=None,
            oof_converts=False,
            adverse=None,
        )
        required = {**required_base, **{k: dec.get(k) for k in ("VERDICT", "NEXT_RESEARCH")}}
        return _finish(
            required,
            dec,
            gate,
            paired,
            rank_cur,
            rank_c3,
            stitched,
            cov,
            p0,
            p2,
            a0_pack,
            a2_pack,
            cas,
            integ_exec,
            coupling={},
            success=None,
        )

    if not p0.get("ok") or not p2.get("ok"):
        return write_stop(
            {
                **required_base,
                "VERDICT": "C3_CANONICAL_RETRAIN_INTEGRITY_FAILED",
                "NEXT_RESEARCH": "NONE",
                "note": "A0/A2 Exact parity anchor missed before candidate Exact. Values not adopted as new SoT.",
            },
            extra={"parity": {"A0": p0, "A2": p2}},
            sheets_extra={"Parity": kv_rows({"A0": p0, "A2": p2})},
        )

    print("final spec select on all development days (same inner objective)", flush=True)
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
        cached = _load(_cache_fp(day, "OOF_EXACT"))
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
                    "score_lookup": {},
                }
            )
        cached_f = _load(_cache_fp(day, "C3_EXACT"))
        if cached_f.get("ok") and cached_f.get("date") == day:
            final_got.append(cached_f)
            print(f"C3_EXACT {day} cache-hit trades={len(cached_f.get('trades') or [])}", flush=True)
        else:
            r = by_date[day]
            final_jobs.append(
                {
                    "date": day,
                    "capture_path": r["capture_path"],
                    "universe": r["universe_symbols"],
                    "fit": slim_final,
                    "score_lookup": {},
                }
            )
    print(f"exact jobs OOF={len(oof_jobs)} FINAL={len(final_jobs)}", flush=True)
    for body in _pool(process_exact, oof_jobs, "OOF_EXACT"):
        body["stage"] = "OOF_EXACT"
        if body.get("ok"):
            _save_json(_cache_fp(str(body.get("date")), "OOF_EXACT"), body)
        oof_got.append(body)
    for body in _pool(process_exact, final_jobs, "C3_EXACT"):
        body["stage"] = "C3_EXACT"
        if body.get("ok"):
            _save_json(_cache_fp(str(body.get("date")), "C3_EXACT"), body)
        final_got.append(body)
    oof_fail = [b for b in oof_got if not b.get("ok")]
    final_fail = [b for b in final_got if not b.get("ok")]
    if oof_fail or final_fail or len(oof_got) != 18 or len(final_got) != 18:
        print("STOP Exact failed", [(b.get("date"), b.get("blocker")) for b in oof_fail + final_fail], flush=True)
        return write_stop(
            {
                **required_base,
                "FINAL_SPEC": spec_label(final_spec),
                "VERDICT": "C3_CANONICAL_RETRAIN_INTEGRITY_FAILED",
                "NEXT_RESEARCH": "NONE",
                "note": "Exact Dual-Lane failed after OOF gate pass.",
            }
        )

    oof_trades = []
    c3_trades = []
    for b in oof_got:
        oof_trades.extend(b.get("trades") or [])
    for b in final_got:
        c3_trades.extend(b.get("trades") or [])
    oof_pack = pack_metrics(oof_trades, days)
    c3_pack = pack_metrics(c3_trades, days)
    oof_exact_ran = True
    final_exact_ran = True
    print(f"OOF_EXACT {pack_line(oof_pack)}", flush=True)
    print(f"FINAL_EXACT {pack_line(c3_pack)}", flush=True)

    success = exact_success(c3_pack, a0_pack, a2_pack, gate_pass=True)
    oof_converts = oof_exact_converts(oof_pack, a2_pack)

    wf_by = wf_index(_load_would_fill())
    attach_would_fill(attached, wf_by)
    matched_wf = matched_rows(attached)
    act_cur = current_actual(attached)
    act_c3 = c3_actual(attached)
    coupling = {
        "MATCHED": {
            "CURRENT_TOP1": topk_exec_block(matched_wf, "current_score", 1),
            "CURRENT_TOP3": topk_exec_block(matched_wf, "current_score", 3),
            "CURRENT_TOP5": topk_exec_block(matched_wf, "current_score", 5),
            "C3_TOP1": topk_exec_block(matched_wf, SCORE_KEY, 1),
            "C3_TOP3": topk_exec_block(matched_wf, SCORE_KEY, 3),
            "C3_TOP5": topk_exec_block(matched_wf, SCORE_KEY, 5),
        },
        "ACTUAL": {
            "CURRENT_TOP1": topk_exec_block(act_cur, "current_score", 1),
            "CURRENT_TOP3": topk_exec_block(act_cur, "current_score", 3),
            "CURRENT_TOP5": topk_exec_block(act_cur, "current_score", 5),
            "C3_TOP1": topk_exec_block(act_c3, SCORE_KEY, 1),
            "C3_TOP3": topk_exec_block(act_c3, SCORE_KEY, 3),
            "C3_TOP5": topk_exec_block(act_c3, SCORE_KEY, 5),
        },
    }
    cur_f6 = (coupling["MATCHED"]["CURRENT_TOP3"] or {}).get("FILL_TO_600S_RETURN_MEAN")
    c3_f6 = (coupling["MATCHED"]["C3_TOP3"] or {}).get("FILL_TO_600S_RETURN_MEAN")
    adverse = bool(
        _f_delta(cur_f6) is not None and _f_delta(c3_f6) is not None and float(c3_f6) < float(cur_f6)
    )
    rank_edge = bool(gate.get("OOF_RANKING_GATE_PASS"))
    oof_first_n, oof_first_pnl = _slice_n_pnl(oof_pack, "first_entry")
    oof_re_n, oof_re_pnl = _slice_n_pnl(oof_pack, "re_entry")

    dec = decide(
        integrity_ok=True,
        gate=gate,
        exact_ran=final_exact_ran,
        oof_exact_ran=oof_exact_ran,
        success=success,
        oof_converts=oof_converts,
        adverse=adverse,
    )
    required = {
        **required_base,
        "FINAL_SPEC": spec_label(final_spec),
        "FINAL_EXACT_RAN": True,
        "OOF_EXACT_RAN": True,
        "CANON_C3_OOF_EXACT": pack_line(oof_pack),
        "CANON_C3_FINAL_EXACT": pack_line(c3_pack),
        "EXACT_SUCCESS_BAR_PASS": bool(success.get("HISTORICAL_ENTRY_CANDIDATE")),
        "PASSIVE_FILL_ADVERSE_SELECTION": adverse,
        "OOF_EXACT_TRADES": oof_pack.get("trades"),
        "OOF_EXACT_PNL": oof_pack.get("PnL"),
        "OOF_EXACT_PF": oof_pack.get("PF"),
        "OOF_EXACT_DD": oof_pack.get("maxDD"),
        "OOF_EXACT_FIRST_N": oof_first_n,
        "OOF_EXACT_FIRST_PNL": oof_first_pnl,
        "OOF_EXACT_REENTRY_N": oof_re_n,
        "OOF_EXACT_REENTRY_PNL": oof_re_pnl,
        "VERDICT": dec.get("VERDICT"),
        "NEXT_RESEARCH": dec.get("NEXT_RESEARCH"),
        "rank_edge": rank_edge,
        "oof_converts": oof_converts,
    }
    extra_exact = {
        "oof_pack": oof_pack,
        "c3_pack": c3_pack,
        "success": success,
        "final_spec": final_spec,
        "final_fit_kind": final_fit.get("kind"),
        "final_train_n": final_fit.get("train_n"),
    }
    return _finish(
        required,
        dec,
        gate,
        paired,
        rank_cur,
        rank_c3,
        stitched,
        cov,
        p0,
        p2,
        a0_pack,
        a2_pack,
        cas,
        integ_exec,
        coupling,
        success,
        extra=extra_exact,
    )


def _f_delta(v: Any):
    from research.canonical_entry_performance_rebase.analyze import _f

    return _f(v)


def _finish(
    required: dict,
    dec: dict,
    gate: dict,
    paired: dict,
    rank_cur: dict,
    rank_c3: dict,
    stitched: dict,
    cov: dict,
    p0: dict,
    p2: dict,
    a0_pack: dict,
    a2_pack: dict,
    cas: dict,
    integ_exec: dict,
    coupling: dict,
    success: dict | None,
    extra: dict | None = None,
) -> int:
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": required,
        "decision": dec,
        "gate": gate,
        "ranking_matched": {"CURRENT": slim_rank(rank_cur), "C3": slim_rank(rank_c3), "paired": {k: v for k, v in paired.items() if k != "days"}},
        "coverage": {k: v for k, v in cov.items() if k not in {"native_current", "native_c3", "common_current", "common_c3"}},
        "folds": stitched.get("folds"),
        "SPEC_COUNTS": stitched.get("SPEC_COUNTS"),
        "parity": {"A0": p0, "A2": p2},
        "A0": a0_pack,
        "A2": a2_pack,
        "causality": cas,
        "target_integrity": {k: v for k, v in integ_exec.items() if k != "counts"},
        "execution_coupling": coupling,
        "success": success,
        "frozen": {
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
            "NEW_FEATURE_CREATED": NEW_FEATURE_CREATED,
            "EXECUTION_AWARE_MODEL_CREATED": EXECUTION_AWARE_MODEL_CREATED,
            "LEGACY_COEFFICIENTS_REUSED": LEGACY_COEFFICIENTS_REUSED,
            "TRUE_OOS": TRUE_OOS,
            "NEW_FORWARD_N": NEW_FORWARD_N,
        },
        **(extra or {}),
    }
    report["_markdown"] = build_markdown(report)

    def _flat_couple() -> list[dict]:
        out = []
        for pop, blk in (coupling or {}).items():
            if not isinstance(blk, dict):
                continue
            for name, rec in blk.items():
                out.append({"population": pop, "name": name, **(rec or {})})
        return out or [{"empty": True}]

    sheets = {
        "Summary": kv_rows(required),
        "Manifest": kv_rows(
            {
                "ANALYSIS_ID": ANALYSIS_ID,
                "SOURCE": "CanonicalEngine only",
                "CLOCK": "CURRENT IRREGULAR",
                "TARGET": "TARGET V4 M4_PERSISTENT 600s",
                "Fill": "Corrected Passive Fill",
                "MODEL": "Ridge 27 specs. No RF. No pairwise.",
                "RANK": "eligible → feature → score → rank → TopK fixed → Target join. NO TARGET BACKFILL.",
                "LEGACY_COEFFICIENTS_REUSED": LEGACY_COEFFICIENTS_REUSED,
            }
        ),
        "Integrity": kv_rows(
            {
                "FUTURE_EVENT_USE_N": cas.get("FUTURE_EVENT_USE_N"),
                "TARGET_UNEXPECTED_MISSING_N": integ_exec.get("TARGET_UNEXPECTED_MISSING_N"),
                "TARGET_CONTAMINATION_N": integ_exec.get("TARGET_CONTAMINATION_N"),
                **{k: v for k, v in integ_exec.items() if k != "counts"},
            }
        ),
        "OOFGate": kv_rows(gate),
        "PairedDays": paired.get("days") or [{"empty": True}],
        "Folds": stitched.get("folds") or [{"empty": True}],
        "Coverage": kv_rows(
            {
                "SPEC_NATIVE_TOP3_DELTA": cov.get("SPEC_NATIVE_TOP3_DELTA"),
                "COMMON_POP_TOP3_DELTA": cov.get("COMMON_POP_TOP3_DELTA"),
                "COVERAGE_EDGE_CONTRIBUTION": cov.get("COVERAGE_EDGE_CONTRIBUTION"),
                "EDGE_DEPENDS_ON_COVERAGE_SELECTION": cov.get("EDGE_DEPENDS_ON_COVERAGE_SELECTION"),
                "COMMON_DIAGNOSTIC_ROWS": cov.get("COMMON_DIAGNOSTIC_ROWS"),
                "note": cov.get("note"),
            }
        ),
        "Parity": kv_rows({"A0": p0, "A2": p2}),
        "Exact": kv_rows(
            {
                "OOF_EXACT": required.get("CANON_C3_OOF_EXACT"),
                "FINAL_EXACT": required.get("CANON_C3_FINAL_EXACT"),
                "FINAL_SPEC": required.get("FINAL_SPEC"),
                "success": success,
            }
        ),
        "ExecCoupling": _flat_couple(),
        "Decision": kv_rows(dec),
        "Safety": kv_rows(
            {
                "submit_cancel_live": "0/0/0",
                "Paper": 0,
                "OPVAL": 0,
                "C14_CHANGED": C14_CHANGED,
                "RUNTIME_CHANGED": RUNTIME_CHANGED,
                "C4_STARTED": C4_STARTED,
                "EXECUTION_AWARE_MODEL_CREATED": EXECUTION_AWARE_MODEL_CREATED,
                "LEGACY_COEFFICIENTS_REUSED": LEGACY_COEFFICIENTS_REUSED,
            }
        ),
    }
    write_artifacts(report, sheets)
    print(f"VERDICT={dec.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print("STOP. No C4 auto-start. Runtime unchanged. submit/cancel/live=0/0/0.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
