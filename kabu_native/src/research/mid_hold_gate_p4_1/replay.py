"""FULL14 CollectorEngine + Dual Lane exact replay. Picklable for Windows workers."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_vs_event_driven.run_comparison import _boot, _stream_day, extract_trades
from research.fixed_selection_diagnostic_reconcile_p3_0r.replay import _pop_webhooks
from research.mid_hold_gate_p4_1 import CACHE_VERSION
from research.mid_hold_gate_p4_1.gate import attach_mid_hold_gate
from run_p0_3_exact_runtime_replay_20260820 import _anchor_from_fill_t, _iso
from run_p0_4_exact_vs_fast_parity import CollectorEngine, _Discard
from small_paper.v1r_live_dual_lane import canonical_symbol_key

DAY_CACHE = NATIVE / "results" / "research" / "_p4_1_day_cache"


def _pack_trades(day: str, eng: Any, dual: Any) -> list[dict[str, Any]]:
    raw = extract_trades(dual)
    trades: list[dict[str, Any]] = []
    for i, tr in enumerate(raw, start=1):
        fill_t = float(tr.get("entry_time") or 0.0)
        exit_t = tr.get("exit_time")
        an = _anchor_from_fill_t(day, fill_t)
        sym = canonical_symbol_key(tr.get("symbol"))
        snap = eng.snapshots.get((an, sym), {})
        fill_row = next((f for f in eng.a_fills if f.get("symbol") == sym and f.get("anchor") == an), None)
        admit_row = next((a for a in eng.a_admits if a.get("symbol") == sym and a.get("anchor") == an), None)
        src = fill_row or admit_row or snap
        holding = None
        if fill_t and exit_t is not None:
            holding = round(float(exit_t) - fill_t, 3)
        trades.append(
            {
                "date": day,
                "session": tr.get("session"),
                "trade_id": f"{day}|{tr.get('session')}|{an}|{sym}|{i}",
                "symbol": sym,
                "anchor_time": an,
                "score": src.get("score") if src else None,
                "limit": (fill_row or admit_row or {}).get("limit") or tr.get("entry_price"),
                "fill_time": fill_t,
                "fill_time_iso": _iso(fill_t),
                "fill_price": tr.get("entry_price"),
                "exit_time": exit_t,
                "exit_time_iso": _iso(exit_t),
                "exit_price": tr.get("exit_price"),
                "exit_reason": tr.get("reason"),
                "pnl_yen_100": float(tr.get("pnl_yen_100") or 0.0),
                "holding_sec": holding,
            }
        )
    return trades


def replay_day(payload: dict[str, Any]) -> dict[str, Any]:
    _pop_webhooks()
    t0w = time.perf_counter()
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    live_exit = bool(payload.get("live_exit"))
    precommit_sha = str(payload.get("precommit_sha") or "")
    try:
        eng, dual = _boot(universe, CollectorEngine)
        if dual is None or not eng.ready:
            return {
                "ok": False,
                "date": day,
                "blocker": getattr(eng, "fail_reason", "dual_unavailable"),
                "elapsed_sec": round(time.perf_counter() - t0w, 3),
            }
        eng.notify_enabled = False
        eng.ingest_audit = _Discard()  # type: ignore[assignment]
        attach_mid_hold_gate(dual, live_exit=live_exit, precommit_sha=precommit_sha, date=day)
        _stream_day(day, capture, eng, dual)
        eng._harvest(eng.events)
        trades = _pack_trades(day, eng, dual)
        admits = [
            {
                "date": day,
                "symbol": canonical_symbol_key(a.get("symbol")),
                "anchor": a.get("anchor"),
                "score": a.get("score"),
                "limit": a.get("limit"),
            }
            for a in (eng.a_admits or [])
        ]
        fills = [
            {
                "date": day,
                "symbol": canonical_symbol_key(f.get("symbol")),
                "anchor": f.get("anchor"),
                "fill_time": f.get("fill_time"),
                "fill_price": f.get("fill_price"),
            }
            for f in (eng.a_fills or [])
        ]
        return {
            "ok": True,
            "date": day,
            "live_exit": live_exit,
            "precommit_sha": precommit_sha,
            "trades": trades,
            "admits": admits,
            "fills": fills,
            "mh_records": list(getattr(dual, "mh_records", []) or []),
            "leak_n": int(getattr(dual, "mh_leak_n", 0) or 0),
            "cap_blocked": int(getattr(eng, "cap_blocked", 0) or 0),
            "same_symbol_blocked": int(getattr(eng, "same_symbol_blocked", 0) or 0),
            "dual_primary_capacity_block": int(getattr(dual.stats, "primary_capacity_block", 0) or 0),
            "n_events": None,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }


def replay_day_cached(payload: dict[str, Any]) -> dict[str, Any]:
    day = str(payload["date"])
    live = "gate_on" if payload.get("live_exit") else "baseline"
    DAY_CACHE.mkdir(parents=True, exist_ok=True)
    (DAY_CACHE / live).mkdir(parents=True, exist_ok=True)
    cp = DAY_CACHE / live / f"{day}.json"
    want_ver = {
        "cache_version": CACHE_VERSION,
        "precommit_sha": str(payload.get("precommit_sha") or ""),
        "live_exit": bool(payload.get("live_exit")),
    }
    if cp.is_file():
        try:
            cached = json.loads(cp.read_text(encoding="utf-8"))
        except Exception:
            cached = None
        if (
            cached
            and cached.get("ok")
            and cached.get("cache_version") == want_ver["cache_version"]
            and cached.get("precommit_sha") == want_ver["precommit_sha"]
            and bool(cached.get("live_exit")) == want_ver["live_exit"]
        ):
            cached["from_cache"] = True
            return cached
    out = replay_day(payload)
    out["cache_version"] = CACHE_VERSION
    if out.get("ok"):
        cp.write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
    return out
