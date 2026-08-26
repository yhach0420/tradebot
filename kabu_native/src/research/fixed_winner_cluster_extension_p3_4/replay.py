"""FULL14 uncompacted boards: 600s gate + 600→750 Bid/MID. No new fill/exit."""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_vs_event_driven.run_comparison import _boot, _stream_day
from research.fixed_selection_diagnostic_reconcile_p3_0r.replay import P3REngine, _pop_webhooks
from research.fixed_winner_cluster_extension_p3_4 import EXIT600_REASON, EXTEND_REASON, REACHED_600_REASONS
from research.fixed_winner_cluster_extension_p3_4.decision import (
    checkpoint_quotes,
    reconstruct_600_decision,
    rel_close,
)
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_live_dual_lane import canonical_symbol_key

DAY_CACHE = NATIVE / "results" / "research" / "_p3_4_day_cache"
CACHE_VERSION = 2


def _attach_fill_to_600_trace(dual: Any) -> None:
    """Research wrap only. Does not change Dual Lane source. Stamps fill identity on 600_DECISION."""
    orig = dual._trace

    def _trace(event: str, symbol: str, extra: dict[str, Any]) -> None:
        if event == "600_DECISION":
            pos = dual.primary.get(canonical_symbol_key(symbol))
            extra = dict(extra or {})
            if pos is not None:
                extra["fill_time"] = pos.fill_time
                extra["fill_price"] = pos.fill_price
                extra["n_ticks_at_decision"] = len(pos.t)
                if pos.t:
                    extra["last_tick_t"] = pos.t[-1]
                    extra["decision_off_last_tick"] = float(pos.t[-1]) - float(pos.fill_time)
            orig(event, symbol, extra)
            return
        orig(event, symbol, extra)

    dual._trace = _trace  # type: ignore[method-assign]


