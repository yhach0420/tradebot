"""Offline CURRENT IRREGULAR non-exec mechanism audit. Does not rewrite B2 / C2 / Runtime."""
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
from research.current_entry_nonexec_mechanism import (
    ANALYSIS_ID,
    B2_FORMAL,
    B2_VERDICT_REWRITTEN,
    C14_ID,
    C2_STATUS_MAINTAINED,
    C3_STARTED,
    ELIGIBLE_DAYS,
    MAX_WORKERS,
    RUNTIME_CHANGED,
    WAIT_SEC,
)
from research.current_entry_nonexec_mechanism.analyze import (
    a0_parity,
    b2_trade_decomp,
    compare_a0_a2,
    decide,
    opportunity_a2,
    paired_day_block,
    prefix_rank,
    slim_pack,
)
from research.current_entry_nonexec_mechanism.publish import (
    OUT,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.current_entry_nonexec_mechanism.replay import process_day
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.executable_target_v2_b_threshold.analyze import pack_metrics
from research.uniform10_b_followup.analyze import flow_join, rank_audit
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
B_CACHE = NATIVE / "results" / "research" / "uniform10_b_followup" / "_work_cache"


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
    _cache_fp(day, stage).write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")


def _pool(jobs: list[dict]) -> list[dict]:
    if not jobs:
        return []
    out = []
    workers = min(MAX_WORKERS, len(jobs))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(process_day, job): (job["date"], job.get("stage")) for job in jobs}
        for fut in as_completed(futs):
            day, stage = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, "date": day, "stage": stage, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            print(
                f"extract {day} {body.get('stage')} ok={body.get('ok')} "
                f"trades={len(body.get('trades') or [])} sec={body.get('elapsed_sec')} "
                f"blocker={body.get('blocker')}",
                flush=True,
            )
    return out


def _run_jobs(elig: list[dict], jobs_spec: list[dict], stage: str) -> list[dict]:
    jobs = []
    got = []
    by_date = {r["date"]: r for r in elig}
    for spec in jobs_spec:
        day = spec["date"]
        cached = _load_cache(day, stage)
        if cached:
            got.append(cached)
            print(f"extract {day} {stage} cache-hit trades={len(cached.get('trades') or [])}", flush=True)
        else:
            r = by_date[day]
            jobs.append(
                {
                    "date": day,
                    "capture_path": r["capture_path"],
                    "universe": r["universe_symbols"],
                    **spec,
                }
            )
    for body in _pool(jobs):
        got.append(body)
        if body.get("ok"):
            _save_cache(str(body.get("date")), str(body.get("stage")), body)
    got.sort(key=lambda x: str(x.get("date") or ""))
    return got


