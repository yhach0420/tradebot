"""Harvest-time selected + uncompacted board/CurrentPrice diagnostic for P3-1."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_vs_event_driven.run_comparison import _boot, _stream_day
from research.e1_x34b_entry_execution.features import preentry_from_board
from research.fixed_anchor_mechanism_audit_p3_0.diagnostic import independent_diagnostic_outcome
from research.fixed_selection_diagnostic_reconcile_p3_0r.boards import last_bid_at_or_before
from research.fixed_selection_diagnostic_reconcile_p3_0r.classify import grid_t0
from research.fixed_selection_diagnostic_reconcile_p3_0r.replay import P3REngine, _session_of_anchor
from research.fixed_selection_edge_decomposition_p3_1 import HORIZONS_SEC
from research.fixed_selection_edge_decomposition_p3_1.scan import (
    horizon_status,
    last_px_at_or_before,
    run_fill,
    wait_ask_stats,
)
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_live_dual_lane import canonical_symbol_key
from small_paper.v1r_native_entry_live import FEATURE_ORDER


def _pop_webhooks() -> None:
    for _k in (
        "KABU_V1R_ENTRY_WEBHOOK_URL",
        "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
        "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL",
        "KABU_DISCORD_RESEARCH_WEBHOOK_URL",
        "KABU_SHADOW_DISCORD_WEBHOOK_URL",
        "KABU_MARKET_CAPTURE_WEBHOOK_URL",
        "KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL",
    ):
        os.environ.pop(_k, None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x and x > 0 else None
    except (TypeError, ValueError):
        return None


class _PxBuf:
    __slots__ = ("n", "t", "px")

    def __init__(self) -> None:
        self.n = 0
        self.t = np.empty(64, dtype=float)
        self.px = np.empty(64, dtype=float)

    def append(self, t: float, px: float) -> None:
        if self.n >= self.t.size:
            new = int(self.t.size * 2)
            nt = np.empty(new, dtype=float)
            npx = np.empty(new, dtype=float)
            nt[: self.n] = self.t[: self.n]
            npx[: self.n] = self.px[: self.n]
            self.t, self.px = nt, npx
        i = self.n
        self.t[i] = float(t)
        self.px[i] = float(px)
        self.n = i + 1

    def view(self) -> tuple[np.ndarray, np.ndarray]:
        return self.t[: self.n], self.px[: self.n]


class P31Engine(P3REngine):
    """Same harvest scoring as P3-0R. Also keep causal CurrentPrice (event_t <= t)."""

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self.px_bufs: dict[str, _PxBuf] = {}

    def ingest_push(self, *, symbol: str, payload: dict[str, Any], event_t: Optional[float] = None) -> dict[str, Any]:
        rec = super().ingest_push(symbol=symbol, payload=payload, event_t=event_t)
        if rec.get("ingested"):
            sym = canonical_symbol_key(rec.get("symbol") or symbol)
            px = _f(payload.get("CurrentPrice"))
            et = rec.get("event_t")
            if et is None:
                et = event_t
            if px is not None and et is not None:
                self.px_bufs.setdefault(sym, _PxBuf()).append(float(et), float(px))
        return rec


def replay_day(payload: dict[str, Any]) -> dict[str, Any]:
    _pop_webhooks()
    t0w = time.perf_counter()
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    try:
        eng, dual = _boot(universe, P31Engine)
        if dual is None or not eng.ready:
            return {
                "ok": False,
                "date": day,
                "blocker": getattr(eng, "fail_reason", "dual_unavailable"),
                "elapsed_sec": round(time.perf_counter() - t0w, 3),
            }
        eng.notify_enabled = False
        eng.ingest_audit = _Discard()  # type: ignore[assignment]
        _stream_day(day, capture, eng, dual)
        eng._harvest(eng.events)

        boards = {s: b.view() for s, b in eng.full_bufs.items()}
        px_by = {s: b.view() for s, b in eng.px_bufs.items()}

        cand_by: dict[tuple[str, str], dict[str, Any]] = {}
        for c in eng.a_candidates:
            cand_by[(str(c.get("anchor") or ""), canonical_symbol_key(c.get("symbol")))] = c

        rows: list[dict[str, Any]] = []
        for (an, sym), c in sorted(cand_by.items()):
            t0 = c.get("t0")
            if t0 is None:
                t0 = grid_t0(day, an)
            board = boards.get(sym)
            limit = c.get("bid")
            if limit is None and board is not None and t0 is not None:
                limit = last_bid_at_or_before(board, float(t0))
            selected = bool(c.get("admitted"))
            sess = _session_of_anchor(an)
            filled = False
            fill_t = fill_px = None
            pnl = None
            fill_reason = None
            time_to_fill_ms = None
            first_bps = min_bps = None
            feats: dict[str, Any] = {f: None for f in FEATURE_ORDER}
            if board is not None and t0 is not None and limit is not None:
                fill = run_fill(board, float(t0), float(limit))
                filled = bool(fill.get("filled"))
                fill_t = fill.get("fill_t")
                fill_px = fill.get("fill_price")
                fill_reason = fill.get("reason")
                if filled and fill_t is not None:
                    time_to_fill_ms = (float(fill_t) - float(t0)) * 1000.0
                    diag = independent_diagnostic_outcome(
                        board,
                        date=day,
                        symbol=sym,
                        session=sess,
                        t0=float(t0),
                        limit_price=float(limit),
                    )
                    pnl = diag.get("independent_pnl")
                wst = wait_ask_stats(board, float(t0), float(limit))
                first_bps = wst.get("first_ask_minus_limit_bps")
                min_bps = wst.get("min_ask_minus_limit_bps")
                raw = preentry_from_board(board, float(t0))
                for f in FEATURE_ORDER:
                    v = raw.get(f)
                    try:
                        fv = float(v) if v is not None else None
                        feats[f] = fv if fv is not None and fv == fv else None
                    except (TypeError, ValueError):
                        feats[f] = None
            elif t0 is not None:
                fill_reason = "NO_BOARD_OR_LIMIT"

            rec: dict[str, Any] = {
                "date": day,
                "session": sess,
                "anchor_time": an,
                "symbol": sym,
                "t0": t0,
                "selected": selected,
                "rank": c.get("rank"),
                "alloc_score": c.get("score"),
                "limit_bid": limit,
                "independent_filled": filled,
                "independent_fill_time": fill_t,
                "independent_fill_price": fill_px,
                "independent_pnl": pnl,
                "fill_reason": fill_reason,
                "time_to_fill_ms": time_to_fill_ms,
                "first_ask_minus_limit_bps": first_bps,
                "min_ask_minus_limit_bps": min_bps,
                "label_filled_outcome": "INDEPENDENT_FILLED_ARCH_E_OUTCOME",
                "label_directional": "FILL_INDEPENDENT_DIRECTIONAL_DIAGNOSTIC",
            }
            rec.update(feats)
            t_px, p_px = px_by.get(sym, (np.empty(0), np.empty(0)))
            start_px = last_px_at_or_before(t_px, p_px, float(t0)) if t0 is not None else None
            rec["anchor_current_price"] = start_px
            for h in HORIZONS_SEC:
                key = f"ret_{h}"
                st_key = f"status_{h}"
                if t0 is None:
                    rec[key] = None
                    rec[st_key] = "MISSING_PRICE"
                    continue
                st = horizon_status(day, sess, float(t0), int(h))
                if st != "OK":
                    rec[key] = None
                    rec[st_key] = st
                    continue
                if start_px is None:
                    rec[key] = None
                    rec[st_key] = "MISSING_PRICE"
                    continue
                fut = last_px_at_or_before(t_px, p_px, float(t0) + float(h))
                if fut is None:
                    rec[key] = None
                    rec[st_key] = "MISSING_PRICE"
                    continue
                rec[key] = float(fut) / float(start_px) - 1.0
                rec[st_key] = "OK"
            rows.append(rec)

        return {
            "ok": True,
            "date": day,
            "rows": rows,
            "harvest_selected_n": sum(1 for r in rows if r.get("selected")),
            "harvest_eligible_n": len(rows),
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
