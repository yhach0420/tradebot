"""Exact Dual-Lane day replay for re-entry architecture variants. Occupancy forbidden."""
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
from research.anchor_vs_event_driven.run_comparison import _boot, _stream_day
from research.reentry_architecture_v1.engine import ReentryEngine
from research.uniform10_entry_rebuild import UNIFORM10
from run_p0_4_exact_vs_fast_parity import _Discard


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    _pop_webhooks()
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    stage = str(payload.get("stage") or "A_R0")
    policy = str(payload.get("reentry_policy") or "R0")
    executable_t0_only = bool(payload.get("executable_t0_only") or False)
    clock = str(payload.get("clock") or "A")
    t0w = time.perf_counter()
    try:
        eng, dual = _boot(universe, ReentryEngine)
        if dual is None or eng is None or not getattr(eng, "ready", False):
            return {
                "ok": False,
                "date": day,
                "stage": stage,
                "blocker": getattr(eng, "fail_reason", "dual_unavailable") if eng is not None else "boot_failed",
            }
        eng.reentry_policy = policy
        eng.score_pct = None
        eng.executable_t0_only = executable_t0_only
        eng.require_executable_continuous_fill = True
        eng.offset_sec = 0
        if clock == "B":
            eng.allowed_hm = UNIFORM10
            eng.fire_mode = "shifted_grid"
        else:
            eng.allowed_hm = None
            eng.fire_mode = "production"
        eng.notify_enabled = False
        eng.ingest_audit = _Discard()  # type: ignore[assignment]
        events_n, last_et = _stream_day(day, capture, eng, dual)
        eng._harvest(eng.events)
        packed = _portfolio_pack(
            eng, dual, day=day, variant=f"REENTRY_{stage}", offset_sec=0, events_n=events_n, last_et=last_et
        )
        trades = packed.get("trades") or []
        lockout_skips = int(getattr(eng, "lockout_skips", 0) or 0)
        print(
            f"{day} {stage} trades={packed.get('trade_n')} pnl={packed.get('pnl')} "
            f"lockout_skips={lockout_skips}",
            flush=True,
        )
        body = {
            "ok": True,
            "date": day,
            "stage": stage,
            "reentry_policy": policy,
            "executable_t0_only": executable_t0_only,
            "clock": clock,
            "fire_mode": eng.fire_mode,
            "trades": trades,
            "lockout_skips": lockout_skips,
            "admitted": packed.get("admitted"),
            "fills": packed.get("fills"),
            "expired": packed.get("expired"),
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
        del eng, dual, packed
        gc.collect()
        return body
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "stage": stage,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
