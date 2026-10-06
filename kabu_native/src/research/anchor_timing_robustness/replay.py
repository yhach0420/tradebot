"""One-day causal replay: REFERENCE stream + local scoring + shifted full-grid portfolios."""
from __future__ import annotations

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

from research.anchor_timing_robustness import LOT_QTY, SHIFT_KEYS, SHIFTS_MIN
from research.anchor_timing_robustness.engine import RobustEngine
from research.anchor_timing_robustness.grid import (
    canonical_grid,
    hm_epoch,
    hm_label,
    regular_clock_grid,
    session_of_epoch,
    shifted_epoch,
    tod_bucket,
    valid_shift_map,
)
from research.anchor_timing_robustness.metrics import overlap_at_k, spearman
from research.anchor_vs_event_driven.run_comparison import _boot, _stream_day, extract_trades
from research.e1_x35_passive_exit.paths import build_path, path_metrics
from research.fixed_anchor_mechanism_audit_p3_0.diagnostic import (
    _with_mid,
    independent_diagnostic_outcome,
    score_universe_at,
)
from research.fixed_anchor_mechanism_audit_p3_0.replay import _Discard, _pack_trades
from run_p0_3_exact_runtime_replay_20260820 import _iso, _ledger_sha, _maxdd, _pf, _sess_stats
from small_paper.v1r_live_dual_lane import canonical_symbol_key, session_end_for_position


