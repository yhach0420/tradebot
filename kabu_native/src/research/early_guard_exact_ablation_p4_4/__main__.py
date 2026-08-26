"""P4-4 runner. Precommit SHA first, Guard-ON baseline, then EARLY_GUARD_OFF_ONLY. No Runtime."""
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
from research.early_guard_exact_ablation_p4_4 import (
    CANONICAL_GUARD_N,
    FULL14,
    GUARD_EXIT_REASON,
    MAX_WORKERS,
    P1_PF,
    P1_PNL,
    P1_TRADES,
    VERDICT_OK,
)
from research.early_guard_exact_ablation_p4_4.metrics import is_guard_exit, reconcile_89
from research.early_guard_exact_ablation_p4_4.precommit import write_precommit
from research.early_guard_exact_ablation_p4_4.publish import build_report, write_artifacts
from research.early_guard_exact_ablation_p4_4.replay import DAY_CACHE, replay_day_cached
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
    reasons = []
    if n != P1_TRADES:
        reasons.append(f"trade_n={n}!={P1_TRADES}")
    if abs(sp - P1_PNL) >= 0.51:
        reasons.append(f"sum_pnl={sp}!={P1_PNL}")
    if pf is None or abs(float(pf) - float(P1_PF)) >= 1e-9:
        reasons.append(f"PF={pf}!={P1_PF}")
    if not sha_ok:
        reasons.append("daily_ledger_sha_mismatch")
    g_n = sum(1 for t in primary if is_guard_exit(t))
    if g_n != CANONICAL_GUARD_N:
        reasons.append(f"canonical_IMBALANCE={g_n}!={CANONICAL_GUARD_N}")
    if P3_3_REPORT.is_file():
        p33 = json.loads(P3_3_REPORT.read_text(encoding="utf-8"))
        pf14 = p33.get("PRIMARY_FULL14") or {}
        if int(pf14.get("trades") or 0) != P1_TRADES or abs(float(pf14.get("pnl") or 0) - P1_PNL) >= 0.51:
            reasons.append("P3_3_PRIMARY_MISMATCH")
    unused = p1, GUARD_EXIT_REASON
    del unused
    return {
        "pass": not reasons,
        "trade_n": n,
        "sum_pnl": round(sp, 2),
        "PF": pf if pf != float("inf") else "Infinity",
        "imbalance_n": g_n,
        "daily": [{"date": d.get("date"), "trades": d.get("trades"), "pnl": d.get("pnl"), "sha_match": d.get("sha_match")} for d in full_days],
        "reasons": reasons,
    }


def _jobs(inv, precommit_sha: str, guard_off: bool) -> list[dict]:
    jobs = []
    for day in FULL14:
        row = inv.get(day)
        if not row or not row.get("capture_path") or not row.get("universe_symbols"):
            return []
        jobs.append(
            {
                "date": day,
                "capture_path": row["capture_path"],
                "universe": row["universe_symbols"],
                "precommit_sha": precommit_sha,
                "guard_off": guard_off,
            }
        )
    return jobs


def _run_pool(jobs: list[dict], label: str) -> tuple[list[dict], list[str]]:
    print(f"P4-4 {label} days={len(jobs)} workers={MAX_WORKERS}", flush=True)
    results = []
    failed = []
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(replay_day_cached, j): j["date"] for j in jobs}
        for fut in as_completed(futs):
            day = futs[fut]
            try:
                out = fut.result()
            except Exception as exc:
                print(f"FAIL {label} {day} {exc!r}", flush=True)
                failed.append(day)
                continue
            if not out.get("ok"):
                print(f"FAIL {label} {day} {out.get('blocker')}", flush=True)
                failed.append(day)
                continue
            src = "cache" if out.get("from_cache") else "stream"
            print(
                f"OK {label} {day} [{src}] trades={len(out.get('trades') or [])} "
                f"imb={sum(1 for t in (out.get('trades') or []) if is_guard_exit(t))} "
                f"sec={out.get('elapsed_sec')}",
                flush=True,
            )
            results.append(out)
    results.sort(key=lambda r: str(r.get("date")))
    return results, failed


