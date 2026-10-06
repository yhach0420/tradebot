"""Offline UNIFORM10 B follow-up. Does not touch C2. No Runtime write. No occupancy."""
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
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.executable_target_v2_b_threshold.analyze import pack_metrics
from research.uniform10_b_followup import (
    ANALYSIS_ID,
    B1_PERCENTILES,
    C14_ID,
    C_REBUILD_THIS_RUN,
    ELIGIBLE_DAYS,
    MAX_WORKERS,
    PER_FEATURE_THRESHOLD,
    WAIT_SEC,
)
from research.uniform10_b_followup.analyze import (
    b0_parity,
    compare_pack,
    flow_join,
    label_p,
    nested_exact,
    opportunity_cost_b2,
    rank_audit,
    robust_vs_b0,
    slim_pack,
    verdict_pack,
)
from research.uniform10_b_followup.publish import (
    OUT,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.uniform10_b_followup.replay import process_day
from small_paper.v1r_native_entry_live import FEATURE_ORDER

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
CACHE = OUT / "_work_cache"


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
    _cache_fp(day, stage).write_text(json.dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")


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


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE B FOLLOW-UP Exact Dual-Lane", flush=True)
    print("C2 not modified. Occupancy forbidden. Runtime/C14/Fill frozen.", flush=True)

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
    if missing:
        print("STOP missing eligible days", missing, flush=True)
        return 2
    if len(elig) != 18:
        print("STOP expected 18 eligible days", len(elig), days, flush=True)
        return 2
    print(f"eligible days={len(elig)} WAIT_SEC={WAIT_SEC} ANALYSIS_ID={ANALYSIS_ID}", flush=True)

    b0_rows = _run_jobs(
        elig,
        [{"date": d, "stage": "B0", "score_pct": None, "executable_t0_only": False, "keep_rank": True} for d in days],
        "B0",
    )
    failed = [r for r in b0_rows if not r.get("ok")]
    if failed:
        print("STOP B0 extract failed", [(r.get("date"), r.get("blocker")) for r in failed], flush=True)
        return 2
    b0_trades: list[dict] = []
    rank_rows: list[dict] = []
    admits: list[dict] = []
    fills: list[dict] = []
    expired: list[dict] = []
    for r in b0_rows:
        b0_trades.extend(r.get("trades") or [])
        rank_rows.extend(r.get("rank_rows") or [])
        admits.extend(r.get("admits") or [])
        fills.extend(r.get("fills") or [])
        expired.extend(r.get("expired") or [])
    b0_pack = pack_metrics(b0_trades, days)
    parity = b0_parity(b0_pack)
    print("B0 Exact", parity.get("observed"), "ok", parity.get("ok"), flush=True)

    if not parity.get("ok"):
        req = {
            "B0_PARITY": False,
            "NONEXEC_ALL_RATE": None,
            "NONEXEC_TOP1_RATE": None,
            "NONEXEC_TOP3_RATE": None,
            "NONEXEC_TOP5_RATE": None,
            "NONEXEC_TOP3_ENRICHMENT": None,
            "PENDING_NONEXEC_RATE": None,
            "EXPIRED_FROM_NONEXEC_RATE": None,
            "FILL_FROM_NONEXEC_T0_N": None,
            "CURRENT_ENTRY_NONEXEC_RANK_BIAS": None,
            "B1_SELECTED_THRESHOLD": None,
            "B1_EXACT_TRADES": None,
            "B1_EXACT_PNL": None,
            "B1_EXACT_PF": None,
            "B1_EXACT_DD": None,
            "B1_MEDIAN_DAILY_PNL": None,
            "B1_POSITIVE_DAY_RATE": None,
            "B1_ROBUST_IMPROVEMENT": False,
            "B2_TESTED": False,
            "B2_PNL": None,
            "B2_PF": None,
            "B2_DD": None,
            "B2_ROBUST_IMPROVEMENT": None,
            "RECOMMENDED_B_VARIANT": "NONE",
            "VERDICT": "B_INTEGRITY_FAILED",
            "B_BASELINE_PARITY_FAILED": True,
        }
        gates = {**req, "RECOMMENDED_NEXT_STEP": "STOP. B0 Exact Dual-Lane headline mismatch. B1/B2 not started."}
        report = {
            "ANALYSIS_ID": ANALYSIS_ID,
            "required": req,
            "parity": parity,
            "gates": gates,
            "C_REBUILD_THIS_RUN": False,
            "occupancy_used": False,
        }
        report["_markdown"] = build_markdown(report)
        write_artifacts(
            report,
            {
                "Summary": kv_rows(req),
                "B0": kv_rows(parity),
                "Decision": kv_rows(gates),
                "Safety": kv_rows({"submit": 0, "cancel": 0, "live": 0, "C2_changed": False, "occupancy_used": False}),
            },
        )
        print("VERDICT: B_INTEGRITY_FAILED", flush=True)
        print("B_BASELINE_PARITY_FAILED: True", flush=True)
        print("STOP. B1 not started.", flush=True)
        return 0

    rank = rank_audit(rank_rows)
    flow = flow_join(rank_rows=rank_rows, admits=admits, fills=fills, expired=expired)
    opp = opportunity_cost_b2(rank_rows, b0_trades)
    bias = bool(rank.get("CURRENT_ENTRY_NONEXEC_RANK_BIAS"))
    print(
        f"rank ALL={rank.get('NONEXEC_ALL_RATE')} Top3={rank.get('NONEXEC_TOP3_RATE')} "
        f"enr={rank.get('NONEXEC_TOP3_ENRICHMENT')} bias={bias}",
        flush=True,
    )

    trades_by_day_p: dict[str, dict] = {d: {None: []} for d in days}
    for t in b0_trades:
        trades_by_day_p[str(t.get("date"))][None].append(t)

    search_rows = []
    for p in B1_PERCENTILES:
        if p is None:
            m = pack_metrics(b0_trades, days)
            search_rows.append({"threshold": "NO_THRESHOLD", **slim_pack(m)})
            continue
        stage = f"B1_{label_p(p)}"
        rows = _run_jobs(
            elig,
            [
                {
                    "date": d,
                    "stage": stage,
                    "score_pct": p,
                    "executable_t0_only": False,
                    "keep_rank": False,
                }
                for d in days
            ],
            stage,
        )
        bad = [r for r in rows if not r.get("ok")]
        if bad:
            print("STOP B1 extract failed", [(r.get("date"), r.get("blocker")) for r in bad], flush=True)
            return 2
        xs = []
        for d in days:
            trades_by_day_p[d][p] = []
        for r in rows:
            xs.extend(r.get("trades") or [])
            trades_by_day_p[str(r.get("date"))][p] = list(r.get("trades") or [])
        m = pack_metrics(xs, days)
        search_rows.append({"threshold": label_p(p), **slim_pack(m)})
        print(f"B1 {label_p(p)} trades={m.get('trades')} PnL={m.get('PnL')} PF={m.get('PF')}", flush=True)

    nested = nested_exact(trades_by_day_p, days)
    b1_oof = nested.get("oof_trades") or []
    b1_pack = pack_metrics(b1_oof, days)
    b1_chk = robust_vs_b0(b1_pack, b0_pack)
    b1_robust = bool(b1_chk.get("robust")) and nested.get("mode_p") is not None
    print("B1 OOF", nested.get("BEST_B1_THRESHOLD"), slim_pack(b1_pack), "robust", b1_robust, flush=True)

    b2_tested = False
    b2_pack: dict = {}
    b2_chk: dict = {}
    b2_cmp: dict = {}
    b2_robust = None
    if bias:
        b2_tested = True
        rows = _run_jobs(
            elig,
            [
                {
                    "date": d,
                    "stage": "B2",
                    "score_pct": None,
                    "executable_t0_only": True,
                    "keep_rank": False,
                }
                for d in days
            ],
            "B2",
        )
        bad = [r for r in rows if not r.get("ok")]
        if bad:
            print("STOP B2 extract failed", [(r.get("date"), r.get("blocker")) for r in bad], flush=True)
            return 2
        b2_trades = []
        for r in rows:
            b2_trades.extend(r.get("trades") or [])
        b2_pack = pack_metrics(b2_trades, days)
        b2_chk = robust_vs_b0(b2_pack, b0_pack)
        b2_robust = bool(b2_chk.get("robust"))
        b2_cmp = compare_pack(b0_pack, b2_pack, tag="B0_vs_B2")
        print("B2", slim_pack(b2_pack), "robust", b2_robust, flush=True)

    gates = verdict_pack(
        parity_ok=True,
        bias=bias,
        b1_robust=b1_robust,
        b2_tested=b2_tested,
        b2_robust=b2_robust,
        b1_label=str(nested.get("BEST_B1_THRESHOLD") or "NO_THRESHOLD"),
    )
    req = {
        "B0_PARITY": True,
        "NONEXEC_ALL_RATE": rank.get("NONEXEC_ALL_RATE"),
        "NONEXEC_TOP1_RATE": rank.get("NONEXEC_TOP1_RATE"),
        "NONEXEC_TOP3_RATE": rank.get("NONEXEC_TOP3_RATE"),
        "NONEXEC_TOP5_RATE": rank.get("NONEXEC_TOP5_RATE"),
        "NONEXEC_TOP3_ENRICHMENT": rank.get("NONEXEC_TOP3_ENRICHMENT"),
        "PENDING_NONEXEC_RATE": flow.get("PENDING_NONEXEC_AT_T0_RATE"),
        "EXPIRED_FROM_NONEXEC_RATE": flow.get("EXPIRED_FROM_NONEXEC_RATE"),
        "FILL_FROM_NONEXEC_T0_N": flow.get("FILL_FROM_NONEXEC_AT_T0_N"),
        "CURRENT_ENTRY_NONEXEC_RANK_BIAS": bias,
        "B1_SELECTED_THRESHOLD": nested.get("BEST_B1_THRESHOLD"),
        "B1_EXACT_TRADES": b1_pack.get("trades"),
        "B1_EXACT_PNL": b1_pack.get("PnL"),
        "B1_EXACT_PF": b1_pack.get("PF"),
        "B1_EXACT_DD": b1_pack.get("maxDD"),
        "B1_MEDIAN_DAILY_PNL": b1_pack.get("median_daily_pnl"),
        "B1_POSITIVE_DAY_RATE": b1_pack.get("positive_day_rate"),
        "B1_ROBUST_IMPROVEMENT": b1_robust,
        "B2_TESTED": b2_tested,
        "B2_PNL": b2_pack.get("PnL"),
        "B2_PF": b2_pack.get("PF"),
        "B2_DD": b2_pack.get("maxDD"),
        "B2_ROBUST_IMPROVEMENT": b2_robust,
        "RECOMMENDED_B_VARIANT": gates.get("RECOMMENDED_B_VARIANT"),
        "VERDICT": gates.get("VERDICT"),
    }

    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "C14_ID": C14_ID,
        "C14_SHA": c14.get("sha256"),
        "WAIT_SEC": WAIT_SEC,
        "FILL_SOT": "is_executable_continuous_board",
        "development_days": days,
        "required": req,
        "parity": parity,
        "rank": rank,
        "flow": flow,
        "b2_opportunity_cost": opp,
        "B0": slim_pack(b0_pack),
        "B1_search": search_rows,
        "B1_nested": {k: v for k, v in nested.items() if k != "oof_trades"},
        "B1_OOF": slim_pack(b1_pack),
        "B1_robust_checks": b1_chk,
        "B2": slim_pack(b2_pack) if b2_tested else None,
        "B2_robust_checks": b2_chk if b2_tested else None,
        "B2_vs_B0": b2_cmp if b2_tested else None,
        "gates": gates,
        "B_STATUS": {"per_feature_threshold": PER_FEATURE_THRESHOLD},
        "C_REBUILD_THIS_RUN": C_REBUILD_THIS_RUN,
        "occupancy_used": False,
        "SAFETY": "submit/cancel/live=0/0/0",
    }
    report["_markdown"] = build_markdown(report)
    sheets = {
        "Summary": kv_rows(req),
        "Rank_Audit": kv_rows(rank),
        "Flow": kv_rows(flow) + kv_rows(opp),
        "B0": kv_rows(slim_pack(b0_pack)) + kv_rows(parity),
        "B1_Folds": nested.get("folds") or [{"empty": True}],
        "B1_Search": search_rows,
        "B2": (kv_rows(slim_pack(b2_pack)) + kv_rows(b2_chk) + kv_rows(opp)) if b2_tested else [{"B2_TESTED": False}],
        "Comparison": [compare_pack(b0_pack, b1_pack, tag="B0_vs_B1_OOF")]
        + ([b2_cmp] if b2_cmp else []),
        "Decision": kv_rows(gates),
        "Safety": kv_rows(
            {
                "OFFLINE_B_FOLLOWUP_ONLY": True,
                "C2_changed": False,
                "C14_changed": False,
                "Runtime_changed": False,
                "CLOCK_changed": False,
                "ENTRY_changed": False,
                "EXIT_changed": False,
                "Fill_changed": False,
                "occupancy_used": False,
                "absolute_score_threshold": False,
                "AM_PM_threshold": False,
                "per_feature_threshold": False,
                "OPENS_WITHIN_1S_as_gate": False,
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
    print("C_REBUILD_THIS_RUN: False", flush=True)
    print("STOP. B follow-up only. Runtime not changed. C2 not changed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
