"""CanonicalEngine training-row harvest. No KEEPALL patch. No Paper. No Dual-Lane strategy change."""
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

from research.anchor_vs_event_driven.run_comparison import _boot, _stream_day
from research.entry_objective_redesign_c3 import F0_CURRENT6, F1_C2_6
from research.entry_panel_exact_reconciliation.engine import CanonicalEngine
from run_p0_4_exact_vs_fast_parity import _Discard

TRAIN_KEYS = (
    "date",
    "session",
    "anchor",
    "t0",
    "symbol",
    "decision_id",
    "board_event_time",
    "event_age_sec",
    "bid",
    "ask",
    "exact_executable",
    "canonical_executable",
    "current_score",
    "future_event_use",
    "series_has_future_unused",
    "max_source_event_time",
    "xs_imbalance_z",
    *F0_CURRENT6,
    *(f for f in F1_C2_6 if f != "xs_imbalance_z"),
    "cur_spread_bps",
    "cur_imbalance",
    "cur_mid_ret_60s",
    "cur_mid_ret_180s",
    "cur_event_rate_60s",
    "cur_log_bid_qty",
)


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    t0w = time.perf_counter()
    try:
        eng, dual = _boot(universe, CanonicalEngine)
        if dual is None or eng is None or not getattr(eng, "ready", False):
            return {
                "ok": False,
                "date": day,
                "stage": "TRAIN",
                "blocker": getattr(eng, "fail_reason", "dual_unavailable") if eng is not None else "boot_failed",
            }
        eng.c3_fit = {}
        eng.oof_fit = {}
        eng.score_pct = None
        eng.executable_t0_only = True
        eng.require_executable_continuous_fill = True
        eng.offset_sec = 0
        eng.allowed_hm = None
        eng.fire_mode = "production"
        eng.notify_enabled = False
        eng.ingest_audit = _Discard()  # type: ignore[assignment]
        events_n, last_et = _stream_day(day, capture, eng, dual)
        snaps = []
        future_n = 0
        for r in list(getattr(eng, "contract_rows", []) or []):
            if r.get("future_event_use"):
                future_n += 1
            snaps.append({k: r.get(k) for k in TRAIN_KEYS})
        print(
            f"{day} TRAIN snaps={len(snaps)} future={future_n} events={events_n} last_et={last_et}",
            flush=True,
        )
        del eng, dual
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "stage": "TRAIN",
            "c3_series_policy": "collected_only",
            "train_features": "causal_f0_f1",
            "snaps": snaps,
            "future_event_use_n": future_n,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "stage": "TRAIN",
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
