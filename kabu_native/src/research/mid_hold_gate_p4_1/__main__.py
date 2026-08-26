"""P4-1 runner. Precommit SHA first, Gate-OFF reconcile, one-shot Gate-ON. No Runtime adopt."""
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
from research.mid_hold_gate_p4_1 import FULL14, MAX_WORKERS, P1_PF, P1_PNL, P1_TRADES
from research.mid_hold_gate_p4_1.precommit import write_precommit
from research.mid_hold_gate_p4_1.publish import build_report, load_frozen, write_artifacts
from research.mid_hold_gate_p4_1.replay import DAY_CACHE, replay_day_cached
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
    n_ok = n == P1_TRADES
    sum_ok = abs(sp - P1_PNL) < 0.51
    pf_ok = pf is not None and abs(float(pf) - float(P1_PF)) < 1e-9
    reasons = []
    if not n_ok:
        reasons.append(f"trade_n={n}!={P1_TRADES}")
    if not sum_ok:
        reasons.append(f"sum_pnl={sp}!={P1_PNL}")
    if not pf_ok:
        reasons.append(f"PF={pf}!={P1_PF}")
    if not sha_ok:
        reasons.append("daily_ledger_sha_mismatch")
    p33_ok = True
    if P3_3_REPORT.is_file():
        p33 = json.loads(P3_3_REPORT.read_text(encoding="utf-8"))
        pf14 = p33.get("PRIMARY_FULL14") or {}
        p33_ok = int(pf14.get("trades") or 0) == P1_TRADES and abs(float(pf14.get("pnl") or 0) - P1_PNL) < 0.51
        if not p33_ok:
            reasons.append("P3_3_PRIMARY_MISMATCH")
    return {
        "pass": not reasons,
        "trade_n": n,
        "sum_pnl": round(sp, 2),
        "PF": pf if pf != float("inf") else "Infinity",
        "daily": [
            {"date": d.get("date"), "trades": d.get("trades"), "pnl": d.get("pnl"), "sha_match": d.get("sha_match")}
            for d in full_days
        ],
        "reasons": reasons,
    }


def _jobs(inv, primary, precommit_sha: str, live_exit: bool) -> list[dict]:
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
                "live_exit": live_exit,
            }
        )
    unused = primary
    del unused
    return jobs


def _run_pool(jobs: list[dict], label: str) -> tuple[list[dict], list[str]]:
    print(f"P4-1 {label} days={len(jobs)} workers={MAX_WORKERS}", flush=True)
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
                f"mh={len(out.get('mh_records') or [])} leak={out.get('leak_n')} sec={out.get('elapsed_sec')}",
                flush=True,
            )
            results.append(out)
    results.sort(key=lambda r: str(r.get("date")))
    return results, failed


def _baseline_ok(days: list[dict]) -> tuple[bool, dict]:
    trades = [t for d in days for t in (d.get("trades") or [])]
    n = len(trades)
    sp = round(sum(pnl(t) for t in trades), 2)
    pf = _pf([pnl(t) for t in trades])
    reasons = []
    if n != P1_TRADES:
        reasons.append(f"baseline_n={n}!={P1_TRADES}")
    if abs(sp - P1_PNL) >= 0.51:
        reasons.append(f"baseline_pnl={sp}!={P1_PNL}")
    return not reasons, {"n": n, "pnl": sp, "PF": pf if pf != float("inf") else "Infinity", "reasons": reasons}


