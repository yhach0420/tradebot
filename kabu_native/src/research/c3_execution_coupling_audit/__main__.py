"""Offline C3 OOF → Exact execution coupling audit. No Runtime write. No Paper. No C4."""
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
from research.c3_execution_coupling_audit import (
    ANALYSIS_ID,
    B2_FORMAL,
    C14_CHANGED,
    C14_ID,
    C2_STATUS_MAINTAINED,
    C3_IMPLEMENTED,
    C3_VERDICT_MAINTAINED,
    C4_STARTED,
    CLOCK_SEARCHED,
    ELIGIBLE_DAYS,
    FINAL_SPEC,
    FIT_KEYS,
    MAX_WORKERS,
    NEW_FEATURE_CREATED,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    NEW_THRESHOLD_CREATED,
    OOF_EXACT_KIND,
    OPVAL_OPERATED,
    PAPER_OPERATED,
    PNL_OPTIMIZED,
    REENTRY_SEARCHED,
    REENTRY_V2_FORMAL,
    RUNTIME_CHANGED,
    TRUE_OOS,
    WAIT_SEC,
)
from research.c3_execution_coupling_audit.analyze import (
    a2_parity,
    abc_line,
    all_target_mean,
    attach_scores,
    c3_final_parity,
    decile_block,
    decide,
    entry_vs_exit,
    fillability_split,
    first_entry_decomp,
    funnel_from_panel,
    lineage_lost,
    occupancy_summary,
    questions,
    reconstruct_a2_occupancy,
    replacement_block,
    score_map,
    slim_pack,
    slot_occupancy_sec,
    topk_block,
    wf_map,
)
from research.c3_execution_coupling_audit.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from research.c3_execution_coupling_audit.replay import process_day as process_exact
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.entry_objective_redesign_c3.oof import (
    executable_rows,
    fit_on_universe,
    score_rows,
    spec_label,
)
from research.executable_target_v2_b_threshold.analyze import pack_metrics
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import CLOCK_GRID

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
CACHE = OUT / "_work_cache"
C3_CACHE = NATIVE / "results" / "research" / "entry_objective_redesign_c3" / "_work_cache"
A_CACHE = NATIVE / "results" / "research" / "current_entry_nonexec_mechanism" / "_work_cache"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _cache_fp(day: str, stage: str) -> Path:
    return CACHE / f"{day}_{stage}.json"


def _load_cache(day: str, stage: str) -> dict | None:
    fp = _cache_fp(day, stage)
    if not fp.is_file():
        return None
    try:
        body = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    if body.get("ok") and body.get("date") == day and body.get("stage") == stage:
        return body
    return None


