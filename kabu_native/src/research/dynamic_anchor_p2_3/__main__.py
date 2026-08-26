"""P2-3 runner. Research accounting only. No production changes. No retune. No new Dynamic."""
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

from research.dynamic_anchor_p2_1.inventory import build_inventory
from research.dynamic_anchor_p2_2.binding import (
    P1_REPORT,
    P2_1_REPORT,
    verify_entry_binding,
    verify_p2_1_shas,
)
from research.dynamic_anchor_p2_3 import FULL14, MAX_WORKERS
from research.dynamic_anchor_p2_3.publish import build_report, write_artifacts
from research.dynamic_anchor_p2_3.replay import replay_decomp_day

P2_2_REPORT = NATIVE / "results" / "research" / "dynamic_anchor_pnl_test_p2_2" / "report.json"


def _payload(row: dict) -> dict:
    return {
        "date": row["date"],
        "capture_path": row["capture_path"],
        "capture_class": row["capture_class"],
        "universe": row["universe_symbols"],
        "universe_source": row["universe_source"],
    }


def main() -> int:
    inv = build_inventory()
    entry_bind = verify_entry_binding()
    if not P2_1_REPORT.is_file():
        print("P2_3_BLOCKED NO_P2_1_REPORT", flush=True)
        return 1
    if not P1_REPORT.is_file():
        print("P2_3_BLOCKED NO_P1_REPORT", flush=True)
        return 1
    if not P2_2_REPORT.is_file():
        print("P2_3_BLOCKED NO_P2_2_REPORT", flush=True)
        return 1
    p21 = json.loads(P2_1_REPORT.read_text(encoding="utf-8"))
    p1 = json.loads(P1_REPORT.read_text(encoding="utf-8"))
    p2_2 = json.loads(P2_2_REPORT.read_text(encoding="utf-8"))
    sha_bind = verify_p2_1_shas(p21)
    print(
        f"P2-1 SHA trigger={sha_bind['P2_1_TRIGGER_SHA_MATCH']} confirm={sha_bind['P2_1_CONFIRM_SHA_MATCH']} "
        f"ENTRY_BINDING={entry_bind['CURRENT_ENTRY_BINDING']}",
        flush=True,
    )
    by_inv = {r["date"]: r for r in inv}
    elig = []
    for d in FULL14:
        r = by_inv.get(d)
        if not r or not r.get("replay_eligible") or not r.get("universe_symbols") or not r.get("capture_path"):
            print(f"P2_3_BLOCKED missing FULL day {d}", flush=True)
            return 1
        if r.get("capture_class") != "FULL":
            print(f"P2_3_BLOCKED {d} not FULL ({r.get('capture_class')})", flush=True)
            return 1
        elig.append(r)
    if entry_bind.get("CURRENT_ENTRY_BINDING") != "PASS":
        failed = ["ENTRY_BINDING_FAIL"]
        rep = build_report(
            inventory=inv, day_results=[], failed=failed, p1=p1, p2_2=p2_2,
            sha_bind=sha_bind, entry_bind=entry_bind,
        )
        paths = write_artifacts(rep)
        print(rep["verdict"], paths["report_json"], flush=True)
        return 1
    if not sha_bind.get("pass"):
        failed = ["P2_1_SHA_MISMATCH"]
        rep = build_report(
            inventory=inv, day_results=[], failed=failed, p1=p1, p2_2=p2_2,
            sha_bind=sha_bind, entry_bind=entry_bind,
        )
        paths = write_artifacts(rep)
        print(rep["verdict"], paths["report_json"], flush=True)
        return 1

    print(f"P2-3 FULL14 days={len(elig)} workers={MAX_WORKERS}", flush=True)
    results = []
    failed = []
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(replay_decomp_day, _payload(r)): r["date"] for r in elig}
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
                f"OK {day} conf={out.get('confirmed')} "
                f"dyn_tr={len(out.get('dyn_trades') or [])} "
                f"fix_tr={len(out.get('fixed_trades') or [])} "
                f"term={len(out.get('dyn_terminals') or [])} "
                f"unresolved={len(out.get('dyn_unresolved') or [])} "
                f"sec={out.get('elapsed_sec')}",
                flush=True,
            )
            results.append(out)
    results.sort(key=lambda d: d["date"])
    rep = build_report(
        inventory=inv,
        day_results=results,
        failed=failed,
        p1=p1,
        p2_2=p2_2,
        sha_bind=sha_bind,
        entry_bind=entry_bind,
    )
    paths = write_artifacts(rep)
    print(rep["verdict"], paths["report_json"], flush=True)
    print(
        f"FUNNEL_ACCOUNTING={rep['FUNNEL_ACCOUNTING']} "
        f"PRIMARY={rep['PRIMARY_EVIDENCE_SUPPORTED_DRIVER']} "
        f"SECONDARY={rep['SECONDARY_DRIVERS']}",
        flush=True,
    )
    return 0 if rep["verdict"] == "P2_3_FAILURE_DECOMPOSITION_COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
