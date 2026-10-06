"""Offline C3 execution coupling reconciliation. No Runtime write. No Paper. No C4."""
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
from research.c3_execution_reconciliation import (
    ANALYSIS_ID,
    B2_FORMAL,
    C14_CHANGED,
    C14_ID,
    C2_STATUS_MAINTAINED,
    C3_AUDIT_VERDICT_MAINTAINED,
    C3_IMPLEMENTED,
    C3_VERDICT_MAINTAINED,
    C4_STARTED,
    ELIGIBLE_DAYS,
    EXECUTION_AWARE_MODEL_CREATED,
    EXPECTED_EXECUTABLE_T0,
    FINAL_SPEC,
    MAX_WORKERS,
    NEW_MODEL_CREATED,
    OPVAL_OPERATED,
    PAPER_OPERATED,
    REENTRY_V2_FORMAL,
    RUNTIME_CHANGED,
)
from research.c3_execution_reconciliation.analyze import (
    abc_line,
    adverse_on_common,
    attach_would_fill,
    common_scorable,
    coverage_block,
    decide,
    exit_classes,
    fill_identity,
    first_entry_canonical,
    merge_lane,
    original_vs_common_overlap,
    row_key,
    trade_admit_key,
)
from research.c3_execution_reconciliation.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from research.c3_execution_reconciliation.replay import process_day as process_exact
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.entry_objective_redesign_c3.oof import (
    executable_rows,
    fit_on_universe,
    ranking_metrics,
    ranking_pop,
    score_rows,
    spec_label,
)
from research.executable_target_v2_b_threshold.analyze import pack_metrics
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
    _cache_fp(day, stage).write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, default=str),
        encoding="utf-8",
    )


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
            out[row_key(r)] = x
    return out


