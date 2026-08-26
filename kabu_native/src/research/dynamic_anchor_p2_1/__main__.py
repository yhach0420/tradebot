"""P2-1 runner: Capture → T1+C1 events. No PnL / no trades."""
from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from research.dynamic_anchor_p2_1 import MAX_WORKERS
from research.dynamic_anchor_p2_1.inventory import build_inventory, process_day
from research.dynamic_anchor_p2_1.publish import (
    CONFIRM_SHA_KEYS,
    TRIGGER_SHA_KEYS,
    build_report,
    ledger_sha,
    write_artifacts,
)


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
    elig = [r for r in inv if r.get("replay_eligible") and r.get("universe_symbols") and r.get("capture_path")]
    print(f"P2-1 inventory days={len(inv)} eligible={len(elig)}", flush=True)
    results = []
    failed = []
    if not elig:
        rep = build_report(inventory=inv, day_results=[], failed=["NO_ELIGIBLE_DAYS"], det={"pass": False})
        write_artifacts(rep)
        print("BLOCKED no eligible days", flush=True)
        return 1
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(process_day, _payload(r)): r["date"] for r in elig}
        for fut in as_completed(futs):
            day = futs[fut]
            try:
                out = fut.result()
            except Exception as exc:
                print(f"FAIL {day} {exc!r}", flush=True)
                failed.append(day)
                continue
            print(
                f"OK {day} class={out.get('capture_class')} trig={out.get('false_to_true_triggers')} "
                f"conf={sum(1 for c in out.get('confirms') or [] if c.get('status')=='CONFIRMED')} "
                f"leak_xs={out.get('cross_section_future_leak_count')}",
                flush=True,
            )
            results.append(out)
    results.sort(key=lambda d: d["date"])

    full = [d for d in results if d.get("capture_class") == "FULL"]
    max_full = max(full, key=lambda d: int(d.get("false_to_true_triggers") or 0)) if full else None
    det_days = []
    if any(d["date"] == "20260820" for d in results):
        det_days.append("20260820")
    if max_full is not None and max_full["date"] not in det_days:
        det_days.append(max_full["date"])
    elif max_full is not None and max_full["date"] == "20260820" and len(full) > 1:
        rest = [d for d in full if d["date"] != "20260820"]
        det_days.append(max(rest, key=lambda d: int(d.get("false_to_true_triggers") or 0))["date"])

    det = {"pass": False, "days": det_days, "trig_sha1": None, "trig_sha2": None, "conf_sha1": None, "conf_sha2": None}
    if len(det_days) >= 1:
        by_date = {r["date"]: r for r in elig}
        run1_trigs, run1_confs, run2_trigs, run2_confs = [], [], [], []
        ok_det = True
        for d in det_days:
            src = next(x for x in results if x["date"] == d)
            run1_trigs.extend(src.get("triggers") or [])
            run1_confs.extend(src.get("confirms") or [])
            try:
                again = process_day(_payload(by_date[d]))
            except Exception as exc:
                print(f"DET FAIL {d} {exc!r}", flush=True)
                ok_det = False
                continue
            run2_trigs.extend(again.get("triggers") or [])
            run2_confs.extend(again.get("confirms") or [])
        det["trig_sha1"] = ledger_sha(run1_trigs, TRIGGER_SHA_KEYS)
        det["trig_sha2"] = ledger_sha(run2_trigs, TRIGGER_SHA_KEYS)
        det["conf_sha1"] = ledger_sha(run1_confs, CONFIRM_SHA_KEYS)
        det["conf_sha2"] = ledger_sha(run2_confs, CONFIRM_SHA_KEYS)
        det["pass"] = ok_det and det["trig_sha1"] == det["trig_sha2"] and det["conf_sha1"] == det["conf_sha2"]
        print(f"DET days={det_days} pass={det['pass']}", flush=True)

    rep = build_report(inventory=inv, day_results=results, failed=failed, det=det)
    paths = write_artifacts(rep)
    print(rep["verdict"], paths["report_json"], flush=True)
    return 0 if rep["verdict"] == "P2_1_DYNAMIC_ANCHOR_EVENT_VALIDATION_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
