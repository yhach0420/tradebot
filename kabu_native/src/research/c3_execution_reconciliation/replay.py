"""COMMON_SCORABLE Exact Dual-Lane day replay. Occupancy approximation forbidden."""
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
from research.c3_execution_reconciliation.engine import CommonA2Engine, LookupC3Engine
from run_p0_4_exact_vs_fast_parity import _Discard


def dual_counts(traces: list[dict[str, Any]]) -> dict[str, int]:
    admit_p = 0
    exit_p = 0
    for row in traces:
        ev = str(row.get("event") or "")
        lane = str(row.get("lane") or "")
        if ev == "ADMIT" and lane == "primary":
            admit_p += 1
        elif ev == "EXIT_EXECUTED" and lane == "primary":
            exit_p += 1
    return {"DUAL_ADMIT_PRIMARY_N": admit_p, "DUAL_EXIT_PRIMARY_N": exit_p}


def _stamp(xs: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    for rec in xs:
        rec["date"] = day
        rec["symbol"] = _bare(rec.get("symbol"))
    return xs


def process_a2_common(payload: dict[str, Any]) -> dict[str, Any]:
    _pop_webhooks()
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    allowed = set(payload.get("allowed_keys") or [])
    t0w = time.perf_counter()
    try:
        eng, dual = _boot(universe, CommonA2Engine)
        if dual is None or eng is None or not getattr(eng, "ready", False):
            return {
                "ok": False,
                "date": day,
                "stage": "A2_COMMON",
                "blocker": getattr(eng, "fail_reason", "dual_unavailable") if eng is not None else "boot_failed",
            }
        eng.allowed_keys = allowed
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
            eng, dual, day=day, variant="A2_COMMON", offset_sec=0, events_n=events_n, last_et=last_et
        )
        traces = list(getattr(dual, "traces", None) or [])
        dc = dual_counts(traces)
        admits = _stamp(list(getattr(eng, "a_admits", []) or []), day)
        fills = _stamp(list(getattr(eng, "a_fills", []) or []), day)
        expired = _stamp(list(getattr(eng, "a_expired", []) or []), day)
        trades = packed.get("trades") or []
        print(
            f"{day} A2_COMMON trades={packed.get('trade_n')} pnl={packed.get('pnl')} "
            f"pending={len(admits)} harvest_fill={len(fills)} dual_fill={dc['DUAL_ADMIT_PRIMARY_N']} "
            f"exit={dc['DUAL_EXIT_PRIMARY_N']}",
            flush=True,
        )
        del eng, dual, packed
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "stage": "A2_COMMON",
            "trades": trades,
            "admits": admits,
            "fills": fills,
            "expired": expired,
            **dc,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "stage": "A2_COMMON",
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }


def process_c3_common(payload: dict[str, Any]) -> dict[str, Any]:
    _pop_webhooks()
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    lookup = dict(payload.get("score_lookup") or {})
    allowed = set(payload.get("allowed_keys") or [])
    stage = str(payload.get("stage") or "C3_COMMON")
    t0w = time.perf_counter()
    try:
        eng, dual = _boot(universe, LookupC3Engine)
        if dual is None or eng is None or not getattr(eng, "ready", False):
            return {
                "ok": False,
                "date": day,
                "stage": stage,
                "blocker": getattr(eng, "fail_reason", "dual_unavailable") if eng is not None else "boot_failed",
            }
        eng.allowed_keys = allowed
        eng.score_lookup = lookup
        eng.require_executable_continuous_fill = True
        eng.offset_sec = 0
        eng.allowed_hm = None
        eng.fire_mode = "production"
        eng.notify_enabled = False
        eng.ingest_audit = _Discard()  # type: ignore[assignment]
        events_n, last_et = _stream_day(day, capture, eng, dual)
        eng._harvest(eng.events)
        packed = _portfolio_pack(
            eng, dual, day=day, variant=stage, offset_sec=0, events_n=events_n, last_et=last_et
        )
        traces = list(getattr(dual, "traces", None) or [])
        dc = dual_counts(traces)
        admits = _stamp(list(getattr(eng, "a_admits", []) or []), day)
        fills = _stamp(list(getattr(eng, "a_fills", []) or []), day)
        expired = _stamp(list(getattr(eng, "a_expired", []) or []), day)
        trades = packed.get("trades") or []
        print(
            f"{day} {stage} trades={packed.get('trade_n')} pnl={packed.get('pnl')} "
            f"pending={len(admits)} harvest_fill={len(fills)} dual_fill={dc['DUAL_ADMIT_PRIMARY_N']} "
            f"exit={dc['DUAL_EXIT_PRIMARY_N']}",
            flush=True,
        )
        del eng, dual, packed
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "stage": stage,
            "trades": trades,
            "admits": admits,
            "fills": fills,
            "expired": expired,
            **dc,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "stage": stage,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    kind = str(payload.get("engine") or payload.get("stage") or "")
    if kind in {"A2", "A2_COMMON"}:
        return process_a2_common(payload)
    return process_c3_common(payload)
