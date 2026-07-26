"""Strategy contract: thesis, horizon, invalidation, exit states."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class StrategyContract:
    strategy_id: str
    name: str
    thesis: str
    expected_horizons_sec: tuple[int, ...]
    invariants: tuple[str, ...]
    warnings: tuple[str, ...]
    invalidations: tuple[str, ...]
    exit_modes: tuple[str, ...]  # X0..X6 labels strategy-specific


Z1 = StrategyContract(
    strategy_id="Z1",
    name="Pullback Reclaim Continuation",
    thesis="Controlled pullback after impulse ends; price/volume/flow/board realign upward.",
    expected_horizons_sec=(30, 120, 300),
    invariants=("no_lower_low", "reclaim_hold", "sell_flow_not_accelerating"),
    warnings=("bid_thinning", "spread_widening"),
    invalidations=("pullback_low_break", "reclaim_fail", "lower_low", "bid_support_gone"),
    exit_modes=("X0", "X1", "X2", "X3", "X4", "X5", "X6"),
)

Z2 = StrategyContract(
    strategy_id="Z2",
    name="Breakout Volume Continuation",
    thesis="Clear high/range break with volume/flow/board support continues.",
    expected_horizons_sec=(15, 60, 180),
    invariants=("above_break_level", "buy_flow_persist"),
    warnings=("volume_fade", "ask_wall_rebuild"),
    invalidations=("failed_breakout", "ask_wall_reform", "buy_flow_stop"),
    exit_modes=("X0", "X1", "X2", "X3", "X4", "X5", "X6"),
)

Z3 = StrategyContract(
    strategy_id="Z3",
    name="Sell-Wall Absorption Breakout",
    thesis="Persistent ask wall absorbed by buys then broken → supply conversion.",
    expected_horizons_sec=(10, 30, 120),
    invariants=("wall_absorbed", "no_lower_low"),
    warnings=("wall_rebuild", "spread_widen"),
    invalidations=("ask_wall_reform", "wall_fail", "best_bid_retreat", "sell_flow_accel"),
    exit_modes=("X0", "X1", "X2", "X3", "X4", "X5", "X6"),
)

Z4 = StrategyContract(
    strategy_id="Z4",
    name="Compression Expansion",
    thesis="Range/vol/spread compress then co-expand with flow/board → new impulse.",
    expected_horizons_sec=(15, 60, 180),
    invariants=("outside_range_hold", "expansion_aligned"),
    warnings=("range_reentry_risk", "flow_flip"),
    invalidations=("range_reentry", "expansion_fail", "flow_reversal", "spread_worsen"),
    exit_modes=("X0", "X1", "X2", "X3", "X4", "X5", "X6"),
)

CONTRACTS = {"Z1": Z1, "Z2": Z2, "Z3": Z3, "Z4": Z4}


@dataclass
class EntryRule:
    rule_id: str
    strategy_id: str
    template: str  # T0..T9
    groups: tuple[str, ...]
    conditions: dict[str, Any]  # frozen thresholds
    n_conditions: int

    def score_simplicity(self) -> int:
        return self.n_conditions
