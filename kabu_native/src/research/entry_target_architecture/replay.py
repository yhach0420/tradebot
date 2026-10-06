"""CanonicalEngine day harvest + 1s path targets. No MarkBoardBuf. No Dual-Lane strategy change."""
from __future__ import annotations

import gc
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    _boot,
    capture_event_epoch,
    iter_push,
    record_event_stamp,
)
from research.entry_objective_redesign_c3 import F0_CURRENT6, F1_C2_6
from research.entry_panel_exact_reconciliation.engine import CanonicalEngine
from research.entry_target_architecture.path import path_targets, valid_mid_series
from research.target_price_contract_v4.persist import reversal_cut_times
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_native_entry_live import _BoardBuf

JST = ZoneInfo("Asia/Tokyo")

SNAP_KEYS = (
    "date",
    "session",
    "anchor",
    "t0",
    "symbol",
    "decision_id",
    "board_event_time",
    "max_source_event_time",
    "series_has_future_unused",
    "exact_executable",
    "canonical_executable",
    "current_score",
    "future_event_use",
    "xs_imbalance_z",
    *F0_CURRENT6,
    *(f for f in F1_C2_6 if f != "xs_imbalance_z"),
)


def _parse_hm(anchor: str) -> tuple[int, int]:
    hh, mm = str(anchor).split(":")
    return int(hh), int(mm)


def stream_day_with_restarts(day: str, capture_dir: Path, eng: Any, dual: Any) -> tuple[int, Optional[float], list[float]]:
    from research.e1_x22_actual_exit_factory.paths import session_end_epoch
    from small_paper.v1r_live_dual_lane import canonical_symbol_key

    last_dual_t: dict[str, float] = {}
    events_n = 0
    last_et: Optional[float] = None
    last_seq: Optional[int] = None
    restart_cuts: list[float] = []
    am_end = session_end_epoch(day, "AM")
    pm_end = session_end_epoch(day, "PM")
    am_closed = False
    pm_closed = False

    def maybe_close(t: float) -> None:
        nonlocal am_closed, pm_closed
        if not am_closed and t + 1e-9 >= am_end:
            dual.close_open_at_session_end(event_t=am_end, session="AM")
            eng.on_tick_fill_check(event_t=am_end)
            am_closed = True
        if not pm_closed and t + 1e-9 >= pm_end:
            dual.close_open_at_session_end(event_t=pm_end, session="PM")
            eng.on_tick_fill_check(event_t=pm_end)
            pm_closed = True

    for rec in iter_push(capture_dir):
        recv = record_event_stamp(rec)
        seq = int(rec.get("sequence") or 0)
        sym = _bare(rec.get("symbol"))
        pay = dict(rec.get("payload") or rec.get("original_payload") or {})
        et = capture_event_epoch(rec, pay)
        if last_seq is not None and seq > 0 and last_seq > 0 and seq + 1 < last_seq and et is not None:
            restart_cuts.append(float(et))
        if seq > 0:
            last_seq = seq
        if et is None:
            continue
        recv = recv or datetime.fromtimestamp(float(et), JST).isoformat(timespec="milliseconds")
        pay["received_at"] = recv
        pay["recorded_at"] = recv
        pay["sequence"] = seq
        pay["__ingress_sequence__"] = seq
        pay["__ingress_received_at__"] = recv
        last_et = float(et)
        events_n += 1
        maybe_close(float(et))
        eng.process_market_push(symbol=sym, payload=pay, event_t=float(et))
        if hasattr(eng, "on_stream_event"):
            eng.on_stream_event(sym, float(et))
        key = canonical_symbol_key(sym)
        open_here = (key in dual.primary and not dual.primary[key].closed) or (
            key in dual.control and not dual.control[key].closed
        )
        if open_here:
            prev = last_dual_t.get(key)
            if prev is None or (float(et) - prev) >= 0.5 - 1e-12:
                last_dual_t[key] = float(et)
                dual.on_tick(symbol=sym, payload=pay, event_t=float(et), push_sequence=seq)
        if events_n % 200000 == 0:
            eng.events.clear()
            if getattr(eng, "ingest_audit", None) is not None:
                eng.ingest_audit.clear()
    if last_et is not None:
        maybe_close(float(last_et))
    return events_n, last_et, restart_cuts


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    t0w = time.perf_counter()
    try:
        # Research-only: live compact_tail(20000) would drop morning quotes before
        # a post-stream 600s path. Runtime CanonicalEngine is not modified.
        _BoardBuf.compact_tail = lambda self, keep=None: None  # type: ignore[method-assign, assignment]
        eng, dual = _boot(universe, CanonicalEngine)
        if dual is None or eng is None or not getattr(eng, "ready", False):
            return {
                "ok": False,
                "date": day,
                "stage": "PATH",
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
        events_n, last_et, restart_cuts = stream_day_with_restarts(day, capture, eng, dual)

        series_by: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        rev_by: dict[str, list[float]] = {}
        empty = np.asarray([], dtype=float)
        for raw in list(eng.universe):
            s = _bare(raw)
            board = eng._board_arrays(s)
            vacant = board.get("t") is None or board["t"].size == 0
            rev_by[s] = [] if vacant else reversal_cut_times(board["t"])
            series_by[s] = (empty, empty) if vacant else valid_mid_series(board)

        rows = []
        future_snap = 0
        for r in list(getattr(eng, "contract_rows", []) or []):
            if r.get("future_event_use"):
                future_snap += 1
            s = _bare(r.get("symbol"))
            an = str(r.get("anchor") or "")
            h, m = _parse_hm(an)
            t0 = r.get("t0")
            t0 = float(t0) if t0 is not None and t0 != "" else float(hm_epoch(day, h, m))
            vt, vm = series_by.get(s) or (empty, empty)
            path = path_targets(
                day=day,
                h=h,
                m=m,
                t0=t0,
                valid_t=vt,
                valid_mid=vm,
                reversal_cuts=rev_by.get(s) or [],
                restart_cuts=restart_cuts,
            )
            rec = {k: r.get(k) for k in SNAP_KEYS}
            rec["date"] = day
            rec["anchor"] = an
            rec["t0"] = t0
            rec["symbol"] = s
            rec["executable_at_t0"] = bool(r.get("exact_executable") or r.get("canonical_executable"))
            rec.update(path)
            rec["future_event_use"] = bool(path.get("future_event_use") or r.get("future_event_use"))
            rows.append(rec)

        print(
            f"{day} PATH rows={len(rows)} future_snap={future_snap} restarts={len(restart_cuts)} "
            f"events={events_n} last_et={last_et}",
            flush=True,
        )
        del eng, dual
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "stage": "PATH",
            "rows": rows,
            "future_event_use_n": future_snap,
            "n_seq_resets": len(restart_cuts),
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "stage": "PATH",
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
