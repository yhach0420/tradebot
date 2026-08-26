"""FULL14 uncompacted boards + harvest join on canonical fills. No new fill/exit."""
from __future__ import annotations

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
from research.canonical_fixed_pnl_source_p3_3 import HORIZONS_SEC
from research.canonical_fixed_pnl_source_p3_3.path import bid1_at_or_before, mid_checkpoint, walk_fill_to_exit
from research.fixed_anchor_mechanism_audit_p3_0.grid import session_of_epoch
from research.fixed_selection_diagnostic_reconcile_p3_0r.classify import grid_t0
from research.fixed_selection_diagnostic_reconcile_p3_0r.replay import _session_of_anchor
from research.fixed_selection_edge_decomposition_p3_1.replay import P31Engine, _pop_webhooks
from research.fixed_selection_edge_decomposition_p3_1.scan import horizon_status, wait_ask_stats
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_live_dual_lane import canonical_symbol_key


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if x != x:
            return None
        return x
    except (TypeError, ValueError):
        return None


def replay_day(payload: dict[str, Any]) -> dict[str, Any]:
    _pop_webhooks()
    t0w = time.perf_counter()
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    trades = list(payload.get("canonical_trades") or [])
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
        cand_by: dict[tuple[str, str], dict[str, Any]] = {}
        for c in eng.a_candidates:
            cand_by[(str(c.get("anchor") or ""), canonical_symbol_key(c.get("symbol")))] = c
        n_at_anchor: dict[str, int] = {}
        for an, _sym in cand_by:
            n_at_anchor[an] = n_at_anchor.get(an, 0) + 1

        rows: list[dict[str, Any]] = []
        leak_fill = leak_mid = leak_path = leak_bid = 0
        joined = 0

        for tr in trades:
            an = str(tr.get("anchor_time") or "")
            sym = canonical_symbol_key(tr.get("symbol"))
            fill_t = float(tr["fill_time"])
            exit_t = float(tr["exit_time"])
            fill_px = float(tr["fill_price"])
            exit_px = float(tr["exit_price"])
            c = cand_by.get((an, sym))
            harvest_joined = c is not None
            if harvest_joined:
                joined += 1
            t0 = None
            if c is not None and c.get("t0") is not None:
                t0 = float(c["t0"])
            else:
                t0 = grid_t0(day, an)
            board = boards.get(sym)
            sess = str(tr.get("session") or session_of_epoch(day, fill_t) or _session_of_anchor(an))
            rec: dict[str, Any] = {
                "trade_id": tr.get("trade_id"),
                "date": day,
                "symbol": sym,
                "session": sess,
                "anchor_time": an,
                "signal_time": t0,
                "fill_time": fill_t,
                "exit_time": exit_t,
                "fill_price": fill_px,
                "exit_price": exit_px,
                "exit_reason": tr.get("exit_reason"),
                "pnl_yen_100": tr.get("pnl_yen_100"),
                "holding_sec": tr.get("holding_sec"),
                "harvest_joined": harvest_joined,
                "alloc_score": None if c is None else c.get("score"),
                "harvest_rank": None if c is None else c.get("rank"),
                "p1_score": tr.get("score"),
                "p1_candidate_rank": tr.get("candidate_rank"),
                "rank_quintile": None,
                "limit": tr.get("limit") if tr.get("limit") is not None else (None if c is None else c.get("bid")),
                "board_present": board is not None,
                "fill_latency_ms": None if t0 is None else (fill_t - float(t0)) * 1000.0,
                "first_ask_minus_limit_bps": None,
                "min_ask_minus_limit_bps": None,
                "spread_bps_at_anchor": None,
                "spread_bps_at_fill": None,
                "execution_advantage_bps": None,
                "mid_at_fill": None,
                "bid1_at_fill": None,
                "mid_evaluable_at_fill": False,
                "realized_return": None,
                "executable_mfe": None,
                "executable_mae": None,
                "mid_mfe": None,
                "mid_mae": None,
                "capture_ratio": None,
                "path_leak_n": 0,
                "future_leak": False,
            }
            n_elig = n_at_anchor.get(an) or 0
            rank = rec["harvest_rank"]
            if rank is None:
                rank = tr.get("candidate_rank")
                if rank is not None:
                    try:
                        rank = int(float(rank)) - 1
                    except (TypeError, ValueError):
                        rank = None
            if rank is not None and n_elig > 0:
                try:
                    ri = int(float(rank))
                    if ri < 0:
                        ri = 0
                    q = min(4, int(ri * 5 / n_elig))
                    rec["rank_quintile"] = f"Q{q + 1}"
                    rec["harvest_rank_used"] = ri
                    rec["eligible_n_at_anchor"] = n_elig
                except (TypeError, ValueError):
                    pass

            if fill_px > 0 and exit_px == exit_px:
                rec["realized_return"] = float(exit_px) / fill_px - 1.0

            if board is None or t0 is None:
                rows.append(rec)
                continue

            limit = rec.get("limit")
            if limit is not None:
                wst = wait_ask_stats(board, float(t0), float(limit))
                rec["first_ask_minus_limit_bps"] = wst.get("first_ask_minus_limit_bps")
                rec["min_ask_minus_limit_bps"] = wst.get("min_ask_minus_limit_bps")

            mid_a = mid_checkpoint(board, float(t0))
            leak_fill += int(mid_a.get("leak_n") or 0)
            rec["spread_bps_at_anchor"] = mid_a.get("spread_bps")

            mid_f = mid_checkpoint(board, fill_t)
            leak_fill += int(mid_f.get("leak_n") or 0)
            rec["mid_at_fill"] = mid_f.get("mid")
            rec["bid1_at_fill"] = mid_f.get("bid")
            rec["spread_bps_at_fill"] = mid_f.get("spread_bps")
            rec["mid_evaluable_at_fill"] = bool(mid_f.get("evaluable"))
            if mid_f.get("evaluable") and fill_px > 0:
                rec["execution_advantage_bps"] = (float(mid_f["mid"]) / fill_px - 1.0) * 10000.0

            bid_f = bid1_at_or_before(board, fill_t)
            leak_bid += int(bid_f.get("leak_n") or 0)

            walked = walk_fill_to_exit(board, fill_t, exit_t)
            leak_path += int(walked.get("leak_n") or 0)
            rec["path_leak_n"] = int(walked.get("leak_n") or 0)
            rec["n_path_ticks"] = walked.get("n_path_ticks")
            rec["max_bid1"] = walked.get("max_bid1")
            rec["min_bid1"] = walked.get("min_bid1")
            rec["max_mid"] = walked.get("max_mid")
            rec["min_mid"] = walked.get("min_mid")
            if walked.get("path_evaluable_bid") and fill_px > 0:
                rec["executable_mfe"] = float(walked["max_bid1"]) / fill_px - 1.0
                rec["executable_mae"] = float(walked["min_bid1"]) / fill_px - 1.0
            if walked.get("path_evaluable_mid") and mid_f.get("evaluable") and float(mid_f["mid"]) > 0:
                rec["mid_mfe"] = float(walked["max_mid"]) / float(mid_f["mid"]) - 1.0
                rec["mid_mae"] = float(walked["min_mid"]) / float(mid_f["mid"]) - 1.0
            mfe = rec.get("executable_mfe")
            rr = rec.get("realized_return")
            if mfe is not None and float(mfe) > 0 and rr is not None:
                rec["capture_ratio"] = float(rr) / float(mfe)

            if rec["path_leak_n"] or int(mid_f.get("leak_n") or 0) or int(bid_f.get("leak_n") or 0):
                rec["future_leak"] = True

            for h in HORIZONS_SEC:
                chk = fill_t + float(h)
                st = horizon_status(day, sess, fill_t, int(h))
                rec[f"status_{h}"] = st
                rec[f"mid_{h}"] = None
                rec[f"mid_markout_{h}"] = None
                rec[f"bid1_{h}"] = None
                if st != "OK":
                    continue
                fut = mid_checkpoint(board, chk)
                leak_mid += int(fut.get("leak_n") or 0)
                bb = bid1_at_or_before(board, chk)
                leak_bid += int(bb.get("leak_n") or 0)
                rec[f"bid1_{h}"] = bb.get("bid1")
                if not fut.get("evaluable"):
                    rec[f"status_{h}"] = "NOT_EVALUABLE"
                    continue
                rec[f"mid_{h}"] = fut["mid"]
                if mid_f.get("evaluable") and float(mid_f["mid"]) > 0:
                    rec[f"mid_markout_{h}"] = float(fut["mid"]) / float(mid_f["mid"]) - 1.0

            rows.append(rec)

        return {
            "ok": True,
            "date": day,
            "rows": rows,
            "n_canonical": len(trades),
            "harvest_joined_n": joined,
            "leak_fill": leak_fill,
            "leak_mid": leak_mid,
            "leak_path": leak_path,
            "leak_bid": leak_bid,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
