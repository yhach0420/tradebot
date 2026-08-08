"""PFQ EXIT state machines — distinct progress-struct vs protect."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from research.e1_x7_pfq.config import EXIT_THRESHOLDS


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


@dataclass
class PfqPos:
    symbol: str
    exit_candidate: str
    entry_t: float
    entry_ask: float
    entry_mid: float
    reclaim_level: float
    pullback_low: Optional[float]
    entry_pu10: Optional[int]
    state: str = "OPEN_INIT"
    mfe_bps: float = 0.0
    mae_bps: float = 0.0
    peak_bid: float = float("nan")
    cost_covered: bool = False
    transitions: list[dict] = field(default_factory=list)
    # Research-only BE5_FLOOR0 state (unused by baseline exits)
    profit_floor_armed: bool = False
    profit_floor_armed_at: Optional[float] = None
    profit_floor_armed_bid: Optional[float] = None
    profit_floor_armed_net_bps: Optional[float] = None
    max_executable_net_bps: float = float("-inf")


def _net_bps(entry_ask: float, bid: float) -> float:
    return (bid / entry_ask - 1.0) * 10000.0 - 5.0


def _emit(pos: PfqPos, t: float, to: str, reason: str) -> Optional[dict]:
    pos.transitions.append({"t": t, "from": pos.state, "to": to, "reason": reason})
    pos.state = to
    if to == "EXIT":
        return {"exit_reason": reason, "exit_state": pos.state, "t": t}
    return None


def step_pfq_exit(
    pos: PfqPos,
    *,
    t: float,
    bid: float,
    ask: float,
    mid: float,
    price_update_count_10s: Optional[int],
) -> Optional[dict[str, Any]]:
    thr = EXIT_THRESHOLDS
    hold = t - pos.entry_t
    net = _net_bps(pos.entry_ask, bid)
    # MFE/MAE in bps post-cost path using bid
    if net > pos.mfe_bps:
        pos.mfe_bps = net
        pos.peak_bid = bid
    if net < pos.mae_bps:
        pos.mae_bps = net

    # hard exits
    if hold >= float(thr["max_hold_sec"]) - 1e-12:
        return _emit(pos, t, "EXIT", "MAX_HOLD")
    if net <= float(thr["hard_stop_bps"]) + 1e-12:
        return _emit(pos, t, "EXIT", "HARD_STOP")

    tick = _tick(pos.reclaim_level)
    if pos.pullback_low is not None and mid < float(pos.pullback_low) - 1e-12:
        return _emit(pos, t, "EXIT", "PULLBACK_LOW_BREAK")
    if mid < pos.reclaim_level - float(thr["level_break_ticks"]) * tick - 1e-12:
        return _emit(pos, t, "EXIT", "RECLAIM_LEVEL_BREAK")

    # state machine
    if pos.state == "OPEN_INIT":
        _emit(pos, t, "STRUCTURE_HOLD", "ENTER_STRUCTURE")

    if pos.exit_candidate in ("PFQ_X_PROGRESS_STRUCT", "PFQ_X_PROGRESS_BE5_FLOOR0"):
        if net > pos.max_executable_net_bps:
            pos.max_executable_net_bps = net
        # Research-only floor: after hard invalidation checks above
        if pos.exit_candidate == "PFQ_X_PROGRESS_BE5_FLOOR0":
            if (not pos.profit_floor_armed) and net >= 5.0 - 1e-9:
                pos.profit_floor_armed = True
                pos.profit_floor_armed_at = float(t)
                pos.profit_floor_armed_bid = float(bid)
                pos.profit_floor_armed_net_bps = float(net)
            if pos.profit_floor_armed and net <= 0.0 + 1e-9:
                return _emit(pos, t, "EXIT", "PLUS5_BREAKEVEN_FLOOR")
        if pos.state == "STRUCTURE_HOLD":
            _emit(pos, t, "PROGRESS_CHECK", "ARM_PROGRESS")
        if pos.state == "PROGRESS_CHECK":
            if hold >= float(thr["progress_deadline_sec"]):
                if net < float(thr["progress_min_net_bps"]):
                    # activity deterioration
                    det = False
                    if pos.entry_pu10 is not None and price_update_count_10s is not None:
                        det = int(price_update_count_10s) <= max(
                            int(thr["update_deterioration_max"]), int(pos.entry_pu10) // 2
                        )
                    else:
                        det = True
                    if det:
                        return _emit(pos, t, "EXIT", "NO_PROGRESS_UPDATE_DEAD")
        return None

    # PFQ_X_PROTECT
    if pos.state == "STRUCTURE_HOLD":
        if net >= float(thr["protect_min_net_bps_for_arm"]) - 1e-12:
            pos.cost_covered = True
            _emit(pos, t, "COST_COVERED", "NET_PLUS_5")
            _emit(pos, t, "PROFIT_PROTECTION", "ARM_PROTECT")
    if pos.state == "PROFIT_PROTECTION":
        # giveback from MFE
        if pos.mfe_bps > 0 and (pos.mfe_bps - net) >= float(thr["protect_giveback_frac"]) * pos.mfe_bps:
            return _emit(pos, t, "EXIT", "MFE_GIVEBACK")
        if price_update_count_10s is not None and pos.entry_pu10 is not None:
            if int(price_update_count_10s) <= int(thr["update_deterioration_max"]) and net < pos.mfe_bps:
                return _emit(pos, t, "EXIT", "UPDATE_DETERIORATION")
        if mid < pos.reclaim_level - 1e-12 and net < pos.mfe_bps * 0.5:
            return _emit(pos, t, "EXIT", "RECLAIM_LEVEL_LOSS")
    return None
