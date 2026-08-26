"""FULL14 boards → causal mid-hold checkpoint states. No new fill/exit."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_vs_event_driven.run_comparison import _boot, _stream_day
from research.canonical_fixed_pnl_source_p3_3.ledger import pnl, wl
from research.fixed_selection_diagnostic_reconcile_p3_0r.replay import P3REngine, _pop_webhooks
from research.mid_hold_state_separability_p4_0 import (
    CHECKPOINTS_SEC,
    EXIT600_REASON,
    EXTEND_REASON,
    IMBALANCE_REASON,
    SESSION_CLOSE_REASON,
)
from research.mid_hold_state_separability_p4_0.state import checkpoint_state
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_live_dual_lane import canonical_symbol_key

DAY_CACHE = NATIVE / "results" / "research" / "_p4_0_day_cache"
CACHE_VERSION = 1


def _labels(tr: dict[str, Any], *, top10: set[str], top20: set[str]) -> dict[str, Any]:
    reason = str(tr.get("exit_reason") or "")
    p = pnl(tr)
    hold = float(tr.get("holding_sec") or 0.0)
    tid = str(tr.get("trade_id") or "")
    return {
        "CANONICAL_FINAL_WIN": wl(p) == "WIN",
        "CANONICAL_FINAL_LOSS": wl(p) == "LOSS",
        "CANONICAL_FINAL_DRAW": wl(p) == "DRAW",
        "EARLY_FAILURE_BEFORE_600": reason == IMBALANCE_REASON and hold + 1e-12 < 600.0,
        "REACHED_600_EXIT": reason == EXIT600_REASON,
        "REACHED_600_EXTEND": reason == EXTEND_REASON,
        "TOP10_CANONICAL_WINNER": tid in top10,
        "TOP20_CANONICAL_WINNER": tid in top20,
        "exit_reason": reason,
        "holding_sec": hold,
        "pnl_yen_100": p,
    }


def replay_day(payload: dict[str, Any]) -> dict[str, Any]:
    _pop_webhooks()
    t0w = time.perf_counter()
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    trades = list(payload.get("canonical_trades") or [])
    top10 = set(payload.get("top10_ids") or [])
    top20 = set(payload.get("top20_ids") or [])
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
        boards = {s: b.view() for s, b in eng.full_bufs.items()}
        rows: list[dict[str, Any]] = []
        leak_n = ident_n = ident_fail = 0

        for tr in trades:
            sym = canonical_symbol_key(tr.get("symbol"))
            labs = _labels(tr, top10=top10, top20=top20)
            fill_t = tr.get("fill_time")
            fill_px = tr.get("fill_price")
            exit_t = tr.get("exit_time")
            sess = str(tr.get("session") or "AM")
            base = {
                "trade_id": tr.get("trade_id"),
                "date": day,
                "symbol": sym,
                "session": sess,
                "anchor_time": tr.get("anchor_time"),
                "fill_time": fill_t,
                "fill_price": fill_px,
                "exit_time": exit_t,
                **labs,
            }
            if fill_t is None or fill_px is None or float(fill_px) <= 0:
                for h in CHECKPOINTS_SEC:
                    rec = dict(base)
                    rec["horizon_sec"] = h
                    rec["eligible"] = False
                    rec["uneval_reason"] = "FILL_INVALID"
                    rec["status"] = "PATH_NOT_EVALUABLE"
                    rows.append(rec)
                continue
            fill_t = float(fill_t)
            fill_px = float(fill_px)
            board = boards.get(sym)
            if board is None:
                for h in CHECKPOINTS_SEC:
                    rec = dict(base)
                    rec["horizon_sec"] = h
                    rec["eligible"] = False
                    rec["uneval_reason"] = "NO_BOARD"
                    rec["status"] = "PATH_NOT_EVALUABLE"
                    rows.append(rec)
                continue
            if exit_t is None or float(exit_t) + 1e-12 < fill_t:
                for h in CHECKPOINTS_SEC:
                    rec = dict(base)
                    rec["horizon_sec"] = h
                    rec["eligible"] = False
                    rec["uneval_reason"] = "EXIT_TIMESTAMP_ARTIFACT"
                    rec["status"] = "PATH_NOT_EVALUABLE"
                    rows.append(rec)
                continue

            for h in CHECKPOINTS_SEC:
                rec = dict(base)
                rec["horizon_sec"] = int(h)
                chk = fill_t + float(h)
                rec["checkpoint"] = chk
                still_open = float(exit_t) > chk + 1e-12
                rec["still_open"] = still_open
                if not still_open:
                    rec["eligible"] = False
                    rec["uneval_reason"] = "ALREADY_EXITED"
                    rec["status"] = "NOT_OPEN"
                    rows.append(rec)
                    continue
                st = checkpoint_state(
                    board,
                    day=day,
                    session=sess,
                    fill_time=fill_t,
                    fill_price=fill_px,
                    horizon_sec=int(h),
                )
                rec.update({k: v for k, v in st.items() if k not in rec})
                leak_n += int(st.get("leak_n") or 0)
                if st.get("identity_pass") is not None:
                    ident_n += 1
                    if st.get("identity_pass") is False:
                        ident_fail += 1
                rec["eligible"] = st.get("status") == "OK"
                if not rec["eligible"] and not rec.get("uneval_reason"):
                    rec["uneval_reason"] = st.get("uneval_reason") or "PATH_NOT_EVALUABLE"
                rows.append(rec)

        return {
            "ok": True,
            "date": day,
            "rows": rows,
            "n_canonical": len(trades),
            "leak_n": leak_n,
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


def replay_day_cached(payload: dict[str, Any]) -> dict[str, Any]:
    day = str(payload["date"])
    DAY_CACHE.mkdir(parents=True, exist_ok=True)
    cp = DAY_CACHE / f"{day}.json"
    if cp.is_file():
        try:
            cached = json.loads(cp.read_text(encoding="utf-8"))
        except Exception:
            cached = None
        if cached and cached.get("ok") and cached.get("cache_version") == CACHE_VERSION:
            cached["from_cache"] = True
            return cached
    out = replay_day(payload)
    out["cache_version"] = CACHE_VERSION
    if out.get("ok"):
        cp.write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
    return out
