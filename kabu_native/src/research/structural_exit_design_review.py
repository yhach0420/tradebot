"""
Phase 59: Structural exit design review (analysis only — no pilot / ENTRY changes).

Explains structural_observer_v1 PF vs legacy VH PF; compares structure-only EXIT policies
and overlap handling without fixed horizons or virtual_hold_expired.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.continuation_quality_ranking import continuation_components
from research.research_exit_criteria import _as_float
from research.small_paper_performance_review import (
    _load_events,
    _load_json,
    _parse_dt,
    _parse_ts,
    _profit_factor,
)
from research.structural_exit_policies import (
    POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    simulate_structural_policy,
)
from research.structural_observer_review import (
    _pnl_pct,
    _session_end_time,
    replay_structural_observer_v1,
)
from small_paper.discord_notifier import observer_tracker_config_from_pilot

MIN_PF_TARGET = 1.2
GIVEBACK_FROM_MFE_MIN = 0.15
VWAP_BREAK_PEAK_PNL = 0.10
LATE_SPIKE_MFE_ENTRY = 0.50
LATE_SPIKE_LOSS_PNL = -0.30
CHURN_HOLD_SEC = 30.0
CHURN_MAX_TICKS = 2
TRAILING_GIVEBACK_PCT = 0.18
LOWER_HIGH_TICKS = 3

LOSS_BUCKETS = (
    "stop_hit",
    "overlap_replaced_review",
    "session_end",
    "quality_decay_missing",
    "momentum_decay_missing",
    "vwap_break_missing",
    "giveback_after_mfe",
    "duplicate_symbol_churn",
    "late_entry_after_spike",
    "adverse_expansion",
    "other_loss",
)

STRUCTURAL_EXIT_POLICIES = (
    "structural_observer_v1_baseline",
    "stop_only_exit",
    "quality_decay_exit",
    "momentum_fade_exit",
    "favorable_fade_exit",
    "vwap_break_exit",
    "mfe_giveback_exit",
    "adverse_expansion_exit",
    "lower_high_exit",
    "combined_structural_exit_v1",
    "duplicate_reject_policy",
    "overlap_ignore_policy",
)

REC_ADD_STRUCTURAL = "add_structural_exit_v1"
REC_FIX_OVERLAP = "fix_duplicate_overlap_first"
REC_ENTRY_QUALITY = "entry_quality_not_enough"
REC_OBSERVE = "continue_observation_only"


@dataclass
class EvalPath:
    symbol: str
    entry_time: str
    entry_ts: float
    entry_price: float
    entry_quality: float
    rolling_mfe_at_entry: float
    rolling_mae_at_entry: float
    close_reason: str = ""
    realized_pnl_pct: float = 0.0
    take_time: str = ""
    take_reason: str = ""
    take_pnl_pct: Optional[float] = None
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    ticks: list[dict[str, Any]] = field(default_factory=list)


def _load_structural_trades_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _vwap_break_on_ticks(ticks: Sequence[Mapping[str, Any]], entry_px: float) -> bool:
    peak_pnl = 0.0
    for t in ticks:
        pnl = float(t.get("pnl_pct") or 0)
        peak_pnl = max(peak_pnl, pnl)
        if peak_pnl > VWAP_BREAK_PEAK_PNL and pnl < 0:
            return True
    return False


def _lower_high_on_ticks(ticks: Sequence[Mapping[str, Any]]) -> bool:
    if len(ticks) < LOWER_HIGH_TICKS:
        return False
    prices = [float(t.get("price") or 0) for t in ticks[-LOWER_HIGH_TICKS:]]
    return all(prices[i] > prices[i + 1] for i in range(len(prices) - 1))


def _path_mfe_mae(ticks: Sequence[Mapping[str, Any]], entry_px: float) -> tuple[float, float]:
    if not ticks or entry_px <= 0:
        return 0.0, 0.0
    pnls = [float(t.get("pnl_pct") or 0) for t in ticks]
    return round(max(pnls), 4), round(min(pnls), 4)


def build_eval_paths(
    events: Sequence[Mapping[str, Any]],
    *,
    session_end: str,
) -> list[EvalPath]:
    """One EvalPath per gate accept; ticks until overlap close, structural exit, or session end."""
    ordered = sorted(events, key=lambda e: int(e.get("message_index") or 0))
    session_end_ts = _parse_ts(session_end)
    by_sym_accept: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in ordered:
        if e.get("event_type") == "accepted":
            by_sym_accept[str(e.get("symbol") or "")].append(dict(e))

    open_path: dict[str, EvalPath] = {}
    completed: list[EvalPath] = []

    def _close_path(sym: str, close_time: str, close_px: float, reason: str) -> None:
        p = open_path.pop(sym, None)
        if p is None:
            return
        p.close_reason = reason
        p.realized_pnl_pct = _pnl_pct(p.entry_price, close_px)
        p.mfe_pct, p.mae_pct = _path_mfe_mae(p.ticks, p.entry_price)
        completed.append(p)

    for ev in ordered:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        ent_raw = str(ev.get("entry_time") or "")
        ts = _parse_ts(ent_raw)
        price = _as_float(ev.get("current_price"))

        if ev.get("event_type") == "accepted" and price and price > 0:
            if sym in open_path:
                _close_path(sym, ent_raw, float(price), "overlap_replaced_review")
            open_path[sym] = EvalPath(
                symbol=sym,
                entry_time=ent_raw,
                entry_ts=ts,
                entry_price=float(price),
                entry_quality=float(ev.get("continuation_quality_score") or 0),
                rolling_mfe_at_entry=float(ev.get("rolling_mfe_pct") or 0),
                rolling_mae_at_entry=float(ev.get("rolling_mae_pct") or 0),
            )
        elif ev.get("event_type") == "candidate" and sym in open_path and price and price > 0:
            p = open_path[sym]
            if ts > session_end_ts:
                continue
            trade = dict(ev)
            comps = continuation_components(trade)
            pnl = _pnl_pct(p.entry_price, float(price))
            p.ticks.append(
                {
                    "ts": ent_raw,
                    "ts_epoch": ts,
                    "price": float(price),
                    "pnl_pct": pnl,
                    "quality": float(comps["continuation_quality"]),
                    "momentum": float(comps["momentum_continuation"]),
                    "favorable": float(comps["favorable_continuation"]),
                }
            )
            if not p.take_time:
                take_reason = _detect_take_reason(p, comps, float(price), pnl)
                if take_reason:
                    p.take_time = ent_raw
                    p.take_reason = take_reason
                    p.take_pnl_pct = pnl

    for sym, p in list(open_path.items()):
        close_px = float(p.ticks[-1]["price"]) if p.ticks else p.entry_price
        close_time = p.ticks[-1]["ts"] if p.ticks else session_end
        _close_path(sym, close_time, close_px, "session_end")

    return completed


def _detect_take_reason(
    path: EvalPath,
    comps: Mapping[str, float],
    price: float,
    pnl_pct: float,
    *,
    take_quality_drop: float = 0.08,
    momentum_ratio: float = 0.85,
    favorable_ratio: float = 0.85,
    display_take_pct: float = 4.0,
) -> str:
    peak_q = max((float(t.get("quality") or 0) for t in path.ticks), default=path.entry_quality)
    peak_mom = max((float(t.get("momentum") or 0) for t in path.ticks), default=0.0)
    peak_fav = max((float(t.get("favorable") or 0) for t in path.ticks), default=0.0)
    q = float(comps["continuation_quality"])
    if q <= peak_q - take_quality_drop:
        return "quality_deterioration"
    if comps["favorable_continuation"] < peak_fav * favorable_ratio and peak_fav > 0:
        return "favorable_fade"
    if comps["momentum_continuation"] < peak_mom * momentum_ratio and peak_mom > 0:
        return "continuation_weakening"
    take_target = path.entry_price * (1.0 + display_take_pct / 100.0)
    if price >= take_target:
        return "display_take_target_reached"
    if pnl_pct >= display_take_pct * 0.9:
        return "unrealized_pnl_near_take"
    return ""


def _classify_loss_row(trade: Mapping[str, Any], path: Optional[EvalPath]) -> dict[str, Any]:
    pnl = float(trade.get("realized_pnl_pct") or 0)
    close_reason = str(trade.get("close_reason") or "")
    mfe = float(trade.get("mfe_pct") or 0)
    mae = float(trade.get("mae_pct") or 0)
    hold = float(trade.get("hold_duration_sec") or 0)
    ticks_n = int(trade.get("tick_count") or 0)
    take_reason = str(trade.get("take_reason") or "")
    had_take = bool(trade.get("had_take_before_exit") or trade.get("take_time"))
    rolling_mfe = float(trade.get("rolling_mfe_at_entry") or 0)
    if path:
        rolling_mfe = path.rolling_mfe_at_entry or rolling_mfe
        ticks = path.ticks
    else:
        ticks = []

    tags: list[str] = []
    if pnl >= 0:
        primary = "winner"
    elif close_reason == "stop_hit":
        primary = "stop_hit"
    elif close_reason == "overlap_replaced_review":
        primary = "overlap_replaced_review"
    elif close_reason == "session_end":
        primary = "session_end"
    else:
        primary = "other_loss"

    if pnl < 0:
        if close_reason == "overlap_replaced_review" and hold <= CHURN_HOLD_SEC and ticks_n <= CHURN_MAX_TICKS:
            tags.append("duplicate_symbol_churn")
        if mfe >= GIVEBACK_FROM_MFE_MIN and pnl < 0:
            tags.append("giveback_after_mfe")
        if had_take and take_reason == "quality_deterioration" and close_reason != "stop_hit":
            tags.append("quality_decay_missing")
        if had_take and take_reason in ("continuation_weakening", "favorable_fade"):
            tags.append("momentum_decay_missing")
        if ticks and _vwap_break_on_ticks(ticks, float(trade.get("entry_price") or 0)):
            tags.append("vwap_break_missing")
        if rolling_mfe >= LATE_SPIKE_MFE_ENTRY and pnl <= LATE_SPIKE_LOSS_PNL:
            tags.append("late_entry_after_spike")
        if mae <= -0.50:
            tags.append("adverse_expansion")
        if ticks and _lower_high_on_ticks(ticks):
            tags.append("lower_high_lower_low_proxy")

    secondary = [t for t in tags if t != primary]
    return {
        "symbol": trade.get("symbol"),
        "entry_time": trade.get("entry_time"),
        "close_reason": close_reason,
        "realized_pnl_pct": pnl,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "hold_duration_sec": hold,
        "tick_count": ticks_n,
        "had_take_before_exit": had_take,
        "take_reason": take_reason,
        "primary_loss_driver": primary,
        "secondary_loss_tags": "|".join(secondary) if secondary else "",
        "all_loss_tags": "|".join([primary] + secondary) if pnl < 0 else "winner",
    }


def _simulate_tick_policy(path: EvalPath, policy: str, cfg: Any) -> tuple[float, str]:
    if not path.ticks:
        return path.realized_pnl_pct, "no_ticks"
    result = simulate_structural_policy(
        path.ticks,
        path.entry_price,
        POLICY_COMBINED_STRUCTURAL_EXIT_V1 if policy == "combined_structural_exit_v1" else policy,
        cfg,
        allow_session_end=True,
    )
    if result is None:
        return path.realized_pnl_pct, "no_ticks"
    return result


def _paths_overlap_ignore(events: Sequence[Mapping[str, Any]], session_end: str) -> list[float]:
    """Single position per symbol from first accept; no overlap close."""
    ordered = sorted(events, key=lambda e: int(e.get("message_index") or 0))
    session_end_ts = _parse_ts(session_end)
    open_path: dict[str, EvalPath] = {}
    pnls: list[float] = []

    for ev in ordered:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        ent_raw = str(ev.get("entry_time") or "")
        ts = _parse_ts(ent_raw)
        price = _as_float(ev.get("current_price"))
        if ev.get("event_type") == "accepted" and price and price > 0:
            if sym not in open_path:
                open_path[sym] = EvalPath(
                    symbol=sym,
                    entry_time=ent_raw,
                    entry_ts=ts,
                    entry_price=float(price),
                    entry_quality=0.0,
                    rolling_mfe_at_entry=0.0,
                    rolling_mae_at_entry=0.0,
                )
        elif ev.get("event_type") == "candidate" and sym in open_path and price and price > 0:
            if ts > session_end_ts:
                continue
            p = open_path[sym]
            pnl = _pnl_pct(p.entry_price, float(price))
            trade = dict(ev)
            comps = continuation_components(trade)
            p.ticks.append(
                {
                    "ts": ent_raw,
                    "ts_epoch": ts,
                    "price": float(price),
                    "pnl_pct": pnl,
                    "quality": float(comps["continuation_quality"]),
                    "momentum": float(comps["momentum_continuation"]),
                    "favorable": float(comps["favorable_continuation"]),
                }
            )

    for p in open_path.values():
        if p.ticks:
            pnls.append(float(p.ticks[-1].get("pnl_pct") or 0))
        else:
            pnls.append(0.0)
    return pnls


def _paths_duplicate_reject(events: Sequence[Mapping[str, Any]], session_end: str) -> list[EvalPath]:
    ordered = sorted(events, key=lambda e: int(e.get("message_index") or 0))
    session_end_ts = _parse_ts(session_end)
    open_sym: set[str] = set()
    paths: list[EvalPath] = []
    current: dict[str, EvalPath] = {}

    for ev in ordered:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        ent_raw = str(ev.get("entry_time") or "")
        ts = _parse_ts(ent_raw)
        price = _as_float(ev.get("current_price"))

        if ev.get("event_type") == "accepted" and price and price > 0:
            if sym in open_sym:
                continue
            open_sym.add(sym)
            current[sym] = EvalPath(
                symbol=sym,
                entry_time=ent_raw,
                entry_ts=ts,
                entry_price=float(price),
                entry_quality=float(ev.get("continuation_quality_score") or 0),
                rolling_mfe_at_entry=float(ev.get("rolling_mfe_pct") or 0),
                rolling_mae_at_entry=float(ev.get("rolling_mae_pct") or 0),
            )
        elif ev.get("event_type") == "candidate" and sym in current and price and price > 0:
            if ts > session_end_ts:
                continue
            p = current[sym]
            trade = dict(ev)
            comps = continuation_components(trade)
            pnl = _pnl_pct(p.entry_price, float(price))
            p.ticks.append(
                {
                    "ts": ent_raw,
                    "ts_epoch": ts,
                    "price": float(price),
                    "pnl_pct": pnl,
                    "quality": float(comps["continuation_quality"]),
                    "momentum": float(comps["momentum_continuation"]),
                    "favorable": float(comps["favorable_continuation"]),
                }
            )

    for p in current.values():
        if p.ticks:
            p.realized_pnl_pct = float(p.ticks[-1].get("pnl_pct") or 0)
        paths.append(p)
    return paths


def _overlap_review_rows(
    trades: Sequence[Mapping[str, Any]],
    path_by_key: dict[tuple[str, str], EvalPath],
    events: Sequence[Mapping[str, Any]],
    *,
    cfg: Any,
) -> list[dict[str, Any]]:
    overlaps = [t for t in trades if str(t.get("close_reason")) == "overlap_replaced_review"]
    rows: list[dict[str, Any]] = []

    by_sym_events: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for e in events:
        if e.get("event_type") != "candidate":
            continue
        sym = str(e.get("symbol") or "")
        px = _as_float(e.get("current_price"))
        if sym and px:
            by_sym_events[sym].append((_parse_ts(str(e.get("entry_time") or "")), float(px)))

    for t in overlaps:
        sym = str(t.get("symbol") or "")
        ent = str(t.get("entry_time") or "")
        close_t = str(t.get("close_time") or "")
        entry_px = float(t.get("entry_price") or 0)
        close_px = float(t.get("close_price") or 0)
        close_ts = _parse_ts(close_t)
        key = (sym, ent)
        path = path_by_key.get(key)
        as_exit_pnl = float(t.get("realized_pnl_pct") or 0)

        continue_pnl = as_exit_pnl
        if path and path.ticks:
            future = [tick for tick in path.ticks if float(tick.get("ts_epoch") or 0) >= close_ts]
            merged_ticks = list(path.ticks)
            if not future:
                future_prices = [(ts, px) for ts, px in by_sym_events.get(sym, []) if ts >= close_ts]
                if future_prices:
                    end_px = future_prices[-1][1]
                    continue_pnl = _pnl_pct(entry_px, end_px)
            else:
                end_px = float(future[-1].get("price") or close_px)
                continue_pnl = _pnl_pct(entry_px, end_px)

            p_continue = EvalPath(
                symbol=sym,
                entry_time=ent,
                entry_ts=_parse_ts(ent),
                entry_price=entry_px,
                entry_quality=0.0,
                rolling_mfe_at_entry=0.0,
                rolling_mae_at_entry=0.0,
                ticks=merged_ticks,
            )
            for ts, px in by_sym_events.get(sym, []):
                if ts > close_ts and (not merged_ticks or ts > float(merged_ticks[-1].get("ts_epoch") or 0)):
                    p_continue.ticks.append(
                        {
                            "ts_epoch": ts,
                            "price": px,
                            "pnl_pct": _pnl_pct(entry_px, px),
                            "quality": 0.0,
                            "momentum": 0.0,
                            "favorable": 0.0,
                        }
                    )
            if p_continue.ticks:
                sim_pnl, _ = _simulate_tick_policy(p_continue, "combined_structural_exit_v1", cfg)
                continue_ignore_pnl = sim_pnl
            else:
                continue_ignore_pnl = continue_pnl
        else:
            continue_ignore_pnl = continue_pnl

        rows.append(
            {
                "symbol": sym,
                "prior_entry_time": ent,
                "overlap_close_time": close_t,
                "prior_entry_price": entry_px,
                "overlap_close_price": close_px,
                "price_delta_at_overlap_pct": round(_pnl_pct(entry_px, close_px), 4),
                "pnl_if_overlap_is_exit": as_exit_pnl,
                "pnl_if_ignore_overlap_to_session_end": round(continue_pnl, 4),
                "pnl_if_ignore_with_combined_structural_exit": round(continue_ignore_pnl, 4),
                "hold_duration_sec": float(t.get("hold_duration_sec") or 0),
                "tick_count": int(t.get("tick_count") or 0),
                "had_take_before_overlap": bool(t.get("had_take_before_exit")),
                "overlap_churn": float(t.get("hold_duration_sec") or 0) <= CHURN_HOLD_SEC
                and int(t.get("tick_count") or 0) <= CHURN_MAX_TICKS,
            }
        )
    return rows


def _policy_matrix(
    paths: list[EvalPath],
    trades: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    *,
    session_end: str,
    cfg: Any,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    baseline_pnls = [float(t.get("realized_pnl_pct") or 0) for t in trades]

    def _row(policy: str, pnls: list[float], note: str = "") -> dict[str, Any]:
        pf = _profit_factor(pnls)
        return {
            "policy": policy,
            "trade_count": len(pnls),
            "avg_pnl_pct": round(statistics.mean(pnls), 4) if pnls else None,
            "profit_factor": round(pf, 4) if pf not in (None, float("inf")) else pf,
            "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else None,
            "max_loss_pct": round(min(pnls), 4) if pnls else None,
            "note": note,
        }

    rows.append(_row("structural_observer_v1_baseline", baseline_pnls, "from structural_trades.csv"))

    for policy in STRUCTURAL_EXIT_POLICIES:
        if policy in (
            "structural_observer_v1_baseline",
            "duplicate_reject_policy",
            "overlap_ignore_policy",
        ):
            continue
        pnls = []
        exit_reasons: Counter[str] = Counter()
        for p in paths:
            pnl, reason = _simulate_tick_policy(p, policy, cfg)
            pnls.append(pnl)
            exit_reasons[reason] += 1
        rows.append(
            {
                **_row(policy, pnls),
                "simulated_exit_reason_top": exit_reasons.most_common(1)[0][0] if exit_reasons else "",
            }
        )

    dup_paths = _paths_duplicate_reject(events, session_end)
    dup_pnls = [p.realized_pnl_pct for p in dup_paths]
    rows.append(
        _row(
            "duplicate_reject_policy",
            dup_pnls,
            "second+ accept same symbol skipped; one position per symbol",
        )
    )

    ign_pnls = _paths_overlap_ignore(events, session_end)
    rows.append(
        _row(
            "overlap_ignore_policy",
            ign_pnls,
            "first entry per symbol; no overlap_replaced_review close",
        )
    )

    return rows


def _explain_pf_gap(
    trades: Sequence[Mapping[str, Any]],
    loss_rows: Sequence[Mapping[str, Any]],
    overlap_rows: Sequence[Mapping[str, Any]],
    legacy: Mapping[str, Any],
    structural_metrics: Mapping[str, Any],
) -> list[str]:
    lines: list[str] = []
    st_pf = float(structural_metrics.get("structural_pf") or 0)
    leg_pf = float(legacy.get("legacy_virtual_hold_pf") or 0)
    lines.append(
        f"legacy_virtual_hold_pf={leg_pf:.4f} marks every accept at ~300s last-tick price; "
        f"structural_pf={st_pf:.4f} uses stop/overlap/session_end only."
    )
    overlap = [t for t in trades if t.get("close_reason") == "overlap_replaced_review"]
    stops = [t for t in trades if t.get("close_reason") == "stop_hit"]
    overlap_pnl = sum(float(t.get("realized_pnl_pct") or 0) for t in overlap)
    stop_pnl = sum(float(t.get("realized_pnl_pct") or 0) for t in stops)
    lines.append(
        f"overlap_replaced_review: {len(overlap)} trades ({100*len(overlap)/max(1,len(trades)):.1f}%), "
        f"sum_pnl={overlap_pnl:.2f}% - churn closes before structural decay/stop on many symbols."
    )
    lines.append(
        f"stop_hit: {len(stops)} trades, sum_pnl={stop_pnl:.2f}% - hard stop drives structural losses."
    )
    losers = [r for r in loss_rows if float(r.get("realized_pnl_pct") or 0) < 0]
    churn = sum(1 for r in losers if "duplicate_symbol_churn" in str(r.get("secondary_loss_tags")))
    decay_miss = sum(1 for r in losers if "quality_decay_missing" in str(r.get("all_loss_tags")))
    mom_miss = sum(1 for r in losers if "momentum_decay_missing" in str(r.get("all_loss_tags")))
    lines.append(
        f"Losing trades={len(losers)}: churn_tag={churn}, quality_decay_missing={decay_miss}, "
        f"momentum_decay_missing={mom_miss}."
    )
    if overlap_rows:
        avg_exit = statistics.mean(float(r["pnl_if_overlap_is_exit"]) for r in overlap_rows)
        avg_cont = statistics.mean(float(r["pnl_if_ignore_overlap_to_session_end"]) for r in overlap_rows)
        lines.append(
            f"Overlap what-if avg pnl: as_exit={avg_exit:.4f}, ignore_to_session={avg_cont:.4f}."
        )
    return lines


def _recommend_next_step(
    matrix: Sequence[Mapping[str, Any]],
    overlap_rows: Sequence[Mapping[str, Any]],
    loss_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    by_policy = {str(r["policy"]): r for r in matrix}
    baseline_pf = float(by_policy.get("structural_observer_v1_baseline", {}).get("profit_factor") or 0)
    overlap_ign = by_policy.get("overlap_ignore_policy", {})
    combined = by_policy.get("combined_structural_exit_v1", {})
    dup = by_policy.get("duplicate_reject_policy", {})

    scores: dict[str, float] = {
        REC_ADD_STRUCTURAL: 0.0,
        REC_FIX_OVERLAP: 0.0,
        REC_ENTRY_QUALITY: 0.0,
        REC_OBSERVE: 0.0,
    }

    ign_pf = float(overlap_ign.get("profit_factor") or 0)
    comb_pf = float(combined.get("profit_factor") or 0)
    dup_pf = float(dup.get("profit_factor") or 0)

    if ign_pf > baseline_pf + 0.15 or dup_pf > baseline_pf + 0.10:
        scores[REC_FIX_OVERLAP] += 3.0
    if comb_pf >= MIN_PF_TARGET:
        scores[REC_ADD_STRUCTURAL] += 3.0
    elif comb_pf >= 1.15:
        scores[REC_ADD_STRUCTURAL] += 2.0
    if float(by_policy.get("quality_decay_exit", {}).get("profit_factor") or 0) >= MIN_PF_TARGET:
        scores[REC_ADD_STRUCTURAL] += 1.0
    if comb_pf > baseline_pf + 0.12:
        scores[REC_ADD_STRUCTURAL] += 1.5

    churn_losses = sum(
        1
        for r in loss_rows
        if "duplicate_symbol_churn" in str(r.get("secondary_loss_tags") or r.get("all_loss_tags"))
    )
    if churn_losses >= 10:
        scores[REC_FIX_OVERLAP] += 1.0

    if baseline_pf < 0.7 and comb_pf < 1.0 and ign_pf < 1.0:
        scores[REC_ENTRY_QUALITY] += 2.0

    if max(scores.values()) < 1.5:
        scores[REC_OBSERVE] += 1.0

    best = max(scores.items(), key=lambda x: x[1])
    step = best[0] if best[1] >= 1.5 else REC_OBSERVE

    return {
        "recommend_next_step": step,
        "recommendation_scores": scores,
        "baseline_structural_pf": baseline_pf,
        "best_combined_structural_pf": comb_pf,
        "overlap_ignore_pf": ign_pf,
        "duplicate_reject_pf": dup_pf,
        "rationale": (
            f"structural baseline PF={baseline_pf:.4f}; "
            f"combined_structural_exit_v1 PF={comb_pf:.4f}; "
            f"overlap_ignore PF={ign_pf:.4f}."
        ),
    }


def run_structural_exit_design_review(
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
    session_end = _session_end_time(events)
    cfg = observer_tracker_config_from_pilot(pilot_config)

    trades_path = session_dir / "structural_trades.csv"
    trades = _load_structural_trades_csv(trades_path)
    if not trades:
        _, _ = replay_structural_observer_v1(
            events, pilot_config=pilot_config, poll_interval_sec=interval, session_end=session_end
        )
        trades = _load_structural_trades_csv(trades_path)

    struct_json = _load_json(session_dir / "structural_observer_review.json")
    from research.structural_observer_review import _legacy_virtual_hold_summary

    legacy = _legacy_virtual_hold_summary(events)
    if struct_json.get("legacy_virtual_hold_pf") is not None:
        legacy = {
            k: struct_json.get(k)
            for k in struct_json
            if k.startswith("legacy_")
            and k != "legacy_comparison"
            and not isinstance(struct_json.get(k), dict)
        }

    eval_paths = build_eval_paths(events, session_end=session_end)
    path_by_key = {(p.symbol, p.entry_time): p for p in eval_paths}

    loss_rows = [_classify_loss_row(t, path_by_key.get((str(t["symbol"]), str(t["entry_time"])))) for t in trades]
    overlap_rows = _overlap_review_rows(trades, path_by_key, events, cfg=cfg)
    matrix = _policy_matrix(eval_paths, trades, events, session_end=session_end, cfg=cfg)

    losers = [r for r in loss_rows if float(r.get("realized_pnl_pct") or 0) < 0]
    loss_summary = dict(Counter(r["primary_loss_driver"] for r in losers))
    tag_counter: Counter[str] = Counter()
    for r in losers:
        for tag in str(r.get("all_loss_tags") or "").split("|"):
            if tag and tag != "winner":
                tag_counter[tag] += 1

    explanation = _explain_pf_gap(trades, loss_rows, overlap_rows, legacy, struct_json)
    recommendation = _recommend_next_step(matrix, overlap_rows, loss_rows)

    overlap_as_exit_avg = (
        round(statistics.mean(float(r["pnl_if_overlap_is_exit"]) for r in overlap_rows), 4)
        if overlap_rows
        else None
    )
    overlap_ignore_avg = (
        round(statistics.mean(float(r["pnl_if_ignore_overlap_to_session_end"]) for r in overlap_rows), 4)
        if overlap_rows
        else None
    )

    return {
        "phase": 59,
        "mode": "structural_exit_design_review",
        "session_dir": str(session_dir),
        "session_end_time": session_end,
        "structural_baseline": {
            "structural_pf": struct_json.get("structural_pf"),
            "structural_trade_count": struct_json.get("structural_trade_count"),
            "exit_reason_distribution": struct_json.get("exit_reason_distribution"),
        },
        "legacy_comparison": legacy,
        "pf_gap_explanation": explanation,
        "loss_decomposition_summary": {
            "losing_trade_count": len(losers),
            "primary_driver_counts": loss_summary,
            "tag_counts": dict(tag_counter),
        },
        "overlap_analysis": {
            "overlap_exit_count": len(overlap_rows),
            "overlap_pct_of_trades": round(100.0 * len(overlap_rows) / max(1, len(trades)), 2),
            "avg_pnl_overlap_as_exit": overlap_as_exit_avg,
            "avg_pnl_ignore_overlap_to_session_end": overlap_ignore_avg,
            "overlap_should_be_exit_verdict": (
                "prefer_fix_overlap_policy"
                if overlap_ignore_avg is not None
                and overlap_as_exit_avg is not None
                and overlap_ignore_avg > overlap_as_exit_avg + 0.05
                else "overlap_as_exit_is_conservative"
            ),
        },
        "exit_candidate_matrix_summary": {
            "policies_evaluated": [r["policy"] for r in matrix],
            "best_policy_by_pf": max(
                matrix,
                key=lambda r: float(r.get("profit_factor") or 0),
            ).get("policy")
            if matrix
            else None,
        },
        **recommendation,
        "_loss_rows": loss_rows,
        "_overlap_rows": overlap_rows,
        "_matrix_rows": matrix,
    }


def write_structural_exit_design_review(session_dir: Path, review: Mapping[str, Any]) -> dict[str, Path]:
    session_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    public = {k: v for k, v in review.items() if not k.startswith("_")}
    json_path = session_dir / "structural_exit_design_review.json"
    json_path.write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    paths["json"] = json_path

    for key, name in (
        ("_loss_rows", "structural_loss_decomposition.csv"),
        ("_overlap_rows", "overlap_replacement_review.csv"),
        ("_matrix_rows", "structural_exit_candidate_matrix.csv"),
    ):
        rows = review.get(key) or []
        if not rows:
            continue
        p = session_dir / name
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        paths[name.replace(".csv", "")] = p

    return paths


def build_and_write_structural_exit_design_review(
    session_dir: Path,
    *,
    pilot_config: Any,
    poll_interval_sec: Optional[float] = None,
) -> dict[str, Any]:
    review = run_structural_exit_design_review(
        session_dir,
        pilot_config=pilot_config,
        poll_interval_sec=poll_interval_sec,
    )
    paths = write_structural_exit_design_review(session_dir, review)
    public = {k: v for k, v in review.items() if not k.startswith("_")}
    public["output_files"] = {k: str(v) for k, v in paths.items()}
    paths["json"].write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return public
