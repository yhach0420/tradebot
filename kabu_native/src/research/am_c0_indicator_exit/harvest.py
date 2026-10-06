"""Sealed Capture → event-state dataset. Parallelism=1. No dual-lane. No live WS."""
from __future__ import annotations

import gc
import json
import os
import sys
import time
from collections import defaultdict
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

from research.am_c0_indicator_exit import ARCHITECTURE_ID, EXIT_FEATURES, POSITION_FEATURES, SYMBOL_FEATURES
from research.am_c0_indicator_exit.features import (
    position_features,
    stack_symbol_matrix,
    symbol_feature_table,
    valid_decision_mask,
    vol_arrays_from_events,
)
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    find_capture_dir,
    iter_push,
    record_event_stamp,
)
from replay.pnl_yen import compute_pnl_yen_100
from research.canonical_entry_performance_rebase.analyze import _f
from run_p0_4_exact_vs_fast_parity import _Discard
from small_paper.v1r_live_dual_lane import session_end_for_position
from small_paper.v1r_native_entry_live import _BoardBuf, boot_v1r_native_entry, extract_board_row, reset_native_entry_for_tests

JST = ZoneInfo("Asia/Tokyo")
CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_c0_indicator_exit"
N_FEAT = len(EXIT_FEATURES)
N_SYM = len(SYMBOL_FEATURES)
N_POS = len(POSITION_FEATURES)


def sealed_day_caps(days: list[str], today: str) -> list[dict[str, Any]]:
    from _p1_inventory import resolve_universe

    out = []
    for day in days:
        if str(day) == str(today):
            raise RuntimeError("ACTIVE_DAY_IN_RESEARCH_INPUT")
        cap = find_capture_dir(str(day))
        uni = resolve_universe(str(day), cap)
        out.append(
            {
                "date": str(day),
                "capture_path": str(cap) if cap is not None else "",
                "universe_symbols": list(uni.get("symbols") or []),
                "ok": cap is not None and bool(uni.get("symbols")),
            }
        )
    return out


def _boot_board_engine(universe: list[str]) -> tuple[Any, Optional[str]]:
    reset_native_entry_for_tests()
    _BoardBuf.compact_tail = lambda self, keep=None: None  # type: ignore[method-assign, assignment]
    eng = boot_v1r_native_entry(universe=list(universe), trace_dir=None, universe_source="historical_capture")
    if eng is None or not getattr(eng, "ready", False):
        return None, getattr(eng, "fail_reason", "boot_failed") if eng is not None else "boot_failed"
    eng.notify_enabled = False
    eng.ingest_audit = _Discard()  # type: ignore[assignment]
    return eng, None


def _stream_boards(day: str, capture: Path, eng: Any) -> tuple[int, Optional[float], dict[str, list]]:
    vol_events: dict[str, list[tuple[float, Optional[float], Optional[float]]]] = defaultdict(list)
    events_n = 0
    last_et: Optional[float] = None
    last_seq: Optional[int] = None
    for rec in iter_push(capture):
        recv = record_event_stamp(rec)
        seq = int(rec.get("sequence") or 0)
        if seq > 0:
            last_seq = seq
        sym = _bare(rec.get("symbol"))
        pay = dict(rec.get("payload") or rec.get("original_payload") or {})
        et = capture_event_epoch(rec, pay)
        if et is None:
            continue
        recv = recv or datetime.fromtimestamp(float(et), JST).isoformat(timespec="milliseconds")
        pay["received_at"] = recv
        pay["recorded_at"] = recv
        pay["sequence"] = seq
        pay["__ingress_sequence__"] = seq
        pay["__ingress_received_at__"] = recv
        last_et = float(et)
        events_n += 1
        eng.ingest_push(symbol=sym, payload=pay, event_t=float(et))
        brow = extract_board_row(pay, float(et))
        tv = _f(brow.get("TradingVolume"))
        bid = _f(brow.get("bid"))
        ask = _f(brow.get("ask"))
        mid = (bid + ask) / 2.0 if bid is not None and ask is not None and bid > 0 and ask > bid else None
        vol_events[str(sym)].append((float(et), tv, mid))
        if events_n % 200000 == 0:
            if getattr(eng, "events", None) is not None:
                eng.events.clear()
            if getattr(eng, "ingest_audit", None) is not None:
                try:
                    eng.ingest_audit.clear()
                except Exception:
                    pass
    _ = last_seq
    return events_n, last_et, vol_events