def _wf_map(rows: list[dict]) -> dict[str, dict]:
    return {row_key(r): r for r in rows}


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE C3 EXECUTION COUPLING RECONCILIATION", flush=True)

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
    if not oof_rows:
        for day in ELIGIBLE_DAYS:
            fold = _load(C3_CACHE / f"oof_fold_{day}.json")
            oof_rows.extend(fold.get("scored") or [])
    oof_by = _score_map(oof_rows, "c3_score")

    print("FINAL spec score on executable rows (not a new search)", flush=True)
    final_fit = fit_on_universe(panel, dict(FINAL_SPEC))
    final_scored = score_rows(executable_rows(panel), final_fit)
    final_by = _score_map(final_scored, "c3_score")

    cov = coverage_block(ranking, oof_by=oof_by)
    print(
        f"C3_SCORE_AVAILABLE={cov.get('C3_SCORE_AVAILABLE_N')} "
        f"MISSING={cov.get('C3_SCORE_MISSING_N')} reasons={cov.get('missing_reason_counts')}",
        flush=True,
    )

    common = common_scorable(ranking, oof_by=oof_by, final_by=final_by)
    print(f"COMMON_SCORABLE_N={len(common)}", flush=True)

    wf_rows: list[dict] = []
    for day in ELIGIBLE_DAYS:
        body = _load(AUDIT_CACHE / f"{day}_OOF_EXACT.json")
        wf_rows.extend(body.get("would_fill") or [])
        if not body.get("would_fill"):
            body2 = _load(AUDIT_CACHE / f"{day}_C3_FINAL_TRACE.json")
            wf_rows.extend(body2.get("would_fill") or [])
    attach_would_fill(common, _wf_map(wf_rows))

    cur_m = ranking_metrics(common, "current_score")
    oof_m = ranking_metrics(common, "c3_oof_score")
    fin_m = ranking_metrics(common, "c3_final_score")
    rank_req = {
        "CURRENT_COMMON_TOP3_UPLIFT": cur_m.get("TOP3_UPLIFT"),
        "C3_OOF_COMMON_TOP3_UPLIFT": oof_m.get("TOP3_UPLIFT"),
        "C3_FINAL_COMMON_TOP3_UPLIFT": fin_m.get("TOP3_UPLIFT"),
        "CURRENT_COMMON_TOP1_UPLIFT": cur_m.get("TOP1_UPLIFT"),
        "C3_OOF_COMMON_TOP1_UPLIFT": oof_m.get("TOP1_UPLIFT"),
        "C3_FINAL_COMMON_TOP1_UPLIFT": fin_m.get("TOP1_UPLIFT"),
        "CURRENT_COMMON_TOP5_UPLIFT": cur_m.get("TOP5_UPLIFT"),
        "C3_OOF_COMMON_TOP5_UPLIFT": oof_m.get("TOP5_UPLIFT"),
        "C3_FINAL_COMMON_TOP5_UPLIFT": fin_m.get("TOP5_UPLIFT"),
        "n_cohorts_used": cur_m.get("n_cohorts_used"),
        "n_cohorts_excluded_lt10": cur_m.get("n_cohorts_excluded_lt10"),
    }
    print(
        f"COMMON TOP3 uplift CURRENT={rank_req['CURRENT_COMMON_TOP3_UPLIFT']} "
        f"OOF={rank_req['C3_OOF_COMMON_TOP3_UPLIFT']} FINAL={rank_req['C3_FINAL_COMMON_TOP3_UPLIFT']}",
        flush=True,
    )
    adv = adverse_on_common(common)

    a2_bodies = _load_stage(A_CACHE, ELIGIBLE_DAYS, "A2")
    oof_bodies = _load_stage(AUDIT_CACHE, ELIGIBLE_DAYS, "OOF_EXACT")
    fin_bodies = _load_stage(AUDIT_CACHE, ELIGIBLE_DAYS, "C3_FINAL_TRACE")
    c3_exact_bodies = _load_stage(C3_CACHE, ELIGIBLE_DAYS, "C3_EXACT")

    def _trades(bodies: list[dict]) -> list[dict]:
        xs = []
        for b in bodies:
            xs.extend(b.get("trades") or [])
        return xs

    a2_trades = _trades(a2_bodies)
    oof_trades = _trades(oof_bodies)
    fin_trades = _trades(c3_exact_bodies) or _trades(fin_bodies)
    days = list(ELIGIBLE_DAYS)
    a2_pack = pack_metrics(a2_trades, days)
    oof_pack = pack_metrics(oof_trades, days) if oof_trades else None
    fin_pack = pack_metrics(fin_trades, days)

    def _id_from_bodies(bodies: list[dict], trades: list[dict], lane: str) -> dict:
        admits, fills, expired = [], [], []
        for b in bodies:
            admits.extend(b.get("admits") or [])
            fills.extend(b.get("fills") or [])
            expired.extend(b.get("expired") or [])
        return fill_identity(
            trades=trades,
            admits=admits,
            harvest_fills=fills,
            expired=expired,
            dual_admit=None,
            dual_exit=None,
            lane=lane,
        )

    id_a2 = _id_from_bodies(a2_bodies, a2_trades, "A2")
    id_oof = _id_from_bodies(oof_bodies, oof_trades, "C3_OOF")
    id_fin = _id_from_bodies(fin_bodies if fin_bodies else c3_exact_bodies, fin_trades, "C3_FINAL")

    ranking_keys = {row_key(r) for r in ranking}
    common_keys = {row_key(r) for r in common}
    panel_by = {row_key(r): r for r in panel}
    ov_a2 = original_vs_common_overlap(
        trades=a2_trades,
        ranking_keys=ranking_keys,
        common_keys=common_keys,
        panel_by=panel_by,
        lane="A2",
    )
    ov_oof = original_vs_common_overlap(
        trades=oof_trades,
        ranking_keys=ranking_keys,
        common_keys=common_keys,
        panel_by=panel_by,
        lane="C3_OOF",
    )
    ov_fin = original_vs_common_overlap(
        trades=fin_trades,
        ranking_keys=ranking_keys,
        common_keys=common_keys,
        panel_by=panel_by,
        lane="C3_FINAL",
    )

    first = first_entry_canonical(fin_trades, panel)
    exits = exit_classes(fin_trades, panel)
    print(
        f"FIRST_N={first.get('FIRST_N')} FILL_TO_600={first.get('FIRST_FILL_TO_600_MEAN')} "
        f"EXIT A/B/C/D={exits.get('C3_EXIT_CLASS_A_N')}/{exits.get('C3_EXIT_CLASS_B_N')}/"
        f"{exits.get('C3_EXIT_CLASS_C_N')}/{exits.get('C3_EXIT_CLASS_D_N')} assert={exits.get('ASSERT_SUM_EQ_53')}",
        flush=True,
    )
    print(
        f"ORIG_VS_COMMON A2 {ov_a2.get('ON_COMMON_SCORABLE_N')}/{ov_a2.get('CLOSED_TRADE_N')} "
        f"C3OOF {ov_oof.get('ON_COMMON_SCORABLE_N')}/{ov_oof.get('CLOSED_TRADE_N')} "
        f"C3FIN {ov_fin.get('ON_COMMON_SCORABLE_N')}/{ov_fin.get('CLOSED_TRADE_N')}",
        flush=True,
    )

    allowed_all = {row_key(r) for r in common}
    oof_lookup = {row_key(r): float(r["c3_oof_score"]) for r in common}
    final_lookup = {row_key(r): float(r["c3_final_score"]) for r in common}

    jobs = []
    got: dict[str, list] = {"A2_COMMON": [], "C3_OOF_COMMON": [], "C3_FINAL_COMMON": []}
    for stage, lookup in (
        ("A2_COMMON", None),
        ("C3_OOF_COMMON", oof_lookup),
        ("C3_FINAL_COMMON", final_lookup),
    ):
        for day in ELIGIBLE_DAYS:
            cached = _load_cache(day, stage)
            if cached:
                got[stage].append(cached)
                print(f"{stage} {day} cache-hit trades={len(cached.get('trades') or [])}", flush=True)
                continue
            r = by_date[day]
            day_keys = [k for k in allowed_all if k.startswith(f"{day}|")]
            job = {
                "date": day,
                "capture_path": r["capture_path"],
                "universe": r["universe_symbols"],
                "allowed_keys": day_keys,
                "stage": stage,
                "engine": "A2" if stage == "A2_COMMON" else "C3",
            }
            if lookup is not None:
                job["score_lookup"] = {k: lookup[k] for k in day_keys if k in lookup}
            jobs.append(job)

    print(f"exact jobs={len(jobs)}", flush=True)
    for body in _pool(jobs):
        st = str(body.get("stage") or "")
        if body.get("ok"):
            _save_cache(str(body.get("date")), st, body)
        if st in got:
            got[st].append(body)

    fail = [b for xs in got.values() for b in xs if not b.get("ok")]
    if fail or any(len(got[k]) != len(ELIGIBLE_DAYS) for k in got):
        print("STOP COMMON exact failed", [(b.get("date"), b.get("stage"), b.get("blocker")) for b in fail], flush=True)
        required = {
            "EXECUTABLE_T0_ROWS": cov.get("EXECUTABLE_T0_ROWS"),
            "C3_SCORE_AVAILABLE_N": cov.get("C3_SCORE_AVAILABLE_N"),
            "C3_SCORE_MISSING_N": cov.get("C3_SCORE_MISSING_N"),
            "COMMON_SCORABLE_N": len(common),
            "VERDICT": "C3_EXECUTION_AUDIT_INTEGRITY_FAILED",
            "PRIMARY_CAUSE_AFTER_RECONCILIATION": "COMMON_EXACT_FAIL",
            "NEXT_RESEARCH": "NONE",
        }
        report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, "coverage": cov, "fail": fail}
        report["_markdown"] = build_markdown(report)
        write_artifacts(report, {"Summary": kv_rows(required), "Safety": kv_rows({"submit_cancel_live": "0/0/0"})})
        return 2

    a2c = merge_lane(got["A2_COMMON"])
    oofc = merge_lane(got["C3_OOF_COMMON"])
    finc = merge_lane(got["C3_FINAL_COMMON"])
    a2c_pack = pack_metrics(a2c["trades"], days)
    oofc_pack = pack_metrics(oofc["trades"], days)
    finc_pack = pack_metrics(finc["trades"], days)
    print(f"A2_COMMON {abc_line(a2c_pack)}", flush=True)
    print(f"C3_OOF_COMMON {abc_line(oofc_pack)}", flush=True)
    print(f"C3_FINAL_COMMON {abc_line(finc_pack)}", flush=True)

    def _keyset(xs: list[dict]) -> set[str]:
        return {trade_admit_key(t) for t in xs}

    overlap_closed = {
        "A2_ORIG_KEYS_ALSO_A2_COMMON": len(_keyset(a2_trades) & _keyset(a2c["trades"])),
        "C3_OOF_ORIG_KEYS_ALSO_OOF_COMMON": len(_keyset(oof_trades) & _keyset(oofc["trades"])),
        "C3_FINAL_ORIG_KEYS_ALSO_FINAL_COMMON": len(_keyset(fin_trades) & _keyset(finc["trades"])),
    }

    id_a2c = fill_identity(
        trades=a2c["trades"],
        admits=a2c["admits"],
        harvest_fills=a2c["fills"],
        expired=a2c["expired"],
        dual_admit=a2c["DUAL_ADMIT_PRIMARY_N"],
        dual_exit=a2c["DUAL_EXIT_PRIMARY_N"],
        lane="A2_COMMON",
    )
    id_oofc = fill_identity(
        trades=oofc["trades"],
        admits=oofc["admits"],
        harvest_fills=oofc["fills"],
        expired=oofc["expired"],
        dual_admit=oofc["DUAL_ADMIT_PRIMARY_N"],
        dual_exit=oofc["DUAL_EXIT_PRIMARY_N"],
        lane="C3_OOF_COMMON",
    )
    id_finc = fill_identity(
        trades=finc["trades"],
        admits=finc["admits"],
        harvest_fills=finc["fills"],
        expired=finc["expired"],
        dual_admit=finc["DUAL_ADMIT_PRIMARY_N"],
        dual_exit=finc["DUAL_EXIT_PRIMARY_N"],
        lane="C3_FINAL_COMMON",
    )

    dec = decide(
        common_n=len(common),
        exec_n=len(ranking),
        ranking=rank_req,
        a2_pack=a2_pack,
        a2_common=a2c_pack,
        c3_oof_common=oofc_pack,
        c3_final_common=finc_pack,
        adverse=adv,
        identities=[id_a2c, id_oofc, id_finc],
        exit_ok=bool(exits.get("ASSERT_SUM_EQ_53")),
    )
    print(f"VERDICT={dec.get('VERDICT')}", flush=True)

    required = {
        "EXECUTABLE_T0_ROWS": cov.get("EXECUTABLE_T0_ROWS") or EXPECTED_EXECUTABLE_T0,
        "C3_SCORE_AVAILABLE_N": cov.get("C3_SCORE_AVAILABLE_N"),
        "C3_SCORE_MISSING_N": cov.get("C3_SCORE_MISSING_N"),
        "COMMON_SCORABLE_N": len(common),
        "CURRENT_COMMON_TOP3_UPLIFT": rank_req.get("CURRENT_COMMON_TOP3_UPLIFT"),
        "C3_OOF_COMMON_TOP3_UPLIFT": rank_req.get("C3_OOF_COMMON_TOP3_UPLIFT"),
        "C3_FINAL_COMMON_TOP3_UPLIFT": rank_req.get("C3_FINAL_COMMON_TOP3_UPLIFT"),
        "A2_COMMON": abc_line(a2c_pack),
        "C3_OOF_COMMON": abc_line(oofc_pack),
        "C3_FINAL_COMMON": abc_line(finc_pack),
        "A2_PORTFOLIO_FILL_N": id_a2.get("PORTFOLIO_FILL_N"),
        "A2_CLOSED_TRADE_N": id_a2.get("CLOSED_TRADE_N"),
        "C3_OOF_PORTFOLIO_FILL_N": id_oof.get("PORTFOLIO_FILL_N"),
        "C3_OOF_CLOSED_TRADE_N": id_oof.get("CLOSED_TRADE_N"),
        "C3_FINAL_PORTFOLIO_FILL_N": id_fin.get("PORTFOLIO_FILL_N"),
        "C3_FINAL_CLOSED_TRADE_N": id_fin.get("CLOSED_TRADE_N"),
        "C3_FIRST_FILL_TO_600_CANONICAL": first.get("FIRST_FILL_TO_600_MEAN"),
        "C3_EXIT_CLASS_A_N": exits.get("C3_EXIT_CLASS_A_N"),
        "C3_EXIT_CLASS_B_N": exits.get("C3_EXIT_CLASS_B_N"),
        "C3_EXIT_CLASS_C_N": exits.get("C3_EXIT_CLASS_C_N"),
        "C3_EXIT_CLASS_D_N": exits.get("C3_EXIT_CLASS_D_N"),
        "PASSIVE_FILL_ADVERSE_SELECTION_CONFIRMED": adv.get("PASSIVE_FILL_ADVERSE_SELECTION_CONFIRMED"),
        "PRIMARY_CAUSE_AFTER_RECONCILIATION": dec.get("PRIMARY_CAUSE_AFTER_RECONCILIATION"),
        "NEXT_RESEARCH": dec.get("NEXT_RESEARCH"),
        "VERDICT": dec.get("VERDICT"),
        "C3_FORMAL_VERDICT_MAINTAINED": C3_VERDICT_MAINTAINED,
        "C3_AUDIT_VERDICT_MAINTAINED": C3_AUDIT_VERDICT_MAINTAINED,
        "FINAL_SPEC": spec_label(FINAL_SPEC),
    }

    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": required,
        "coverage": {k: v for k, v in cov.items() if k != "missing_head"},
        "common_ranking": rank_req,
        "adverse": {k: v for k, v in adv.items() if k not in {"CURRENT_TOP3", "C3_OOF_TOP3", "C3_FINAL_TOP3"}},
        "adverse_topk": {
            "CURRENT": adv.get("CURRENT_TOP3"),
            "C3_OOF": adv.get("C3_OOF_TOP3"),
            "C3_FINAL": adv.get("C3_FINAL_TOP3"),
        },
        "original_exact": {
            "A2": abc_line(a2_pack),
            "C3_OOF": abc_line(oof_pack) if oof_pack else None,
            "C3_FINAL": abc_line(fin_pack),
        },
        "common_exact": {
            "A2_COMMON": abc_line(a2c_pack),
            "C3_OOF_COMMON": abc_line(oofc_pack),
            "C3_FINAL_COMMON": abc_line(finc_pack),
        },
        "fill_identities": {
            "A2": id_a2,
            "C3_OOF": id_oof,
            "C3_FINAL": id_fin,
            "A2_COMMON": id_a2c,
            "C3_OOF_COMMON": id_oofc,
            "C3_FINAL_COMMON": id_finc,
            "definitions": {
                "STANDALONE_WOULD_FILL_1S": "candidate-only Corrected Passive Fill WAIT_SEC=1.0; not a portfolio event",
                "PENDING_CREATED": "V1R_ENTRY_PENDING / a_admits",
                "PORTFOLIO_FILL": "Dual-Lane primary ADMIT (fill into lane). Original caches without dual traces use CLOSED_TRADE as canonical fill-that-closed.",
                "CLOSED_TRADE": "packed completed trade (primary ADMIT + EXIT_EXECUTED)",
                "HARVEST_FILL": "CollectorEngine a_fills; incomplete vs Dual-Lane (explains 53 vs 36, 80 vs 47)",
            },
        },
        "first_entry": first,
        "exit_classes": {k: v for k, v in exits.items() if k != "rows"},
        "original_vs_common": {
            "A2": ov_a2,
            "C3_OOF": ov_oof,
            "C3_FINAL": ov_fin,
            "common_exact_key_overlap_with_original": overlap_closed,
            "count_gap_note": (
                "HARVEST_FILL (CollectorEngine a_fills) is not PORTFOLIO_FILL. "
                "C3 FINAL 53 CLOSED_TRADE vs 36 HARVEST_FILL; C3 OOF 80 vs 47; A2 233 vs 148. "
                "Headline Exact trade count is CLOSED_TRADE_N (packed ADMIT+EXIT_EXECUTED). "
                "STANDALONE_WOULD_FILL_1S is a candidate-only diagnostic and is not a funnel event."
            ),
        },
        "decision": dec,
        "frozen": {
            "C3": C3_VERDICT_MAINTAINED,
            "C3_AUDIT": C3_AUDIT_VERDICT_MAINTAINED,
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
        },
    }
    report["_markdown"] = build_markdown(report)

    sheets = {
        "Summary": kv_rows(required),
        "Manifest": kv_rows(
            {
                "ANALYSIS_ID": ANALYSIS_ID,
                "COMMON_SCORABLE": "t0 executable AND CURRENT score AND C3 OOF score AND C3 FINAL score",
                "C3_COMMON_Exact": "precomputed OOF/FINAL lookup; no live Ridge on subset",
                "A2_COMMON_Exact": "CURRENT live score_fn + executable_t0 + common keys",
            }
        ),
        "Coverage": kv_rows({k: v for k, v in cov.items() if k != "missing_head"})
        + [{"section": "missing_head", **r} for r in (cov.get("missing_head") or [])],
        "CommonRanking": kv_rows(rank_req),
        "CommonExact": [
            {"lane": "A2_COMMON", **{k: a2c_pack.get(k) for k in ("trades", "PnL", "PF", "maxDD")}},
            {"lane": "C3_OOF_COMMON", **{k: oofc_pack.get(k) for k in ("trades", "PnL", "PF", "maxDD")}},
            {"lane": "C3_FINAL_COMMON", **{k: finc_pack.get(k) for k in ("trades", "PnL", "PF", "maxDD")}},
        ],
        "FillIdentities": [id_a2, id_oof, id_fin, id_a2c, id_oofc, id_finc],
        "FirstEntry": kv_rows(first),
        "ExitClasses": kv_rows({k: v for k, v in exits.items() if k != "rows"}) + (exits.get("rows") or []),
        "OriginalVsCommon": [ov_a2, ov_oof, ov_fin, {"lane": "KEY_OVERLAP", **overlap_closed}],
        "Adverse": kv_rows(
            {
                "PASSIVE_FILL_ADVERSE_SELECTION_CONFIRMED": adv.get("PASSIVE_FILL_ADVERSE_SELECTION_CONFIRMED"),
                "confirmed_on_oof": adv.get("confirmed_on_oof"),
                "confirmed_on_final": adv.get("confirmed_on_final"),
                "CURRENT_TOP3": adv.get("CURRENT_TOP3"),
                "C3_OOF_TOP3": adv.get("C3_OOF_TOP3"),
                "C3_FINAL_TOP3": adv.get("C3_FINAL_TOP3"),
            }
        ),
        "Decision": kv_rows(dec),
        "Safety": kv_rows(
            {
                "submit_cancel_live": "0/0/0",
                "Paper": 0,
                "OPVAL": 0,
                "C14_CHANGED": C14_CHANGED,
                "RUNTIME_CHANGED": RUNTIME_CHANGED,
                "C4_STARTED": C4_STARTED,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "EXECUTION_AWARE_MODEL_CREATED": EXECUTION_AWARE_MODEL_CREATED,
            }
        ),
    }
    write_artifacts(report, sheets)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print("STOP. No C4. Runtime unchanged. submit/cancel/live=0/0/0.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
