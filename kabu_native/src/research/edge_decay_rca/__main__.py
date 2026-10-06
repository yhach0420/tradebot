"""Offline edge-decay RCA. Paper not started. Runtime / CLOCK_GRID not changed."""
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

from research.edge_decay_rca import HISTORICAL_DAYS, MAX_WORKERS
from research.edge_decay_rca.analyze import run_all
from research.edge_decay_rca.load import (
    attach_isolated_to_portfolio,
    load_feature_rows,
    load_isolated_reference,
    load_portfolios,
    load_rank_rows,
    load_universes,
)
from research.edge_decay_rca.publish import OUT, build_report, write_artifacts
from research.edge_decay_rca.replay import process_day
from research.anchor_timing_robustness.inventory import build_inventory
from research.anchor_timing_robustness.provenance import collect_provenance
from small_paper.v1r_native_entry_live import FEATURE_ORDER

CACHE = OUT / "_work_cache"


def _cache_path(day: str) -> Path:
    return CACHE / f"{day}.json"


def _feat_complete(body: dict) -> bool:
    rows = body.get("feature_rows") or []
    if not body.get("ok") or not rows:
        return False
    sample = rows[0]
    return all(f in sample for f in FEATURE_ORDER)


def _load_cache(day: str) -> dict | None:
    fp = _cache_path(day)
    if not fp.is_file():
        return None
    try:
        body = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    if body.get("date") == day and _feat_complete(body):
        return body
    return None


def _save_cache(day: str, body: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    _cache_path(day).write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")


def main() -> int:
    print("SAFETY submit/cancel/live=0/0/0 offline_research_only", flush=True)
    print("CLOCK_GRID unchanged. No optimization. RCA only.", flush=True)
    prov = collect_provenance()
    print("SOURCE_FILE:", prov.get("SOURCE_FILE"), flush=True)
    print("SOURCE_SHA:", prov.get("SOURCE_SHA"), flush=True)

    inv = build_inventory()
    elig = [
        r
        for r in inv
        if r.get("replay_eligible")
        and r.get("universe_symbols")
        and r.get("capture_path")
        and r["date"] in HISTORICAL_DAYS
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
    failed: list[str] = []
    pending = []
    for j in jobs:
        cached = _load_cache(j["date"])
        if cached is not None:
            print(f"CACHE {j['date']} n={len(cached.get('feature_rows') or [])} sec={cached.get('elapsed_sec')}", flush=True)
        else:
            pending.append(j)

    if pending:
        print(f"RUN feature days={len(pending)} workers={MAX_WORKERS}", flush=True)
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
                print(f"OK {day} leak={out.get('snapshot_future_leak')} n={len(out.get('feature_rows') or [])} sec={out.get('elapsed_sec')}", flush=True)

    if failed:
        print("FAILED", failed, flush=True)
        return 2

    ports = load_portfolios()
    iso = load_isolated_reference()
    attach_isolated_to_portfolio(ports["REFERENCE"], iso)
    attach_isolated_to_portfolio(ports["UNIFORM10"], iso)
    rank_rows = load_rank_rows()
    feat_rows = load_feature_rows(CACHE)
    universes = load_universes()
    print(
        f"LOADED ref={len(ports['REFERENCE'])} uni={len(ports['UNIFORM10'])} "
        f"iso={len(iso)} rank_rows={len(rank_rows)} feat={len(feat_rows)}",
        flush=True,
    )
    analysis = run_all(
        ref=ports["REFERENCE"],
        uni=ports["UNIFORM10"],
        admits=ports["admits"],
        iso=iso,
        rank_rows=rank_rows,
        feat_rows=feat_rows,
        universes=universes,
        daily_pack=ports["daily_pack"],
    )
    check = {
        "days": used_dates,
        "n": len(used_dates),
        "failed": failed,
        "feature_rows": len(feat_rows),
        "PRIMARY_BASELINE": "CANONICAL_CLOCK_GRID_REFERENCE",
    }
    report = build_report(provenance=prov, inventory_check=check, analysis=analysis)
    paths = write_artifacts(report)
    print(report.get("verdict"), paths["report_json"], flush=True)
    print(
        f"DEV_PNL={report.get('DEV_PNL')} POST_PNL={report.get('POST_PNL')} "
        f"PRIMARY={report.get('PRIMARY_ROOT_CAUSE')} SECONDARY={report.get('SECONDARY_ROOT_CAUSE')}",
        flush=True,
    )
    print("STOP CLOCK_GRID unchanged. submit/cancel/live=0/0/0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
