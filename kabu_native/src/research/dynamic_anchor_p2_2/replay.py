"""One-day chronological Dynamic replay. Same Capture stream as P1."""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
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
from research.dynamic_anchor_p2_1.inventory import process_day
from research.dynamic_anchor_p2_2 import LOT_QTY, NORMAL, POST_CUTOFF_ZERO_HOLD
from research.dynamic_anchor_p2_2.engine import DynamicEngine
from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from run_p0_3_exact_runtime_replay_20260820 import _iso, _maxdd, _pf, _sess_stats
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_live_dual_lane import canonical_symbol_key

JST = ZoneInfo("Asia/Tokyo")


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


def _classify_special(day: str, tr: dict[str, Any]) -> str:
    fill_t = tr.get("fill_time")
    exit_t = tr.get("exit_time")
    reason = str(tr.get("exit_reason") or "")
    pnl = float(tr.get("pnl_yen_100") or 0.0)
    if fill_t is None:
        return NORMAL
    try:
        pm_end = session_end_epoch(day, "PM")
    except Exception:
        return NORMAL
    if (
        float(fill_t) > float(pm_end) + 1e-12
        and reason == "SESSION_CLOSE"
        and abs(pnl) <= 1e-9
        and exit_t is not None
        and abs(float(exit_t) - float(pm_end)) <= 1e-6
    ):
        return POST_CUTOFF_ZERO_HOLD
    return NORMAL


def _meta_for_fill(eng: DynamicEngine, sym: str, fill_t: float, admit: dict[str, Any]) -> dict[str, Any]:
    an = str(admit.get("anchor") or "")
    t1 = None
    if an.startswith("D"):
        try:
            t1 = float(an[1:])
        except ValueError:
            t1 = None
    if t1 is not None:
        hit = eng.dynamic_meta.get((sym, float(t1)))
        if hit:
            return hit
    # nearest signal_time <= fill
    best = None
    best_dt = None
    for (s, t1k), m in eng.dynamic_meta.items():
        if s != sym:
            continue
        dt = float(fill_t) - float(t1k)
        if dt < -1e-9:
            continue
        if best is None or dt < best_dt:
            best, best_dt = m, dt
    return best or {}


