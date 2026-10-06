"""Harvest 180x11 causal 1s sequences from Capture. Research-only. No Runtime write."""
from __future__ import annotations

import gc
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_event_sequence_architecture.bins import bin_sequence
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import _bare, _boot
from research.canonical_entry_performance_rebase.analyze import row_key, session_of
from research.entry_panel_exact_reconciliation.engine import CanonicalEngine
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
    want = set(str(k) for k in (payload.get("keys") or []))
    out_path = Path(str(payload["out_path"]))
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

        keys: list[str] = []
        xs: list[np.ndarray] = []
        tot = {
            "event_time_after_t0_n": 0,
            "session_carry_n": 0,
            "itayose_event_use_n": 0,
            "special_event_use_n": 0,
            "future_event_use_n": 0,
            "same_timestamp_after_decision_use_n": 0,
            "pm_rows_used_n": 0,
        }
        for r in list(getattr(eng, "contract_rows", []) or []):
            rec = {
                "date": day,
                "session": r.get("session"),
                "anchor": str(r.get("anchor") or ""),
                "symbol": _bare(r.get("symbol")),
            }
            k = row_key(rec)
            if want and k not in want:
                continue
            if session_of(rec) != "AM":
                tot["pm_rows_used_n"] += 1
                continue
            s = rec["symbol"]
            an = rec["anchor"]
            h, m = _parse_hm(an)
            t0 = r.get("t0")
            t0 = float(t0) if t0 is not None and t0 != "" else float(hm_epoch(day, h, m))
            t_lo = session_lo_for_hm(day, h, m)
            seq, acc = bin_sequence(board=boards.get(s) or {}, t0=t0, t_lo=t_lo)
            for name in tot:
                if name in acc:
                    tot[name] += int(acc.get(name) or 0)
            keys.append(k)
            xs.append(seq)

        X = np.stack(xs, axis=0) if xs else np.zeros((0, 180, 11), dtype=np.float32)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(out_path, keys=np.asarray(keys), X=X)
        print(
            f"{day} SEQ n={len(keys)} after_t0={tot['event_time_after_t0_n']} "
            f"future={tot['future_event_use_n']} same_ts={tot['same_timestamp_after_decision_use_n']} "
            f"events={events_n} last_et={last_et} restarts={len(restart_cuts)}",
            flush=True,
        )
        del eng, dual
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "stage": "SEQ",
            "n": len(keys),
            "out_path": str(out_path),
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
