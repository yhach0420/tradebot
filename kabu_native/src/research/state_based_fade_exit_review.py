"""
Phase 126: State-based fade_watch exit simulation (event-driven, no fixed-time exit).
"""

from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.fade_exit_replay import FADE_EXIT_REASONS, is_fade_trade
from research.mfe_mae_exit_review import (
    as_float,
    load_structural_trades,
    parse_ts,
    pnl_pct,
    session_end_ts_from_trades,
)

GIVEBACK_FRAC = 0.25
MOMENTUM_EPS = 0.02
REACCEL_MIN_SIGNALS = 2
PNL_EPS = 0.01


@dataclass
class TickEvent:
    ts: float
    price: float
    momentum: Optional[float] = None
    rolling_mfe: Optional[float] = None
    rolling_mae: Optional[float] = None
    favorable: Optional[float] = None


@dataclass
class SimResult:
    scenario_id: str
    exit_ts: float
    exit_price: float
    exit_pnl: float
    exit_reason: str
    ticks_observed: int
    peak_mfe_after_fade: float
    reacceleration_detected: bool
    state_log: list[str] = field(default_factory=list)


def load_session_event_index(events_csv: Path) -> dict[str, list[TickEvent]]:
    by_sym: dict[str, list[TickEvent]] = {}
    if not events_csv.is_file():
        return by_sym
    with events_csv.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = str(row.get("symbol") or "")
            px = as_float(row.get("current_price"))
            ts = parse_ts(str(row.get("event_time") or row.get("entry_time") or ""))
            if not sym or px is None or px <= 0 or ts <= 0:
                continue
            by_sym.setdefault(sym, []).append(
                TickEvent(
                    ts=ts,
                    price=float(px),
                    momentum=as_float(row.get("momentum_continuation_score")),
                    rolling_mfe=as_float(row.get("rolling_mfe_pct")),
                    rolling_mae=as_float(row.get("rolling_mae_pct")),
                    favorable=as_float(row.get("favorable_continuation")),
                )
            )
    for sym in by_sym:
        by_sym[sym].sort(key=lambda e: e.ts)
        dedup: list[TickEvent] = []
        last_ts = -1.0
        for e in by_sym[sym]:
            if e.ts == last_ts and dedup:
                dedup[-1] = e
            else:
                dedup.append(e)
                last_ts = e.ts
        by_sym[sym] = dedup
    return by_sym


def slice_stream(
    stream: Sequence[TickEvent],
    *,
    start_ts: float,
    end_ts: float,
) -> list[TickEvent]:
    return [e for e in stream if start_ts <= e.ts <= end_ts]


def load_symbol_event_stream(
    events_csv: Path,
    symbol: str,
    *,
    start_ts: float,
    end_ts: float,
    index: Optional[dict[str, list[TickEvent]]] = None,
) -> list[TickEvent]:
    if index is not None:
        return slice_stream(index.get(symbol, []), start_ts=start_ts, end_ts=end_ts)


def _fade_context(
    stream: Sequence[TickEvent],
    fade_ts: float,
) -> tuple[Optional[TickEvent], list[TickEvent]]:
    at_fade: Optional[TickEvent] = None
    after: list[TickEvent] = []
    for e in stream:
        if e.ts <= fade_ts:
            at_fade = e
        elif e.ts > fade_ts:
            after.append(e)
    return at_fade, after


def _reaccel_score(
    *,
    price: float,
    fade_price: float,
    peak_price: float,
    pnl: float,
    peak_pnl: float,
    momentum: Optional[float],
    fade_momentum: Optional[float],
    new_high_this_tick: bool,
    mfe_updated_this_tick: bool,
    vwap_above: Optional[bool],
) -> int:
    score = 0
    if price > fade_price:
        score += 1
    if new_high_this_tick or price >= peak_price - 1e-9:
        score += 1
    if mfe_updated_this_tick or pnl >= peak_pnl - 1e-9:
        score += 1
    if momentum is not None and fade_momentum is not None and momentum > fade_momentum + MOMENTUM_EPS:
        score += 1
    if vwap_above is True:
        score += 1
    return score


