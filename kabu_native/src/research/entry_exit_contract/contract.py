"""Frozen ENTRY–EXIT contract objects (immutable after ENTRY)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass(frozen=True)
class EntryContract:
    strategy_id: str
    contract_version: str
    symbol: str
    day: str
    session: str
    entry_signal_time: datetime
    entry_time: datetime
    entry_price: float
    entry_reason: str
    entry_feature_snapshot: dict[str, Optional[float]]
    expected_market_path: str
    expected_horizon_sec: float
    invalidation_level: float
    invalidation_reason_definition: str
    hold_condition_definition: str
    profit_exit_definition: str
    emergency_exit_definition: str
    setup_id: str
    episode_id: str
    source_quality: str
    quote_quality: str
    volume_quality: str
    trade_side_quality: str
    # frozen levels / references (strategy-specific extras)
    levels: dict[str, float] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "contract_version": self.contract_version,
            "symbol": self.symbol,
            "day": self.day,
            "session": self.session,
            "entry_signal_time": self.entry_signal_time.isoformat(),
            "entry_time": self.entry_time.isoformat(),
            "entry_price": self.entry_price,
            "entry_reason": self.entry_reason,
            "expected_market_path": self.expected_market_path,
            "expected_horizon_sec": self.expected_horizon_sec,
            "invalidation_level": self.invalidation_level,
            "invalidation_reason_definition": self.invalidation_reason_definition,
            "hold_condition_definition": self.hold_condition_definition,
            "profit_exit_definition": self.profit_exit_definition,
            "emergency_exit_definition": self.emergency_exit_definition,
            "setup_id": self.setup_id,
            "episode_id": self.episode_id,
            "source_quality": self.source_quality,
            "quote_quality": self.quote_quality,
            "volume_quality": self.volume_quality,
            "trade_side_quality": self.trade_side_quality,
            "levels": dict(self.levels),
            "entry_feature_snapshot": dict(self.entry_feature_snapshot),
        }


@dataclass
class ContractOutcome:
    contract_expected: str
    contract_observed: str
    contract_maintained: bool
    contract_weakened: bool
    contract_invalidated: bool
    exit_contract_consistent: bool
    contract_violation_reason: str
    classification: str  # CONTRACT_* labels
    invalidation_to_exit_sec: Optional[float]
    expected_path_achieved: bool
    horizon_achieved: bool
    matched_exit_used: bool
    fallback_exit_used: bool


def classify_contract(
    *,
    expected_achieved: bool,
    invalidated: bool,
    invalidated_at_sec: Optional[float],
    exit_hold_sec: float,
    pnl_5bps: float,
    capture_ratio: Optional[float],
    evaluable: bool,
    false_invalidation: bool,
) -> str:
    if not evaluable:
        return "CONTRACT_NOT_EVALUABLE"
    if false_invalidation:
        return "CONTRACT_FALSE_INVALIDATION"
    if not expected_achieved and not invalidated:
        return "CONTRACT_NEVER_ACTIVATED"
    if expected_achieved and pnl_5bps > 0 and (capture_ratio is None or capture_ratio >= 0.35):
        return "CONTRACT_SUCCESS_CAPTURED"
    if expected_achieved and (pnl_5bps <= 0 or (capture_ratio is not None and capture_ratio < 0.35)):
        return "CONTRACT_SUCCESS_UNDER_CAPTURED"
    if invalidated and invalidated_at_sec is not None:
        delay = exit_hold_sec - invalidated_at_sec
        if delay <= 15:
            return "CONTRACT_FAILED_EXITED_FAST"
        return "CONTRACT_FAILED_EXITED_LATE"
    return "CONTRACT_NEVER_ACTIVATED"
