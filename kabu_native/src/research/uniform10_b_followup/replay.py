"""Exact Dual-Lane day replay. Occupancy approximation forbidden."""
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

from research.anchor_10min_opportunity.replay import _session_fallback
from research.anchor_timing_robustness.grid import hm_epoch, hm_label
from research.anchor_timing_robustness.replay import _portfolio_pack, _pop_webhooks
from research.anchor_vs_event_driven.run_comparison import _boot, _stream_day
from research.e1_x34b_entry_execution.features import preentry_from_board
from research.uniform10_b_followup.classify import classify_t0_row
from research.uniform10_b_followup.engine import BFollowEngine
from research.uniform10_entry_rebuild import UNIFORM10
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_native_entry_live import FEATURE_ORDER


def _opens_within_1s(board: dict[str, np.ndarray], t0: float) -> bool:
    t = board.get("t")
    if t is None or t.size == 0:
        return False
    exe = board.get("executable")
    lo = int(np.searchsorted(t, float(t0), side="right"))
    hi = int(np.searchsorted(t, float(t0) + 1.0, side="right"))
    for j in range(lo, hi):
        if float(t[j]) <= float(t0) + 1e-12:
            continue
        if exe is not None and j < int(exe.size) and bool(exe[j]):
            return True
    return False


def extract_rank_rows(eng: Any, *, day: str) -> list[dict[str, Any]]:
    """Rebuild CURRENT ENTRY score/rank at each UNIFORM10 t0. Diagnostic only. No gate."""
    out: list[dict[str, Any]] = []
    for h, m in UNIFORM10:
        t0 = hm_epoch(day, h, m)
        anchor = hm_label(h, m)
        session = _session_fallback(day, h, m, t0) or ("AM" if h < 12 else "PM")
        scored: list[dict[str, Any]] = []
        for sym in list(eng.universe):
            s = str(sym).replace(".T", "")
            rows = eng.boards.get(s) or []
            board = eng._board_arrays(s)
            if board["t"].size == 0:
                continue
            i = int(np.searchsorted(board["t"], t0, side="right") - 1)
            if i < 0:
                continue
            src = rows[i] if i < len(rows) else {}
            feats = preentry_from_board(board, t0)
            if any(feats.get(f) is None or not np.isfinite(feats.get(f)) for f in FEATURE_ORDER):
                continue
            score = float(eng.score_fn(feats))
            if not np.isfinite(score):
                continue
            limit = float(board["bid"][i])
            if not np.isfinite(limit) or limit <= 0:
                continue
            klass = classify_t0_row(src, event_t=float(board["t"][i]))
            scored.append(
                {
                    "date": day,
                    "anchor": anchor,
                    "session": session,
                    "symbol": s,
                    "t0": float(t0),
                    "score": score,
                    "executable_at_t0": bool(klass["executable_at_t0"]),
                    "nonexec_bucket": klass.get("nonexec_bucket"),
                    "board_state": klass.get("state"),
                }
            )
        scored.sort(key=lambda e: (-float(e["score"]), str(e["symbol"])))
        for rank, rec in enumerate(scored, start=1):
            rec["rank"] = rank
            if rec.get("executable_at_t0"):
                rec["opens_within_1s"] = False
            else:
                rec["opens_within_1s"] = _opens_within_1s(eng._board_arrays(str(rec["symbol"])), float(t0))
            out.append(rec)
    return out


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    _pop_webhooks()
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    stage = str(payload.get("stage") or "B0")
    score_pct = payload.get("score_pct")
    if score_pct is not None:
        score_pct = int(score_pct)
    executable_t0_only = bool(payload.get("executable_t0_only") or False)
    keep_rank = bool(payload.get("keep_rank") or False)
    t0w = time.perf_counter()
    try:
        eng, dual = _boot(universe, BFollowEngine)
        if dual is None or eng is None or not getattr(eng, "ready", False):
            return {
                "ok": False,
                "date": day,
                "stage": stage,
                "blocker": getattr(eng, "fail_reason", "dual_unavailable") if eng is not None else "boot_failed",
            }
        eng.score_pct = score_pct
        eng.executable_t0_only = executable_t0_only
        eng.require_executable_continuous_fill = True
        eng.offset_sec = 0
        eng.allowed_hm = UNIFORM10
        eng.fire_mode = "shifted_grid"
        eng.notify_enabled = False
        eng.ingest_audit = _Discard()  # type: ignore[assignment]
        events_n, last_et = _stream_day(day, capture, eng, dual)
        eng._harvest(eng.events)
        packed = _portfolio_pack(
            eng, dual, day=day, variant=f"B_FOLLOWUP_{stage}", offset_sec=0, events_n=events_n, last_et=last_et
        )
        admits = list(getattr(eng, "a_admits", []) or [])
        fills = list(getattr(eng, "a_fills", []) or [])
        expired = list(getattr(eng, "a_expired", []) or [])
        rank_rows: list[dict[str, Any]] = []
        if keep_rank:
            rank_rows = extract_rank_rows(eng, day=day)
            for xs in (admits, fills, expired):
                for rec in xs:
                    rec["date"] = day
        trades = packed.get("trades") or []
        print(
            f"{day} {stage} trades={packed.get('trade_n')} pnl={packed.get('pnl')} "
            f"pending={len(admits)} expired={len(expired)} fills={len(fills)}",
            flush=True,
        )
        del eng, dual, packed
        gc.collect()
        body: dict[str, Any] = {
            "ok": True,
            "date": day,
            "stage": stage,
            "score_pct": score_pct,
            "executable_t0_only": executable_t0_only,
            "trades": trades,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
        if keep_rank:
            body["rank_rows"] = rank_rows
            body["admits"] = admits
            body["fills"] = fills
            body["expired"] = expired
        return body
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "stage": stage,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
