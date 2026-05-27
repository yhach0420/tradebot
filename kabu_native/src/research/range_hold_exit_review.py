"""
Phase 130: Range-hold vs breakdown classification for fade exits (review / what-if only).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.mfe_mae_exit_review import (
    as_float,
    load_structural_trades,
    parse_ts,
    pnl_pct,
    session_end_ts_from_trades,
)
from research.state_based_fade_exit_review import (
    GIVEBACK_FRAC,
    MOMENTUM_EPS,
    PNL_EPS,
    REACCEL_MIN_SIGNALS,
    TickEvent,
    _fade_context,
    _reaccel_score,
    load_session_event_index,
    load_symbol_event_stream,
    simulate_state_exit,
)
from research.fade_exit_replay import is_fade_trade

IMPROVE_EPS = 0.001
MIN_TICKS_FOR_CLASS = 2
FADE_PRICE_BREAK_EPS = 0.0005  # 0.05% below fade_price
RECENT_LOW_BREAK_EPS = 0.0005
NOISY_FLIP_THRESHOLD = 4
MFE_GATE = 0.15
WORSENED_TOLERANCE = 0.35


@dataclass
class FadeSnapshot:
    fade_price: float
    fade_pnl: float
    fade_mfe: float
    fade_mae: float
    recent_high: float
    recent_low: float
    peak_pnl: float
    momentum_at_fade: Optional[float]
    quality: Optional[float]
    hold_sec: float


@dataclass
class PathSignals:
    ticks_observed: int = 0
    range_low_intact_ticks: int = 0
    fade_price_intact_ticks: int = 0
    small_giveback_ticks: int = 0
    high_zone_ticks: int = 0
    new_high_after_fade: bool = False
    new_mfe_created: bool = False
    momentum_stable_ticks: int = 0
    recent_low_broken: bool = False
    fade_price_broken: bool = False
    giveback_exceeded: bool = False
    lower_low_sequence: bool = False
    momentum_redecline_ticks: int = 0
    pnl_turned_negative: bool = False
    reacceleration_detected: bool = False
    breakdown_at_fade: bool = False
    state_flips: int = 0
    first_breakdown_tick: int = 0
    peak_mfe_after_fade: float = 0.0
    last_pnl: float = 0.0
    last_price: float = 0.0
    last_ts: float = 0.0


def _snapshot_at_fade(
    stream: Sequence[TickEvent],
    *,
    fade_ts: float,
    entry_price: float,
    entry_ts: float,
    fade_price: float,
    mfe_at_fade: float,
    mae_at_fade: float,
    fade_momentum: Optional[float],
    quality: Optional[float],
) -> FadeSnapshot:
    pre = [e for e in stream if entry_ts <= e.ts <= fade_ts]
    prices = [e.price for e in pre] if pre else [fade_price]
    recent_high = max(prices)
    recent_low = min(prices)
    peak_pnl = max(pnl_pct(entry_price, p) for p in prices)
    fade_pnl = pnl_pct(entry_price, fade_price)
    return FadeSnapshot(
        fade_price=fade_price,
        fade_pnl=fade_pnl,
        fade_mfe=mfe_at_fade,
        fade_mae=mae_at_fade,
        recent_high=recent_high,
        recent_low=recent_low,
        peak_pnl=peak_pnl,
        momentum_at_fade=fade_momentum,
        quality=quality,
        hold_sec=max(0.0, fade_ts - entry_ts),
    )


def _breakdown_on_tick(
    *,
    px: float,
    pnl: float,
    mom: Optional[float],
    fade_momentum: Optional[float],
    fade_price: float,
    recent_low: float,
    peak_pnl: float,
    post_low: float,
    prev_post_low: float,
    new_high_since_fade: bool,
) -> bool:
    broke_recent_low = px < recent_low * (1.0 - RECENT_LOW_BREAK_EPS)
    broke_fade = px < fade_price * (1.0 - FADE_PRICE_BREAK_EPS)
    giveback = peak_pnl > PNL_EPS and pnl <= peak_pnl * (1.0 - GIVEBACK_FRAC)
    lower_low = px <= post_low + 1e-9 and post_low < prev_post_low - 1e-9
    mom_down = (
        mom is not None
        and fade_momentum is not None
        and mom < fade_momentum - MOMENTUM_EPS
        and not new_high_since_fade
    )
    pnl_neg = pnl < -PNL_EPS
    return broke_recent_low or broke_fade or giveback or lower_low or (mom_down and pnl_neg)


def _range_hold_on_tick(
    *,
    px: float,
    pnl: float,
    mom: Optional[float],
    fade_momentum: Optional[float],
    fade_price: float,
    recent_low: float,
    peak_pnl: float,
    new_high_since_fade: bool,
    mfe_updated: bool,
) -> bool:
    low_intact = px >= recent_low * (1.0 - RECENT_LOW_BREAK_EPS)
    fade_intact = px >= fade_price * (1.0 - FADE_PRICE_BREAK_EPS)
    small_giveback = peak_pnl <= PNL_EPS or pnl >= peak_pnl * (1.0 - GIVEBACK_FRAC * 0.5)
    high_zone = px >= fade_price * (1.0 - FADE_PRICE_BREAK_EPS) or new_high_since_fade
    mom_stable = (
        mom is None
        or fade_momentum is None
        or mom >= fade_momentum - MOMENTUM_EPS
        or new_high_since_fade
        or mfe_updated
    )
    return low_intact and fade_intact and small_giveback and high_zone and mom_stable


def analyze_post_fade_path(
    *,
    entry_price: float,
    snap: FadeSnapshot,
    fade_ts: float,
    after_ticks: Sequence[TickEvent],
) -> PathSignals:
    sig = PathSignals(
        peak_mfe_after_fade=max(snap.fade_mfe, snap.fade_pnl),
        last_pnl=snap.fade_pnl,
        last_price=snap.fade_price,
        last_ts=fade_ts,
    )

    if not after_ticks:
        sig.breakdown_at_fade = _breakdown_on_tick(
            px=snap.fade_price,
            pnl=snap.fade_pnl,
            mom=snap.momentum_at_fade,
            fade_momentum=snap.momentum_at_fade,
            fade_price=snap.fade_price,
            recent_low=snap.recent_low,
            peak_pnl=snap.peak_pnl,
            post_low=snap.fade_price,
            prev_post_low=snap.fade_price,
            new_high_since_fade=False,
        )
        return sig

    peak_price = snap.fade_price
    post_low = snap.fade_price
    peak_pnl = max(snap.peak_pnl, snap.fade_pnl)
    new_high_since_fade = False
    mfe_updated = False
    prev_state: Optional[str] = None

    sig.breakdown_at_fade = _breakdown_on_tick(
        px=snap.fade_price,
        pnl=snap.fade_pnl,
        mom=snap.momentum_at_fade,
        fade_momentum=snap.momentum_at_fade,
        fade_price=snap.fade_price,
        recent_low=snap.recent_low,
        peak_pnl=snap.peak_pnl,
        post_low=snap.fade_price,
        prev_post_low=snap.fade_price,
        new_high_since_fade=False,
    )

    for i, tick in enumerate(after_ticks, start=1):
        sig.ticks_observed = i
        px = tick.price
        pnl = pnl_pct(entry_price, px)
        mom = tick.momentum
        prev_post_low = post_low
        prev_peak = peak_price

        if px > peak_price:
            peak_price = px
        if px < post_low:
            post_low = px
        if pnl > peak_pnl + 1e-9:
            peak_pnl = pnl
            mfe_updated = True
            sig.new_mfe_created = True

        new_high_tick = px > prev_peak + 1e-9
        if new_high_tick and px > snap.fade_price:
            new_high_since_fade = True
            sig.new_high_after_fade = True

        if _range_hold_on_tick(
            px=px,
            pnl=pnl,
            mom=mom,
            fade_momentum=snap.momentum_at_fade,
            fade_price=snap.fade_price,
            recent_low=snap.recent_low,
            peak_pnl=peak_pnl,
            new_high_since_fade=new_high_since_fade,
            mfe_updated=mfe_updated,
        ):
            sig.range_low_intact_ticks += 1
            sig.fade_price_intact_ticks += 1
            sig.small_giveback_ticks += 1
            sig.high_zone_ticks += 1
            sig.momentum_stable_ticks += 1
            cur = "range_hold"
        else:
            cur = "other"

        bd = _breakdown_on_tick(
            px=px,
            pnl=pnl,
            mom=mom,
            fade_momentum=snap.momentum_at_fade,
            fade_price=snap.fade_price,
            recent_low=snap.recent_low,
            peak_pnl=peak_pnl,
            post_low=post_low,
            prev_post_low=prev_post_low,
            new_high_since_fade=new_high_since_fade,
        )
        if bd:
            if not sig.recent_low_broken and px < snap.recent_low * (1.0 - RECENT_LOW_BREAK_EPS):
                sig.recent_low_broken = True
            if not sig.fade_price_broken and px < snap.fade_price * (1.0 - FADE_PRICE_BREAK_EPS):
                sig.fade_price_broken = True
            if not sig.giveback_exceeded and peak_pnl > PNL_EPS and pnl <= peak_pnl * (1.0 - GIVEBACK_FRAC):
                sig.giveback_exceeded = True
            if post_low < prev_post_low - 1e-9:
                sig.lower_low_sequence = True
            if pnl < -PNL_EPS:
                sig.pnl_turned_negative = True
            if (
                mom is not None
                and snap.momentum_at_fade is not None
                and mom < snap.momentum_at_fade - MOMENTUM_EPS
            ):
                sig.momentum_redecline_ticks += 1
            if sig.first_breakdown_tick == 0:
                sig.first_breakdown_tick = i
            cur = "breakdown"

        if prev_state and cur != prev_state and cur != "other":
            sig.state_flips += 1
        if cur in ("range_hold", "breakdown"):
            prev_state = cur

        reaccel = _reaccel_score(
            price=px,
            fade_price=snap.fade_price,
            peak_price=peak_price,
            pnl=pnl,
            peak_pnl=peak_pnl,
            momentum=mom,
            fade_momentum=snap.momentum_at_fade,
            new_high_this_tick=new_high_tick,
            mfe_updated_this_tick=mfe_updated,
            vwap_above=None,
        )
        if reaccel >= REACCEL_MIN_SIGNALS:
            sig.reacceleration_detected = True

        sig.peak_mfe_after_fade = max(sig.peak_mfe_after_fade, peak_pnl)
        sig.last_pnl = pnl
        sig.last_price = px
        sig.last_ts = tick.ts

    return sig


def classify_path(sig: PathSignals) -> str:
    if sig.ticks_observed < MIN_TICKS_FOR_CLASS:
        return "insufficient_ticks"
    if sig.reacceleration_detected and sig.new_high_after_fade:
        return "reacceleration"
    if sig.breakdown_at_fade and sig.first_breakdown_tick <= 1:
        return "breakdown"
    if sig.state_flips >= NOISY_FLIP_THRESHOLD:
        return "noisy"

    range_score = (
        (1 if sig.range_low_intact_ticks >= sig.ticks_observed * 0.5 else 0)
        + (1 if sig.fade_price_intact_ticks >= sig.ticks_observed * 0.5 else 0)
        + (1 if sig.small_giveback_ticks >= sig.ticks_observed * 0.4 else 0)
        + (1 if sig.high_zone_ticks >= sig.ticks_observed * 0.4 else 0)
        + (1 if sig.momentum_stable_ticks >= sig.ticks_observed * 0.4 else 0)
    )
    breakdown_score = sum(
        [
            sig.recent_low_broken,
            sig.fade_price_broken,
            sig.giveback_exceeded,
            sig.lower_low_sequence,
            sig.pnl_turned_negative,
            sig.momentum_redecline_ticks >= max(1, sig.ticks_observed // 3),
        ]
    )

    if breakdown_score >= 2 and (sig.first_breakdown_tick <= 3 or sig.breakdown_at_fade):
        return "breakdown"
    if range_score >= 3 and breakdown_score <= 1:
        return "range_hold"
    if sig.reacceleration_detected:
        return "reacceleration"
    if breakdown_score >= range_score:
        return "breakdown"
    if range_score > breakdown_score:
        return "range_hold"
    return "noisy"


def simulate_range_hold_until_breakdown(
    *,
    entry_price: float,
    snap: FadeSnapshot,
    fade_ts: float,
    after_ticks: Sequence[TickEvent],
) -> tuple[float, float, str, float]:
    """Hold through range; exit on breakdown or session-end proxy."""
    if not after_ticks:
        return snap.fade_pnl, fade_ts, "insufficient_ticks", 0.0

    peak_pnl = max(snap.peak_pnl, snap.fade_pnl)
    post_low = snap.fade_price
    new_high_since_fade = False

    for tick in after_ticks:
        px = tick.price
        pnl = pnl_pct(entry_price, px)
        prev_post_low = post_low
        if px < post_low:
            post_low = px
        if px > snap.fade_price:
            new_high_since_fade = True
        if pnl > peak_pnl:
            peak_pnl = pnl

        if _breakdown_on_tick(
            px=px,
            pnl=pnl,
            mom=tick.momentum,
            fade_momentum=snap.momentum_at_fade,
            fade_price=snap.fade_price,
            recent_low=snap.recent_low,
            peak_pnl=peak_pnl,
            post_low=post_low,
            prev_post_low=prev_post_low,
            new_high_since_fade=new_high_since_fade,
        ):
            hold = max(0.0, tick.ts - fade_ts)
            return pnl, tick.ts, "breakdown_exit", hold

    last = after_ticks[-1]
    hold = max(0.0, last.ts - fade_ts)
    return pnl_pct(entry_price, last.price), last.ts, "session_end_proxy", hold


def build_enriched_contexts(session_dirs: Sequence[Path]) -> list[dict[str, Any]]:
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
            mae_at_fade = as_float(t.get("mae_pct")) or 0.0
            quality = as_float(t.get("continuation_quality_score"))

            stream = load_symbol_event_stream(
                events_csv,
                sym,
                start_ts=entry_ts,
                end_ts=end_ts,
                index=event_index,
            )
            at_fade, after = _fade_context(stream, close_ts)
            fade_mom = at_fade.momentum if at_fade else None

            snap = _snapshot_at_fade(
                stream,
                fade_ts=close_ts,
                entry_price=entry_px,
                entry_ts=entry_ts,
                fade_price=fade_px,
                mfe_at_fade=mfe_at_fade,
                mae_at_fade=mae_at_fade,
                fade_momentum=fade_mom,
                quality=quality,
            )

            sig = analyze_post_fade_path(
                entry_price=entry_px,
                snap=snap,
                fade_ts=close_ts,
                after_ticks=after,
            )
            path_class = classify_path(sig)

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
                    "snapshot": snap,
                    "path_signals": sig,
                    "path_class": path_class,
                    "quality": quality,
                }
            )

    return contexts


def _scenario_a(ctx: Mapping[str, Any]) -> tuple[float, str, float]:
    return float(ctx["baseline_pnl"]), "immediate_fade_exit", 0.0


def _scenario_b(ctx: Mapping[str, Any]) -> tuple[float, str, float]:
    snap: FadeSnapshot = ctx["snapshot"]
    pnl, _ts, reason, hold = simulate_range_hold_until_breakdown(
        entry_price=float(ctx["entry_price"]),
        snap=snap,
        fade_ts=float(ctx["fade_ts"]),
        after_ticks=ctx.get("after_ticks") or [],
    )
    return pnl, reason, hold


def _scenario_c(ctx: Mapping[str, Any]) -> tuple[float, str, float]:
    sig: PathSignals = ctx["path_signals"]
    if sig.breakdown_at_fade:
        return float(ctx["baseline_pnl"]), "breakdown_at_fade_immediate", 0.0
    sim = simulate_state_exit(
        scenario_id="C",
        entry_price=float(ctx["entry_price"]),
        fade_price=float(ctx["fade_price"]),
        fade_ts=float(ctx["fade_ts"]),
        mfe_at_fade=float(ctx["mfe_at_fade"] or 0),
        fade_momentum=ctx.get("fade_momentum"),
        after_ticks=ctx.get("after_ticks") or [],
        mode="state_based",
    )
    hold = max(0.0, sim.exit_ts - float(ctx["fade_ts"]))
    return sim.exit_pnl, sim.exit_reason, hold


def _scenario_d(ctx: Mapping[str, Any]) -> tuple[float, str, float]:
    mfe = float(ctx.get("mfe_at_fade") or 0)
    if mfe <= MFE_GATE:
        pnl, reason, hold = _scenario_a(ctx)
        return pnl, f"gate_skip:{reason}", hold
    return _scenario_b(ctx)


def run_scenarios(contexts: Sequence[Mapping[str, Any]]) -> tuple[list[dict], list[dict], list[dict]]:
    scenario_fns = {
        "A_current": ("current_immediate_exit", _scenario_a),
        "B_range_hold_until_breakdown": ("range_hold_continue_breakdown_exit", _scenario_b),
        "C_breakdown_only_immediate": ("breakdown_only_else_structural_continue", _scenario_c),
        "D_mfe_gate_range_hold": (f"mfe_gt_{MFE_GATE}_range_hold", _scenario_d),
    }

    paths: list[dict[str, Any]] = []
    results: dict[str, list[tuple[float, float]]] = {k: [] for k in scenario_fns}

    for ctx in contexts:
        snap: FadeSnapshot = ctx["snapshot"]
        sig: PathSignals = ctx["path_signals"]
        baseline = float(ctx["baseline_pnl"])

        for sid, (label, fn) in scenario_fns.items():
            pnl, reason, hold = fn(ctx)
            delta = round(pnl - baseline, 4)
            paths.append(
                {
                    "session_id": ctx.get("session_id"),
                    "symbol": ctx.get("symbol"),
                    "entry_time": ctx.get("entry_time"),
                    "close_time": ctx.get("close_time"),
                    "exit_reason_actual": ctx.get("exit_reason"),
                    "path_class": ctx.get("path_class"),
                    "scenario_id": sid,
                    "scenario_label": label,
                    "fade_price": snap.fade_price,
                    "fade_pnl": snap.fade_pnl,
                    "fade_mfe": snap.fade_mfe,
                    "fade_mae": snap.fade_mae,
                    "recent_high": snap.recent_high,
                    "recent_low": snap.recent_low,
                    "peak_pnl_at_fade": snap.peak_pnl,
                    "momentum_at_fade": snap.momentum_at_fade,
                    "quality": snap.quality,
                    "hold_sec_at_fade": snap.hold_sec,
                    "baseline_pnl": baseline,
                    "exit_pnl": round(pnl, 4),
                    "delta_vs_baseline": delta,
                    "sim_exit_reason": reason,
                    "hold_after_fade_sec": round(hold, 1),
                    "ticks_after_fade": sig.ticks_observed,
                    "new_high_after_fade": sig.new_high_after_fade,
                    "new_mfe_created": sig.new_mfe_created,
                    "reacceleration_detected": sig.reacceleration_detected,
                    "breakdown_at_fade": sig.breakdown_at_fade,
                    "recent_low_broken": sig.recent_low_broken,
                    "fade_price_broken": sig.fade_price_broken,
                    "giveback_exceeded": sig.giveback_exceeded,
                    "worsened_vs_baseline": delta < -IMPROVE_EPS,
                    "loss_expanded": pnl < baseline and pnl < 0,
                }
            )
            results[sid].append((pnl, baseline))

    summaries: list[dict[str, Any]] = []
    class_counts = {
        c: sum(1 for ctx in contexts if ctx.get("path_class") == c)
        for c in (
            "range_hold",
            "breakdown",
            "reacceleration",
            "noisy",
            "insufficient_ticks",
        )
    }

    a_total = sum(p for p, _ in results["A_current"])
    for sid, (label, _) in scenario_fns.items():
        pairs = results[sid]
        n = len(pairs)
        pnls = [p for p, _ in pairs]
        baselines = [b for _, b in pairs]
        worsened = sum(1 for p, b in zip(pnls, baselines) if p < b - IMPROVE_EPS)
        loss_exp = sum(1 for p, b in zip(pnls, baselines) if p < b and p < 0)
        holds = [
            float(row["hold_after_fade_sec"])
            for row in paths
            if row["scenario_id"] == sid
        ]
        total = round(sum(pnls), 4)
        summaries.append(
            {
                "scenario_id": sid,
                "scenario_label": label,
                "trade_count": n,
                "total_pnl": total,
                "avg_pnl": round(statistics.mean(pnls), 4) if pnls else None,
                "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4) if n else None,
                "worsened_rate": round(worsened / n, 4) if n else None,
                "worsened_count": worsened,
                "loss_expansion_rate": round(loss_exp / n, 4) if n else None,
                "loss_expansion_count": loss_exp,
                "median_hold_after_fade_sec": round(statistics.median(holds), 1) if holds else None,
                "improvement_vs_current": round(total - a_total, 4),
                "range_hold_count": class_counts.get("range_hold", 0),
                "breakdown_count": class_counts.get("breakdown", 0),
                "reacceleration_count": class_counts.get("reacceleration", 0),
                "noisy_count": class_counts.get("noisy", 0),
                "insufficient_ticks_count": class_counts.get("insufficient_ticks", 0),
            }
        )

    rule_rows = _build_rule_candidates(contexts, paths)
    return paths, summaries, rule_rows


def _build_rule_candidates(
    contexts: Sequence[Mapping[str, Any]],
    paths: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Counterfactual gates on path_class and mfe/pnl at fade."""
    b_by_key = {
        (p["session_id"], p["symbol"], p["entry_time"]): p
        for p in paths
        if p["scenario_id"] == "B_range_hold_until_breakdown"
    }
    rows: list[dict[str, Any]] = []

    def eval_rule(rule_id: str, mask_fn) -> None:
        matched = [ctx for ctx in contexts if mask_fn(ctx)]
        if not matched:
            return
        improved = worsened = 0
        total_delta = 0.0
        for ctx in matched:
            key = (ctx["session_id"], ctx["symbol"], ctx["entry_time"])
            row = b_by_key.get(key)
            if not row:
                continue
            delta = float(row["delta_vs_baseline"])
            total_delta += delta
            if delta > IMPROVE_EPS:
                improved += 1
            elif delta < -IMPROVE_EPS:
                worsened += 1
        decided = improved + worsened
        rows.append(
            {
                "rule_id": rule_id,
                "selected_trade_count": len(matched),
                "improved_count": improved,
                "worsened_count": worsened,
                "total_pnl_delta": round(total_delta, 4),
                "precision": round(improved / decided, 4) if decided else None,
                "coverage": round(len(matched) / len(contexts), 4) if contexts else None,
            }
        )

    eval_rule("path_class_range_hold", lambda c: c.get("path_class") == "range_hold")
    eval_rule("path_class_reacceleration", lambda c: c.get("path_class") == "reacceleration")
    eval_rule("path_class_breakdown", lambda c: c.get("path_class") == "breakdown")
    eval_rule("not_breakdown_at_fade", lambda c: not c["path_signals"].breakdown_at_fade)

    for mfe_thr in (0.05, 0.10, 0.15, 0.20):
        eval_rule(
            f"mfe_gt_{mfe_thr}_range_hold_path",
            lambda c, t=mfe_thr: float(c.get("mfe_at_fade") or 0) > t
            and c.get("path_class") in ("range_hold", "reacceleration"),
        )
        eval_rule(
            f"mfe_gt_{mfe_thr}",
            lambda c, t=mfe_thr: float(c.get("mfe_at_fade") or 0) > t,
        )

    for pnl_thr in (0.0, 0.05, 0.10):
        eval_rule(
            f"pnl_at_fade_gt_{pnl_thr}_range_hold",
            lambda c, t=pnl_thr: float(c["snapshot"].fade_pnl) > t
            and c.get("path_class") == "range_hold",
        )

    eval_rule(
        "mfe_gt_0.15_not_breakdown_at_fade",
        lambda c: float(c.get("mfe_at_fade") or 0) > 0.15
        and not c["path_signals"].breakdown_at_fade,
    )

    rows.sort(key=lambda r: float(r.get("total_pnl_delta") or -1e9), reverse=True)
    return rows


