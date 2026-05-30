"""
Phase 172: Exit metric redesign review (replay only).

Goal:
- Compare simple price/time/MFE based exits against current `combined_structural_exit_v1`.
- Avoid parameter search / single-day overfitting: fixed scenario set only.

Input:
- structural_trades.csv (accepted trade lifecycles + MFE/MAE)
- small_paper_events.csv (candidate/accepted events with current_price for price timeline reconstruction)
- small_paper_summary.json (session_end)

Outputs are written by the companion script under kabu_native/scripts/.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.mfe_mae_exit_review import (
    as_float,
    build_price_timeline_from_events_csv,
    load_structural_trades,
    parse_ts,
    pnl_pct,
)

JST = ZoneInfo("Asia/Tokyo")


POST_EXIT_HORIZONS = (60, 120, 300)


@dataclass(frozen=True)
class ExitResult:
    exit_ts: float
    exit_time: str
    exit_price: float
    exit_pnl_pct: float
    exit_reason: str
    hold_sec: float
    mfe_pct: float
    mae_pct: float


def _parse_dt_iso(ts: str) -> datetime:
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(JST)


def _session_end_ts_from_summary(summary_path: Path) -> float:
    if not summary_path.is_file():
        return 0.0
    s = json.loads(summary_path.read_text(encoding="utf-8"))
    end_hhmm = str(s.get("session_end") or "15:30")
    # Use generated_at date as anchor (same day)
    gen = _parse_dt_iso(str(s.get("generated_at") or s.get("ended_at") or ""))
    if not gen:
        return 0.0
    try:
        hh, mm = int(end_hhmm[:2]), int(end_hhmm[3:5])
    except Exception:
        hh, mm = 15, 30
    end_dt = gen.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return end_dt.timestamp()


def _timeline_slice(
    tl: Sequence[tuple[float, float]],
    *,
    start_ts: float,
    end_ts: float,
) -> list[tuple[float, float]]:
    if not tl:
        return []
    out: list[tuple[float, float]] = []
    for ts, px in tl:
        if ts < start_ts:
            continue
        if ts > end_ts:
            break
        out.append((ts, px))
    return out


def _first_price_at_or_after(
    tl: Sequence[tuple[float, float]],
    ts_target: float,
    *,
    fallback_last_before_end: bool = True,
) -> Optional[tuple[float, float]]:
    last: Optional[tuple[float, float]] = None
    for ts, px in tl:
        if ts >= ts_target:
            return ts, px
        last = (ts, px)
    return last if fallback_last_before_end else None


def _mfe_mae_from_path(
    tl: Sequence[tuple[float, float]],
    *,
    entry_px: float,
) -> tuple[float, float]:
    if not tl or entry_px <= 0:
        return 0.0, 0.0
    best = -1e9
    worst = 1e9
    for _, px in tl:
        p = pnl_pct(entry_px, px) or 0.0
        best = max(best, p)
        worst = min(worst, p)
    return round(best, 4), round(worst, 4)


def _best_pnl_in_window(
    tl: Sequence[tuple[float, float]],
    *,
    entry_px: float,
    base_ts: float,
    window_sec: float,
    session_end_ts: float,
) -> Optional[float]:
    end = min(session_end_ts, base_ts + window_sec)
    best: Optional[float] = None
    for ts, px in tl:
        if ts < base_ts:
            continue
        if ts > end:
            break
        p = pnl_pct(entry_px, px)
        if p is None:
            continue
        best = p if best is None else max(best, p)
    return round(best, 4) if best is not None else None


def _median(values: Sequence[float]) -> Optional[float]:
    vals = [float(v) for v in values if v is not None]
    return float(statistics.median(vals)) if vals else None


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gl = abs(sum(losses))
    if gl <= 0:
        return None if not wins else float("inf")
    return sum(wins) / gl


def _exit_by_simple_stop_take(
    path: Sequence[tuple[float, float]],
    *,
    entry_ts: float,
    entry_px: float,
    session_end_ts: float,
    stop_pct: float = -1.2,
    take_pct: float = 1.5,
) -> ExitResult:
    # Evaluate in chronological order; first threshold hit wins.
    for ts, px in path:
        p = pnl_pct(entry_px, px) or 0.0
        if p <= stop_pct:
            hold = ts - entry_ts
            mfe, mae = _mfe_mae_from_path(_timeline_slice(path, start_ts=entry_ts, end_ts=ts), entry_px=entry_px)
            return ExitResult(ts, datetime.fromtimestamp(ts, JST).isoformat(), px, p, "stop_hit", hold, mfe, mae)
        if p >= take_pct:
            hold = ts - entry_ts
            mfe, mae = _mfe_mae_from_path(_timeline_slice(path, start_ts=entry_ts, end_ts=ts), entry_px=entry_px)
            return ExitResult(ts, datetime.fromtimestamp(ts, JST).isoformat(), px, p, "take_profit", hold, mfe, mae)
    # session close fallback
    last = _first_price_at_or_after(path, session_end_ts) or (entry_ts, entry_px)
    ts, px = last
    p = pnl_pct(entry_px, px) or 0.0
    mfe, mae = _mfe_mae_from_path(_timeline_slice(path, start_ts=entry_ts, end_ts=ts), entry_px=entry_px)
    return ExitResult(ts, datetime.fromtimestamp(ts, JST).isoformat(), px, p, "session_close", ts - entry_ts, mfe, mae)


def _exit_by_trailing_mfe(
    path: Sequence[tuple[float, float]],
    *,
    entry_ts: float,
    entry_px: float,
    session_end_ts: float,
    activate_mfe_pct: float = 0.8,
    giveback_frac: float = 0.5,
) -> ExitResult:
    mfe = 0.0
    active = False
    for ts, px in path:
        p = pnl_pct(entry_px, px) or 0.0
        mfe = max(mfe, p)
        if not active and mfe >= activate_mfe_pct:
            active = True
        if active and p <= mfe * giveback_frac:
            hold = ts - entry_ts
            mfe2, mae2 = _mfe_mae_from_path(_timeline_slice(path, start_ts=entry_ts, end_ts=ts), entry_px=entry_px)
            return ExitResult(ts, datetime.fromtimestamp(ts, JST).isoformat(), px, p, "trailing_mfe_giveback", hold, mfe2, mae2)
    last = _first_price_at_or_after(path, session_end_ts) or (entry_ts, entry_px)
    ts, px = last
    p = pnl_pct(entry_px, px) or 0.0
    mfe2, mae2 = _mfe_mae_from_path(_timeline_slice(path, start_ts=entry_ts, end_ts=ts), entry_px=entry_px)
    return ExitResult(ts, datetime.fromtimestamp(ts, JST).isoformat(), px, p, "session_close", ts - entry_ts, mfe2, mae2)


def _exit_by_time_stop(
    path: Sequence[tuple[float, float]],
    *,
    entry_ts: float,
    entry_px: float,
    session_end_ts: float,
    window_sec: float = 180.0,
    min_up_pct: float = 0.3,
) -> ExitResult:
    end_check = min(session_end_ts, entry_ts + window_sec)
    max_pnl = 0.0
    last_px = entry_px
    last_ts = entry_ts
    for ts, px in path:
        if ts < entry_ts:
            continue
        if ts > end_check:
            break
        last_px = px
        last_ts = ts
        p = pnl_pct(entry_px, px) or 0.0
        max_pnl = max(max_pnl, p)
    # if never reached threshold, exit at window end unless profitable at that moment
    if max_pnl < min_up_pct:
        chosen = _first_price_at_or_after(path, end_check) or (last_ts, last_px)
        ts, px = chosen
        p = pnl_pct(entry_px, px) or 0.0
        if p <= 0:
            mfe2, mae2 = _mfe_mae_from_path(_timeline_slice(path, start_ts=entry_ts, end_ts=ts), entry_px=entry_px)
            return ExitResult(ts, datetime.fromtimestamp(ts, JST).isoformat(), px, p, "time_stop_no_pop", ts - entry_ts, mfe2, mae2)
    # otherwise hold to session close
    last = _first_price_at_or_after(path, session_end_ts) or (entry_ts, entry_px)
    ts, px = last
    p = pnl_pct(entry_px, px) or 0.0
    mfe2, mae2 = _mfe_mae_from_path(_timeline_slice(path, start_ts=entry_ts, end_ts=ts), entry_px=entry_px)
    return ExitResult(ts, datetime.fromtimestamp(ts, JST).isoformat(), px, p, "session_close", ts - entry_ts, mfe2, mae2)


def _exit_by_recent_low_break(
    path: Sequence[tuple[float, float]],
    *,
    entry_ts: float,
    entry_px: float,
    session_end_ts: float,
    lookback_sec: float = 60.0,
) -> ExitResult:
    window: list[tuple[float, float]] = []
    for ts, px in path:
        if ts < entry_ts:
            continue
        # maintain 60s window
        window.append((ts, px))
        cutoff = ts - lookback_sec
        while window and window[0][0] < cutoff:
            window.pop(0)
        # need enough history
        if len(window) < 2:
            continue
        prev_low = min(p for _, p in window[:-1])
        if px < prev_low - 1e-9:
            p = pnl_pct(entry_px, px) or 0.0
            mfe2, mae2 = _mfe_mae_from_path(_timeline_slice(path, start_ts=entry_ts, end_ts=ts), entry_px=entry_px)
            return ExitResult(ts, datetime.fromtimestamp(ts, JST).isoformat(), px, p, "recent_low_break", ts - entry_ts, mfe2, mae2)
    last = _first_price_at_or_after(path, session_end_ts) or (entry_ts, entry_px)
    ts, px = last
    p = pnl_pct(entry_px, px) or 0.0
    mfe2, mae2 = _mfe_mae_from_path(_timeline_slice(path, start_ts=entry_ts, end_ts=ts), entry_px=entry_px)
    return ExitResult(ts, datetime.fromtimestamp(ts, JST).isoformat(), px, p, "session_close", ts - entry_ts, mfe2, mae2)


def _exit_hold_reference(
    path: Sequence[tuple[float, float]],
    *,
    entry_ts: float,
    entry_px: float,
    session_end_ts: float,
    hold_sec: float,
    stop_pct: float = -1.2,
) -> ExitResult:
    end_ts = min(session_end_ts, entry_ts + hold_sec)
    # stop active
    for ts, px in path:
        if ts < entry_ts:
            continue
        if ts > end_ts:
            break
        p = pnl_pct(entry_px, px) or 0.0
        if p <= stop_pct:
            mfe2, mae2 = _mfe_mae_from_path(_timeline_slice(path, start_ts=entry_ts, end_ts=ts), entry_px=entry_px)
            return ExitResult(ts, datetime.fromtimestamp(ts, JST).isoformat(), px, p, "stop_hit", ts - entry_ts, mfe2, mae2)
    chosen = _first_price_at_or_after(path, end_ts) or (entry_ts, entry_px)
    ts, px = chosen
    p = pnl_pct(entry_px, px) or 0.0
    mfe2, mae2 = _mfe_mae_from_path(_timeline_slice(path, start_ts=entry_ts, end_ts=ts), entry_px=entry_px)
    reason = "hold_elapsed" if end_ts < session_end_ts - 1e-6 else "session_close"
    return ExitResult(ts, datetime.fromtimestamp(ts, JST).isoformat(), px, p, reason, ts - entry_ts, mfe2, mae2)


def evaluate_exit_policies(
    *,
    session_dir: Path,
) -> dict[str, Any]:
    trades_path = session_dir / "structural_trades.csv"
    events_csv = session_dir / "small_paper_events.csv"
    summary_path = session_dir / "small_paper_summary.json"

    trades = load_structural_trades(trades_path)
    if not trades:
        return {"ok": False, "error": "no_structural_trades"}

    symbols = {str(t.get("symbol") or "") for t in trades if str(t.get("symbol") or "")}
    tl_map = build_price_timeline_from_events_csv(events_csv, symbols=set(symbols))
    session_end_ts = _session_end_ts_from_summary(summary_path) or max(parse_ts(str(t.get("close_time") or "")) for t in trades)

    # Step1: price path reconstruction feasibility
    cover = [sym for sym in symbols if tl_map.get(sym)]
    coverage_rate = len(cover) / max(1, len(symbols))
    median_pts = _median([len(tl_map.get(sym) or []) for sym in symbols]) or 0
    mode = "tick_replay" if coverage_rate >= 0.9 and median_pts >= 30 else "structural_approximation"

    scenarios = {
        "A_current_combined_structural_exit_v1": {"kind": "baseline"},
        "B_simple_stop_take": {"kind": "simple_stop_take"},
        "C_trailing_mfe": {"kind": "trailing_mfe"},
        "D_time_stop": {"kind": "time_stop"},
        "E_vwap_break_exit": {"kind": "not_supported_no_vwap"},
        "F_recent_low_break": {"kind": "recent_low_break"},
        "G_simple_combined": {"kind": "simple_combined"},
        "H_hold_5min_reference": {"kind": "hold_ref", "hold_sec": 300.0},
        "I_hold_10min_reference": {"kind": "hold_ref", "hold_sec": 600.0},
    }

    per_trade_rows: dict[str, list[dict[str, Any]]] = {k: [] for k in scenarios}

    for t in trades:
        sym = str(t.get("symbol") or "")
        entry_time = str(t.get("entry_time") or "")
        entry_ts = parse_ts(entry_time)
        entry_px = float(as_float(t.get("entry_price")) or 0.0)
        if not sym or entry_ts <= 0 or entry_px <= 0:
            continue
        tl_full = tl_map.get(sym) or []
        path = _timeline_slice(tl_full, start_ts=entry_ts, end_ts=session_end_ts)
        if not path:
            # structural approx fallback: we can only reuse realized exit; other scenarios will be marked NA
            path = [(entry_ts, entry_px)]

        # Baseline A: use provided close_time/close_price/realized_pnl/mfe/mae/hold
        close_ts = parse_ts(str(t.get("close_time") or ""))
        close_px = float(as_float(t.get("close_price")) or 0.0)
        realized = as_float(t.get("realized_pnl_pct"))
        if realized is None and close_px > 0:
            realized = pnl_pct(entry_px, close_px) or 0.0
        baseline = ExitResult(
            exit_ts=close_ts,
            exit_time=str(t.get("close_time") or ""),
            exit_price=close_px,
            exit_pnl_pct=float(realized or 0.0),
            exit_reason=str(t.get("close_reason") or ""),
            hold_sec=float(as_float(t.get("hold_duration_sec")) or 0.0),
            mfe_pct=float(as_float(t.get("mfe_pct")) or 0.0),
            mae_pct=float(as_float(t.get("mae_pct")) or 0.0),
        )

        results: dict[str, Optional[ExitResult]] = {"A_current_combined_structural_exit_v1": baseline}
        if mode != "tick_replay":
            # Provide only baseline in approximation mode (explicitly).
            for k in scenarios:
                if k not in results:
                    results[k] = None
        else:
            results["B_simple_stop_take"] = _exit_by_simple_stop_take(path, entry_ts=entry_ts, entry_px=entry_px, session_end_ts=session_end_ts)
            results["C_trailing_mfe"] = _exit_by_trailing_mfe(path, entry_ts=entry_ts, entry_px=entry_px, session_end_ts=session_end_ts)
            results["D_time_stop"] = _exit_by_time_stop(path, entry_ts=entry_ts, entry_px=entry_px, session_end_ts=session_end_ts)
            results["E_vwap_break_exit"] = None  # not supported
            results["F_recent_low_break"] = _exit_by_recent_low_break(path, entry_ts=entry_ts, entry_px=entry_px, session_end_ts=session_end_ts)
            # simple_combined: stop/take/trailing/recent_low/session_close in priority order
            # Priority: stop/take first, then trailing, then recent_low, else close.
            st = _exit_by_simple_stop_take(path, entry_ts=entry_ts, entry_px=entry_px, session_end_ts=session_end_ts)
            if st.exit_reason in ("stop_hit", "take_profit"):
                results["G_simple_combined"] = st
            else:
                tr = _exit_by_trailing_mfe(path, entry_ts=entry_ts, entry_px=entry_px, session_end_ts=session_end_ts)
                if tr.exit_reason == "trailing_mfe_giveback":
                    results["G_simple_combined"] = tr
                else:
                    rl = _exit_by_recent_low_break(path, entry_ts=entry_ts, entry_px=entry_px, session_end_ts=session_end_ts)
                    results["G_simple_combined"] = rl
            results["H_hold_5min_reference"] = _exit_hold_reference(path, entry_ts=entry_ts, entry_px=entry_px, session_end_ts=session_end_ts, hold_sec=300.0)
            results["I_hold_10min_reference"] = _exit_hold_reference(path, entry_ts=entry_ts, entry_px=entry_px, session_end_ts=session_end_ts, hold_sec=600.0)

        for scen_key, res in results.items():
            if res is None:
                per_trade_rows[scen_key].append(
                    {
                        "scenario": scen_key,
                        "mode": mode,
                        "symbol": sym,
                        "entry_time": entry_time,
                        "entry_price": entry_px,
                        "exit_time": None,
                        "exit_price": None,
                        "exit_pnl_pct": None,
                        "exit_reason": "not_available_in_mode" if mode != "tick_replay" else "not_supported",
                        "hold_sec": None,
                        "mfe_pct": None,
                        "mae_pct": None,
                    }
                )
                continue

            # post-exit reacceleration: best pnl after exit for horizons
            post_best: dict[str, Optional[float]] = {}
            for h in POST_EXIT_HORIZONS:
                post_best[f"post_exit_best_pnl_{h}s"] = _best_pnl_in_window(
                    path,
                    entry_px=entry_px,
                    base_ts=res.exit_ts,
                    window_sec=float(h),
                    session_end_ts=session_end_ts,
                )

            mfe_capture = None
            if res.mfe_pct > 0.01:
                mfe_capture = round(res.exit_pnl_pct / res.mfe_pct, 4)
            after_reaccel = {}
            for h in POST_EXIT_HORIZONS:
                pb = post_best.get(f"post_exit_best_pnl_{h}s")
                after_reaccel[f"after_exit_reacceleration_{h}s"] = (
                    pb is not None and pb >= res.exit_pnl_pct + 0.2
                )

            per_trade_rows[scen_key].append(
                {
                    "scenario": scen_key,
                    "mode": mode,
                    "symbol": sym,
                    "entry_time": entry_time,
                    "entry_price": entry_px,
                    "exit_time": res.exit_time,
                    "exit_price": res.exit_price,
                    "exit_pnl_pct": round(res.exit_pnl_pct, 4),
                    "exit_reason": res.exit_reason,
                    "hold_sec": round(res.hold_sec, 1),
                    "mfe_pct": round(res.mfe_pct, 4),
                    "mae_pct": round(res.mae_pct, 4),
                    "mfe_capture_rate": mfe_capture,
                    **post_best,
                    **after_reaccel,
                }
            )

    # aggregate scenario metrics
    scenario_rows: list[dict[str, Any]] = []
    mfe_capture_rows: list[dict[str, Any]] = []
    after_exit_rows: list[dict[str, Any]] = []
    reason_breakdown_rows: list[dict[str, Any]] = []
    simple_trade_details: list[dict[str, Any]] = []

    for scen_key, rows in per_trade_rows.items():
        valid = [r for r in rows if r.get("exit_pnl_pct") is not None]
        pnls = [float(r["exit_pnl_pct"]) for r in valid]
        holds = [float(r["hold_sec"]) for r in valid if r.get("hold_sec") is not None]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        pf = _profit_factor(pnls)
        win_rate = (len(wins) / len(pnls)) if pnls else None

        scenario_rows.append(
            {
                "scenario": scen_key,
                "mode": mode,
                "trade_count": len(valid),
                "total_pnl": round(sum(pnls), 4) if pnls else None,
                "total_win_pnl": round(sum(wins), 4) if pnls else None,
                "total_loss_pnl": round(sum(losses), 4) if pnls else None,
                "avg_pnl": round(statistics.mean(pnls), 4) if pnls else None,
                "pf": round(pf, 4) if pf is not None and math.isfinite(pf) else pf,
                "win_rate": round(win_rate, 4) if win_rate is not None else None,
                "max_loss": round(min(pnls), 4) if pnls else None,
                "max_gain": round(max(pnls), 4) if pnls else None,
                "stop_hit_count": sum(1 for r in valid if str(r.get("exit_reason")) == "stop_hit"),
                "take_count": sum(1 for r in valid if str(r.get("exit_reason")) == "take_profit"),
                "avg_hold_sec": round(statistics.mean(holds), 2) if holds else None,
                "median_hold_sec": round(statistics.median(holds), 2) if holds else None,
                "early_exit_rate": round(
                    sum(1 for r in valid if float(r.get("hold_sec") or 0) <= 30.0) / len(valid), 4
                )
                if valid
                else None,
                "late_exit_rate": round(
                    sum(1 for r in valid if float(r.get("hold_sec") or 0) >= 300.0) / len(valid), 4
                )
                if valid
                else None,
            }
        )

        caps = [float(r["mfe_capture_rate"]) for r in valid if as_float(r.get("mfe_capture_rate")) is not None]
        mfe_capture_rows.append(
            {
                "scenario": scen_key,
                "trade_count": len(valid),
                "avg_mfe_capture_rate": round(statistics.mean(caps), 4) if caps else None,
                "median_mfe_capture_rate": round(statistics.median(caps), 4) if caps else None,
                "low_capture_rate_lt_0_2": sum(1 for c in caps if c < 0.2),
            }
        )

        for h in POST_EXIT_HORIZONS:
            key = f"after_exit_reacceleration_{h}s"
            after_exit_rows.append(
                {
                    "scenario": scen_key,
                    "horizon_sec": h,
                    "trade_count": len(valid),
                    "reacceleration_rate": round(
                        sum(1 for r in valid if r.get(key) in (True, "True", 1)) / len(valid), 4
                    )
                    if valid
                    else None,
                }
            )

        by_reason: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for r in valid:
            by_reason[str(r.get("exit_reason") or "unknown")].append(r)
        for reason, grp in sorted(by_reason.items()):
            grp_pnls = [float(x["exit_pnl_pct"]) for x in grp]
            reason_breakdown_rows.append(
                {
                    "scenario": scen_key,
                    "exit_reason": reason,
                    "trade_count": len(grp),
                    "total_pnl": round(sum(grp_pnls), 4),
                    "avg_pnl": round(statistics.mean(grp_pnls), 4) if grp_pnls else None,
                }
            )

        if scen_key in ("B_simple_stop_take", "C_trailing_mfe", "D_time_stop", "F_recent_low_break", "G_simple_combined"):
            # include per-trade details for these simple policies
            simple_trade_details.extend(valid)

    # verdict heuristic (lightweight, not overfit):
    # choose best PF among supported scenarios; if baseline is best -> D.
    pf_by = {r["scenario"]: r.get("pf") for r in scenario_rows if r.get("pf") is not None and r.get("trade_count")}
    best = None
    best_pf = -1e9
    for k, v in pf_by.items():
        if v is None or v == float("inf"):
            continue
        if float(v) > best_pf:
            best_pf = float(v)
            best = k
    baseline_pf = pf_by.get("A_current_combined_structural_exit_v1")
    verdict = "F_insufficient_data" if not pf_by else "E_need_new_indicator"
    if best == "G_simple_combined":
        verdict = "A_simple_exit_promising"
    elif best == "C_trailing_mfe":
        verdict = "B_trailing_mfe_promising"
    elif best in ("F_recent_low_break",):
        verdict = "C_vwap_recent_low_promising"
    elif best == "A_current_combined_structural_exit_v1":
        verdict = "D_current_exit_still_best"
    elif baseline_pf is not None and best_pf <= float(baseline_pf):
        verdict = "D_current_exit_still_best"

    return {
        "ok": True,
        "phase": 172,
        "session_dir": str(session_dir),
        "mode": mode,
        "price_path_coverage_rate": round(coverage_rate, 4),
        "price_path_median_points_per_symbol": median_pts,
        "session_end_ts": session_end_ts,
        "verdict": verdict,
        "verdict_options": {
            "A": "simple_exit_promising",
            "B": "trailing_mfe_promising",
            "C": "vwap_recent_low_promising",
            "D": "current_exit_still_best",
            "E": "need_new_indicator",
            "F": "insufficient_data",
        },
        "scenario_metrics": scenario_rows,
        "mfe_capture_analysis": mfe_capture_rows,
        "after_exit_reacceleration": after_exit_rows,
        "exit_reason_failure_breakdown": reason_breakdown_rows,
        "simple_exit_trade_details": simple_trade_details,
        "per_trade_by_scenario": per_trade_rows,
    }

