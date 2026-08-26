"""P4-2 runner. Path decomposition only. No Gate / no threshold / no PnL replay."""
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

from research.canonical_fixed_pnl_source_p3_3.ledger import day_table, pnl
from research.dynamic_anchor_p2_1.inventory import build_inventory
from research.mid_hold_recovery_failure_path_p4_2 import FULL14, MAX_WORKERS, P1_PF, P1_PNL, P1_TRADES, VERDICT_OK
from research.mid_hold_recovery_failure_path_p4_2.publish import build_report, load_frozen, write_artifacts
from research.mid_hold_recovery_failure_path_p4_2.replay import DAY_CACHE, replay_day_cached
from run_p0_3_exact_runtime_replay_20260820 import _pf

P1_REPORT = NATIVE / "results" / "research" / "current_runtime_full_capture_recalc_p1" / "report.json"
P3_3_REPORT = NATIVE / "results" / "research" / "canonical_fixed_pnl_source_p3_3" / "report.json"


def _recon(primary: list[dict], p1: dict, p1_daily: list[dict]) -> dict:
    n = len(primary)
    sp = sum(pnl(t) for t in primary)
    pfv = _pf([pnl(t) for t in primary])
    days = day_table(primary, p1_daily)
    full_days = [d for d in days if str(d.get("date")) in set(FULL14)]
    sha_ok = all(bool(d.get("sha_match")) for d in full_days) and len(full_days) == 14
    reasons = []
    if n != P1_TRADES:
        reasons.append(f"trade_n={n}!={P1_TRADES}")
    if abs(sp - P1_PNL) >= 0.51:
        reasons.append(f"sum_pnl={sp}!={P1_PNL}")
    if pfv is None or abs(float(pfv) - float(P1_PF)) >= 1e-9:
        reasons.append(f"PF={pfv}!={P1_PF}")
    if not sha_ok:
        reasons.append("daily_ledger_sha_mismatch")
    if P3_3_REPORT.is_file():
        p33 = json.loads(P3_3_REPORT.read_text(encoding="utf-8"))
        pf14 = p33.get("PRIMARY_FULL14") or {}
        if int(pf14.get("trades") or 0) != P1_TRADES or abs(float(pf14.get("pnl") or 0) - P1_PNL) >= 0.51:
            reasons.append("P3_3_PRIMARY_MISMATCH")
    unused = p1
    del unused
    return {
        "pass": not reasons,
        "trade_n": n,
        "sum_pnl": round(sp, 2),
        "PF": pfv if pfv != float("inf") else "Infinity",
        "daily": [{"date": d.get("date"), "trades": d.get("trades"), "pnl": d.get("pnl"), "sha_match": d.get("sha_match")} for d in full_days],
        "reasons": reasons,
    }


def _blocked(reason, recon, frozen) -> int:
    rep = build_report(
        path_rows=[], recon=recon, frozen=frozen, leak_n=0, identity_n=0, identity_fail=0,
        blocked=True, blocked_reason=reason,
    )
    paths = write_artifacts(rep)
    print("P4_2_BLOCKED", reason, paths["report_json"], flush=True)
    return 1


def main() -> int:
    frozen = load_frozen()
    if not P1_REPORT.is_file():
        return _blocked("NO_P1_REPORT", {"pass": False, "reasons": ["NO_P1"]}, frozen)
    p1 = json.loads(P1_REPORT.read_text(encoding="utf-8"))
    primary = [t for t in (p1.get("trades") or []) if str(t.get("date")) in set(FULL14)]
    recon = _recon(primary, p1, list(p1.get("daily") or []))
    print(f"P4-2 reconcile pass={recon['pass']} n={recon['trade_n']} pnl={recon['sum_pnl']} frozen={frozen.get('ok')}", flush=True)
    if not recon["pass"] or not frozen.get("ok"):
        return _blocked("CANONICAL_OR_FROZEN_FAIL", recon, frozen)

    ranked = sorted(primary, key=pnl, reverse=True)
    top10_ids = [str(t.get("trade_id")) for t in ranked[:10]]
    top20_ids = [str(t.get("trade_id")) for t in ranked[:20]]
    inv = {r["date"]: r for r in build_inventory()}
    jobs = []
    for day in FULL14:
        row = inv.get(day)
        if not row or not row.get("capture_path") or not row.get("universe_symbols"):
            return _blocked(f"MISSING_{day}", recon, frozen)
        jobs.append(
            {
                "date": day,
                "capture_path": row["capture_path"],
                "universe": row["universe_symbols"],
                "canonical_trades": [t for t in primary if str(t.get("date")) == day],
                "top10_ids": top10_ids,
                "top20_ids": top20_ids,
            }
        )

    print(f"P4-2 days={len(jobs)} workers={MAX_WORKERS} canonical={len(primary)}", flush=True)
    results = []
    failed = []
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(replay_day_cached, j): j["date"] for j in jobs}
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
            src = "cache" if out.get("from_cache") else "stream"
            print(
                f"OK {day} [{src}] path={len(out.get('rows') or [])} leak={out.get('leak_n')} "
                f"ident_fail={out.get('identity_fail')} sec={out.get('elapsed_sec')}",
                flush=True,
            )
            results.append(out)

    if failed or len(results) != 14:
        return _blocked("DAY_REPLAY_FAILED", recon, frozen)

    path_rows = [r for d in results for r in (d.get("rows") or [])]
    rep = build_report(
        path_rows=path_rows,
        recon=recon,
        frozen=frozen,
        leak_n=sum(int(d.get("leak_n") or 0) for d in results),
        identity_n=sum(int(d.get("identity_n") or 0) for d in results),
        identity_fail=sum(int(d.get("identity_fail") or 0) for d in results),
        blocked=False,
        blocked_reason=None,
    )
    paths = write_artifacts(rep)
    if str(rep.get("verdict")) == VERDICT_OK and DAY_CACHE.exists():
        for p in DAY_CACHE.rglob("*.json"):
            p.unlink(missing_ok=True)
    print(rep["verdict"], paths["report_json"], flush=True)
    print(
        f"RECON={rep['CANONICAL_RECONCILE']} MECH={rep['MECHANISM_CLASSIFICATION']} "
        f"FAMILIES={rep['CANDIDATE_PATH_FAMILIES']} LEAK={rep['FUTURE_LEAK']}",
        flush=True,
    )
    return 0 if str(rep["verdict"]) != "P4_2_BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
