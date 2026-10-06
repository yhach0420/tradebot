"""Causal Exact CLOCK snapshot harvest. No Paper. No Dual-Lane strategy change."""
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

from research.anchor_vs_event_driven.run_comparison import _bare, _boot, _stream_day
from research.entry_panel_exact_reconciliation.engine import CanonicalEngine
from run_p0_4_exact_vs_fast_parity import _Discard

SNAP_KEYS = (
    "date",
    "session",
    "anchor",
    "t0",
    "symbol",
    "decision_id",
    "board_event_time",
    "snapshot_sequence",
    "event_age_sec",
    "bid",
    "ask",
    "AskSign",
    "BidSign",
    "Buy1_Sign",
    "Sell1_Sign",
    "CurrentPriceStatus",
    "boards_n",
    "buf_n",
    "raw_executable",
    "exact_executable",
    "canonical_executable",
    "raw_state",
    "exact_state",
    "current_feature_available",
    "current_score",
    "c3_feature_complete",
    "c3_live_score",
    "c3_oof_score",
    "c3_missing_reason",
    "c3_oof_missing_reason",
    "exact_in_admit_pool",
    "max_source_event_time",
    "future_event_use",
    "series_has_future_unused",
    "series_t_used",
    "lookback_t_max",
    "c3_series_cached",
    "xs_imbalance_z",
    "drawdown_180s",
    "mid_range_180s_bps",
    "vwap_dist_bps",
    "mid_abs_ret_60s",
    "mid_ret_180s",
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
    fit = dict(payload.get("c3_fit") or {})
    oof_fit = dict(payload.get("oof_fit") or {})
    t0w = time.perf_counter()
    try:
        eng, dual = _boot(universe, CanonicalEngine)
        if dual is None or eng is None or not getattr(eng, "ready", False):
            return {
                "ok": False,
                "date": day,
                "stage": "CANONICAL",
                "blocker": getattr(eng, "fail_reason", "dual_unavailable") if eng is not None else "boot_failed",
            }
        eng.c3_fit = fit
        eng.oof_fit = oof_fit
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
            snaps.append({k: r.get(k) for k in SNAP_KEYS})
        print(
            f"{day} CANONICAL snaps={len(snaps)} future={future_n} events={events_n} last_et={last_et}",
            flush=True,
        )
        del eng, dual
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "stage": "CANONICAL",
            "c3_series_policy": "collected_only",
            "snaps": snaps,
            "future_event_use_n": future_n,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "stage": "CANONICAL",
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
