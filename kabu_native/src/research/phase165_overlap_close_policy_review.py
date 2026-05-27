"""
Phase 165: Overlap close policy review — suppress/delay overlap_replaced_review close
without discarding accepted events (review only).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.cap3_entry_replay import _profit_factor
from research.fade_exit_replay import FADE_EXIT_REASONS
from research.phase161_fade_shadow_policy_review import (
    _guard_pass_keys,
    _session_id,
    _write_csv,
    analyze_session as phase161_analyze_session,
)
from research.phase159_overlap_review import load_cap5_only_keys
from research.small_paper_performance_review import _load_events
from research.structural_exit_policies import (
    POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    combined_exit_signal_on_latest_tick,
)
from research.structural_observer_review import (
    StructuralTrade,
    _ActiveStructural,
    _as_float,
    _close_structural_trade,
    _last_tick_price,
    _parse_dt,
    _parse_ts,
    _session_end_time,
    _trade_for_structural_eval,
    _trade_key,
    block_shadow_cfg_for_v1,
    cooldown_shadow_cfg_for_v1,
    first_switch_shadow_cfg_for_v1,
    map_session_close_reason,
    pure_price_momentum_from_prices,
    session_bucket_at,
    simulate_structural_policy,
    tick_from_candidate,
)
from small_paper.discord_notifier import observer_tracker_config_from_pilot

GIVEBACK_SMALL_FRAC = 0.25
HIGH_ZONE_FRAC = 0.85

OVERLAP_SCENARIOS: tuple[tuple[str, str], ...] = (
    ("A_baseline", "baseline"),
    ("B_hold_old", "hold_old"),
    ("C_protect_profitable_old", "protect_profitable"),
    ("D_protect_mfe_old", "protect_mfe"),
    ("E_delayed_replace_60s", "delayed_60"),
    ("F_priority_qgap_005", "priority_qgap_005"),
    ("G_fade_watch_protect", "fade_watch_protect"),
    ("H_combined", "combined"),
)


@dataclass
class VirtualAccepted:
    symbol: str
    entry_time: str
    entry_price: float
    entry_quality: float
    entry_ts: float
    overlap_event_time: str
    old_entry_time: str
    ticks: list[dict[str, Any]] = field(default_factory=list)

    def append_tick(self, tick: Mapping[str, Any]) -> None:
        self.ticks.append(dict(tick))

    def final_pnl(self, session_end_ts: float) -> tuple[float, str]:
        if not self.ticks:
            return 0.0, "no_ticks"
        last = self.ticks[-1]
        ts = float(last.get("ts_epoch") or 0)
        px = float(last.get("price") or self.entry_price)
        pnl = ((px - self.entry_price) / self.entry_price * 100.0) if self.entry_price > 0 else 0.0
        reason = "session_end" if ts >= session_end_ts - 1 else "virtual_hold_end"
        return round(pnl, 4), reason


@dataclass
class PendingReplace:
    symbol: str
    old_key: tuple[str, str]
    new_entry_time: str
    new_price: float
    new_quality: float
    new_tier: str
    decide_after_ts: float
    overlap_time: str


@dataclass
class OverlapReplayStats:
    overlap_count: int = 0
    overlap_close_count: int = 0
    overlap_delayed_count: int = 0
    overlap_suppressed_count: int = 0
    accepted_preserved_count: int = 0
    virtual_accepted_count: int = 0
    cap_violation_count: int = 0
    missed_good_new_count: int = 0
    saved_good_old_count: int = 0


def _pnl_pct(entry: float, price: float) -> float:
    if entry <= 0:
        return 0.0
    return (price - entry) / entry * 100.0


def _peak_pnl(act: _ActiveStructural) -> float:
    if not act.ticks:
        return 0.0
    ep = act.trade.entry_price
    return max(_pnl_pct(ep, px) for _, px in act.ticks)


def _current_pnl(act: _ActiveStructural, price: float) -> float:
    return _pnl_pct(act.trade.entry_price, price)


def _mfe_protect(act: _ActiveStructural, price: float) -> bool:
    peak = _peak_pnl(act)
    pnl = _current_pnl(act, price)
    if peak <= 0.01:
        return False
    giveback = (peak - pnl) / peak if peak > 0 else 0.0
    if giveback < GIVEBACK_SMALL_FRAC:
        return True
    if pnl >= peak * HIGH_ZONE_FRAC:
        return True
    return False


def _priority_replace_allowed(
    old_act: _ActiveStructural,
    *,
    new_quality: float,
    price: float,
    q_gap: float,
) -> bool:
    old_q = float(old_act.trade.entry_quality or 0)
    old_pnl = _current_pnl(old_act, price)
    if old_act.fade_watch is not None:
        return False
    return new_quality > old_q + q_gap and old_pnl <= 0


def _should_suppress_overlap_close(
    policy: str,
    old_act: _ActiveStructural,
    *,
    new_quality: float,
    price: float,
    now_ts: float,
) -> bool:
    if policy == "baseline":
        return False
    if policy == "hold_old":
        return True
    if policy == "protect_profitable":
        return _current_pnl(old_act, price) >= 0
    if policy == "protect_mfe":
        return _mfe_protect(old_act, price)
    if policy == "fade_watch_protect":
        return old_act.fade_watch is not None
    if policy == "priority_qgap_005":
        return not _priority_replace_allowed(old_act, new_quality=new_quality, price=price, q_gap=0.05)
    if policy == "combined":
        if old_act.fade_watch is not None:
            return True
        if _current_pnl(old_act, price) >= 0:
            return True
        if _mfe_protect(old_act, price):
            return True
        return not _priority_replace_allowed(old_act, new_quality=new_quality, price=price, q_gap=0.05)
    if policy == "delayed_60":
        return True  # immediate close suppressed; decision later
    return False


def _open_position(
    active: dict[tuple[str, str], _ActiveStructural],
    *,
    sym: str,
    ent_raw: str,
    price: float,
    quality: float,
    tier: str,
    bucket: str,
) -> tuple[str, str]:
    ent_ts = _parse_ts(ent_raw)
    st = StructuralTrade(
        symbol=sym,
        entry_time=ent_raw,
        entry_price=float(price),
        entry_quality=float(quality),
        quality_tier=str(tier or ""),
        session_bucket=bucket,
    )
    key = _trade_key(sym, ent_raw)
    active[key] = _ActiveStructural(trade=st, entry_ts=ent_ts)
    return key


def _close_keys(
    active: dict[tuple[str, str], _ActiveStructural],
    keys: Sequence[tuple[str, str]],
    *,
    close_time: str,
    close_px: float,
    reason: str,
    completed: list[StructuralTrade],
) -> None:
    for key in keys:
        act = active.pop(key, None)
        if not act:
            continue
        completed.append(
            _close_structural_trade(act, close_time=close_time, close_price=close_px, close_reason=reason)
        )


def replay_overlap_policy(
    events: Sequence[Mapping[str, Any]],
    *,
    pilot_config: Any,
    overlap_policy: str,
    max_concurrent: int = 3,
    session_end: Optional[str] = None,
) -> tuple[list[StructuralTrade], list[dict[str, Any]], list[VirtualAccepted], OverlapReplayStats]:
    session_end = session_end or _session_end_time(events)
    session_end_ts = _parse_ts(session_end)

    cfg = observer_tracker_config_from_pilot(pilot_config)
    cfg.structural_exit_policy = POLICY_COMBINED_STRUCTURAL_EXIT_V1
    exit_cfg = first_switch_shadow_cfg_for_v1(block_shadow_cfg_for_v1(cooldown_shadow_cfg_for_v1(cfg)))
    exit_policy = POLICY_COMBINED_STRUCTURAL_EXIT_V1

    replay_prices: dict[str, list[float]] = {}
    active: dict[tuple[str, str], _ActiveStructural] = {}
    completed: list[StructuralTrade] = []
    overlap_events: list[dict[str, Any]] = []
    virtuals: list[VirtualAccepted] = []
    pending: list[PendingReplace] = []
    stats = OverlapReplayStats()

    ordered = sorted(events, key=lambda e: int(e.get("message_index") or 0))

    def _process_pending(now_ts: float, ent_raw: str, sym: str, price: float, bucket: str) -> None:
        nonlocal stats
        done: list[PendingReplace] = []
        for p in pending:
            if p.symbol != sym or now_ts < p.decide_after_ts:
                continue
            old_act = active.get(p.old_key)
            if old_act is None:
                done.append(p)
                continue
            replace = _priority_replace_allowed(
                old_act, new_quality=p.new_quality, price=price, q_gap=0.05
            )
            if replace:
                _close_keys(
                    active,
                    [p.old_key],
                    close_time=ent_raw,
                    close_px=price,
                    reason="overlap_replaced_review",
                    completed=completed,
                )
                stats.overlap_close_count += 1
                if len(active) < max_concurrent:
                    _open_position(
                        active,
                        sym=sym,
                        ent_raw=p.new_entry_time,
                        price=p.new_price,
                        quality=p.new_quality,
                        tier=p.new_tier,
                        bucket=bucket,
                    )
                    stats.accepted_preserved_count += 1
                else:
                    stats.cap_violation_count += 1
            else:
                stats.overlap_suppressed_count += 1
                if _current_pnl(old_act, price) > 0:
                    stats.saved_good_old_count += 1
            stats.overlap_delayed_count += 1
            done.append(p)

        for p in done:
            pending.remove(p)

    for ev in ordered:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        ent_raw = str(ev.get("entry_time") or "")
        as_of = _parse_dt(ent_raw) if ent_raw else None
        if as_of is None:
            continue
        now_ts = _parse_ts(ent_raw)
        trade = _trade_for_structural_eval(dict(ev), session_end)
        price = _as_float(ev.get("current_price"))
        bucket = session_bucket_at(as_of)

        if price and price > 0:
            prices = replay_prices.setdefault(sym, [])
            prices.append(float(price))
            if len(prices) > 120:
                prices.pop(0)
            ppm = pure_price_momentum_from_prices(prices)
            trade = {**trade, "pure_price_momentum": ppm, "current_price": price}

        if overlap_policy == "delayed_60" and price and price > 0:
            _process_pending(now_ts, ent_raw, sym, float(price), bucket)

        # Update virtual accepted ticks
        for v in virtuals:
            if v.symbol == sym and v.entry_time <= ent_raw:
                if ev.get("event_type") == "candidate":
                    tick = tick_from_candidate(trade, v.entry_price, v.entry_quality)
                    tick["ts_epoch"] = now_ts
                    v.append_tick(tick)

        if ev.get("event_type") == "accepted" and price and price > 0:
            old_keys = [k for k, a in active.items() if a.trade.symbol == sym]
            new_q = float(ev.get("continuation_quality_score") or 0)
            new_tier = str(ev.get("quality_tier") or "")

            if old_keys:
                stats.overlap_count += 1
                old_key = old_keys[0]
                old_act = active[old_key]
                old_pnl = _current_pnl(old_act, float(price))
                old_mfe = _peak_pnl(old_act)
                suppress = _should_suppress_overlap_close(
                    overlap_policy,
                    old_act,
                    new_quality=new_q,
                    price=float(price),
                    now_ts=now_ts,
                )

                action = "immediate_replace"
                if overlap_policy == "delayed_60":
                    pending.append(
                        PendingReplace(
                            symbol=sym,
                            old_key=old_key,
                            new_entry_time=ent_raw,
                            new_price=float(price),
                            new_quality=new_q,
                            new_tier=new_tier,
                            decide_after_ts=now_ts + 60.0,
                            overlap_time=ent_raw,
                        )
                    )
                    action = "delayed_pending"
                    stats.accepted_preserved_count += 1
                    virtuals.append(
                        VirtualAccepted(
                            symbol=sym,
                            entry_time=ent_raw,
                            entry_price=float(price),
                            entry_quality=new_q,
                            entry_ts=now_ts,
                            overlap_event_time=ent_raw,
                            old_entry_time=old_act.trade.entry_time,
                        )
                    )
                    stats.virtual_accepted_count += 1
                elif suppress:
                    action = "suppress_close_hold_old"
                    stats.overlap_suppressed_count += 1
                    stats.accepted_preserved_count += 1
                    if old_pnl > 0 or old_mfe > 0.05:
                        stats.saved_good_old_count += 1
                    virtuals.append(
                        VirtualAccepted(
                            symbol=sym,
                            entry_time=ent_raw,
                            entry_price=float(price),
                            entry_quality=new_q,
                            entry_ts=now_ts,
                            overlap_event_time=ent_raw,
                            old_entry_time=old_act.trade.entry_time,
                        )
                    )
                    stats.virtual_accepted_count += 1
                else:
                    # Replace: close old, open new if cap allows
                    _close_keys(
                        active,
                        old_keys,
                        close_time=ent_raw,
                        close_px=float(price),
                        reason="overlap_replaced_review",
                        completed=completed,
                    )
                    stats.overlap_close_count += 1
                    if len(active) < max_concurrent:
                        _open_position(
                            active,
                            sym=sym,
                            ent_raw=ent_raw,
                            price=float(price),
                            quality=new_q,
                            tier=new_tier,
                            bucket=bucket,
                        )
                        stats.accepted_preserved_count += 1
                    else:
                        stats.cap_violation_count += 1
                        virtuals.append(
                            VirtualAccepted(
                                symbol=sym,
                                entry_time=ent_raw,
                                entry_price=float(price),
                                entry_quality=new_q,
                                entry_ts=now_ts,
                                overlap_event_time=ent_raw,
                                old_entry_time=old_act.trade.entry_time,
                            )
                        )
                        stats.virtual_accepted_count += 1

                v_pnl, v_reason = 0.0, ""
                if virtuals and virtuals[-1].entry_time == ent_raw:
                    v_pnl, v_reason = virtuals[-1].final_pnl(session_end_ts)

                overlap_events.append(
                    {
                        "symbol": sym,
                        "overlap_time": ent_raw,
                        "old_entry_time": old_act.trade.entry_time,
                        "new_entry_time": ent_raw,
                        "old_pnl_at_overlap": round(old_pnl, 4),
                        "old_mfe_at_overlap": round(old_mfe, 4),
                        "old_quality": round(float(old_act.trade.entry_quality or 0), 4),
                        "new_quality": round(new_q, 4),
                        "quality_delta": round(new_q - float(old_act.trade.entry_quality or 0), 4),
                        "old_in_fade_watch": old_act.fade_watch is not None,
                        "action": action,
                        "virtual_new_pnl": v_pnl,
                        "virtual_new_reason": v_reason,
                        "old_vs_new_delta": round(old_pnl - v_pnl, 4),
                    }
                )
                continue

            # No overlap — normal open
            if len(active) >= max_concurrent:
                stats.cap_violation_count += 1
                stats.accepted_preserved_count += 1
                virtuals.append(
                    VirtualAccepted(
                        symbol=sym,
                        entry_time=ent_raw,
                        entry_price=float(price),
                        entry_quality=new_q,
                        entry_ts=now_ts,
                        overlap_event_time="",
                        old_entry_time="",
                    )
                )
                stats.virtual_accepted_count += 1
                continue

            _open_position(
                active,
                sym=sym,
                ent_raw=ent_raw,
                price=float(price),
                quality=new_q,
                tier=new_tier,
                bucket=bucket,
            )
            stats.accepted_preserved_count += 1

        elif ev.get("event_type") == "candidate":
            act = next((a for a in active.values() if a.trade.symbol == sym), None)
            if not act or not price or price <= 0:
                continue

            tick = tick_from_candidate(trade, act.trade.entry_price, act.trade.entry_quality)
            tick["ts_epoch"] = now_ts
            act.ticks.append((now_ts, float(tick["price"])))
            act.rich_ticks.append(tick)

            stop_px = act.trade.entry_price * (1.0 - exit_cfg.hard_stop_pct / 100.0)
            if float(tick["price"]) <= stop_px:
                key = _trade_key(sym, act.trade.entry_time)
                _close_keys(
                    active,
                    [key],
                    close_time=ent_raw,
                    close_px=float(tick["price"]),
                    reason="stop_hit",
                    completed=completed,
                )
                continue

            sig = combined_exit_signal_on_latest_tick(act.rich_ticks, act.trade.entry_price, exit_cfg)
            if sig:
                sig_pnl, reason, close_px = sig
                if reason in FADE_EXIT_REASONS or reason not in ("",):
                    key = _trade_key(sym, act.trade.entry_time)
                    _close_keys(
                        active,
                        [key],
                        close_time=ent_raw,
                        close_px=float(close_px),
                        reason=reason,
                        completed=completed,
                    )
                    continue

    # Flush pending delayed (force decision at session end)
    if overlap_policy == "delayed_60":
        for p in list(pending):
            old_act = active.get(p.old_key)
            px = p.new_price
            if old_act:
                replace = _priority_replace_allowed(old_act, new_quality=p.new_quality, price=px, q_gap=0.05)
                if replace:
                    _close_keys(
                        active,
                        [p.old_key],
                        close_time=session_end,
                        close_px=px,
                        reason="overlap_replaced_review",
                        completed=completed,
                    )
                    stats.overlap_close_count += 1
                else:
                    stats.overlap_suppressed_count += 1
            stats.overlap_delayed_count += 1
        pending.clear()

    for key, act in list(active.items()):
        active.pop(key, None)
        close_px = _last_tick_price(act.ticks, act.trade.entry_price)
        end_reason = map_session_close_reason("session_end")
        completed.append(
            _close_structural_trade(act, close_time=session_end, close_price=close_px, close_reason=end_reason)
        )

    # Finalize virtual PnL at session end
    for v in virtuals:
        if not v.ticks and v.entry_price > 0:
            v.append_tick({"price": v.entry_price, "ts_epoch": v.entry_ts})
        pnl, reason = v.final_pnl(session_end_ts)
        if pnl > 0.05 and v.overlap_event_time:
            stats.missed_good_new_count += 1

    return completed, overlap_events, virtuals, stats


def _summarize_trades(trades: Sequence[StructuralTrade], stats: OverlapReplayStats) -> dict[str, Any]:
    pnls = [float(t.realized_pnl_pct) for t in trades]
    holds = [float(t.hold_duration_sec) for t in trades]
    reasons = Counter(str(t.close_reason or "") for t in trades)
    fade_exit = sum(1 for t in trades if str(t.close_reason or "") in FADE_EXIT_REASONS)

    return {
        "trade_count": len(trades),
        "pf": _profit_factor(pnls),
        "total_pnl": round(sum(pnls), 4) if pnls else 0.0,
        "avg_pnl": round(statistics.mean(pnls), 4) if pnls else None,
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else None,
        "max_loss": round(min(pnls), 4) if pnls else None,
        "fade_exit_count": fade_exit,
        "stop_hit_count": reasons.get("stop_hit", 0),
        "avg_hold_sec": round(statistics.mean(holds), 2) if holds else None,
        "overlap_count": stats.overlap_count,
        "overlap_close_count": stats.overlap_close_count,
        "overlap_delayed_count": stats.overlap_delayed_count,
        "overlap_suppressed_count": stats.overlap_suppressed_count,
        "accepted_preserved_count": stats.accepted_preserved_count,
        "missed_good_new_count": stats.missed_good_new_count,
        "saved_good_old_count": stats.saved_good_old_count,
        "cap_violation_count": stats.cap_violation_count,
    }


def analyze_phase165(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
    cap5_csv: Optional[Path] = None,
) -> dict[str, Any]:
    cap5_keys = load_cap5_only_keys(cap5_csv) if cap5_csv else set()
    max_concurrent = int(getattr(pilot_config, "max_concurrent_positions", 3) or 3)

    exit_cfg = observer_tracker_config_from_pilot(pilot_config)
    exit_cfg.structural_exit_policy = POLICY_COMBINED_STRUCTURAL_EXIT_V1

    # Phase161 improved cohort (G hybrid vs actual)
    phase161_rows: list[dict[str, Any]] = []
    guard_keys_all: set[tuple[str, str]] = set()
    cache: list[tuple[str, Path, Sequence[Mapping[str, Any]], set[tuple[str, str]]]] = []
    for sdir in session_dirs:
        events = _load_events(sdir)
        guard_keys = _guard_pass_keys(events)
        guard_keys_all |= guard_keys
        cache.append((sdir, sdir, events, guard_keys))
        phase161_rows.extend(
            phase161_analyze_session(
                sdir,
                exit_cfg=exit_cfg,
                cap5_keys=cap5_keys,
                guard_keys=guard_keys,
            )
        )

    g_improved = {
        (str(r["session"]), str(r["symbol"]), str(r["entry_time"])): r
        for r in phase161_rows
        if r.get("scenario") == "G_hybrid"
        and r.get("subset") == "all"
        and r.get("improved_vs_actual")
    }

    scenario_rows: list[dict[str, Any]] = []
    overlap_event_rows: list[dict[str, Any]] = []
    old_vs_new_rows: list[dict[str, Any]] = []
    cap5_overlap_rows: list[dict[str, Any]] = []
    gain_recovery_rows: list[dict[str, Any]] = []

    baseline_pnls_by_key: dict[tuple[str, str, str], float] = {}

    for scen_id, policy in OVERLAP_SCENARIOS:
        all_trades: list[StructuralTrade] = []
        guard_trades: list[StructuralTrade] = []
        cap5_trades: list[StructuralTrade] = []
        stats_acc = OverlapReplayStats()

        for sdir, _p, events, guard_keys in cache:
            session_id = _session_id(sdir)
            trades, ov_events, _virtuals, stats = replay_overlap_policy(
                events,
                pilot_config=pilot_config,
                overlap_policy=policy,
                max_concurrent=max_concurrent,
            )
            all_trades.extend(trades)
            stats_acc.overlap_count += stats.overlap_count
            stats_acc.overlap_close_count += stats.overlap_close_count
            stats_acc.overlap_delayed_count += stats.overlap_delayed_count
            stats_acc.overlap_suppressed_count += stats.overlap_suppressed_count
            stats_acc.accepted_preserved_count += stats.accepted_preserved_count
            stats_acc.missed_good_new_count += stats.missed_good_new_count
            stats_acc.saved_good_old_count += stats.saved_good_old_count
            stats_acc.cap_violation_count += stats.cap_violation_count
            stats_acc.virtual_accepted_count += stats.virtual_accepted_count

            for ov in ov_events:
                overlap_event_rows.append({**ov, "session": session_id, "scenario": scen_id})
                old_vs_new_rows.append(
                    {
                        "session": session_id,
                        "scenario": scen_id,
                        "symbol": ov.get("symbol"),
                        "overlap_time": ov.get("overlap_time"),
                        "old_entry_time": ov.get("old_entry_time"),
                        "new_entry_time": ov.get("new_entry_time"),
                        "old_pnl_at_overlap": ov.get("old_pnl_at_overlap"),
                        "virtual_new_pnl": ov.get("virtual_new_pnl"),
                        "old_vs_new_delta": ov.get("old_vs_new_delta"),
                        "action": ov.get("action"),
                        "quality_delta": ov.get("quality_delta"),
                    }
                )
                k = (session_id, str(ov.get("symbol")), str(ov.get("new_entry_time")))
                if cap5_keys and (str(ov.get("symbol")), str(ov.get("new_entry_time"))) in cap5_keys:
                    cap5_overlap_rows.append({**ov, "session": session_id, "scenario": scen_id})

            for t in trades:
                k3 = (session_id, t.symbol, t.entry_time)
                if (t.symbol, t.entry_time) in guard_keys:
                    guard_trades.append(t)
                if cap5_keys and (t.symbol, t.entry_time) in cap5_keys:
                    cap5_trades.append(t)
                if scen_id == "A_baseline":
                    baseline_pnls_by_key[k3] = float(t.realized_pnl_pct)

                g = g_improved.get(k3)
                if g and scen_id != "A_baseline":
                    base_pnl = baseline_pnls_by_key.get(k3)
                    if base_pnl is None:
                        continue
                    gain_161 = float(g.get("scenario_pnl") or 0) - float(g.get("actual_pnl") or 0)
                    gain_165 = float(t.realized_pnl_pct) - base_pnl
                    gain_165 = float(t.realized_pnl_pct) - base_pnl
                    gain_recovery_rows.append(
                        {
                            "scenario": scen_id,
                            "session": session_id,
                            "symbol": t.symbol,
                            "entry_time": t.entry_time,
                            "gain_161": round(gain_161, 4),
                            "gain_165": round(gain_165, 4),
                            "gain_lost": round(gain_161 - gain_165, 4),
                            "baseline_pnl": round(base_pnl, 4),
                            "scenario_pnl": round(float(t.realized_pnl_pct), 4),
                            "actual_pnl": round(float(g.get("actual_pnl") or 0), 4),
                        }
                    )

        summ = _summarize_trades(all_trades, stats_acc)
        scenario_rows.append({"scenario": scen_id, "overlap_policy": policy, "subset": "all", **summ})
        if guard_trades:
            scenario_rows.append(
                {
                    "scenario": scen_id,
                    "overlap_policy": policy,
                    "subset": "guard_pass",
                    **_summarize_trades(guard_trades, stats_acc),
                }
            )
        if cap5_trades:
            scenario_rows.append(
                {
                    "scenario": scen_id,
                    "overlap_policy": policy,
                    "subset": "cap5_only",
                    **_summarize_trades(cap5_trades, stats_acc),
                }
            )

    verdict, notes = _determine_verdict(scenario_rows)

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "session_count": len(session_dirs),
        "phase161_improved_count": len(g_improved),
        "scenario_rows": scenario_rows,
        "overlap_event_details": overlap_event_rows,
        "old_vs_new_comparison": old_vs_new_rows,
        "cap5_overlap_subset": cap5_overlap_rows,
        "phase161_gain_recovery": gain_recovery_rows,
    }


def _determine_verdict(scenario_rows: Sequence[Mapping[str, Any]]) -> tuple[str, list[str]]:
    notes: list[str] = []

    def _row(scen: str, subset: str = "all") -> dict[str, Any]:
        return next(
            (r for r in scenario_rows if r.get("scenario") == scen and r.get("subset") == subset),
            {},
        )

    base = _row("A_baseline")
    base_pf = float(base.get("pf") or 0)
    base_overlap_close = int(base.get("overlap_close_count") or 0)

    candidates = [
        ("B_hold_old", "overlap_close_delay_promising"),
        ("C_protect_profitable_old", "protect_profitable_old_promising"),
        ("F_priority_qgap_005", "priority_replace_promising"),
        ("G_fade_watch_protect", "fade_watch_overlap_protect_promising"),
        ("H_combined", "overlap_close_delay_promising"),
    ]

    best_scen = None
    best_pf = base_pf
    for scen, _label in candidates:
        r = _row(scen)
        pf = float(r.get("pf") or 0)
        if pf > best_pf:
            best_pf = pf
            best_scen = scen

    notes.append(f"baseline_pf={base_pf:.4f} baseline_overlap_close={base_overlap_close}")

    if best_scen:
        r = _row(best_scen)
        notes.append(
            f"best={best_scen} pf={best_pf:.4f} overlap_close={r.get('overlap_close_count')} "
            f"suppressed={r.get('overlap_suppressed_count')} saved_old={r.get('saved_good_old_count')}"
        )
        if best_pf >= base_pf + 0.03 and int(r.get("overlap_close_count") or 0) < base_overlap_close:
            label = next(l for s, l in candidates if s == best_scen)
            return label, notes

    # If overlap suppression doesn't help PF but overlap isn't dominant in gain loss anymore
    if base_overlap_close > 0:
        h = _row("H_combined")
        if int(h.get("overlap_close_count") or 0) < base_overlap_close * 0.5:
            notes.append("overlap_close_reduced_but_pf_flat")
            return "overlap_not_primary_after_correct_replay", notes

    return "need_live_shadow", notes


def write_phase165_outputs(result: Mapping[str, Any], *, reports_dir: Path, docs_dir: Path) -> dict[str, str]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": reports_dir / "phase165_overlap_close_policy_review.json",
        "scenarios": reports_dir / "phase165_overlap_close_scenarios.csv",
        "events": reports_dir / "phase165_overlap_event_details.csv",
        "compare": reports_dir / "phase165_old_vs_new_comparison.csv",
        "cap5": reports_dir / "phase165_cap5_overlap_subset.csv",
        "gain": reports_dir / "phase165_phase161_gain_recovery.csv",
        "md": docs_dir / "phase165_recommendation.md",
    }

    design = {
        k: v
        for k, v in result.items()
        if k
        not in (
            "scenario_rows",
            "overlap_event_details",
            "old_vs_new_comparison",
            "cap5_overlap_subset",
            "phase161_gain_recovery",
        )
    }
    paths["json"].write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(paths["scenarios"], result.get("scenario_rows") or [])
    _write_csv(paths["events"], result.get("overlap_event_details") or [])
    _write_csv(paths["compare"], result.get("old_vs_new_comparison") or [])
    _write_csv(paths["cap5"], result.get("cap5_overlap_subset") or [])
    _write_csv(paths["gain"], result.get("phase161_gain_recovery") or [])

    rows_all = [r for r in (result.get("scenario_rows") or []) if r.get("subset") == "all"]
    lines = [
        "# Phase 165: overlap close policy review",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        "",
        "## Scenario summary (subset=all)",
        "",
        "| Scenario | PF | total PnL | overlap close | suppressed | saved old | missed good new |",
        "|----------|-----|-----------|--------------:|-----------:|----------:|----------------:|",
    ]
    for r in rows_all:
        lines.append(
            f"| {r.get('scenario')} | {r.get('pf')} | {r.get('total_pnl')} | "
            f"{r.get('overlap_close_count')} | {r.get('overlap_suppressed_count')} | "
            f"{r.get('saved_good_old_count')} | {r.get('missed_good_new_count')} |"
        )
    lines.extend(["", "## Notes", ""])
    for n in result.get("verdict_notes") or []:
        lines.append(f"- {n}")
    lines.extend(
        [
            "",
            "## Design principle",
            "",
            "- Accepted events are **never discarded**; when not opened as a position, they are tracked as virtual entries with would-be PnL.",
            "- Only `overlap_replaced_review` **close timing** changes; structural exit rules unchanged.",
        ]
    )
    paths["md"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {k: str(v) for k, v in paths.items()}
