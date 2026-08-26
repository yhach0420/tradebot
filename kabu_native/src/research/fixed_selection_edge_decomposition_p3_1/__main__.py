"""P3-1 runner. Execution vs directional decomposition. Clock frozen. No new strategy."""
from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.dynamic_anchor_p2_1.inventory import build_inventory
from research.fixed_selection_edge_decomposition_p3_1 import FULL14, MAX_WORKERS
from research.fixed_selection_edge_decomposition_p3_1.publish import build_report, write_artifacts
from research.fixed_selection_edge_decomposition_p3_1.replay import replay_day

P3_0R = NATIVE / "results" / "research" / "fixed_selection_diagnostic_reconcile_p3_0r" / "report.json"


def main() -> int:
    if not P3_0R.is_file():
        rep = build_report(rows=[], failed=["NO_P3_0R"], blocked=True, blocked_reason="NO_P3_0R_REPORT")
        paths = write_artifacts(rep)
        print("P3_1_BLOCKED NO_P3_0R_REPORT", paths["report_json"], flush=True)
        return 1

    inv = {r["date"]: r for r in build_inventory()}
    jobs = []
    for day in FULL14:
        row = inv.get(day)
        if not row or not row.get("capture_path") or not row.get("universe_symbols"):
            rep = build_report(rows=[], failed=[day], blocked=True, blocked_reason=f"MISSING_{day}")
            write_artifacts(rep)
            print("P3_1_BLOCKED MISSING", day, flush=True)
            return 1
        jobs.append({"date": day, "capture_path": row["capture_path"], "universe": row["universe_symbols"]})

    print(f"P3-1 days={len(jobs)} workers={MAX_WORKERS}", flush=True)
    results = []
    failed = []
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(replay_day, j): j["date"] for j in jobs}
        for fut in as_completed(futs):
            day = futs[fut]
            try:
                out = fut.result()
            except Exception as exc:
                print(f"FAIL {day} {exc!r}", flush=True)
                failed.append(day)
                continue
            if not out.get("ok"):
                print(f"FAIL {day} {out.get('blocker')}", flush=True)
                failed.append(day)
                continue
            sel_n = sum(1 for r in out.get("rows") or [] if r.get("selected"))
            fill_n = sum(1 for r in out.get("rows") or [] if r.get("selected") and r.get("independent_filled"))
            print(
                f"OK {day} elig={len(out.get('rows') or [])} sel={sel_n} sel_fill={fill_n} sec={out.get('elapsed_sec')}",
                flush=True,
            )
            results.append(out)

    if failed or len(results) != 14:
        rep = build_report(rows=[], failed=failed, blocked=True, blocked_reason="DAY_REPLAY_FAILED")
        paths = write_artifacts(rep)
        print("P3_1_BLOCKED DAY_REPLAY_FAILED", paths["report_json"], flush=True)
        return 1

    rows = [r for d in results for r in (d.get("rows") or [])]
    rep = build_report(rows=rows, failed=[], blocked=False)
    paths = write_artifacts(rep)
    print(rep["verdict"], paths["report_json"], flush=True)
    print(
        f"EXEC={rep['EXECUTION_EDGE']} DIR={rep['DIRECTIONAL_EDGE']} EDGE={rep['SELECTION_EDGE']}",
        flush=True,
    )
    return 0 if not str(rep["verdict"]).endswith("BLOCKED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
