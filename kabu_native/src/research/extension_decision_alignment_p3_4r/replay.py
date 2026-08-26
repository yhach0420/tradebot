"""FULL14 Dual Lane 600_DECISION times + post-decision Bid/MID. No new fill/exit."""
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
from research.extension_decision_alignment_p3_4r import EXIT600_REASON, EXTEND_REASON, REACHED_600_REASONS
from research.extension_decision_alignment_p3_4r.quotes import quotes_asof
from research.fixed_selection_diagnostic_reconcile_p3_0r.replay import P3REngine, _pop_webhooks
from research.fixed_winner_cluster_extension_p3_4.decision import rel_close
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_live_dual_lane import canonical_symbol_key

DAY_CACHE = NATIVE / "results" / "research" / "_p3_4r_day_cache"
CACHE_VERSION = 1


def _attach_decision_time(dual: Any) -> None:
    """Research wrap only. Dual Lane source unchanged. Stamps capture event_time on 600_DECISION."""
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
                    extra["decision_event_time"] = float(pos.t[-1])
                    extra["last_tick_t"] = float(pos.t[-1])
                    extra["decision_off_last_tick"] = float(pos.t[-1]) - float(pos.fill_time)
            orig(event, symbol, extra)
            return
        orig(event, symbol, extra)

    dual._trace = _trace  # type: ignore[method-assign]