def simulate_state_exit(
    *,
    scenario_id: str,
    entry_price: float,
    fade_price: float,
    fade_ts: float,
    mfe_at_fade: float,
    fade_momentum: Optional[float],
    after_ticks: Sequence[TickEvent],
    mode: str,
    vwap_available: bool = False,
    vwap_at_fade: Optional[float] = None,
) -> SimResult:
    """Event-driven simulation; no fixed-second exit."""
    peak_price = fade_price
    post_low = fade_price
    peak_pnl = max(pnl_pct(entry_price, fade_price), mfe_at_fade)
    new_high_since_fade = False
    mfe_updated_since_fade = False
    reaccel_seen = False
    state_log: list[str] = ["fade_watch"]
    ticks = 0

    if not after_ticks:
        p = pnl_pct(entry_price, fade_price)
        return SimResult(
            scenario_id=scenario_id,
            exit_ts=fade_ts,
            exit_price=fade_price,
            exit_pnl=p,
            exit_reason="no_post_fade_ticks",
            ticks_observed=0,
            peak_mfe_after_fade=peak_pnl,
            reacceleration_detected=False,
            state_log=state_log,
        )

    for tick in after_ticks:
        ticks += 1
        px = tick.price
        pnl = pnl_pct(entry_price, px)
        mom = tick.momentum

        prev_peak = peak_price
        if px > peak_price:
            peak_price = px
        if px < post_low:
            post_low = px

        new_high_tick = px > prev_peak + 1e-9
        if new_high_tick and px > fade_price:
            new_high_since_fade = True

        mfe_updated_tick = pnl > peak_pnl + 1e-9
        if mfe_updated_tick:
            peak_pnl = pnl
            mfe_updated_since_fade = True

        vwap_above: Optional[bool] = None
        if vwap_available and vwap_at_fade is not None:
            vwap_above = px >= vwap_at_fade

        reaccel = _reaccel_score(
            price=px,
            fade_price=fade_price,
            peak_price=peak_price,
            pnl=pnl,
            peak_pnl=peak_pnl,
            momentum=mom,
            fade_momentum=fade_momentum,
            new_high_this_tick=new_high_tick,
            mfe_updated_this_tick=mfe_updated_tick,
            vwap_above=vwap_above,
        )
        reacceleration_detected = reaccel >= REACCEL_MIN_SIGNALS
        if reacceleration_detected:
            reaccel_seen = True
            state_log.append("continue_hold:reacceleration")

        giveback_exceeded = peak_pnl > PNL_EPS and pnl <= peak_pnl * (1.0 - GIVEBACK_FRAC)

        broke_fade_low = px < fade_price - 1e-9 and px <= post_low + 1e-9 and post_low < fade_price
        vwap_break = vwap_available and vwap_at_fade is not None and px < vwap_at_fade
        momentum_down = (
            mom is not None
            and fade_momentum is not None
            and mom < fade_momentum - MOMENTUM_EPS
        )
        no_new_high_momentum_down = (not new_high_since_fade) and momentum_down
        breakdown = (
            broke_fade_low
            or vwap_break
            or (px < fade_price and not mfe_updated_since_fade and momentum_down)
        )

        exit_reason: Optional[str] = None

        if mode == "giveback_only":
            if giveback_exceeded:
                exit_reason = "giveback_exceeded"
        elif mode == "reaccel_giveback":
            if breakdown:
                exit_reason = "breakdown_detected"
            elif giveback_exceeded and not reacceleration_detected:
                exit_reason = "giveback_exceeded"
            elif no_new_high_momentum_down and not reaccel_seen:
                exit_reason = "no_new_high_and_momentum_down"
        else:  # state_based full
            if breakdown:
                exit_reason = "breakdown_detected"
            elif giveback_exceeded:
                exit_reason = "giveback_exceeded"
            elif no_new_high_momentum_down:
                exit_reason = "no_new_high_and_momentum_down"

        if exit_reason:
            state_log.append(f"exit:{exit_reason}")
            return SimResult(
                scenario_id=scenario_id,
                exit_ts=tick.ts,
                exit_price=px,
                exit_pnl=pnl,
                exit_reason=exit_reason,
                ticks_observed=ticks,
                peak_mfe_after_fade=peak_pnl,
                reacceleration_detected=reaccel_seen,
                state_log=state_log,
            )

    last = after_ticks[-1]
    state_log.append("exit:observation_window_end")
    return SimResult(
        scenario_id=scenario_id,
        exit_ts=last.ts,
        exit_price=last.price,
        exit_pnl=pnl_pct(entry_price, last.price),
        exit_reason="observation_window_end",
        ticks_observed=ticks,
        peak_mfe_after_fade=peak_pnl,
        reacceleration_detected=reaccel_seen,
        state_log=state_log,
    )


