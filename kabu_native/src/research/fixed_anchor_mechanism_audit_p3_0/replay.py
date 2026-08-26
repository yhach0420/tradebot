"""One-day chronological Fixed replay. Baseline matches P1 CollectorEngine path."""
from __future__ import annotations

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

from research.anchor_vs_event_driven.run_comparison import (  # noqa: E402
    _boot,
    _stream_day,
    extract_trades,
)
from research.fixed_anchor_mechanism_audit_p3_0.diagnostic import (  # noqa: E402
    assemble_cross_section,
    market_state_grid,
)
from research.fixed_anchor_mechanism_audit_p3_0.engine import P3Engine  # noqa: E402
from run_p0_3_exact_runtime_replay_20260820 import (  # noqa: E402
    _anchor_from_fill_t,
    _iso,
    _ledger_sha,
    _maxdd,
    _pf,
    _sess_stats,
)
from run_p0_4_exact_vs_fast_parity import _Discard  # noqa: E402
from small_paper.v1r_live_dual_lane import canonical_symbol_key  # noqa: E402


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


def _pack_trades(
    *,
    day: str,
    raw_trades: list[dict[str, Any]],
    eng: Any,
    variant: str,
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for i, tr in enumerate(raw_trades, start=1):
        fill_t = float(tr.get("entry_time") or 0.0)
        exit_t = tr.get("exit_time")
        sym = canonical_symbol_key(tr.get("symbol"))
        if variant == "baseline":
            an = _anchor_from_fill_t(day, fill_t)
            fill_row = next((f for f in eng.a_fills if f.get("symbol") == sym and f.get("anchor") == an), None)
            admit_row = next((a for a in eng.a_admits if a.get("symbol") == sym and a.get("anchor") == an), None)
            snap = eng.snapshots.get((an, sym), {})
        else:
            fill_row = next(
                (
                    f
                    for f in eng.a_fills
                    if f.get("symbol") == sym and abs(float(f.get("fill_time") or 0.0) - fill_t) < 1e-6
                ),
                None,
            )
            an = str((fill_row or {}).get("anchor") or "")
            if not an:
                admit_row = next((a for a in reversed(eng.a_admits) if a.get("symbol") == sym), None)
                an = str((admit_row or {}).get("anchor") or _anchor_from_fill_t(day, fill_t))
            else:
                admit_row = next(
                    (a for a in eng.a_admits if a.get("symbol") == sym and a.get("anchor") == an),
                    None,
                )
            snap = eng.snapshots.get((an, sym), {})
        src = fill_row or admit_row or snap
        holding = None
        if fill_t and exit_t is not None:
            holding = round(float(exit_t) - fill_t, 3)
        trades.append(
            {
                "date": day,
                "session": tr.get("session"),
                "trade_id": f"{day}|{tr.get('session')}|{an}|{sym}|{i}",
                "symbol": sym,
                "anchor_time": an,
                "snapshot_sequence": snap.get("snapshot_sequence"),
                "score": src.get("score") if src else None,
                "candidate_rank": (admit_row or snap).get("rank") if (admit_row or snap) else None,
                "limit": (fill_row or admit_row or {}).get("limit") or tr.get("entry_price"),
                "fill_time": fill_t,
                "fill_time_iso": _iso(fill_t),
                "fill_price": tr.get("entry_price"),
                "exit_time": exit_t,
                "exit_time_iso": _iso(exit_t),
                "exit_price": tr.get("exit_price"),
                "exit_reason": tr.get("reason"),
                "pnl_yen_100": float(tr.get("pnl_yen_100") or 0.0),
                "holding_sec": holding,
            }
        )
    return trades


def replay_p3_day(payload: dict[str, Any]) -> dict[str, Any]:
    _pop_webhooks()
    if str(NATIVE / "src") not in sys.path:
        sys.path.insert(0, str(NATIVE / "src"))
        sys.path.insert(0, str(NATIVE / "scripts"))

    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    variant = str(payload.get("variant") or "baseline")
    offset_sec = int(payload.get("offset_sec") or 0)
    allowed_hm = payload.get("allowed_hm")
    fire_mode = str(payload.get("fire_mode") or "production")
    with_diag = bool(payload.get("with_diagnostics"))
    t0w = time.perf_counter()
    try:
        eng, dual = _boot(universe, P3Engine)
        if dual is None or not eng.ready:
            return {
                "ok": False,
                "date": day,
                "variant": variant,
                "blocker": getattr(eng, "fail_reason", "dual_unavailable"),
                "elapsed_sec": round(time.perf_counter() - t0w, 3),
            }
        eng.offset_sec = offset_sec
        eng.allowed_hm = (
            tuple((int(h), int(m)) for h, m in allowed_hm) if allowed_hm is not None else None
        )
        eng.fire_mode = fire_mode
        eng.notify_enabled = False
        eng.ingest_audit = _Discard()  # type: ignore[assignment]
        events_n, last_et = _stream_day(day, capture, eng, dual)
        eng._harvest(eng.events)
        raw_trades = extract_trades(dual)
        trades = _pack_trades(day=day, raw_trades=raw_trades, eng=eng, variant=variant)
        pnls = [float(t.get("pnl_yen_100") or 0.0) for t in trades]
        w, l, d = _wl(trades)
        gp, gl = _gross(trades)
        ledger_sum = round(sum(pnls), 2)
        out: dict[str, Any] = {
            "ok": True,
            "date": day,
            "variant": variant,
            "offset_sec": offset_sec,
            "fire_mode": fire_mode,
            "universe_n": len(universe),
            "universe_source": str(payload.get("universe_source") or ""),
            "events_processed": events_n,
            "anchor_fires": int(eng.anchor_fires),
            "admitted": int(eng.primary_admitted),
            "fills": int(eng.primary_fills),
            "expired": int(eng.primary_expired),
            "trades": trades,
            "trade_n": len(trades),
            "win": w,
            "loss": l,
            "draw": d,
            "pnl": ledger_sum,
            "gross_profit": gp,
            "gross_loss": gl,
            "PF": _pf(pnls),
            "avg_pnl": round(ledger_sum / len(trades), 4) if trades else 0.0,
            "maxDD": _maxdd(trades),
            "AM": _sess_stats(trades, "AM"),
            "PM": _sess_stats(trades, "PM"),
            "ledger_sha": _ledger_sha(trades),
            "last_et": last_et,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
            "xs_rows": [],
            "market_state": [],
            "snapshot_future_leak": False,
        }
        if with_diag:
            xs, leak_xs = assemble_cross_section(eng, day=day, trades=trades)
            ms, leak_ms = market_state_grid(eng, day=day)
            out["xs_rows"] = xs
            out["market_state"] = ms
            out["snapshot_future_leak"] = bool(leak_xs or leak_ms)
            out["elapsed_sec"] = round(time.perf_counter() - t0w, 3)
        return out
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "variant": variant,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
