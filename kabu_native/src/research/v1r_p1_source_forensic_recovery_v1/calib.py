"""FULL14 Fast Replay calibration against frozen P1 ledgers. Research-only isolated import of current modules."""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from research.v1r_p1_source_forensic_recovery_v1.isolation import CACHE, NATIVE, P1_OUT
from research.v1r_p1_source_forensic_recovery_v1.spec import P1_FULL14_DAYS

CALIB_DIR = CACHE / "full14_fast"


def _load_p1() -> dict[str, Any]:
    return json.loads((P1_OUT / "report.json").read_text(encoding="utf-8"))


def expected_full14(p1: dict[str, Any] | None = None) -> dict[str, Any]:
    body = p1 or _load_p1()
    daily = {str(r.get("date")): r for r in list(body.get("daily") or [])}
    trades = [t for t in list(body.get("trades") or []) if str(t.get("date")) in set(P1_FULL14_DAYS)]
    by_day: dict[str, list[dict[str, Any]]] = {d: [] for d in P1_FULL14_DAYS}
    for t in trades:
        by_day[str(t.get("date"))].append(t)
    rows = []
    for d in P1_FULL14_DAYS:
        r = daily.get(d) or {}
        rows.append(
            {
                "date": d,
                "trade_n": int(r.get("trades") or len(by_day[d])),
                "pnl": r.get("pnl"),
                "ledger_sha": r.get("ledger_sha"),
                "admitted": r.get("admitted"),
                "anchor_fires": r.get("anchor_fires"),
                "universe_source": r.get("universe_source"),
                "universe_n": r.get("universe_n"),
                "capture_class": r.get("capture_class"),
            }
        )
    return {"days": rows, "trades_by_day": by_day}


def _jobs() -> list[dict[str, Any]]:
    sys.path.insert(0, str(NATIVE / "scripts"))
    from _p1_inventory import resolve_universe
    from research.anchor_vs_event_driven.run_comparison import find_capture_dir

    jobs = []
    for day in P1_FULL14_DAYS:
        cap = find_capture_dir(day)
        uni = resolve_universe(day, cap)
        jobs.append(
            {
                "date": day,
                "capture_path": str(cap) if cap else "",
                "universe": list(uni.get("symbols") or []),
                "universe_source": uni.get("source"),
                "universe_resolved": bool(uni.get("resolved")),
            }
        )
    return jobs


def _replay_one(payload: dict[str, Any]) -> dict[str, Any]:
    if str(NATIVE / "src") not in sys.path:
        sys.path.insert(0, str(NATIVE / "src"))
        sys.path.insert(0, str(NATIVE / "scripts"))
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    for k in (
        "KABU_V1R_ENTRY_WEBHOOK_URL",
        "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
        "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL",
        "KABU_DISCORD_RESEARCH_WEBHOOK_URL",
        "KABU_SHADOW_DISCORD_WEBHOOK_URL",
        "KABU_MARKET_CAPTURE_WEBHOOK_URL",
        "KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL",
    ):
        os.environ.pop(k, None)
    from run_p1_current_runtime_full_capture_recalc import replay_one

    return replay_one(payload)


def run_full14(*, workers: int = 2) -> dict[str, Any]:
    CALIB_DIR.mkdir(parents=True, exist_ok=True)
    exp = expected_full14()
    jobs = _jobs()
    got: dict[str, Any] = {}
    pending = []
    for j in jobs:
        day = j["date"]
        cache_p = CALIB_DIR / f"{day}.json"
        if cache_p.is_file():
            got[day] = json.loads(cache_p.read_text(encoding="utf-8"))
            continue
        if not j.get("capture_path") or not j.get("universe_resolved"):
            got[day] = {"ok": False, "date": day, "blocker": "CAPTURE_OR_UNIVERSE"}
            cache_p.write_text(json.dumps(got[day], ensure_ascii=False, default=str), encoding="utf-8")
            continue
        pending.append(j)
    print(f"FULL14 cached={len(got)} pending={len(pending)} workers={workers}", flush=True)
    if pending:
        with ProcessPoolExecutor(max_workers=int(workers)) as ex:
            futs = {ex.submit(_replay_one, j): j["date"] for j in pending}
            for fut in as_completed(futs):
                day = futs[fut]
                try:
                    body = fut.result()
                except Exception as exc:
                    body = {"ok": False, "date": day, "blocker": f"{type(exc).__name__}:{exc}"}
                got[day] = body
                (CALIB_DIR / f"{day}.json").write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")
                print(
                    f"CALIB {day} ok={body.get('ok')} trades={body.get('trade_n')} pnl={body.get('pnl')} "
                    f"sha={str(body.get('ledger_sha') or '')[:12]} sec={body.get('elapsed_sec')} {body.get('blocker') or ''}",
                    flush=True,
                )
    return compare_full14(exp, got)


