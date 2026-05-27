"""
Phase 136: cap=3 + ExposureGate entry replay with fade-exit switch scenarios (review only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from research.exposure_gate import REJECT_MAX_CONCURRENT, ExposureGate, ExposureGateConfig
from research.fade_switch_policy_review import FADE_EXIT_REASONS
from research.fade_watch_shadow import MOMENTUM_EPS, PNL_EPS, REACCEL_MIN_SIGNALS, _pnl, _reaccel_score
from research.mfe_mae_exit_review import parse_ts, pnl_pct
from research.range_hold_exit_review import _breakdown_on_tick
from research.runtime_pilot_policy_review import _trade_from_candidate
from research.structural_exit_policies import (
    POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    combined_exit_signal_on_latest_tick,
    tick_from_candidate,
)
from research.switch_old_vs_new_review import MAX_PAIR_SEC

SCENARIO_CURRENT = "A_current"
SCENARIO_COOLDOWN = "B_fade_switch_cooldown"
SCENARIO_BLOCK = "C_fade_switch_block"

FADE_SWITCH_SCENARIOS = (SCENARIO_CURRENT, SCENARIO_COOLDOWN, SCENARIO_BLOCK)
COOLDOWN_MIN_OBS_TICKS = 2
REACCEL_PNL_EPS = 0.03
NO_TICK_CROSS_ATTEMPT_RELEASE = True


@dataclass
class PostFadeState:
    symbol: str
    fade_exit_ts: float
    fade_exit_time: str
    fade_exit_reason: str
    entry_price: float
    fade_price: float
    fade_pnl: float
    fade_momentum: Optional[float] = None
    post_fade_ticks: int = 0
    peak_pnl: float = 0.0
    post_low: float = 0.0
    peak_price: float = 0.0
    released: bool = False
    release_reason: str = ""
    breakdown_confirmed: bool = False
    reacceleration_confirmed: bool = False

    @classmethod
    def from_fade_close(
        cls,
        *,
        symbol: str,
        fade_exit_time: str,
        fade_exit_ts: float,
        fade_exit_reason: str,
        entry_price: float,
        fade_price: float,
        fade_momentum: Optional[float],
        fade_pnl: float,
    ) -> PostFadeState:
        return cls(
            symbol=symbol,
            fade_exit_ts=fade_exit_ts,
            fade_exit_time=fade_exit_time,
            fade_exit_reason=fade_exit_reason,
            entry_price=entry_price,
            fade_price=fade_price,
            fade_pnl=fade_pnl,
            fade_momentum=fade_momentum,
            peak_pnl=max(fade_pnl, _pnl(entry_price, fade_price)),
            post_low=fade_price,
            peak_price=fade_price,
        )


@dataclass
class SimPosition:
    symbol: str
    entry_time: str
    entry_ts: float
    entry_price: float
    rich_ticks: list[dict[str, Any]] = field(default_factory=list)
    close_time: str = ""
    close_ts: float = 0.0
    close_reason: str = ""
    realized_pnl_pct: float = 0.0
    replaced_by_overlap: bool = False

    @property
    def is_open(self) -> bool:
        return not self.close_reason


@dataclass
class Cap3ReplayResult:
    scenario: str
    session_id: str
    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejects: list[dict[str, Any]] = field(default_factory=list)
    event_log: list[dict[str, Any]] = field(default_factory=list)
    closed_positions: list[SimPosition] = field(default_factory=list)
    post_fade_states: list[PostFadeState] = field(default_factory=list)
    switch_count: int = 0
    switch_block_count: int = 0
    release_reason_counts: dict[str, int] = field(default_factory=dict)


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gl = abs(sum(losses))
    if gl <= 0:
        return None if not wins else float("inf")
    return round(sum(wins) / gl, 4)


def _sync_gate_slots(gate: ExposureGate, open_positions: Sequence[SimPosition], *, horizon_ts: float) -> None:
    gate.state.open_slots = [
        (p.entry_ts, p.close_ts if p.close_ts > 0 else horizon_ts + 86400, p.symbol)
        for p in open_positions
        if p.is_open
    ]


def _process_post_fade_tick(
    state: PostFadeState,
    *,
    price: float,
    momentum: Optional[float],
    scenario: str,
) -> Optional[str]:
    if state.released:
        return state.release_reason

    state.post_fade_ticks += 1
    pnl = _pnl(state.entry_price, price)
    prev_peak_px = state.peak_price
    if price > state.peak_price:
        state.peak_price = price
    if price < state.post_low:
        state.post_low = price

    new_high = price > prev_peak_px + 1e-9 and price > state.fade_price
    mfe_up = pnl > state.peak_pnl + 1e-9
    if mfe_up:
        state.peak_pnl = pnl

    momentum_up = (
        momentum is not None
        and state.fade_momentum is not None
        and momentum > state.fade_momentum + MOMENTUM_EPS
    )
    reaccel_score = _reaccel_score(
        price=price,
        fade_price=state.fade_price,
        pnl=pnl,
        peak_pnl=state.peak_pnl,
        momentum=momentum,
        fade_momentum=state.fade_momentum,
        new_high_tick=new_high,
        mfe_updated_tick=mfe_up,
        vwap_above=None,
    )
    breakdown = _breakdown_on_tick(
        px=price,
        pnl=pnl,
        mom=momentum,
        fade_momentum=state.fade_momentum,
        fade_price=state.fade_price,
        recent_low=state.post_low,
        peak_pnl=state.peak_pnl,
        post_low=state.post_low,
        prev_post_low=state.post_low,
        new_high_since_fade=new_high,
    )
    state.breakdown_confirmed = breakdown
    state.reacceleration_confirmed = (
        reaccel_score >= REACCEL_MIN_SIGNALS or pnl >= state.fade_pnl + REACCEL_PNL_EPS
    )

    min_ticks = COOLDOWN_MIN_OBS_TICKS if scenario == SCENARIO_COOLDOWN else 0
    if state.post_fade_ticks < min_ticks:
        return None

    if breakdown:
        state.released = True
        state.release_reason = "old_breakdown_confirmed"
    elif state.reacceleration_confirmed:
        state.released = True
        state.release_reason = "old_reacceleration_confirmed"
    return state.release_reason if state.released else None


def _cross_symbol_cooldown_blocks(
    post_fade: Mapping[str, PostFadeState],
    *,
    new_symbol: str,
    scenario: str,
) -> tuple[bool, Optional[str], Optional[str]]:
    if scenario == SCENARIO_CURRENT:
        return False, None, None
    for sym, st in post_fade.items():
        if sym == new_symbol or st.released:
            continue
        if NO_TICK_CROSS_ATTEMPT_RELEASE and st.post_fade_ticks == 0:
            st.released = True
            st.release_reason = "old_no_post_fade_ticks"
            continue
        return True, sym, st.release_reason or "cooldown_active"
    return False, None, None


def _close_position(
    pos: SimPosition,
    *,
    close_time: str,
    close_ts: float,
    close_price: float,
    reason: str,
    result: Cap3ReplayResult,
) -> PostFadeState | None:
    pos.close_time = close_time
    pos.close_ts = close_ts
    pos.close_reason = reason
    pos.realized_pnl_pct = round(_pnl(pos.entry_price, close_price), 4)
    result.closed_positions.append(pos)
    result.event_log.append(
        {
            "event_kind": "position_close",
            "scenario": result.scenario,
            "symbol": pos.symbol,
            "entry_time": pos.entry_time,
            "close_time": close_time,
            "close_reason": reason,
            "pnl_pct": pos.realized_pnl_pct,
        }
    )
    if reason in FADE_EXIT_REASONS:
        pf = PostFadeState.from_fade_close(
            symbol=pos.symbol,
            fade_exit_time=close_time,
            fade_exit_ts=close_ts,
            fade_exit_reason=reason,
            entry_price=pos.entry_price,
            fade_price=close_price,
            fade_momentum=float(pos.rich_ticks[-1].get("momentum") or 0) if pos.rich_ticks else None,
            fade_pnl=pos.realized_pnl_pct,
        )
        result.post_fade_states.append(pf)
        return pf
    return None


def simulate_cap3_entry_replay(
    events: Sequence[Mapping[str, Any]],
    *,
    session_id: str,
    scenario: str,
    gate: ExposureGate,
    exit_cfg: Any,
    session_end: str,
    session_end_ts: float,
) -> Cap3ReplayResult:
    """Chronological cap=3 replay on candidate stream with structural v1 exits."""
    result = Cap3ReplayResult(scenario=scenario, session_id=session_id)
    ordered = sorted(events, key=lambda e: int(e.get("message_index") or 0))
    open_positions: list[SimPosition] = []
    post_fade: dict[str, PostFadeState] = {}
    recent_fades: list[PostFadeState] = []
    mc_reject_seen: set[tuple[str, str]] = set()

    def open_by_symbol() -> dict[str, SimPosition]:
        return {p.symbol: p for p in open_positions if p.is_open}

    def try_close_on_tick(pos: SimPosition, row: Mapping[str, Any], ts: float) -> None:
        tick = tick_from_candidate(dict(row), pos.entry_price, float(row.get("continuation_quality_score") or 0))
        tick["ts_epoch"] = ts
        pos.rich_ticks.append(tick)
        sig = combined_exit_signal_on_latest_tick(pos.rich_ticks, pos.entry_price, exit_cfg)
        if not sig:
            return
        pnl, reason, close_px = sig
        if reason in FADE_EXIT_REASONS or reason == "stop_hit":
            open_positions.remove(pos)
            pf = _close_position(
                pos,
                close_time=str(row.get("entry_time") or ""),
                close_ts=ts,
                close_price=float(close_px),
                reason=reason,
                result=result,
            )
            if pf:
                post_fade[pos.symbol] = pf
                recent_fades.append(pf)

    def _candidate_row(ev: Mapping[str, Any]) -> dict[str, Any]:
        return dict(ev)

    for ev in ordered:
        et = str(ev.get("event_type") or "")
        sym = str(ev.get("symbol") or "")
        ent_raw = str(ev.get("entry_time") or "")
        ts = parse_ts(ent_raw)
        price = float(ev.get("current_price") or 0)

        if et == "candidate":
            row = _candidate_row(ev)
            for pos in list(open_positions):
                if pos.symbol == sym and pos.is_open:
                    try_close_on_tick(pos, row, ts)

            if sym in post_fade and not post_fade[sym].released and price > 0:
                rel = _process_post_fade_tick(
                    post_fade[sym],
                    price=price,
                    momentum=float(ev.get("momentum_continuation_score") or 0),
                    scenario=scenario,
                )
                if rel:
                    result.release_reason_counts[rel] = result.release_reason_counts.get(rel, 0) + 1
                    result.event_log.append(
                        {
                            "event_kind": "fade_switch_cooldown_released",
                            "scenario": scenario,
                            "symbol": sym,
                            "release_reason": rel,
                        }
                    )
            continue

        if et != "accepted" or price <= 0:
            continue

        row = _candidate_row(ev)
        trade = _trade_from_candidate(row)
        _sync_gate_slots(gate, open_positions, horizon_ts=ts)
        decision = gate.evaluate_entry(trade)
        if not decision.accept:
            if decision.reason == REJECT_MAX_CONCURRENT:
                mc_key = (sym, ent_raw)
                if mc_key not in mc_reject_seen:
                    mc_reject_seen.add(mc_key)
                    result.rejects.append(
                        {
                            "symbol": sym,
                            "entry_time": ent_raw,
                            "reject_reason": decision.reason,
                            "quality": decision.continuation_quality_score,
                        }
                    )
            continue

        ob = open_by_symbol()
        if sym in ob:
            old = ob[sym]
            open_positions.remove(old)
            old.replaced_by_overlap = True
            _close_position(
                old,
                close_time=ent_raw,
                close_ts=ts,
                close_price=price,
                reason="overlap_replaced_review",
                result=result,
            )

        blocked, cooled_sym, block_reason = _cross_symbol_cooldown_blocks(
            post_fade, new_symbol=sym, scenario=scenario
        )
        if blocked:
            result.switch_block_count += 1
            result.event_log.append(
                {
                    "event_kind": "fade_switch_blocked",
                    "scenario": scenario,
                    "symbol": sym,
                    "entry_time": ent_raw,
                    "cooldown_symbol": cooled_sym,
                    "block_reason": block_reason,
                }
            )
            continue

        pos = SimPosition(
            symbol=sym,
            entry_time=ent_raw,
            entry_ts=ts,
            entry_price=price,
        )
        open_positions.append(pos)
        result.accepted.append(
            {
                "symbol": sym,
                "entry_time": ent_raw,
                "entry_price": price,
                "quality": decision.continuation_quality_score,
            }
        )
        result.event_log.append(
            {
                "event_kind": "entry_accepted",
                "scenario": scenario,
                "symbol": sym,
                "entry_time": ent_raw,
            }
        )

        for pf in recent_fades:
            if pf.symbol == sym:
                continue
            if pf.fade_exit_ts <= ts <= pf.fade_exit_ts + MAX_PAIR_SEC:
                result.switch_count += 1
                result.event_log.append(
                    {
                        "event_kind": "fade_switch",
                        "scenario": scenario,
                        "old_symbol": pf.symbol,
                        "new_symbol": sym,
                        "old_close_time": pf.fade_exit_time,
                        "gap_sec": round(ts - pf.fade_exit_ts, 1),
                    }
                )
                break

    for pos in list(open_positions):
        if not pos.is_open:
            continue
        close_px = pos.entry_price
        if pos.rich_ticks:
            close_px = float(pos.rich_ticks[-1].get("price") or close_px)
        open_positions.remove(pos)
        pf = _close_position(
            pos,
            close_time=session_end,
            close_ts=session_end_ts,
            close_price=close_px,
            reason="session_end",
            result=result,
        )
        if pf and scenario != SCENARIO_CURRENT:
            pf.released = True
            pf.release_reason = "session_close"
            result.release_reason_counts["session_close"] = (
                result.release_reason_counts.get("session_close", 0) + 1
            )

    for st in post_fade.values():
        if not st.released:
            st.released = True
            st.release_reason = "session_close"
            result.release_reason_counts["session_close"] = (
                result.release_reason_counts.get("session_close", 0) + 1
            )

    return result


def summarize_scenario(result: Cap3ReplayResult) -> dict[str, Any]:
    pnls = [p.realized_pnl_pct for p in result.closed_positions]
    return {
        "scenario": result.scenario,
        "session_id": result.session_id,
        "total_pnl_proxy": round(sum(pnls), 4) if pnls else 0.0,
        "pf_proxy": _profit_factor(pnls),
        "accepted_count": len(result.accepted),
        "rejected_max_concurrent_count": sum(
            1 for r in result.rejects if r.get("reject_reason") == REJECT_MAX_CONCURRENT
        ),
        "switch_count": result.switch_count,
        "switch_block_count": result.switch_block_count,
        "release_reason_counts": dict(result.release_reason_counts),
        "closed_trade_count": len(result.closed_positions),
    }
