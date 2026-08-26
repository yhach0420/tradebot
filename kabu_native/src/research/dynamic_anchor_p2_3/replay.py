"""One-day P2-3 harvest: frozen P2-1 T1/C1 + P2-2 Dynamic stream + P1 Fixed stream."""
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
from research.dynamic_anchor_p2_1.inventory import process_day
from research.dynamic_anchor_p2_2.replay import _classify_special, _meta_for_fill
from research.dynamic_anchor_p2_3.engine import DecompEngine, attach_fill_terminals
from research.dynamic_anchor_p2_3.fill_path import last_bid_at_or_before, wait_ask_path
from research.dynamic_anchor_p2_3.fill_stage import reconcile_fills_with_trades, sym_key
from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from run_p0_3_exact_runtime_replay_20260820 import _anchor_from_fill_t, _ledger_sha
from run_p0_4_exact_vs_fast_parity import CollectorEngine, _Discard
from small_paper.v1r_live_dual_lane import canonical_symbol_key
from small_paper.v1r_primary_runtime import WAIT_SEC

JST = ZoneInfo("Asia/Tokyo")

CONFIRM_KEEP = (
    "date", "session", "symbol", "t0", "t1", "status", "reason",
    "trend_slope", "endpoint_return", "p0", "p10",
)


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


def _session_from_hm(hm: str) -> str:
    return "AM" if int(hm.split(":")[0]) < 12 else "PM"


def _slim_confirm(c: dict[str, Any]) -> dict[str, Any]:
    return {k: c.get(k) for k in CONFIRM_KEEP}


def _ask_for_admit(
    eng: Any,
    *,
    symbol: str,
    signal_time: float,
    limit: Any,
) -> dict[str, Any]:
    board = eng._board_arrays(canonical_symbol_key(symbol))
    lim = None if limit is None else float(limit)
    if lim is None or not (lim == lim) or lim <= 0:
        lim = last_bid_at_or_before(board, float(signal_time))
    path = wait_ask_path(board, signal_time=float(signal_time), limit_bid=lim, wait_sec=WAIT_SEC)
    return path