def replay_day(payload: dict[str, Any]) -> dict[str, Any]:
    _pop_webhooks()
    t0w = time.perf_counter()
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
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
        _attach_fill_to_600_trace(dual)
        _stream_day(day, capture, eng, dual)

        boards = {s: b.view() for s, b in eng.full_bufs.items()}
        runtime_600: dict[tuple[str, float], dict[str, Any]] = {}
        by_sym_time: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for tr in dual.traces:
            if tr.get("event") != "600_DECISION" or tr.get("lane") != "primary":
                continue
            by_sym_time[str(tr.get("symbol"))].append(tr)
            ft = tr.get("fill_time")
            if ft is not None:
                runtime_600[(str(tr.get("symbol")), round(float(ft), 6))] = tr
        rows: list[dict[str, Any]] = []
        leak_decision = leak_chk = 0
        ident_n = ident_fail = 0

        for tr in trades:
            sym = canonical_symbol_key(tr.get("symbol"))
            fill_t = float(tr["fill_time"])
            fill_px = float(tr["fill_price"])
            sess = str(tr.get("session") or "AM")
            reason = str(tr.get("exit_reason") or "")
            reached = reason in REACHED_600_REASONS
            rec: dict[str, Any] = {
                "trade_id": tr.get("trade_id"),
                "date": day,
                "symbol": sym,
                "session": sess,
                "anchor_time": tr.get("anchor_time"),
                "fill_time": fill_t,
                "fill_price": fill_px,
                "exit_time": tr.get("exit_time"),
                "exit_price": tr.get("exit_price"),
                "exit_reason": reason,
                "pnl_yen_100": tr.get("pnl_yen_100"),
                "holding_sec": tr.get("holding_sec"),
                "reached_600": reached,
                "canonical_class": (
                    "EXTEND_TO_750" if reason == EXTEND_REASON else ("EXIT_AT_600" if reason == EXIT600_REASON else "NOT_REACHED_600")
                ),
                "board_present": boards.get(sym) is not None,
            }
            board = boards.get(sym)
            if board is None:
                rec["recon_class"] = "NOT_EVALUABLE"
                rec["matched"] = False
                rows.append(rec)
                continue

            dec = reconstruct_600_decision(
                board,
                date=day,
                symbol=sym,
                session=sess,
                fill_time=fill_t,
                fill_price=fill_px,
            )
            rec["feat_ret"] = dec.get("feat_ret")
            rec["feat_mfe"] = dec.get("feat_mfe")
            rec["feat_imb"] = dec.get("feat_imb")
            rec["feat_gb_frac"] = dec.get("feat_gb_frac")
            rec["independent_recon_class"] = dec.get("recon_class")
            rec["independent_guard_hit"] = dec.get("guard_hit")
            rec["continuation_id"] = dec.get("continuation_id")
            rec["guard_id"] = dec.get("guard_id")
            rec["decision_future_leak"] = 0
            rec["runtime_off_now"] = None
            rec["runtime_last_tick_t"] = None
            rec["runtime_n_ticks"] = None
            rec["dual_lane_600_fire_after_t600"] = False
            rt = runtime_600.get((sym, round(fill_t, 6)))
            if rt is None:
                cands = [
                    x
                    for x in by_sym_time.get(sym) or []
                    if x.get("fill_time") is not None and abs(float(x["fill_time"]) - fill_t) < 0.05
                ]
                rt = cands[0] if cands else None
            if rt is not None:
                ext_rt = bool(rt.get("extended"))
                rec["recon_class"] = "EXTEND_TO_750" if ext_rt else "EXIT_AT_600"
                rec["extended_recon"] = ext_rt
                rec["runtime_reason"] = rt.get("reason")
                rec["runtime_off_now"] = rt.get("off_now")
                rec["runtime_last_tick_t"] = rt.get("last_tick_t")
                rec["runtime_n_ticks"] = rt.get("n_ticks_at_decision")
                last_t = rt.get("last_tick_t")
                off_now = rt.get("off_now")
                if off_now is None and last_t is not None:
                    off_now = float(last_t) - fill_t
                    rec["runtime_off_now"] = off_now
                # Dual Lane 600_DECISION is the freeze. The 0.5s sampler fires on the first
                # tick with off>=600, which may be after fill+600. That is Runtime clock,
                # not 750-window leakage. Count decision leak only if the 600 gate consumed
                # a tick at/after the 750s horizon.
                if off_now is not None and float(off_now) > 600.0 + 1e-9:
                    rec["dual_lane_600_fire_after_t600"] = True
                if off_now is not None and float(off_now) + 1e-9 >= 750.0:
                    rec["decision_future_leak"] = 1
                    leak_decision += 1
                elif last_t is not None and float(last_t) + 1e-9 >= fill_t + 750.0:
                    rec["decision_future_leak"] = 1
                    leak_decision += 1
            else:
                rec["recon_class"] = "NOT_EVALUABLE"
                rec["extended_recon"] = None
            if reached:
                rec["matched"] = rec["recon_class"] == rec["canonical_class"]
            else:
                rec["matched"] = None

            q600 = checkpoint_quotes(board, day=day, session=sess, fill_time=fill_t, horizon_sec=600)
            q750 = checkpoint_quotes(board, day=day, session=sess, fill_time=fill_t, horizon_sec=750)
            leak_chk += int(q600.get("leak_n") or 0) + int(q750.get("leak_n") or 0)
            rec["status_600"] = q600.get("status")
            rec["status_750"] = q750.get("status")
            rec["bid600"] = q600.get("bid1")
            rec["bid750"] = q750.get("bid1")
            rec["mid600"] = q600.get("mid")
            rec["mid750"] = q750.get("mid")
            rec["bid_ret_600_750"] = None
            rec["mid_ret_600_750"] = None
            rec["ret_entry_to_600"] = None
            rec["ret_600_to_750"] = None
            rec["ret_entry_to_750"] = None
            rec["incremental_value_600_750_yen"] = None
            rec["identity_pass"] = None
            rec["outcome_evaluable"] = False

            b6, b7 = q600.get("bid1"), q750.get("bid1")
            if (
                q600.get("evaluable")
                and q750.get("evaluable")
                and q600.get("status") == "OK"
                and q750.get("status") == "OK"
                and b6 is not None
                and b7 is not None
                and float(b6) > 0
                and fill_px > 0
            ):
                rec["outcome_evaluable"] = True
                rec["bid_ret_600_750"] = float(b7) / float(b6) - 1.0
                rec["ret_entry_to_600"] = float(b6) / fill_px - 1.0
                rec["ret_600_to_750"] = float(b7) / float(b6) - 1.0
                rec["ret_entry_to_750"] = float(b7) / fill_px - 1.0
                rec["incremental_value_600_750_yen"] = (float(b7) - float(b6)) * 100.0
                if rec["canonical_class"] == "EXTEND_TO_750":
                    lhs = float(b7) / fill_px
                    rhs = (float(b6) / fill_px) * (float(b7) / float(b6))
                    ident_n += 1
                    ok_id = rel_close(lhs, rhs)
                    rec["identity_pass"] = ok_id
                    if not ok_id:
                        ident_fail += 1
            m6, m7 = q600.get("mid"), q750.get("mid")
            if m6 is not None and m7 is not None and float(m6) > 0 and q600.get("status") == "OK" and q750.get("status") == "OK":
                rec["mid_ret_600_750"] = float(m7) / float(m6) - 1.0

            rows.append(rec)

        return {
            "ok": True,
            "date": day,
            "rows": rows,
            "n_canonical": len(trades),
            "leak_decision": leak_decision,
            "leak_checkpoint": leak_chk,
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
    """Picklable worker entry. Cache is outside the P3-4 output folder."""
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