def _load_b_stage(day: str, stage: str) -> dict:
    fp = B_CACHE / f"{day}_{stage}.json"
    if not fp.is_file():
        return {}
    return json.loads(fp.read_text(encoding="utf-8"))


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE CURRENT ENTRY NONEXEC MECHANISM", flush=True)
    print("B2 formal verdict frozen. C2/C3/C14/Runtime not modified.", flush=True)

    if list(FEATURE_ORDER) != [
        "spread_bps", "imbalance", "mid_ret_60s", "mid_ret_180s", "event_rate_60s", "log_bid_qty"
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
    want = set(ELIGIBLE_DAYS)
    elig = [
        r
        for r in inv
        if r.get("date") in want
        and r.get("replay_eligible")
        and r.get("universe_symbols")
        and r.get("capture_path")
    ]
    days = [r["date"] for r in elig]
    missing = [d for d in ELIGIBLE_DAYS if d not in set(days)]
    if missing or len(elig) != 18:
        print("STOP eligible days", missing, len(elig), flush=True)
        return 2
    print(f"eligible days={len(elig)} CLOCK_GRID_N={len(CLOCK_GRID)} ANALYSIS_ID={ANALYSIS_ID}", flush=True)

    a0_rows = _run_jobs(
        elig,
        [{"date": d, "stage": "A0", "executable_t0_only": False, "keep_rank": True} for d in days],
        "A0",
    )
    failed = [r for r in a0_rows if not r.get("ok")]
    if failed:
        print("STOP A0 extract failed", [(r.get("date"), r.get("blocker")) for r in failed], flush=True)
        return 2
    a0_trades: list[dict] = []
    rank_rows: list[dict] = []
    admits: list[dict] = []
    fills: list[dict] = []
    expired: list[dict] = []
    for r in a0_rows:
        a0_trades.extend(r.get("trades") or [])
        rank_rows.extend(r.get("rank_rows") or [])
        admits.extend(r.get("admits") or [])
        fills.extend(r.get("fills") or [])
        expired.extend(r.get("expired") or [])
    a0_pack = pack_metrics(a0_trades, days)
    parity = a0_parity(a0_pack)
    print("A0 Exact", parity.get("observed"), "ok", parity.get("ok"), flush=True)

    empty_req = {
        "A0_PARITY": False,
        "A_CURRENT_ENTRY_NONEXEC_RANK_BIAS": None,
        "A2_TESTED": False,
        "B2_PAIRED_ANALYSIS_CLASSIFICATION": "POST_HOC_DIAGNOSTIC_ONLY",
        "NONEXEC_BIAS_SCOPE": "INCONCLUSIVE",
        "VERDICT": "CURRENT_ENTRY_NONEXEC_AUDIT_INTEGRITY_FAILED",
    }
    if not parity.get("ok"):
        gates = decide(parity_ok=False, bias=False, a2_tested=False, a0={}, a2={}, flow0={}, flow2={}, opp={}, decomp={})
        req = {**empty_req, **{k: gates.get(k) for k in ("VERDICT", "NONEXEC_BIAS_SCOPE", "PRIMARY_MECHANISM", "RECOMMENDED_NEXT_RESEARCH")}}
        report = {"ANALYSIS_ID": ANALYSIS_ID, "required": req, "parity": parity, "gates": gates, "answers": gates}
        report["_markdown"] = build_markdown(report)
        write_artifacts(report, {"Summary": kv_rows(req), "A0_Parity": kv_rows(parity), "Safety": kv_rows({"submit": 0})})
        print("VERDICT: CURRENT_ENTRY_NONEXEC_AUDIT_INTEGRITY_FAILED", flush=True)
        return 0

    rank_raw = rank_audit(rank_rows)
    rank = prefix_rank(rank_raw)
    flow0 = flow_join(rank_rows=rank_rows, admits=admits, fills=fills, expired=expired)
    opp = opportunity_a2(rank_rows, a0_trades)
    bias = bool(rank.get("A_CURRENT_ENTRY_NONEXEC_RANK_BIAS"))
    print(
        f"A rank ALL={rank.get('A_NONEXEC_ALL_RATE')} Top3={rank.get('A_NONEXEC_TOP3_RATE')} "
        f"enr={rank.get('A_NONEXEC_TOP3_ENRICHMENT')} bias={bias}",
        flush=True,
    )

    a2_tested = False
    a2_pack: dict = {}
    flow2: dict = {}
    a2_cmp: dict = {}
    if bias:
        a2_tested = True
        a2_rows = _run_jobs(
            elig,
            [{"date": d, "stage": "A2", "executable_t0_only": True, "keep_rank": False} for d in days],
            "A2",
        )
        bad = [r for r in a2_rows if not r.get("ok")]
        if bad:
            print("STOP A2 extract failed", [(r.get("date"), r.get("blocker")) for r in bad], flush=True)
            return 2
        a2_trades: list[dict] = []
        a2_admits: list[dict] = []
        a2_fills: list[dict] = []
        a2_expired: list[dict] = []
        for r in a2_rows:
            a2_trades.extend(r.get("trades") or [])
            a2_admits.extend(r.get("admits") or [])
            a2_fills.extend(r.get("fills") or [])
            a2_expired.extend(r.get("expired") or [])
        a2_pack = pack_metrics(a2_trades, days)
        flow2 = flow_join(rank_rows=rank_rows, admits=a2_admits, fills=a2_fills, expired=a2_expired)
        a2_cmp = compare_a0_a2(a0_pack, a2_pack, flow0=flow0, flow2=flow2)
        print("A2", slim_pack(a2_pack), flush=True)

    # B2 post-hoc from frozen B caches. Read-only. Does not rewrite B verdict.
    b0_trades: list[dict] = []
    b2_trades: list[dict] = []
    b0_admits: list[dict] = []
    b0_expired: list[dict] = []
    b0_rank: list[dict] = []
    b_missing = []
    for d in days:
        b0b = _load_b_stage(d, "B0")
        b2b = _load_b_stage(d, "B2")
        if not b0b.get("ok") or not b2b.get("ok"):
            b_missing.append(d)
            continue
        b0_trades.extend(b0b.get("trades") or [])
        b2_trades.extend(b2b.get("trades") or [])
        b0_admits.extend(b0b.get("admits") or [])
        b0_expired.extend(b0b.get("expired") or [])
        b0_rank.extend(b0b.get("rank_rows") or [])
    if b_missing:
        print("STOP missing B0/B2 cache", b_missing, flush=True)
        return 2
    decomp = b2_trade_decomp(
        b0_trades=b0_trades, b2_trades=b2_trades, b0_admits=b0_admits, b0_expired=b0_expired, b0_rank=b0_rank
    )
    paired = paired_day_block(b0_trades=b0_trades, b2_trades=b2_trades, days=days)
    print(
        f"B2 decomp added={decomp.get('B2_ADDED_TRADES_N')} removed={decomp.get('B2_REMOVED_TRADES_N')} "
        f"paired pos={paired.get('positive_delta_days')} neg={paired.get('negative_delta_days')}",
        flush=True,
    )

    gates = decide(
        parity_ok=True,
        bias=bias,
        a2_tested=a2_tested,
        a0=a0_pack,
        a2=a2_pack if a2_tested else {},
        flow0=flow0,
        flow2=flow2 if a2_tested else {},
        opp=opp,
        decomp=decomp,
    )
    req = {
        "A0_PARITY": True,
        "A_NONEXEC_ALL_RATE": rank.get("A_NONEXEC_ALL_RATE"),
        "A_NONEXEC_TOP1_RATE": rank.get("A_NONEXEC_TOP1_RATE"),
        "A_NONEXEC_TOP3_RATE": rank.get("A_NONEXEC_TOP3_RATE"),
        "A_NONEXEC_TOP3_ENRICHMENT": rank.get("A_NONEXEC_TOP3_ENRICHMENT"),
        "A_PENDING_NONEXEC_RATE": flow0.get("PENDING_NONEXEC_AT_T0_RATE"),
        "A_FILL_FROM_NONEXEC_T0_N": flow0.get("FILL_FROM_NONEXEC_AT_T0_N"),
        "A_CURRENT_ENTRY_NONEXEC_RANK_BIAS": bias,
        "A2_TESTED": a2_tested,
        "A2_TRADES": a2_pack.get("trades") if a2_tested else None,
        "A2_PNL": a2_pack.get("PnL") if a2_tested else None,
        "A2_PF": a2_pack.get("PF") if a2_tested else None,
        "A2_MAXDD": a2_pack.get("maxDD") if a2_tested else None,
        "A2_POSITIVE_DAY_RATE": a2_pack.get("positive_day_rate") if a2_tested else None,
        "A2_MEDIAN_DAILY_PNL": a2_pack.get("median_daily_pnl") if a2_tested else None,
        "A2_WOULD_DROP_FILL_N": opp.get("A2_WOULD_DROP_FILL_N"),
        "A2_WOULD_DROP_FILL_PNL": opp.get("A2_WOULD_DROP_FILL_PNL"),
        "B2_ADDED_TRADES_N": decomp.get("B2_ADDED_TRADES_N"),
        "B2_ADDED_FIRST_PNL": decomp.get("B2_ADDED_FIRST_PNL"),
        "B2_ADDED_REENTRY_PNL": decomp.get("B2_ADDED_REENTRY_PNL"),
        "B2_REMOVED_TRADES_N": decomp.get("B2_REMOVED_TRADES_N"),
        "B2_DELTA_POSITIVE_DAYS": paired.get("positive_delta_days"),
        "B2_DELTA_NEGATIVE_DAYS": paired.get("negative_delta_days"),
        "B2_MEDIAN_DAY_DELTA": paired.get("median_delta"),
        "B2_PAIRED_ANALYSIS_CLASSIFICATION": "POST_HOC_DIAGNOSTIC_ONLY",
        "NONEXEC_BIAS_SCOPE": gates.get("NONEXEC_BIAS_SCOPE"),
        "PRIMARY_MECHANISM": gates.get("PRIMARY_MECHANISM"),
        "RECOMMENDED_NEXT_RESEARCH": gates.get("RECOMMENDED_NEXT_RESEARCH"),
        "VERDICT": gates.get("VERDICT"),
    }
    manifest = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "C14_ID": C14_ID,
        "C14_SHA": c14.get("sha256"),
        "WAIT_SEC": WAIT_SEC,
        "FILL_SOT": "is_executable_continuous_board",
        "CLOCK": "CURRENT IRREGULAR CLOCK_GRID production",
        "CLOCK_GRID": [f"{h:02d}:{m:02d}" for h, m in CLOCK_GRID],
        "development_days": days,
        "B2_FORMAL_FROZEN": B2_FORMAL,
        "C2_STATUS_MAINTAINED": C2_STATUS_MAINTAINED,
        "C3_STARTED": C3_STARTED,
        "B2_VERDICT_REWRITTEN": B2_VERDICT_REWRITTEN,
        "occupancy_used": False,
        "score_threshold_used": False,
        "new_model": False,
    }
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": req,
        "parity": parity,
        "rank": rank,
        "rank_raw": rank_raw,
        "flow_A0": flow0,
        "flow_A2": flow2 if a2_tested else None,
        "A0": slim_pack(a0_pack),
        "A2": slim_pack(a2_pack) if a2_tested else None,
        "A0_vs_A2": a2_cmp if a2_tested else None,
        "A2_opportunity": opp,
        "B2_decomp": decomp,
        "B2_paired": {k: v for k, v in paired.items() if k != "days"},
        "gates": gates,
        "answers": {
            "Q1": gates.get("Q1"),
            "Q2": gates.get("Q2"),
            "Q3": gates.get("Q3"),
            "Q4": gates.get("Q4"),
            "Q5": gates.get("Q5"),
        },
        "manifest": manifest,
        "B2_FORMAL_UNCHANGED": B2_FORMAL,
        "C2_STATUS_MAINTAINED": C2_STATUS_MAINTAINED,
        "SAFETY": "submit/cancel/live=0/0/0",
    }
    report["_markdown"] = build_markdown(report)
    sheets = {
        "Summary": kv_rows(req),
        "Data_Manifest": kv_rows(manifest),
        "A0_Parity": kv_rows(parity) + kv_rows(slim_pack(a0_pack)),
        "A_Rank_Audit": kv_rows(rank),
        "A_Flow": kv_rows(flow0) + (kv_rows({"A2": flow2}) if flow2 else []),
        "A0_vs_A2": [a2_cmp] if a2_cmp else [{"A2_TESTED": False}],
        "A2_Opportunity_Cost": kv_rows(opp),
        "B0_vs_B2_Daily": paired.get("days") or [{"empty": True}],
        "B2_Trade_Decomposition": kv_rows({k: v for k, v in decomp.items() if k != "B2_formal_unchanged"}),
        "Mechanism": kv_rows(gates) + kv_rows(report["answers"]),
        "Safety": kv_rows(
            {
                "OFFLINE_ONLY": True,
                "C2_changed": False,
                "C2_STATUS_MAINTAINED": C2_STATUS_MAINTAINED,
                "C3_STARTED": C3_STARTED,
                "C14_changed": False,
                "Runtime_changed": RUNTIME_CHANGED,
                "CLOCK_changed": False,
                "ENTRY_changed": False,
                "EXIT_changed": False,
                "Fill_changed": False,
                "B2_VERDICT_REWRITTEN": B2_VERDICT_REWRITTEN,
                "B2_ROBUST_IMPROVEMENT_FORMAL": False,
                "RECOMMENDED_B_VARIANT_FORMAL": "B0",
                "B_VERDICT_FORMAL": "B_NO_ROBUST_IMPROVEMENT",
                "POST_HOC_DIAGNOSTIC_ONLY": True,
                "occupancy_used": False,
                "score_threshold": False,
                "new_model": False,
                "per_feature_threshold": False,
                "reentry_rule_changed": False,
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
    for k, v in req.items():
        print(f"{k}: {v}", flush=True)
    print("B2_FORMAL_UNCHANGED:", B2_FORMAL, flush=True)
    print("C2_STATUS_MAINTAINED:", C2_STATUS_MAINTAINED, flush=True)
    print("STOP. Runtime/C14/C2/B2-verdict unchanged. Paper/OPVAL not operated.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
