"""Offline 10-minute gap / uniform-grid diagnostic. Paper not started. Runtime not changed."""
from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_10min_opportunity import HISTORICAL_DAYS, MAX_WORKERS
from research.anchor_10min_opportunity.analyze import run_analysis
from research.anchor_10min_opportunity.grids import grid_contract
from research.anchor_10min_opportunity.publish import OUT, write_artifacts, build_report
from research.anchor_10min_opportunity.replay import process_day
from research.anchor_timing_robustness.inventory import build_inventory
from research.anchor_timing_robustness.provenance import collect_provenance

CACHE = OUT / "_work_cache"


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
    if body.get("ok") and body.get("date") == day and (body.get("portfolios") or {}).get("UNIFORM10"):
        return body
    return None


def _save_cache(day: str, body: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    _cache_path(day).write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")


def main() -> int:
    print("SAFETY submit/cancel/live=0/0/0 offline_research_only", flush=True)
    print("CLOCK_GRID unchanged. No start-minute search. No 10-min adoption.", flush=True)
    prov = collect_provenance()
    g = grid_contract()
    print("SOURCE_FILE:", prov.get("SOURCE_FILE"), flush=True)
    print("SOURCE_SHA:", prov.get("SOURCE_SHA"), flush=True)
    print("REFERENCE_ANCHORS:", g["REFERENCE_ANCHORS"], flush=True)
    print("PHASE_A_ADDED_ANCHORS:", g["PHASE_A_ADDED_ANCHORS"], flush=True)
    print("UNIFORM10_N:", g["UNIFORM10_N"], flush=True)

    inv = build_inventory()
    elig = [
        r
        for r in inv
        if r.get("replay_eligible") and r.get("universe_symbols") and r.get("capture_path") and r["date"] in HISTORICAL_DAYS
    ]
    used_dates = [r["date"] for r in elig]
    missing = [d for d in HISTORICAL_DAYS if d not in used_dates]
    print(f"INVENTORY eligible_used={len(elig)} missing={missing}", flush=True)
    if missing:
        print("BLOCKED missing historical days", missing, flush=True)
        return 2

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
    results: list[dict] = []
    failed: list[str] = []
    pending = []
    for j in jobs:
        cached = _load_cache(j["date"])
        if cached is not None:
            print(f"CACHE {j['date']} elapsed={cached.get('elapsed_sec')}", flush=True)
            results.append(cached)
        else:
            pending.append(j)

    if pending:
        print(f"RUN days={len(pending)} workers={MAX_WORKERS}", flush=True)
        with ProcessPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {ex.submit(process_day, j): j for j in pending}
            for fut in as_completed(futs):
                job = futs[fut]
                day = job["date"]
                try:
                    out = fut.result()
                except Exception as exc:
                    print(f"FAIL {day} {exc!r}", flush=True)
                    failed.append(f"{day}:EXC:{exc}")
                    continue
                if not out.get("ok"):
                    print(f"FAIL {day} {out.get('blocker')}", flush=True)
                    failed.append(f"{day}:{out.get('blocker')}")
                    continue
                _save_cache(day, out)
                print(f"OK {day} leak={out.get('snapshot_future_leak')} sec={out.get('elapsed_sec')}", flush=True)
                results.append(out)

    if failed:
        print("FAILED", failed, flush=True)
        return 2
    results.sort(key=lambda d: str(d.get("date")))
    analysis = run_analysis(results)
    check = {
        "days": [d["date"] for d in results],
        "n": len(results),
        "DATA_STATUS": "HISTORICAL_DIAGNOSTIC",
        "NEW_GRID_TRUE_HOLDOUT": False,
        "failed": failed,
    }
    report = build_report(provenance=prov, inventory_check=check, analysis=analysis)
    paths = write_artifacts(report)
    print(report.get("verdict"), paths["report_json"], flush=True)
    print(
        f"PHASE_A={report.get('PHASE_A_VERDICT')} SUPPORT={report.get('HISTORICAL_10MIN_SUPPORT')} "
        f"MISSED={report.get('MISSED_OPPORTUNITY_EVIDENCE')}",
        flush=True,
    )
    print("STOP CLOCK_GRID unchanged. FUTURE_FORWARD_REQUIRED=true. submit/cancel/live=0/0/0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
