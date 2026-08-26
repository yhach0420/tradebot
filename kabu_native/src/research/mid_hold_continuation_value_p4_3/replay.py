"""FULL14 boards → P4-2 causal state + local counterfactual EXIT value. No Gate."""
from __future__ import annotations

import json
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
from research.canonical_fixed_pnl_source_p3_3.ledger import pnl, wl
from research.fixed_selection_diagnostic_reconcile_p3_0r.replay import P3REngine, _pop_webhooks
from research.mid_hold_continuation_value_p4_3 import (
    CACHE_VERSION,
    EXIT600_REASON,
    EXIT_CHECKPOINTS_SEC,
    EXTEND_REASON,
    STATE_CHECKPOINTS_SEC,
    STATE_NON_RECOVERING,
    STATE_RECOVERING,
)
from research.mid_hold_continuation_value_p4_3.exec import (
    checkpoint_exit_pnl_yen_100,
    first_valid_executable_buy1,
)
from research.mid_hold_state_separability_p4_0.state import checkpoint_state
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_live_dual_lane import canonical_symbol_key, session_end_for_position

DAY_CACHE = NATIVE / "results" / "research" / "_p4_3_day_cache"


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


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
            fill_t = tr.get("fill_time")
            fill_px = tr.get("fill_price")
            exit_t = tr.get("exit_time")
            exit_px = tr.get("exit_price")
            sess = str(tr.get("session") or "AM")
            reason = str(tr.get("exit_reason") or "")
            p = pnl(tr)
            tid = str(tr.get("trade_id") or "")
            hold = float(tr.get("holding_sec") or 0.0)
            base = {
                "trade_id": tid,
                "date": day,
                "symbol": sym,
                "session": sess,
                "anchor_time": tr.get("anchor_time"),
                "fill_time": fill_t,
                "fill_price": fill_px,
                "exit_time": exit_t,
                "exit_price": exit_px,
                "exit_reason": reason,
                "holding_sec": hold,
                "canonical_final_pnl_yen_100": p,
                "FINAL_WIN": wl(p) == "WIN",
                "FINAL_LOSS": wl(p) == "LOSS",
                "FINAL_DRAW": wl(p) == "DRAW",
                "EXTEND_TO_750": reason == EXTEND_REASON,
                "EXIT_AT_600": reason == EXIT600_REASON,
                "TOP10": tid in top10,
                "TOP20": tid in top20,
            }
            if fill_t is None or fill_px is None or float(fill_px) <= 0:
                for h in EXIT_CHECKPOINTS_SEC:
                    rec = dict(base)
                    rec["horizon_sec"] = h
                    rec["evaluable"] = False
                    rec["uneval_reason"] = "FILL_INVALID"
                    rows.append(rec)
                continue
            fill_t = float(fill_t)
            fill_px = float(fill_px)
            board = boards.get(sym)
            if board is None:
                for h in EXIT_CHECKPOINTS_SEC:
                    rec = dict(base)
                    rec["horizon_sec"] = h
                    rec["evaluable"] = False
                    rec["uneval_reason"] = "NO_BOARD"
                    rows.append(rec)
                continue
            if exit_t is None or float(exit_t) + 1e-12 < fill_t:
                for h in EXIT_CHECKPOINTS_SEC:
                    rec = dict(base)
                    rec["horizon_sec"] = h
                    rec["evaluable"] = False
                    rec["uneval_reason"] = "EXIT_TIMESTAMP_ARTIFACT"
                    rows.append(rec)
                continue
            exit_t = float(exit_t)
            sess_end = session_end_for_position(date=day, session=sess, fill_time=fill_t)
            t_until = min(exit_t, float(sess_end))

            states: dict[int, dict[str, Any]] = {}
            for h in STATE_CHECKPOINTS_SEC:
                chk = fill_t + float(h)
                still_open = exit_t > chk + 1e-12
                st_row: dict[str, Any] = {
                    "horizon_sec": int(h),
                    "checkpoint": chk,
                    "still_open": still_open,
                    "eligible": False,
                }
                if not still_open:
                    st_row["uneval_reason"] = "ALREADY_EXITED"
                    states[int(h)] = st_row
                    continue
                st = checkpoint_state(
                    board,
                    day=day,
                    session=sess,
                    fill_time=fill_t,
                    fill_price=fill_px,
                    horizon_sec=int(h),
                )
                st_row.update({k: v for k, v in st.items() if k not in st_row})
                leak_n += int(st.get("leak_n") or 0)
                if st.get("identity_pass") is not None:
                    ident_n += 1
                    if st.get("identity_pass") is False:
                        ident_fail += 1
                st_row["eligible"] = st.get("status") == "OK"
                if not st_row["eligible"] and not st_row.get("uneval_reason"):
                    st_row["uneval_reason"] = st.get("uneval_reason") or "PATH_NOT_EVALUABLE"
                states[int(h)] = st_row

            r120 = states.get(120) or {}
            br120 = _f(r120.get("bid_return_from_fill")) if r120.get("eligible") else None
            adverse120 = bool(r120.get("eligible") is True and br120 is not None and br120 < 0)

            bid600 = None
            bid600_t = None
            exec600 = first_valid_executable_buy1(board, t_from=fill_t + 600.0, t_until=t_until)
            if exec600.get("ok"):
                bid600 = exec600.get("bid")
                bid600_t = exec600.get("event_time")

            for h in EXIT_CHECKPOINTS_SEC:
                rec = dict(base)
                rec["horizon_sec"] = int(h)
                rec["bid_return_120"] = br120
                rec["cohort_B_adverse120"] = adverse120
                rec["bid_600"] = bid600
                rec["bid_600_time"] = bid600_t
                st = states.get(int(h)) or {}
                rec["checkpoint"] = st.get("checkpoint")
                rec["still_open"] = st.get("still_open")
                rec["state_eligible"] = st.get("eligible") is True
                rec["bid_return_t"] = st.get("bid_return_from_fill")
                rec["bid_t"] = st.get("bid_t")
                rec["bid_t_time"] = st.get("bid_t_time")
                rec["identity_pass"] = st.get("identity_pass")
                delta = None
                br_t = _f(st.get("bid_return_from_fill")) if st.get("eligible") else None
                if br120 is not None and br_t is not None:
                    delta = br_t - br120
                rec["delta_bid_120_to_t"] = delta
                if not adverse120:
                    rec["evaluable"] = False
                    rec["state"] = None
                    rec["uneval_reason"] = "NOT_ADVERSE120"
                    rows.append(rec)
                    continue
                if delta is None:
                    rec["evaluable"] = False
                    rec["state"] = None
                    rec["uneval_reason"] = st.get("uneval_reason") or "STATE_UNEVALUABLE"
                    rows.append(rec)
                    continue
                rec["state"] = STATE_RECOVERING if delta > 0.0 else STATE_NON_RECOVERING
                trigger = fill_t + float(h)
                rec["trigger_time"] = trigger
                if trigger > t_until + 1e-12:
                    rec["evaluable"] = False
                    rec["uneval_reason"] = "TRIGGER_AFTER_CANONICAL_EXIT"
                    rows.append(rec)
                    continue
                nex = first_valid_executable_buy1(board, t_from=trigger, t_until=t_until)
                if not nex.get("ok"):
                    rec["evaluable"] = False
                    rec["uneval_reason"] = nex.get("uneval_reason") or "NO_EXECUTION"
                    rec["execution_time"] = None
                    rec["execution_bid"] = None
                    rec["execution_latency"] = None
                    rows.append(rec)
                    continue
                et = float(nex["event_time"])
                xb = float(nex["bid"])
                rec["execution_time"] = et
                rec["execution_bid"] = xb
                rec["execution_latency"] = et - trigger
                rec["checkpoint_exit_pnl_yen_100"] = checkpoint_exit_pnl_yen_100(fill_px, xb)
                rec["continuation_value_yen_100"] = p - float(rec["checkpoint_exit_pnl_yen_100"])
                rec["evaluable"] = True
                rec["uneval_reason"] = None
                if xb > 0 and exit_px is not None and float(exit_px) > 0:
                    rec["checkpoint_to_canonical_exit_bid_return"] = float(exit_px) / xb - 1.0
                if xb > 0 and bid600 is not None and float(bid600) > 0:
                    rec["checkpoint_to_600_bid_return"] = float(bid600) / xb - 1.0
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
