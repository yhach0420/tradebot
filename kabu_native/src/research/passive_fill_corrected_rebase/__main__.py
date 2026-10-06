"""Offline corrected Passive Fill rebase. Paper/OPVAL not started."""
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
from research.passive_fill_corrected_rebase import LATE_DAY, MAX_WORKERS
from research.passive_fill_corrected_rebase.analyze import (
    baseline_bundle,
    period_recheck,
    supersede_record,
)
from research.passive_fill_corrected_rebase.publish import (
    OUT,
    build_markdown,
    write_artifacts,
)
from research.passive_fill_corrected_rebase.quality import capture_quality
from research.passive_fill_corrected_rebase.replay import process_day
from small_paper.v1r_activation_binding import file_sha256
from small_paper.v1r_primary_runtime import WAIT_SEC

CACHE = OUT / "_work_cache"
C14_MANIFEST = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)


def _cache_path(day: str) -> Path:
    return CACHE / f"{day}.json"


def _load_cache(day: str) -> dict | None:
    fp = _cache_path(day)
    if not fp.is_file():
        return None
    try:
        body = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    if body.get("ok") and body.get("date") == day and body.get("require_executable_continuous") is True:
        return body
    return None


def _save_cache(day: str, body: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    _cache_path(day).write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")


def _load_c14() -> dict:
    if not C14_MANIFEST.is_file():
        return {}
    return json.loads(C14_MANIFEST.read_text(encoding="utf-8"))


def _load_parity() -> dict:
    fp = CACHE / "parity_summary.json"
    if not fp.is_file():
        return {"verdict": "MISSING", "ok": False}
    body = json.loads(fp.read_text(encoding="utf-8"))
    body["ok"] = str(body.get("verdict") or "").endswith("PASS")
    return body


def main() -> int:
    os.environ["PYTHONPATH"] = f"{SRC};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE.parent}"
    print("SAFETY submit/cancel/live=0/0/0 offline_rebase_only", flush=True)
    if WAIT_SEC != 1.0:
        print("REFUSE WAIT_SEC", WAIT_SEC, flush=True)
        return 2
    parity = _load_parity()
    if not parity.get("ok"):
        print("STOP: Exact/Fast parity not PASS", parity.get("verdict"), flush=True)
        return 2
    c14 = _load_c14()
    if not c14.get("sha256"):
        print("STOP: Candidate-14 snapshot missing", flush=True)
        return 2

    inv = build_inventory()
    elig = [
        r
        for r in inv
        if r.get("replay_eligible") and r.get("universe_symbols") and r.get("capture_path")
    ]
    print(f"INVENTORY all={len(inv)} eligible={len(elig)}", flush=True)
    for r in inv:
        if r.get("jpx_trading_day") or r.get("capture_path"):
            print(
                f"  {r.get('date')} {r.get('capture_class')} uni={r.get('universe_n')} "
                f"elig={r.get('replay_eligible')} {r.get('exclusion_reason') or ''}",
                flush=True,
            )
    q27 = capture_quality(
        LATE_DAY,
        inv_row=next((r for r in inv if r.get("date") == LATE_DAY), None),
    )
    print("20260827 quality", q27.get("DECISION"), q27.get("fail"), flush=True)

    jobs = [
        {
            "date": r["date"],
            "capture_path": r["capture_path"],
            "universe": r["universe_symbols"],
            "universe_source": r.get("universe_source"),
            "period": r.get("period"),
        }
        for r in elig
    ]
    day_rows: list[dict] = []
    pending = []
    for job in jobs:
        cached = _load_cache(job["date"])
        if cached:
            day_rows.append(cached)
            print(f"cache {job['date']}", flush=True)
        else:
            pending.append(job)
    if pending:
        workers = min(MAX_WORKERS, len(pending))
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(process_day, job): job["date"] for job in pending}
            for fut in as_completed(futs):
                day = futs[fut]
                try:
                    body = fut.result()
                except Exception as exc:
                    body = {"ok": False, "date": day, "blocker": f"{type(exc).__name__}:{exc}"}
                day_rows.append(body)
                if body.get("ok"):
                    _save_cache(str(body.get("date")), body)
                print(f"done {day} ok={body.get('ok')} blocker={body.get('blocker')}", flush=True)
    day_rows.sort(key=lambda r: str(r.get("date") or ""))
    failed = [{"date": r.get("date"), "blocker": r.get("blocker")} for r in day_rows if not r.get("ok")]
    trades: list[dict] = []
    fills: list[dict] = []
    expired: list[dict] = []
    for r in day_rows:
        if not r.get("ok"):
            continue
        trades.extend(r.get("trades") or [])
        fills.extend(r.get("fills") or [])
        expired.extend(r.get("expired") or [])

    golden = next((r.get("golden_5801_0905") for r in day_rows if r.get("date") == LATE_DAY), None)
    if LATE_DAY in {j["date"] for j in jobs} and golden and not golden.get("ok"):
        print("STOP: 5801 09:05 still FILL under corrected semantics", golden, flush=True)
        return 2

    base = baseline_bundle(trades)
    period = period_recheck(trades, include_0827=bool(q27.get("PASS")))
    supersede = supersede_record()
    cand = {
        "candidate_id": c14.get("candidate_id"),
        "candidate_sha": c14.get("sha256"),
        "runtime_code_sha": c14.get("runtime_code_sha"),
        "runtime_code_git_commit": c14.get("runtime_code_git_commit"),
        "runtime_inventory_digest": c14.get("runtime_inventory_digest"),
        "config_sha256": c14.get("config_sha256"),
        "STRATEGY_CHANGED": c14.get("STRATEGY_CHANGED"),
        "CLOCK_GRID_CHANGED": c14.get("CLOCK_GRID_CHANGED"),
        "ENTRY_CHANGED": c14.get("ENTRY_CHANGED"),
        "EXIT_CHANGED": c14.get("EXIT_CHANGED"),
        "EXECUTION_SEMANTICS_CHANGED": c14.get("EXECUTION_SEMANTICS_CHANGED"),
        "classification": c14.get("classification"),
    }
    replay_ok = not failed and bool(jobs)
    report = {
        "ANALYSIS_ID": "PASSIVE_FILL_CORRECTED_REBASE",
        "STRATEGY_RETUNED": False,
        "EXECUTION_SEMANTICS_DEFECT_FIXED": True,
        "WAIT_SEC": WAIT_SEC,
        "fill_price": "limit_price",
        "SAFETY": "submit/cancel/live=0/0/0",
        "parity": {"verdict": parity.get("verdict"), "days": parity.get("days")},
        "NEW_RUNTIME_CANDIDATE": cand,
        "CORRECTED_BASELINE": base.get("headline"),
        "baseline": base,
        "OLD_RESULTS_STATUS": supersede.get("OLD_RESULTS_STATUS"),
        "supersede": supersede,
        "20260827_CAPTURE": q27.get("DECISION"),
        "capture_20260827": q27,
        "period_recheck": period,
        "golden_5801_0905": golden,
        "eligible_days": [j["date"] for j in jobs],
        "failed": failed,
        "verdict": (
            "CORRECTED_FILL_BASELINE_READY"
            if replay_ok
            else "CORRECTED_FILL_BASELINE_REPLAY_FAILED"
        ),
        "native_entry_sha": file_sha256(NATIVE / "src" / "small_paper" / "v1r_native_entry_live.py"),
    }
    report["_markdown"] = build_markdown(report)
    extra = {
        "inventory": [
            {
                k: r.get(k)
                for k in (
                    "date",
                    "capture_class",
                    "universe_n",
                    "am_coverage",
                    "pm_coverage",
                    "replay_eligible",
                    "exclusion_reason",
                    "dropped_event_count",
                    "first_seq",
                    "last_seq",
                )
            }
            for r in inv
        ],
        "day_replay": [
            {
                "date": r.get("date"),
                "ok": r.get("ok"),
                "blocker": r.get("blocker"),
                "trades": r.get("trade_n"),
                "pnl": r.get("pnl"),
                "PF": r.get("PF"),
                "maxDD": r.get("maxDD"),
                "fills": r.get("fills_n"),
                "expired": r.get("expired_n"),
                "cap": r.get("cap_blocked"),
                "same_symbol": r.get("same_symbol_blocked"),
                "elapsed_sec": r.get("elapsed_sec"),
            }
            for r in day_rows
        ],
        "trades": [
            {
                k: t.get(k)
                for k in (
                    "date",
                    "symbol",
                    "session",
                    "anchor_time",
                    "candidate_rank",
                    "score",
                    "limit",
                    "fill_time",
                    "fill_price",
                    "exit_time",
                    "exit_price",
                    "exit_reason",
                    "pnl_yen_100",
                    "fill_class",
                )
            }
            for t in trades
        ],
        "by_day": base.get("by_day") or [],
        "by_symbol": base.get("by_symbol") or [],
        "by_anchor": base.get("by_anchor") or [],
        "supersede": [{"item": x, "status": supersede.get("OLD_RESULTS_STATUS")} for x in supersede.get("items") or []],
        "quality_20260827": [q27],
        "period": [
            {"scope": "DEV10", **(period.get("DEV10") or {})},
            {"scope": "POST7", **(period.get("POST7") or {})},
        ],
        "fills": fills,
        "expired": expired,
    }
    write_artifacts(report, extra_sheets=extra)
    print("OUT", OUT, flush=True)
    print("verdict", report["verdict"], flush=True)
    print("CORRECTED_BASELINE", base.get("headline"), flush=True)
    print("20260827", q27.get("DECISION"), flush=True)
    print("period_decay", period.get("period_decay_status"), flush=True)
    return 0 if replay_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
