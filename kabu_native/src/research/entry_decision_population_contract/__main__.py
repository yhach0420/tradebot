"""Offline ENTRY decision population contract audit. No Runtime write. No Paper. No C4."""
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
from research.entry_decision_population_contract import (
    ANALYSIS_ID,
    B2_FORMAL,
    C14_CHANGED,
    C14_ID,
    C2_STATUS_MAINTAINED,
    C3_AUDIT_VERDICT_MAINTAINED,
    C3_IMPLEMENTED,
    C3_RECON_VERDICT_MAINTAINED,
    C3_VERDICT_MAINTAINED,
    C4_STARTED,
    ELIGIBLE_DAYS,
    EXECUTION_AWARE_MODEL_CREATED,
    EXPECTED_A2_CLOSED,
    EXPECTED_C3_FINAL_CLOSED,
    EXPECTED_C3_OOF_CLOSED,
    EXPECTED_EXECUTABLE_T0,
    FINAL_SPEC,
    FIX_IMPLEMENTED,
    MAX_WORKERS,
    NEW_MODEL_CREATED,
    OPVAL_OPERATED,
    PAPER_OPERATED,
    REENTRY_V2_FORMAL,
    RUNTIME_CHANGED,
)
from research.entry_decision_population_contract.analyze import (
    build_indexes,
    c3_missing_handling,
    coverage_by_anchor,
    current_score_parity,
    decide,
    decision_jaccard,
    lane_origins,
    rank_agreement,
    reclassify_155,
    snap_three_way,
)
from research.entry_decision_population_contract.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from research.entry_decision_population_contract.replay import process_day
from research.entry_objective_redesign_c3.oof import (
    executable_rows,
    fit_on_universe,
    ranking_pop,
    score_rows,
)
from small_paper.v1r_native_entry_live import FEATURE_ORDER

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
AUDIT_CACHE = NATIVE / "results" / "research" / "c3_execution_coupling_audit" / "_work_cache"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _cache_fp(day: str) -> Path:
    return CACHE / f"{day}_CONTRACT.json"


def _load_cache(day: str) -> dict | None:
    fp = _cache_fp(day)
    if not fp.is_file():
        return None
    try:
        body = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    if body.get("ok") and body.get("date") == day and body.get("snaps"):
        return body
    return None


def _save_cache(day: str, body: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    _cache_fp(day).write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _pool(jobs: list[dict]) -> list[dict]:
    if not jobs:
        return []
    out = []
    workers = min(MAX_WORKERS, len(jobs))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(process_day, job): job["date"] for job in jobs}
        for fut in as_completed(futs):
            day = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, "date": day, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            print(
                f"done {day} ok={body.get('ok')} trades={len(body.get('trades') or [])} "
                f"snaps={len(body.get('snaps') or [])} sec={body.get('elapsed_sec')} "
                f"blocker={body.get('blocker')}",
                flush=True,
            )
    return out


def _load_stage(cache_dir: Path, days: tuple[str, ...], stage: str) -> list[dict]:
    bodies = []
    for day in days:
        fp = cache_dir / f"{day}_{stage}.json"
        if not fp.is_file():
            continue
        body = json.loads(fp.read_text(encoding="utf-8"))
        if body.get("ok"):
            bodies.append(body)
    return bodies


def _score_map(rows: list[dict], key: str) -> dict[str, float]:
    from research.entry_decision_population_contract.analyze import snap_key

    out: dict[str, float] = {}
    for r in rows:
        v = r.get(key)
        try:
            if v is None:
                continue
            x = float(v)
        except (TypeError, ValueError):
            continue
        if x == x:
            out[snap_key(r)] = x
    return out


