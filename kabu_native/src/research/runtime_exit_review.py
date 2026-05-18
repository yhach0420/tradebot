"""
Phase 54: TAKE / HOLD / EXIT runtime review (observer-only what-if — no new ENTRY/EXIT logic).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from unittest.mock import patch
from zoneinfo import ZoneInfo

from research.continuation_quality_ranking import continuation_components
from research.research_exit_criteria import _as_float
from research.runtime_pilot_policy_review import _build_price_index, _parse_dt, _parse_ts, _profit_factor
from research.small_paper_performance_review import (
    EXCESSIVE_HOLD_SEC,
    _build_trade_lifecycles,
    _load_events,
    _load_json,
)
from small_paper.discord_notifier import observer_tracker_config_from_pilot
from small_paper.observer_position_tracker import (
    OBSERVER_EXIT,
    OBSERVER_HOLD,
    OBSERVER_TAKE,
    ObserverPositionTracker,
)

JST = ZoneInfo("Asia/Tokyo")

TAKE_HORIZONS_SEC = (30, 60, 120, 300)
LONG_HOLD_SEC = 300.0
TRAILING_GIVEBACK_PCT = 0.18
QUALITY_DECAY_DROP = 0.08
MOMENTUM_FADE_RATIO = 0.85
EARLY_TAKE_EXTENDED_THRESHOLD = 0.05
MIN_PF_TARGET = 1.2

REC_TAKE_TOO_EARLY = "take_is_too_early"
REC_HOLD_TOO_LONG = "hold_is_too_long"
REC_EXIT_DECAY_MISSING = "exit_decay_missing"
REC_TRAILING_NEEDED = "trailing_needed"
REC_NO_CHANGE = "no_change"


@dataclass
class TradeRuntimePath:
    symbol: str
    entry_time: str
    exit_time: str
    entry_price: float
    entry_quality: float
    quality_tier: str
    ticks: list[dict[str, Any]] = field(default_factory=list)
    take: Optional[dict[str, Any]] = None
    holds: list[dict[str, Any]] = field(default_factory=list)
    exit: Optional[dict[str, Any]] = None
    observer_exit_reason: str = ""
    virtual_hold_pnl_pct: float = 0.0


def _trade_key(symbol: str, entry_time: str) -> tuple[str, str]:
    return symbol, entry_time


def _pnl_pct(entry: float, price: float) -> float:
    if entry <= 0:
        return 0.0
    return round((price - entry) / entry * 100.0, 4)


def _max_upside_horizons(
    entry_px: float,
    take_ts: float,
    price_series: Sequence[tuple[float, float]],
) -> dict[str, Optional[float]]:
    out: dict[str, Optional[float]] = {}
    for h in TAKE_HORIZONS_SEC:
        end = take_ts + h
        prices = [px for ts, px in price_series if take_ts <= ts <= end]
        if entry_px > 0 and prices:
            out[f"max_upside_{h}s_pct"] = round(max((p - entry_px) / entry_px * 100.0 for p in prices), 4)
        else:
            out[f"max_upside_{h}s_pct"] = None
    return out


def _vwap_break_proxy(ticks: Sequence[Mapping[str, Any]], entry_px: float) -> bool:
    """Proxy when VWAP not on PUSH: price below entry after MFE > 0.1%."""
    peak_pnl = 0.0
    for t in ticks:
        px = _as_float(t.get("price")) or 0.0
        pnl = _pnl_pct(entry_px, px)
        peak_pnl = max(peak_pnl, pnl)
        if peak_pnl > 0.1 and pnl < 0:
            return True
    return False


def _replay_trade_paths(
    events: Sequence[Mapping[str, Any]],
    *,
    pilot_config: Any,
    poll_interval_sec: float,
) -> tuple[list[TradeRuntimePath], dict[str, list[tuple[float, float]]]]:
    import small_paper.observer_position_tracker as ot

    price_index = _build_price_index(events)
    tracker = ObserverPositionTracker(observer_tracker_config_from_pilot(pilot_config))
    cfg = tracker.cfg
    ordered = sorted(events, key=lambda e: int(e.get("message_index") or 0))
    mono = [0.0]
    active: dict[str, TradeRuntimePath] = {}
    completed: list[TradeRuntimePath] = []

    def _mono() -> float:
        return mono[0]

    for ev in ordered:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        ent_raw = str(ev.get("entry_time") or "")
        as_of = _parse_dt(ent_raw) if ent_raw else datetime.now(JST)
        mono[0] += max(poll_interval_sec, 0.001)
        trade = dict(ev)
        price = _as_float(ev.get("current_price"))

        with patch.object(ot.time, "monotonic", _mono):
            with patch.object(ot, "datetime") as mdt:
                mdt.now.return_value = as_of
                mdt.combine = datetime.combine
                mdt.fromisoformat = datetime.fromisoformat

                if ev.get("event_type") == "accepted" and price and price > 0:
                    if tracker.has_open(sym):
                        old_key = next((k for k, p in active.items() if p.symbol == sym), None)
                        if old_key:
                            prev = active.pop(old_key)
                            prev.observer_exit_reason = "overlap_replaced_review"
                            prev.exit = {
                                "exit_time": ent_raw,
                                "exit_pnl_pct": _pnl_pct(prev.entry_price, float(price)),
                                "exit_reason": "overlap_replaced_review",
                            }
                            completed.append(prev)
                        tracker._positions.pop(sym, None)
                    key = _trade_key(sym, ent_raw)
                    path = TradeRuntimePath(
                        symbol=sym,
                        entry_time=ent_raw,
                        exit_time=str(ev.get("exit_time") or ""),
                        entry_price=float(price),
                        entry_quality=float(ev.get("continuation_quality_score") or 0),
                        quality_tier=str(ev.get("quality_tier") or ""),
                    )
                    active[key] = path
                    tracker.register_entry(
                        trade=trade,
                        payload=trade,
                        quality_tier=path.quality_tier,
                        entry_price=float(price),
                    )
                elif ev.get("event_type") == "candidate" and tracker.has_open(sym):
                    path = next((p for p in active.values() if p.symbol == sym), None)
                    if path and price and price > 0:
                        comps = continuation_components(trade)
                        pnl = _pnl_pct(path.entry_price, float(price))
                        path.ticks.append(
                            {
                                "ts": ent_raw,
                                "ts_epoch": _parse_ts(ent_raw),
                                "price": float(price),
                                "pnl_pct": pnl,
                                "quality": comps["continuation_quality"],
                                "momentum": comps["momentum_continuation"],
                                "favorable": comps["favorable_continuation"],
                                "rolling_mfe_pct": _as_float(trade.get("rolling_mfe_pct")),
                                "rolling_mae_pct": _as_float(trade.get("rolling_mae_pct")),
                            }
                        )
                    for oe in tracker.on_tick(
                        symbol=sym,
                        trade=trade,
                        payload=trade,
                        current_price=price,
                        session_bucket="",
                    ):
                        ctx = oe.context
                        if oe.kind == OBSERVER_TAKE and path and not path.take:
                            path.take = {
                                "take_time": ent_raw,
                                "take_ts": _parse_ts(ent_raw),
                                "take_pnl_pct": ctx.get("unrealized_pnl_pct"),
                                "take_quality": ctx.get("continuation_quality"),
                                "take_reason": ctx.get("take_reason"),
                                "peak_pnl_at_take": ctx.get("peak_pnl_pct"),
                            }
                        elif oe.kind == OBSERVER_HOLD and path:
                            path.holds.append(
                                {
                                    "hold_time": ent_raw,
                                    "hold_reason": ctx.get("hold_reason"),
                                    "quality": ctx.get("continuation_quality"),
                                    "hold_duration_sec": ctx.get("hold_duration_sec"),
                                }
                            )
                        elif oe.kind == OBSERVER_EXIT and path:
                            path.exit = {
                                "exit_time": ent_raw,
                                "exit_pnl_pct": ctx.get("realized_pnl_pct", ctx.get("unrealized_pnl_pct")),
                                "exit_reason": ctx.get("exit_reason"),
                                "exit_kind": ctx.get("continuation_breakdown"),
                            }
                            path.observer_exit_reason = str(ctx.get("exit_reason") or "")
                            key = _trade_key(sym, path.entry_time)
                            if key in active:
                                completed.append(active.pop(key))

    with patch.object(ot.time, "monotonic", _mono):
        with patch.object(ot, "datetime") as mdt:
            mdt.now.return_value = datetime.now(JST)
            mdt.combine = datetime.combine
            for oe in tracker.close_all(reason="runtime_review_end"):
                if oe.kind != OBSERVER_EXIT:
                    continue
                sym = oe.symbol
                for key, path in list(active.items()):
                    if path.symbol != sym:
                        continue
                    path.exit = {
                        "exit_time": oe.context.get("timestamp"),
                        "exit_pnl_pct": oe.context.get("realized_pnl_pct"),
                        "exit_reason": oe.context.get("exit_reason"),
                    }
                    path.observer_exit_reason = str(oe.context.get("exit_reason") or "session_end")
                    completed.append(active.pop(key))

    lifecycles = {(_trade_key(t.symbol, t.entry_time)): t for t in _build_trade_lifecycles(events)}
    for path in completed:
        lc = lifecycles.get(_trade_key(path.symbol, path.entry_time))
        if lc:
            path.virtual_hold_pnl_pct = lc.realized_pnl_pct

    return completed, price_index


def _enrich_take_rows(
    paths: Sequence[TradeRuntimePath],
    price_index: Mapping[str, list[tuple[float, float]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        if not path.take:
            continue
        take = path.take
        take_ts = float(take.get("take_ts") or 0)
        entry_px = path.entry_price
        take_pnl = float(take.get("take_pnl_pct") or 0)
        horizons = _max_upside_horizons(entry_px, take_ts, price_index.get(path.symbol, []))
        max_up = max((v or 0.0) for v in horizons.values())
        extended = max_up > take_pnl + EARLY_TAKE_EXTENDED_THRESHOLD
        exit_pnl = _as_float((path.exit or {}).get("exit_pnl_pct"))
        rows.append(
            {
                "symbol": path.symbol,
                "entry_time": path.entry_time,
                "take_time": take.get("take_time"),
                "take_quality": take.get("take_quality"),
                "take_pnl_pct": take_pnl,
                "take_reason": take.get("take_reason"),
                "peak_pnl_at_take": take.get("peak_pnl_at_take"),
                **horizons,
                "max_upside_after_take_pct": round(max_up, 4),
                "extended_after_take": extended,
                "exit_pnl_pct": exit_pnl,
                "virtual_hold_pnl_pct": path.virtual_hold_pnl_pct,
                "take_to_exit_delta": round((exit_pnl or 0) - take_pnl, 4) if exit_pnl is not None else None,
                "fell_after_take": bool(exit_pnl is not None and exit_pnl < take_pnl - 0.03),
            }
        )
    return rows


def _hold_rows(paths: Sequence[TradeRuntimePath]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        if not path.ticks:
            continue
        entry_px = path.entry_price
        peak_q = peak_pnl = peak_mom = 0.0
        giveback_at_exit = 0.0
        quality_decay = False
        mom_decay = False
        for t in path.ticks:
            q = float(t.get("quality") or 0)
            pnl = float(t.get("pnl_pct") or 0)
            mom = float(t.get("momentum") or 0)
            peak_q = max(peak_q, q)
            peak_pnl = max(peak_pnl, pnl)
            peak_mom = max(peak_mom, mom)
            if q <= peak_q - QUALITY_DECAY_DROP:
                quality_decay = True
            if peak_mom > 0 and mom < peak_mom * MOMENTUM_FADE_RATIO:
                mom_decay = True
        hold_sec = float(path.ticks[-1].get("ts_epoch", 0)) - float(path.ticks[0].get("ts_epoch", 0))
        if path.exit:
            hold_sec = max(
                hold_sec,
                _parse_ts(str(path.exit.get("exit_time") or path.entry_time)) - _parse_ts(path.entry_time),
            )
        exit_pnl = path.virtual_hold_pnl_pct
        giveback_at_exit = round(peak_pnl - exit_pnl, 4)
        long_hold = hold_sec >= LONG_HOLD_SEC
        profit_lost = peak_pnl > 0.15 and exit_pnl < peak_pnl * 0.5
        rows.append(
            {
                "symbol": path.symbol,
                "entry_time": path.entry_time,
                "hold_duration_sec": round(hold_sec, 1),
                "hold_notify_count": len(path.holds),
                "long_hold": long_hold,
                "exit_pnl_pct": exit_pnl,
                "peak_pnl_pct": round(peak_pnl, 4),
                "mfe_giveback_pct": giveback_at_exit,
                "quality_decay_seen": quality_decay,
                "momentum_decay_seen": mom_decay,
                "vwap_break_proxy": _vwap_break_proxy(path.ticks, entry_px),
                "profit_lost_to_hold": profit_lost,
                "entry_quality": path.entry_quality,
            }
        )
    return rows


def _exit_rows(paths: Sequence[TradeRuntimePath]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        reason = path.observer_exit_reason or "live_virtual_hold"
        exit_pnl = _as_float((path.exit or {}).get("exit_pnl_pct")) or path.virtual_hold_pnl_pct
        take_pnl = _as_float((path.take or {}).get("take_pnl_pct"))
        late_exit = bool(
            take_pnl is not None and exit_pnl is not None and exit_pnl > take_pnl + 0.05
        )
        rows.append(
            {
                "symbol": path.symbol,
                "entry_time": path.entry_time,
                "exit_time": (path.exit or {}).get("exit_time") or path.exit_time,
                "exit_reason": reason,
                "exit_pnl_pct": exit_pnl,
                "virtual_hold_pnl_pct": path.virtual_hold_pnl_pct,
                "had_take": bool(path.take),
                "take_pnl_pct": take_pnl,
                "late_exit_vs_take": late_exit,
                "stop_detected": reason == "stop_hit",
                "quality_decay_take": (path.take or {}).get("take_reason") == "quality_deterioration",
                "momentum_fade_take": (path.take or {}).get("take_reason") in (
                    "continuation_weakening",
                    "favorable_fade",
                ),
            }
        )
    return rows


def _simulate_exit_policy(path: TradeRuntimePath, policy: str, cfg: Any) -> float:
    if not path.ticks:
        return path.virtual_hold_pnl_pct
    entry = path.entry_price
    ent_ts = _parse_ts(path.entry_time)
    ex_ts = _parse_ts(path.exit_time) or ent_ts + 300
    peak_q = peak_pnl = peak_mom = 0.0
    take_price_target = entry * (1.0 + cfg.display_take_pct / 100.0)
    stop_price = entry * (1.0 - cfg.hard_stop_pct / 100.0)

    for t in path.ticks:
        ts = float(t.get("ts_epoch") or 0)
        if ts > ex_ts:
            break
        px = float(t.get("price") or entry)
        pnl = float(t.get("pnl_pct") or 0)
        q = float(t.get("quality") or 0)
        mom = float(t.get("momentum") or 0)
        peak_q = max(peak_q, q)
        peak_pnl = max(peak_pnl, pnl)
        peak_mom = max(peak_mom, mom)
        hold_sec = ts - ent_ts

        if px <= stop_price:
            return pnl

        if policy == "take_as_exit" and path.take:
            take_ts = float(path.take.get("take_ts") or 0)
            if ts >= take_ts:
                return float(path.take.get("take_pnl_pct") or pnl)

        if policy == "ignore_take":
            pass
        elif policy == "quality_decay_exit" and q <= peak_q - cfg.take_quality_drop:
            return pnl
        elif policy == "momentum_fade_exit" and peak_mom > 0 and mom < peak_mom * cfg.momentum_weaken_ratio:
            return pnl
        elif policy == "trailing_giveback_exit" and peak_pnl > 0 and pnl <= peak_pnl - TRAILING_GIVEBACK_PCT:
            return pnl
        elif policy.startswith("hold_max_"):
            limit = int(policy.replace("hold_max_", "").replace("s", ""))
            if hold_sec >= limit:
                return pnl

    if policy == "take_as_exit" and path.take:
        return float(path.take.get("take_pnl_pct") or path.virtual_hold_pnl_pct)
    if policy == "baseline_observer_exit":
        exit_pnl = _as_float((path.exit or {}).get("exit_pnl_pct"))
        return exit_pnl if exit_pnl is not None else path.virtual_hold_pnl_pct
    return path.virtual_hold_pnl_pct


def _whatif_grid(paths: Sequence[TradeRuntimePath], pilot_config: Any) -> list[dict[str, Any]]:
    cfg = observer_tracker_config_from_pilot(pilot_config)
    policies = [
        "baseline_observer_exit",
        "take_as_exit",
        "ignore_take",
        "quality_decay_exit",
        "momentum_fade_exit",
        "trailing_giveback_exit",
        "hold_max_180s",
        "hold_max_300s",
        "hold_max_600s",
    ]
    rows: list[dict[str, Any]] = []
    for policy in policies:
        pnls = [_simulate_exit_policy(p, policy, cfg) for p in paths]
        pf = _profit_factor(pnls)
        rows.append(
            {
                "policy": policy,
                "trade_count": len(pnls),
                "avg_pnl_pct": round(statistics.mean(pnls), 4) if pnls else None,
                "profit_factor": round(pf, 4) if pf not in (None, float("inf")) else pf,
                "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else None,
                "max_loss_pct": round(min(pnls), 4) if pnls else None,
            }
        )
    return rows


def _summarize_take(take_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(take_rows)
    if not n:
        return {"take_count": 0}
    extended = sum(1 for r in take_rows if r.get("extended_after_take"))
    early = sum(1 for r in take_rows if r.get("extended_after_take") and float(r.get("take_pnl_pct") or 0) < 0.2)
    fell = sum(1 for r in take_rows if r.get("fell_after_take"))
    by_reason = Counter(str(r.get("take_reason")) for r in take_rows)
    return {
        "take_count": n,
        "extended_after_take_rate_pct": round(100.0 * extended / n, 2),
        "early_take_rate_pct": round(100.0 * early / n, 2),
        "fell_after_take_rate_pct": round(100.0 * fell / n, 2),
        "avg_take_quality": round(
            statistics.mean(float(r.get("take_quality") or 0) for r in take_rows), 4
        ),
        "avg_take_pnl_pct": round(statistics.mean(float(r.get("take_pnl_pct") or 0) for r in take_rows), 4),
        "take_reason_distribution": dict(by_reason),
        "horizon_avg_max_upside": {
            f"{h}s": round(
                statistics.mean(float(r.get(f"max_upside_{h}s_pct") or 0) for r in take_rows), 4
            )
            for h in TAKE_HORIZONS_SEC
        },
    }


def _summarize_hold(hold_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not hold_rows:
        return {}
    holds = [float(r.get("hold_duration_sec") or 0) for r in hold_rows]
    longs = [r for r in hold_rows if r.get("long_hold")]
    lost = [r for r in hold_rows if r.get("profit_lost_to_hold")]
    return {
        "trade_count": len(hold_rows),
        "avg_hold_duration_sec": round(statistics.mean(holds), 1),
        "median_hold_duration_sec": round(statistics.median(holds), 1),
        "long_hold_rate_pct": round(100.0 * len(longs) / len(hold_rows), 2),
        "profit_lost_to_hold_rate_pct": round(100.0 * len(lost) / len(hold_rows), 2),
        "quality_decay_seen_rate_pct": round(
            100.0 * sum(1 for r in hold_rows if r.get("quality_decay_seen")) / len(hold_rows), 2
        ),
        "momentum_decay_seen_rate_pct": round(
            100.0 * sum(1 for r in hold_rows if r.get("momentum_decay_seen")) / len(hold_rows), 2
        ),
        "vwap_break_proxy_rate_pct": round(
            100.0 * sum(1 for r in hold_rows if r.get("vwap_break_proxy")) / len(hold_rows), 2
        ),
        "avg_mfe_giveback_pct": round(
            statistics.mean(float(r.get("mfe_giveback_pct") or 0) for r in hold_rows), 4
        ),
    }


def _summarize_exit(exit_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not exit_rows:
        return {}
    reasons = Counter(str(r.get("exit_reason") or "unknown") for r in exit_rows)
    vh = sum(1 for r in exit_rows if "virtual_hold" in str(r.get("exit_reason")))
    late = sum(1 for r in exit_rows if r.get("late_exit_vs_take"))
    return {
        "trade_count": len(exit_rows),
        "exit_reason_distribution": dict(reasons),
        "live_virtual_hold_rate_pct": round(100.0 * vh / len(exit_rows), 2),
        "late_exit_vs_take_rate_pct": round(100.0 * late / len(exit_rows), 2),
        "stop_hit_count": sum(1 for r in exit_rows if r.get("stop_detected")),
        "had_take_rate_pct": round(100.0 * sum(1 for r in exit_rows if r.get("had_take")) / len(exit_rows), 2),
    }


def _recommend_runtime_fix(
    take_summary: Mapping[str, Any],
    hold_summary: Mapping[str, Any],
    exit_summary: Mapping[str, Any],
    whatif: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    baseline = next((r for r in whatif if r["policy"] == "baseline_observer_exit"), {})
    base_pf = float(baseline.get("profit_factor") or 0)
    base_avg = float(baseline.get("avg_pnl_pct") or 0)

    scores: dict[str, float] = {
        REC_NO_CHANGE: 0.0,
        REC_TAKE_TOO_EARLY: 0.0,
        REC_HOLD_TOO_LONG: 0.0,
        REC_EXIT_DECAY_MISSING: 0.0,
        REC_TRAILING_NEEDED: 0.0,
    }

    ext_rate = float(take_summary.get("extended_after_take_rate_pct") or 0)
    if ext_rate >= 50:
        scores[REC_TAKE_TOO_EARLY] += 2.0
    if ext_rate >= 35:
        scores[REC_TAKE_TOO_EARLY] += 1.0

    avg_hold = float(hold_summary.get("avg_hold_duration_sec") or 0)
    long_hold_rate = float(hold_summary.get("long_hold_rate_pct") or 0)
    if avg_hold >= 280 or long_hold_rate >= 60:
        scores[REC_HOLD_TOO_LONG] += 1.5
    if float(hold_summary.get("profit_lost_to_hold_rate_pct") or 0) >= 15:
        scores[REC_HOLD_TOO_LONG] += 1.0

    decay = next((r for r in whatif if r["policy"] == "quality_decay_exit"), {})
    trail = next((r for r in whatif if r["policy"] == "trailing_giveback_exit"), {})
    if float(decay.get("profit_factor") or 0) > base_pf + 0.08:
        scores[REC_EXIT_DECAY_MISSING] += 2.0
    if float(trail.get("profit_factor") or 0) > base_pf + 0.08:
        scores[REC_TRAILING_NEEDED] += 2.0
    if float(decay.get("avg_pnl_pct") or 0) > base_avg + 0.01:
        scores[REC_EXIT_DECAY_MISSING] += 0.5
    if float(trail.get("avg_pnl_pct") or 0) > base_avg + 0.01:
        scores[REC_TRAILING_NEEDED] += 0.5

    vh_rate = float(exit_summary.get("live_virtual_hold_rate_pct") or 0)
    if vh_rate > 80 and scores[REC_EXIT_DECAY_MISSING] < 1:
        scores[REC_HOLD_TOO_LONG] += 0.5

    best = max(scores.items(), key=lambda x: x[1])
    fix = best[0] if best[1] >= 1.0 else REC_NO_CHANGE

    return {
        "recommend_runtime_fix": fix,
        "scores": scores,
        "baseline_pf": base_pf,
        "baseline_avg_pnl_pct": base_avg,
        "meets_pf_1_2_baseline": base_pf >= MIN_PF_TARGET,
        "note": "What-if policies are review-only; TAKE is notification not order execution.",
    }


def run_runtime_exit_review(
    session_dir: Path,
    *,
    pilot_config: Any,
    poll_interval_sec: Optional[float] = None,
) -> dict[str, Any]:
    session_dir = session_dir.resolve()
    summary = _load_json(session_dir / "small_paper_summary.json")
    events = _load_events(session_dir)
    interval = poll_interval_sec if poll_interval_sec is not None else float(
        summary.get("poll_interval_sec") or 5.0
    )

    paths, price_index = _replay_trade_paths(
        events, pilot_config=pilot_config, poll_interval_sec=interval
    )
    take_rows = _enrich_take_rows(paths, price_index)
    hold_rows = _hold_rows(paths)
    exit_rows = _exit_rows(paths)
    whatif_rows = _whatif_grid(paths, pilot_config)

    take_summary = _summarize_take(take_rows)
    hold_summary = _summarize_hold(hold_rows)
    exit_summary = _summarize_exit(exit_rows)
    recommendation = _recommend_runtime_fix(take_summary, hold_summary, exit_summary, whatif_rows)

    perf = _load_json(session_dir / "small_paper_performance_review.json")
    session_pf = (perf.get("accepted_trade_performance") or {}).get("profit_factor")

    return {
        "phase": 54,
        "mode": "runtime_exit_review",
        "what_if_only": True,
        "observer_only": True,
        "session_dir": str(session_dir),
        "policy_context": {
            "min_continuation_quality": summary.get("min_continuation_quality", 0.7),
            "max_concurrent_positions": summary.get("max_concurrent_positions", 3),
            "policy_label": summary.get("policy_label"),
            "session_observed_pf": session_pf,
        },
        "take_review": take_summary,
        "hold_review": hold_summary,
        "exit_review": {
            **exit_summary,
            "note": "overlap_replaced_review counts are replay-only when same symbol re-accepts before prior virtual hold ends.",
        },
        "exit_policy_whatif": whatif_rows,
        "recommendation": recommendation,
        "_take_path_rows": take_rows,
        "_hold_path_rows": hold_rows,
        "_exit_path_rows": exit_rows,
    }


def write_runtime_exit_review(session_dir: Path, review: Mapping[str, Any]) -> dict[str, Path]:
    session_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    public = {k: v for k, v in review.items() if not k.startswith("_")}
    json_path = session_dir / "runtime_exit_review.json"
    json_path.write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    paths["json"] = json_path

    for key, filename in (
        ("_take_path_rows", "take_path_review.csv"),
        ("_hold_path_rows", "hold_path_review.csv"),
        ("_exit_path_rows", "exit_path_review.csv"),
    ):
        rows = review.get(key) or []
        if rows:
            p = session_dir / filename
            with p.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
            paths[filename.replace(".csv", "")] = p

    whatif = review.get("exit_policy_whatif") or []
    if whatif:
        p = session_dir / "exit_policy_whatif.csv"
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(whatif[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(whatif)
        paths["whatif_csv"] = p

    return paths


def build_and_write_runtime_exit_review(
    session_dir: Path,
    *,
    pilot_config: Any,
    poll_interval_sec: Optional[float] = None,
) -> dict[str, Any]:
    review = run_runtime_exit_review(
        session_dir, pilot_config=pilot_config, poll_interval_sec=poll_interval_sec
    )
    paths = write_runtime_exit_review(session_dir, review)
    public = {k: v for k, v in review.items() if not k.startswith("_")}
    public["output_files"] = {k: str(v) for k, v in paths.items()}
    paths["json"].write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return public