def determine_verdict(
    summaries: Sequence[Mapping[str, Any]],
    contexts: Sequence[Mapping[str, Any]],
    *,
    vwap_available_rate: float = 0.0,
) -> tuple[str, list[str]]:
    notes: list[str] = []
    by_id = {s["scenario_id"]: s for s in summaries}
    a = by_id.get("A_current") or {}
    b = by_id.get("B_range_hold_until_breakdown") or {}
    d = by_id.get("D_mfe_gate_range_hold") or {}

    a_total = float(a.get("total_pnl") or 0)
    notes.append(
        f"A={a_total:.4f} B={float(b.get('total_pnl') or 0):.4f} "
        f"D={float(d.get('total_pnl') or 0):.4f} "
        f"B_worsened={float(b.get('worsened_rate') or 0):.1%} "
        f"D_worsened={float(d.get('worsened_rate') or 0):.1%}"
    )

    class_counts = {
        c: sum(1 for ctx in contexts if ctx.get("path_class") == c) for c in (
            "range_hold", "breakdown", "reacceleration", "noisy", "insufficient_ticks"
        )
    }
    notes.append(f"classification={class_counts}")

    noisy_rate = class_counts.get("noisy", 0) / len(contexts) if contexts else 0
    if noisy_rate > 0.35:
        return "state_signals_too_noisy", notes + [f"noisy_rate={noisy_rate:.1%}"]

    candidates = [s for s in summaries if s["scenario_id"] != "A_current"]
    best = max(candidates, key=lambda s: float(s.get("total_pnl") or -1e9), default={})
    best_id = best.get("scenario_id")
    best_total = float(best.get("total_pnl") or 0)
    best_worse = float(best.get("worsened_rate") or 1)
    best_delta = float(best.get("improvement_vs_current") or 0)

    if vwap_available_rate < 0.05 and best_delta > 0 and best_worse > 0.40:
        return "need_more_features", notes + ["vwap/volume unavailable; high worsened with gain"]

    if best_total > a_total + 0.3 and best_worse <= WORSENED_TOLERANCE:
        return "range_hold_exit_promising", notes + [f"best={best_id} delta={best_delta:.4f}"]

    if best_total <= a_total:
        return "current_fade_exit_best", notes + [f"best={best_id} does not beat A"]

    if best_worse > WORSENED_TOLERANCE:
        return "state_signals_too_noisy", notes + [f"best={best_id} worsened={best_worse:.1%}"]

    if best_delta > 0:
        return "range_hold_exit_promising", notes + [f"best={best_id} marginal delta={best_delta:.4f}"]

    return "current_fade_exit_best", notes


def analyze_range_hold_exit(session_dirs: Sequence[Path]) -> dict[str, Any]:
    contexts = build_enriched_contexts(session_dirs)
    paths, summaries, rule_rows = run_scenarios(contexts)
    vwap_rate = 0.0  # not populated in live events (Phase126 finding)
    verdict, notes = determine_verdict(summaries, contexts, vwap_available_rate=vwap_rate)

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "fade_trade_count": len(contexts),
        "vwap_available_rate": vwap_rate,
        "path_class_counts": {
            c: sum(1 for ctx in contexts if ctx.get("path_class") == c)
            for c in (
                "range_hold",
                "breakdown",
                "reacceleration",
                "noisy",
                "insufficient_ticks",
            )
        },
        "scenario_summaries": summaries,
        "rule_candidates": rule_rows,
        "trade_paths": paths,
    }
