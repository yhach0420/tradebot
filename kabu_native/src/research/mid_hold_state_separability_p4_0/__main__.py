"""P4-0 runner. Mid-hold state separability only. No new EXIT / no threshold."""
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
from research.mid_hold_state_separability_p4_0 import FULL14, MAX_WORKERS, P1_PF, P1_PNL, P1_TRADES
from research.mid_hold_state_separability_p4_0.publish import build_report, load_frozen, write_artifacts
from research.mid_hold_state_separability_p4_0.replay import DAY_CACHE, replay_day_cached
from run_p0_3_exact_runtime_replay_20260820 import _pf

P1_REPORT = NATIVE / "results" / "research" / "current_runtime_full_capture_recalc_p1" / "report.json"
P3_3_REPORT = NATIVE / "results" / "research" / "canonical_fixed_pnl_source_p3_3" / "report.json"


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
    p33_ok = True
    p33_note = None
    if P3_3_REPORT.is_file():
        p33 = json.loads(P3_3_REPORT.read_text(encoding="utf-8"))
        pf14 = p33.get("PRIMARY_FULL14") or {}
        p33_ok = int(pf14.get("trades") or 0) == P1_TRADES and abs(float(pf14.get("pnl") or 0) - P1_PNL) < 0.51
        if not p33_ok:
            p33_note = "P3_3_PRIMARY_MISMATCH"
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
    if not p33_ok:
        reasons.append(p33_note or "P3_3_MISMATCH")
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
                "sha_match": d.get("sha_match"),
            }
            for d in full_days
        ],
        "reasons": reasons,
    }


def _blocked(reason, failed, recon, frozen) -> int:
    rep = build_report(
        path_rows=[],
        recon=recon,
        frozen=frozen,
        leak_n=0,
        identity_n=0,
        identity_fail=0,
        failed=failed,
        blocked=True,
        blocked_reason=reason,
    )
    paths = write_artifacts(rep)
    print("P4_0_BLOCKED", reason, paths["report_json"], flush=True)
    return 1


def main() -> int:
    frozen = load_frozen()
    if not P1_REPORT.is_file():
        recon = {"pass": False, "reasons": ["NO_P1_REPORT"]}
        return _blocked("NO_P1_REPORT", ["NO_P1"], recon, frozen)

    p1 = json.loads(P1_REPORT.read_text(encoding="utf-8"))
    all_trades = list(p1.get("trades") or [])
    p1_daily = list(p1.get("daily") or [])
    primary = [t for t in all_trades if str(t.get("date")) in set(FULL14)]
    recon = _recon(primary, p1, p1_daily)
    print(
        f"P4-0 reconcile pass={recon['pass']} n={recon['trade_n']} pnl={recon['sum_pnl']} "
        f"frozen={frozen.get('ok')} reasons={recon['reasons']}",
        flush=True,
    )
    if not recon["pass"]:
        return _blocked("P4_0_BLOCKED", recon["reasons"], recon, frozen)
    if not frozen.get("ok"):
        return _blocked(frozen.get("reason") or "P3_4R_FROZEN_MISSING", [], recon, frozen)

    ranked = sorted(primary, key=pnl, reverse=True)
    top10_ids = [str(t.get("trade_id")) for t in ranked[:10]]
    top20_ids = [str(t.get("trade_id")) for t in ranked[:20]]

    inv = {r["date"]: r for r in build_inventory()}
    jobs = []
    for day in FULL14:
        row = inv.get(day)
        if not row or not row.get("capture_path") or not row.get("universe_symbols"):
            return _blocked(f"MISSING_{day}", [day], recon, frozen)
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

    print(f"P4-0 days={len(jobs)} workers={MAX_WORKERS} canonical={len(primary)}", flush=True)
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
            n_el = sum(1 for r in (out.get("rows") or []) if r.get("eligible") and r.get("horizon_sec") == 120)
            src = "cache" if out.get("from_cache") else "stream"
            print(
                f"OK {day} [{src}] path={len(out.get('rows') or [])} elig120={n_el} "
                f"leak={out.get('leak_n')} ident_fail={out.get('identity_fail')} sec={out.get('elapsed_sec')}",
                flush=True,
            )
            results.append(out)

    if failed or len(results) != 14:
        return _blocked("DAY_REPLAY_FAILED", failed, recon, frozen)

    path_rows = [r for d in results for r in (d.get("rows") or [])]
    rep = build_report(
        path_rows=path_rows,
        recon=recon,
        frozen=frozen,
        leak_n=sum(int(d.get("leak_n") or 0) for d in results),
        identity_n=sum(int(d.get("identity_n") or 0) for d in results),
        identity_fail=sum(int(d.get("identity_fail") or 0) for d in results),
        failed=[],
        blocked=False,
        blocked_reason=None,
    )
    paths = write_artifacts(rep)
    if str(rep.get("verdict")) == "P4_0_MID_HOLD_STATE_AUDIT_COMPLETE":
        for p in DAY_CACHE.glob("*.json"):
            p.unlink(missing_ok=True)
    print(rep["verdict"], paths["report_json"], flush=True)
    print(
        f"RECON={rep['CANONICAL_RECONCILE']} GATEABILITY={rep['MID_HOLD_GATEABILITY']} "
        f"FAMILIES={rep['CANDIDATE_STATE_FAMILIES']} LEAK={rep['FUTURE_LEAK']}",
        flush=True,
    )
    return 0 if not str(rep["verdict"]).endswith("BLOCKED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
