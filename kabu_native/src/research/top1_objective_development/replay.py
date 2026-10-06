"""RANK1_ONLY Exact Dual-Lane day replay. Occupancy approximation forbidden."""
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
from research.top1_objective_development.engine import Top1RankEngine
from run_p0_4_exact_vs_fast_parity import _Discard


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    _pop_webhooks()
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    fit = dict(payload.get("fit") or {})
    t0w = time.perf_counter()
    try:
        eng, dual = _boot(universe, Top1RankEngine)
        if dual is None or eng is None or not getattr(eng, "ready", False):
            return {
                "ok": False,
                "date": day,
                "stage": payload.get("stage") or "TOP1_EXACT",
                "blocker": getattr(eng, "fail_reason", "dual_unavailable") if eng is not None else "boot_failed",
            }
        eng.fit = fit
        eng.require_executable_continuous_fill = True
        eng.offset_sec = 0
        eng.allowed_hm = None
        eng.fire_mode = "production"
        eng.notify_enabled = False
        eng.ingest_audit = _Discard()  # type: ignore[assignment]
        events_n, last_et = _stream_day(day, capture, eng, dual)
        eng._harvest(eng.events)
        packed = _portfolio_pack(
            eng,
            dual,
            day=day,
            variant="TOP1_RANK1_ONLY_ENTRY",
            offset_sec=0,
            events_n=events_n,
            last_et=last_et,
        )
        trades = packed.get("trades") or []
        cand_n = int(getattr(eng, "anchor_candidate_n", 0) or 0)
        print(
            f"{day} TOP1_EXACT trades={packed.get('trade_n')} pnl={packed.get('pnl')} candidates={cand_n}",
            flush=True,
        )
        del eng, dual, packed
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "stage": payload.get("stage") or "TOP1_EXACT",
            "trades": trades,
            "candidate_n": cand_n,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "stage": payload.get("stage") or "TOP1_EXACT",
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
