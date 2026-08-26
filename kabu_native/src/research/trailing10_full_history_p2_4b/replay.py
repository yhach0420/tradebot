"""One-day chronological TRAIL10 replay. Same Capture stream as P1/P2-2."""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_vs_event_driven.run_comparison import (  # noqa: E402
    _boot,
    _stream_day,
    extract_trades,
)
from research.dynamic_anchor_p2_2 import LOT_QTY, NORMAL
from research.dynamic_anchor_p2_2.replay import _classify_special, _meta_for_fill
from research.dynamic_anchor_p2_3.engine import attach_fill_terminals
from research.dynamic_anchor_p2_3.fill_path import last_bid_at_or_before, wait_ask_path
from research.dynamic_anchor_p2_3.fill_stage import reconcile_fills_with_trades
from research.trailing10_full_history_p2_4b.clock import IncrementalTrail10
from research.trailing10_full_history_p2_4b.coverage import classify_fixed_trade
from research.trailing10_full_history_p2_4b.engine import Trail10Engine
from run_p0_3_exact_runtime_replay_20260820 import _iso, _maxdd, _pf, _sess_stats
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_live_dual_lane import canonical_symbol_key
from small_paper.v1r_primary_runtime import WAIT_SEC

JST = ZoneInfo("Asia/Tokyo")


