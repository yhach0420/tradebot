"""Offline runner. Paper not started. Runtime not changed."""
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

from research.anchor_timing_robustness import MAX_WORKERS
from research.anchor_timing_robustness.grid import canonical_grid, hm_label, split_am_pm
from research.anchor_timing_robustness.inventory import build_inventory
from research.anchor_timing_robustness.provenance import collect_provenance
from research.anchor_timing_robustness.publish import OUT, build_report, write_artifacts
from research.anchor_timing_robustness.replay import process_day

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
    if body.get("ok") and body.get("date") == day:
        return body
    return None


def _save_cache(day: str, body: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    _cache_path(day).write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")


def main() -> int:
    print("SAFETY submit/cancel/live=0/0/0 offline_research_only", flush=True)
    print("RUNTIME_CHANGED=false STRATEGY_CHANGED=false PAPER=not_started", flush=True)

    prov = collect_provenance()
    am, pm = split_am_pm(canonical_grid())
    print("CANONICAL_AM_ANCHORS:", am, flush=True)
    print("CANONICAL_PM_ANCHORS:", pm, flush=True)
    print("SOURCE_FILE:", prov.get("SOURCE_FILE"), flush=True)
    print("SOURCE_SHA:", prov.get("SOURCE_SHA"), flush=True)
    print("ANCHOR_SHA:", prov.get("ANCHOR_SHA"), flush=True)
    print("TRUE_HOLDOUT_AVAILABLE_CONTRACT:", prov.get("TRUE_HOLDOUT_AVAILABLE"), flush=True)

    if not prov.get("runtime_clock_equals_import"):
        print("BLOCKED CLOCK_GRID import mismatch", flush=True)
        return 2
    if not prov.get("x32_matches_runtime"):
        print("WARN runtime CLOCK_GRID != X32 CLOCK_POINTS_HM", flush=True)

    inv = build_inventory()
    elig = [r for r in inv if r.get("replay_eligible") and r.get("universe_symbols") and r.get("capture_path")]
    print(
        f"INVENTORY n={len(inv)} eligible={len(elig)} "
        f"dev={sum(1 for r in elig if r.get('period')=='DEVELOPMENT')} "
        f"holdout={sum(1 for r in elig if r.get('period')=='POST_FREEZE_HOLDOUT')}",
        flush=True,
    )
    for r in inv:
        flag = "USE" if r.get("replay_eligible") else "SKIP"
        print(
            f"  {flag} {r['date']} {r.get('period')} class={r.get('capture_class')} "
            f"uni={r.get('universe_n')} {r.get('exclusion_reason')}",
            flush=True,
        )

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
                print(
                    f"OK {day} leak={out.get('snapshot_future_leak')} "
                    f"err={out.get('shift_errors')} sec={out.get('elapsed_sec')}",
                    flush=True,
                )
                results.append(out)

    results.sort(key=lambda d: str(d.get("date")))
    report = build_report(provenance=prov, inventory=inv, days=results, failed=failed)
    paths = write_artifacts(report)
    print(f"CACHE_KEPT {CACHE} (needed by economic-sensitivity decomposition)", flush=True)
    print(report.get("verdict"), paths["report_json"], flush=True)
    print(
        f"SELECTION_STABLE={report.get('SELECTION_STABLE')} "
        f"ECONOMICS_STABLE={report.get('ECONOMICS_STABLE')} "
        f"EXACT_TIME={report.get('EXACT_TIME_DEPENDENCE')} "
        f"OVERFIT={report.get('OVERFIT_CONCERN')}",
        flush=True,
    )
    print("STOP CLOCK_GRID unchanged. submit/cancel/live=0/0/0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
