"""Harvest-time selected + uncompacted independent fill (no compact diagnostic)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_vs_event_driven.run_comparison import _boot, _stream_day
from research.fixed_anchor_mechanism_audit_p3_0.diagnostic import independent_diagnostic_outcome
from research.fixed_anchor_mechanism_audit_p3_0.engine import P3Engine
from research.fixed_selection_diagnostic_reconcile_p3_0r.boards import last_bid_at_or_before, ticks_in_wait
from research.fixed_selection_diagnostic_reconcile_p3_0r.classify import (
    classify_canonical_fill,
    grid_t0,
    run_fill,
)
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_live_dual_lane import canonical_symbol_key
from small_paper.v1r_native_entry_live import _BoardBuf


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


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return s in {"true", "1", "yes"}


def _session_of_anchor(an: str) -> str:
    try:
        h = int(str(an).split(":")[0])
    except (TypeError, ValueError):
        return "AM"
    return "AM" if h < 12 else "PM"


class P3REngine(P3Engine):
    """Production scoring/compact unchanged. Mirror every ingested tick into an uncompacted buf."""

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self.full_bufs: dict[str, _BoardBuf] = {}

    def ingest_push(self, *, symbol: str, payload: dict[str, Any], event_t: Optional[float] = None) -> dict[str, Any]:
        rec = super().ingest_push(symbol=symbol, payload=payload, event_t=event_t)
        if rec.get("ingested"):
            sym = canonical_symbol_key(rec.get("symbol") or symbol)
            rows = self.boards.get(sym) or []
            if rows:
                self.full_bufs.setdefault(sym, _BoardBuf()).append(rows[-1])
        return rec


def replay_day(payload: dict[str, Any]) -> dict[str, Any]:
    _pop_webhooks()
    t0w = time.perf_counter()
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    old_cands = list(payload.get("candidates") or [])
    trades = list(payload.get("canonical_trades") or [])
    try:
        eng, dual = _boot(universe, P3REngine)
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

        old_by: dict[tuple[str, str], dict[str, Any]] = {}
        for c in old_cands:
            k = (str(c.get("anchor_time") or ""), canonical_symbol_key(c.get("symbol")))
            old_by[k] = c

        cand_by: dict[tuple[str, str], dict[str, Any]] = {}
        for c in eng.a_candidates:
            sym = canonical_symbol_key(c.get("symbol"))
            an = str(c.get("anchor") or "")
            cand_by[(an, sym)] = c

        admit_keys = {
            (str(a.get("anchor") or ""), canonical_symbol_key(a.get("symbol"))) for a in eng.a_admits
        }

        diag_rows: list[dict[str, Any]] = []
        for (an, sym), c in sorted(cand_by.items()):
            t0 = c.get("t0")
            if t0 is None:
                t0 = grid_t0(day, an)
            board = boards.get(sym)
            limit = c.get("bid")
            if limit is None and board is not None and t0 is not None:
                limit = last_bid_at_or_before(board, float(t0))
            selected = bool(c.get("admitted"))
            old = old_by.get((an, sym)) or {}
            filled = False
            fill_t = fill_px = None
            exit_t = exit_px = exit_reason = pnl = None
            fill_reason = None
            sess = _session_of_anchor(an)
            if board is not None and t0 is not None and limit is not None:
                diag = independent_diagnostic_outcome(
                    board,
                    date=day,
                    symbol=sym,
                    session=sess,
                    t0=float(t0),
                    limit_price=float(limit),
                )
                filled = bool(diag.get("independent_filled"))
                fill_t = diag.get("independent_fill_time")
                fill_px = diag.get("independent_fill_price")
                exit_t = diag.get("independent_exit_time")
                exit_px = diag.get("independent_exit_price")
                exit_reason = diag.get("independent_exit_reason")
                pnl = diag.get("independent_pnl")
                fill_reason = diag.get("fill_reason")
            else:
                fill_reason = "NO_BOARD_OR_LIMIT"
            diag_rows.append(
                {
                    "date": day,
                    "session": sess,
                    "anchor_time": an,
                    "symbol": sym,
                    "t0": t0,
                    "score": c.get("score"),
                    "rank": c.get("rank"),
                    "selected": selected,
                    "feature_evaluable": True,
                    "actual_admitted": (an, sym) in admit_keys,
                    "p3_0_xlsx_selected": _truthy(old.get("selected")),
                    "old_independent_filled": _truthy(old.get("independent_filled")),
                    "limit": limit,
                    "independent_filled": filled,
                    "independent_fill_time": fill_t,
                    "independent_fill_price": fill_px,
                    "independent_exit_time": exit_t,
                    "independent_exit_price": exit_px,
                    "independent_exit_reason": exit_reason,
                    "independent_pnl": pnl,
                    "fill_reason": fill_reason,
                }
            )

        by_sel = {
            (str(r["date"]), str(r["anchor_time"]), str(r["symbol"])): r
            for r in diag_rows
            if r.get("selected")
        }
        by_elig = {(str(r["date"]), str(r["anchor_time"]), str(r["symbol"])): r for r in diag_rows}

        recon_rows = []
        for tr in trades:
            sym = canonical_symbol_key(tr.get("symbol"))
            an = str(tr.get("anchor_time") or "")
            key = (day, an, sym)
            sel = by_sel.get(key)
            elig = by_elig.get(key)
            t0 = grid_t0(day, an)
            board = boards.get(sym)
            recon_limit = None
            if sel is not None and sel.get("limit") is not None:
                recon_limit = float(sel["limit"])
            elif elig is not None and elig.get("limit") is not None:
                recon_limit = float(elig["limit"])
            elif board is not None and t0 is not None:
                recon_limit = last_bid_at_or_before(board, float(t0))
            fill = None
            if board is not None and t0 is not None and recon_limit is not None:
                fill = run_fill(board, float(t0), float(recon_limit))
            klass_row = classify_canonical_fill(
                {**tr, "symbol": sym, "date": day},
                sel,
                board,
                diag_fill=fill,
                recon_limit=recon_limit,
            )
            klass_row["p3_0_xlsx_selected"] = _truthy((old_by.get((an, sym)) or {}).get("selected"))
            klass_row["harvest_eligible"] = elig is not None
            klass_row["wait_ticks"] = ticks_in_wait(board if board is not None else {"t": None}, t0 or 0.0, 1.0).get("n")
            src = sel or elig
            if src is not None:
                klass_row["diag_exit_time"] = src.get("independent_exit_time")
                klass_row["diag_exit_price"] = src.get("independent_exit_price")
                klass_row["diag_pnl"] = src.get("independent_pnl")
                klass_row["canonical_exit_time"] = tr.get("exit_time")
                klass_row["canonical_exit_price"] = tr.get("exit_price")
                klass_row["canonical_pnl"] = tr.get("pnl_yen_100")
                klass_row["canonical_exit_reason"] = tr.get("exit_reason")
                if klass_row["klass"] == "MATCH" and src.get("independent_pnl") is not None and tr.get("pnl_yen_100") is not None:
                    klass_row["exit_pnl_match"] = abs(float(src["independent_pnl"]) - float(tr["pnl_yen_100"])) < 1.0
                    et_ok = False
                    if src.get("independent_exit_time") is not None and tr.get("exit_time") is not None:
                        et_ok = abs(float(src["independent_exit_time"]) - float(tr["exit_time"])) <= 0.05
                    klass_row["exit_time_match"] = et_ok
                else:
                    klass_row["exit_pnl_match"] = None
                    klass_row["exit_time_match"] = None
            recon_rows.append(klass_row)

        return {
            "ok": True,
            "date": day,
            "diag_rows": diag_rows,
            "reconcile_rows": recon_rows,
            "harvest_selected_n": sum(1 for r in diag_rows if r.get("selected")),
            "harvest_eligible_n": len(diag_rows),
            "xlsx_selected_among_canonical": sum(1 for r in recon_rows if r.get("p3_0_xlsx_selected")),
            "board_symbols": len(boards),
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