def main() -> int:
    frozen = load_frozen()
    pre = write_precommit()
    print(f"P4-1 PRECOMMIT_SHA={pre.get('SHA')} frozen_ok={frozen.get('ok')}", flush=True)

    if not P1_REPORT.is_file():
        rep = build_report(
            precommit=pre, recon={"pass": False, "reasons": ["NO_P1_REPORT"]}, frozen=frozen,
            canonical=[], top10_ids=[], top20_ids=[], base_days=[], gate_days=[],
            blocked=True, blocked_reason="NO_P1_REPORT",
        )
        write_artifacts(rep)
        print("P4_1_BLOCKED NO_P1_REPORT", flush=True)
        return 1

    p1 = json.loads(P1_REPORT.read_text(encoding="utf-8"))
    all_trades = list(p1.get("trades") or [])
    p1_daily = list(p1.get("daily") or [])
    primary = [t for t in all_trades if str(t.get("date")) in set(FULL14)]
    recon = _recon(primary, p1, p1_daily)
    print(
        f"P4-1 canonical reconcile pass={recon['pass']} n={recon['trade_n']} pnl={recon['sum_pnl']}",
        flush=True,
    )
    ranked = sorted(primary, key=pnl, reverse=True)
    top10_ids = [str(t.get("trade_id")) for t in ranked[:10]]
    top20_ids = [str(t.get("trade_id")) for t in ranked[:20]]

    if not recon["pass"] or not frozen.get("ok"):
        reason = "CANONICAL_OR_FROZEN_FAIL"
        rep = build_report(
            precommit=pre, recon=recon, frozen=frozen, canonical=primary,
            top10_ids=top10_ids, top20_ids=top20_ids, base_days=[], gate_days=[],
            blocked=True, blocked_reason=reason,
        )
        paths = write_artifacts(rep)
        print("P4_1_BLOCKED", reason, paths["report_json"], flush=True)
        return 1

    inv = {r["date"]: r for r in build_inventory()}
    base_jobs = _jobs(inv, primary, str(pre["SHA"]), False)
    if len(base_jobs) != 14:
        rep = build_report(
            precommit=pre, recon=recon, frozen=frozen, canonical=primary,
            top10_ids=top10_ids, top20_ids=top20_ids, base_days=[], gate_days=[],
            blocked=True, blocked_reason="MISSING_CAPTURE",
        )
        write_artifacts(rep)
        print("P4_1_BLOCKED MISSING_CAPTURE", flush=True)
        return 1

    base_days, base_fail = _run_pool(base_jobs, "baseline")
    ok, bstat = _baseline_ok(base_days)
    print(f"P4-1 baseline replay {bstat}", flush=True)
    if base_fail or not ok:
        recon_b = dict(recon)
        recon_b["pass"] = False
        recon_b["reasons"] = list(recon.get("reasons") or []) + list(bstat.get("reasons") or []) + [f"failed={base_fail}"]
        rep = build_report(
            precommit=pre, recon=recon_b, frozen=frozen, canonical=primary,
            top10_ids=top10_ids, top20_ids=top20_ids, base_days=base_days, gate_days=[],
            blocked=True, blocked_reason="BASELINE_REPLAY_MISMATCH",
        )
        paths = write_artifacts(rep)
        print("P4_1_BLOCKED BASELINE_REPLAY_MISMATCH", paths["report_json"], flush=True)
        return 1

    gate_jobs = _jobs(inv, primary, str(pre["SHA"]), True)
    gate_days, gate_fail = _run_pool(gate_jobs, "gate_on")
    if gate_fail or len(gate_days) != 14:
        rep = build_report(
            precommit=pre, recon=recon, frozen=frozen, canonical=primary,
            top10_ids=top10_ids, top20_ids=top20_ids, base_days=base_days, gate_days=gate_days,
            blocked=True, blocked_reason="GATE_ON_REPLAY_FAILED",
        )
        paths = write_artifacts(rep)
        print("P4_1_BLOCKED GATE_ON_REPLAY_FAILED", paths["report_json"], flush=True)
        return 1

    rep = build_report(
        precommit=pre, recon=recon, frozen=frozen, canonical=primary,
        top10_ids=top10_ids, top20_ids=top20_ids, base_days=base_days, gate_days=gate_days,
        blocked=False, blocked_reason=None,
    )
    paths = write_artifacts(rep)
    from research.mid_hold_gate_p4_1 import VERDICT_OK as VOK

    if str(rep.get("verdict")) == VOK and DAY_CACHE.exists():
        for p in DAY_CACHE.rglob("*.json"):
            p.unlink(missing_ok=True)
    print(rep["verdict"], paths["report_json"], flush=True)
    print(
        f"SHA={rep.get('PRECOMMIT_SHA')} STATUS={rep.get('STATUS')} "
        f"TOP10_cut={(rep.get('WINNER_PRESERVATION') or {}).get('TOP10_cut')} "
        f"LEAK={rep.get('FUTURE_LEAK')} ADOPTED={rep.get('NEW_EXIT_ADOPTED')}",
        flush=True,
    )
    return 0 if str(rep["verdict"]) != "P4_1_BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
