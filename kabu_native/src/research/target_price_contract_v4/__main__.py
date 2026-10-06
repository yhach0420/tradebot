"""Offline TARGET PRICE CONTRACT V4. No Runtime write. No C rebuild. No model search."""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_timing_robustness.inventory import build_inventory
from research.target_price_contract_v4 import (
    ANALYSIS_ID,
    B0_STATUS,
    B1_STATUS,
    C14_ID,
    C_REBUILD_THIS_RUN,
    MAX_WORKERS,
    PER_FEATURE_THRESHOLD,
    WAIT_SEC,
)
from research.target_price_contract_v4.analyze import (
    aggregate_persist,
    bias_material,
    by_anchor_m4,
    by_day_m4,
    by_symbol_m4,
    cohort_stats_m4,
    compare_table,
    first_fail_table_m4,
    integrity_counts,
    label_stability_m1_5_vs_m4,
    mark_age_dist,
    missingness_m4,
    persistence_decision,
    plus600_calendar,
    row_primary_m4,
    session_carry_from_ages,
    structural_residual,
    verdict_pack,
    waterfall_m4,
)
from research.target_price_contract_v4.extract import process_day
from research.target_price_contract_v4.inventory import FRESHNESS_INVENTORY
from research.target_price_contract_v4.publish import (
    OUT,
    build_markdown,
    flatten_smd,
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
CACHE = OUT / "_work_cache"

CODE_SEMANTICS = [
    {
        "claim": "A_last_observed_state_remains_current",
        "file": "src/small_paper/v1r_native_entry_live.py",
        "function": "ingest_push",
        "evidence": "Appends the PUSH row to boards[sym] / _BoardBuf. No timer zeros the last row.",
        "supports": "A",
    },
    {
        "claim": "A_snapshot_is_last_event_le_t0",
        "file": "src/small_paper/v1r_native_entry_live.py",
        "function": "_run_anchor",
        "evidence": "searchsorted right-1; snapshot_age_ms = t0 - last_t; book fields copied from that row.",
        "supports": "A",
    },
    {
        "claim": "PUSH_is_on_update_not_5s_heartbeat",
        "file": "src/small_paper/v1r_native_entry_live.py",
        "function": "ingest_push comment",
        "evidence": "Full-PUSH ingest is denser than the old 5s eval cadence.",
        "supports": "A",
    },
    {
        "claim": "fresh_sec_is_same_payload_quote_clock",
        "file": "src/small_paper/v1r_native_entry_live.py",
        "function": "extract_board_row",
        "evidence": "fresh_sec = event_t - CurrentPriceTime/AskTime/BidTime of THIS event. Not inter-PUSH TTL.",
        "supports": "not_B",
    },
    {
        "claim": "BOARD_FRESHNESS_5s_is_execution_safety",
        "file": "src/small_paper/v1r_primary_runtime.py",
        "function": "BOARD_FRESHNESS_SEC_V1R",
        "evidence": "Frozen V1R fill/exit freshness. YAML 3.0 is PBv2 shadow-only.",
        "supports": "EXECUTION_SAFETY_not_HISTORICAL_MARK",
    },
    {
        "claim": "V2_V3_copied_5s_into_historical_label",
        "file": "src/research/executable_target_v2_b_threshold/contract.py",
        "function": "last_executable_mid",
        "evidence": "break if (t_at-event_t)>5. That copy is what V4 tests and must not inherit for M4.",
        "supports": "HISTORICAL_PRICE_MARK_misuse_of_execution_5s",
    },
    {
        "claim": "compact_tail_is_memory_not_TTL",
        "file": "src/small_paper/v1r_native_entry_live.py",
        "function": "compact_tail / boards trim 25000",
        "evidence": "Drops old ticks in live memory. KEEPALL research extract no-ops compact_tail.",
        "supports": "LIVE_DATA_HEALTH",
    },
    {
        "claim": "global_seq_holes_are_not_per_symbol_gaps",
        "file": "src/small_paper/v1r_native_entry_live.py",
        "function": "native_ingest_sequence_holes",
        "evidence": "Accepted-universe global seq. A jump can be other symbols. V4 does not treat +1 skips as CAPTURE_GAP.",
        "supports": "CAPTURE_GAP_definition",
    },
]


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _cache_fp(day: str) -> Path:
    return CACHE / f"{day}_TARGET_V4.json"


def _load_cache(day: str) -> dict | None:
    fp = _cache_fp(day)
    if not fp.is_file():
        return None
    try:
        body = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    if body.get("ok") and body.get("date") == day and body.get("stage") == "TARGET_V4":
        return body
    return None


def _save_cache(day: str, body: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    _cache_fp(day).write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")


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
                f"extract {day} ok={body.get('ok')} rows={len(body.get('rows') or [])} "
                f"sec={body.get('elapsed_sec')} blocker={body.get('blocker')}",
                flush=True,
            )
    return out


def main() -> int:
    os.environ["PYTHONPATH"] = f"{SRC};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE.parent}"
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE TARGET V4 ONLY", flush=True)
    print("C14/Runtime/CLOCK/ENTRY/EXIT forbidden. C model search forbidden. B forbidden.", flush=True)
    print("Execution freshness forbidden to change. Age>60s search forbidden.", flush=True)

    if list(FEATURE_ORDER) != [
        "spread_bps", "imbalance", "mid_ret_60s", "mid_ret_180s", "event_rate_60s", "log_bid_qty"
    ]:
        print("STOP FEATURE_ORDER drift", flush=True)
        return 2
    c14 = _load(C14)
    if str(c14.get("candidate_id") or "") != C14_ID:
        print("STOP: C14 identity mismatch", flush=True)
        return 2

    inv = build_inventory()
    elig = [r for r in inv if r.get("replay_eligible") and r.get("universe_symbols") and r.get("capture_path")]
    days = [r["date"] for r in elig]
    print(f"eligible days={len(elig)} WAIT_SEC={WAIT_SEC} ANALYSIS_ID={ANALYSIS_ID}", flush=True)

    jobs = []
    extracted = []
    for r in elig:
        cached = _load_cache(r["date"])
        if cached:
            extracted.append(cached)
            print(f"extract {r['date']} cache-hit rows={len(cached.get('rows') or [])}", flush=True)
        else:
            jobs.append({"date": r["date"], "capture_path": r["capture_path"], "universe": r["universe_symbols"]})
    for body in _pool(jobs):
        extracted.append(body)
        if body.get("ok"):
            _save_cache(str(body.get("date")), body)
    extracted.sort(key=lambda x: str(x.get("date") or ""))
    failed = [r for r in extracted if not r.get("ok")]
    if failed:
        print("STOP extract failed", [(r.get("date"), r.get("blocker")) for r in failed], flush=True)
        return 2

    rows: list[dict] = []
    for body in extracted:
        rows.extend(body.get("rows") or [])
    print(f"audit_rows={len(rows)}", flush=True)

    persist = aggregate_persist(extracted)
    print(
        f"persist same_session_intervals={persist.get('n_same_session_intervals')} "
        f"share_gt5={persist.get('share_gt5s')} next_still={persist.get('next_gt5_still_valid_continuous_rate')}",
        flush=True,
    )
    miss = missingness_m4(rows)
    coh = cohort_stats_m4(rows)
    prim = sum(1 for r in rows if row_primary_m4(r))
    integ = integrity_counts(rows)
    carry = session_carry_from_ages(rows)
    persist_dec = persistence_decision(persist=persist, carry=carry, integ=integ)
    material = bias_material(miss)
    wf = waterfall_m4(rows)
    ff = first_fail_table_m4(rows)
    anc = by_anchor_m4(rows)
    residual = structural_residual(anc=anc, miss=miss)
    day_t = by_day_m4(rows)
    sym = by_symbol_m4(rows)
    stab = label_stability_m1_5_vs_m4(rows)
    age0 = mark_age_dist(rows, "t0")
    age1 = mark_age_dist(rows, "t1")
    cmp_t = compare_table(rows, miss, coh)
    for rec in cmp_t:
        if rec.get("contract") == "M4_PERSISTENT":
            rec["ITAYOSE_BASE_ROW_N"] = integ.get("ITAYOSE_BASE_ROW_N")
            rec["CLOSING_AUCTION_ENDPOINT_ROW_N"] = integ.get("CLOSING_AUCTION_ENDPOINT_ROW_N")
    gates = verdict_pack(
        n=len(rows),
        prim=prim,
        persist_dec=persist_dec,
        carry=carry,
        integ=integ,
        persist=persist,
        coh=coh,
        miss=miss,
        material=material,
        residual=residual,
        stab=stab,
        age0=age0,
        age1=age1,
    )
    cal = plus600_calendar()

    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "C14_ID": C14_ID,
        "C14_SHA": c14.get("sha256"),
        "WAIT_SEC": WAIT_SEC,
        "fill_price": "limit_price",
        "FILL_SOT": "is_executable_continuous_board",
        "development_days": days,
        "core_question": {
            "A": "last observed state remains current until next event",
            "B": "state becomes unknown after N seconds",
            "answer": "A" if gates.get("MARK_STATE_PERSISTS_UNTIL_NEXT_EVENT") else "B_or_unproven",
            "did_not_reuse_BOARD_FRESHNESS_SEC_as_mark_ttl": True,
        },
        "freshness_inventory": FRESHNESS_INVENTORY,
        "code_semantics": CODE_SEMANTICS,
        "plus600_calendar": cal,
        "persist_audit": persist,
        "persistence_decision": persist_dec,
        "comparison": cmp_t,
        "waterfall": wf,
        "first_fail": ff,
        "by_anchor": anc,
        "by_day": day_t,
        "by_symbol": [{k: r[k] for k in ("symbol", "rows_total", "target_valid", "coverage_rate")} for r in sym],
        "missingness": {k: v for k, v in miss.items() if k not in {"score_deciles", "smd"}},
        "score_deciles": miss.get("score_deciles"),
        "smd": miss.get("smd"),
        "cohort_summary": {k: v for k, v in coh.items() if k != "rows"},
        "label_stability_m1_5s_vs_m4": stab,
        "mark_age_t0": age0,
        "mark_age_t600": age1,
        "integrity": integ,
        "session_carry_check": carry,
        "bias_vs_v3": material,
        "structural_residual": residual,
        "gates": gates,
        "B_STATUS": {"B0": B0_STATUS, "B1": B1_STATUS, "per_feature_threshold": PER_FEATURE_THRESHOLD},
        "C_REBUILD_THIS_RUN": C_REBUILD_THIS_RUN,
        "SAFETY": "submit/cancel/live=0/0/0",
    }
    report["_markdown"] = build_markdown(report)

    next_event_rows = [persist.get("next_gt5") or {"empty": True}]
    if isinstance(next_event_rows[0], dict):
        next_event_rows = kv_rows(
            {
                **(persist.get("next_gt5") or {}),
                "still_valid_continuous_rate": persist.get("next_gt5_still_valid_continuous_rate"),
                "became_itayose_special_rate": persist.get("next_gt5_became_itayose_special_rate"),
                "not_opened_rate": persist.get("next_gt5_not_opened_rate"),
                "share_gt5s": persist.get("share_gt5s"),
                "long_quiet_intervals_common": persist.get("long_quiet_intervals_common"),
                "no_5s_expire_protocol_evidence": persist.get("no_5s_expire_protocol_evidence"),
                "diagnostic_only": True,
                "not_used_as_label_gate": True,
            }
        )

    age_rows = []
    for blk in (age0, age1):
        for b in blk.get("buckets") or []:
            age_rows.append({"side": blk.get("side"), **{k: v for k, v in blk.items() if k != "buckets"}, **b})

    sheets = {
        "Summary": kv_rows(
            {k: gates.get(k) for k in (
                "SESSION_SOT_VALID",
                "MARK_STATE_PERSISTS_UNTIL_NEXT_EVENT",
                "ARBITRARY_MARK_AGE_CUTOFF_REQUIRED",
                "M4_PRIMARY_ROWS",
                "M4_COVERAGE_RATE",
                "M4_MEDIAN_COHORT",
                "M4_P10_COHORT",
                "M4_EVENT_RATE_SMD",
                "M4_SCORE_DECILE_COVERAGE_RANGE",
                "M4_SYMBOL_COVERAGE_RANGE",
                "M4_TARGET_MISSINGNESS_SELECTION_BIAS",
                "M4_T0_MARK_AGE_P90",
                "M4_T600_MARK_AGE_P90",
                "COMMON_5S_M4_LABEL_SPEARMAN",
                "TARGET_V4_READY_FOR_C_REBUILD",
                "VERDICT",
                "RECOMMENDED_NEXT_STEP",
            )}
        ),
        "Inventory": FRESHNESS_INVENTORY,
        "Code_Semantics": CODE_SEMANTICS,
        "Intervals": persist.get("buckets") or [{"empty": True}],
        "Next_Event": next_event_rows,
        "Comparison": cmp_t,
        "Waterfall": wf,
        "First_Fail": ff,
        "By_Anchor": anc,
        "By_Day": day_t,
        "By_Symbol": sym,
        "Missingness_SMD": flatten_smd(miss.get("smd") or []),
        "Score_Decile": miss.get("score_deciles") or [{"empty": True}],
        "Cohort": coh.get("rows") or [{"empty": True}],
        "Label_Stability": [stab],
        "Mark_Age": age_rows or [{"empty": True}],
        "Decision": kv_rows(gates) + kv_rows(persist_dec) + kv_rows(material) + kv_rows(residual),
        "Safety": kv_rows(
            {
                "OFFLINE_TARGET_AUDIT_ONLY": True,
                "C14_changed": False,
                "C14_ID": C14_ID,
                "C14_SHA": c14.get("sha256"),
                "Runtime_changed": False,
                "CLOCK_changed": False,
                "ENTRY_changed": False,
                "EXIT_changed": False,
                "CAP_changed": False,
                "reentry_changed": False,
                "Dual_Lane_SESSION_CLOSE_changed": False,
                "Execution_freshness_changed": False,
                "C_model_search": False,
                "B_optimization": False,
                "Paper_started": False,
                "OPVAL_started": False,
                "submit": 0,
                "cancel": 0,
                "live": 0,
                "age_search_expanded_beyond_60s": False,
                "new_mark_source_search": False,
                "e1_x22_15_00_used_as_target_phase": False,
            }
        ),
    }
    write_artifacts(report, sheets)
    print("OUT", OUT, flush=True)
    for k in (
        "MARK_STATE_PERSISTS_UNTIL_NEXT_EVENT",
        "ARBITRARY_MARK_AGE_CUTOFF_REQUIRED",
        "M4_PRIMARY_ROWS",
        "M4_COVERAGE_RATE",
        "M4_MEDIAN_COHORT",
        "M4_P10_COHORT",
        "M4_EVENT_RATE_SMD",
        "M4_SCORE_DECILE_COVERAGE_RANGE",
        "M4_SYMBOL_COVERAGE_RANGE",
        "M4_TARGET_MISSINGNESS_SELECTION_BIAS",
        "M4_T0_MARK_AGE_P90",
        "M4_T600_MARK_AGE_P90",
        "COMMON_5S_M4_LABEL_SPEARMAN",
        "TARGET_V4_READY_FOR_C_REBUILD",
        "VERDICT",
        "RECOMMENDED_NEXT_STEP",
    ):
        print(f"{k}: {gates.get(k)}", flush=True)
    print("C_REBUILD_THIS_RUN: False", flush=True)
    print("STOP. Target V4 audit only. C not started. Runtime not changed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
