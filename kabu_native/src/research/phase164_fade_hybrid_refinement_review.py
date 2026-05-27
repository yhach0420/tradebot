"""
Phase 164: Fade-hybrid refinement review (review only).

Goal:
- Separate and neutralize Phase163 improvement-loss drivers: overlap_replaced + second_fade.
- Evaluate candidate tweaks under structural replay (multi-position, overlap interactions).

Hard constraints:
- Review only (no production YAML changes, no entry/universe change, no orders).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.cap3_entry_replay import _profit_factor
from research.fade_exit_replay import FADE_EXIT_REASONS
from research.phase161_fade_shadow_policy_review import analyze_session as _phase161_analyze_session
from research.small_paper_performance_review import _load_events
from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1
from small_paper.discord_notifier import observer_tracker_config_from_pilot

from research.fade_hybrid_shadow import (
    FadeHybridState,
    FADE_WATCH_TRIGGER_REASONS,
    breakdown_confirmed_hybrid,
    combined_exit_or_fade_shadow_trigger,
    enter_fade_shadow_state,
    range_hold_protect_hybrid,
)

# Import replay primitives from Phase62 implementation (review module).
from research.structural_observer_review import (  # noqa: PLC0415
    StructuralTrade,
    _ActiveStructural,
    _as_float,
    _close_structural_trade,
    _last_tick_price,
    _parse_dt,
    _parse_ts,
    _session_end_time,
    _take_exit_cfg_for_v1,
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
    uses_fade_shadow_trigger,
    uses_take_exit_shadow,
)


SECOND_FADE_SCENARIOS: tuple[tuple[str, str], ...] = (
    ("A_hybrid_current", "current"),  # 2nd fade exit (existing)
    ("B_second_fade_disabled", "disable"),
    ("C_second_fade_pnl_neg", "pnl_neg"),
    ("D_second_fade_new_low", "new_low"),
    ("E_second_fade_strict", "strict"),
    ("F_breakdown_only", "breakdown_only"),
)

OVERLAP_MODES: tuple[str, ...] = ("current", "disable", "protect_fade_watch")


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def _new_low_since_fade(state: FadeHybridState, price: float) -> bool:
    # Mirrors helper in fade_hybrid_shadow; re-declared to avoid relying on private symbol.
    return price < state.fade_price - 1e-9 and price <= state.post_low + 1e-9


def _should_exit_on_second_fade(
    state: FadeHybridState,
    *,
    mode: str,
    pnl: float,
    price: float,
    momentum: float,
) -> bool:
    if mode == "disable":
        return False
    if mode == "pnl_neg":
        return pnl < 0
    if mode == "new_low":
        return _new_low_since_fade(state, price)
    if mode == "strict":
        return (pnl < 0) and (momentum < 0.15) and (not state.take_reached_at_fade)
    # current
    return True


def process_fade_hybrid_tick_variant(
    state: FadeHybridState,
    *,
    entry_price: float,
    price: float,
    momentum: float,
    ts: float,
    rich_ticks: Sequence[Mapping[str, Any]],
    cfg: Any,
    second_fade_mode: str,
) -> Optional[tuple[str, dict[str, Any]]]:
    """
    Variant of hybrid watch tick for Phase164 scenarios.

    Priority (kept consistent with Phase162 semantics):
    1) (outside) stop_hit
    2) session_close mapping at flush
    3) breakdown confirmed
    4) second_fade logic (scenario-dependent)
    5) other structural exits (except breakdown_only scenario)
    """
    state.ticks_in_watch += 1
    state.fade_watch_hold_sec = max(0.0, ts - state.entry_ts)
    pnl = ((price - entry_price) / entry_price * 100.0) if entry_price > 0 else 0.0
    state.peak_pnl = max(state.peak_pnl, pnl)
    state.peak_price = max(state.peak_price, price)
    state.post_low = min(state.post_low, price)

    # For breakdown_only: ignore other structural exits during watch.
    if second_fade_mode != "breakdown_only":
        sig = simulate_structural_policy(
            rich_ticks,
            entry_price,
            str(getattr(cfg, "structural_exit_policy", "") or POLICY_COMBINED_STRUCTURAL_EXIT_V1),
            cfg,
            allow_session_end=False,
        )
        if sig is not None:
            sig_pnl, sig_reason = sig
            if sig_reason not in FADE_WATCH_TRIGGER_REASONS:
                state.last_signals = {
                    "fade_watch_exit": True,
                    "fade_watch_exit_reason": "fade_hybrid_structural_exit",
                    "exit_pnl": sig_pnl,
                }
                # Keep original reason for reporting (priority in logs stays external).
                return "fade_hybrid_structural_exit", {"fade_watch_exit_reason": "fade_hybrid_structural_exit"}

    if breakdown_confirmed_hybrid(
        momentum=momentum,
        pnl=pnl,
        take_reached_at_fade=state.take_reached_at_fade,
        price=price,
        state=state,
    ):
        state.breakdown_confirmed_exit = True
        return "fade_hybrid_breakdown", {"fade_watch_exit_reason": "fade_hybrid_breakdown"}

    # Second fade handling (scenario-dependent).
    sig2 = combined_exit_or_fade_shadow_trigger(rich_ticks, entry_price, cfg, take_reached=False)
    fade_signal = False
    if sig2 is not None:
        _kind, _pnl2, _px2, reason2 = sig2
        fade_signal = reason2 in FADE_WATCH_TRIGGER_REASONS

    if fade_signal:
        state.fade_signal_count += 1
        if state.fade_signal_count >= 2:
            if range_hold_protect_hybrid(
                pnl=pnl,
                peak_pnl=state.peak_pnl,
                price=price,
                fade_price=state.fade_price,
            ):
                state.range_hold_protect_count += 1
                return None
            if second_fade_mode != "breakdown_only" and _should_exit_on_second_fade(
                state,
                mode=second_fade_mode,
                pnl=pnl,
                price=price,
                momentum=momentum,
            ):
                state.second_fade_exit = True
                return "fade_hybrid_second_fade", {"fade_watch_exit_reason": "fade_hybrid_second_fade"}

    # Range-hold protect continue (no exit).
    if range_hold_protect_hybrid(
        pnl=pnl,
        peak_pnl=state.peak_pnl,
        price=price,
        fade_price=state.fade_price,
    ):
        state.range_hold_protect_count += 1
        return None

    return None


def replay_phase164_structural_exit(
    events: Sequence[Mapping[str, Any]],
    *,
    pilot_config: Any,
    poll_interval_sec: float,
    structural_exit_policy: str,
    second_fade_mode: str,
    overlap_mode: str,
    session_end: Optional[str] = None,
) -> list[StructuralTrade]:
    """
    Phase164 replay runner (review-only).
    Derived from `replay_combined_structural_exit` but with:
    - overlap handling modes
    - second_fade scenario modes applied only to hybrid watch state
    """
    session_end = session_end or _session_end_time(events)
    cfg = observer_tracker_config_from_pilot(pilot_config)
    cfg.structural_exit_policy = structural_exit_policy
    exit_policy = str(cfg.structural_exit_policy or POLICY_COMBINED_STRUCTURAL_EXIT_V1)

    exit_cfg = first_switch_shadow_cfg_for_v1(block_shadow_cfg_for_v1(cooldown_shadow_cfg_for_v1(cfg)))
    replay_prices: dict[str, list[float]] = {}

    ordered = sorted(events, key=lambda e: int(e.get("message_index") or 0))
    active: dict[tuple[str, str], _ActiveStructural] = {}
    completed: list[StructuralTrade] = []

    for ev in ordered:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        ent_raw = str(ev.get("entry_time") or "")
        as_of = _parse_dt(ent_raw) if ent_raw else None
        trade = _trade_for_structural_eval(dict(ev), session_end)
        price = _as_float(ev.get("current_price"))
        if as_of is None:
            continue
        bucket = session_bucket_at(as_of)

        if price and price > 0:
            prices = replay_prices.setdefault(sym, [])
            prices.append(float(price))
            if len(prices) > 120:
                prices.pop(0)
            ppm = pure_price_momentum_from_prices(prices)
            trade = {**trade, "pure_price_momentum": ppm, "current_price": price}

        if ev.get("event_type") == "accepted" and price and price > 0:
            # overlap handling (same symbol)
            act_existing = next((a for a in active.values() if a.trade.symbol == sym), None)
            if act_existing is not None:
                if overlap_mode == "disable":
                    continue
                if overlap_mode == "protect_fade_watch" and act_existing.fade_watch is not None:
                    continue
                for key in [k for k, a in active.items() if a.trade.symbol == sym]:
                    old = active.pop(key)
                    closed = _close_structural_trade(
                        old,
                        close_time=ent_raw,
                        close_price=float(price),
                        close_reason="overlap_replaced_review",
                    )
                    completed.append(closed)

            ent_ts = _parse_ts(ent_raw)
            st = StructuralTrade(
                symbol=sym,
                entry_time=ent_raw,
                entry_price=float(price),
                entry_quality=float(ev.get("continuation_quality_score") or 0),
                quality_tier=str(ev.get("quality_tier") or ""),
                session_bucket=bucket,
            )
            key = _trade_key(sym, ent_raw)
            active[key] = _ActiveStructural(trade=st, entry_ts=ent_ts)

        elif ev.get("event_type") == "candidate":
            act = next((a for a in active.values() if a.trade.symbol == sym), None)
            if not act or not price or price <= 0:
                continue

            tick = tick_from_candidate(trade, act.trade.entry_price, act.trade.entry_quality)
            tick["ts_epoch"] = _parse_ts(ent_raw)
            act.ticks.append((tick["ts_epoch"], float(tick["price"])))
            act.rich_ticks.append(tick)

            stop_px = act.trade.entry_price * (1.0 - exit_cfg.hard_stop_pct / 100.0)
            if float(tick["price"]) <= stop_px:
                key = _trade_key(sym, act.trade.entry_time)
                closed_act = active.pop(key, None)
                if closed_act:
                    closed = _close_structural_trade(
                        closed_act,
                        close_time=ent_raw,
                        close_price=float(tick["price"]),
                        close_reason="stop_hit",
                    )
                    completed.append(closed)
                continue

            if act.fade_watch is not None and isinstance(act.fade_watch, FadeHybridState):
                mom = float(tick.get("momentum") or 0)
                out = process_fade_hybrid_tick_variant(
                    act.fade_watch,
                    entry_price=act.trade.entry_price,
                    price=float(tick["price"]),
                    momentum=mom,
                    ts=float(tick["ts_epoch"]),
                    rich_ticks=act.rich_ticks,
                    cfg=exit_cfg,
                    second_fade_mode=second_fade_mode,
                )
                if out:
                    reason, _fw = out
                    key = _trade_key(sym, act.trade.entry_time)
                    closed_act = active.pop(key, None)
                    if closed_act:
                        closed = _close_structural_trade(
                            closed_act,
                            close_time=ent_raw,
                            close_price=float(tick["price"]),
                            close_reason=reason,
                        )
                        completed.append(closed)
                    continue
                continue

            trigger = None
            if uses_fade_shadow_trigger(exit_policy):
                trigger = combined_exit_or_fade_shadow_trigger(
                    act.rich_ticks,
                    act.trade.entry_price,
                    exit_cfg,
                    take_reached=False,
                )
            else:
                sig_cfg = _take_exit_cfg_for_v1(exit_cfg) if uses_take_exit_shadow(exit_policy) else exit_cfg
                from research.structural_exit_policies import combined_exit_signal_on_latest_tick

                sig = combined_exit_signal_on_latest_tick(act.rich_ticks, act.trade.entry_price, sig_cfg)
                trigger = ("exit", sig[0], sig[2], sig[1]) if sig else None

            if trigger:
                kind, pnl, close_px, reason = trigger
                if kind == "fade_watch":
                    act.fade_watch = enter_fade_shadow_state(
                        policy=exit_policy,
                        entry_time=ent_raw,
                        entry_ts=float(tick["ts_epoch"]),
                        initial_reason=reason,
                        fade_price=float(close_px),
                        fade_momentum=float(tick.get("momentum") or 0),
                        mfe_at_fade=float(pnl),
                        entry_price=act.trade.entry_price,
                        take_reached=False,
                    )
                    continue
                key = _trade_key(sym, act.trade.entry_time)
                closed_act = active.pop(key, None)
                if closed_act:
                    closed = _close_structural_trade(
                        closed_act,
                        close_time=ent_raw,
                        close_price=float(close_px),
                        close_reason=reason,
                    )
                    completed.append(closed)
                continue

    # Flush remaining open
    for key, act in list(active.items()):
        active.pop(key, None)
        close_px = _last_tick_price(act.ticks, act.trade.entry_price)
        end_reason = map_session_close_reason("session_end") if uses_fade_shadow_trigger(exit_policy) else "session_end"
        closed = _close_structural_trade(act, close_time=session_end, close_price=close_px, close_reason=end_reason)
        completed.append(closed)

    return completed


def _summarize_trades(trades: Sequence[StructuralTrade]) -> dict[str, Any]:
    pnls = [float(t.realized_pnl_pct) for t in trades]
    holds = [float(t.hold_duration_sec) for t in trades]
    reasons = Counter(str(t.close_reason or "") for t in trades)

    fade_watch_entered = sum(1 for t in trades if bool(getattr(t, "fade_watch_entered", False)))
    overlap_exit = reasons.get("overlap_replaced_review", 0)
    second_fade_exit = sum(1 for t in trades if str(t.close_reason or "") == "fade_hybrid_second_fade")
    breakdown_exit = sum(1 for t in trades if str(t.close_reason or "") == "fade_hybrid_breakdown")
    range_hold_prot = sum(1 for t in trades if bool(getattr(t, "fade_watch_range_hold_protected", False)))
    stop_hit = reasons.get("stop_hit", 0)

    return {
        "trade_count": len(trades),
        "pf": _profit_factor(pnls),
        "total_pnl": round(sum(pnls), 4),
        "avg_pnl": round(statistics.mean(pnls), 4) if pnls else None,
        "max_loss": round(min(pnls), 4) if pnls else None,
        "avg_hold_sec": round(statistics.mean(holds), 2) if holds else None,
        "fade_watch_entered": fade_watch_entered,
        "second_fade_exit_count": second_fade_exit,
        "breakdown_exit_count": breakdown_exit,
        "overlap_exit_count": overlap_exit,
        "range_hold_protect_count": range_hold_prot,
        "stop_hit_count": stop_hit,
    }


def analyze_phase164(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
    phase162_trade_details_csv: Path,
    cap5_csv: Optional[Path] = None,
) -> dict[str, Any]:
    # Phase161: compute gain_161 from G_hybrid improvements vs actual (same as Phase163)
    from research.phase159_overlap_review import load_cap5_only_keys
    from research.phase161_fade_shadow_policy_review import _guard_pass_keys

    exit_cfg = observer_tracker_config_from_pilot(pilot_config)
    exit_cfg.structural_exit_policy = POLICY_COMBINED_STRUCTURAL_EXIT_V1
    cap5_keys = load_cap5_only_keys(cap5_csv) if cap5_csv else set()

    phase161_rows: list[dict[str, Any]] = []
    for sdir in session_dirs:
        events = _load_events(sdir)
        guard_keys = _guard_pass_keys(events)
        phase161_rows.extend(
            _phase161_analyze_session(
                sdir,
                exit_cfg=exit_cfg,
                cap5_keys=cap5_keys,
                guard_keys=guard_keys,
            )
        )

    g_improved = [
        r
        for r in phase161_rows
        if r.get("scenario") == "G_hybrid"
        and r.get("subset") == "all"
        and r.get("improved_vs_actual")
        and float(r.get("scenario_pnl") or 0) > float(r.get("actual_pnl") or 0) + 0.02
    ]
    g_by_key = {
        (str(r["session"]), str(r["symbol"]), str(r["entry_time"])): r for r in g_improved
    }

    # Phase164: run scenario grid and compute metrics
    scenario_rows: list[dict[str, Any]] = []
    second_fade_rows: list[dict[str, Any]] = []
    overlap_rows: list[dict[str, Any]] = []
    gain_rows: list[dict[str, Any]] = []
    cap5_rows: list[dict[str, Any]] = []

    # Cache events to avoid reload
    cache: list[tuple[Path, Sequence[Mapping[str, Any]]]] = [(s, _load_events(s)) for s in session_dirs]

    # Phase164 matrix can be expensive (6 second-fade x 3 overlap x 7 sessions).
    # Use a two-stage evaluation:
    # - Stage1: run all second-fade scenarios under overlap=current to pick top candidates.
    # - Stage2: evaluate overlap disable/protect only for baseline + A + top2 candidates.
    overlap_plan: dict[str, list[str]] = {"current": [s for s, _ in SECOND_FADE_SCENARIOS]}
    overlap_plan["disable"] = ["A_hybrid_current"]
    overlap_plan["protect_fade_watch"] = ["A_hybrid_current"]

    # Stage1: compute PF under overlap=current for ranking.
    stage1_pf: dict[str, float] = {}

    for overlap_mode in ("current",):
        # baseline A: combined v1 under same overlap mode, for gain_164 calc
        baseline_by_key: dict[tuple[str, str, str], StructuralTrade] = {}
        baseline_all: list[StructuralTrade] = []
        for sdir, events in cache:
            trades = replay_phase164_structural_exit(
                events,
                pilot_config=pilot_config,
                poll_interval_sec=float(getattr(pilot_config, "live_poll_interval_sec", 5.0) or 5.0),
                structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1,
                second_fade_mode="current",
                overlap_mode=overlap_mode,
            )
            baseline_all.extend(trades)
            for t in trades:
                baseline_by_key[(str(sdir.parent.name) + "/" + str(sdir.name), t.symbol, t.entry_time)] = t

        base_sum = _summarize_trades(baseline_all)
        scenario_rows.append(
            {
                "second_fade_scenario": "BASELINE_combined_v1",
                "overlap_mode": overlap_mode,
                **base_sum,
            }
        )

        for scen_id, mode in SECOND_FADE_SCENARIOS:
            all_trades: list[StructuralTrade] = []
            cap5_trades: list[StructuralTrade] = []
            for sdir, events in cache:
                trades = replay_phase164_structural_exit(
                    events,
                    pilot_config=pilot_config,
                    poll_interval_sec=float(getattr(pilot_config, "live_poll_interval_sec", 5.0) or 5.0),
                    structural_exit_policy="combined_structural_exit_v1_fade_hybrid_shadow",
                    second_fade_mode=mode,
                    overlap_mode=overlap_mode,
                )
                all_trades.extend(trades)
                if cap5_keys:
                    cap5_trades.extend([t for t in trades if (t.symbol, t.entry_time) in cap5_keys])

                # gain loss recalc against Phase161 improved cohort
                session_id = f"{sdir.parent.name}/{sdir.name}" if sdir.parent.name.isdigit() else sdir.name
                for t in trades:
                    k = (session_id, t.symbol, t.entry_time)
                    g = g_by_key.get(k)
                    if not g:
                        continue
                    base_t = baseline_by_key.get(k)
                    if not base_t:
                        continue
                    gain_161 = float(g.get("scenario_pnl") or 0) - float(g.get("actual_pnl") or 0)
                    gain_164 = float(t.realized_pnl_pct) - float(base_t.realized_pnl_pct)
                    gain_rows.append(
                        {
                            "second_fade_scenario": scen_id,
                            "overlap_mode": overlap_mode,
                            "session": session_id,
                            "symbol": t.symbol,
                            "entry_time": t.entry_time,
                            "gain_161": round(gain_161, 4),
                            "gain_164": round(gain_164, 4),
                            "gain_lost": round(gain_161 - gain_164, 4),
                            "base_reason": base_t.close_reason,
                            "scenario_reason": t.close_reason,
                        }
                    )

            summ = _summarize_trades(all_trades)
            stage1_pf[scen_id] = float(summ.get("pf") or 0)
            scenario_rows.append(
                {
                    "second_fade_scenario": scen_id,
                    "overlap_mode": overlap_mode,
                    **summ,
                }
            )
            second_fade_rows.append(
                {
                    "second_fade_scenario": scen_id,
                    "overlap_mode": overlap_mode,
                    "pf": summ.get("pf"),
                    "total_pnl": summ.get("total_pnl"),
                    "avg_pnl": summ.get("avg_pnl"),
                    "max_loss": summ.get("max_loss"),
                    "fade_watch_entered": summ.get("fade_watch_entered"),
                    "second_fade_exit_count": summ.get("second_fade_exit_count"),
                    "breakdown_exit_count": summ.get("breakdown_exit_count"),
                    "overlap_exit_count": summ.get("overlap_exit_count"),
                    "range_hold_protect_count": summ.get("range_hold_protect_count"),
                    "avg_hold_sec": summ.get("avg_hold_sec"),
                    "stop_hit_count": summ.get("stop_hit_count"),
                }
            )
            if cap5_trades:
                cap5_s = _summarize_trades(cap5_trades)
                cap5_rows.append(
                    {
                        "second_fade_scenario": scen_id,
                        "overlap_mode": overlap_mode,
                        "subset": "cap5_only",
                        **cap5_s,
                    }
                )

    # Pick top2 PF candidates under overlap=current (excluding A itself).
    ranked = sorted(
        [s for s, _ in SECOND_FADE_SCENARIOS if s != "A_hybrid_current"],
        key=lambda s: stage1_pf.get(s, 0.0),
        reverse=True,
    )
    top2 = ranked[:2] if ranked else []
    overlap_plan["disable"].extend(top2)
    overlap_plan["protect_fade_watch"].extend(top2)

    # Stage2: overlap interaction check (baseline + A + top2 only)
    for overlap_mode in ("disable", "protect_fade_watch"):
        planned = overlap_plan[overlap_mode]
        # baseline
        baseline_by_key: dict[tuple[str, str, str], StructuralTrade] = {}
        baseline_all: list[StructuralTrade] = []
        for sdir, events in cache:
            trades = replay_phase164_structural_exit(
                events,
                pilot_config=pilot_config,
                poll_interval_sec=float(getattr(pilot_config, "live_poll_interval_sec", 5.0) or 5.0),
                structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1,
                second_fade_mode="current",
                overlap_mode=overlap_mode,
            )
            baseline_all.extend(trades)
            session_id = f"{sdir.parent.name}/{sdir.name}" if sdir.parent.name.isdigit() else sdir.name
            for t in trades:
                baseline_by_key[(session_id, t.symbol, t.entry_time)] = t

        base_sum = _summarize_trades(baseline_all)
        scenario_rows.append(
            {
                "second_fade_scenario": "BASELINE_combined_v1",
                "overlap_mode": overlap_mode,
                **base_sum,
            }
        )

        for scen_id, mode in SECOND_FADE_SCENARIOS:
            if scen_id not in planned:
                continue
            all_trades: list[StructuralTrade] = []
            cap5_trades: list[StructuralTrade] = []
            for sdir, events in cache:
                trades = replay_phase164_structural_exit(
                    events,
                    pilot_config=pilot_config,
                    poll_interval_sec=float(getattr(pilot_config, "live_poll_interval_sec", 5.0) or 5.0),
                    structural_exit_policy="combined_structural_exit_v1_fade_hybrid_shadow",
                    second_fade_mode=mode,
                    overlap_mode=overlap_mode,
                )
                all_trades.extend(trades)
                if cap5_keys:
                    cap5_trades.extend([t for t in trades if (t.symbol, t.entry_time) in cap5_keys])

                session_id = f"{sdir.parent.name}/{sdir.name}" if sdir.parent.name.isdigit() else sdir.name
                for t in trades:
                    k = (session_id, t.symbol, t.entry_time)
                    g = g_by_key.get(k)
                    if not g:
                        continue
                    base_t = baseline_by_key.get(k)
                    if not base_t:
                        continue
                    gain_161 = float(g.get("scenario_pnl") or 0) - float(g.get("actual_pnl") or 0)
                    gain_164 = float(t.realized_pnl_pct) - float(base_t.realized_pnl_pct)
                    gain_rows.append(
                        {
                            "second_fade_scenario": scen_id,
                            "overlap_mode": overlap_mode,
                            "session": session_id,
                            "symbol": t.symbol,
                            "entry_time": t.entry_time,
                            "gain_161": round(gain_161, 4),
                            "gain_164": round(gain_164, 4),
                            "gain_lost": round(gain_161 - gain_164, 4),
                            "base_reason": base_t.close_reason,
                            "scenario_reason": t.close_reason,
                        }
                    )

            summ = _summarize_trades(all_trades)
            scenario_rows.append(
                {
                    "second_fade_scenario": scen_id,
                    "overlap_mode": overlap_mode,
                    **summ,
                }
            )
            second_fade_rows.append(
                {
                    "second_fade_scenario": scen_id,
                    "overlap_mode": overlap_mode,
                    "pf": summ.get("pf"),
                    "total_pnl": summ.get("total_pnl"),
                    "avg_pnl": summ.get("avg_pnl"),
                    "max_loss": summ.get("max_loss"),
                    "fade_watch_entered": summ.get("fade_watch_entered"),
                    "second_fade_exit_count": summ.get("second_fade_exit_count"),
                    "breakdown_exit_count": summ.get("breakdown_exit_count"),
                    "overlap_exit_count": summ.get("overlap_exit_count"),
                    "range_hold_protect_count": summ.get("range_hold_protect_count"),
                    "avg_hold_sec": summ.get("avg_hold_sec"),
                    "stop_hit_count": summ.get("stop_hit_count"),
                }
            )
            if cap5_trades:
                cap5_s = _summarize_trades(cap5_trades)
                cap5_rows.append(
                    {
                        "second_fade_scenario": scen_id,
                        "overlap_mode": overlap_mode,
                        "subset": "cap5_only",
                        **cap5_s,
                    }
                )

    # Gain-loss aggregation by scenario x overlap
    gain_summary: dict[tuple[str, str], dict[str, Any]] = {}
    for r in gain_rows:
        key = (str(r["second_fade_scenario"]), str(r["overlap_mode"]))
        g = gain_summary.setdefault(
            key,
            {
                "second_fade_scenario": key[0],
                "overlap_mode": key[1],
                "trade_count": 0,
                "sum_gain_161": 0.0,
                "sum_gain_164": 0.0,
                "sum_gain_lost": 0.0,
            },
        )
        g["trade_count"] += 1
        g["sum_gain_161"] += float(r["gain_161"])
        g["sum_gain_164"] += float(r["gain_164"])
        g["sum_gain_lost"] += float(r["gain_lost"])

    gain_summary_rows = []
    for k, v in sorted(gain_summary.items(), key=lambda kv: (-abs(kv[1]["sum_gain_164"]), kv[0][0], kv[0][1])):
        gain_summary_rows.append(
            {
                **v,
                "sum_gain_161": round(v["sum_gain_161"], 4),
                "sum_gain_164": round(v["sum_gain_164"], 4),
                "sum_gain_lost": round(v["sum_gain_lost"], 4),
            }
        )

    verdict = determine_phase164_verdict(scenario_rows, gain_summary_rows)

    return {
        "verdict": verdict[0],
        "verdict_notes": verdict[1],
        "session_count": len(session_dirs),
        "phase161_improved_trade_count": len(g_improved),
        "scenario_rows": scenario_rows,
        "second_fade_scenarios": second_fade_rows,
        "gain_loss_recalc": gain_summary_rows,
        "cap5_subset": cap5_rows,
    }


def determine_phase164_verdict(
    scenario_rows: Sequence[Mapping[str, Any]],
    gain_rows: Sequence[Mapping[str, Any]],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    # Prefer robust improvements in overlap=current first; then see if overlap_protect needed.
    def _row(scen: str, overlap: str) -> dict[str, Any]:
        return next(
            (
                r
                for r in scenario_rows
                if r.get("second_fade_scenario") == scen and r.get("overlap_mode") == overlap
            ),
            {},
        )

    base = _row("BASELINE_combined_v1", "current")
    base_pf = float(base.get("pf") or 0)

    best = None
    best_pf = -1.0
    for scen, _mode in SECOND_FADE_SCENARIOS:
        r = _row(scen, "current")
        pf = float(r.get("pf") or 0)
        if pf > best_pf:
            best_pf = pf
            best = scen

    notes.append(f"baseline_pf_current={base_pf:.4f}")
    notes.append(f"best_pf_current={best_pf:.4f} scen={best}")

    # Heuristics aligned to requested labels
    if best in ("B_second_fade_disabled",) and best_pf >= base_pf + 0.03:
        return "second_fade_disable_promising", notes
    if best in ("C_second_fade_pnl_neg", "D_second_fade_new_low", "E_second_fade_strict") and best_pf >= base_pf + 0.03:
        return "second_fade_strict_promising", notes

    # If overlap protection materially changes PF for any scenario, flag it.
    overlap_delta = float(_row("A_hybrid_current", "protect_fade_watch").get("pf") or 0) - float(
        _row("A_hybrid_current", "current").get("pf") or 0
    )
    if overlap_delta >= 0.05:
        notes.append(f"overlap_protect_pf_delta={overlap_delta:.4f}")
        return "overlap_protection_needed", notes

    br = _row("F_breakdown_only", "current")
    if float(br.get("pf") or 0) >= base_pf + 0.03:
        return "breakdown_only_promising", notes

    return "no_replay_robust_improvement", notes


def write_phase164_outputs(result: Mapping[str, Any], *, reports_dir: Path, docs_dir: Path) -> dict[str, str]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": reports_dir / "phase164_fade_hybrid_refinement_review.json",
        "second_fade": reports_dir / "phase164_second_fade_scenarios.csv",
        "overlap": reports_dir / "phase164_overlap_interaction_scenarios.csv",
        "gain": reports_dir / "phase164_gain_loss_recalc.csv",
        "cap5": reports_dir / "phase164_cap5_subset.csv",
        "md": docs_dir / "phase164_recommendation.md",
    }

    design = {
        k: v
        for k, v in result.items()
        if k
        not in (
            "scenario_rows",
            "second_fade_scenarios",
            "gain_loss_recalc",
            "cap5_subset",
        )
    }
    paths["json"].write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(paths["second_fade"], result.get("second_fade_scenarios") or [])
    _write_csv(paths["overlap"], result.get("scenario_rows") or [])
    _write_csv(paths["gain"], result.get("gain_loss_recalc") or [])
    _write_csv(paths["cap5"], result.get("cap5_subset") or [])

    md = [
        "# Phase 164: fade hybrid refinement (review)",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        "",
        "## Notes",
        "",
    ]
    for n in (result.get("verdict_notes") or []):
        md.append(f"- {n}")
    md.append("")
    md.append("## Constraints")
    md.append("")
    md.append("- Review only; no production YAML changes; no entry/universe changes; order_enabled=false; paper_only=true.")
    paths["md"].write_text("\n".join(md) + "\n", encoding="utf-8")

    return {k: str(v) for k, v in paths.items()}

