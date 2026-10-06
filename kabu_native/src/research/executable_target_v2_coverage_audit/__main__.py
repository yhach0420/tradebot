"""Offline EXECUTABLE TARGET V2 session + coverage integrity audit. No Runtime write. No C rebuild."""
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
from research.executable_target_v2_coverage_audit import (
    ANALYSIS_ID,
    B0_STATUS,
    B1_STATUS,
    C14_ID,
    C_REBUILD_THIS_RUN,
    MAX_WORKERS,
    PER_FEATURE_THRESHOLD_RESEARCH,
    WAIT_SEC,
)
from research.executable_target_v2_coverage_audit.analyze import (
    by_anchor,
    by_day,
    by_symbol,
    cohort,
    first_fail_table,
    gates_and_verdict,
    lookup_audit,
    missingness,
    plus600_calendar,
    session_verdict,
    v2_null_table,
    waterfall,
)
from research.executable_target_v2_coverage_audit.extract import process_day
from research.executable_target_v2_coverage_audit.publish import (
    OUT,
    build_markdown,
    flatten_smd,
    kv_rows,
    write_artifacts,
)
from research.executable_target_v2_coverage_audit.session_sot import (
    FIFTEEN_00_CLASSIFICATION,
    SESSION_INVENTORY,
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

LOOKUP_CODE_PROOF = (
    "src/research/executable_target_v2_b_threshold/contract.py last_executable_mid: "
    "i = np.searchsorted(t, t_at, side='right')-1; walk j=i..0; break if "
    "(t_at-tj)>BOARD_FRESHNESS_SEC or tj<t_lo; skip non-executable/special/"
    "fresh_sec>5/qty<100/bid>=ask; return mid of first accepted. "
    "primary_target_row: t1=t0+HORIZON_SEC(600); endpoint_in_session = "
    "t1 <= session_end_epoch(day,session) with PM_SESSION_CLOSE_HM=(15,0)."
)


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _cache_fp(day: str) -> Path:
    return CACHE / f"{day}_COVERAGE_AUDIT.json"


def _load_cache(day: str) -> dict | None:
    fp = _cache_fp(day)
    if not fp.is_file():
        return None
    try:
        body = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    if body.get("ok") and body.get("date") == day and body.get("stage") == "COVERAGE_AUDIT":
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
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AUDIT ONLY", flush=True)
    print("C14 mutation forbidden. Runtime ENTRY/EXIT/CLOCK forbidden. C REBUILD V2 forbidden.", flush=True)

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
            jobs.append(
                {
                    "date": r["date"],
                    "capture_path": r["capture_path"],
                    "universe": r["universe_symbols"],
                }
            )
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

    sess = session_verdict()
    wf = waterfall(rows)
    ff = first_fail_table(rows)
    v2n = v2_null_table(rows)
    anc = by_anchor(rows)
    day_t = by_day(rows)
    sym = by_symbol(rows)
    look = lookup_audit(rows)
    miss = missingness(rows)
    coh = cohort(rows)
    cal = plus600_calendar()
    gates = gates_and_verdict(rows=rows, miss=miss, coh=coh, lookup=look, sess=sess)

    if int(gates.get("ROWS_TOTAL") or 0) != 27900:
        print("WARN ROWS_TOTAL != 27900", gates.get("ROWS_TOTAL"), flush=True)
    if int(gates.get("ROWS_EXECUTABLE_T0") or 0) != 26944:
        print("WARN ROWS_EXECUTABLE_T0 != 26944", gates.get("ROWS_EXECUTABLE_T0"), flush=True)
    if int(gates.get("PRIMARY_ROWS") or 0) != 6709:
        print("WARN PRIMARY_ROWS != 6709", gates.get("PRIMARY_ROWS"), flush=True)

    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "C14_ID": C14_ID,
        "C14_SHA": c14.get("sha256"),
        "WAIT_SEC": WAIT_SEC,
        "fill_price": "limit_price",
        "FILL_SOT": "is_executable_continuous_board",
        "development_days": days,
        "LOOKUP_CODE_PROOF": LOOKUP_CODE_PROOF,
        "session": sess,
        "plus600_calendar": cal,
        "waterfall": wf,
        "first_fail": ff,
        "v2_null_reasons": v2n,
        "by_anchor": anc,
        "by_day": day_t,
        "by_symbol": sym,
        "endpoint_lookup": look,
        "missingness": {k: v for k, v in miss.items() if k != "score_deciles"},
        "score_deciles": miss.get("score_deciles"),
        "cohort_summary": {k: v for k, v in coh.items() if k != "rows"},
        "gates": gates,
        "B_STATUS": {
            "B0": B0_STATUS,
            "B1": B1_STATUS,
            "per_feature_hard_threshold_research": PER_FEATURE_THRESHOLD_RESEARCH,
        },
        "C_REBUILD_V2_THIS_RUN": C_REBUILD_THIS_RUN,
        "SAFETY": "submit/cancel/live=0/0/0",
    }
    report["_markdown"] = build_markdown(report)

    sheets = {
        "Summary": kv_rows({k: gates.get(k) for k in (
            "SESSION_SOT_VALID",
            "PM_MARKET_SESSION_END",
            "PM_CONTINUOUS_END",
            "PM_CLOSING_AUCTION",
            "STALE_SESSION_CONSTANT",
            "15_00_PLUS600_STATUS",
            "15_10_PLUS600_STATUS",
            "15_20_PLUS600_STATUS",
            "ROWS_TOTAL",
            "ROWS_EXECUTABLE_T0",
            "PRIMARY_ROWS",
            "PRIMARY_COVERAGE_RATE",
            "TOP_NULL_REASON",
            "TOP_NULL_REASON_N",
            "ENDPOINT_LOOKUP_SEMANTIC",
            "ENDPOINT_LOOKUP_DEFECT",
            "TARGET_MISSINGNESS_SELECTION_BIAS",
            "MEDIAN_TARGET_VALID_N_PER_COHORT",
            "TARGET_COHORT_COVERAGE_ADEQUATE",
            "TARGET_V2_COVERAGE_ACCEPTABLE",
            "VERDICT",
            "RECOMMENDED_NEXT_STEP",
            "ITAYOSE_BASE_ROW_N",
            "NON_EXECUTABLE_T0_ROW_N",
            "would_primary_if_tse_zaraba_n",
        )}),
        "Session_Inventory": SESSION_INVENTORY,
        "Fifteen00_Class": FIFTEEN_00_CLASSIFICATION,
        "Anchor_Plus600": cal,
        "Waterfall": wf,
        "First_Fail": ff,
        "V2_Null_Reasons": v2n,
        "By_Anchor": anc,
        "By_Day": day_t,
        "By_Symbol": sym,
        "Endpoint_Lookup": kv_rows(look),
        "Missingness_SMD": flatten_smd(miss.get("smd") or []),
        "Score_Decile": miss.get("score_deciles") or [{"empty": True}],
        "Cohort": coh.get("rows") or [{"empty": True}],
        "Decision": kv_rows(gates) + kv_rows(sess),
        "B_Status": kv_rows(
            {
                "B0": B0_STATUS,
                "B1": B1_STATUS,
                "per_feature_hard_threshold_research": PER_FEATURE_THRESHOLD_RESEARCH,
                "C_REBUILD_V2_THIS_RUN": C_REBUILD_THIS_RUN,
            }
        ),
        "Safety": kv_rows(
            {
                "OFFLINE_AUDIT_ONLY": True,
                "C14_changed": False,
                "C14_ID": C14_ID,
                "C14_SHA": c14.get("sha256"),
                "Runtime_changed": False,
                "ENTRY_changed": False,
                "EXIT_changed": False,
                "CLOCK_changed": False,
                "PRIMARY_semantics_changed": False,
                "last_executable_mid_changed": False,
                "C_REBUILD_V2_started": False,
                "B_threshold_search": False,
                "per_feature_threshold_started": False,
                "Paper_started": False,
                "OPVAL_started": False,
                "submit": 0,
                "cancel": 0,
                "live": 0,
            }
        ),
    }
    write_artifacts(report, sheets)
    print("OUT", OUT, flush=True)
    for k in (
        "SESSION_SOT_VALID",
        "STALE_SESSION_CONSTANT",
        "15_00_PLUS600_STATUS",
        "15_10_PLUS600_STATUS",
        "15_20_PLUS600_STATUS",
        "ROWS_TOTAL",
        "ROWS_EXECUTABLE_T0",
        "PRIMARY_ROWS",
        "PRIMARY_COVERAGE_RATE",
        "TOP_NULL_REASON",
        "TOP_NULL_REASON_N",
        "ENDPOINT_LOOKUP_DEFECT",
        "TARGET_MISSINGNESS_SELECTION_BIAS",
        "MEDIAN_TARGET_VALID_N_PER_COHORT",
        "TARGET_COHORT_COVERAGE_ADEQUATE",
        "TARGET_V2_COVERAGE_ACCEPTABLE",
        "VERDICT",
    ):
        print(f"{k}: {gates.get(k)}", flush=True)
    print("C_REBUILD_V2_THIS_RUN: False", flush=True)
    print("STOP. Audit only. C not started. Runtime not changed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
