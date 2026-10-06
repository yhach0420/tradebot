"""OOF-stitched / C3 FINAL Exact Dual-Lane day replay. Occupancy approximation forbidden."""
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

from research.anchor_timing_robustness.grid import hm_epoch, hm_label
from research.anchor_timing_robustness.replay import _portfolio_pack, _pop_webhooks
from research.anchor_vs_event_driven.run_comparison import _bare, _boot, _stream_day
from research.c3_execution_coupling_audit.engine import AuditEngine
from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
from research.executable_target_v2_b_threshold.contract import session_of_anchor
from research.uniform10_b_followup.classify import classify_t0_row
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_primary_runtime import CLOCK_GRID, WAIT_SEC


def extract_would_fill(eng: Any, *, day: str) -> list[dict[str, Any]]:
    """Standalone Corrected Passive Fill + WAIT_SEC=1.0. Portfolio/CAP independent."""
    out: list[dict[str, Any]] = []
    for h, m in CLOCK_GRID:
        t0 = float(hm_epoch(day, h, m))
        anchor = hm_label(h, m)
        session = session_of_anchor(h)
        sess_end = float(session_end_epoch(day, session))
        for raw in list(eng.universe):
            s = _bare(raw)
            key = str(raw).replace(".T", "")
            rows = eng.boards.get(key) or eng.boards.get(s) or []
            board = eng._board_arrays(key)
            if board.get("t") is None or board["t"].size == 0:
                board = eng._board_arrays(s)
            if board.get("t") is None or board["t"].size == 0:
                continue
            i = int(np.searchsorted(board["t"], t0, side="right") - 1)
            if i < 0:
                continue
            src = rows[i] if i < len(rows) else {}
            klass = classify_t0_row(src, event_t=float(board["t"][i]))
            if not klass.get("executable_at_t0"):
                continue
            limit = float(board["bid"][i])
            if not np.isfinite(limit) or limit <= 0:
                continue
            fill = find_ask_cross_fill(
                board,
                t0=t0,
                wait_sec=float(WAIT_SEC),
                limit_price=limit,
                sess_end=sess_end,
                require_executable_continuous=True,
            )
            filled = bool(fill.get("filled"))
            out.append(
                {
                    "date": day,
                    "symbol": s,
                    "anchor": anchor,
                    "session": session,
                    "t0": t0,
                    "limit": limit,
                    "WOULD_FILL_1S": filled,
                    "fill_price": float(fill["fill_price"]) if filled else None,
                    "fill_t": fill.get("fill_t") if filled else None,
                    "fill_reason": None if filled else fill.get("reason"),
                }
            )
    return out


def _slim_candidates(rows: list[dict[str, Any]], *, day: str) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        out.append(
            {
                "date": day,
                "symbol": _bare(r.get("symbol")),
                "anchor": r.get("anchor"),
                "t0": r.get("t0"),
                "score": r.get("score"),
                "rank": r.get("rank"),
                "admitted": bool(r.get("admitted")),
                "bid": r.get("bid"),
            }
        )
    return out


def _slim_occ(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        out.append(
            {
                "date": r.get("date"),
                "session": r.get("session"),
                "anchor": r.get("anchor"),
                "t0": r.get("t0"),
                "open_n": r.get("open_n"),
                "pending_n": r.get("pending_n"),
                "exposure": r.get("exposure"),
                "open": r.get("open"),
                "pending": r.get("pending"),
                "position_cap": r.get("position_cap"),
            }
        )
    return out


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    _pop_webhooks()
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    fit = dict(payload.get("fit") or {})
    stage = str(payload.get("stage") or "C3_FINAL_TRACE")
    t0w = time.perf_counter()
    try:
        eng, dual = _boot(universe, AuditEngine)
        if dual is None or eng is None or not getattr(eng, "ready", False):
            return {
                "ok": False,
                "date": day,
                "stage": stage,
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
            eng, dual, day=day, variant=stage, offset_sec=0, events_n=events_n, last_et=last_et
        )
        admits = list(getattr(eng, "a_admits", []) or [])
        fills = list(getattr(eng, "a_fills", []) or [])
        expired = list(getattr(eng, "a_expired", []) or [])
        for xs in (admits, fills, expired):
            for rec in xs:
                rec["date"] = day
                rec["symbol"] = _bare(rec.get("symbol"))
        traces = list(getattr(dual, "traces", None) or [])
        slot_n = sum(1 for tr in traces if str(tr.get("event") or "") == "SLOT_RELEASE")
        would_fill = extract_would_fill(eng, day=day)
        cand_n = int(getattr(eng, "anchor_candidate_n", 0) or 0)
        trades = packed.get("trades") or []
        print(
            f"{day} {stage} trades={packed.get('trade_n')} pnl={packed.get('pnl')} "
            f"pending={len(admits)} fills={len(fills)} expired={len(expired)} "
            f"would_fill={sum(1 for r in would_fill if r.get('WOULD_FILL_1S'))}/{len(would_fill)}",
            flush=True,
        )
        occ = _slim_occ(list(getattr(eng, "anchor_occ", []) or []))
        cands = _slim_candidates(list(getattr(eng, "a_candidates", []) or []), day=day)
        cap_blocked = int(getattr(eng, "cap_blocked", 0) or 0)
        same_symbol_blocked = int(getattr(eng, "same_symbol_blocked", 0) or 0)
        del eng, dual, packed
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "stage": stage,
            "trades": trades,
            "admits": admits,
            "fills": fills,
            "expired": expired,
            "candidates": cands,
            "occupancy": occ,
            "would_fill": would_fill,
            "candidate_n": cand_n,
            "slot_release_n": slot_n,
            "cap_blocked": cap_blocked,
            "same_symbol_blocked": same_symbol_blocked,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "stage": stage,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
