"""Ingest-only extract: executable target V2 + current-ENTRY scores + hypo fill/exit.

No Dual Lane trading. No C14 write. allowed_hm empty so Runtime CLOCK never fires.
"""
from __future__ import annotations

import gc
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

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
from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
from research.e1_x34b_entry_execution.features import preentry_from_board
from research.e1_x35_passive_exit.paths import build_path, path_metrics
from research.executable_target_v2_b_threshold import WAIT_SEC
from research.executable_target_v2_b_threshold import PRIMARY_TARGET
from research.executable_target_v2_b_threshold.contract import (
    primary_target_row,
    session_of_anchor,
)
from research.fixed_anchor_mechanism_audit_p3_0.diagnostic import (
    _with_mid,
    last_valid_bid_at_or_before,
)
from research.fixed_anchor_mechanism_audit_p3_0.engine import P3Engine
from research.uniform10_entry_rebuild import UNIFORM10
from research.v1r_exit_v2_asymmetric.states import build_trade_bundle
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_exit_v2_contract import apply_arch_e_to_bundle
from small_paper.v1r_live_dual_lane import session_end_for_position
from small_paper.v1r_native_entry_live import FEATURE_ORDER, _BoardBuf
from small_paper.v1r_primary_runtime import LOT_QTY, WAIT_SEC as LIVE_WAIT

JST = ZoneInfo("Asia/Tokyo")


def _iso(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), JST).isoformat(timespec="milliseconds")
    except (OSError, OverflowError, ValueError, TypeError):
        return None


def hypo_fill_exit(
    board: dict[str, np.ndarray],
    *,
    day: str,
    symbol: str,
    session: str,
    t0: float,
    limit_price: float,
) -> dict[str, Any]:
    """Independent fill+Arch E. Occupancy applied later. Fill window = WAIT_SEC vs session end."""
    out: dict[str, Any] = {
        "filled": False,
        "fill_time": None,
        "fill_price": None,
        "exit_time": None,
        "exit_price": None,
        "exit_reason": None,
        "pnl_yen_100": None,
        "mfe_bps_raw": None,
        "mfe_bps_floor0": None,
        "mae_bps": None,
        "mfe_yen_100_raw": None,
        "mfe_yen_100_floor0": None,
        "mae_yen_100": None,
        "canonical_exit_ret_bps": None,
    }
    lim = float(limit_price)
    if not np.isfinite(lim) or lim <= 0:
        return out
    sess_end = float(session_end_for_position(date=day, session=session, fill_time=float(t0)))
    fill = find_ask_cross_fill(
        board,
        t0=float(t0),
        wait_sec=float(WAIT_SEC or LIVE_WAIT),
        limit_price=lim,
        sess_end=sess_end,
        require_executable_continuous=True,
    )
    if not fill.get("filled"):
        return out
    fill_t = float(fill["fill_t"])
    fill_px = float(fill.get("fill_price") or lim)
    out["filled"] = True
    out["fill_time"] = fill_t
    out["fill_price"] = fill_px
    board2 = _with_mid(board)
    path = build_path(board2, entry_price=fill_px, entry_t=fill_t, sess_end=sess_end)
    reason = "SESSION_CLOSE"
    exit_t: Optional[float] = None
    exit_px: Optional[float] = None
    ret_bps: Optional[float] = None
    if path.get("ok"):
        bundle = build_trade_bundle(
            {
                "date": day,
                "symbol": symbol,
                "session": session,
                "fill_time": fill_t,
                "fill_price": fill_px,
                "anchor_id": "HYPO_OCCUPANCY",
            },
            path,
            board2,
        )
        pol = apply_arch_e_to_bundle(bundle)
        if pol.get("ok"):
            reason = str(pol.get("reason") or "ARCH_E")
            exit_t = float(pol["exit_time"])
            ret_bps = float(pol.get("exit_ret_bps") or 0.0)
            exit_px = fill_px * (1.0 + ret_bps / 10000.0)
            if exit_t > sess_end + 1e-12:
                reason = "SESSION_CLOSE"
                exit_t = None
                exit_px = None
                ret_bps = None
    if exit_t is None or exit_px is None:
        found = last_valid_bid_at_or_before(board, until_t=sess_end, after_t=fill_t)
        if found is None:
            found = last_valid_bid_at_or_before(board, until_t=sess_end, after_t=None)
        if found is None:
            exit_t = fill_t
            exit_px = fill_px
            reason = "SESSION_CLOSE"
        else:
            exit_t, exit_px = found
            reason = "SESSION_CLOSE"
        ret_bps = (float(exit_px) / fill_px - 1.0) * 10000.0
    out["exit_time"] = float(exit_t)
    out["exit_price"] = float(exit_px)
    out["exit_reason"] = reason
    out["canonical_exit_ret_bps"] = float(ret_bps) if ret_bps is not None else None
    out["pnl_yen_100"] = round((float(exit_px) - fill_px) * 100.0, 4)
    path_mfe = build_path(board2, entry_price=fill_px, entry_t=fill_t, sess_end=float(exit_t))
    met = path_metrics(path_mfe)
    if met.get("ok"):
        mfe_raw = float(met["mfe"])
        mae = float(met["mae"])
        mfe0 = max(0.0, mfe_raw)
        scale = float(fill_px) / 10000.0 * float(LOT_QTY)
        out["mfe_bps_raw"] = mfe_raw
        out["mfe_bps_floor0"] = mfe0
        out["mae_bps"] = mae
        out["mfe_yen_100_raw"] = round(mfe_raw * scale, 4)
        out["mfe_yen_100_floor0"] = round(mfe0 * scale, 4)
        out["mae_yen_100"] = round(mae * scale, 4)
    return out