def _match_trace(runtime_600, by_sym_time, sym: str, fill_t: float):
    rt = runtime_600.get((sym, round(fill_t, 6)))
    if rt is not None:
        return rt
    cands = [
        x
        for x in by_sym_time.get(sym) or []
        if x.get("fill_time") is not None and abs(float(x["fill_time"]) - fill_t) < 0.05
    ]
    return cands[0] if cands else None


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
        _attach_decision_time(dual)
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
        overlap_n = 0
        n_ge_750 = 0

        for tr in trades:
            sym = canonical_symbol_key(tr.get("symbol"))
            fill_t = float(tr["fill_time"])
            fill_px = float(tr["fill_price"])
            sess = str(tr.get("session") or "AM")
            reason = str(tr.get("exit_reason") or "")
            reached = reason in REACHED_600_REASONS
            nominal_t600 = fill_t + 600.0
            nominal_t750 = fill_t + 750.0
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
                "reached_600": reached,
                "nominal_t600": nominal_t600,
                "nominal_t750": nominal_t750,
                "canonical_class": (
                    "EXTEND_TO_750"
                    if reason == EXTEND_REASON
                    else ("EXIT_AT_600" if reason == EXIT600_REASON else "NOT_REACHED_600")
                ),
                "board_present": boards.get(sym) is not None,
                "decision_future_leak": 0,
                "checkpoint_future_leak": 0,
                "decision_outcome_overlap": False,
            }
            board = boards.get(sym)
            if board is None:
                rec["recon_class"] = "NOT_EVALUABLE"
                rec["matched"] = False if reached else None
                rows.append(rec)
                continue

            rt = _match_trace(runtime_600, by_sym_time, sym, fill_t)
            if rt is not None:
                ext_rt = bool(rt.get("extended"))
                rec["recon_class"] = "EXTEND_TO_750" if ext_rt else "EXIT_AT_600"
                rec["extended_recon"] = ext_rt
                rec["runtime_reason"] = rt.get("reason")
                rec["runtime_off_now"] = rt.get("off_now")
                dt = rt.get("decision_event_time")
                if dt is None:
                    dt = rt.get("last_tick_t")
                if dt is None and rt.get("off_now") is not None:
                    dt = fill_t + float(rt["off_now"])
                rec["decision_time"] = float(dt) if dt is not None else None
            else:
                rec["recon_class"] = "NOT_EVALUABLE"
                rec["extended_recon"] = None
                rec["decision_time"] = None
            if reached:
                rec["matched"] = rec["recon_class"] == rec["canonical_class"]
            else:
                rec["matched"] = None

            dtime = rec.get("decision_time")
            rec["decision_offset_sec"] = None if dtime is None else float(dtime) - fill_t
            rec["decision_delay_sec"] = None if dtime is None else float(dtime) - nominal_t600

            q600 = quotes_asof(board, day=day, session=sess, asof=nominal_t600)
            q750 = quotes_asof(board, day=day, session=sess, asof=nominal_t750)
            rec["status_600"] = q600.get("status")
            rec["status_750"] = q750.get("status")
            rec["bid600"] = q600.get("bid1")
            rec["bid750"] = q750.get("bid1")
            rec["mid600"] = q600.get("mid")
            rec["mid750"] = q750.get("mid")
            rec["bid600_t"] = q600.get("bid1_t")
            rec["bid750_t"] = q750.get("bid1_t")
            leak_chk += int(q600.get("leak_n") or 0) + int(q750.get("leak_n") or 0)
            rec["checkpoint_future_leak"] = int(q600.get("leak_n") or 0) + int(q750.get("leak_n") or 0)

            rec["bid_decision"] = None
            rec["mid_decision"] = None
            rec["bid_decision_t"] = None
            rec["status_decision"] = None
            rec["status_plus150"] = None
            rec["bid_plus150"] = None
            rec["mid_plus150"] = None
            rec["old_bid_ret_600_750"] = None
            rec["predecision_bid_return"] = None
            rec["decision_to_750_bid_return"] = None
            rec["decision_to_750_mid_return"] = None
            rec["decision_plus150_bid_return"] = None
            rec["decision_plus150_mid_return"] = None
            rec["old_600_750_value_yen"] = None
            rec["predecision_value_yen"] = None
            rec["post_decision_value_yen"] = None
            rec["identity_pass"] = None
            rec["primary_evaluable"] = False
            rec["plus150_evaluable"] = False
            rec["decision_ge_t750"] = False

            if dtime is not None and float(dtime) + 1e-9 >= nominal_t750:
                rec["decision_ge_t750"] = True
                rec["status_decision"] = "NOT_EVALUABLE"
                n_ge_750 += 1
                rows.append(rec)
                continue

            if dtime is not None:
                qd = quotes_asof(board, day=day, session=sess, asof=float(dtime))
                rec["status_decision"] = qd.get("status")
                rec["bid_decision"] = qd.get("bid1")
                rec["mid_decision"] = qd.get("mid")
                rec["bid_decision_t"] = qd.get("bid1_t")
                leak_d = int(qd.get("leak_n") or 0)
                leak_chk += leak_d
                rec["decision_future_leak"] = leak_d
                leak_decision += leak_d
                bt = qd.get("bid1_t")
                if bt is not None and float(bt) > float(dtime) + 1e-12:
                    rec["decision_future_leak"] = 1
                    leak_decision += 1
                q150 = quotes_asof(board, day=day, session=sess, asof=float(dtime) + 150.0)
                rec["status_plus150"] = q150.get("status")
                rec["bid_plus150"] = q150.get("bid1")
                rec["mid_plus150"] = q150.get("mid")
                leak_chk += int(q150.get("leak_n") or 0)
                rec["checkpoint_future_leak"] = int(rec.get("checkpoint_future_leak") or 0) + int(q150.get("leak_n") or 0)

                bdec, b7 = qd.get("bid1"), q750.get("bid1")
                overlap = False
                if bt is not None and float(bt) > float(dtime) + 1e-12:
                    overlap = True
                b7t = q750.get("bid1_t")
                if (
                    bt is not None
                    and b7t is not None
                    and float(b7t) + 1e-12 < float(bt)
                ):
                    overlap = True
                rec["decision_outcome_overlap"] = overlap
                if overlap:
                    overlap_n += 1

                if (
                    qd.get("status") == "OK"
                    and q750.get("status") == "OK"
                    and bdec is not None
                    and b7 is not None
                    and float(bdec) > 0
                    and int(qd.get("leak_n") or 0) == 0
                    and int(q750.get("leak_n") or 0) == 0
                    and not overlap
                ):
                    rec["decision_to_750_bid_return"] = float(b7) / float(bdec) - 1.0
                    rec["post_decision_value_yen"] = (float(b7) - float(bdec)) * 100.0
                    rec["primary_evaluable"] = bool(reached)
                    mdec, m7 = qd.get("mid"), q750.get("mid")
                    if mdec is not None and m7 is not None and float(mdec) > 0:
                        rec["decision_to_750_mid_return"] = float(m7) / float(mdec) - 1.0
                else:
                    rec["primary_evaluable"] = False
                    mdec = qd.get("mid")

                b150 = q150.get("bid1")
                plus_ok = (
                    reached
                    and qd.get("status") == "OK"
                    and q150.get("status") == "OK"
                    and qd.get("evaluable")
                    and q150.get("evaluable")
                    and bdec is not None
                    and b150 is not None
                    and float(bdec) > 0
                    and int(q150.get("leak_n") or 0) == 0
                )
                rec["plus150_evaluable"] = bool(plus_ok)
                if plus_ok:
                    rec["decision_plus150_bid_return"] = float(b150) / float(bdec) - 1.0
                    rec["plus150_label"] = "STANDARDIZED_POST_DECISION_150S_DIAGNOSTIC"
                m150 = q150.get("mid")
                if plus_ok and mdec is not None and m150 is not None and float(mdec) > 0:
                    rec["decision_plus150_mid_return"] = float(m150) / float(mdec) - 1.0

            b6, b7 = q600.get("bid1"), q750.get("bid1")
            if (
                q600.get("status") == "OK"
                and q750.get("status") == "OK"
                and q600.get("evaluable")
                and q750.get("evaluable")
                and b6 is not None
                and b7 is not None
                and float(b6) > 0
            ):
                rec["old_bid_ret_600_750"] = float(b7) / float(b6) - 1.0
                rec["old_600_750_value_yen"] = (float(b7) - float(b6)) * 100.0
                rec["old_label"] = "OLD_NOMINAL_600_BASED"

            bdec = rec.get("bid_decision")
            if b6 is not None and bdec is not None and float(b6) > 0 and rec.get("status_decision") == "OK":
                rec["predecision_bid_return"] = float(bdec) / float(b6) - 1.0
                rec["predecision_value_yen"] = (float(bdec) - float(b6)) * 100.0

            if rec.get("canonical_class") == "EXTEND_TO_750" and rec.get("old_600_750_value_yen") is not None:
                pre_v = rec.get("predecision_value_yen")
                post_v = rec.get("post_decision_value_yen")
                if pre_v is not None and post_v is not None:
                    lhs = float(rec["old_600_750_value_yen"])
                    rhs = float(pre_v) + float(post_v)
                    ident_n += 1
                    ok_id = rel_close(lhs, rhs)
                    rec["identity_pass"] = ok_id
                    if not ok_id:
                        ident_fail += 1

            rows.append(rec)

        return {
            "ok": True,
            "date": day,
            "rows": rows,
            "n_canonical": len(trades),
            "leak_decision": leak_decision,
            "leak_checkpoint": leak_chk,
            "overlap_n": overlap_n,
            "n_decision_ge_750": n_ge_750,
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
