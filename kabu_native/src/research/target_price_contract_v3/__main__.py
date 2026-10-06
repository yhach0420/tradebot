"""Offline TARGET PRICE CONTRACT V3. No Runtime write. No C rebuild. No model search."""
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
from research.target_price_contract_v3 import (
    AGE_TOLERANCES_SEC,
    ANALYSIS_ID,
    B0_STATUS,
    B1_STATUS,
    C14_ID,
    C_REBUILD_THIS_RUN,
    MARK_CANDIDATES,
    MAX_WORKERS,
    PER_FEATURE_THRESHOLD,
    QTY_SITES,
    WAIT_SEC,
)
from research.target_price_contract_v3.analyze import (
    age_dist,
    by_anchor,
    by_day,
    by_symbol,
    cohort_stats,
    evaluate_combo,
    first_fail_table,
    label_stability,
    locked_audit,
    m0_reference,
    missingness,
    plus600_calendar,
    select_contract,
    verdict_pack,
    waterfall,
)
from research.target_price_contract_v3.extract import process_day
from research.target_price_contract_v3.publish import (
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

THREE_CONTRACTS = [
    {
        "id": "A_MARKET_PHASE",
        "soT": "current TSE cash equity",
        "AM_continuous": "09:00 <= t < 11:30",
        "PM_continuous": "12:30 <= t < 15:25",
        "closing_auction": "15:25 <= t <= 15:30",
        "market_close": "15:30",
        "forbidden": "e1_x22 PM_SESSION_CLOSE_HM=15:00 as TARGET market phase",
        "runtime": "Dual Lane SESSION_CLOSE 15:00 unchanged",
    },
    {
        "id": "B_PRICE_MARK",
        "soT": "causal as-of last valid continuous quote/trade at/before mark_time",
        "not": "qty>=100, WAIT_SEC Ask-cross, fill freshness, OPENS_WITHIN_1S",
        "M0": "REFERENCE last_executable_mid (qty+executable+5s)",
        "M1": "last continuous quote mid, bid>0, ask>bid, not itayose/special",
        "M2": "last CurrentPrice status in {1,2}, not pre-open/indicative/special",
        "M3": "M1 if age-ok else M2; same rule both t0 and t+600",
    },
    {
        "id": "C_EXECUTION",
        "soT": "is_executable_continuous_board + WAIT_SEC=1.0 + fill_price=limit_price",
        "frozen": True,
    },
]


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _cache_fp(day: str) -> Path:
    return CACHE / f"{day}_TARGET_V3.json"


def _load_cache(day: str) -> dict | None:
    fp = _cache_fp(day)
    if not fp.is_file():
        return None
    try:
        body = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    if body.get("ok") and body.get("date") == day and body.get("stage") == "TARGET_V3":
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


def _slim_combo(c: dict) -> dict:
    return {k: v for k, v in c.items() if not str(k).startswith("_")}


def main() -> int:
    os.environ["PYTHONPATH"] = f"{SRC};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE.parent}"
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE TARGET V3 ONLY", flush=True)
    print("C14/Runtime/CLOCK/ENTRY/EXIT forbidden. C model search forbidden. B forbidden.", flush=True)

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

    combos = []
    for mark in MARK_CANDIDATES:
        for tol in AGE_TOLERANCES_SEC:
            print(f"eval {mark} age={tol}", flush=True)
            combos.append(evaluate_combo(rows, mark, float(tol)))
    chosen_raw = select_contract(combos)
    mark = str(chosen_raw.get("mark") or "M1")
    tol = float(chosen_raw.get("max_mark_age_sec") or 60.0)
    # Recompute miss/coh for the selected pair (integrity, not PnL).
    miss = missingness(rows, mark, tol)
    coh = cohort_stats(rows, mark, tol)
    prim = int(chosen_raw.get("PRIMARY_ROWS") or 0)
    gates = verdict_pack(chosen=chosen_raw, miss=miss, coh=coh, n=len(rows), prim=prim)
    wf = waterfall(rows, mark, tol)
    ff = first_fail_table(rows, mark, tol)
    anc = by_anchor(rows, mark, tol)
    day_t = by_day(rows, mark, tol)
    sym = by_symbol(rows, mark, tol)
    locked = locked_audit(rows)
    stab = label_stability(rows, mark, tol)
    cal = plus600_calendar()
    m0 = m0_reference(rows)
    ages = (
        age_dist(rows, "m1_t0_px", "m1_t0_age")
        + [{"mark": "M1_t600", **r} for r in age_dist(rows, "m1_t1_px", "m1_t1_age")]
        + [{"mark": "M2_t0", **r} for r in age_dist(rows, "m2_t0_px", "m2_t0_age")]
        + [{"mark": "M2_t600", **r} for r in age_dist(rows, "m2_t1_px", "m2_t1_age")]
    )
    # first block is M1 t0 without mark tag — tag it
    for r in ages:
        if "mark" not in r:
            r["mark"] = "M1_t0"

    slim = [_slim_combo(c) for c in combos]
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "C14_ID": C14_ID,
        "C14_SHA": c14.get("sha256"),
        "WAIT_SEC": WAIT_SEC,
        "fill_price": "limit_price",
        "FILL_SOT": "is_executable_continuous_board",
        "development_days": days,
        "three_contracts": THREE_CONTRACTS,
        "plus600_calendar": cal,
        "m0_reference": m0,
        "sensitivity": slim,
        "chosen": _slim_combo(chosen_raw),
        "waterfall": wf,
        "first_fail": ff,
        "by_anchor": anc,
        "by_day": day_t,
        "by_symbol": [{k: r[k] for k in ("symbol", "rows_total", "target_valid", "coverage_rate")} for r in sym],
        "locked": locked,
        "missingness": {k: v for k, v in miss.items() if k not in {"score_deciles", "smd"}},
        "score_deciles": miss.get("score_deciles"),
        "smd": miss.get("smd"),
        "cohort_summary": {k: v for k, v in coh.items() if k != "rows"},
        "label_stability": stab,
        "qty_sites": list(QTY_SITES),
        "gates": gates,
        "B_STATUS": {"B0": B0_STATUS, "B1": B1_STATUS, "per_feature_threshold": PER_FEATURE_THRESHOLD},
        "C_REBUILD_THIS_RUN": C_REBUILD_THIS_RUN,
        "SAFETY": "submit/cancel/live=0/0/0",
        "FUTURE_EVENT_USED": False,
        "OPENS_WITHIN_1S_USED": False,
        "selector_used_pnl_or_topk": False,
        "age_search_expanded_beyond_60s": False,
    }
    report["_markdown"] = build_markdown(report)

    sheets = {
        "Summary": kv_rows({k: gates.get(k) for k in (
            "SESSION_SOT_VALID", "PM_CONTINUOUS_END", "SELECTED_MARK_CONTRACT",
            "SELECTED_MAX_MARK_AGE_SEC", "ROWS_TOTAL", "PRIMARY_ROWS", "PRIMARY_COVERAGE_RATE",
            "MEDIAN_TARGET_VALID_N_PER_COHORT", "P10_TARGET_VALID_N_PER_COHORT",
            "TARGET_MISSINGNESS_SELECTION_BIAS", "SCORE_DECILE_COVERAGE_RANGE",
            "SYMBOL_COVERAGE_RANGE", "ITAYOSE_BASE_ROW_N", "CLOSING_AUCTION_ENDPOINT_ROW_N",
            "FUTURE_EVENT_USED", "TARGET_COHORT_COVERAGE_ADEQUATE",
            "TARGET_V3_READY_FOR_C_REBUILD", "VERDICT", "RECOMMENDED_NEXT_STEP",
        )}),
        "Three_Contracts": THREE_CONTRACTS,
        "Qty_Sites": list(QTY_SITES),
        "Plus600": cal,
        "Age_Dist": ages,
        "Sensitivity": slim + [m0],
        "Locked": kv_rows({k: v for k, v in locked.items() if k != "locked_by_state"}) + [
            {"state": k, "n": v} for k, v in (locked.get("locked_by_state") or {}).items()
        ],
        "Waterfall": wf,
        "First_Fail": ff,
        "By_Anchor": anc,
        "By_Day": day_t,
        "By_Symbol": sym,
        "Missingness_SMD": flatten_smd(miss.get("smd") or []),
        "Score_Decile": miss.get("score_deciles") or [{"empty": True}],
        "Cohort": coh.get("rows") or [{"empty": True}],
        "Label_Stability": stab.get("pairs") or [{"empty": True}],
        "Decision": kv_rows(gates),
        "B_Status": kv_rows({"B0": B0_STATUS, "B1": B1_STATUS, "per_feature_threshold": PER_FEATURE_THRESHOLD, "C_REBUILD_THIS_RUN": False}),
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
                "Dual_Lane_SESSION_CLOSE_changed": False,
                "C_model_search": False,
                "B_optimization": False,
                "Paper_started": False,
                "OPVAL_started": False,
                "submit": 0,
                "cancel": 0,
                "live": 0,
                "qty_used_in_v3_mark": False,
                "OPENS_WITHIN_1S": False,
                "e1_x22_15_00_used_as_target_phase": False,
            }
        ),
    }
    write_artifacts(report, sheets)
    print("OUT", OUT, flush=True)
    for k in (
        "SESSION_SOT_VALID", "SELECTED_MARK_CONTRACT", "SELECTED_MAX_MARK_AGE_SEC",
        "ROWS_TOTAL", "PRIMARY_ROWS", "PRIMARY_COVERAGE_RATE",
        "MEDIAN_TARGET_VALID_N_PER_COHORT", "P10_TARGET_VALID_N_PER_COHORT",
        "TARGET_MISSINGNESS_SELECTION_BIAS", "TARGET_COHORT_COVERAGE_ADEQUATE",
        "TARGET_V3_READY_FOR_C_REBUILD", "VERDICT",
    ):
        print(f"{k}: {gates.get(k)}", flush=True)
    print("C_REBUILD_THIS_RUN: False", flush=True)
    print("STOP. Target V3 audit only. C not started. Runtime not changed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
