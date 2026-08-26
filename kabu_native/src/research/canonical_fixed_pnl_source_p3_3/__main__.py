"""P3-3 runner. Canonical Fixed PnL source only. No new strategy."""
from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.canonical_fixed_pnl_source_p3_3 import (
    FULL14,
    MAX_WORKERS,
    P1_PF,
    P1_PNL,
    P1_REF_PNL,
    P1_REF_TRADES,
    P1_TRADES,
)
from research.canonical_fixed_pnl_source_p3_3.ledger import day_table, pnl
from research.canonical_fixed_pnl_source_p3_3.publish import build_report, write_artifacts
from research.canonical_fixed_pnl_source_p3_3.replay import replay_day
from research.dynamic_anchor_p2_1.inventory import build_inventory
from run_p0_3_exact_runtime_replay_20260820 import _pf

P1_REPORT = NATIVE / "results" / "research" / "current_runtime_full_capture_recalc_p1" / "report.json"


def _recon(primary: list[dict], p1: dict, p1_daily: list[dict]) -> dict:
    n = len(primary)
    sp = sum(pnl(t) for t in primary)
    pf = _pf([pnl(t) for t in primary])
    days = day_table(primary, p1_daily)
    full_days = [d for d in days if str(d.get("date")) in set(FULL14)]
    sha_ok = all(bool(d.get("sha_match")) for d in full_days) and len(full_days) == 14
    cnt_ok = all(bool(d.get("count_match")) for d in full_days) and len(full_days) == 14
    pnl_ok = all(bool(d.get("pnl_match")) for d in full_days) and len(full_days) == 14
    n_ok = n == P1_TRADES
    sum_ok = abs(sp - P1_PNL) < 0.51
    pf_ok = pf is not None and abs(float(pf) - float(P1_PF)) < 1e-9
    p1_days = [str(x) for x in ((p1.get("PRIMARY_FULL") or {}).get("day_list") or [])]
    days_ok = set(p1_days) == set(FULL14)
    reasons = []
    if not n_ok:
        reasons.append(f"trade_n={n}!={P1_TRADES}")
    if not sum_ok:
        reasons.append(f"sum_pnl={sp}!={P1_PNL}")
    if not pf_ok:
        reasons.append(f"PF={pf}!={P1_PF}")
    if not days_ok:
        reasons.append("PRIMARY_FULL_day_list_mismatch")
    if not sha_ok:
        reasons.append("daily_ledger_sha_mismatch")
    if not cnt_ok:
        reasons.append("daily_trade_count_mismatch")
    if not pnl_ok:
        reasons.append("daily_pnl_mismatch")
    return {
        "pass": not reasons,
        "trade_n": n,
        "sum_pnl": round(sp, 2),
        "PF": pf if pf != float("inf") else "Infinity",
        "daily": [
            {
                "date": d.get("date"),
                "trades": d.get("trades"),
                "pnl": d.get("pnl"),
                "ledger_sha": d.get("ledger_sha"),
                "p1_ledger_sha": d.get("p1_ledger_sha"),
                "sha_match": d.get("sha_match"),
                "count_match": d.get("count_match"),
                "pnl_match": d.get("pnl_match"),
            }
            for d in full_days
        ],
        "reasons": reasons,
    }


def _blocked(primary, p1_daily, ref, reason, failed, recon) -> int:
    rep = build_report(
        primary=primary,
        path_rows=[],
        p1_daily=p1_daily,
        ref_trades=ref,
        leak_fill=0,
        leak_mid=0,
        leak_path=0,
        leak_bid=0,
        harvest_joined_n=0,
        failed=failed,
        blocked=True,
        blocked_reason=reason,
        recon=recon,
    )
    paths = write_artifacts(rep)
    print("P3_3_BLOCKED", reason, paths["report_json"], flush=True)
    return 1


def main() -> int:
    if not P1_REPORT.is_file():
        recon = {"pass": False, "reasons": ["NO_P1_REPORT"]}
        return _blocked([], [], None, "NO_P1_REPORT", ["NO_P1"], recon)

    p1 = json.loads(P1_REPORT.read_text(encoding="utf-8"))
    all_trades = list(p1.get("trades") or [])
    p1_daily = list(p1.get("daily") or [])
    primary = [t for t in all_trades if str(t.get("date")) in set(FULL14)]
    ref_days = [str(x) for x in ((p1.get("REFERENCE_ALL_USABLE") or {}).get("day_list") or [])]
    ref = [t for t in all_trades if str(t.get("date")) in set(ref_days)] if ref_days else None
    if ref is not None:
        nref = len(ref)
        spref = sum(pnl(t) for t in ref)
        if nref != P1_REF_TRADES or abs(spref - P1_REF_PNL) >= 0.51:
            # still pass the list through publish; it will mark available false on reconcile fail
            pass

    recon = _recon(primary, p1, p1_daily)
    print(
        f"P3-3 reconcile pass={recon['pass']} n={recon['trade_n']} pnl={recon['sum_pnl']} "
        f"reasons={recon['reasons']}",
        flush=True,
    )
    if not recon["pass"]:
        return _blocked(primary, p1_daily, ref, "P3_3_BLOCKED", recon["reasons"], recon)

    inv = {r["date"]: r for r in build_inventory()}
    jobs = []
    for day in FULL14:
        row = inv.get(day)
        if not row or not row.get("capture_path") or not row.get("universe_symbols"):
            return _blocked(primary, p1_daily, ref, f"MISSING_{day}", [day], recon)
        jobs.append(
            {
                "date": day,
                "capture_path": row["capture_path"],
                "universe": row["universe_symbols"],
                "canonical_trades": [t for t in primary if str(t.get("date")) == day],
            }
        )

    print(f"P3-3 days={len(jobs)} workers={MAX_WORKERS} canonical={len(primary)}", flush=True)
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
                f"OK {day} path={len(out.get('rows') or [])}/{out.get('n_canonical')} "
                f"harvest={out.get('harvest_joined_n')} "
                f"leak={out.get('leak_fill')}/{out.get('leak_mid')}/{out.get('leak_path')}/{out.get('leak_bid')} "
                f"sec={out.get('elapsed_sec')}",
                flush=True,
            )
            results.append(out)

    if failed or len(results) != 14:
        return _blocked(primary, p1_daily, ref, "DAY_REPLAY_FAILED", failed, recon)

    path_rows = [r for d in results for r in (d.get("rows") or [])]
    rep = build_report(
        primary=primary,
        path_rows=path_rows,
        p1_daily=p1_daily,
        ref_trades=ref,
        leak_fill=sum(int(d.get("leak_fill") or 0) for d in results),
        leak_mid=sum(int(d.get("leak_mid") or 0) for d in results),
        leak_path=sum(int(d.get("leak_path") or 0) for d in results),
        leak_bid=sum(int(d.get("leak_bid") or 0) for d in results),
        harvest_joined_n=sum(int(d.get("harvest_joined_n") or 0) for d in results),
        failed=[],
        blocked=False,
        blocked_reason=None,
        recon=recon,
    )
    paths = write_artifacts(rep)
    print(rep["verdict"], paths["report_json"], flush=True)
    print(
        f"RECON={rep['CANONICAL_RECONCILE']} SRC={rep['PRIMARY_PNL_SOURCE']} "
        f"SEC={rep['SECONDARY_PNL_SOURCES']} LEAK={rep['FUTURE_LEAK']}",
        flush=True,
    )
    return 0 if not str(rep["verdict"]).endswith("BLOCKED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