def _blocked(reason, pre, recon, canonical, top10, top20, base_days=None) -> int:
    rep = build_report(
        precommit=pre, recon=recon, canonical=canonical, top10_ids=top10, top20_ids=top20,
        base_days=base_days or [], off_days=[], blocked=True, blocked_reason=reason,
    )
    paths = write_artifacts(rep)
    print("P4_4_BLOCKED", reason, paths["report_json"], flush=True)
    return 1


def main() -> int:
    pre = write_precommit()
    print(f"P4-4 PRECOMMIT_SHA={pre.get('SHA')}", flush=True)

    if not P1_REPORT.is_file():
        return _blocked("NO_P1_REPORT", pre, {"pass": False, "reasons": ["NO_P1"]}, [], [], [])

    p1 = json.loads(P1_REPORT.read_text(encoding="utf-8"))
    primary = [t for t in (p1.get("trades") or []) if str(t.get("date")) in set(FULL14)]
    recon = _recon(primary, p1, list(p1.get("daily") or []))
    ranked = sorted(primary, key=pnl, reverse=True)
    top10_ids = [str(t.get("trade_id")) for t in ranked[:10]]
    top20_ids = [str(t.get("trade_id")) for t in ranked[:20]]
    print(
        f"P4-4 canonical pass={recon['pass']} n={recon['trade_n']} pnl={recon['sum_pnl']} imb={recon.get('imbalance_n')}",
        flush=True,
    )
    if not recon["pass"]:
        return _blocked("CANONICAL_RECONCILE_FAIL", pre, recon, primary, top10_ids, top20_ids)

    inv = {r["date"]: r for r in build_inventory()}
    base_jobs = _jobs(inv, str(pre["SHA"]), False)
    if len(base_jobs) != 14:
        return _blocked("MISSING_CAPTURE", pre, recon, primary, top10_ids, top20_ids)

    base_days, base_fail = _run_pool(base_jobs, "baseline")
    base_trades = [t for d in base_days for t in (d.get("trades") or [])]
    n = len(base_trades)
    sp = round(sum(pnl(t) for t in base_trades), 2)
    rec89 = reconcile_89(canonical=primary, baseline=base_trades) if base_trades else {"ok": False}
    print(
        f"P4-4 baseline n={n} pnl={sp} imb={sum(1 for t in base_trades if is_guard_exit(t))} "
        f"guard89_ok={rec89.get('ok')} match={rec89.get('matched_n')}",
        flush=True,
    )
    if base_fail or n != P1_TRADES or abs(sp - P1_PNL) >= 0.51 or not rec89.get("ok"):
        return _blocked("BASELINE_REPLAY_OR_GUARD89_FAIL", pre, recon, primary, top10_ids, top20_ids, base_days)

    off_jobs = _jobs(inv, str(pre["SHA"]), True)
    off_days, off_fail = _run_pool(off_jobs, "guard_off")
    if off_fail or len(off_days) != 14:
        return _blocked("GUARD_OFF_REPLAY_FAILED", pre, recon, primary, top10_ids, top20_ids, base_days)

    rep = build_report(
        precommit=pre, recon=recon, canonical=primary, top10_ids=top10_ids, top20_ids=top20_ids,
        base_days=base_days, off_days=off_days, blocked=False, blocked_reason=None,
    )
    paths = write_artifacts(rep)
    if str(rep.get("verdict")) == VERDICT_OK and DAY_CACHE.exists():
        for p in DAY_CACHE.rglob("*.json"):
            p.unlink(missing_ok=True)
    print(rep["verdict"], paths["report_json"], flush=True)
    print(
        f"SHA={rep.get('PRECOMMIT_SHA')} CLASS={rep.get('CLASSIFICATION')} "
        f"ON={((rep.get('BASELINE') or {}).get('pnl'))} OFF={((rep.get('GUARD_OFF') or {}).get('pnl'))} "
        f"NET={((rep.get('CURRENT_EXACT_GUARD_ECONOMICS') or {}).get('net_guard_value'))}",
        flush=True,
    )
    return 0 if str(rep["verdict"]) != "P4_4_BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
