"""P2-4B runner. Frozen TRAIL10 one-shot reused-history test. No retune. No production changes."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.dynamic_anchor_p2_1.inventory import build_inventory
from research.dynamic_anchor_p2_2.binding import P1_REPORT
from research.dynamic_anchor_p2_2.publish import trade_ledger_sha
from research.trailing10_full_history_p2_4b import MAX_WORKERS, VERDICT_BLOCKED
from research.trailing10_full_history_p2_4b.binding import verify_bindings
from research.trailing10_full_history_p2_4b.publish import (
    P2_2_REPORT,
    P2_3_REPORT,
    build_report,
    write_artifacts,
)
from research.trailing10_full_history_p2_4b.replay import replay_trail10_day


def _payload(row: dict, fixed_by_day: dict) -> dict:
    return {
        "date": row["date"],
        "capture_path": row["capture_path"],
        "capture_class": row["capture_class"],
        "universe": row["universe_symbols"],
        "universe_source": row["universe_source"],
        "fixed_trades": fixed_by_day.get(row["date"]) or [],
    }


def _blocked(inv, bind, failed, reason: str, p1, p2_2, p2_3) -> int:
    det = {"pass": False, "days": [], "sha1": None, "sha2": None, "reason": reason}
    rep = build_report(
        inventory=inv,
        day_results=[],
        failed=failed or [reason],
        bind=bind,
        det=det,
        p1=p1,
        p2_2=p2_2,
        p2_3=p2_3,
    )
    rep["verdict"] = VERDICT_BLOCKED
    paths = write_artifacts(rep)
    print("P2_4B_BLOCKED", reason, paths["report_json"], flush=True)
    return 1


def main() -> int:
    inv = build_inventory()
    bind = verify_bindings()
    print(
        f"SPEC_SHA={bind.get('SPEC_SHA_MATCH')} IMPL_SHA={bind.get('IMPLEMENTATION_SHA_MATCH')} "
        f"ENTRY_BINDING={bind.get('CURRENT_ENTRY_BINDING')}",
        flush=True,
    )
    p1 = json.loads(P1_REPORT.read_text(encoding="utf-8")) if P1_REPORT.is_file() else {}
    p2_2 = json.loads(P2_2_REPORT.read_text(encoding="utf-8")) if P2_2_REPORT.is_file() else {}
    p2_3 = json.loads(P2_3_REPORT.read_text(encoding="utf-8")) if P2_3_REPORT.is_file() else {}
    if not P1_REPORT.is_file():
        return _blocked(inv, bind, ["NO_P1_REPORT"], "NO_P1_REPORT", p1, p2_2, p2_3)
    if not P2_2_REPORT.is_file():
        return _blocked(inv, bind, ["NO_P2_2_REPORT"], "NO_P2_2_REPORT", p1, p2_2, p2_3)
    if not bind.get("pass"):
        return _blocked(inv, bind, ["SPEC_OR_ENTRY_BINDING_FAIL"], "SPEC_OR_ENTRY_BINDING_FAIL", p1, p2_2, p2_3)

    fixed_by_day: dict[str, list] = defaultdict(list)
    for t in p1.get("trades") or []:
        fixed_by_day[str(t.get("date"))].append(t)

    elig = [r for r in inv if r.get("replay_eligible") and r.get("universe_symbols") and r.get("capture_path")]
    print(f"P2-4B inventory eligible={len(elig)} workers={MAX_WORKERS}", flush=True)
    results = []
    failed = []
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(replay_trail10_day, _payload(r, fixed_by_day)): r["date"] for r in elig}
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
                f"OK {day} class={out.get('capture_class')} anchors={out.get('false_to_true_anchors')} "
                f"adm={out.get('admitted')} fills={out.get('fills')} trades={out.get('trade_n')} "
                f"pnl={out.get('pnl')} leak={out.get('snapshot_future_leak')} "
                f"sec={out.get('elapsed_sec')}",
                flush=True,
            )
            results.append(out)
    results.sort(key=lambda d: d["date"])

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
        src = next((x for x in results if x["date"] == d), None)
        if src is None:
            ok_det = False
            continue
        run1.extend(src.get("trades") or [])
        try:
            again = replay_trail10_day(_payload(by_inv[d], fixed_by_day))
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

    rep = build_report(
        inventory=inv,
        day_results=results,
        failed=failed,
        bind=bind,
        det=det,
        p1=p1,
        p2_2=p2_2,
        p2_3=p2_3,
    )
    paths = write_artifacts(rep)
    print(rep["verdict"], paths["report_json"], flush=True)
    t = rep["PRIMARY_FULL"]["TRAIL10"]
    print(
        f"H1={rep['H1_TRIGGER_COVERAGE']['H1_SUPPORTED']} "
        f"H2={rep['H2_EXECUTION']['H2_SUPPORTED']} "
        f"H3={rep['H3_PERFORMANCE_RECOVERY']['H3_SUPPORTED']} "
        f"TRAIL10 trades={t['trades']} pnl={t['pnl']} PF={t['PF']}",
        flush=True,
    )
    return 0 if not str(rep["verdict"]).endswith("BLOCKED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
