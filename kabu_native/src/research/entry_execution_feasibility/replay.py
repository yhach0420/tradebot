"""Standalone Passive Fill harvest. CanonicalEngine. No portfolio/CAP. No Runtime write."""
from __future__ import annotations

import gc
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import _bare, _boot
from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.entry_execution_feasibility.fill import early_returns, limit_bid_at_t0, standalone_fill
from research.entry_panel_exact_reconciliation.engine import CanonicalEngine
from research.entry_target_architecture.path import path_targets, session_lo_for_hm, valid_mid_series
from research.entry_target_architecture.replay import _parse_hm, stream_day_with_restarts
from research.executable_target_v2_b_threshold.contract import session_of_anchor
from research.target_price_contract_v4.persist import reversal_cut_times
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_native_entry_live import _BoardBuf
from small_paper.v1r_primary_runtime import WAIT_SEC

JST = ZoneInfo("Asia/Tokyo")


def _hm_from_epoch(ts: float) -> tuple[int, int]:
    dt = datetime.fromtimestamp(float(ts), tz=JST)
    return int(dt.hour), int(dt.minute)


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    cands = list(payload.get("candidates") or [])
    t0w = time.perf_counter()
    try:
        _BoardBuf.compact_tail = lambda self, keep=None: None  # type: ignore[method-assign, assignment]
        eng, dual = _boot(universe, CanonicalEngine)
        if dual is None or eng is None or not getattr(eng, "ready", False):
            return {
                "ok": False,
                "date": day,
                "stage": "FILL",
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

        boards: dict[str, dict] = {}
        series_by: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        rev_by: dict[str, list[float]] = {}
        empty = np.asarray([], dtype=float)
        for raw in list(eng.universe):
            s = _bare(raw)
            board = eng._board_arrays(s)
            boards[s] = board
            vacant = board.get("t") is None or board["t"].size == 0
            rev_by[s] = [] if vacant else reversal_cut_times(board["t"])
            series_by[s] = (empty, empty) if vacant else valid_mid_series(board)

        rows = []
        miss_board = 0
        miss_limit = 0
        for c in cands:
            s = _bare(c.get("symbol"))
            an = str(c.get("anchor") or "")
            h, m = _parse_hm(an)
            t0 = c.get("t0")
            t0 = float(t0) if t0 is not None and t0 != "" else float(hm_epoch(day, h, m))
            session = str(c.get("session") or session_of_anchor(h))
            sess_end = float(session_end_epoch(day, session))
            board = boards.get(s) or {}
            rec = {
                "date": day,
                "anchor": an,
                "symbol": s,
                "session": session,
                "t0": t0,
                "WOULD_FILL": False,
                "WOULD_EXPIRE": True,
                "fill_price": None,
                "fill_t": None,
                "fill_reason": None,
                "nonfill_class": None,
                "ret_1s": None,
                "ret_5s": None,
                "ret_30s": None,
                "MFE_FROM_FILL": None,
                "DOWNSIDE_FROM_FILL": None,
                "fill_path_complete": False,
            }
            if board.get("t") is None or board["t"].size == 0:
                miss_board += 1
                rec["nonfill_class"] = "NO_VALID_CONTINUOUS_BOARD"
                rec["fill_reason"] = "NO_BOARD"
                rows.append(rec)
                continue
            limit = limit_bid_at_t0(board, t0)
            if limit is None:
                miss_limit += 1
                rec["nonfill_class"] = "OTHER"
                rec["fill_reason"] = "INVALID_LIMIT"
                rows.append(rec)
                continue
            rec["limit"] = float(limit)
            got = standalone_fill(
                board,
                t0=t0,
                wait_sec=float(WAIT_SEC),
                limit_price=float(limit),
                sess_end=sess_end,
            )
            rec.update(got)
            vt, vm = series_by.get(s) or (empty, empty)
            slo = session_lo_for_hm(day, h, m)
            t_lo = float(slo) if slo is not None else float("-inf")
            rec.update(early_returns(vt, vm, t0=t0, t_lo=t_lo))
            ft = rec.get("fill_t")
            if rec.get("WOULD_FILL") and ft is not None:
                fh, fm = _hm_from_epoch(float(ft))
                path = path_targets(
                    day=day,
                    h=fh,
                    m=fm,
                    t0=float(ft),
                    valid_t=vt,
                    valid_mid=vm,
                    reversal_cuts=rev_by.get(s) or [],
                    restart_cuts=restart_cuts,
                )
                rec["MFE_FROM_FILL"] = path.get("T1")
                rec["DOWNSIDE_FROM_FILL"] = path.get("T2")
                rec["fill_path_complete"] = bool(path.get("path_complete"))
            rows.append(rec)

        print(
            f"{day} FILL rows={len(rows)} miss_board={miss_board} miss_limit={miss_limit} "
            f"fill={sum(1 for r in rows if r.get('WOULD_FILL'))} events={events_n} last_et={last_et}",
            flush=True,
        )
        del eng, dual
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "stage": "FILL",
            "rows": rows,
            "miss_board_n": miss_board,
            "miss_limit_n": miss_limit,
            "n_seq_resets": len(restart_cuts),
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "stage": "FILL",
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