def _ext(bodies: list[dict], key: str) -> list[dict]:
    xs: list[dict] = []
    for b in bodies:
        xs.extend(b.get(key) or [])
    return xs


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE ENTRY DECISION POPULATION CONTRACT V2", flush=True)

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
        body = _load(C3_CACHE / f"{day}_C3_PANEL.json")
        if not body.get("ok"):
            print("STOP panel missing", day, flush=True)
            return 2
        panel.extend(body.get("rows") or [])
    ranking = ranking_pop(panel)
    print(f"EXECUTABLE_T0_ROWS={len(ranking)} panel={len(panel)}", flush=True)

    oof_dump = _load(C3_CACHE / "nested_oof.json")
    oof_rows = list(oof_dump.get("oof_rows") or [])
    oof_by = _score_map(oof_rows, "c3_score")
    print("FINAL spec score on executable rows (not a new search)", flush=True)
    final_fit = fit_on_universe(panel, dict(FINAL_SPEC))
    final_scored = score_rows(executable_rows(panel), final_fit)
    final_by = _score_map(final_scored, "c3_score")

    a2_bodies = _load_stage(A_CACHE, ELIGIBLE_DAYS, "A2")
    oof_bodies = _load_stage(AUDIT_CACHE, ELIGIBLE_DAYS, "OOF_EXACT")
    fin_trace = _load_stage(AUDIT_CACHE, ELIGIBLE_DAYS, "C3_FINAL_TRACE")
    fin_exact = _load_stage(C3_CACHE, ELIGIBLE_DAYS, "C3_EXACT")
    a2_trades = _ext(a2_bodies, "trades")
    a2_admits = _ext(a2_bodies, "admits")
    oof_trades = _ext(oof_bodies, "trades")
    oof_admits = _ext(oof_bodies, "admits")
    fin_trades = _ext(fin_exact, "trades") or _ext(fin_trace, "trades")
    fin_admits = _ext(fin_exact, "admits") or _ext(fin_trace, "admits")
    print(
        f"frozen CLOSED A2={len(a2_trades)} C3OOF={len(oof_trades)} C3FIN={len(fin_trades)}",
        flush=True,
    )

    jobs = []
    got = []
    for day in ELIGIBLE_DAYS:
        cached = _load_cache(day)
        if cached:
            got.append(cached)
            print(f"CONTRACT {day} cache-hit snaps={len(cached.get('snaps') or [])}", flush=True)
            continue
        r = by_date[day]
        jobs.append(
            {
                "date": day,
                "capture_path": r["capture_path"],
                "universe": r["universe_symbols"],
                "c3_fit": final_fit,
            }
        )
    print(f"contract jobs={len(jobs)}", flush=True)
    for body in _pool(jobs):
        if body.get("ok"):
            _save_cache(str(body.get("date")), body)
        got.append(body)

    fail = [b for b in got if not b.get("ok")]
    if fail or len(got) != len(ELIGIBLE_DAYS):
        print("STOP contract replay failed", [(b.get("date"), b.get("blocker")) for b in fail], flush=True)
        required = {
            "VERDICT": "ENTRY_DECISION_CONTRACT_AUDIT_FAILED",
            "PRIMARY_CAUSE": "CONTRACT_REPLAY_FAIL",
            "EXECUTION_AWARE_OBJECTIVE_ALLOWED": False,
        }
        report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, "fail": fail}
        report["_markdown"] = build_markdown(report)
        write_artifacts(report, {"Summary": kv_rows(required), "Safety": kv_rows({"submit_cancel_live": "0/0/0"})})
        return 2

    snaps = _ext(got, "snaps")
    live_a2_trades = _ext(got, "trades")
    print(f"snaps={len(snaps)} live_A2_CLOSED={len(live_a2_trades)}", flush=True)

    idx = build_indexes(panel, snaps, oof_by, final_by)
    a2 = lane_origins(a2_trades, a2_admits, idx, lane="A2")
    c3o = lane_origins(oof_trades, oof_admits, idx, lane="C3_OOF")
    c3f = lane_origins(fin_trades, fin_admits, idx, lane="C3_FINAL")
    c155 = reclassify_155(a2, idx)
    tw = snap_three_way(snaps, idx["panel_by"])
    cur_par = current_score_parity(snaps, idx["panel_by"])
    jac_exec = decision_jaccard(snaps, panel, research="executable", exact_pred="exact_executable")
    jac_a2 = decision_jaccard(snaps, panel, research="executable", exact_pred="exact_in_admit_pool")
    jac_rank = decision_jaccard(snaps, panel, research="ranking", exact_pred="exact_in_admit_pool")
    jac_c3 = decision_jaccard(snaps, panel, research="ranking", exact_pred="c3_scored")
    cov = coverage_by_anchor(snaps)
    miss = c3_missing_handling(ranking, oof_by, idx["snap_by"])
    agr_cur = rank_agreement(snaps, panel, score_a="current_score", score_b="current_score")
    agr_c3 = rank_agreement(snaps, final_scored, score_a="c3_live_score", score_b="c3_score")

    origin_complete = a2["ORIGIN_MISSING_N"] == 0 and c3o["ORIGIN_MISSING_N"] == 0 and c3f["ORIGIN_MISSING_N"] == 0
    integrity = (
        len(a2_trades) == EXPECTED_A2_CLOSED
        and len(oof_trades) == EXPECTED_C3_OOF_CLOSED
        and len(fin_trades) == EXPECTED_C3_FINAL_CLOSED
        and len(ranking) == EXPECTED_EXECUTABLE_T0
    )
    dec = decide(
        a2=a2,
        c3o=c3o,
        c3f=c3f,
        c155=c155,
        jac_exec=jac_exec,
        jac_rank=jac_rank,
        jac_a2=jac_a2,
        tw=tw,
        cur_par=cur_par,
        origin_complete=origin_complete and integrity,
    )
    if not integrity:
        dec = {
            "VERDICT": "ENTRY_DECISION_CONTRACT_AUDIT_FAILED",
            "PRIMARY_CAUSE": "HEADLINE_COUNT_DRIFT",
            "EXACT_CONTRACT_MISMATCH": True,
            "DECISION_POPULATION_MATCH": False,
            "EXECUTION_AWARE_OBJECTIVE_ALLOWED": False,
        }

    q1 = (
        "NO. Pending-origin clock matches fill-clock for the 155; panel executable is still false. "
        f"Primary cause={c155.get('A2_155_PRIMARY_CAUSE')}."
    )
    if c155.get("A2_155_PRIMARY_CAUSE") == "FILL_TIME_JOIN_ARTIFACT":
        q1 = "YES. Origin decision is ranking-eligible; fill-time join was the false negative."
    q2 = (
        "A2 Exact filters admit_events by executable_at_t0 (BFollowEngine.executable_t0_only). "
        f"A2_EXACT_EXEC_FALSE_N={a2.get('EXACT_EXEC_FALSE_N')} among origin-found CLOSED."
    )
    q3 = (
        "Both use classify_t0_row ← ingest is_executable_continuous_board flag. "
        f"PANEL_vs_EXACT mismatch snaps={tw.get('PANEL_vs_EXACT_MISMATCH_N')} "
        f"A2 closed={a2.get('PANEL_vs_EXACT_MISMATCH_N')}."
    )
    handling = miss.get("C3_MISSING_SCORE_HANDLING_COUNTS") or {}
    q4 = (
        f"Not all excluded. Of 1787 panel-OOF-missing ranking rows: live C3 score present "
        f"{miss.get('LIVE_C3_SCORE_WHEN_PANEL_OOF_MISSING_N')} (counted OTHER), "
        f"handling={handling}. Code path has no CURRENT fallback."
    )
    q5 = (
        f"C3 OOF origin found {c3o.get('ORIGIN_FOUND_N')}/{c3o.get('CLOSED_N')}, "
        f"Exact used_score {c3o.get('USED_SCORE_N')}, panel OOF at origin {c3o.get('PANEL_C3_OOF_AT_ORIGIN_N')}. "
        f"C3 FINAL origin {c3f.get('ORIGIN_FOUND_N')}/{c3f.get('CLOSED_N')}, "
        f"used_score {c3f.get('USED_SCORE_N')}, panel FINAL at origin {c3f.get('PANEL_C3_FINAL_AT_ORIGIN_N')}."
    )
    q6 = (
        f"Jaccard executable-vs-exact_exec={jac_exec.get('DECISION_POPULATION_JACCARD')} "
        f"research_only={jac_exec.get('RESEARCH_ONLY_N')} exact_only={jac_exec.get('EXACT_ONLY_N')}. "
        f"ranking_pop vs A2 admit pool Jaccard={jac_rank.get('DECISION_POPULATION_JACCARD')} "
        f"research_only={jac_rank.get('RESEARCH_ONLY_N')} exact_only={jac_rank.get('EXACT_ONLY_N')}."
    )
    q7 = "NO. EXECUTION_AWARE_OBJECTIVE_ALLOWED is false." if not dec.get("EXECUTION_AWARE_OBJECTIVE_ALLOWED") else "YES under CASE A/B only. Not starting C4."

    questions = {"Q1": q1, "Q2": q2, "Q3": q3, "Q4": q4, "Q5": q5, "Q6": q6, "Q7": q7}

    required = {
        "A2_CLOSED_N": a2.get("CLOSED_N"),
        "A2_ORIGIN_FOUND_N": a2.get("ORIGIN_FOUND_N"),
        "A2_ORIGIN_MISSING_N": a2.get("ORIGIN_MISSING_N"),
        "A2_EXACT_EXEC_TRUE_N": a2.get("EXACT_EXEC_TRUE_N"),
        "A2_EXACT_EXEC_FALSE_N": a2.get("EXACT_EXEC_FALSE_N"),
        "A2_RAW_PANEL_EXEC_MISMATCH_N": a2.get("RAW_vs_PANEL_MISMATCH_N"),
        "A2_RAW_EXACT_EXEC_MISMATCH_N": a2.get("RAW_vs_EXACT_MISMATCH_N"),
        "A2_155_PRIMARY_CAUSE": c155.get("A2_155_PRIMARY_CAUSE"),
        "A2_EXEC_TRUE_THEN_NONEXEC_AT_FILL_N": a2.get("EXEC_TRUE_THEN_NONEXEC_AT_FILL_N"),
        "C3_OOF_CLOSED_N": c3o.get("CLOSED_N"),
        "C3_OOF_ORIGIN_FOUND_N": c3o.get("ORIGIN_FOUND_N"),
        "C3_OOF_SCORE_PRESENT_AT_ORIGIN_N": c3o.get("PANEL_C3_OOF_AT_ORIGIN_N"),
        "C3_OOF_SCORE_MISSING_AT_ORIGIN_N": int(c3o.get("CLOSED_N") or 0) - int(c3o.get("PANEL_C3_OOF_AT_ORIGIN_N") or 0),
        "C3_FINAL_CLOSED_N": c3f.get("CLOSED_N"),
        "C3_FINAL_ORIGIN_FOUND_N": c3f.get("ORIGIN_FOUND_N"),
        "C3_FINAL_SCORE_PRESENT_AT_ORIGIN_N": c3f.get("PANEL_C3_FINAL_AT_ORIGIN_N"),
        "C3_FINAL_SCORE_MISSING_AT_ORIGIN_N": int(c3f.get("CLOSED_N") or 0) - int(c3f.get("PANEL_C3_FINAL_AT_ORIGIN_N") or 0),
        "C3_SCORE_MISSING_RUNTIME_HANDLING": miss.get("C3_MISSING_SCORE_HANDLING_COUNTS"),
        "CURRENT_SCORE_MAX_ABS_DIFF": cur_par.get("max_abs_diff"),
        "C3_OOF_SCORE_MAX_ABS_DIFF": (c3o.get("used_vs_oof") or {}).get("max_abs_diff"),
        "C3_FINAL_SCORE_MAX_ABS_DIFF": (c3f.get("used_vs_final") or {}).get("max_abs_diff"),
        "DECISION_POPULATION_JACCARD": jac_exec.get("DECISION_POPULATION_JACCARD"),
        "RESEARCH_ONLY_N": jac_exec.get("RESEARCH_ONLY_N"),
        "EXACT_ONLY_N": jac_exec.get("EXACT_ONLY_N"),
        "EXACT_CONTRACT_MISMATCH": dec.get("EXACT_CONTRACT_MISMATCH"),
        "DECISION_POPULATION_MATCH": dec.get("DECISION_POPULATION_MATCH"),
        "EXECUTION_AWARE_OBJECTIVE_ALLOWED": dec.get("EXECUTION_AWARE_OBJECTIVE_ALLOWED"),
        "PRIMARY_CAUSE": dec.get("PRIMARY_CAUSE"),
        "VERDICT": dec.get("VERDICT"),
        "LIVE_A2_CLOSED_N": len(live_a2_trades),
        "C3_FORMAL_VERDICT_MAINTAINED": C3_VERDICT_MAINTAINED,
        "C3_AUDIT_VERDICT_MAINTAINED": C3_AUDIT_VERDICT_MAINTAINED,
        "C3_RECON_VERDICT_MAINTAINED": C3_RECON_VERDICT_MAINTAINED,
    }
    print(f"VERDICT={required.get('VERDICT')}", flush=True)

    def _slim_lane(d: dict) -> dict:
        return {k: v for k, v in d.items() if k != "rows"}

    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": required,
        "questions": questions,
        "a2": _slim_lane(a2),
        "c3_oof": _slim_lane(c3o),
        "c3_final": _slim_lane(c3f),
        "a2_155": c155,
        "three_way_snaps": tw,
        "current_score_parity": cur_par,
        "c3_missing": miss,
        "coverage_by_anchor": cov,
        "jaccard": {"executable": jac_exec, "a2_admit_pool": jac_a2, "ranking_vs_a2": jac_rank, "ranking_vs_c3": jac_c3},
        "rank_agreement_current": agr_cur,
        "rank_agreement_c3": agr_c3,
        "decision": dec,
        "fill_definitions": {
            "STANDALONE_WOULD_FILL_1S": "candidate-only diagnostic",
            "PENDING_CREATED": "ENTRY_PENDING / a_admits",
            "PORTFOLIO_FILL": "Dual-Lane primary ADMIT",
            "CLOSED_TRADE": "completed primary trade",
            "HARVEST_FILL": "CollectorEngine a_fills",
        },
        "frozen": {
            "C3": C3_VERDICT_MAINTAINED,
            "C3_AUDIT": C3_AUDIT_VERDICT_MAINTAINED,
            "C3_RECON": C3_RECON_VERDICT_MAINTAINED,
            "C2": C2_STATUS_MAINTAINED,
            "B2": B2_FORMAL,
            "REENTRY_V2": REENTRY_V2_FORMAL,
            "C14_CHANGED": C14_CHANGED,
            "RUNTIME_CHANGED": RUNTIME_CHANGED,
            "PAPER_OPERATED": PAPER_OPERATED,
            "OPVAL_OPERATED": OPVAL_OPERATED,
            "C4_STARTED": C4_STARTED,
            "C3_IMPLEMENTED": C3_IMPLEMENTED,
            "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
            "EXECUTION_AWARE_MODEL_CREATED": EXECUTION_AWARE_MODEL_CREATED,
            "FIX_IMPLEMENTED": FIX_IMPLEMENTED,
        },
    }
    report["_markdown"] = build_markdown(report)
    sheets = {
        "Summary": kv_rows(required),
        "Manifest": kv_rows(
            {
                "ORIGIN": "PENDING.anchor WAIT_SEC window vs fill_time. nearest-anchor forbidden.",
                "EXACT_A2": "BFollowEngine executable_t0_only=True",
                "EXACT_C3": "C3LookupEngine: skip non-exec; skip non-finite Ridge",
                "RAW": "is_executable_continuous_board on ingest-stored board row fields",
            }
        ),
        "A2Origins": a2.get("rows") or [],
        "C3Origins": (c3o.get("rows") or []) + (c3f.get("rows") or []),
        "Causes155": [c155],
        "ThreeWay": kv_rows(tw),
        "Missing1787": kv_rows({k: v for k, v in miss.items()}),
        "CoverageByAnchor": [{"anchor": k, **v} for k, v in cov.items()],
        "ScoreParity": kv_rows(
            {
                "current": cur_par,
                "c3_oof_used_vs_panel": c3o.get("used_vs_oof"),
                "c3_final_used_vs_panel": c3f.get("used_vs_final"),
                "rank_current": agr_cur,
                "rank_c3": agr_c3,
            }
        ),
        "Jaccard": [
            {"name": "executable_vs_exact_exec", **{k: v for k, v in jac_exec.items() if k != "per_cohort_head"}},
            {"name": "executable_vs_a2_admit", **{k: v for k, v in jac_a2.items() if k != "per_cohort_head"}},
            {"name": "ranking_vs_a2_admit", **{k: v for k, v in jac_rank.items() if k != "per_cohort_head"}},
            {"name": "ranking_vs_c3_scored", **{k: v for k, v in jac_c3.items() if k != "per_cohort_head"}},
        ],
        "QA": kv_rows(questions),
        "Decision": kv_rows(dec),
        "Safety": kv_rows(
            {
                "submit_cancel_live": "0/0/0",
                "Paper": 0,
                "OPVAL": 0,
                "C14_CHANGED": C14_CHANGED,
                "RUNTIME_CHANGED": RUNTIME_CHANGED,
                "C4_STARTED": C4_STARTED,
                "FIX_IMPLEMENTED": FIX_IMPLEMENTED,
                "EXECUTION_AWARE_MODEL_CREATED": EXECUTION_AWARE_MODEL_CREATED,
            }
        ),
    }
    write_artifacts(report, sheets)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print("STOP. No C4. No fix. Runtime unchanged. submit/cancel/live=0/0/0.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
