"""Causal Fixed replay with old vs new Passive Fill executable-board gate."""
from __future__ import annotations

import gc
import os
import sys
import time
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_10min_opportunity.replay import _Discard
from research.anchor_timing_robustness.replay import _portfolio_pack, _pop_webhooks
from research.anchor_vs_event_driven.run_comparison import _boot, _stream_day
from research.e1_x34a_execution_policy.executable_board import classify_passive_fill_state
from research.fixed_anchor_mechanism_audit_p3_0.engine import P3Engine


def _compact_fill(row: dict[str, Any], *, date: str) -> dict[str, Any]:
    state = str(row.get("board_execution_state") or "")
    return {
        "date": date,
        "symbol": row.get("symbol"),
        "anchor": row.get("anchor"),
        "limit": row.get("limit") or row.get("limit_price"),
        "limit_price": row.get("limit_price") or row.get("limit"),
        "fill_price": row.get("fill_price"),
        "fill_time": row.get("fill_time"),
        "fill_event_time": row.get("fill_event_time") or row.get("fill_time"),
        "cross_ask": row.get("cross_ask"),
        "cross_ask_qty": row.get("cross_ask_qty"),
        "board_execution_state": state,
        "fill_class": classify_passive_fill_state(state) if state else "",
        "AskSign": row.get("AskSign"),
        "BidSign": row.get("BidSign"),
        "OpeningPrice": row.get("OpeningPrice"),
        "OpeningPriceTime": row.get("OpeningPriceTime"),
        "opening_status": row.get("opening_status"),
        "score": row.get("score"),
    }


def _merge_trace(trades: list[dict[str, Any]], fills: list[dict[str, Any]]) -> None:
    idx = {(str(f.get("symbol") or ""), str(f.get("anchor") or "")): f for f in fills}
    for t in trades:
        f = idx.get((str(t.get("symbol") or ""), str(t.get("anchor_time") or "")))
        if not f:
            continue
        t["board_execution_state"] = f.get("board_execution_state")
        t["fill_class"] = f.get("fill_class")
        t["cross_ask"] = f.get("cross_ask")
        t["cross_ask_qty"] = f.get("cross_ask_qty")
        t["AskSign"] = f.get("AskSign")
        t["opening_status"] = f.get("opening_status")
        t["OpeningPrice"] = f.get("OpeningPrice")
        t["OpeningPriceTime"] = f.get("OpeningPriceTime")
        t["fill_event_time"] = f.get("fill_event_time")
        t["limit_price"] = f.get("limit_price") or t.get("limit")


def _run_mode(
    *,
    day: str,
    capture: Path,
    universe: list[str],
    require_executable_continuous: bool,
) -> dict[str, Any]:
    _pop_webhooks()
    t0 = time.perf_counter()
    eng, dual = _boot(universe, P3Engine)
    if dual is None or not eng.ready:
        return {
            "ok": False,
            "date": day,
            "require_executable_continuous": require_executable_continuous,
            "blocker": getattr(eng, "fail_reason", "dual_unavailable"),
            "elapsed_sec": round(time.perf_counter() - t0, 3),
        }
    eng.require_executable_continuous_fill = bool(require_executable_continuous)
    eng.offset_sec = 0
    eng.allowed_hm = None
    eng.fire_mode = "production"
    eng.notify_enabled = False
    eng.ingest_audit = _Discard()  # type: ignore[assignment]
    events_n, last_et = _stream_day(day, capture, eng, dual)
    eng._harvest(eng.events)
    packed = _portfolio_pack(
        eng, dual, day=day, variant="REFERENCE", offset_sec=0, events_n=events_n, last_et=last_et
    )
    fills = [_compact_fill(f, date=day) for f in (getattr(eng, "a_fills", None) or [])]
    expired = [
        {
            "date": day,
            "symbol": e.get("symbol"),
            "anchor": e.get("anchor"),
            "limit": e.get("limit"),
        }
        for e in (getattr(eng, "a_expired", None) or [])
    ]
    trades = list(packed.get("trades") or [])
    _merge_trace(trades, fills)
    out = {
        "ok": True,
        "date": day,
        "require_executable_continuous": require_executable_continuous,
        "events_processed": events_n,
        "anchor_fires": packed.get("anchor_fires"),
        "admitted": packed.get("admitted"),
        "fills_n": len(fills),
        "expired_n": len(expired),
        "trade_n": packed.get("trade_n"),
        "pnl": packed.get("pnl"),
        "PF": packed.get("PF"),
        "maxDD": packed.get("maxDD"),
        "win": packed.get("win"),
        "loss": packed.get("loss"),
        "draw": packed.get("draw"),
        "AM": packed.get("AM"),
        "PM": packed.get("PM"),
        "fills": fills,
        "expired": expired,
        "trades": trades,
        "elapsed_sec": round(time.perf_counter() - t0, 3),
    }
    del eng, dual, packed
    gc.collect()
    return out


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    t0 = time.perf_counter()
    try:
        old = _run_mode(
            day=day, capture=capture, universe=universe, require_executable_continuous=False
        )
        new = _run_mode(
            day=day, capture=capture, universe=universe, require_executable_continuous=True
        )
        ok = bool(old.get("ok") and new.get("ok"))
        print(
            f"{day} old_fills={old.get('fills_n')} new_fills={new.get('fills_n')} "
            f"old_pnl={old.get('pnl')} new_pnl={new.get('pnl')} ok={ok}",
            flush=True,
        )
        return {
            "ok": ok,
            "date": day,
            "universe_n": len(universe),
            "capture_class": payload.get("capture_class"),
            "old": old,
            "new": new,
            "elapsed_sec": round(time.perf_counter() - t0, 3),
            "blocker": None if ok else (old.get("blocker") or new.get("blocker")),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0, 3),
        }
