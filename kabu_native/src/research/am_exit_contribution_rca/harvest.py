"""Capture-day board stream → post-fill path metrics. No EXIT/ENTRY rewrite."""
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

from research.am_exit_contribution_rca.contract import canonical_eval_end
from research.am_exit_contribution_rca.path import path_metrics
from research.anchor_vs_event_driven.run_comparison import _bare, _boot
from research.canonical_entry_performance_rebase.analyze import row_key
from research.entry_panel_exact_reconciliation.engine import CanonicalEngine
from research.entry_target_architecture.replay import stream_day_with_restarts
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_live_dual_lane import session_end_for_position
from small_paper.v1r_native_entry_live import _BoardBuf


def process_path_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    fills = list(payload.get("fills") or [])
    horizons = dict(payload.get("horizons") or {})
    t0w = time.perf_counter()
    leak_tot = {
        "ITAYOSE_PATH_USE_N": 0,
        "SPECIAL_BOARD_PATH_USE_N": 0,
        "SESSION_CARRY_N": 0,
        "INVALID_QUOTE_USE_N": 0,
        "ITAYOSE_SKIP_N": 0,
        "SPECIAL_SKIP_N": 0,
        "INVALID_SKIP_N": 0,
    }
    try:
        _BoardBuf.compact_tail = lambda self, keep=None: None  # type: ignore[method-assign, assignment]
        eng, dual = _boot(universe, CanonicalEngine)
        if dual is None or eng is None or not getattr(eng, "ready", False):
            return {
                "ok": False,
                "date": day,
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
        events_n, last_et, _cuts = stream_day_with_restarts(day, capture, eng, dual)
        boards: dict[str, dict] = {}
        for raw in list(eng.universe):
            s = _bare(raw)
            boards[s] = eng._board_arrays(s)
        rows = []
        miss_board = 0
        for c in fills:
            s = _bare(c.get("symbol"))
            board = boards.get(s) or {}
            rec = {
                "date": day,
                "anchor": c.get("anchor"),
                "symbol": s,
                "session": c.get("session") or "AM",
                "fill_t": c.get("fill_t"),
                "fill_price": c.get("fill_price"),
                "exit_t": c.get("exit_t"),
                "realized_pnl_yen_100": c.get("realized_pnl_yen_100"),
                "arm": c.get("arm"),
                "row_key": c.get("row_key") or row_key({"date": day, "anchor": c.get("anchor"), "symbol": s}),
            }
            if board.get("t") is None or board["t"].size == 0:
                miss_board += 1
                rec["ok"] = False
                rec["blocker"] = "NO_BOARD"
                rows.append(rec)
                continue
            ft = c.get("fill_t")
            fp = c.get("fill_price")
            et = c.get("exit_t")
            if ft is None or fp is None or et is None:
                rec["ok"] = False
                rec["blocker"] = "MISSING_FILL_OR_EXIT"
                rows.append(rec)
                continue
            sess = str(c.get("session") or "AM")
            sess_end = float(session_end_for_position(date=day, session=sess, fill_time=float(ft)))
            eval_end = canonical_eval_end(date=day, session=sess, fill_t=float(ft), horizons=horizons)
            got = path_metrics(
                board,
                fill_t=float(ft),
                fill_px=float(fp),
                exit_t=float(et),
                realized_pnl=float(c.get("realized_pnl_yen_100") or 0.0),
                eval_end=float(eval_end),
                sess_end=sess_end,
            )
            rec.update(got)
            rec["SESSION_END"] = sess_end
            rec["HOLDING_SEC"] = float(et) - float(ft)
            ig = dict(got.get("integrity") or {})
            for k in leak_tot:
                leak_tot[k] += int(ig.get(k) or 0)
            rec.pop("integrity", None)
            rows.append(rec)
        fail_n = sum(1 for r in rows if not r.get("ok"))
        print(
            f"{day} PATH fills={len(fills)} ok={len(rows) - fail_n} miss_board={miss_board} "
            f"events={events_n} last_et={last_et}",
            flush=True,
        )
        del eng, dual
        gc.collect()
        return {
            "ok": fail_n == 0,
            "date": day,
            "rows": rows,
            "miss_board_n": miss_board,
            "fail_n": fail_n,
            "leak": leak_tot,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
            "blocker": None if fail_n == 0 else "PATH_SIM_FAIL",
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
