"""P2-2 runner. Research only. No production changes. No retune."""
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
from research.dynamic_anchor_p2_2 import MAX_WORKERS, P2_1_CONFIRM_SHA, P2_1_DET_DAYS, P2_1_TRIGGER_SHA
from research.dynamic_anchor_p2_2.binding import (
    P2_1_REPORT,
    instream_ledger_sha,
    verify_entry_binding,
    verify_p2_1_shas,
)
from research.dynamic_anchor_p2_2.publish import build_report, trade_ledger_sha, write_artifacts
from research.dynamic_anchor_p2_2.replay import replay_dynamic_day


def _payload(row: dict) -> dict:
    return {
        "date": row["date"],
        "capture_path": row["capture_path"],
        "capture_class": row["capture_class"],
        "universe": row["universe_symbols"],
        "universe_source": row["universe_source"],
    }


def _blocked(inv, sha_bind, entry_bind, failed, reason: str) -> int:
    det = {"pass": False, "days": [], "sha1": None, "sha2": None, "reason": reason}
    instream = {"pass": False, "trig": None, "conf": None, "reason": reason}
    rep = build_report(
        inventory=inv,
        day_results=[],
        failed=failed or [reason],
        sha_bind=sha_bind,
        entry_bind=entry_bind,
        det=det,
        instream_sha=instream,
    )
    rep["verdict"] = "P2_2_BLOCKED"
    paths = write_artifacts(rep)
    print("P2_2_BLOCKED", reason, paths["report_json"], flush=True)
    return 1


def main() -> int:
    inv = build_inventory()
    if not P2_1_REPORT.is_file():
        return _blocked(inv, {"pass": False, "P2_1_TRIGGER_SHA_MATCH": "FAIL", "P2_1_CONFIRM_SHA_MATCH": "FAIL"}, verify_entry_binding(), ["NO_P2_1_REPORT"], "NO_P2_1_REPORT")
    p21 = json.loads(P2_1_REPORT.read_text(encoding="utf-8"))
    sha_bind = verify_p2_1_shas(p21)
    entry_bind = verify_entry_binding()
    print(
        f"P2-1 SHA trigger={sha_bind['P2_1_TRIGGER_SHA_MATCH']} confirm={sha_bind['P2_1_CONFIRM_SHA_MATCH']} "
        f"ENTRY_BINDING={entry_bind['CURRENT_ENTRY_BINDING']}",
        flush=True,
    )
    if not sha_bind.get("pass"):
        return _blocked(inv, sha_bind, entry_bind, ["P2_1_SHA_MISMATCH"], "P2_1_SHA_MISMATCH")
    if entry_bind.get("CURRENT_ENTRY_BINDING") != "PASS":
        return _blocked(inv, sha_bind, entry_bind, ["ENTRY_BINDING_FAIL"], "ENTRY_BINDING_FAIL")

    elig = [r for r in inv if r.get("replay_eligible") and r.get("universe_symbols") and r.get("capture_path")]
    print(f"P2-2 inventory eligible={len(elig)}", flush=True)
    results = []
    failed = []
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(replay_dynamic_day, _payload(r)): r["date"] for r in elig}
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
                f"OK {day} class={out.get('capture_class')} trig={out.get('false_to_true_triggers')} "
                f"conf={out.get('confirmed')} trades={out.get('trade_n')} pnl={out.get('pnl')} "
                f"leak={out.get('snapshot_future_leak')}",
                flush=True,
            )
            results.append(out)
    results.sort(key=lambda d: d["date"])

    trigs, confs = [], []
    for d in P2_1_DET_DAYS:
        src = next((x for x in results if x["date"] == d), None)
        if src is None:
            instream = {"pass": False, "trig": None, "conf": None, "reason": f"missing {d}"}
            break
        trigs.extend(src.get("triggers") or [])
        confs.extend(src.get("confirms") or [])
    else:
        tsha, csha = instream_ledger_sha(trigs, confs)
        instream = {
            "pass": tsha == P2_1_TRIGGER_SHA and csha == P2_1_CONFIRM_SHA,
            "trig": tsha,
            "conf": csha,
            "expected_trig": P2_1_TRIGGER_SHA,
            "expected_conf": P2_1_CONFIRM_SHA,
        }
        print(f"INSTREAM T1 SHA match={instream['pass']}", flush=True)

    full = [d for d in results if d.get("capture_class") == "FULL"]
    det_days = ["20260820"] if any(d["date"] == "20260820" for d in results) else []
    if full:
        max_tr = max(full, key=lambda d: int(d.get("trade_n") or 0))
        if max_tr["date"] not in det_days:
            det_days.append(max_tr["date"])
        elif len(full) > 1:
            rest = [d for d in full if d["date"] != "20260820"]
            det_days.append(max(rest, key=lambda d: int(d.get("trade_n") or 0))["date"])
    by_inv = {r["date"]: r for r in elig}
    run1, run2 = [], []
    ok_det = True
    for d in det_days:
        src = next(x for x in results if x["date"] == d)
        run1.extend(src.get("trades") or [])
        try:
            again = replay_dynamic_day(_payload(by_inv[d]))
        except Exception as exc:
            print(f"DET FAIL {d} {exc!r}", flush=True)
            ok_det = False
            continue
        if not again.get("ok"):
            print(f"DET FAIL {d} {again.get('blocker')}", flush=True)
            ok_det = False
            continue
        run2.extend(again.get("trades") or [])
        print(f"DET {d} trades={again.get('trade_n')} pnl={again.get('pnl')}", flush=True)
    sha1 = trade_ledger_sha(run1) if run1 else None
    sha2 = trade_ledger_sha(run2) if run2 else None
    det = {
        "pass": bool(ok_det and sha1 and sha1 == sha2),
        "days": det_days,
        "sha1": sha1,
        "sha2": sha2,
    }
    print(f"DET days={det_days} pass={det['pass']}", flush=True)

    if not instream.get("pass"):
        failed = list(failed) + ["INSTREAM_T1_SHA_MISMATCH"]
    rep = build_report(
        inventory=inv,
        day_results=results,
        failed=failed,
        sha_bind=sha_bind,
        entry_bind=entry_bind,
        det=det,
        instream_sha=instream,
    )
    paths = write_artifacts(rep)
    print(rep["verdict"], paths["report_json"], flush=True)
    return 0 if not str(rep["verdict"]).endswith("BLOCKED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