def build_fade_trade_contexts(session_dirs: Sequence[Path]) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []

    for sdir in session_dirs:
        sdir = Path(sdir)
        trades_raw = load_structural_trades(sdir / "structural_trades.csv")
        fade_trades = [t for t in trades_raw if is_fade_trade(t)]
        if not fade_trades:
            continue
        session_id = (
            str(sdir.relative_to(sdir.parent.parent)) if sdir.parent.parent else sdir.name
        )
        events_csv = sdir / "small_paper_events.csv"
        end_ts = session_end_ts_from_trades(trades_raw)
        event_index = load_session_event_index(events_csv)

        for t in fade_trades:
            sym = str(t.get("symbol") or "")
            entry_ts = parse_ts(str(t.get("entry_time") or ""))
            close_ts = parse_ts(str(t.get("close_time") or ""))
            entry_px = as_float(t.get("entry_price")) or 0.0
            fade_px = as_float(t.get("close_price")) or entry_px
            baseline_pnl = as_float(t.get("realized_pnl_pct")) or 0.0
            mfe_at_fade = as_float(t.get("mfe_pct")) or 0.0

            stream = load_symbol_event_stream(
                events_csv,
                sym,
                start_ts=entry_ts,
                end_ts=end_ts,
                index=event_index,
            )
            at_fade, after = _fade_context(stream, close_ts)
            fade_mom = at_fade.momentum if at_fade else None

            contexts.append(
                {
                    "session_id": session_id,
                    "symbol": sym,
                    "entry_time": t.get("entry_time"),
                    "close_time": t.get("close_time"),
                    "exit_reason": t.get("close_reason"),
                    "entry_price": entry_px,
                    "fade_price": fade_px,
                    "fade_ts": close_ts,
                    "mfe_at_fade": mfe_at_fade,
                    "fade_momentum": fade_mom,
                    "baseline_pnl": baseline_pnl,
                    "after_ticks": after,
                    "vwap_available": False,
                    "vwap_at_fade": None,
                    "continuation_quality_score": as_float(t.get("continuation_quality_score")),
                    "had_take_before_exit": str(t.get("had_take_before_exit") or "").lower()
                    in ("true", "1", "yes"),
                }
            )

    return contexts


