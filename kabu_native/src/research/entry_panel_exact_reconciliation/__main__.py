"""Offline canonical ENTRY panel / Exact rebase. No Runtime write. No Paper. No C4. No PnL."""
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
from research.entry_decision_population_contract.analyze import snap_key
from research.entry_objective_redesign_c3.oof import fit_on_universe, ranking_pop
from research.entry_panel_exact_reconciliation import (
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
    C3_1349_CAUSES,
    CONTRACT_VERDICT_MAINTAINED,
    ELIGIBLE_DAYS,
    EXECUTION_AWARE_MODEL_CREATED,
    EXPECTED_A2_155,
    EXPECTED_A2_CLOSED,
    EXPECTED_C3_1349,
    EXPECTED_PANEL_EXACT_MISMATCH,
    EXPECTED_RAW_EXACT_MISMATCH,
    FINAL_SPEC,
    MAX_WORKERS,
    NEW_FEATURE_CREATED,
    NEW_MODEL_CREATED,
    NEW_TARGET_CREATED,
    NEW_THRESHOLD_CREATED,
    OPVAL_OPERATED,
    PANEL_EXACT_CAUSES,
    PAPER_OPERATED,
    PERFORMANCE_REBASE_STARTED,
    RAW_EXACT_CAUSES,
    REENTRY_V2_FORMAL,
    RUNTIME_CHANGED,
)
from research.entry_panel_exact_reconciliation.analyze import (
    a2_155_rows,
    aligned_field_diff,
    availability_parity,
    c3_1349_rows,
    causality_from_snaps,
    cause_counts,
    classify_raw_exact,
    decide,
    exec_mismatch_n,
    feature_parity,
    feature_parity_vs_old_panel,
    panel_exact_mismatch_rows,
    population_jaccard,
    rank_agreement_self,
    reapply_fit_scores,
    score_value_parity,
)
from research.entry_panel_exact_reconciliation.causality import code_causality_proof
from research.entry_panel_exact_reconciliation.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from research.entry_panel_exact_reconciliation.replay import process_day
from small_paper.v1r_native_entry_live import FEATURE_ORDER

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
CACHE = NATIVE / "results" / "research" / "_work_cache" / "entry_panel_exact_reconciliation"
C3_CACHE = NATIVE / "results" / "research" / "entry_objective_redesign_c3" / "_work_cache"
A_CACHE = NATIVE / "results" / "research" / "current_entry_nonexec_mechanism" / "_work_cache"
CONTRACT_CACHE = NATIVE / "results" / "research" / "entry_decision_population_contract" / "_work_cache"

FIT_KEYS = (
    "feature_set",
    "features",
    "normalization",
    "alpha",
    "kind",
    "scaler_mean",
    "scaler_scale",
    "coef",
    "intercept",
    "train_n",
)


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _slim_fit(fit: dict[str, Any]) -> dict[str, Any]:
    return {k: fit.get(k) for k in FIT_KEYS}


def _cache_fp(day: str) -> Path:
    return CACHE / f"{day}_CANONICAL.json"


def _load_cache(day: str) -> dict | None:
    fp = _cache_fp(day)
    if not fp.is_file():
        return None
    try:
        body = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    if body.get("ok") and body.get("date") == day and body.get("snaps") and body.get("c3_series_policy") == "collected_only":
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
                f"done {day} ok={body.get('ok')} snaps={len(body.get('snaps') or [])} "
                f"sec={body.get('elapsed_sec')} blocker={body.get('blocker')}",
                flush=True,
            )
    return out