def _pop_webhooks() -> None:
    for _k in (
        "KABU_V1R_ENTRY_WEBHOOK_URL",
        "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
        "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL",
        "KABU_DISCORD_RESEARCH_WEBHOOK_URL",
        "KABU_SHADOW_DISCORD_WEBHOOK_URL",
        "KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL",
        "KABU_MARKET_CAPTURE_WEBHOOK_URL",
    ):
        os.environ.pop(_k, None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"


def _hm_epoch(day: str, hm: str) -> float:
    h, m = hm.split(":")
    return datetime(
        int(day[:4]), int(day[4:6]), int(day[6:]), int(h), int(m), tzinfo=JST
    ).timestamp()


def _wl(trades: list[dict[str, Any]]) -> tuple[int, int, int]:
    w = l = d = 0
    for t in trades:
        p = float(t.get("pnl_yen_100") or 0.0)
        if p > 1e-9:
            w += 1
        elif p < -1e-9:
            l += 1
        else:
            d += 1
    return w, l, d


def _gross(trades: list[dict[str, Any]]) -> tuple[float, float]:
    gp = sum(float(t.get("pnl_yen_100") or 0.0) for t in trades if float(t.get("pnl_yen_100") or 0.0) > 0)
    gl = sum(-float(t.get("pnl_yen_100") or 0.0) for t in trades if float(t.get("pnl_yen_100") or 0.0) < 0)
    return round(gp, 2), round(gl, 2)


def _ask_for_admit(eng: Trail10Engine, *, symbol: str, signal_time: float, limit: Any) -> dict[str, Any]:
    board = eng._board_arrays(canonical_symbol_key(symbol))
    lim = None if limit is None else float(limit)
    if lim is None or not (lim == lim) or lim <= 0:
        lim = last_bid_at_or_before(board, float(signal_time))
    return wait_ask_path(board, signal_time=float(signal_time), limit_bid=lim, wait_sec=WAIT_SEC)


def replay_trail10_day(payload: dict[str, Any]) -> dict[str, Any]:
    _pop_webhooks()
    if str(NATIVE / "src") not in sys.path:
        sys.path.insert(0, str(NATIVE / "src"))
        sys.path.insert(0, str(NATIVE / "scripts"))

    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    cap_class = str(payload.get("capture_class") or "")
    t0w = time.perf_counter()
    try:
        clock = IncrementalTrail10(day=day, universe=universe)
        eng, dual = _boot(universe, Trail10Engine)
        if dual is None or not eng.ready:
            return {
                "ok": False,
                "date": day,
                "blocker": getattr(eng, "fail_reason", "dual_unavailable"),
                "elapsed_sec": round(time.perf_counter() - t0w, 3),
            }
        eng.notify_enabled = False
        eng.ingest_audit = _Discard()  # type: ignore[assignment]
        eng.bind_trail10(clock)
        events_n, last_et = _stream_day(day, capture, eng, dual)
        eng._harvest(eng.events)
        unresolved = attach_fill_terminals(eng)

        raw_trades = extract_trades(dual)
        trades: list[dict[str, Any]] = []
        for i, tr in enumerate(raw_trades, start=1):
            fill_t = float(tr.get("entry_time") or 0.0)
            exit_t = tr.get("exit_time")
            sym = canonical_symbol_key(tr.get("symbol"))
            fill_row = next(
                (f for f in eng.a_fills if f.get("symbol") == sym and abs(float(f.get("fill_time") or 0) - fill_t) < 1e-6),
                None,
            )
            admit_row = next((a for a in reversed(eng.a_admits) if a.get("symbol") == sym), None)
            src = fill_row or admit_row or {}
            meta = _meta_for_fill(eng, sym, fill_t, src)
            holding = None
            if fill_t and exit_t is not None:
                holding = round(float(exit_t) - fill_t, 3)
            g = meta.get("g") if meta.get("g") is not None else meta.get("t1")
            special = _classify_special(day, {
                "fill_time": fill_t,
                "exit_time": exit_t,
                "exit_reason": tr.get("reason"),
                "pnl_yen_100": tr.get("pnl_yen_100"),
            })
            trades.append({
                "date": day,
                "session": tr.get("session") or meta.get("session"),
                "symbol": sym,
                "g": g,
                "t0": g,
                "t1": g,
                "trend_slope": meta.get("trend_slope"),
                "p0": meta.get("p0"),
                "p10": meta.get("p10"),
                "decision_time": meta.get("decision_time"),
                "snapshot_cutoff": meta.get("snapshot_cutoff") or g,
                "entry_score": src.get("score") if src else meta.get("score"),
                "candidate_rank": src.get("rank") if src else meta.get("universe_rank"),
                "limit": src.get("limit") or tr.get("entry_price"),
                "fill_time": fill_t,
                "fill_time_iso": _iso(fill_t),
                "fill_price": tr.get("entry_price"),
                "exit_time": exit_t,
                "exit_time_iso": _iso(exit_t),
                "exit_price": tr.get("exit_price"),
                "exit_reason": tr.get("reason"),
                "holding_sec": holding,
                "pnl_yen_100": float(tr.get("pnl_yen_100") or 0.0),
                "special_class": special,
                "anchor": src.get("anchor") or meta.get("anchor"),
            })

        reconcile_fills_with_trades(eng.terminal_rows, trades, wait_sec=float(WAIT_SEC))

        ask_bps: list[float] = []
        for row in eng.terminal_rows:
            if row.get("entry_terminal") != "ADMITTED":
                continue
            g = row.get("t1") if row.get("t1") is not None else row.get("g")
            if g is None:
                continue
            path = _ask_for_admit(
                eng,
                symbol=str(row.get("symbol")),
                signal_time=float(g),
                limit=row.get("limit"),
            )
            bps = path.get("min_ask_minus_limit_bps")
            if bps is not None:
                try:
                    x = float(bps)
                except (TypeError, ValueError):
                    continue
                if x == x:
                    ask_bps.append(x)

        coverage: list[dict[str, Any]] = []
        for t in payload.get("fixed_trades") or []:
            hm = str(t.get("anchor_time") or "")
            try:
                sig = _hm_epoch(day, hm)
            except Exception:
                sig = float(t.get("fill_time") or 0.0)
            sess = str(t.get("session") or ("AM" if datetime.fromtimestamp(sig, JST).hour < 12 else "PM"))
            st = classify_fixed_trade(
                symbol=str(t.get("symbol")),
                session=sess,
                signal_t=sig,
                anchors=clock.anchors,
                evals=clock.evals,
            )
            coverage.append({
                "date": day,
                "symbol": t.get("symbol"),
                "session": sess,
                "fixed_anchor_time": hm,
                "fixed_signal_time": sig,
                "fixed_pnl": t.get("pnl_yen_100"),
                **st,
            })

        terminals = []
        for r in eng.terminal_rows:
            terminals.append({
                "date": day,
                "session": r.get("session"),
                "symbol": r.get("symbol"),
                "g": r.get("t1") if r.get("t1") is not None else r.get("g"),
                "entry_terminal": r.get("entry_terminal"),
                "fill_terminal": r.get("fill_terminal"),
            })

        pnls = [float(t.get("pnl_yen_100") or 0.0) for t in trades]
        w, l, d = _wl(trades)
        gp, gl = _gross(trades)
        expired = int(eng.primary_expired)
        fills = int(eng.primary_fills)
        buy_n = round(sum(float(t.get("fill_price") or 0.0) * LOT_QTY for t in trades), 2)
        sell_n = round(sum(float(t.get("exit_price") or 0.0) * LOT_QTY for t in trades), 2)
        hold = [float(t["holding_sec"]) for t in trades if t.get("holding_sec") is not None]
        mean_occ = (eng.occ_integral / eng.occ_span) if eng.occ_span > 0 else 0.0
        pending_left = [u for u in unresolved if str(u).startswith("PENDING_LEFT")]
        return {
            "ok": True,
            "date": day,
            "capture_class": cap_class,
            "universe_n": len(universe),
            "universe_source": payload.get("universe_source"),
            "events_processed": events_n,
            "last_et": last_et,
            "CHRONOLOGICAL_SINGLE_STREAM": True,
            "clock_grid_calls_blocked": int(eng.clock_grid_blocked),
            "dynamic_anchor_fires": int(eng.dynamic_anchor_fires),
            "snapshot_future_leak": int(eng.snapshot_future_leak),
            "checkpoint_future_leak": int(clock.checkpoint_future_leak),
            "decision_snapshot_future_leak": int(eng.decision_snapshot_future_leak),
            "TRUE_PERSISTENCE_REFIRE": int(clock.persist_refire),
            "duplicate_edge": int(clock.duplicate_edge),
            "not_evaluable_created_edge": int(clock.ne_created_edge),
            "grid_evaluations": int(clock.grid_n),
            "evaluable_states": int(clock.evaluable_n),
            "true_state_rows": int(clock.true_n),
            "false_to_true_anchors": len(clock.anchors),
            "anchors": [
                {
                    "date": a["date"],
                    "session": a.get("session"),
                    "symbol": a["symbol"],
                    "g": a["g"],
                }
                for a in clock.anchors
            ],
            "funnel": dict(eng.funnel),
            "terminals": terminals,
            "coverage": coverage,
            "ask_bps": ask_bps,
            "admitted": int(eng.primary_admitted),
            "fills": fills,
            "expired": expired,
            "pending_left": pending_left,
            "trades": trades,
            "trade_n": len(trades),
            "win": w,
            "loss": l,
            "draw": d,
            "pnl": round(sum(pnls), 2),
            "gross_profit": gp,
            "gross_loss": gl,
            "PF": _pf(pnls),
            "avg_pnl": round(sum(pnls) / len(trades), 4) if trades else 0.0,
            "maxDD": _maxdd(trades),
            "AM": _sess_stats(trades, "AM"),
            "PM": _sess_stats(trades, "PM"),
            "gross_buy_notional": buy_n,
            "gross_sell_notional": sell_n,
            "mean_concurrent_positions": round(mean_occ, 4),
            "max_concurrent_positions": int(eng.max_concurrent),
            "mean_holding_seconds": round(sum(hold) / len(hold), 3) if hold else None,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