def run_scenarios(contexts: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    scenario_defs = (
        ("A_current", "current_immediate_exit", "current"),
        ("B_state_fade_watch", "state_based_fade_watch", "state_based"),
        ("C_mfe_giveback_only", "mfe_giveback_only", "giveback_only"),
        ("D_reaccel_giveback", "reacceleration_plus_giveback", "reaccel_giveback"),
    )

    paths: list[dict[str, Any]] = []
    results_by_scenario: dict[str, list[SimResult]] = {s[0]: [] for s in scenario_defs}

    for ctx in contexts:
        baseline = float(ctx["baseline_pnl"])
        entry_px = float(ctx["entry_price"])
        fade_px = float(ctx["fade_price"])
        fade_ts = float(ctx["fade_ts"])

        for sid, label, mode in scenario_defs:
            if mode == "current":
                sim = SimResult(
                    scenario_id=sid,
                    exit_ts=fade_ts,
                    exit_price=fade_px,
                    exit_pnl=baseline,
                    exit_reason="immediate_fade_exit",
                    ticks_observed=0,
                    peak_mfe_after_fade=float(ctx["mfe_at_fade"] or 0),
                    reacceleration_detected=False,
                    state_log=["immediate_exit"],
                )
            else:
                sim = simulate_state_exit(
                    scenario_id=sid,
                    entry_price=entry_px,
                    fade_price=fade_px,
                    fade_ts=fade_ts,
                    mfe_at_fade=float(ctx["mfe_at_fade"] or 0),
                    fade_momentum=ctx.get("fade_momentum"),
                    after_ticks=ctx.get("after_ticks") or [],
                    mode=mode,
                    vwap_available=bool(ctx.get("vwap_available")),
                    vwap_at_fade=ctx.get("vwap_at_fade"),
                )

            results_by_scenario[sid].append(sim)
            hold_sec = max(0.0, sim.exit_ts - fade_ts)
            paths.append(
                {
                    "session_id": ctx.get("session_id"),
                    "symbol": ctx.get("symbol"),
                    "entry_time": ctx.get("entry_time"),
                    "close_time": ctx.get("close_time"),
                    "scenario_id": sid,
                    "scenario_label": label,
                    "baseline_pnl": baseline,
                    "exit_pnl": sim.exit_pnl,
                    "delta_vs_baseline": round(sim.exit_pnl - baseline, 4),
                    "exit_reason": sim.exit_reason,
                    "exit_ts_offset_sec": round(hold_sec, 1),
                    "ticks_observed": sim.ticks_observed,
                    "peak_mfe_after_fade": sim.peak_mfe_after_fade,
                    "reacceleration_detected": sim.reacceleration_detected,
                    "state_log": " | ".join(sim.state_log),
                    "worsened_vs_baseline": sim.exit_pnl < baseline,
                }
            )

    summaries: list[dict[str, Any]] = []
    for sid, label, _ in scenario_defs:
        sims = results_by_scenario[sid]
        n = len(sims)
        pnls = [s.exit_pnl for s in sims]
        baselines = [float(c["baseline_pnl"]) for c in contexts]
        worsened = sum(1 for s, b in zip(sims, baselines) if s.exit_pnl < b)
        wins = sum(1 for p in pnls if p > 0)
        holds = [max(0.0, s.exit_ts - float(c["fade_ts"])) for s, c in zip(sims, contexts)]
        summaries.append(
            {
                "scenario_id": sid,
                "scenario_label": label,
                "trade_count": n,
                "total_pnl": round(sum(pnls), 4),
                "avg_pnl": round(statistics.mean(pnls), 4) if pnls else None,
                "win_rate": round(wins / n, 4) if n else None,
                "worsened_vs_A_rate": round(worsened / n, 4) if n else None,
                "worsened_vs_A_count": worsened,
                "avg_hold_after_fade_sec": round(statistics.mean(holds), 1) if holds else None,
                "median_hold_after_fade_sec": round(statistics.median(holds), 1) if holds else None,
                "reacceleration_detected_rate": round(
                    sum(1 for s in sims if s.reacceleration_detected) / n, 4
                )
                if n
                else None,
                "delta_vs_A_total": round(sum(pnls) - sum(baselines), 4),
            }
        )

    return paths, summaries


def determine_verdict(
    summaries: Sequence[Mapping[str, Any]],
    *,
    vwap_available_rate: float,
) -> tuple[str, list[str]]:
    notes: list[str] = []
    by_id = {s["scenario_id"]: s for s in summaries}
    a = by_id.get("A_current") or {}
    b = by_id.get("B_state_fade_watch") or {}
    d = by_id.get("D_reaccel_giveback") or {}

    a_total = float(a.get("total_pnl") or 0)
    b_total = float(b.get("total_pnl") or 0)
    d_total = float(d.get("total_pnl") or 0)
    b_worse = float(b.get("worsened_vs_A_rate") or 0)
    d_worse = float(d.get("worsened_vs_A_rate") or 0)

    notes.append(
        f"A={a_total:.4f} B={b_total:.4f} D={d_total:.4f} "
        f"B_worsened={b_worse:.1%} D_worsened={d_worse:.1%}"
    )

    best = max(
        (s for s in summaries if s["scenario_id"] != "A_current"),
        key=lambda s: float(s.get("total_pnl") or -1e9),
        default={},
    )
    best_id = best.get("scenario_id")
    best_total = float(best.get("total_pnl") or 0)
    best_worse = float(best.get("worsened_vs_A_rate") or 1)

    if vwap_available_rate < 0.05 and best_total <= a_total + 0.5:
        if best_total > a_total:
            return "need_vwap_or_volume_features", notes + ["vwap unavailable; marginal gain"]
        return "current_exit_best", notes + ["vwap unavailable"]

    if best_total > a_total + 1.0 and best_worse <= 0.35:
        return "state_based_fade_exit_promising", notes + [f"best={best_id}"]

    if best_total > a_total + 0.3 and best_worse > 0.4:
        return "state_signals_too_noisy", notes + [f"best={best_id} noisy worsened={best_worse:.1%}"]

    if best_total <= a_total:
        return "current_exit_best", notes

    if best_worse > 0.38:
        return "state_signals_too_noisy", notes

    return "state_based_fade_exit_promising", notes + [f"best={best_id} moderate gain"]


def analyze_state_based_fade_exit(session_dirs: Sequence[Path]) -> dict[str, Any]:
    contexts = build_fade_trade_contexts(session_dirs)
    paths, summaries = run_scenarios(contexts)
    vwap_rate = (
        sum(1 for c in contexts if c.get("vwap_available")) / len(contexts) if contexts else 0
    )
    verdict, notes = determine_verdict(summaries, vwap_available_rate=vwap_rate)

    exit_reason_counts: dict[str, dict[str, int]] = {}
    for p in paths:
        sid = str(p.get("scenario_id") or "")
        reason = str(p.get("exit_reason") or "")
        exit_reason_counts.setdefault(sid, {})
        exit_reason_counts[sid][reason] = exit_reason_counts[sid].get(reason, 0) + 1

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "fade_trade_count": len(contexts),
        "vwap_available_rate": round(vwap_rate, 4),
        "scenario_summaries": summaries,
        "exit_reason_counts": exit_reason_counts,
        "trade_paths": paths,
    }