def replay_dynamic_day(payload: dict[str, Any]) -> dict[str, Any]:
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
    if str(NATIVE / "src") not in sys.path:
        sys.path.insert(0, str(NATIVE / "src"))
        sys.path.insert(0, str(NATIVE / "scripts"))

    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    cap_class = str(payload.get("capture_class") or "")
    t0 = time.perf_counter()
    try:
        t1_out = process_day(payload)
        trig_map = {
            (str(t["symbol"]), round(float(t["t0"]), 6)): t.get("vol_percentile_60s")
            for t in (t1_out.get("triggers") or [])
        }
        for c in t1_out.get("confirms") or []:
            c["vol_percentile_60s"] = trig_map.get((str(c.get("symbol")), round(float(c.get("t0") or 0), 6)))
        eng, dual = _boot(universe, DynamicEngine)
        if dual is None or not eng.ready:
            return {
                "ok": False,
                "date": day,
                "blocker": getattr(eng, "fail_reason", "dual_unavailable"),
                "elapsed_sec": round(time.perf_counter() - t0, 3),
            }
        eng.notify_enabled = False
        eng.ingest_audit = _Discard()  # type: ignore[assignment]
        eng.bind_p2_1(t1_out)
        events_n, last_et = _stream_day(day, capture, eng, dual)
        eng._harvest(eng.events)
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
            admit_row = next(
                (a for a in reversed(eng.a_admits) if a.get("symbol") == sym),
                None,
            )
            src = fill_row or admit_row or {}
            meta = _meta_for_fill(eng, sym, fill_t, src)
            holding = None
            if fill_t and exit_t is not None:
                holding = round(float(exit_t) - fill_t, 3)
            special = _classify_special(day, {
                "fill_time": fill_t,
                "exit_time": exit_t,
                "exit_reason": tr.get("reason"),
                "pnl_yen_100": tr.get("pnl_yen_100"),
            })
            t1 = meta.get("t1")
            trades.append({
                "date": day,
                "session": tr.get("session") or meta.get("session"),
                "symbol": sym,
                "t0": meta.get("t0"),
                "t1": t1,
                "T1_percentile": meta.get("vol_percentile_60s"),
                "C1_slope": meta.get("trend_slope"),
                "C1_endpoint_return": meta.get("endpoint_return"),
                "decision_time": meta.get("decision_time"),
                "snapshot_cutoff": meta.get("snapshot_cutoff") or t1,
                "entry_score": src.get("score") if src else meta.get("score"),
                "candidate_rank": src.get("rank") if src else meta.get("universe_rank"),
                "universe_rank": meta.get("universe_rank"),
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
        pnls = [float(t.get("pnl_yen_100") or 0.0) for t in trades]
        w, l, d = _wl(trades)
        gp, gl = _gross(trades)
        t1c = eng.t1_out or {}
        trig = list(t1c.get("triggers") or [])
        conf = list(t1c.get("confirms") or [])
        confirmed_n = sum(1 for c in conf if c.get("status") == "CONFIRMED")
        expired = int(eng.primary_expired)
        fills = int(eng.primary_fills)
        buy_n = round(sum(float(t.get("fill_price") or 0.0) * LOT_QTY for t in trades), 2)
        sell_n = round(sum(float(t.get("exit_price") or 0.0) * LOT_QTY for t in trades), 2)
        hold = [float(t["holding_sec"]) for t in trades if t.get("holding_sec") is not None]
        mean_occ = (eng.occ_integral / eng.occ_span) if eng.occ_span > 0 else 0.0
        zc = [t for t in trades if t.get("special_class") == POST_CUTOFF_ZERO_HOLD]
        return {
            "ok": True,
            "date": day,
            "capture_class": cap_class,
            "universe_n": len(universe),
            "universe_source": payload.get("universe_source"),
            "events_processed": events_n,
            "CHRONOLOGICAL_SINGLE_STREAM": True,
            "clock_grid_calls_blocked": int(eng.clock_grid_blocked),
            "dynamic_anchor_fires": int(eng.dynamic_anchor_fires),
            "snapshot_future_leak": int(eng.snapshot_future_leak),
            "checkpoint_future_leak": int(t1c.get("checkpoint_future_leak_count") or 0),
            "decision_snapshot_future_leak": int(eng.decision_snapshot_leaks()),
            "TRUE_PERSISTENCE_REFIRE": int(t1c.get("TRUE_PERSISTENCE_REFIRE") or 0),
            "triggers": trig,
            "confirms": conf,
            "false_to_true_triggers": len(trig),
            "confirmed": confirmed_n,
            "funnel": dict(eng.funnel),
            "admitted": int(eng.primary_admitted),
            "fills": fills,
            "expired": expired,
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
            "gross_turnover_notional": round(buy_n + sell_n, 2),
            "total_holding_seconds": round(sum(hold), 3) if hold else 0.0,
            "mean_holding_seconds": round(sum(hold) / len(hold), 3) if hold else None,
            "mean_concurrent_positions": round(mean_occ, 4),
            "max_concurrent_positions": int(eng.max_concurrent),
            "post_cutoff_zero_hold": {
                "count": len(zc),
                "symbols": sorted({t["symbol"] for t in zc}),
                "draw_count": sum(1 for t in zc if abs(float(t.get("pnl_yen_100") or 0)) <= 1e-9),
                "pnl": round(sum(float(t.get("pnl_yen_100") or 0) for t in zc), 2),
            },
            "grid_evaluations": int(t1c.get("grid_evaluations") or 0),
            "t1_evaluable_rows": int(t1c.get("t1_evaluable_rows") or 0),
            "elapsed_sec": round(time.perf_counter() - t0, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0, 3),
        }