def _regime_matrix(
    tables: dict[str, dict[str, np.ndarray]],
    mats: dict[str, np.ndarray],
    query_t: np.ndarray,
) -> np.ndarray:
    nq = int(query_t.size)
    n_reg = N_FEAT - N_SYM - N_POS
    if nq == 0:
        return np.zeros((0, n_reg), dtype=float)
    stacked = []
    for s, tab in tables.items():
        tt = tab.get("t")
        if tt is None or tt.size == 0:
            continue
        idx = np.searchsorted(tt, query_t, side="right") - 1
        row = np.full((nq, N_SYM), np.nan, dtype=float)
        ok = idx >= 0
        if np.any(ok):
            row[ok] = mats[s][idx[ok]]
        stacked.append(row)
    out = np.full((nq, n_reg), np.nan, dtype=float)
    if not stacked:
        return out
    cube = np.stack(stacked, axis=1)  # nq, n_sym, 12
    idx = {k: i for i, k in enumerate(SYMBOL_FEATURES)}

    def med(col: int) -> np.ndarray:
        v = cube[:, :, col]
        with np.errstate(all="ignore"):
            return np.nanmedian(v, axis=1)

    def iqr(col: int) -> np.ndarray:
        v = cube[:, :, col]
        with np.errstate(all="ignore"):
            q75 = np.nanpercentile(v, 75, axis=1)
            q25 = np.nanpercentile(v, 25, axis=1)
        return q75 - q25

    def breadth(col: int) -> np.ndarray:
        v = cube[:, :, col]
        good = np.isfinite(v)
        cnt = np.sum(good, axis=1)
        pos = np.sum((v > 0.0) & good, axis=1)
        return np.where(cnt > 0, pos / np.maximum(cnt, 1), np.nan)

    out[:, 0] = med(idx["mid_ret_60s"])
    out[:, 1] = med(idx["mid_ret_180s"])
    out[:, 2] = breadth(idx["mid_ret_60s"])
    out[:, 3] = breadth(idx["mid_ret_180s"])
    out[:, 4] = iqr(idx["mid_ret_60s"])
    out[:, 5] = iqr(idx["mid_ret_180s"])
    out[:, 6] = med(idx["spread_bps"])
    out[:, 7] = med(idx["imbalance"])
    out[:, 8] = med(idx["event_rate_60s"])
    out[:, 9] = med(idx["volume_rate_60s"])
    out[:, 10] = med(idx["volume_percentile_60s"])
    out[:, 11] = med(idx["trading_value_percentile_180s"])
    out[:, 12] = med(idx["distance_from_vwap_bps"])
    out[:, 13] = med(idx["rebound_from_recent_low_bps"])
    return out


