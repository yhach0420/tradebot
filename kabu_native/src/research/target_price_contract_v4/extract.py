"""V4 diagnostic extract. Persistent-state M4. Fill contract untouched. No C14 write."""
from __future__ import annotations

import gc
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_timing_robustness.grid import hm_epoch, hm_label
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    _boot,
    capture_event_epoch,
    iter_push,
)
from research.e1_x34b_entry_execution.features import preentry_from_board
from research.executable_target_v2_b_threshold.contract import session_of_anchor
from research.executable_target_v2_coverage_audit.session_sot import (
    plus_sec_hm,
    tse_continuous_at_hm,
    tse_phase_hm,
)
from research.fixed_anchor_mechanism_audit_p3_0.engine import P3Engine
from research.target_price_contract_v3.buf import patch_board_buf_for_marks
from research.target_price_contract_v3.extract import session_t_lo
from research.target_price_contract_v3.marks import last_idx, last_m1_quote, pack_mark
from research.target_price_contract_v4 import HORIZON_SEC
from research.target_price_contract_v4.persist import (
    m4_t_lo,
    reversal_cut_times,
    symbol_persist_audit,
)
from research.uniform10_entry_rebuild import UNIFORM10
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_native_entry_live import FEATURE_ORDER


def _num(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = [_bare(s) for s in (payload.get("universe") or [])]
    universe_set = set(universe)
    universe_n = len(universe)
    t_wall = time.perf_counter()
    try:
        patch_board_buf_for_marks()
        eng, _dual = _boot(universe, P3Engine)
        if eng is None or not getattr(eng, "ready", False):
            return {
                "ok": False,
                "date": day,
                "stage": "TARGET_V4",
                "blocker": getattr(eng, "fail_reason", "boot_failed"),
                "elapsed_sec": round(time.perf_counter() - t_wall, 3),
            }
        eng.allowed_hm = ()
        eng.fire_mode = "production"
        eng.notify_enabled = False
        eng.ingest_audit = _Discard()  # type: ignore[assignment]
        events_n = 0
        last_seq: Optional[int] = None
        restart_cuts: list[float] = []
        for rec in iter_push(capture):
            pay = dict(rec.get("payload") or rec.get("original_payload") or {})
            et = capture_event_epoch(rec, pay)
            seq = int(rec.get("sequence") or 0)
            # Process/capture restart: global sequence resets downward.
            # Not a per-symbol quiet interval. Not last_seen+1 skips (other symbols).
            if last_seq is not None and seq > 0 and last_seq > 0 and seq + 1 < last_seq and et is not None:
                restart_cuts.append(float(et))
            if seq > 0:
                last_seq = seq
            if et is None:
                continue
            sym = _bare(rec.get("symbol") or pay.get("Symbol"))
            if universe_set and sym not in universe_set:
                continue
            pay["sequence"] = seq
            pay["__ingress_sequence__"] = seq
            eng.ingest_push(symbol=sym, payload=pay, event_t=float(et))
            events_n += 1

        score_fn = eng.score_fn
        persist_parts: list[dict[str, Any]] = []
        reversal_by_sym: dict[str, list[float]] = {}
        first_event_by_sym: dict[str, Optional[float]] = {}
        for raw_sym in list(eng.universe):
            s = _bare(raw_sym)
            board = eng._board_arrays(s)
            empty = board.get("t") is None or board["t"].size == 0
            first_event_by_sym[s] = None if empty else float(board["t"][0])
            revs = [] if empty else reversal_cut_times(board["t"])
            reversal_by_sym[s] = revs
            if not empty:
                part = symbol_persist_audit(day, s, board)
                part["symbol"] = s
                part["n_reversals"] = len(revs)
                persist_parts.append(part)

        rows: list[dict[str, Any]] = []
        for h, m in UNIFORM10:
            t0 = float(hm_epoch(day, h, m))
            t1 = t0 + float(HORIZON_SEC)
            an = hm_label(h, m)
            sess = session_of_anchor(h)
            h1, m1 = plus_sec_hm(h, m, HORIZON_SEC)
            p0 = tse_phase_hm(h, m)
            p1 = tse_phase_hm(h1, m1)
            t_lo0 = session_t_lo(day, t0, h, m)
            t_lo1 = session_t_lo(day, t1, h1, m1)
            for raw_sym in list(eng.universe):
                s = _bare(raw_sym)
                board = eng._board_arrays(s)
                empty = board.get("t") is None or board["t"].size == 0
                i0 = last_idx(board["t"], t0) if not empty else -1
                revs = reversal_by_sym.get(s) or []
                m4_lo0, cut0 = m4_t_lo(
                    session_lo=t_lo0, reversal_cuts=revs, restart_cuts=restart_cuts, t_at=t0
                )
                m4_lo1, cut1 = m4_t_lo(
                    session_lo=t_lo1, reversal_cuts=revs, restart_cuts=restart_cuts, t_at=t1
                )
                m1_0 = pack_mark(last_m1_quote(board, t0, t_lo=t_lo0))
                m1_1 = pack_mark(last_m1_quote(board, t1, t_lo=t_lo1))
                m4_0 = pack_mark(last_m1_quote(board, t0, t_lo=m4_lo0))
                m4_1 = pack_mark(last_m1_quote(board, t1, t_lo=m4_lo1))
                feats = preentry_from_board(board, t0) if not empty else {f: None for f in FEATURE_ORDER}
                score = None
                if not empty and not any(
                    feats.get(f) is None or not np.isfinite(feats.get(f) if feats.get(f) is not None else np.nan)
                    for f in FEATURE_ORDER
                ):
                    try:
                        sc = float(score_fn(feats))
                    except Exception:
                        sc = float("nan")
                    if np.isfinite(sc):
                        score = sc
                last_bid = last_ask = last_bq = last_aq = last_lag = last_status = None
                last_state = last_ask_sign = last_bid_sign = None
                last_locked = last_exe = last_special = False
                last_spread = None
                if i0 >= 0:
                    last_lag = float(t0) - float(board["t"][i0])
                    last_bid = _num(board["bid"][i0])
                    last_ask = _num(board["ask"][i0])
                    last_bq = _num(board["bid_qty"][i0])
                    last_aq = _num(board["ask_qty"][i0])
                    last_state = (
                        str(board["board_execution_state"][i0] or "")
                        if board.get("board_execution_state") is not None
                        else None
                    )
                    last_exe = bool(board["executable"][i0]) if board.get("executable") is not None else False
                    last_special = bool(board["special"][i0]) if board.get("special") is not None else False
                    if board.get("ask_sign") is not None and i0 < int(board["ask_sign"].size):
                        last_ask_sign = str(board["ask_sign"][i0] or "")
                    if board.get("bid_sign") is not None and i0 < int(board["bid_sign"].size):
                        last_bid_sign = str(board["bid_sign"][i0] or "")
                    if board.get("locked") is not None and i0 < int(board["locked"].size):
                        last_locked = bool(board["locked"][i0])
                    if board.get("last_status") is not None and i0 < int(board["last_status"].size):
                        last_status = _num(board["last_status"][i0])
                    if last_bid is not None and last_ask is not None and last_bid > 0:
                        last_spread = last_ask - last_bid
                price_level = last_bid if last_bid is not None else m4_0.get("px")
                first_t = first_event_by_sym.get(s)
                rows.append(
                    {
                        "date": day,
                        "symbol": s,
                        "anchor": an,
                        "session": sess,
                        "universe_n": universe_n,
                        "t0_phase": p0,
                        "t1_phase": p1,
                        "tse_t0_cont": tse_continuous_at_hm(h, m),
                        "tse_t1_cont": tse_continuous_at_hm(h1, m1),
                        "t1_hm": f"{h1:02d}:{m1:02d}",
                        "empty": bool(empty),
                        "t0_has_event": i0 >= 0,
                        "first_event_t": first_t,
                        "last_lag": last_lag,
                        "last_bid": last_bid,
                        "last_ask": last_ask,
                        "last_bq": last_bq,
                        "last_aq": last_aq,
                        "last_spread": last_spread,
                        "last_state": last_state,
                        "last_ask_sign": last_ask_sign,
                        "last_bid_sign": last_bid_sign,
                        "last_locked": last_locked,
                        "last_exe": last_exe,
                        "last_special": last_special,
                        "last_status": last_status,
                        "m1_t0_px": m1_0.get("px"),
                        "m1_t0_age": m1_0.get("age"),
                        "m1_t1_px": m1_1.get("px"),
                        "m1_t1_age": m1_1.get("age"),
                        "m4_t0_px": m4_0.get("px"),
                        "m4_t0_age": m4_0.get("age"),
                        "m4_t1_px": m4_1.get("px"),
                        "m4_t1_age": m4_1.get("age"),
                        "m4_t_lo0_reason": cut0,
                        "m4_t_lo1_reason": cut1,
                        "score": score,
                        "spread_bps": _num(feats.get("spread_bps")),
                        "imbalance": _num(feats.get("imbalance")),
                        "mid_ret_60s": _num(feats.get("mid_ret_60s")),
                        "mid_ret_180s": _num(feats.get("mid_ret_180s")),
                        "event_rate_60s": _num(feats.get("event_rate_60s")),
                        "log_bid_qty": _num(feats.get("log_bid_qty")),
                        "price_level": _num(price_level),
                    }
                )
        raw_holes = int(getattr(eng, "native_ingest_raw_sequence_holes", 0) or 0)
        uni_holes = int(getattr(eng, "native_ingest_sequence_holes", 0) or 0)
        del eng, _dual
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "stage": "TARGET_V4",
            "events_n": events_n,
            "universe_n": universe_n,
            "rows": rows,
            "persist_parts": persist_parts,
            "n_seq_resets": len(restart_cuts),
            "native_ingest_raw_sequence_holes": raw_holes,
            "native_ingest_sequence_holes": uni_holes,
            "seq_holes_are_not_per_symbol_gaps": True,
            "elapsed_sec": round(time.perf_counter() - t_wall, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "stage": "TARGET_V4",
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t_wall, 3),
        }