def replay_decomp_day(payload: dict[str, Any]) -> dict[str, Any]:
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
        t1_out = process_day(payload)
        confirms = [_slim_confirm(c) for c in (t1_out.get("confirms") or [])]
        confirmed_n = sum(1 for c in confirms if c.get("status") == "CONFIRMED")

        deng, ddual = _boot(universe, DecompEngine)
        if ddual is None or not deng.ready:
            return {
                "ok": False,
                "date": day,
                "blocker": getattr(deng, "fail_reason", "dual_unavailable"),
                "elapsed_sec": round(time.perf_counter() - t0w, 3),
            }
        deng.notify_enabled = False
        deng.ingest_audit = _Discard()  # type: ignore[assignment]
        deng.bind_p2_1(t1_out)
        _stream_day(day, capture, deng, ddual)
        deng._harvest(deng.events)
        unresolved = attach_fill_terminals(deng)

        fill_by = {(str(a.get("symbol")), str(a.get("anchor"))): a for a in deng.a_fills}
        exp_by = {(str(a.get("symbol")), str(a.get("anchor"))): a for a in deng.a_expired}
        dyn_paths: list[dict[str, Any]] = []
        for row in deng.terminal_rows:
            t0 = row.get("t0")
            t1 = row.get("t1")
            sym = str(row.get("symbol"))
            board = deng._board_arrays(sym)
            bid_t0 = last_bid_at_or_before(board, float(t0)) if t0 is not None else None
            bid_t1 = last_bid_at_or_before(board, float(t1)) if t1 is not None else None
            bid_move = None
            if bid_t0 is not None and bid_t1 is not None and bid_t0 > 0:
                bid_move = (bid_t1 / bid_t0 - 1.0) * 10000.0
            row["bid_t0"] = bid_t0
            row["bid_t1"] = bid_t1
            row["bid_move_bps"] = bid_move
            if row.get("entry_terminal") != "ADMITTED":
                continue
            path = _ask_for_admit(
                deng, symbol=sym, signal_time=float(t1), limit=row.get("limit") or bid_t1
            )
            hour = datetime.fromtimestamp(float(t1), JST).hour
            rec = {
                "date": day,
                "session": row.get("session") or ("AM" if hour < 12 else "PM"),
                "symbol": sym,
                "t0": t0,
                "t1": t1,
                "anchor": row.get("anchor"),
                "fill_terminal": row.get("fill_terminal"),
                "trend_slope": row.get("trend_slope"),
                "endpoint_return": row.get("endpoint_return"),
                "bid_t0": bid_t0,
                "bid_t1": bid_t1,
                "bid_move_bps": bid_move,
                **path,
            }
            key = (sym, str(row.get("anchor")))
            if key in fill_by:
                rec["engine_fill_time"] = fill_by[key].get("fill_time")
                rec["engine_fill_price"] = fill_by[key].get("fill_price")
            if key in exp_by:
                rec["engine_expired"] = True
            dyn_paths.append(rec)

        raw_dyn = extract_trades(ddual)
        dyn_trades: list[dict[str, Any]] = []
        for tr in raw_dyn:
            fill_t = float(tr.get("entry_time") or 0.0)
            exit_t = tr.get("exit_time")
            sym = canonical_symbol_key(tr.get("symbol"))
            fill_row = next(
                (f for f in deng.a_fills if f.get("symbol") == sym and abs(float(f.get("fill_time") or 0) - fill_t) < 1e-6),
                None,
            )
            admit_row = next((a for a in reversed(deng.a_admits) if a.get("symbol") == sym), None)
            src = fill_row or admit_row or {}
            meta = _meta_for_fill(deng, sym, fill_t, src)
            holding = None
            if fill_t and exit_t is not None:
                holding = round(float(exit_t) - fill_t, 3)
            dyn_trades.append({
                "date": day,
                "session": tr.get("session") or meta.get("session"),
                "symbol": sym,
                "t0": meta.get("t0"),
                "t1": meta.get("t1"),
                "limit": src.get("limit") or tr.get("entry_price"),
                "fill_time": fill_t,
                "fill_price": tr.get("entry_price"),
                "exit_time": exit_t,
                "exit_price": tr.get("exit_price"),
                "exit_reason": tr.get("reason"),
                "holding_sec": holding,
                "pnl_yen_100": float(tr.get("pnl_yen_100") or 0.0),
                "special_class": _classify_special(day, {
                    "fill_time": fill_t,
                    "exit_time": exit_t,
                    "exit_reason": tr.get("reason"),
                    "pnl_yen_100": tr.get("pnl_yen_100"),
                }),
            })

        reconcile_fills_with_trades(
            deng.terminal_rows, dyn_trades, wait_sec=float(WAIT_SEC)
        )
        unresolved = [u for u in unresolved if str(u).startswith("PENDING_LEFT")]
        term_by = {(str(r.get("symbol")), str(r.get("anchor"))): r for r in deng.terminal_rows}
        for rec in dyn_paths:
            src = term_by.get((str(rec.get("symbol")), str(rec.get("anchor"))))
            if src is not None:
                rec["fill_terminal"] = src.get("fill_terminal")

        feng, fdual = _boot(universe, CollectorEngine)
        if fdual is None or not feng.ready:
            return {
                "ok": False,
                "date": day,
                "blocker": getattr(feng, "fail_reason", "fixed_dual_unavailable"),
                "elapsed_sec": round(time.perf_counter() - t0w, 3),
            }
        feng.notify_enabled = False
        feng.ingest_audit = _Discard()  # type: ignore[assignment]
        _stream_day(day, capture, feng, fdual)
        feng._harvest(feng.events)

        selected_n = sum(1 for c in feng.a_candidates if c.get("admitted"))
        fixed_paths: list[dict[str, Any]] = []
        used_ff: set[int] = set()
        fills_f = list(feng.a_fills)
        for a in feng.a_admits:
            sym = str(a.get("symbol"))
            an = str(a.get("anchor") or "")
            if ":" in an:
                sig = _hm_epoch(day, an)
                sess = _session_from_hm(an)
            else:
                sig = None
                sess = None
            if sig is None:
                continue
            path = _ask_for_admit(feng, symbol=sym, signal_time=sig, limit=a.get("limit"))
            ft = "EXPIRED"
            for i, f in enumerate(fills_f):
                if i in used_ff:
                    continue
                if sym_key(f.get("symbol")) != sym_key(sym):
                    continue
                anc_ok = str(f.get("anchor") or "") == an
                ftv = f.get("fill_time")
                time_ok = False
                if ftv is not None:
                    time_ok = (float(sig) - 1e-3) <= float(ftv) <= (float(sig) + float(WAIT_SEC) + 0.5)
                if anc_ok or time_ok:
                    used_ff.add(i)
                    ft = "FILLED"
                    break
            fixed_paths.append({
                "date": day,
                "session": sess,
                "symbol": sym,
                "anchor": an,
                "signal_time": sig,
                "fill_terminal": ft,
                "limit": a.get("limit"),
                **path,
            })

        raw_fix = extract_trades(fdual)
        fixed_trades: list[dict[str, Any]] = []
        for i, tr in enumerate(raw_fix, start=1):
            fill_t = float(tr.get("entry_time") or 0.0)
            exit_t = tr.get("exit_time")
            an = _anchor_from_fill_t(day, fill_t)
            sym = canonical_symbol_key(tr.get("symbol"))
            holding = None
            if fill_t and exit_t is not None:
                holding = round(float(exit_t) - fill_t, 3)
            fixed_trades.append({
                "date": day,
                "session": tr.get("session"),
                "trade_id": f"{day}|{tr.get('session')}|{an}|{sym}|{i}",
                "symbol": sym,
                "anchor_time": an,
                "fill_time": fill_t,
                "fill_price": tr.get("entry_price"),
                "exit_time": exit_t,
                "exit_price": tr.get("exit_price"),
                "exit_reason": tr.get("reason"),
                "pnl_yen_100": float(tr.get("pnl_yen_100") or 0.0),
                "holding_sec": holding,
            })

        am_adm = [p for p in fixed_paths if p.get("session") == "AM"]
        pm_adm = [p for p in fixed_paths if p.get("session") == "PM"]
        dyn_am = [p for p in dyn_paths if p.get("session") == "AM"]
        dyn_pm = [p for p in dyn_paths if p.get("session") == "PM"]

        return {
            "ok": True,
            "date": day,
            "capture_class": cap_class,
            "confirmed": confirmed_n,
            "false_to_true_triggers": len(t1_out.get("triggers") or []),
            "confirms": confirms,
            "dyn_funnel_p22_style": dict(deng.funnel),
            "dyn_terminals": list(deng.terminal_rows),
            "dyn_unresolved": unresolved,
            "dyn_admitted_engine": int(deng.primary_admitted),
            "dyn_fills_engine": int(deng.primary_fills),
            "dyn_expired_engine": int(deng.primary_expired),
            "dyn_paths": dyn_paths,
            "dyn_trades": dyn_trades,
            "dyn_am_admitted": len(dyn_am),
            "dyn_pm_admitted": len(dyn_pm),
            "fixed_candidates_scored": len(feng.a_candidates),
            "fixed_selected": selected_n,
            "fixed_admitted": int(feng.primary_admitted),
            "fixed_fills": int(feng.primary_fills),
            "fixed_expired": int(feng.primary_expired),
            "fixed_cap_blocked": int(feng.cap_blocked),
            "fixed_same_symbol_blocked": int(feng.same_symbol_blocked),
            "fixed_paths": fixed_paths,
            "fixed_trades": fixed_trades,
            "fixed_am_admitted": len(am_adm),
            "fixed_pm_admitted": len(pm_adm),
            "fixed_ledger_sha": _ledger_sha(fixed_trades),
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
            "WAIT_SEC": WAIT_SEC,
            "pm_end": session_end_epoch(day, "PM"),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
