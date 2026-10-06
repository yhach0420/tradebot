"""Harvest causal 180s/5s board sequences. Research-only. No Runtime write."""
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

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import _bare, _boot
from research.entry_panel_exact_reconciliation.engine import CanonicalEngine
from research.entry_sequence_representation import N_MARKS, SEQUENCE_FEATURE_N
from research.entry_sequence_representation.sequence import flatten_sequence
from research.entry_target_architecture.path import session_lo_for_hm
from research.entry_target_architecture.replay import _parse_hm, stream_day_with_restarts
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_native_entry_live import _BoardBuf


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    t0w = time.perf_counter()
    try:
        _BoardBuf.compact_tail = lambda self, keep=None: None  # type: ignore[method-assign, assignment]
        eng, dual = _boot(universe, CanonicalEngine)
        if dual is None or eng is None or not getattr(eng, "ready", False):
            return {
                "ok": False,
                "date": day,
                "stage": "SEQ",
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
        for raw in list(eng.universe):
            s = _bare(raw)
            boards[s] = eng._board_arrays(s)

        rows = []
        tot = {
            "future_event_use_n": 0,
            "session_carry_n": 0,
            "itayose_state_use_n": 0,
            "special_state_use_n": 0,
            "sequence_after_t0_n": 0,
        }
        grid_bad = 0
        for r in list(getattr(eng, "contract_rows", []) or []):
            s = _bare(r.get("symbol"))
            an = str(r.get("anchor") or "")
            h, m = _parse_hm(an)
            t0 = r.get("t0")
            t0 = float(t0) if t0 is not None and t0 != "" else float(hm_epoch(day, h, m))
            t_lo = session_lo_for_hm(day, h, m)
            vec, acc = flatten_sequence(board=boards.get(s) or {}, t0=t0, t_lo=t_lo)
            if int(acc.get("grid_marks") or 0) != int(N_MARKS) or len(vec) != int(SEQUENCE_FEATURE_N):
                grid_bad += 1
            for k in tot:
                tot[k] += int(acc.get(k) or 0)
            rows.append(
                {
                    "date": day,
                    "session": r.get("session"),
                    "anchor": an,
                    "symbol": s,
                    "t0": t0,
                    "seq": [float(x) for x in vec],
                    "grid_marks": int(N_MARKS),
                    "future_event_use_n": int(acc.get("future_event_use_n") or 0),
                    "session_carry_n": int(acc.get("session_carry_n") or 0),
                    "itayose_state_use_n": int(acc.get("itayose_state_use_n") or 0),
                    "special_state_use_n": int(acc.get("special_state_use_n") or 0),
                    "sequence_after_t0_n": int(acc.get("sequence_after_t0_n") or 0),
                }
            )

        print(
            f"{day} SEQ rows={len(rows)} future={tot['future_event_use_n']} "
            f"carry={tot['session_carry_n']} itay={tot['itayose_state_use_n']} "
            f"spec={tot['special_state_use_n']} after_t0={tot['sequence_after_t0_n']} "
            f"grid_bad={grid_bad} events={events_n} last_et={last_et} restarts={len(restart_cuts)}",
            flush=True,
        )
        del eng, dual
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "stage": "SEQ",
            "rows": rows,
            "grid_bad_n": int(grid_bad),
            "n_seq_resets": len(restart_cuts),
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
            **tot,
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "stage": "SEQ",
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