def _coverage_row(tgt: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": tgt["date"],
        "anchor": tgt["anchor"],
        "session": tgt["session"],
        "executable_at_t0": bool(tgt.get("executable_at_t0")),
        "MODEL_ROW_ELIGIBLE": bool(tgt.get("MODEL_ROW_ELIGIBLE")),
        "PRIMARY_TARGET_NULL": bool(tgt.get("PRIMARY_TARGET_NULL")),
        "null_reason": tgt.get("null_reason"),
        "t0_price_source": tgt.get("t0_price_source"),
        "ITAYOSE_BASE": bool(tgt.get("ITAYOSE_BASE")),
        "NON_EXECUTABLE_T0_IN_PRIMARY": bool(tgt.get("NON_EXECUTABLE_T0_IN_PRIMARY")),
        "is_15_20": bool(tgt.get("is_15_20")),
        "status_15_20": tgt.get("status_15_20"),
        "endpoint_in_session": bool(tgt.get("endpoint_in_session")),
        "has_target": tgt.get(PRIMARY_TARGET) is not None,
        "has_session_close": tgt.get("SESSION_CLOSE_RETURN") is not None,
    }


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = [_bare(s) for s in (payload.get("universe") or [])]
    universe_set = set(universe)
    t_wall = time.perf_counter()
    try:
        eng, _dual = _boot(universe, P3Engine)
        if eng is None or not getattr(eng, "ready", False):
            return {
                "ok": False,
                "date": day,
                "stage": "EXTRACT_KEEPALL",
                "blocker": getattr(eng, "fail_reason", "boot_failed"),
                "elapsed_sec": round(time.perf_counter() - t_wall, 3),
            }
        eng.allowed_hm = ()
        eng.fire_mode = "production"
        eng.notify_enabled = False
        eng.ingest_audit = _Discard()  # type: ignore[assignment]
        # Keep full-day board history. Live compact_tail(20000) would drop morning
        # ticks before a post-hoc UNIFORM10 extract, so 09:05 scores/fills vanish.
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

        coverage: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        itayose_primary = 0
        nonexe_primary = 0
        primary_n = 0
        target_null_n = 0
        rows_total = 0
        exe_t0_n = 0
        n_1520_valid = 0
        n_1520 = 0
        score_fn = eng.score_fn

        for h, m in UNIFORM10:
            t0 = hm_epoch(day, h, m)
            an = hm_label(h, m)
            sess = session_of_anchor(h)
            for sym in list(eng.universe):
                s = _bare(sym)
                board = eng._board_arrays(s)
                rows_total += 1
                tgt = primary_target_row(
                    board, day=day, symbol=s, anchor=an, session=sess, t0=float(t0)
                )
                coverage.append(_coverage_row(tgt))
                if tgt.get("executable_at_t0"):
                    exe_t0_n += 1
                if tgt.get("MODEL_ROW_ELIGIBLE"):
                    primary_n += 1
                    if tgt.get("ITAYOSE_BASE"):
                        itayose_primary += 1
                    if tgt.get("NON_EXECUTABLE_T0_IN_PRIMARY"):
                        nonexe_primary += 1
                if tgt.get("PRIMARY_TARGET_NULL"):
                    target_null_n += 1
                if tgt.get("is_15_20"):
                    n_1520 += 1
                    if tgt.get(PRIMARY_TARGET) is not None:
                        n_1520_valid += 1

                score = None
                limit = None
                eligible = False
                if board["t"].size:
                    i = int(np.searchsorted(board["t"], t0, side="right") - 1)
                    if i >= 0:
                        feats = preentry_from_board(board, t0)
                        if not any(
                            feats.get(f) is None or not np.isfinite(feats.get(f)) for f in FEATURE_ORDER
                        ):
                            try:
                                sc = float(score_fn(feats))
                            except Exception:
                                sc = float("nan")
                            if np.isfinite(sc):
                                lim = float(board["bid"][i])
                                if np.isfinite(lim) and lim > 0:
                                    score = sc
                                    limit = lim
                                    eligible = True
                if not eligible:
                    continue
                hypo = hypo_fill_exit(
                    board, day=day, symbol=s, session=sess, t0=float(t0), limit_price=float(limit)
                )
                candidates.append(
                    {
                        "date": day,
                        "symbol": s,
                        "anchor": an,
                        "session": sess,
                        "signal_time": float(t0),
                        "score": float(score),
                        "limit_price": float(limit),
                        "eligible": True,
                        "MODEL_ROW_ELIGIBLE": bool(tgt.get("MODEL_ROW_ELIGIBLE")),
                        PRIMARY_TARGET: tgt.get(PRIMARY_TARGET),
                        **hypo,
                    }
                )

        summary = {
            "date": day,
            "rows_total": rows_total,
            "rows_executable_t0": exe_t0_n,
            "rows_primary_target_valid": primary_n,
            "rows_target_null": target_null_n,
            "ITAYOSE_BASE_ROW_N": itayose_primary,
            "NON_EXECUTABLE_T0_ROW_N": nonexe_primary,
            "n_15_20": n_1520,
            "n_15_20_primary_valid": n_1520_valid,
            "candidate_n": len(candidates),
            "hypo_fill_n": sum(1 for c in candidates if c.get("filled")),
        }
        del eng, _dual
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "stage": "EXTRACT_KEEPALL",
            "events_n": events_n,
            "summary": summary,
            "coverage": coverage,
            "candidates": candidates,
            "elapsed_sec": round(time.perf_counter() - t_wall, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "stage": "EXTRACT_KEEPALL",
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t_wall, 3),
        }