def _trade_key(t: dict[str, Any]) -> tuple:
    return (
        str(t.get("symbol") or ""),
        str(t.get("session") or ""),
        str(t.get("anchor_time") or ""),
        round(float(t.get("fill_time") or 0.0), 6),
        round(float(t.get("fill_price") or 0.0), 6),
    )


def compare_full14(exp: dict[str, Any], got: dict[str, Any]) -> dict[str, Any]:
    rows = []
    n_ok = 0
    trade_n_match = 0
    sha_match = 0
    pnl_match = 0
    identity_match = 0
    fill_exit_match = 0
    admit_match = 0
    for spec in list(exp.get("days") or []):
        day = str(spec["date"])
        g = dict(got.get(day) or {})
        exp_sha = spec.get("ledger_sha")
        got_sha = g.get("ledger_sha")
        exp_n = int(spec.get("trade_n") or 0)
        got_n = int(g.get("trade_n") or 0) if g.get("ok") else None
        exp_pnl = spec.get("pnl")
        got_pnl = g.get("pnl")
        tn = got_n == exp_n
        sm = bool(exp_sha) and exp_sha == got_sha
        pm = g.get("ok") and exp_pnl is not None and got_pnl is not None and abs(float(exp_pnl) - float(got_pnl)) < 1e-6
        exp_tr = list((exp.get("trades_by_day") or {}).get(day) or [])
        got_tr = list(g.get("trades") or [])
        exp_keys = sorted(_trade_key(t) for t in exp_tr)
        got_keys = sorted(_trade_key(t) for t in got_tr)
        ident = exp_keys == got_keys
        fe = True
        if ident and exp_tr:
            em = {(_trade_key(t)): t for t in exp_tr}
            gm = {(_trade_key(t)): t for t in got_tr}
            for k, et in em.items():
                gt = gm.get(k) or {}
                if round(float(et.get("exit_time") or 0.0), 6) != round(float(gt.get("exit_time") or 0.0), 6):
                    fe = False
                if round(float(et.get("exit_price") or 0.0), 6) != round(float(gt.get("exit_price") or 0.0), 6):
                    fe = False
                if str(et.get("exit_reason") or "") != str(gt.get("exit_reason") or ""):
                    fe = False
        else:
            fe = False
        adm = g.get("ok") and spec.get("admitted") is not None and int(g.get("admitted") or -1) == int(spec.get("admitted") or -2)
        anc = g.get("ok") and spec.get("anchor_fires") is not None and int(g.get("anchor_fires") or -1) == int(spec.get("anchor_fires") or -2)
        day_ok = bool(g.get("ok") and tn and sm and pm and ident and fe and adm and anc)
        if tn:
            trade_n_match += 1
        if sm:
            sha_match += 1
        if pm:
            pnl_match += 1
        if ident:
            identity_match += 1
        if fe:
            fill_exit_match += 1
        if adm and anc:
            admit_match += 1
        if day_ok:
            n_ok += 1
        rows.append(
            {
                "date": day,
                "ok": bool(g.get("ok")),
                "blocker": g.get("blocker"),
                "exp_trade_n": exp_n,
                "got_trade_n": got_n,
                "exp_pnl": exp_pnl,
                "got_pnl": got_pnl,
                "exp_ledger_sha": exp_sha,
                "got_ledger_sha": got_sha,
                "trade_n_match": tn,
                "ledger_sha_match": sm,
                "pnl_match": bool(pm),
                "identity_match": ident,
                "fill_exit_match": fe,
                "admitted_match": adm,
                "anchor_match": anc,
                "day_pass": day_ok,
                "elapsed_sec": g.get("elapsed_sec"),
            }
        )
    all_pass = n_ok == 14
    return {
        "ran": True,
        "FULL14_DAY_N": 14,
        "days_pass": n_ok,
        "trade_n_match_n": trade_n_match,
        "ledger_sha_match_n": sha_match,
        "pnl_match_n": pnl_match,
        "identity_match_n": identity_match,
        "fill_exit_match_n": fill_exit_match,
        "anchor_admission_match_n": admit_match,
        "BEHAVIORAL_EQUIVALENCE": bool(all_pass),
        "rows": rows,
        "jobs_universe": None,
    }