def _pop_webhooks() -> None:
    for k in (
        "KABU_V1R_ENTRY_WEBHOOK_URL",
        "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
        "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL",
        "KABU_DISCORD_RESEARCH_WEBHOOK_URL",
        "KABU_SHADOW_DISCORD_WEBHOOK_URL",
        "KABU_MARKET_CAPTURE_WEBHOOK_URL",
        "KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL",
    ):
        os.environ.pop(k, None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"


def _mfe_mae_yen(eng: Any, *, symbol: str, fill_t: float, fill_px: float, exit_t: Optional[float], day: str, session: str) -> dict[str, Any]:
    out = {"mfe_bps": None, "mae_bps": None, "mfe_yen_100": None, "mae_yen_100": None}
    if fill_px is None or not np.isfinite(float(fill_px)) or float(fill_px) <= 0:
        return out
    board = eng._board_arrays(canonical_symbol_key(symbol))
    if board["t"].size == 0:
        return out
    end = float(exit_t) if exit_t is not None else float(session_end_for_position(date=day, session=session, fill_time=fill_t))
    path = build_path(_with_mid(board), entry_price=float(fill_px), entry_t=float(fill_t), sess_end=end)
    pm = path_metrics(path)
    if not pm.get("ok"):
        return out
    mfe_bps = float(pm["mfe"])
    mae_bps = float(pm["mae"])
    scale = float(fill_px) / 10000.0 * float(LOT_QTY)
    out["mfe_bps"] = mfe_bps
    out["mae_bps"] = mae_bps
    out["mfe_yen_100"] = round(mfe_bps * scale, 4)
    out["mae_yen_100"] = round(mae_bps * scale, 4)
    return out


def _attach_path_stats(eng: Any, trades: list[dict[str, Any]], *, day: str) -> None:
    for t in trades:
        extra = _mfe_mae_yen(
            eng,
            symbol=str(t.get("symbol") or ""),
            fill_t=float(t.get("fill_time") or 0.0),
            fill_px=float(t.get("fill_price") or 0.0),
            exit_t=t.get("exit_time"),
            day=day,
            session=str(t.get("session") or "AM"),
        )
        t.update(extra)


def _ranked(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ev = [r for r in rows if r.get("feature_evaluable") and r.get("rank") is not None]
    ev.sort(key=lambda r: (int(r["rank"]), str(r.get("symbol") or "")))
    return ev


def _selected(rows: list[dict[str, Any]]) -> list[str]:
    return [
        str(r["symbol"])
        for r in sorted(rows, key=lambda x: (int(x.get("rank") if x.get("rank") is not None else 10**9), str(x.get("symbol") or "")))
        if r.get("selected")
    ]


def _anchor_book_snap(board: dict[str, np.ndarray], t0: float) -> dict[str, Any]:
    """Diagnostic book/path at canonical t0. Not used by ENTRY/EXIT rules."""
    out = {
        "bid_at_anchor": None,
        "ask_at_anchor": None,
        "spread_bps": None,
        "imbalance": None,
        "ret_1m_bps": None,
        "ret_5m_bps": None,
    }
    t = np.asarray(board.get("t"), dtype=float)
    if t.size == 0:
        return out
    i = int(np.searchsorted(t, float(t0), side="right") - 1)
    j = i
    while j >= 0:
        if board["special"][j]:
            j -= 1
            continue
        bid = float(board["bid"][j])
        ask = float(board["ask"][j])
        bq = float(board["bid_qty"][j]) if np.isfinite(board["bid_qty"][j]) else 0.0
        aq = float(board["ask_qty"][j]) if np.isfinite(board["ask_qty"][j]) else 0.0
        if not (np.isfinite(bid) and np.isfinite(ask) and bid > 0 and ask > 0):
            j -= 1
            continue
        mid0 = 0.5 * (bid + ask)
        out["bid_at_anchor"] = bid
        out["ask_at_anchor"] = ask
        out["spread_bps"] = (ask - bid) / mid0 * 10000.0 if mid0 > 0 else None
        denom = bq + aq
        out["imbalance"] = (bq - aq) / denom if denom > 0 else None
        break

    bid_a = np.asarray(board["bid"], dtype=float)
    ask_a = np.asarray(board["ask"], dtype=float)
    spec = np.asarray(board["special"], dtype=bool)
    mid = np.where(
        (~spec) & np.isfinite(bid_a) & np.isfinite(ask_a) & (bid_a > 0) & (ask_a > 0),
        0.5 * (bid_a + ask_a),
        np.nan,
    )

    def _fwd(sec: float) -> Optional[float]:
        if out["bid_at_anchor"] is None or out["ask_at_anchor"] is None:
            return None
        m0 = 0.5 * (float(out["bid_at_anchor"]) + float(out["ask_at_anchor"]))
        if not (np.isfinite(m0) and m0 > 0):
            return None
        target = float(t0) + float(sec)
        k = int(np.searchsorted(t, target, side="left"))
        m1 = np.nan
        for kk in range(k, t.size):
            if np.isfinite(mid[kk]):
                m1 = float(mid[kk])
                break
        if not (np.isfinite(m1) and m1 > 0):
            return None
        return float((m1 / m0 - 1.0) * 10000.0)

    out["ret_1m_bps"] = _fwd(60.0)
    out["ret_5m_bps"] = _fwd(300.0)
    return out


def _isolated_trades(eng: Any, *, day: str, session: str, anchor: str, shift_min: int, t0: float, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for rec in rows:
        if not rec.get("selected") or not rec.get("feature_evaluable"):
            continue
        sym = str(rec["symbol"])
        limit = rec.get("limit")
        if limit is None:
            continue
        board = eng._board_arrays(sym)
        diag = independent_diagnostic_outcome(
            board,
            date=day,
            symbol=sym,
            session=session,
            t0=float(t0),
            limit_price=float(limit),
        )
        row = {
            "date": day,
            "session": session,
            "anchor": anchor,
            "shift_min": int(shift_min),
            "shift_key": SHIFT_KEYS[int(shift_min)],
            "t0": float(t0),
            "symbol": sym,
            "rank": rec.get("rank"),
            "score": rec.get("score"),
            "alloc_score": rec.get("alloc_score"),
            "limit": float(limit),
            "independent_filled": bool(diag.get("independent_filled")),
            "fill_time": diag.get("independent_fill_time"),
            "fill_price": diag.get("independent_fill_price"),
            "exit_time": diag.get("independent_exit_time"),
            "exit_price": diag.get("independent_exit_price"),
            "exit_reason": diag.get("independent_exit_reason"),
            "pnl_yen_100": diag.get("independent_pnl"),
            "fill_reason": diag.get("fill_reason"),
            "mfe_bps": None,
            "mae_bps": None,
            "mfe_yen_100": None,
            "mae_yen_100": None,
        }
        row.update(_anchor_book_snap(board, float(t0)))
        if row["independent_filled"] and row["fill_time"] is not None and row["fill_price"]:
            extra = _mfe_mae_yen(
                eng,
                symbol=sym,
                fill_t=float(row["fill_time"]),
                fill_px=float(row["fill_price"]),
                exit_t=row.get("exit_time"),
                day=day,
                session=session,
            )
            row.update(extra)
        trades.append(row)
    return trades


def _compare_pair(ref_rows: list[dict[str, Any]], sh_rows: list[dict[str, Any]]) -> dict[str, Any]:
    ref_r = _ranked(ref_rows)
    sh_r = _ranked(sh_rows)
    ref_by = {str(r["symbol"]): r for r in ref_r}
    sh_by = {str(r["symbol"]): r for r in sh_r}
    common = sorted(set(ref_by) & set(sh_by))
    sp = spearman(
        [float(ref_by[s]["rank"]) for s in common],
        [float(sh_by[s]["rank"]) for s in common],
    ) if common else None
    ref_syms = [str(r["symbol"]) for r in ref_r]
    sh_syms = [str(r["symbol"]) for r in sh_r]
    ov: dict[str, Any] = {}
    for k in (3, 5, 10):
        ov.update(overlap_at_k(ref_syms, sh_syms, k))
    sel_ref = _selected(ref_rows)
    sel_sh = _selected(sh_rows)
    inter = set(sel_ref) & set(sel_sh)
    union = set(sel_ref) | set(sel_sh)
    entry_j = (len(inter) / float(len(union))) if union else None
    repl = (1.0 - (len(inter) / float(len(sel_ref)))) if sel_ref else None
    ref_at_sh = []
    for s in sel_ref:
        ref_at_sh.append(
            {
                "symbol": s,
                "rank_reference": ref_by.get(s, {}).get("rank"),
                "rank_shift": sh_by.get(s, {}).get("rank"),
            }
        )
    sh_at_ref = []
    for s in sel_sh:
        sh_at_ref.append(
            {
                "symbol": s,
                "rank_shift": sh_by.get(s, {}).get("rank"),
                "rank_reference": ref_by.get(s, {}).get("rank"),
            }
        )
    return {
        "common_n": len(common),
        "spearman": sp,
        "eligible_ref": len(ref_r),
        "eligible_shift": len(sh_r),
        "selected_ref": sel_ref,
        "selected_shift": sel_sh,
        "entry_jaccard": entry_j,
        "entry_overlap_of_ref": (len(inter) / float(len(sel_ref))) if sel_ref else None,
        "entry_replacement_rate": repl,
        "candidate_count_ref": len(ref_r),
        "candidate_count_shift": len(sh_r),
        "ref_entry_rank_at_shift": ref_at_sh,
        "shift_entry_rank_at_ref": sh_at_ref,
        **{k: v for k, v in ov.items() if not str(k).endswith("_a") and not str(k).endswith("_b") and not str(k).endswith("_inter")},
        "top5_ref": ref_syms[:5],
        "top5_shift": sh_syms[:5],
        "top10_ref": ref_syms[:10],
        "top10_shift": sh_syms[:10],
    }


def _score_at(eng: Any, *, day: str, t0: float, session: str) -> dict[str, Any]:
    scored = score_universe_at(eng, t0=float(t0), day=day, session=session)
    return scored


def _portfolio_pack(eng: Any, dual: Any, *, day: str, variant: str, offset_sec: int, events_n: int, last_et: Any) -> dict[str, Any]:
    raw = extract_trades(dual)
    trades = _pack_trades(day=day, raw_trades=raw, eng=eng, variant=variant)
    _attach_path_stats(eng, trades, day=day)
    pnls = [float(t.get("pnl_yen_100") or 0.0) for t in trades]
    w = sum(1 for p in pnls if p > 1e-9)
    l = sum(1 for p in pnls if p < -1e-9)
    gp = sum(p for p in pnls if p > 0)
    gl = sum(-p for p in pnls if p < 0)
    symbols = [str(t.get("symbol") or "") for t in trades]
    reentry = len(symbols) - len(set(symbols))
    return {
        "ok": True,
        "date": day,
        "variant": variant,
        "offset_sec": int(offset_sec),
        "events_processed": events_n,
        "anchor_fires": int(eng.anchor_fires),
        "admitted": int(eng.primary_admitted),
        "fills": int(eng.primary_fills),
        "expired": int(eng.primary_expired),
        "cap_blocked": int(getattr(eng, "cap_blocked", 0) or 0),
        "same_symbol_blocked": int(getattr(eng, "same_symbol_blocked", 0) or 0),
        "reentry_extra_fills": int(reentry),
        "trades": trades,
        "trade_n": len(trades),
        "pnl": round(sum(pnls), 2),
        "PF": _pf(pnls),
        "win": w,
        "loss": l,
        "draw": len(pnls) - w - l,
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "avg_pnl": round(sum(pnls) / len(trades), 4) if trades else 0.0,
        "maxDD": _maxdd(trades),
        "AM": _sess_stats(trades, "AM"),
        "PM": _sess_stats(trades, "PM"),
        "ledger_sha": _ledger_sha(trades),
        "last_et": last_et,
    }


def _stream_one(
    *,
    day: str,
    capture: Path,
    universe: list[str],
    offset_sec: int,
    fire_mode: str,
    variant: str,
) -> tuple[Any, Any, dict[str, Any]]:
    eng, dual = _boot(universe, RobustEngine)
    if dual is None or not eng.ready:
        return None, None, {
            "ok": False,
            "date": day,
            "variant": variant,
            "blocker": getattr(eng, "fail_reason", "dual_unavailable"),
        }
    eng.offset_sec = int(offset_sec)
    eng.allowed_hm = None
    eng.fire_mode = fire_mode
    eng.notify_enabled = False
    eng.ingest_audit = _Discard()  # type: ignore[assignment]
    events_n, last_et = _stream_day(day, capture, eng, dual)
    eng._harvest(eng.events)
    packed = _portfolio_pack(eng, dual, day=day, variant=variant, offset_sec=offset_sec, events_n=events_n, last_et=last_et)
    packed["native_ingest_raw_sequence_holes"] = int(getattr(eng, "native_ingest_raw_sequence_holes", 0) or 0)
    return eng, dual, packed


def score_primary(eng: Any, *, day: str) -> dict[str, Any]:
    validity = valid_shift_map(day=day)
    leak = False
    comparisons: list[dict[str, Any]] = []
    isolated: list[dict[str, Any]] = []
    rank_rows: list[dict[str, Any]] = []
    scored_cache: dict[tuple[str, int], dict[str, Any]] = {}

    def get_scored(anchor: str, shift_min: int, t0: float, session: str) -> dict[str, Any]:
        key = (anchor, int(shift_min))
        if key not in scored_cache:
            scored_cache[key] = _score_at(eng, day=day, t0=t0, session=session)
        return scored_cache[key]

    for h, m in canonical_grid():
        anchor = hm_label(h, m)
        t_ref = hm_epoch(day, h, m)
        sess_ref = session_of_epoch(day, t_ref)
        if sess_ref is None:
            continue
        ref_scored = get_scored(anchor, 0, t_ref, sess_ref)
        if ref_scored.get("snapshot_future_leak"):
            leak = True
        isolated.extend(
            _isolated_trades(
                eng,
                day=day,
                session=sess_ref,
                anchor=anchor,
                shift_min=0,
                t0=t_ref,
                rows=ref_scored.get("rows") or [],
            )
        )
        for sh in SHIFTS_MIN:
            if sh == 0:
                continue
            meta = validity.get((anchor, int(sh))) or {}
            valid = bool(meta.get("valid"))
            t1 = shifted_epoch(day, h, m, int(sh))
            sess1 = session_of_epoch(day, t1)
            row = {
                "date": day,
                "anchor": anchor,
                "tod_bucket": tod_bucket(anchor),
                "shift_min": int(sh),
                "shift_key": SHIFT_KEYS[int(sh)],
                "valid": valid,
                "reason": "" if valid else "INVALID_SESSION_BOUNDARY",
                "shifted_hhmm": meta.get("shifted_hhmm"),
            }
            if not valid or sess1 is None:
                comparisons.append(row)
                continue
            sh_scored = get_scored(anchor, int(sh), t1, sess1)
            if sh_scored.get("snapshot_future_leak"):
                leak = True
            cmp_ = _compare_pair(ref_scored.get("rows") or [], sh_scored.get("rows") or [])
            row.update(cmp_)
            comparisons.append(row)
            isolated.extend(
                _isolated_trades(
                    eng,
                    day=day,
                    session=sess1,
                    anchor=anchor,
                    shift_min=int(sh),
                    t0=t1,
                    rows=sh_scored.get("rows") or [],
                )
            )
            for rec in _ranked(sh_scored.get("rows") or []):
                rank_rows.append(
                    {
                        "date": day,
                        "anchor": anchor,
                        "shift_key": SHIFT_KEYS[int(sh)],
                        "symbol": rec["symbol"],
                        "rank": rec["rank"],
                        "score": rec.get("alloc_score"),
                    }
                )
        for rec in _ranked(ref_scored.get("rows") or []):
            rank_rows.append(
                {
                    "date": day,
                    "anchor": anchor,
                    "shift_key": "REFERENCE",
                    "symbol": rec["symbol"],
                    "rank": rec["rank"],
                    "score": rec.get("alloc_score"),
                }
            )

    regular: list[dict[str, Any]] = []
    for step in (10, 20):
        grid = regular_clock_grid(step)
        for h, m in grid:
            t0 = hm_epoch(day, h, m)
            sess = session_of_epoch(day, t0)
            if sess is None:
                continue
            sc = _score_at(eng, day=day, t0=t0, session=sess)
            if sc.get("snapshot_future_leak"):
                leak = True
            sel = _selected(sc.get("rows") or [])
            iso = _isolated_trades(
                eng,
                day=day,
                session=sess,
                anchor=hm_label(h, m),
                shift_min=0,
                t0=t0,
                rows=sc.get("rows") or [],
            )
            filled = [t for t in iso if t.get("independent_filled")]
            pnls = [float(t.get("pnl_yen_100") or 0.0) for t in filled]
            regular.append(
                {
                    "date": day,
                    "grid": f"{step}MIN",
                    "slot": hm_label(h, m),
                    "session": sess,
                    "eligible_n": sc.get("eligible_symbol_n"),
                    "selected_n": len(sel),
                    "selected": sel,
                    "isolated_fill_n": len(filled),
                    "isolated_pnl": round(sum(pnls), 2) if pnls else 0.0,
                }
            )

    # nearest regular-10 rank stability vs each canonical
    g10 = regular_clock_grid(10)
    nearest_rows: list[dict[str, Any]] = []
    for h, m in canonical_grid():
        t = h * 60 + m
        nh, nm = min(g10, key=lambda hm: (abs(hm[0] * 60 + hm[1] - t), hm[0] * 60 + hm[1]))
        dist = abs(nh * 60 + nm - t)
        if dist > 10:
            continue
        anchor = hm_label(h, m)
        t_ref = hm_epoch(day, h, m)
        sess = session_of_epoch(day, t_ref)
        t_n = hm_epoch(day, nh, nm)
        sess_n = session_of_epoch(day, t_n)
        if sess is None or sess_n is None:
            continue
        ref_scored = get_scored(anchor, 0, t_ref, sess)
        near = _score_at(eng, day=day, t0=t_n, session=sess_n)
        cmp_ = _compare_pair(ref_scored.get("rows") or [], near.get("rows") or [])
        nearest_rows.append(
            {
                "date": day,
                "anchor": anchor,
                "nearest_10min": hm_label(nh, nm),
                "distance_min": dist,
                "spearman": cmp_.get("spearman"),
                "top5_overlap": cmp_.get("top5_overlap"),
                "entry_jaccard": cmp_.get("entry_jaccard"),
            }
        )

    return {
        "comparisons": comparisons,
        "isolated_trades": isolated,
        "rank_rows": rank_rows,
        "regular": regular,
        "nearest_regular10": nearest_rows,
        "snapshot_future_leak": bool(leak),
    }


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    _pop_webhooks()
    if str(NATIVE / "src") not in sys.path:
        sys.path.insert(0, str(NATIVE / "src"))
        sys.path.insert(0, str(NATIVE / "scripts"))
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    t0w = time.perf_counter()
    try:
        eng, _dual, pref = _stream_one(
            day=day,
            capture=capture,
            universe=universe,
            offset_sec=0,
            fire_mode="production",
            variant="REFERENCE",
        )
        if eng is None or not pref.get("ok"):
            return {
                "ok": False,
                "date": day,
                "blocker": pref.get("blocker") or "REFERENCE_STREAM_FAILED",
                "elapsed_sec": round(time.perf_counter() - t0w, 3),
            }
        print(
            f"{day} REFERENCE done fires={pref.get('anchor_fires')} trades={pref.get('trade_n')} pnl={pref.get('pnl')}",
            flush=True,
        )
        primary = score_primary(eng, day=day)
        print(f"{day} PRIMARY scoring done leak={primary.get('snapshot_future_leak')}", flush=True)
        del eng, _dual
        import gc
        gc.collect()
        portfolios = {"REFERENCE": pref}
        shift_errors: list[str] = []
        for sh in SHIFTS_MIN:
            if sh == 0:
                continue
            _e, _d, packed = _stream_one(
                day=day,
                capture=capture,
                universe=universe,
                offset_sec=int(sh) * 60,
                fire_mode="shifted_grid",
                variant=SHIFT_KEYS[int(sh)],
            )
            if not packed.get("ok"):
                shift_errors.append(f"{SHIFT_KEYS[int(sh)]}:{packed.get('blocker')}")
                print(f"{day} {SHIFT_KEYS[int(sh)]} FAIL {packed.get('blocker')}", flush=True)
                continue
            portfolios[SHIFT_KEYS[int(sh)]] = packed
            print(
                f"{day} {SHIFT_KEYS[int(sh)]} fires={packed.get('anchor_fires')} trades={packed.get('trade_n')} pnl={packed.get('pnl')}",
                flush=True,
            )
            del _e, _d
            gc.collect()
        return {
            "ok": True,
            "date": day,
            "period": payload.get("period"),
            "universe_n": len(universe),
            "universe_source": payload.get("universe_source"),
            "primary": primary,
            "portfolios": portfolios,
            "shift_errors": shift_errors,
            "snapshot_future_leak": bool(primary.get("snapshot_future_leak")),
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
