"""Causal A2 Exact day replay with contract snapshots. Occupancy approximation forbidden."""
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

from research.anchor_timing_robustness.replay import _portfolio_pack, _pop_webhooks
from research.anchor_vs_event_driven.run_comparison import _bare, _boot, _stream_day
from research.entry_decision_population_contract.engine import ContractEngine
from run_p0_4_exact_vs_fast_parity import _Discard


def _stamp(xs: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    for rec in xs:
        rec["date"] = day
        rec["symbol"] = _bare(rec.get("symbol"))
    return xs


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    _pop_webhooks()
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    fit = dict(payload.get("c3_fit") or {})
    t0w = time.perf_counter()
    try:
        eng, dual = _boot(universe, ContractEngine)
        if dual is None or eng is None or not getattr(eng, "ready", False):
            return {
                "ok": False,
                "date": day,
                "stage": "CONTRACT",
                "blocker": getattr(eng, "fail_reason", "dual_unavailable") if eng is not None else "boot_failed",
            }
        eng.c3_fit = fit
        eng.score_pct = None
        eng.executable_t0_only = True
        eng.require_executable_continuous_fill = True
        eng.offset_sec = 0
        eng.allowed_hm = None
        eng.fire_mode = "production"
        eng.notify_enabled = False
        eng.ingest_audit = _Discard()  # type: ignore[assignment]
        events_n, last_et = _stream_day(day, capture, eng, dual)
        eng._harvest(eng.events)
        packed = _portfolio_pack(
            eng, dual, day=day, variant="CONTRACT_A2", offset_sec=0, events_n=events_n, last_et=last_et
        )
        admits = _stamp(list(getattr(eng, "a_admits", []) or []), day)
        fills = _stamp(list(getattr(eng, "a_fills", []) or []), day)
        expired = _stamp(list(getattr(eng, "a_expired", []) or []), day)
        for a in admits:
            a["pending_id"] = f"{day}|{a.get('anchor')}|{a.get('symbol')}|PENDING"
            a["decision_id"] = f"{day}|{a.get('anchor')}|{a.get('symbol')}"
        snaps = []
        for r in list(getattr(eng, "contract_rows", []) or []):
            snaps.append(
                {
                    k: r.get(k)
                    for k in (
                        "date",
                        "session",
                        "anchor",
                        "t0",
                        "symbol",
                        "decision_id",
                        "board_event_time",
                        "snapshot_sequence",
                        "bid",
                        "ask",
                        "AskSign",
                        "BidSign",
                        "CurrentPriceStatus",
                        "raw_executable",
                        "exact_executable",
                        "raw_state",
                        "exact_state",
                        "current_feature_available",
                        "current_score",
                        "c3_feature_complete",
                        "c3_live_score",
                        "c3_missing_reason",
                        "exact_in_admit_pool",
                    )
                }
            )
        trades = packed.get("trades") or []
        print(
            f"{day} CONTRACT trades={packed.get('trade_n')} pnl={packed.get('pnl')} "
            f"pending={len(admits)} snaps={len(snaps)}",
            flush=True,
        )
        del eng, dual, packed
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "stage": "CONTRACT",
            "trades": trades,
            "admits": admits,
            "fills": fills,
            "expired": expired,
            "snaps": snaps,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "stage": "CONTRACT",
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