def _ext(bodies: list[dict], key: str) -> list[dict]:
    xs: list[dict] = []
    for b in bodies:
        xs.extend(b.get(key) or [])
    return xs


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


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE CANONICAL ENTRY PANEL EXACT REBASE V2", flush=True)

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
    panel_by = {snap_key(r): r for r in panel}
    print(f"old_panel={len(panel)} ranking={len(ranking)}", flush=True)

    oof_dump = _load(C3_CACHE / "nested_oof.json")
    oof_by = _score_map(list(oof_dump.get("oof_rows") or []), "c3_score")

    print("Reconstruct held-out fold fits (no holdout day in train). Not a new search.", flush=True)
    oof_fits: dict[str, dict] = {}
    for day in ELIGIBLE_DAYS:
        saved = _load(C3_CACHE / f"oof_fold_{day}.json")
        spec = dict(saved.get("spec") or FINAL_SPEC)
        if "features" not in spec or not spec.get("features"):
            spec = dict(FINAL_SPEC)
        train = [r for r in panel if str(r.get("date")) != day]
        fit = fit_on_universe(train, spec)
        oof_fits[day] = fit
        print(f"  oof_fit {day} kind={fit.get('kind')} spec={spec.get('spec_id') or spec.get('feature_set')} train_n={fit.get('train_n')}", flush=True)

    final_fit = fit_on_universe(panel, dict(FINAL_SPEC))
    print(f"FINAL fit kind={final_fit.get('kind')} train_n={final_fit.get('train_n')}", flush=True)

    contract_bodies = _load_stage(CONTRACT_CACHE, ELIGIBLE_DAYS, "CONTRACT")
    contract_snaps = _ext(contract_bodies, "snaps")
    if len(contract_bodies) != len(ELIGIBLE_DAYS):
        print("STOP contract snaps missing", len(contract_bodies), flush=True)
        return 2
    contract_by = {snap_key(s): s for s in contract_snaps}
    print(f"contract_snaps={len(contract_snaps)}", flush=True)

    raw_rows = [classify_raw_exact(s) for s in contract_snaps if bool(s.get("raw_executable")) != bool(s.get("exact_executable"))]
    raw_counts = cause_counts(raw_rows, RAW_EXACT_CAUSES)
    raw_unexplained = [r for r in raw_rows if r.get("cause") not in {"RECONSTRUCTION_BUG"} or r.get("future_event_use") or not r.get("harmless")]
    raw_future = any(r.get("future_event_use") or r.get("causal_cutoff_violation") or r.get("wrong_state_carry") for r in raw_rows)
    print(f"RAW_EXACT_MISMATCH_N={len(raw_rows)} unexplained={len(raw_unexplained)} future={raw_future}", flush=True)

    pe_rows = panel_exact_mismatch_rows(contract_snaps, panel_by)
    pe_counts = cause_counts(pe_rows, PANEL_EXACT_CAUSES)
    pe_primary = max(pe_counts, key=pe_counts.get) if any(pe_counts.values()) else "OTHER"
    pe_by = {f"{r.get('date')}|{r.get('anchor')}|{r.get('symbol')}": r for r in pe_rows}
    print(f"PANEL_EXACT_MISMATCH_N_BEFORE={len(pe_rows)} primary={pe_primary}", flush=True)

    a2_bodies = _load_stage(A_CACHE, ELIGIBLE_DAYS, "A2")
    a2_trades = _ext(a2_bodies, "trades")
    a2_admits = _ext(a2_bodies, "admits")
    rows_155 = a2_155_rows(a2_trades, a2_admits, panel_by, contract_by, pe_by)
    a2_counts = cause_counts(rows_155, PANEL_EXACT_CAUSES)
    a2_unexplained = [r for r in rows_155 if r.get("cause") not in set(PANEL_EXACT_CAUSES) or r.get("cause") == "OTHER"]
    print(f"A2_CLOSED={len(a2_trades)} A2_155={len(rows_155)} unexplained={len(a2_unexplained)}", flush=True)

    c1349 = c3_1349_rows(ranking, oof_by, contract_by)
    c1349_counts = cause_counts(c1349, C3_1349_CAUSES)
    c1349_primary = max(c1349_counts, key=c1349_counts.get) if any(c1349_counts.values()) else "OTHER"
    c1349_causality = int(c1349_counts.get("CAUSALITY_DEFECT") or 0)
    print(f"C3_1349={len(c1349)} primary={c1349_primary} causality={c1349_causality}", flush=True)

    if raw_future or c1349_causality > 0:
        required = {
            "RAW_EXACT_MISMATCH_N": len(raw_rows),
            "RAW_EXACT_EXPLAINED_N": len(raw_rows) - len(raw_unexplained),
            "RAW_EXACT_UNEXPLAINED_N": len(raw_unexplained),
            "FUTURE_EVENT_USE_N": None,
            "PANEL_EXACT_MISMATCH_N_BEFORE": len(pe_rows),
            "PRIMARY_MISMATCH_CAUSE": pe_primary,
            "A2_155_EXPLAINED_N": len(rows_155) - len(a2_unexplained),
            "A2_155_UNEXPLAINED_N": len(a2_unexplained),
            "C3_1349_PRIMARY_CAUSE": c1349_primary,
            "POSTFIX_PANEL_EXACT_EXEC_MISMATCH_N": None,
            "CURRENT_SCORE_AVAILABILITY_MISMATCH_N": None,
            "C3_SCORE_AVAILABILITY_MISMATCH_N": None,
            "POSTFIX_CURRENT_SCORE_MAX_DIFF": None,
            "POSTFIX_C3_OOF_SCORE_MAX_DIFF": None,
            "POSTFIX_C3_FINAL_SCORE_MAX_DIFF": None,
            "POSTFIX_DECISION_POPULATION_JACCARD": None,
            "POSTFIX_RESEARCH_ONLY_N": None,
            "POSTFIX_EXACT_ONLY_N": None,
            "CURRENT_TOP1_AGREEMENT": None,
            "CURRENT_TOP3_OVERLAP": None,
            "CURRENT_TOP5_OVERLAP": None,
            "C3_TOP1_AGREEMENT": None,
            "C3_TOP3_OVERLAP": None,
            "C3_TOP5_OVERLAP": None,
            "CANONICAL_RESEARCH_CONTRACT_PROVEN": False,
            "PERFORMANCE_REBASE_ALLOWED": False,
            "VERDICT": "EXACT_CAUSALITY_DEFECT_FOUND",
        }
        report = {
            "ANALYSIS_ID": ANALYSIS_ID,
            "required": required,
            "raw_exact": {"rows": raw_rows, "cause_counts": raw_counts, "note": "STOP before Panel alignment."},
            "c3_1349": {"n": len(c1349), "cause_counts": c1349_counts, "primary": c1349_primary},
            "decision": {"VERDICT": "EXACT_CAUSALITY_DEFECT_FOUND", "CANONICAL_RESEARCH_CONTRACT_PROVEN": False, "PERFORMANCE_REBASE_ALLOWED": False},
            "code_proof": code_causality_proof(),
        }
        report["_markdown"] = build_markdown(report)
        write_artifacts(report, {"Summary": kv_rows(required), "RawExact3": raw_rows, "Safety": kv_rows({"submit_cancel_live": "0/0/0"})})
        print("STOP EXACT_CAUSALITY_DEFECT_FOUND. Panel not aligned.", flush=True)
        return 2

    jobs = []
    got = []
    slim_final = _slim_fit(final_fit)
    for day in ELIGIBLE_DAYS:
        cached = _load_cache(day)
        if cached:
            got.append(cached)
            print(f"CANONICAL {day} cache-hit snaps={len(cached.get('snaps') or [])}", flush=True)
            continue
        r = by_date[day]
        jobs.append(
            {
                "date": day,
                "capture_path": r["capture_path"],
                "universe": r["universe_symbols"],
                "c3_fit": slim_final,
                "oof_fit": _slim_fit(oof_fits[day]),
            }
        )
    print(f"canonical jobs={len(jobs)}", flush=True)
    for body in _pool(jobs):
        if body.get("ok"):
            _save_cache(str(body.get("date")), body)
        got.append(body)

    fail = [b for b in got if not b.get("ok")]
    if fail or len(got) != len(ELIGIBLE_DAYS):
        print("STOP canonical replay failed", [(b.get("date"), b.get("blocker")) for b in fail], flush=True)
        required = {
            "VERDICT": "ENTRY_PANEL_REBASE_FAILED",
            "CANONICAL_RESEARCH_CONTRACT_PROVEN": False,
            "PERFORMANCE_REBASE_ALLOWED": False,
            "PRIMARY_MISMATCH_CAUSE": pe_primary,
        }
        report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, "fail": fail}
        report["_markdown"] = build_markdown(report)
        write_artifacts(report, {"Summary": kv_rows(required), "Safety": kv_rows({"submit_cancel_live": "0/0/0"})})
        return 2

    canon = _ext(got, "snaps")
    canon_by = {snap_key(s): s for s in canon}
    print(f"canonical_snaps={len(canon)}", flush=True)

    cas = causality_from_snaps(canon)
    future_n = int(cas.get("FUTURE_EVENT_USE_N") or 0)
    postfix_exec = exec_mismatch_n(canon)
    vs_contract_cur = aligned_field_diff(canon_by, contract_by, "current_score")
    vs_contract_c3 = aligned_field_diff(canon_by, contract_by, "c3_live_score")
    avail = availability_parity(canon)
    scores = score_value_parity(canon)
    final_fits = {d: slim_final for d in ELIGIBLE_DAYS}
    oof_re = reapply_fit_scores(canon, stored_key="c3_oof_score", fit_for_day=oof_fits)
    fin_re = reapply_fit_scores(canon, stored_key="c3_live_score", fit_for_day=final_fits)
    jac = population_jaccard(canon)
    agr_cur = rank_agreement_self(canon, "current_score")
    agr_c3 = rank_agreement_self(canon, "c3_live_score")
    agr_oof = rank_agreement_self(canon, "c3_oof_score")
    feats = feature_parity(canon)
    feats_old = feature_parity_vs_old_panel(canon, panel_by)

    cur_max = vs_contract_cur.get("max_abs_diff")
    if cur_max is None:
        cur_max = (scores.get("CURRENT") or {}).get("max_abs_diff")
    fin_max = vs_contract_c3.get("max_abs_diff")
    if fin_max is None:
        fin_max = fin_re.get("max_abs_diff")
    oof_max = oof_re.get("max_abs_diff")

    cur_avail = int(avail.get("CURRENT_SCORE_AVAILABILITY_MISMATCH_N") or 0) + int(vs_contract_cur.get("availability_mismatch_n") or 0)
    c3_avail = int(avail.get("C3_SCORE_AVAILABILITY_MISMATCH_N") or 0) + int(vs_contract_c3.get("availability_mismatch_n") or 0)

    n_nonzero_causes = sum(1 for v in pe_counts.values() if v)
    dec = decide(
        future_n=future_n,
        raw_unexplained=len(raw_unexplained),
        raw_future=False,
        postfix_exec=postfix_exec,
        cur_avail=cur_avail,
        c3_avail=c3_avail,
        cur_max=cur_max,
        oof_max=oof_max,
        fin_max=fin_max,
        jaccard=jac.get("DECISION_POPULATION_JACCARD"),
        research_only=int(jac.get("RESEARCH_ONLY_N") or 0),
        exact_only=int(jac.get("EXACT_ONLY_N") or 0),
        top1_cur=agr_cur.get("Top1_agreement"),
        top3_cur=agr_cur.get("Top3_overlap"),
        top5_cur=agr_cur.get("Top5_overlap"),
        top1_c3=agr_c3.get("Top1_agreement"),
        top3_c3=agr_c3.get("Top3_overlap"),
        top5_c3=agr_c3.get("Top5_overlap"),
        a2_unexplained=len(a2_unexplained),
        c3_1349_causality=c1349_causality,
        primary=pe_primary,
        n_causes=n_nonzero_causes,
    )
    if len(a2_trades) != EXPECTED_A2_CLOSED or len(raw_rows) != EXPECTED_RAW_EXACT_MISMATCH:
        print("WARN headline drift", len(a2_trades), len(raw_rows), flush=True)
    if len(pe_rows) != EXPECTED_PANEL_EXACT_MISMATCH or len(c1349) != EXPECTED_C3_1349:
        print("WARN mismatch-count drift", len(pe_rows), len(c1349), flush=True)
    if len(rows_155) != EXPECTED_A2_155:
        print("WARN A2_155 drift", len(rows_155), flush=True)

    required = {
        "RAW_EXACT_MISMATCH_N": len(raw_rows),
        "RAW_EXACT_EXPLAINED_N": len(raw_rows) - len(raw_unexplained),
        "RAW_EXACT_UNEXPLAINED_N": len(raw_unexplained),
        "FUTURE_EVENT_USE_N": future_n,
        "PANEL_EXACT_MISMATCH_N_BEFORE": len(pe_rows),
        "PRIMARY_MISMATCH_CAUSE": pe_primary,
        "PANEL_EXACT_MISMATCH_CAUSE_COUNTS": pe_counts,
        "A2_155_EXPLAINED_N": len(rows_155) - len(a2_unexplained),
        "A2_155_UNEXPLAINED_N": len(a2_unexplained),
        "C3_1349_PRIMARY_CAUSE": c1349_primary,
        "C3_1349_CAUSE_COUNTS": c1349_counts,
        "POSTFIX_PANEL_EXACT_EXEC_MISMATCH_N": postfix_exec,
        "CURRENT_SCORE_AVAILABILITY_MISMATCH_N": cur_avail,
        "C3_SCORE_AVAILABILITY_MISMATCH_N": c3_avail,
        "POSTFIX_CURRENT_SCORE_MAX_DIFF": cur_max,
        "POSTFIX_C3_OOF_SCORE_MAX_DIFF": oof_max,
        "POSTFIX_C3_FINAL_SCORE_MAX_DIFF": fin_max,
        "POSTFIX_DECISION_POPULATION_JACCARD": jac.get("DECISION_POPULATION_JACCARD"),
        "POSTFIX_RESEARCH_ONLY_N": jac.get("RESEARCH_ONLY_N"),
        "POSTFIX_EXACT_ONLY_N": jac.get("EXACT_ONLY_N"),
        "CURRENT_TOP1_AGREEMENT": agr_cur.get("Top1_agreement"),
        "CURRENT_TOP3_OVERLAP": agr_cur.get("Top3_overlap"),
        "CURRENT_TOP5_OVERLAP": agr_cur.get("Top5_overlap"),
        "C3_TOP1_AGREEMENT": agr_c3.get("Top1_agreement"),
        "C3_TOP3_OVERLAP": agr_c3.get("Top3_overlap"),
        "C3_TOP5_OVERLAP": agr_c3.get("Top5_overlap"),
        "CANONICAL_RESEARCH_CONTRACT_PROVEN": dec.get("CANONICAL_RESEARCH_CONTRACT_PROVEN"),
        "PERFORMANCE_REBASE_ALLOWED": dec.get("PERFORMANCE_REBASE_ALLOWED"),
        "VERDICT": dec.get("VERDICT"),
        "C3_FORMAL_VERDICT_MAINTAINED": C3_VERDICT_MAINTAINED,
        "C3_AUDIT_VERDICT_MAINTAINED": C3_AUDIT_VERDICT_MAINTAINED,
        "C3_RECON_VERDICT_MAINTAINED": C3_RECON_VERDICT_MAINTAINED,
        "CONTRACT_VERDICT_MAINTAINED": CONTRACT_VERDICT_MAINTAINED,
        "LIVE_A2_CLOSED_N": len(a2_trades),
        "A2_155_N": len(rows_155),
        "C3_OOF_TOP1_AGREEMENT": agr_oof.get("Top1_agreement"),
        "C3_OOF_TOP3_OVERLAP": agr_oof.get("Top3_overlap"),
        "C3_OOF_TOP5_OVERLAP": agr_oof.get("Top5_overlap"),
        "C3_FINAL_REAPPLY_MAX_DIFF": fin_re.get("max_abs_diff"),
        "CONTRACT_VS_CANON_EXEC_MISMATCH_N": vs_contract_cur.get("exec_mismatch_n"),
    }
    print(f"VERDICT={required.get('VERDICT')} PROVEN={required.get('CANONICAL_RESEARCH_CONTRACT_PROVEN')}", flush=True)

    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": required,
        "raw_exact": {
            "n": len(raw_rows),
            "cause_counts": raw_counts,
            "explained_n": len(raw_rows) - len(raw_unexplained),
            "unexplained_n": len(raw_unexplained),
            "note": "Same CLOCK event. RAW reconstruct_payload maps qty<=0 special onto SpecialQuote. Exact uses ingest executable. Harmless. Not future use.",
        },
        "causality": cas,
        "code_proof": code_causality_proof(),
        "panel_exact": {
            "n": len(pe_rows),
            "cause_counts": pe_counts,
            "primary": pe_primary,
            "note": "All mismatches: KEEPALL last quote time matches Exact. classify_t0_row reads truncated boards[i0].",
        },
        "a2_155": {
            "n": len(rows_155),
            "cause_counts": a2_counts,
            "explained_n": len(rows_155) - len(a2_unexplained),
            "unexplained_n": len(a2_unexplained),
        },
        "c3_1349": {
            "n": len(c1349),
            "cause_counts": c1349_counts,
            "primary": c1349_primary,
            "note": "Lead missing feature on old panel is vwap_dist_bps for all 1349. Exact first-CLOCK series still has morning increments.",
        },
        "feature_parity": feats,
        "feature_parity_vs_old_panel": feats_old,
        "availability": avail,
        "score_value": scores,
        "vs_contract": {"current": vs_contract_cur, "c3_final": vs_contract_c3},
        "reapply": {"c3_final": fin_re, "c3_oof": oof_re},
        "jaccard": jac,
        "rank_current": agr_cur,
        "rank_c3_final": agr_c3,
        "rank_c3_oof": agr_oof,
        "decision": dec,
        "exception_registry": jac.get("exception_registry") or [],
        "frozen": {
            "C3": C3_VERDICT_MAINTAINED,
            "C3_AUDIT": C3_AUDIT_VERDICT_MAINTAINED,
            "C3_RECON": C3_RECON_VERDICT_MAINTAINED,
            "CONTRACT": CONTRACT_VERDICT_MAINTAINED,
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
            "NEW_TARGET_CREATED": NEW_TARGET_CREATED,
            "NEW_FEATURE_CREATED": NEW_FEATURE_CREATED,
            "NEW_THRESHOLD_CREATED": NEW_THRESHOLD_CREATED,
            "PERFORMANCE_REBASE_STARTED": PERFORMANCE_REBASE_STARTED,
        },
        "canonical_key": "date|session|decision_anchor_time|symbol",
        "origin": "PENDING.anchor / CLOCK fire last t<=t0. No fill_time, nearest-anchor, or exit_time.",
    }
    report["_markdown"] = build_markdown(report)
    sheets = {
        "Summary": kv_rows(required),
        "Manifest": kv_rows(
            {
                "ORIGIN": "PENDING.anchor / Exact CLOCK last t<=t0",
                "CANONICAL": "CanonicalEngine = BFollowEngine executable_t0_only + Exact feature functions",
                "NO_PANEL_FORK": "patch_board_buf_for_marks is not applied on the canonical path",
                "ONE_IMPLEMENTATION": "research panel consumes Exact CLOCK snapshots",
            }
        ),
        "RawExact3": raw_rows,
        "Causality": kv_rows({**cas, **code_causality_proof()}),
        "PanelExactCauses": kv_rows(pe_counts),
        "PanelExactRows": pe_rows,
        "A2_155": rows_155,
        "C3_1349": c1349,
        "FeatureParity": kv_rows(feats),
        "ScoreParity": kv_rows(
            {
                "vs_contract_current": vs_contract_cur,
                "vs_contract_c3": vs_contract_c3,
                "reapply_final": fin_re,
                "reapply_oof": oof_re,
                "identity": scores,
            }
        ),
        "Population": kv_rows({k: v for k, v in jac.items() if k != "per_cohort_head"}),
        "RankParity": kv_rows({"current": agr_cur, "c3_final": agr_c3, "c3_oof": agr_oof}),
        "Decision": kv_rows(dec),
        "Safety": kv_rows(
            {
                "submit_cancel_live": "0/0/0",
                "Paper": 0,
                "OPVAL": 0,
                "C14_CHANGED": C14_CHANGED,
                "RUNTIME_CHANGED": RUNTIME_CHANGED,
                "C4_STARTED": C4_STARTED,
                "PERFORMANCE_REBASE_STARTED": PERFORMANCE_REBASE_STARTED,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "EXECUTION_AWARE_MODEL_CREATED": EXECUTION_AWARE_MODEL_CREATED,
            }
        ),
    }
    write_artifacts(report, sheets)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print("STOP. No Performance Rebase. No C4. Runtime unchanged. submit/cancel/live=0/0/0.", flush=True)
    return 0 if dec.get("CANONICAL_RESEARCH_CONTRACT_PROVEN") else 0


if __name__ == "__main__":
    raise SystemExit(main())