def _save_cache(day: str, stage: str, body: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    slim = dict(body)
    _cache_fp(day, stage).write_text(
        json.dumps(json_sanitize(slim), ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _slim_fit(fit: dict) -> dict:
    return {k: fit.get(k) for k in FIT_KEYS}


def _pool(jobs: list[dict]) -> list[dict]:
    if not jobs:
        return []
    out = []
    workers = min(MAX_WORKERS, len(jobs))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(process_exact, job): (job["date"], job.get("stage")) for job in jobs}
        for fut in as_completed(futs):
            day, stage = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {
                    "ok": False,
                    "date": day,
                    "stage": stage,
                    "blocker": f"{type(exc).__name__}:{exc}",
                }
            out.append(body)
            print(
                f"done {day} stage={body.get('stage')} ok={body.get('ok')} "
                f"trades={len(body.get('trades') or [])} sec={body.get('elapsed_sec')} "
                f"blocker={body.get('blocker')}",
                flush=True,
            )
    return out


def _load_stage_trades(cache_dir: Path, days: tuple[str, ...], stage: str) -> tuple[list[dict], list[str]]:
    trades: list[dict] = []
    missing: list[str] = []
    for day in days:
        fp = cache_dir / f"{day}_{stage}.json"
        if not fp.is_file():
            missing.append(day)
            continue
        body = json.loads(fp.read_text(encoding="utf-8"))
        if not body.get("ok"):
            missing.append(day)
            continue
        trades.extend(body.get("trades") or [])
    return trades, missing


def _load_a2_traces() -> tuple[dict[str, Any], list[str]]:
    traces: dict[str, Any] = {
        "admits": [],
        "fills": [],
        "expired": [],
        "candidates": [],
        "occupancy": [],
        "slot_release_n": 0,
        "cap_blocked": 0,
        "same_symbol_blocked": 0,
    }
    missing: list[str] = []
    for day in ELIGIBLE_DAYS:
        fp = A_CACHE / f"{day}_A2.json"
        if not fp.is_file():
            missing.append(day)
            continue
        body = json.loads(fp.read_text(encoding="utf-8"))
        if not body.get("ok"):
            missing.append(day)
            continue
        traces["admits"].extend(body.get("admits") or [])
        traces["fills"].extend(body.get("fills") or [])
        traces["expired"].extend(body.get("expired") or [])
        traces["slot_release_n"] += int(body.get("slot_release_n") or 0)
    return traces, missing


def _merge_traces(bodies: list[dict]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "admits": [],
        "fills": [],
        "expired": [],
        "candidates": [],
        "occupancy": [],
        "would_fill": [],
        "trades": [],
        "slot_release_n": 0,
        "cap_blocked": 0,
        "same_symbol_blocked": 0,
        "candidate_n": 0,
    }
    for b in bodies:
        for k in ("admits", "fills", "expired", "candidates", "occupancy", "would_fill", "trades"):
            out[k].extend(b.get(k) or [])
        out["slot_release_n"] += int(b.get("slot_release_n") or 0)
        out["cap_blocked"] += int(b.get("cap_blocked") or 0)
        out["same_symbol_blocked"] += int(b.get("same_symbol_blocked") or 0)
        out["candidate_n"] += int(b.get("candidate_n") or 0)
    return out


def _headline_pnl(pack: dict | None, slice_key: str) -> tuple[int | None, Any]:
    if not pack:
        return None, None
    sl = pack.get(slice_key) or {}
    n = sl.get("n") if sl.get("n") is not None else sl.get("trades")
    pnl = sl.get("pnl") if sl.get("pnl") is not None else sl.get("PnL")
    return n, pnl


def _failed_report(required: dict, extra: dict | None = None) -> int:
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": required,
        "questions": {
            "Q1": "parity failed",
            "Q2": None,
            "Q3": None,
            "Q4": None,
            "Q5": None,
            "Q6": None,
            "Q7": None,
            "Q8": "NONE",
        },
        "frozen": {
            "C3": C3_VERDICT_MAINTAINED,
            "C2": C2_STATUS_MAINTAINED,
            "B2": B2_FORMAL,
            "REENTRY_V2": REENTRY_V2_FORMAL,
        },
        **(extra or {}),
    }
    report["_markdown"] = build_markdown(report)
    sheets = {
        "Summary": kv_rows(required),
        "Manifest": kv_rows({"ANALYSIS_ID": ANALYSIS_ID, "STOP": "PARITY"}),
        "Parity": kv_rows(required),
        "Safety": kv_rows(
            {
                "submit_cancel_live": "0/0/0",
                "Paper": 0,
                "OPVAL": 0,
                "C14_CHANGED": C14_CHANGED,
                "RUNTIME_CHANGED": RUNTIME_CHANGED,
                "C4_STARTED": C4_STARTED,
                "C3_IMPLEMENTED": C3_IMPLEMENTED,
            }
        ),
    }
    write_artifacts(report, sheets)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print("STOP. C3_EXECUTION_AUDIT_FAILED. No C4. Runtime unchanged.", flush=True)
    return 2


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE C3 EXECUTION COUPLING AUDIT V2", flush=True)
    print("Diagnosis only. No new model. No C4. C14/Runtime frozen.", flush=True)

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

    a2_trades, a2_miss = _load_stage_trades(A_CACHE, ELIGIBLE_DAYS, "A2")
    c3_trades, c3_miss = _load_stage_trades(C3_CACHE, ELIGIBLE_DAYS, "C3_EXACT")
    days = list(ELIGIBLE_DAYS)
    if a2_miss or c3_miss:
        print("STOP A2/C3 cache missing", a2_miss, c3_miss, flush=True)
        return _failed_report(
            {
                "A2_PARITY": False,
                "C3_FINAL_PARITY": False,
                "VERDICT": "C3_EXECUTION_AUDIT_FAILED",
                "PRIMARY_CAUSE": "CACHE_MISSING",
            }
        )
    a2_pack = pack_metrics(a2_trades, days)
    c3_pack = pack_metrics(c3_trades, days)
    p2 = a2_parity(a2_pack)
    p3 = c3_final_parity(c3_pack)
    print(f"A2_PARITY={p2.get('ok')} {abc_line(a2_pack)}", flush=True)
    print(f"C3_FINAL_PARITY={p3.get('ok')} {abc_line(c3_pack)}", flush=True)
    if not p2.get("ok") or not p3.get("ok"):
        return _failed_report(
            {
                "A2_PARITY": bool(p2.get("ok")),
                "C3_FINAL_PARITY": bool(p3.get("ok")),
                "A2": abc_line(a2_pack),
                "C3_FINAL": abc_line(c3_pack),
                "VERDICT": "C3_EXECUTION_AUDIT_FAILED",
                "PRIMARY_CAUSE": "PARITY_FAIL",
            },
            extra={"A2_parity": p2, "C3_parity": p3},
        )

    inv = build_inventory()
    want = set(ELIGIBLE_DAYS)
    elig = [
        r
        for r in inv
        if r.get("date") in want
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
        body = _load(C3_CACHE / f"{day}_C3_PANEL.json")
        if not body.get("ok"):
            print("STOP panel missing", day, flush=True)
            return 2
        panel.extend(body.get("rows") or [])
    print(f"panel_rows={len(panel)}", flush=True)

    oof_dump = _load(C3_CACHE / "nested_oof.json")
    oof_rows = list(oof_dump.get("oof_rows") or [])
    if not oof_rows:
        for day in ELIGIBLE_DAYS:
            fold = _load(C3_CACHE / f"oof_fold_{day}.json")
            oof_rows.extend(fold.get("scored") or [])
    print(f"oof_score_rows={len(oof_rows)}", flush=True)

    print("FINAL spec refit on all development days (not a new search)", flush=True)
    final_fit = fit_on_universe(panel, dict(FINAL_SPEC))
    final_scored = score_rows(executable_rows(panel), final_fit)
    print(f"FINAL_SPEC={spec_label(FINAL_SPEC)} fit_kind={final_fit.get('kind')} train_n={final_fit.get('train_n')}", flush=True)

    oof_jobs = []
    oof_got = []
    for day in ELIGIBLE_DAYS:
        cached = _load_cache(day, "OOF_EXACT")
        if cached:
            oof_got.append(cached)
            print(f"OOF_EXACT {day} cache-hit trades={len(cached.get('trades') or [])}", flush=True)
            continue
        fold = _load(C3_CACHE / f"oof_fold_{day}.json")
        spec = dict(fold.get("spec") or {})
        if not spec.get("features"):
            spec["features"] = list(FINAL_SPEC["features"])
        train = [r for r in panel if str(r.get("date")) != day]
        fit = _slim_fit(fit_on_universe(train, spec))
        r = by_date[day]
        oof_jobs.append(
            {
                "date": day,
                "capture_path": r["capture_path"],
                "universe": r["universe_symbols"],
                "fit": fit,
                "stage": "OOF_EXACT",
            }
        )

    final_jobs = []
    final_got = []
    slim_final = _slim_fit(final_fit)
    for day in ELIGIBLE_DAYS:
        cached = _load_cache(day, "C3_FINAL_TRACE")
        if cached:
            final_got.append(cached)
            print(f"C3_FINAL_TRACE {day} cache-hit trades={len(cached.get('trades') or [])}", flush=True)
            continue
        r = by_date[day]
        final_jobs.append(
            {
                "date": day,
                "capture_path": r["capture_path"],
                "universe": r["universe_symbols"],
                "fit": slim_final,
                "stage": "C3_FINAL_TRACE",
            }
        )

    all_jobs = oof_jobs + final_jobs
    print(f"exact jobs OOF={len(oof_jobs)} FINAL_TRACE={len(final_jobs)}", flush=True)
    for body in _pool(all_jobs):
        stage = str(body.get("stage") or "")
        day = str(body.get("date") or "")
        if body.get("ok"):
            _save_cache(day, stage, body)
        if stage == "OOF_EXACT":
            oof_got.append(body)
        elif stage == "C3_FINAL_TRACE":
            final_got.append(body)
        else:
            print("WARN unknown stage", stage, day, flush=True)

    oof_got.sort(key=lambda x: str(x.get("date") or ""))
    final_got.sort(key=lambda x: str(x.get("date") or ""))
    oof_fail_days = [r for r in oof_got if not r.get("ok")]
    final_fail_days = [r for r in final_got if not r.get("ok")]
    if oof_fail_days or final_fail_days:
        print(
            "STOP exact stream failed",
            [(r.get("date"), r.get("stage"), r.get("blocker")) for r in oof_fail_days + final_fail_days],
            flush=True,
        )
        return _failed_report(
            {
                "A2_PARITY": True,
                "C3_FINAL_PARITY": True,
                "VERDICT": "C3_EXECUTION_AUDIT_FAILED",
                "PRIMARY_CAUSE": "EXACT_STREAM_FAIL",
                "OOF_FAIL_DAYS": [(r.get("date"), r.get("blocker")) for r in oof_fail_days],
                "FINAL_FAIL_DAYS": [(r.get("date"), r.get("blocker")) for r in final_fail_days],
            }
        )

    oof_tr = _merge_traces(oof_got)
    final_tr = _merge_traces(final_got)
    oof_pack = pack_metrics(oof_tr["trades"], days)
    final_trace_pack = pack_metrics(final_tr["trades"], days)
    p3_trace = c3_final_parity(final_trace_pack)
    print(f"OOF_EXACT {abc_line(oof_pack)}", flush=True)
    print(f"C3_FINAL_TRACE {abc_line(final_trace_pack)} parity={p3_trace.get('ok')}", flush=True)
    if not p3_trace.get("ok"):
        return _failed_report(
            {
                "A2_PARITY": True,
                "C3_FINAL_PARITY": False,
                "C3_FINAL_CACHE": abc_line(c3_pack),
                "C3_FINAL_TRACE": abc_line(final_trace_pack),
                "VERDICT": "C3_EXECUTION_AUDIT_FAILED",
                "PRIMARY_CAUSE": "C3_FINAL_TRACE_MISMATCH",
            }
        )

    wf_rows = list(oof_tr.get("would_fill") or []) or list(final_tr.get("would_fill") or [])
    print(f"WOULD_FILL_1S rows={len(wf_rows)} true={sum(1 for r in wf_rows if r.get('WOULD_FILL_1S'))}", flush=True)

    scored_panel = attach_scores(
        panel,
        oof_by=score_map(oof_rows, "c3_score"),
        final_by=score_map(final_scored, "c3_score"),
        wf_by=wf_map(wf_rows),
    )
    all_t = all_target_mean(scored_panel)

    a2_traces, _a2m = _load_a2_traces()
    a2_traces["occupancy"] = reconstruct_a2_occupancy(a2_trades)

    fun_cur = funnel_from_panel(scored_panel, "current_score", traces=a2_traces, all_target=all_t)
    fun_oof = funnel_from_panel(scored_panel, "c3_oof_score", traces=oof_tr, all_target=all_t)
    fun_fin = funnel_from_panel(scored_panel, "c3_final_score", traces=final_tr, all_target=all_t)

    cur3 = topk_block(scored_panel, "current_score", 3, all_target=all_t)
    oof3 = topk_block(scored_panel, "c3_oof_score", 3, all_target=all_t)
    fin3 = topk_block(scored_panel, "c3_final_score", 3, all_target=all_t)

    repl_oof = replacement_block(scored_panel, left_key="current_score", right_key="c3_oof_score", k=3, all_target=all_t)
    repl_fin = replacement_block(scored_panel, left_key="current_score", right_key="c3_final_score", k=3, all_target=all_t)
    repl_oof1 = replacement_block(scored_panel, left_key="current_score", right_key="c3_oof_score", k=1, all_target=all_t)
    repl_oof5 = replacement_block(scored_panel, left_key="current_score", right_key="c3_oof_score", k=5, all_target=all_t)
    repl_fin1 = replacement_block(scored_panel, left_key="current_score", right_key="c3_final_score", k=1, all_target=all_t)
    repl_fin5 = replacement_block(scored_panel, left_key="current_score", right_key="c3_final_score", k=5, all_target=all_t)

    split_cur3 = fillability_split(scored_panel, "current_score", 3, all_target=all_t)
    split_oof3 = fillability_split(scored_panel, "c3_oof_score", 3, all_target=all_t)
    split_fin3 = fillability_split(scored_panel, "c3_final_score", 3, all_target=all_t)
    split_cur1 = fillability_split(scored_panel, "current_score", 1, all_target=all_t)
    split_oof1 = fillability_split(scored_panel, "c3_oof_score", 1, all_target=all_t)
    split_fin1 = fillability_split(scored_panel, "c3_final_score", 1, all_target=all_t)
    split_cur5 = fillability_split(scored_panel, "current_score", 5, all_target=all_t)
    split_oof5 = fillability_split(scored_panel, "c3_oof_score", 5, all_target=all_t)
    split_fin5 = fillability_split(scored_panel, "c3_final_score", 5, all_target=all_t)

    dec_cur = decile_block(scored_panel, "current_score", all_target=all_t)
    dec_oof = decile_block(scored_panel, "c3_oof_score", all_target=all_t)
    dec_fin = decile_block(scored_panel, "c3_final_score", all_target=all_t)

    lin = lineage_lost(a2_trades, final_tr["trades"], c3_traces=final_tr, panel=scored_panel)
    first_c3 = first_entry_decomp(c3_trades, scored_panel, score_key="c3_final_score")
    first_oof = first_entry_decomp(oof_tr["trades"], scored_panel, score_key="c3_oof_score")
    ee = entry_vs_exit(c3_trades, scored_panel)

    a2_occ = occupancy_summary(
        a2_traces["occupancy"],
        fills=len(a2_traces["fills"]),
        expired=len(a2_traces["expired"]),
        admits=len(a2_traces["admits"]),
        slot_release_n=int(a2_traces["slot_release_n"]),
    )
    a2_occ["slot_occupancy_sec"] = slot_occupancy_sec(a2_trades)
    c3_occ = occupancy_summary(
        final_tr["occupancy"],
        fills=len(final_tr["fills"]),
        expired=len(final_tr["expired"]),
        admits=len(final_tr["admits"]),
        slot_release_n=int(final_tr["slot_release_n"]),
    )
    c3_occ["slot_occupancy_sec"] = slot_occupancy_sec(final_tr["trades"])
    oof_occ = occupancy_summary(
        oof_tr["occupancy"],
        fills=len(oof_tr["fills"]),
        expired=len(oof_tr["expired"]),
        admits=len(oof_tr["admits"]),
        slot_release_n=int(oof_tr["slot_release_n"]),
    )
    oof_occ["slot_occupancy_sec"] = slot_occupancy_sec(oof_tr["trades"])

    dec = decide(
        a2_ok=True,
        c3_ok=True,
        oof_pack=oof_pack,
        a2_pack=a2_pack,
        c3_pack=c3_pack,
        current_top3=cur3,
        oof_top3=oof3,
        final_top3=fin3,
        repl_oof=repl_oof,
        repl_final=repl_fin,
        fill_split_oof=split_oof3,
        fill_split_final=split_fin3,
        lineage=lin,
        entry_exit=ee,
        first=first_c3,
    )
    qs = questions(dec, lin, first_c3, ee)

    oof_first_n, oof_first_pnl = _headline_pnl(oof_pack, "first_entry")
    oof_re_n, oof_re_pnl = _headline_pnl(oof_pack, "re_entry")

    required = {
        "A2_PARITY": True,
        "C3_FINAL_PARITY": True,
        "A2": abc_line(a2_pack),
        "C3_FINAL": abc_line(c3_pack),
        "OOF_EXACT_KIND": OOF_EXACT_KIND,
        "OOF_EXACT_TRADES": oof_pack.get("trades"),
        "OOF_EXACT_PNL": oof_pack.get("PnL"),
        "OOF_EXACT_PF": oof_pack.get("PF"),
        "OOF_EXACT_DD": oof_pack.get("maxDD"),
        "OOF_EXACT_FIRST_N": oof_first_n if oof_first_n is not None else first_oof.get("first_n"),
        "OOF_EXACT_FIRST_PNL": oof_first_pnl if oof_first_pnl is not None else first_oof.get("first_PnL"),
        "OOF_EXACT_REENTRY_N": oof_re_n if oof_re_n is not None else first_oof.get("re_n"),
        "OOF_EXACT_REENTRY_PNL": oof_re_pnl if oof_re_pnl is not None else first_oof.get("re_PnL"),
        "CURRENT_TOP3_WOULD_FILL_RATE": cur3.get("WOULD_FILL_1S_RATE"),
        "C3_OOF_TOP3_WOULD_FILL_RATE": oof3.get("WOULD_FILL_1S_RATE"),
        "C3_FINAL_TOP3_WOULD_FILL_RATE": fin3.get("WOULD_FILL_1S_RATE"),
        "CURRENT_ONLY_TOP3_TARGET": (repl_oof.get("CURRENT_ONLY") or {}).get("TARGET_V4_600S"),
        "CURRENT_ONLY_TOP3_FILL_RATE": (repl_oof.get("CURRENT_ONLY") or {}).get("WOULD_FILL_1S_RATE"),
        "C3_ONLY_TOP3_TARGET": (repl_oof.get("C3_ONLY") or {}).get("TARGET_V4_600S"),
        "C3_ONLY_TOP3_FILL_RATE": (repl_oof.get("C3_ONLY") or {}).get("WOULD_FILL_1S_RATE"),
        "CURRENT_FILLABLE_TOP3_TARGET": (split_cur3.get("fillable") or {}).get("TARGET_V4_600S"),
        "C3_OOF_FILLABLE_TOP3_TARGET": (split_oof3.get("fillable") or {}).get("TARGET_V4_600S"),
        "C3_FINAL_FILLABLE_TOP3_TARGET": (split_fin3.get("fillable") or {}).get("TARGET_V4_600S"),
        "CURRENT_FILL_TO_600": cur3.get("FILL_TO_600S_RETURN_MEAN"),
        "C3_OOF_FILL_TO_600": oof3.get("FILL_TO_600S_RETURN_MEAN"),
        "C3_FINAL_FILL_TO_600": fin3.get("FILL_TO_600S_RETURN_MEAN"),
        "UPWARD_TARGET_LOW_FILLABILITY": dec.get("UPWARD_TARGET_LOW_FILLABILITY"),
        "PASSIVE_FILL_ADVERSE_SELECTION": dec.get("PASSIVE_FILL_ADVERSE_SELECTION"),
        "C3_LOST_VS_A2_N": lin.get("C3_LOST_VS_A2_N"),
        "PRIMARY_LOST_TRADE_CAUSE": lin.get("PRIMARY_LOST_TRADE_CAUSE"),
        "ENTRY_OR_EXIT_FAILURE": ee.get("ENTRY_OR_EXIT_FAILURE"),
        "FINAL_SPEC_INSTABILITY": dec.get("FINAL_SPEC_INSTABILITY"),
        "PRIMARY_CAUSE": dec.get("PRIMARY_CAUSE"),
        "RECOMMENDED_NEXT_RESEARCH": dec.get("RECOMMENDED_NEXT_RESEARCH"),
        "VERDICT": dec.get("VERDICT"),
        "C3_FORMAL_VERDICT_MAINTAINED": C3_VERDICT_MAINTAINED,
    }

    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": required,
        "questions": qs,
        "decision": dec,
        "parity": {"A2": p2, "C3_FINAL": p3, "C3_FINAL_TRACE": p3_trace},
        "A2": slim_pack(a2_pack),
        "C3_FINAL": slim_pack(c3_pack),
        "OOF_EXACT": slim_pack(oof_pack),
        "funnel": {"CURRENT": fun_cur, "C3_OOF": fun_oof, "C3_FINAL": fun_fin},
        "topk": {"CURRENT_TOP3": cur3, "C3_OOF_TOP3": oof3, "C3_FINAL_TOP3": fin3},
        "replacement": {
            "OOF_TOP1": repl_oof1,
            "OOF_TOP3": repl_oof,
            "OOF_TOP5": repl_oof5,
            "FINAL_TOP1": repl_fin1,
            "FINAL_TOP3": repl_fin,
            "FINAL_TOP5": repl_fin5,
        },
        "target_fill": {
            "CURRENT_TOP1": split_cur1,
            "CURRENT_TOP3": split_cur3,
            "CURRENT_TOP5": split_cur5,
            "C3_OOF_TOP1": split_oof1,
            "C3_OOF_TOP3": split_oof3,
            "C3_OOF_TOP5": split_oof5,
            "C3_FINAL_TOP1": split_fin1,
            "C3_FINAL_TOP3": split_fin3,
            "C3_FINAL_TOP5": split_fin5,
        },
        "lineage": {k: v for k, v in lin.items() if k != "detail_head"},
        "occupancy": {"A2": a2_occ, "C3_OOF_EXACT": oof_occ, "C3_FINAL": c3_occ},
        "first_entry": {"C3_FINAL": first_c3, "C3_OOF_EXACT": first_oof},
        "entry_vs_exit": ee,
        "ALL_TARGET": all_t,
        "frozen": {
            "C3": C3_VERDICT_MAINTAINED,
            "C2": C2_STATUS_MAINTAINED,
            "B2": B2_FORMAL,
            "REENTRY_V2": REENTRY_V2_FORMAL,
            "C14_CHANGED": C14_CHANGED,
            "RUNTIME_CHANGED": RUNTIME_CHANGED,
            "PAPER_OPERATED": PAPER_OPERATED,
            "OPVAL_OPERATED": OPVAL_OPERATED,
            "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
            "NEW_FEATURE_CREATED": NEW_FEATURE_CREATED,
            "NEW_THRESHOLD_CREATED": NEW_THRESHOLD_CREATED,
            "CLOCK_SEARCHED": CLOCK_SEARCHED,
            "REENTRY_SEARCHED": REENTRY_SEARCHED,
            "PNL_OPTIMIZED": PNL_OPTIMIZED,
            "C4_STARTED": C4_STARTED,
            "C3_IMPLEMENTED": C3_IMPLEMENTED,
            "TRUE_OOS": TRUE_OOS,
            "NEW_FORWARD_N": NEW_FORWARD_N,
            "WAIT_SEC": WAIT_SEC,
            "OOF_EXACT_KIND": OOF_EXACT_KIND,
        },
    }
    report["_markdown"] = build_markdown(report)

    def _flat_repl(name: str, blk: dict) -> dict:
        return {
            "name": name,
            "K": blk.get("K"),
            "BOTH_N": (blk.get("BOTH_SELECTED") or {}).get("N"),
            "CURRENT_ONLY_N": (blk.get("CURRENT_ONLY") or {}).get("N"),
            "C3_ONLY_N": (blk.get("C3_ONLY") or {}).get("N"),
            "CURRENT_ONLY_TARGET": (blk.get("CURRENT_ONLY") or {}).get("TARGET_V4_600S"),
            "CURRENT_ONLY_FILL": (blk.get("CURRENT_ONLY") or {}).get("WOULD_FILL_1S_RATE"),
            "CURRENT_ONLY_F600": (blk.get("CURRENT_ONLY") or {}).get("FILL_TO_600S_RETURN_MEAN"),
            "C3_ONLY_TARGET": (blk.get("C3_ONLY") or {}).get("TARGET_V4_600S"),
            "C3_ONLY_FILL": (blk.get("C3_ONLY") or {}).get("WOULD_FILL_1S_RATE"),
            "C3_ONLY_F600": (blk.get("C3_ONLY") or {}).get("FILL_TO_600S_RETURN_MEAN"),
            "BOTH_TARGET": (blk.get("BOTH_SELECTED") or {}).get("TARGET_V4_600S"),
            "BOTH_FILL": (blk.get("BOTH_SELECTED") or {}).get("WOULD_FILL_1S_RATE"),
        }

    def _flat_split(name: str, blk: dict) -> list[dict]:
        out = []
        for side in ("fillable", "unfillable", "all_topk"):
            rec = {"name": name, "side": side, "K": blk.get("K"), **(blk.get(side) or {})}
            out.append(rec)
        return out

    sheets = {
        "Summary": kv_rows(required),
        "Manifest": kv_rows(
            {
                "ANALYSIS_ID": ANALYSIS_ID,
                "CLOCK": "CURRENT IRREGULAR CLOCK_GRID",
                "CLOCK_GRID": [f"{h:02d}:{m:02d}" for h, m in CLOCK_GRID],
                "Fill": "Corrected Fill SoT is_executable_continuous_board WAIT_SEC=1 fill_price=limit_price",
                "TARGET": "TARGET V4 M4_PERSISTENT 600s",
                "ranking_population": "t0 executable only",
                "OOF_EXACT": OOF_EXACT_KIND,
                "FINAL_SPEC": spec_label(FINAL_SPEC),
                "C3_VERDICT_MAINTAINED": C3_VERDICT_MAINTAINED,
            }
        ),
        "Parity": kv_rows({"A2": p2, "C3_FINAL": p3, "C3_FINAL_TRACE": p3_trace}),
        "OOF_Exact": kv_rows({**slim_pack(oof_pack), "FIRST": first_oof, "kind": OOF_EXACT_KIND}),
        "Funnel": [
            {"side": "CURRENT", **{k: v for k, v in fun_cur.items() if k not in {"TOP1", "TOP3", "TOP5"}}},
            {"side": "C3_OOF", **{k: v for k, v in fun_oof.items() if k not in {"TOP1", "TOP3", "TOP5"}}},
            {"side": "C3_FINAL", **{k: v for k, v in fun_fin.items() if k not in {"TOP1", "TOP3", "TOP5"}}},
        ],
        "Replacement": [
            _flat_repl("OOF_TOP1", repl_oof1),
            _flat_repl("OOF_TOP3", repl_oof),
            _flat_repl("OOF_TOP5", repl_oof5),
            _flat_repl("FINAL_TOP1", repl_fin1),
            _flat_repl("FINAL_TOP3", repl_fin),
            _flat_repl("FINAL_TOP5", repl_fin5),
        ],
        "TargetFill": (
            _flat_split("CURRENT_TOP1", split_cur1)
            + _flat_split("CURRENT_TOP3", split_cur3)
            + _flat_split("CURRENT_TOP5", split_cur5)
            + _flat_split("C3_OOF_TOP1", split_oof1)
            + _flat_split("C3_OOF_TOP3", split_oof3)
            + _flat_split("C3_OOF_TOP5", split_oof5)
            + _flat_split("C3_FINAL_TOP1", split_fin1)
            + _flat_split("C3_FINAL_TOP3", split_fin3)
            + _flat_split("C3_FINAL_TOP5", split_fin5)
        ),
        "FillTo600": [
            {"side": "CURRENT_TOP3", **{k: cur3.get(k) for k in cur3 if k.startswith("FILL") or k in {"N", "WOULD_FILL_1S_RATE", "TARGET_V4_600S"}}},
            {"side": "C3_OOF_TOP3", **{k: oof3.get(k) for k in oof3 if k.startswith("FILL") or k in {"N", "WOULD_FILL_1S_RATE", "TARGET_V4_600S"}}},
            {"side": "C3_FINAL_TOP3", **{k: fin3.get(k) for k in fin3 if k.startswith("FILL") or k in {"N", "WOULD_FILL_1S_RATE", "TARGET_V4_600S"}}},
        ],
        "Adverse": kv_rows(
            {
                "UPWARD_TARGET_LOW_FILLABILITY": dec.get("UPWARD_TARGET_LOW_FILLABILITY"),
                "PASSIVE_FILL_ADVERSE_SELECTION": dec.get("PASSIVE_FILL_ADVERSE_SELECTION"),
                "A_note": "C3 target uplift > CURRENT AND C3 fillability < CURRENT",
                "B_note": "C3 fillable subset FILL_TO_600 <= CURRENT or <= 0",
            }
        ),
        "Deciles": dec_cur + dec_oof + dec_fin,
        "Lineage": kv_rows({k: v for k, v in lin.items() if k != "detail_head"}) + (lin.get("detail_head") or []),
        "Occupancy": [
            {"lane": "A2", **a2_occ},
            {"lane": "C3_OOF_EXACT", **oof_occ},
            {"lane": "C3_FINAL", **c3_occ},
        ],
        "FirstEntry": kv_rows({"C3_FINAL": first_c3, "C3_OOF_EXACT": first_oof}),
        "EntryExit": kv_rows(ee),
        "Questions": kv_rows(qs),
        "Safety": kv_rows(
            {
                "submit_cancel_live": "0/0/0",
                "Paper": 0,
                "OPVAL": 0,
                "C14_CHANGED": C14_CHANGED,
                "RUNTIME_CHANGED": RUNTIME_CHANGED,
                "C4_STARTED": C4_STARTED,
                "C3_IMPLEMENTED": C3_IMPLEMENTED,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "occupancy_approximation": False,
                "WAIT_SEC": WAIT_SEC,
            }
        ),
    }
    write_artifacts(report, sheets)
    print(f"VERDICT={dec.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print("STOP. No C4. C3 not implemented. C14/Runtime unchanged. submit/cancel/live=0/0/0.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