def process_state_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    fills = list(payload.get("fills") or [])
    t0w = time.perf_counter()
    leak = {
        "ITAYOSE_SKIP_N": 0,
        "SPECIAL_SKIP_N": 0,
        "INVALID_SKIP_N": 0,
        "FUTURE_FEATURE_USE_N": 0,
        "C14_REPLAY_MISMATCH_N": 0,
    }
    try:
        eng, blocker = _boot_board_engine(universe)
        if eng is None:
            return {"ok": False, "date": day, "blocker": blocker or "boot_failed"}
        events_n, last_et, vol_events = _stream_boards(day, capture, eng)
        t_lo = float(hm_epoch(day, 9, 0))
        tables: dict[str, dict[str, np.ndarray]] = {}
        mats: dict[str, np.ndarray] = {}
        boards: dict[str, dict] = {}
        for raw in list(getattr(eng, "universe", []) or universe):
            s = _bare(raw)
            board = eng._board_arrays(s)
            boards[s] = board
            vol = vol_arrays_from_events(vol_events.get(s) or [])
            tab = symbol_feature_table(board, vol, t_lo=t_lo)
            tables[s] = tab
            mats[s] = stack_symbol_matrix(tab)
        prepared: list[dict[str, Any]] = []
        all_td = []
        for c in fills:
            s = _bare(c.get("symbol"))
            board = boards.get(s) or {}
            rk = str(c.get("row_key") or "")
            rec: dict[str, Any] = {
                "date": day,
                "anchor": c.get("anchor"),
                "symbol": s,
                "session": c.get("session") or "AM",
                "fill_t": c.get("fill_t"),
                "fill_price": c.get("fill_price"),
                "c14_pnl_yen_100": c.get("pnl_yen_100"),
                "c14_exit_t": c.get("exit_t"),
                "c14_exit_price": c.get("exit_price"),
                "c14_exit_reason": c.get("exit_reason"),
                "role": c.get("role"),
                "arm": c.get("arm"),
                "row_key": rk,
            }
            ft = _f(c.get("fill_t"))
            fp = _f(c.get("fill_price"))
            c14_pnl = _f(c.get("pnl_yen_100"))
            if board.get("t") is None or board["t"].size == 0 or ft is None or fp is None or c14_pnl is None:
                rec["ok"] = False
                rec["blocker"] = "MISSING_BOARD_OR_FILL"
                prepared.append({"rec": rec, "idx": None})
                continue
            sess = str(c.get("session") or "AM")
            sess_end = float(session_end_for_position(date=day, session=sess, fill_time=float(ft)))
            mask, skip = valid_decision_mask(board)
            for k, v in skip.items():
                leak[k] = int(leak.get(k) or 0) + int(v)
            t = np.asarray(board["t"], dtype=float)
            bid = np.asarray(board["bid"], dtype=float)
            after = (t > float(ft) + 1e-12) & (t <= float(sess_end) + 1e-12) & mask
            idx = np.flatnonzero(after)
            rec["sess_end"] = sess_end
            rec["fill_px"] = float(fp)
            rec["c14_pnl"] = float(c14_pnl)
            if tables.get(s) is None or idx.size == 0:
                rec["ok"] = True
                rec["n_states"] = 0
                rec["blocker"] = None
                prepared.append({"rec": rec, "idx": None, "symbol": s})
                continue
            t_d = t[idx]
            all_td.append(t_d)
            prepared.append(
                {
                    "rec": rec,
                    "idx": idx,
                    "symbol": s,
                    "t": t,
                    "bid": bid,
                    "t_d": t_d,
                    "bid_d": bid[idx],
                    "fp": float(fp),
                    "c14_pnl": float(c14_pnl),
                }
            )
        uniq_t = np.unique(np.concatenate(all_td)) if all_td else np.asarray([], dtype=float)
        print(f"{day} regime unique_t={uniq_t.size} fills={len(fills)}", flush=True)
        n_reg = N_FEAT - N_SYM - N_POS
        if uniq_t.size == 0:
            X_reg_u = np.zeros((0, n_reg), dtype=float)
        else:
            parts = []
            step = 4000
            for i0 in range(0, int(uniq_t.size), step):
                parts.append(_regime_matrix(tables, mats, uniq_t[i0 : i0 + step]))
            X_reg_u = np.vstack(parts)
        trades = []
        for item in prepared:
            rec = item["rec"]
            if item.get("idx") is None:
                trades.append(rec)
                continue
            s = item["symbol"]
            idx = item["idx"]
            t_d = item["t_d"]
            bid_d = item["bid_d"]
            fp = item["fp"]
            c14_pnl = item["c14_pnl"]
            X_sym = mats[s][idx]
            loc = np.searchsorted(uniq_t, t_d)
            loc = np.clip(loc, 0, max(int(uniq_t.size) - 1, 0))
            X_reg = X_reg_u[loc] if uniq_t.size else np.zeros((t_d.size, N_FEAT - N_SYM - N_POS))
            unreal = np.where(np.isfinite(bid_d) & (fp > 0), (bid_d / fp - 1.0) * 10000.0, np.nan)
            X_pos = position_features(unreal)
            X = np.column_stack([X_sym, X_reg, X_pos])
            exit_now = np.array(
                [float(compute_pnl_yen_100(fp, float(px))) if px == px else np.nan for px in bid_d],
                dtype=float,
            )
            y = (exit_now > float(c14_pnl)).astype(np.float64)
            y[~np.isfinite(exit_now)] = 0.0
            complete = np.all(np.isfinite(X), axis=1)
            n_valid = int(idx.size)
            w = np.full(n_valid, 1.0 / float(n_valid), dtype=float)
            rec.update(
                {
                    "ok": True,
                    "blocker": None,
                    "n_states": n_valid,
                    "n_complete": int(np.sum(complete)),
                    "t": t_d,
                    "bid": bid_d,
                    "X": X,
                    "y": y,
                    "exit_now_pnl": exit_now,
                    "complete": complete,
                    "weight": w,
                }
            )
            trades.append(rec)
        fail_n = sum(1 for r in trades if not r.get("ok"))
        print(
            f"{day} STATE fills={len(fills)} ok={len(trades) - fail_n} events={events_n} "
            f"states={sum(int(r.get('n_states') or 0) for r in trades)} last_et={last_et}",
            flush=True,
        )
        del eng
        gc.collect()
        return {
            "ok": fail_n == 0,
            "date": day,
            "trades": trades,
            "fail_n": fail_n,
            "leak": leak,
            "events_n": events_n,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
            "blocker": None if fail_n == 0 else "STATE_HARVEST_FAIL",
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }


