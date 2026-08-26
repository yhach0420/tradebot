"""P3-2 runner. Post-fill execution price vs MID direction. No new strategy."""
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
from research.post_fill_edge_decomposition_p3_2 import FULL14, MAX_WORKERS
from research.post_fill_edge_decomposition_p3_2.publish import build_report, write_artifacts
from research.post_fill_edge_decomposition_p3_2.replay import replay_day

P3_1 = NATIVE / "results" / "research" / "fixed_selection_edge_decomposition_p3_1" / "report.json"


def main() -> int:
    if not P3_1.is_file():
        rep = build_report(
            rows=[],
            leak_fill=0,
            leak_mid=0,
            leak_cp=0,
            identity_n=0,
            identity_fail=0,
            harvest_selected_n=0,
            harvest_eligible_n=0,
            failed=["NO_P3_1"],
            blocked=True,
            blocked_reason="NO_P3_1_REPORT",
        )
        paths = write_artifacts(rep)
        print("P3_2_BLOCKED NO_P3_1_REPORT", paths["report_json"], flush=True)
        return 1

    inv = {r["date"]: r for r in build_inventory()}
    jobs = []
    for day in FULL14:
        row = inv.get(day)
        if not row or not row.get("capture_path") or not row.get("universe_symbols"):
            rep = build_report(
                rows=[],
                leak_fill=0,
                leak_mid=0,
                leak_cp=0,
                identity_n=0,
                identity_fail=0,
                harvest_selected_n=0,
                harvest_eligible_n=0,
                failed=[day],
                blocked=True,
                blocked_reason=f"MISSING_{day}",
            )
            write_artifacts(rep)
            print("P3_2_BLOCKED MISSING", day, flush=True)
            return 1
        jobs.append({"date": day, "capture_path": row["capture_path"], "universe": row["universe_symbols"]})

    print(f"P3-2 days={len(jobs)} workers={MAX_WORKERS}", flush=True)
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
            print(
                f"OK {day} fills={len(out.get('rows') or [])} "
                f"sel={out.get('selected_fill_n')} nos={out.get('not_selected_fill_n')} "
                f"ident_fail={out.get('identity_fail')} leak={out.get('leak_fill')}/{out.get('leak_mid')}/{out.get('leak_cp')} "
                f"sec={out.get('elapsed_sec')}",
                flush=True,
            )
            results.append(out)

    if failed or len(results) != 14:
        rep = build_report(
            rows=[],
            leak_fill=0,
            leak_mid=0,
            leak_cp=0,
            identity_n=0,
            identity_fail=0,
            harvest_selected_n=0,
            harvest_eligible_n=0,
            failed=failed,
            blocked=True,
            blocked_reason="DAY_REPLAY_FAILED",
        )
        paths = write_artifacts(rep)
        print("P3_2_BLOCKED DAY_REPLAY_FAILED", paths["report_json"], flush=True)
        return 1

    rows = [r for d in results for r in (d.get("rows") or [])]
    rep = build_report(
        rows=rows,
        leak_fill=sum(int(d.get("leak_fill") or 0) for d in results),
        leak_mid=sum(int(d.get("leak_mid") or 0) for d in results),
        leak_cp=sum(int(d.get("leak_cp") or 0) for d in results),
        identity_n=sum(int(d.get("identity_n") or 0) for d in results),
        identity_fail=sum(int(d.get("identity_fail") or 0) for d in results),
        harvest_selected_n=sum(int(d.get("harvest_selected_n") or 0) for d in results),
        harvest_eligible_n=sum(int(d.get("harvest_eligible_n") or 0) for d in results),
        failed=[],
        blocked=False,
    )
    paths = write_artifacts(rep)
    print(rep["verdict"], paths["report_json"], flush=True)
    print(
        f"DIR={rep['POST_FILL_DIRECTION']} MECH={rep['POST_FILL_MECHANISM']} "
        f"IDENT={rep['DECOMPOSITION_IDENTITY']} LEAK={rep['FUTURE_LEAK']}",
        flush=True,
    )
    return 0 if not str(rep["verdict"]).endswith("BLOCKED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
