"""Corrected-only causal Dual Lane replay. No post-hoc ledger filter."""
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
from small_paper.v1r_primary_runtime import WAIT_SEC


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
        t["fill_event_time"] = f.get("fill_event_time")
        t["limit_price"] = f.get("limit_price") or t.get("limit")


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    _pop_webhooks()
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    t0 = time.perf_counter()
    try:
        if WAIT_SEC != 1.0:
            return {"ok": False, "date": day, "blocker": f"WAIT_SEC={WAIT_SEC}"}
        eng, dual = _boot(universe, P3Engine)
        if dual is None or not eng.ready:
            return {
                "ok": False,
                "date": day,
                "blocker": getattr(eng, "fail_reason", "dual_unavailable"),
                "elapsed_sec": round(time.perf_counter() - t0, 3),
            }
        eng.require_executable_continuous_fill = True
        eng.offset_sec = 0
        eng.allowed_hm = None
        eng.fire_mode = "production"
        eng.notify_enabled = False
        eng.ingest_audit = _Discard()  # type: ignore[assignment]
        events_n, last_et = _stream_day(day, capture, eng, dual)
        eng._harvest(eng.events)
        packed = _portfolio_pack(
            eng, dual, day=day, variant="CORRECTED_REFERENCE", offset_sec=0, events_n=events_n, last_et=last_et
        )
        fills = [_compact_fill(f, date=day) for f in (getattr(eng, "a_fills", None) or [])]
        expired = [
            {"date": day, "symbol": e.get("symbol"), "anchor": e.get("anchor"), "limit": e.get("limit")}
            for e in (getattr(eng, "a_expired", None) or [])
        ]
        trades = list(packed.get("trades") or [])
        _merge_trace(trades, fills)
        golden = None
        if day == "20260827":
            bad = [f for f in fills if str(f.get("symbol")) == "5801" and str(f.get("anchor")) == "09:05"]
            exp = [e for e in expired if str(e.get("symbol")) == "5801" and str(e.get("anchor")) == "09:05"]
            golden = {"fills": len(bad), "expired": len(exp), "ok": len(bad) == 0}
        traces = list(getattr(dual, "traces", None) or [])
        slot_n = sum(1 for tr in traces if str(tr.get("event") or "") == "SLOT_RELEASE")
        out = {
            "ok": True,
            "date": day,
            "period": payload.get("period"),
            "require_executable_continuous": True,
            "WAIT_SEC": WAIT_SEC,
            "events_processed": events_n,
            "sequence_holes": int(getattr(eng, "native_ingest_sequence_holes", 0) or 0),
            "raw_sequence_holes": int(getattr(eng, "native_ingest_raw_sequence_holes", 0) or 0),
            "anchor_fires": packed.get("anchor_fires"),
            "admitted": packed.get("admitted"),
            "fills_n": len(fills),
            "expired_n": len(expired),
            "cap_blocked": packed.get("cap_blocked"),
            "same_symbol_blocked": packed.get("same_symbol_blocked"),
            "slot_release_n": slot_n,
            "trade_n": packed.get("trade_n"),
            "pnl": packed.get("pnl"),
            "PF": packed.get("PF"),
            "maxDD": packed.get("maxDD"),
            "win": packed.get("win"),
            "loss": packed.get("loss"),
            "draw": packed.get("draw"),
            "AM": packed.get("AM"),
            "PM": packed.get("PM"),
            "golden_5801_0905": golden,
            "fills": fills,
            "expired": expired,
            "trades": trades,
            "elapsed_sec": round(time.perf_counter() - t0, 3),
        }
        del eng, dual, packed
        gc.collect()
        print(
            f"{day} trades={out['trade_n']} pnl={out['pnl']} fills={out['fills_n']} "
            f"expired={out['expired_n']} cap={out['cap_blocked']} same={out['same_symbol_blocked']}",
            flush=True,
        )
        return out
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0, 3),
        }