def save_day_cache(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    trades = list(body.get("trades") or [])
    meta_trades = []
    arrays: dict[str, Any] = {}
    for i, tr in enumerate(trades):
        key = f"t{i}"
        slim = {k: v for k, v in tr.items() if k not in ("t", "bid", "X", "y", "exit_now_pnl", "complete", "weight")}
        meta_trades.append(slim)
        if tr.get("ok") and tr.get("t") is not None:
            arrays[f"{key}_t"] = np.asarray(tr["t"], dtype=float)
            arrays[f"{key}_bid"] = np.asarray(tr["bid"], dtype=float)
            arrays[f"{key}_X"] = np.asarray(tr["X"], dtype=float)
            arrays[f"{key}_y"] = np.asarray(tr["y"], dtype=float)
            arrays[f"{key}_pnl"] = np.asarray(tr["exit_now_pnl"], dtype=float)
            arrays[f"{key}_c"] = np.asarray(tr["complete"], dtype=bool)
            arrays[f"{key}_w"] = np.asarray(tr["weight"], dtype=float)
    meta = {
        "ok": body.get("ok"),
        "date": body.get("date"),
        "architecture_id": ARCHITECTURE_ID,
        "fail_n": body.get("fail_n"),
        "leak": body.get("leak"),
        "events_n": body.get("events_n"),
        "elapsed_sec": body.get("elapsed_sec"),
        "n_trades": len(meta_trades),
        "trades": meta_trades,
        "feature_names": list(EXIT_FEATURES),
    }
    np.savez_compressed(path.with_suffix(".npz"), **arrays)
    path.write_text(json.dumps(meta, default=str), encoding="utf-8")


def load_day_cache(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    meta = json.loads(path.read_text(encoding="utf-8"))
    npz_p = path.with_suffix(".npz")
    if not npz_p.is_file():
        return {}
    if str(meta.get("architecture_id") or "") != ARCHITECTURE_ID:
        return {}
    blob = np.load(npz_p, allow_pickle=False)
    trades = []
    for i, slim in enumerate(list(meta.get("trades") or [])):
        rec = dict(slim)
        key = f"t{i}"
        t_k = f"{key}_t"
        if t_k in blob:
            rec["t"] = blob[t_k]
            rec["bid"] = blob[f"{key}_bid"]
            rec["X"] = blob[f"{key}_X"]
            rec["y"] = blob[f"{key}_y"]
            rec["exit_now_pnl"] = blob[f"{key}_pnl"]
            rec["complete"] = blob[f"{key}_c"]
            rec["weight"] = blob[f"{key}_w"]
        trades.append(rec)
    return {
        "ok": bool(meta.get("ok")),
        "date": meta.get("date"),
        "trades": trades,
        "fail_n": meta.get("fail_n"),
        "leak": meta.get("leak") or {},
        "events_n": meta.get("events_n"),
        "elapsed_sec": meta.get("elapsed_sec"),
        "architecture_id": meta.get("architecture_id"),
    }
