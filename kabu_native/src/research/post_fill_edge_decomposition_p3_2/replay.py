"""Harvest fill + causal post-fill MID/CurrentPrice markouts. Uncompacted boards only."""
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
from research.fixed_selection_diagnostic_reconcile_p3_0r.boards import last_bid_at_or_before
from research.fixed_selection_diagnostic_reconcile_p3_0r.classify import grid_t0
from research.fixed_selection_diagnostic_reconcile_p3_0r.replay import _session_of_anchor
from research.fixed_selection_edge_decomposition_p3_1.replay import P31Engine, _pop_webhooks
from research.fixed_selection_edge_decomposition_p3_1.scan import (
    horizon_status,
    last_px_at_or_before,
    run_fill,
    wait_ask_stats,
)
from research.fixed_anchor_mechanism_audit_p3_0.grid import session_of_epoch
from research.post_fill_edge_decomposition_p3_2 import HORIZONS_SEC, IDENTITY_REL_TOL
from research.post_fill_edge_decomposition_p3_2.quotes import mid_at_or_before
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_live_dual_lane import canonical_symbol_key


def _rel_close(a: float, b: float) -> bool:
    denom = max(abs(a), abs(b), 1e-12)
    return abs(float(a) - float(b)) / denom <= IDENTITY_REL_TOL or abs(float(a) - float(b)) <= 1e-12


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

        by_anchor: dict[str, list[tuple[str, str]]] = {}
        for an, sym in cand_by:
            by_anchor.setdefault(an, []).append((an, sym))

        rows: list[dict[str, Any]] = []
        n_sel = n_elig = n_sel_fill = n_nos_fill = 0
        leak_fill = leak_mid = leak_cp = 0
        ident_n = ident_fail = 0

        for (an, sym), c in sorted(cand_by.items()):
            n_elig += 1
            selected = bool(c.get("admitted"))
            if selected:
                n_sel += 1
            t0 = c.get("t0")
            if t0 is None:
                t0 = grid_t0(day, an)
            board = boards.get(sym)
            limit = c.get("bid")
            if limit is None and board is not None and t0 is not None:
                limit = last_bid_at_or_before(board, float(t0))
            if board is None or t0 is None or limit is None:
                continue
            fill = run_fill(board, float(t0), float(limit))
            if not fill.get("filled"):
                continue
            fill_t = float(fill["fill_t"])
            fill_px = float(fill.get("fill_price") or limit)
            if selected:
                n_sel_fill += 1
            else:
                n_nos_fill += 1

            n_at_anchor = len(by_anchor.get(an) or [])
            rank = c.get("rank")
            q = None
            if rank is not None and n_at_anchor > 0:
                q = min(4, int(int(float(rank)) * 5 / n_at_anchor))

            sess_fill = session_of_epoch(day, fill_t) or _session_of_anchor(an)
            wst = wait_ask_stats(board, float(t0), float(limit))
            mid_fill = mid_at_or_before(board, fill_t)
            leak_fill += int(mid_fill.get("leak_n") or 0)
            mid_anchor = mid_at_or_before(board, float(t0))
            t_px, p_px = px_by.get(sym, (np.empty(0), np.empty(0)))

            def _cp(until: float) -> tuple[Optional[float], int]:
                leak = 0
                if getattr(t_px, "size", 0):
                    j = int(np.searchsorted(t_px, float(until), side="right") - 1)
                    if j >= 0 and float(t_px[j]) > float(until) + 1e-12:
                        leak = 1
                return last_px_at_or_before(t_px, p_px, until), leak

            cp_fill, leak_c0 = _cp(fill_t)
            leak_cp += leak_c0

            rec: dict[str, Any] = {
                "date": day,
                "session": sess_fill,
                "anchor_time": an,
                "symbol": sym,
                "selected": selected,
                "rank": rank,
                "quintile": None if q is None else f"Q{q + 1}",
                "t0": float(t0),
                "fill_time": fill_t,
                "fill_price": fill_px,
                "fill_latency_ms": (fill_t - float(t0)) * 1000.0,
                "limit_bid": float(limit),
                "anchor_bid": mid_anchor.get("bid"),
                "anchor_ask": mid_anchor.get("ask"),
                "anchor_mid": mid_anchor.get("mid"),
                "spread_bps_at_anchor": mid_anchor.get("spread_bps"),
                "first_ask_minus_limit_bps": wst.get("first_ask_minus_limit_bps"),
                "min_ask_minus_limit_bps": wst.get("min_ask_minus_limit_bps"),
                "buy1_at_fill": mid_fill.get("bid"),
                "sell1_at_fill": mid_fill.get("ask"),
                "mid_at_fill": mid_fill.get("mid"),
                "spread_bps_at_fill": mid_fill.get("spread_bps"),
                "current_price_at_fill": cp_fill,
                "mid_evaluable_at_fill": bool(mid_fill.get("evaluable")),
                "execution_advantage_bps": None,
                "anchor_to_fill_mid_return": None,
                "identity_pass": True,
            }
            if mid_fill.get("evaluable") and fill_px > 0:
                rec["execution_advantage_bps"] = (float(mid_fill["mid"]) / fill_px - 1.0) * 10000.0
            if mid_fill.get("evaluable") and mid_anchor.get("evaluable"):
                rec["anchor_to_fill_mid_return"] = float(mid_fill["mid"]) / float(mid_anchor["mid"]) - 1.0

            for h in HORIZONS_SEC:
                chk = fill_t + float(h)
                st = horizon_status(day, sess_fill, fill_t, int(h))
                rec[f"status_{h}"] = st
                rec[f"mid_{h}"] = None
                rec[f"mid_markout_{h}"] = None
                rec[f"execution_markout_{h}"] = None
                rec[f"cp_{h}"] = None
                rec[f"cp_markout_{h}"] = None
                if st != "OK":
                    continue
                fut = mid_at_or_before(board, chk)
                leak_mid += int(fut.get("leak_n") or 0)
                cp_h, leak_ch = _cp(chk)
                leak_cp += leak_ch
                rec[f"cp_{h}"] = cp_h
                if cp_h is not None and cp_fill is not None and cp_fill > 0:
                    rec[f"cp_markout_{h}"] = float(cp_h) / float(cp_fill) - 1.0
                if not fut.get("evaluable"):
                    rec[f"status_{h}"] = "NOT_EVALUABLE" if st == "OK" else st
                    continue
                rec[f"mid_{h}"] = fut["mid"]
                if mid_fill.get("evaluable"):
                    rec[f"mid_markout_{h}"] = float(fut["mid"]) / float(mid_fill["mid"]) - 1.0
                    exec_m = float(fut["mid"]) / fill_px - 1.0
                    rec[f"execution_markout_{h}"] = exec_m
                    lhs = float(fut["mid"]) / fill_px
                    rhs = (float(mid_fill["mid"]) / fill_px) * (float(fut["mid"]) / float(mid_fill["mid"]))
                    ident_n += 1
                    if not _rel_close(lhs, rhs):
                        ident_fail += 1
                        rec["identity_pass"] = False
            rows.append(rec)

        return {
            "ok": True,
            "date": day,
            "rows": rows,
            "harvest_eligible_n": n_elig,
            "harvest_selected_n": n_sel,
            "selected_fill_n": n_sel_fill,
            "not_selected_fill_n": n_nos_fill,
            "leak_fill": leak_fill,
            "leak_mid": leak_mid,
            "leak_cp": leak_cp,
            "identity_n": ident_n,
            "identity_fail": ident_fail,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
