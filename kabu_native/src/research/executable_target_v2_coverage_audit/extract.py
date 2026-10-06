"""Diagnostic extract: V2 PRIMARY unchanged. Extra lookup/feature fields for coverage audit.

No hypo fill/exit. No Dual Lane trading. No C14 write. allowed_hm empty.
"""
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
from research.executable_target_v2_b_threshold.contract import (
    primary_target_row,
    session_of_anchor,
)
from research.executable_target_v2_coverage_audit import HORIZON_SEC
from research.executable_target_v2_coverage_audit.diagnose import (
    first_fail_reason,
    lookup_t0_t1,
    n_events_between,
    n_events_window,
    probe_last,
)
from research.executable_target_v2_coverage_audit.session_sot import (
    plus_sec_hm,
    tse_continuous_at_hm,
    tse_phase_hm,
    v1r_endpoint_in_session,
    v1r_session_of_t0,
)
from research.fixed_anchor_mechanism_audit_p3_0.engine import P3Engine
from research.uniform10_entry_rebuild import UNIFORM10
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_native_entry_live import FEATURE_ORDER, _BoardBuf


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
        eng, _dual = _boot(universe, P3Engine)
        if eng is None or not getattr(eng, "ready", False):
            return {
                "ok": False,
                "date": day,
                "stage": "COVERAGE_AUDIT",
                "blocker": getattr(eng, "fail_reason", "boot_failed"),
                "elapsed_sec": round(time.perf_counter() - t_wall, 3),
            }
        eng.allowed_hm = ()
        eng.fire_mode = "production"
        eng.notify_enabled = False
        eng.ingest_audit = _Discard()  # type: ignore[assignment]
        _BoardBuf.compact_tail = lambda self, keep=None: None  # type: ignore[method-assign, assignment]
        events_n = 0
        for rec in iter_push(capture):
            pay = dict(rec.get("payload") or rec.get("original_payload") or {})
            et = capture_event_epoch(rec, pay)
            if et is None:
                continue
            sym = _bare(rec.get("symbol") or pay.get("Symbol"))
            if universe_set and sym not in universe_set:
                continue
            seq = int(rec.get("sequence") or 0)
            pay["sequence"] = seq
            pay["__ingress_sequence__"] = seq
            eng.ingest_push(symbol=sym, payload=pay, event_t=float(et))
            events_n += 1

        score_fn = eng.score_fn
        rows: list[dict[str, Any]] = []
        for h, m in UNIFORM10:
            t0 = float(hm_epoch(day, h, m))
            t1 = t0 + float(HORIZON_SEC)
            an = hm_label(h, m)
            sess = session_of_anchor(h)
            h1, m1 = plus_sec_hm(h, m, HORIZON_SEC)
            v1r_ep = v1r_endpoint_in_session(h, m, HORIZON_SEC)
            p0 = tse_phase_hm(h, m)
            p1 = tse_phase_hm(h1, m1)
            for sym in list(eng.universe):
                s = _bare(sym)
                board = eng._board_arrays(s)
                tgt = primary_target_row(
                    board, day=day, symbol=s, anchor=an, session=sess, t0=t0
                )
                empty = board.get("t") is None or board["t"].size == 0
                p_t0 = probe_last(board, t0)
                p_t1 = probe_last(board, t1)
                t0m, t1m = lookup_t0_t1(board, t0, t1)
                n600 = n_events_between(board, t0, t1)
                n60 = n_events_window(board, t0 - 60.0, t0)
                primary_valid = bool(tgt.get("MODEL_ROW_ELIGIBLE"))
                ff = first_fail_reason(
                    board_empty=bool(empty),
                    t0=p_t0,
                    t1=p_t1,
                    t0_hm=(h, m),
                    t1_hm=(h1, m1),
                    t0m=t0m,
                    t1m=t1m,
                    v1r_endpoint_in_session=v1r_ep,
                    n_t600_events=n600,
                    primary_valid=primary_valid,
                )
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
                last_bid = p_t0.get("bid")
                t0_mid = None if t0m is None else t0m.get("mid")
                price_level = last_bid if last_bid is not None else t0_mid
                tse_t600_cont = tse_continuous_at_hm(h1, m1)
                t0_mid_ok = t0m is not None
                t1_mid_ok = t1m is not None
                would_tse = bool(t0_mid_ok and t1_mid_ok and tse_t600_cont and tgt.get("executable_at_t0"))
                rows.append(
                    {
                        "date": day,
                        "symbol": s,
                        "anchor": an,
                        "session": sess,
                        "universe_n": universe_n,
                        "t0_phase_tse": p0,
                        "t1_phase_tse": p1,
                        "t1_hm": f"{h1:02d}:{m1:02d}",
                        "v1r_t0_in_session": v1r_session_of_t0(h, m) is not None,
                        "v1r_endpoint_in_session": v1r_ep,
                        "tse_t0_continuous": tse_continuous_at_hm(h, m),
                        "tse_t600_continuous": tse_t600_cont,
                        "executable_at_t0": bool(tgt.get("executable_at_t0")),
                        "t0_price_source": tgt.get("t0_price_source"),
                        "ITAYOSE_BASE": bool(tgt.get("ITAYOSE_BASE")),
                        "NON_EXECUTABLE_T0_IN_PRIMARY": bool(tgt.get("NON_EXECUTABLE_T0_IN_PRIMARY")),
                        "MODEL_ROW_ELIGIBLE": primary_valid,
                        "PRIMARY_TARGET_NULL": bool(tgt.get("PRIMARY_TARGET_NULL")),
                        "v2_null_reason": tgt.get("null_reason"),
                        "first_fail": ff,
                        "t0_has_event": bool(p_t0.get("has_event")),
                        "t0_lag_sec": p_t0.get("lag_sec"),
                        "t0_last_executable": bool(p_t0.get("executable")),
                        "t0_qty_ok": bool(p_t0.get("qty_ok")),
                        "t0_locked": bool(p_t0.get("locked")),
                        "t0_mid_ok": t0_mid_ok,
                        "t0_mid": t0_mid,
                        "t0_mid_lag_sec": None if t0m is None else t0m.get("lag_sec"),
                        "t1_has_event": bool(p_t1.get("has_event")),
                        "t1_lag_sec": p_t1.get("lag_sec"),
                        "t1_last_executable": bool(p_t1.get("executable")),
                        "t1_qty_ok": bool(p_t1.get("qty_ok")),
                        "t1_mid_ok": t1_mid_ok,
                        "t1_mid": None if t1m is None else t1m.get("mid"),
                        "t1_mid_lag_sec": None if t1m is None else t1m.get("lag_sec"),
                        "n_t600_events": n600,
                        "n_event_60s": n60,
                        "would_primary_if_tse_zaraba": would_tse,
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

        del eng, _dual
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "stage": "COVERAGE_AUDIT",
            "events_n": events_n,
            "universe_n": universe_n,
            "rows": rows,
            "elapsed_sec": round(time.perf_counter() - t_wall, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "stage": "COVERAGE_AUDIT",
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t_wall, 3),
        }
