"""Distinct TAER EXIT state machines (setup-aware). Causal only — no future labels."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


def _tick(price: float) -> float:
    p = float(price)
    if p <= 1000:
        return 0.1
    if p <= 3000:
        return 0.5
    if p <= 5000:
        return 1.0
    if p <= 10000:
        return 1.0
    if p <= 30000:
        return 5.0
    if p <= 50000:
        return 10.0
    return 50.0


# Frozen thresholds for P2_EXIT_PRECOMMIT (no PnL tuning)
EXIT_THRESHOLDS: dict[str, Any] = {
    "pullback_structural": {
        "level_break_ticks": 1.0,
        "level_break_hold_sec": 5.0,
        "vwap_break_atr": 0.20,
        "pullback_low_break": True,
        "bid_support_grace_sec": 8.0,
    },
    "range_structural": {
        "reentry_width_frac": 0.25,
        "reentry_hold_sec": 10.0,
        "range_low_break": True,
        "spread_spike_bps": 12.0,
    },
    "continuation": {
        "min_hold_sec": 25.0,
        "no_progress_sec": 55.0,
        "cost_bps": 5.0,
        "min_update_speed_10s": 2,
        "volume_persist_min_ratio": 0.35,
    },
    "hybrid": {
        "giveback_frac": 0.55,
        "structure_until_cost_cover": True,
        "flow_dead_updates": 1,
    },
    "hard_stop_bps": -25.0,
    "max_hold_sec": 300.0,
}


@dataclass
class ExitPos:
    symbol: str
    setup_type: str
    exit_candidate: str
    entry_t: float
    entry_ask: float
    entry_mid: float
    reclaim_level: float
    pullback_low: Optional[float]
    range_high: Optional[float]
    range_low: Optional[float]
    vwap_at_entry: Optional[float]
    atr: Optional[float]
    state: str = "OPEN_INIT"
    peak_mid: float = float("nan")
    mfe: float = 0.0
    mae: float = 0.0
    last_progress_t: float = float("nan")
    new_high_count: int = 0
    below_level_since: float = float("nan")
    cost_covered: bool = False
    transitions: list[dict[str, Any]] = field(default_factory=list)
    vol30_at_entry: Optional[float] = None


def _emit(pos: ExitPos, t: float, to: str, reason: str) -> None:
    pos.transitions.append({
        "t": t, "from": pos.state, "to": to, "reason": reason,
        "exit_candidate": pos.exit_candidate, "setup_type": pos.setup_type,
    })
    pos.state = to


def _update_mfe_mae(pos: ExitPos, mid: float, t: float) -> None:
    d = mid - pos.entry_mid
    if d > pos.mfe:
        pos.mfe = d
        pos.last_progress_t = t
    if d < pos.mae:
        pos.mae = d
    if not (pos.peak_mid == pos.peak_mid) or mid > pos.peak_mid + 1e-12:
        if pos.peak_mid == pos.peak_mid:
            pos.new_high_count += 1
        pos.peak_mid = mid
        pos.last_progress_t = t


def _gross_bps(pos: ExitPos, bid: float) -> float:
    if pos.entry_ask <= 0:
        return 0.0
    return (bid / pos.entry_ask - 1.0) * 10000.0


def _cost_covered(pos: ExitPos, bid: float) -> bool:
    return _gross_bps(pos, bid) >= EXIT_THRESHOLDS["continuation"]["cost_bps"] - 1e-12


def step_exit(
    pos: ExitPos,
    *,
    t: float,
    bid: float,
    ask: float,
    mid: float,
    vwap: Optional[float],
    spread_bps: Optional[float],
    volume_30s: Optional[float],
    price_update_count_10s: Optional[int],
) -> Optional[dict[str, Any]]:
    """Advance EXIT SM one event. Returns exit dict or None."""
    _update_mfe_mae(pos, mid, t)
    hold = t - pos.entry_t
    if _cost_covered(pos, bid):
        pos.cost_covered = True

    # hard stops shared
    if _gross_bps(pos, bid) <= EXIT_THRESHOLDS["hard_stop_bps"] + 1e-12:
        _emit(pos, t, "EXIT", "HARD_STOP")
        return _pack(pos, t, bid, "HARD_STOP")
    if hold >= EXIT_THRESHOLDS["max_hold_sec"] - 1e-9:
        _emit(pos, t, "EXIT", "MAX_HOLD")
        return _pack(pos, t, bid, "MAX_HOLD")

    if pos.state == "OPEN_INIT":
        _emit(pos, t, "STRUCTURE_HOLD", "INIT")
        # fall through same event? Spec: 1 advance per obs — return None this tick after init
        return None

    xc = pos.exit_candidate
    if xc == "X_STRUCTURAL":
        return _structural(pos, t, bid, mid, vwap, spread_bps, hold)
    if xc == "X_CONTINUATION":
        return _continuation(pos, t, bid, mid, volume_30s, price_update_count_10s, hold)
    if xc == "X_HYBRID":
        return _hybrid(pos, t, bid, mid, vwap, spread_bps, volume_30s, price_update_count_10s, hold)
    _emit(pos, t, "EXIT", "UNKNOWN_CANDIDATE")
    return _pack(pos, t, bid, "UNKNOWN_CANDIDATE")


def _structural(
    pos: ExitPos, t: float, bid: float, mid: float,
    vwap: Optional[float], spread_bps: Optional[float], hold: float,
) -> Optional[dict[str, Any]]:
    tick = _tick(pos.reclaim_level)
    if pos.setup_type == "PULLBACK_RECLAIM":
        th = EXIT_THRESHOLDS["pullback_structural"]
        level = pos.reclaim_level
        broken = mid < level - th["level_break_ticks"] * tick - 1e-12
        if broken:
            if not (pos.below_level_since == pos.below_level_since):
                pos.below_level_since = t
            elif t - pos.below_level_since >= th["level_break_hold_sec"] - 1e-9:
                if pos.state != "EXIT":
                    _emit(pos, t, "EXIT", "RECLAIM_LEVEL_BREAK")
                return _pack(pos, t, bid, "RECLAIM_LEVEL_BREAK")
        else:
            pos.below_level_since = float("nan")
        if th["pullback_low_break"] and pos.pullback_low is not None:
            if mid < float(pos.pullback_low) - 1e-12:
                _emit(pos, t, "EXIT", "PULLBACK_LOW_BREAK")
                return _pack(pos, t, bid, "PULLBACK_LOW_BREAK")
        if vwap is not None and pos.atr and pos.atr > 0:
            if mid < float(vwap) - th["vwap_break_atr"] * pos.atr:
                _emit(pos, t, "EXIT", "VWAP_BREAK")
                return _pack(pos, t, bid, "VWAP_BREAK")
        if bid < level - tick - 1e-12:
            # bid support lost; grace then exit
            if not (pos.below_level_since == pos.below_level_since):
                pos.below_level_since = t
            elif t - pos.below_level_since >= th["bid_support_grace_sec"] - 1e-9:
                _emit(pos, t, "EXIT", "BID_SUPPORT_LOST")
                return _pack(pos, t, bid, "BID_SUPPORT_LOST")
        if pos.state == "STRUCTURE_HOLD" and hold >= 15.0:
            _emit(pos, t, "PROGRESS_CHECK", "STRUCTURE_OK")
        return None

    # RANGE_BREAKOUT structural
    th = EXIT_THRESHOLDS["range_structural"]
    rh = pos.range_high if pos.range_high is not None else pos.reclaim_level
    rl = pos.range_low
    width = (rh - rl) if (rl is not None and rh is not None) else None
    if spread_bps is not None and spread_bps > th["spread_spike_bps"]:
        _emit(pos, t, "EXIT", "SPREAD_SPIKE")
        return _pack(pos, t, bid, "SPREAD_SPIKE")
    if th["range_low_break"] and rl is not None and mid < float(rl) - 1e-12:
        _emit(pos, t, "EXIT", "RANGE_LOW_BREAK")
        return _pack(pos, t, bid, "RANGE_LOW_BREAK")
    if width and width > 0:
        reentry = mid < rh - th["reentry_width_frac"] * width
        if reentry:
            if not (pos.below_level_since == pos.below_level_since):
                pos.below_level_since = t
            elif t - pos.below_level_since >= th["reentry_hold_sec"] - 1e-9:
                _emit(pos, t, "EXIT", "RANGE_REENTRY")
                return _pack(pos, t, bid, "RANGE_REENTRY")
        else:
            pos.below_level_since = float("nan")
    if pos.state == "STRUCTURE_HOLD" and hold >= 15.0:
        _emit(pos, t, "PROGRESS_CHECK", "STRUCTURE_OK")
    return None


def _continuation(
    pos: ExitPos, t: float, bid: float, mid: float,
    volume_30s: Optional[float], pu10: Optional[int], hold: float,
) -> Optional[dict[str, Any]]:
    th = EXIT_THRESHOLDS["continuation"]
    if pos.state == "STRUCTURE_HOLD":
        _emit(pos, t, "PROGRESS_CHECK", "CONT_START")
        return None
    if hold + 1e-9 < th["min_hold_sec"]:
        return None  # protect late continuation from early exit
    # progress stall
    if (pos.last_progress_t == pos.last_progress_t) and (
        t - pos.last_progress_t >= th["no_progress_sec"] - 1e-9
    ):
        # only exit if not covering cost OR volume dead
        vol_dead = False
        if pos.vol30_at_entry and pos.vol30_at_entry > 0 and volume_30s is not None:
            vol_dead = float(volume_30s) / float(pos.vol30_at_entry) < th["volume_persist_min_ratio"]
        slow = pu10 is not None and int(pu10) < th["min_update_speed_10s"]
        if (not pos.cost_covered) or vol_dead or slow:
            reason = "NO_PROGRESS"
            if not pos.cost_covered and (vol_dead or slow):
                reason = "NO_PROGRESS_UNCOVERED"
            elif vol_dead:
                reason = "VOLUME_FADE"
            elif slow:
                reason = "UPDATE_SPEED_DEAD"
            _emit(pos, t, "EXIT", reason)
            return _pack(pos, t, bid, reason)
        # cost covered + still some life → trend management, keep
        if pos.state != "TREND_MANAGEMENT":
            _emit(pos, t, "TREND_MANAGEMENT", "HOLD_LATE_CONTINUATION")
    if pos.state == "PROGRESS_CHECK" and pos.cost_covered:
        _emit(pos, t, "PROFIT_PROTECTION", "COST_COVERED")
    return None


def _hybrid(
    pos: ExitPos, t: float, bid: float, mid: float,
    vwap: Optional[float], spread_bps: Optional[float],
    volume_30s: Optional[float], pu10: Optional[int], hold: float,
) -> Optional[dict[str, Any]]:
    th = EXIT_THRESHOLDS["hybrid"]
    # before cost cover: structural
    if not pos.cost_covered:
        if pos.state in ("STRUCTURE_HOLD", "OPEN_INIT", "PROGRESS_CHECK"):
            hit = _structural(pos, t, bid, mid, vwap, spread_bps, hold)
            if hit:
                hit["exit_reason"] = "HYBRID_STRUCTURE_" + hit["exit_reason"]
                return hit
        return None
    # after cost cover: giveback / flow
    if pos.state not in ("PROFIT_PROTECTION", "TREND_MANAGEMENT"):
        _emit(pos, t, "PROFIT_PROTECTION", "COST_COVERED")
        return None
    giveback = pos.mfe - (mid - pos.entry_mid)
    if pos.mfe > 0 and giveback >= th["giveback_frac"] * pos.mfe - 1e-12:
        _emit(pos, t, "EXIT", "MFE_GIVEBACK")
        return _pack(pos, t, bid, "MFE_GIVEBACK")
    if pu10 is not None and int(pu10) <= th["flow_dead_updates"]:
        if pos.vol30_at_entry and volume_30s is not None and pos.vol30_at_entry > 0:
            if float(volume_30s) / float(pos.vol30_at_entry) < 0.30:
                _emit(pos, t, "EXIT", "FLOW_DETERIORATION")
                return _pack(pos, t, bid, "FLOW_DETERIORATION")
    return None


def _pack(pos: ExitPos, t: float, bid: float, reason: str) -> dict[str, Any]:
    giveback = pos.mfe - (bid - pos.entry_mid)  # approx at bid
    return {
        "exit_t": t,
        "exit_bid": bid,
        "exit_reason": reason,
        "exit_state": pos.state,
        "hold_sec": t - pos.entry_t,
        "mfe_at_exit": pos.mfe,
        "mae_at_exit": pos.mae,
        "giveback_at_exit": giveback,
        "new_high_count": pos.new_high_count,
        "cost_covered": pos.cost_covered,
        "transitions": list(pos.transitions),
    }
