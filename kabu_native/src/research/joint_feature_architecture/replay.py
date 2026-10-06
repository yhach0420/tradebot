"""Harvest causal P/L/M/X features from CanonicalEngine Capture stream. Research-only. No Runtime write."""
from __future__ import annotations

import gc
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

import numpy as np

from research.anchor_timing_robustness.grid import hm_epoch
from research.entry_panel_exact_reconciliation.engine import CanonicalEngine
from research.entry_target_architecture.path import session_lo_for_hm, valid_mid_series
from research.entry_target_architecture.replay import _parse_hm
from research.joint_feature_architecture import JOIN_KEYS
from research.joint_feature_architecture.features import (
    attach_block_x,
    board_micro_arrays,
    features_at,
    vol_arrays,
)
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_native_entry_live import _BoardBuf, _f, extract_board_row


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    t0w = time.perf_counter()
    try:
        _BoardBuf.compact_tail = lambda self, keep=None: None  # type: ignore[method-assign, assignment]
        from research.anchor_vs_event_driven.run_comparison import _boot

        eng, dual = _boot(universe, CanonicalEngine)
        if dual is None or eng is None or not getattr(eng, "ready", False):
            return {
                "ok": False,
                "date": day,
                "stage": "FEAT",
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

        vol_events: dict[str, list[tuple[float, Optional[float], Optional[float]]]] = defaultdict(list)
        from research.anchor_vs_event_driven.run_comparison import (
            _bare,
            capture_event_epoch,
            iter_push,
            record_event_stamp,
        )
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from research.e1_x22_actual_exit_factory.paths import session_end_epoch
        from small_paper.v1r_live_dual_lane import canonical_symbol_key

        JST = ZoneInfo("Asia/Tokyo")
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

        for rec in iter_push(capture):
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
            brow = extract_board_row(pay, float(et))
            tv = _f(brow.get("TradingVolume"))
            bid = _f(brow.get("bid"))
            ask = _f(brow.get("ask"))
            mid = (bid + ask) / 2.0 if bid is not None and ask is not None and bid > 0 and ask > bid else None
            vol_events[sym].append((float(et), tv, mid))
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

        series_by: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        micro_by: dict[str, dict[str, np.ndarray]] = {}
        vol_by: dict[str, dict[str, np.ndarray]] = {}
        empty = np.asarray([], dtype=float)
        for raw in list(eng.universe):
            s = _bare(raw)
            board = eng._board_arrays(s)
            vacant = board.get("t") is None or board["t"].size == 0
            series_by[s] = (empty, empty) if vacant else valid_mid_series(board)
            micro_by[s] = board_micro_arrays({} if vacant else board)
            vol_by[s] = vol_arrays(vol_events.get(s) or [])

        rows = []
        future_n = 0
        for r in list(getattr(eng, "contract_rows", []) or []):
            s = _bare(r.get("symbol"))
            an = str(r.get("anchor") or "")
            h, m = _parse_hm(an)
            t0 = r.get("t0")
            t0 = float(t0) if t0 is not None and t0 != "" else float(hm_epoch(day, h, m))
            t_lo = session_lo_for_hm(day, h, m)
            vt, vm = series_by.get(s) or (empty, empty)
            feat, fut = features_at(
                t_mid=vt,
                mid=vm,
                micro=micro_by.get(s) or board_micro_arrays({}),
                vol=vol_by.get(s) or vol_arrays([]),
                t0=t0,
                t_lo=t_lo,
                event_rate_60s=r.get("event_rate_60s"),
                spread_bps=r.get("spread_bps"),
            )
            future_n += int(fut)
            rec = {
                "date": day,
                "session": r.get("session"),
                "anchor": an,
                "symbol": s,
                "t0": t0,
                "future_event_use_n": int(fut),
            }
            rec.update(feat)
            rows.append(rec)

        attach_block_x(rows)
        slim = []
        for rec in rows:
            out = {
                "date": rec.get("date"),
                "anchor": rec.get("anchor"),
                "symbol": rec.get("symbol"),
                "session": rec.get("session"),
                "future_event_use_n": rec.get("future_event_use_n"),
            }
            for k in JOIN_KEYS:
                out[k] = rec.get(k)
            slim.append(out)

        print(
            f"{day} FEAT rows={len(slim)} future={future_n} events={events_n}",
            flush=True,
        )
        del eng, dual
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "stage": "FEAT",
            "rows": slim,
            "future_event_use_n": int(future_n),
            "n_seq_resets": len(restart_cuts),
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "stage": "FEAT",
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
