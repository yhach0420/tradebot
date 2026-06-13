"""
Phase364: Production ENTRY guard — near day high + low momentum on Dynamic40 only.

Reject when universe is Dynamic40 AND:
  day_high_distance_pct <= 1.5 AND entry_momentum_score < 0.30
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from small_paper.near_day_high_low_mom_entry_guard_shadow import (
    DAY_HIGH_DISTANCE_MAX_PCT,
    MOMENTUM_MAX_EXCLUSIVE,
    would_block_near_day_high_low_mom_guard,
)
from small_paper.pullback_misread_dynamic40_entry_guard import is_dynamic40_universe

REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD = (
    "near_day_high_low_momentum_dynamic40_guard"
)
LOG_EVENT_KIND = "near_day_high_low_momentum_dynamic40_guard_triggered"


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _day_high_distance(fields: Mapping[str, Any]) -> Optional[float]:
    return _float(fields.get("day_high_distance_pct")) or _float(
        fields.get("entry_near_day_high_pct")
    )


def _entry_momentum(fields: Mapping[str, Any]) -> Optional[float]:
    return (
        _float(fields.get("entry_momentum_score"))
        or _float(fields.get("entry_momentum_continuation_score"))
        or _float(fields.get("momentum_continuation_score"))
    )


def compute_near_day_high_low_momentum_guard_fields(
    trade: Mapping[str, Any],
) -> dict[str, Any]:
    dist = _day_high_distance(trade)
    mom = _entry_momentum(trade)
    dyn40 = is_dynamic40_universe(trade)
    cond = would_block_near_day_high_low_mom_guard(
        {
            "day_high_distance_pct": dist,
            "entry_near_day_high_pct": dist,
            "entry_momentum_score": mom,
            "entry_momentum_continuation_score": _float(
                trade.get("entry_momentum_continuation_score")
            ),
            "momentum_continuation_score": mom,
        }
    )
    blocked = dyn40 and cond
    return {
        "day_high_distance_pct": dist,
        "entry_momentum_score": mom,
        "near_day_high_low_momentum_dynamic40_guard_candidate": bool(cond),
        "near_day_high_low_momentum_dynamic40_guard_blocked": blocked,
        "universe_slot": trade.get("universe_slot"),
        "universe_bucket": trade.get("universe_bucket"),
    }


@dataclass
class NearDayHighLowMomentumDynamic40GuardConfig:
    enabled: bool = True


@dataclass
class NearDayHighLowMomentumDynamic40GuardCheck:
    blocked: bool
    day_high_distance_pct: Optional[float] = None
    entry_momentum_score: Optional[float] = None
    universe_slot: str = ""
    universe_bucket: str = ""
    reject_reason: str = ""

    def log_fields(self, *, symbol: str) -> dict[str, Any]:
        return {
            "event_kind": LOG_EVENT_KIND,
            "symbol": symbol,
            "day_high_distance_pct": self.day_high_distance_pct,
            "entry_momentum_score": self.entry_momentum_score,
            "day_high_distance_max_pct": DAY_HIGH_DISTANCE_MAX_PCT,
            "momentum_max_exclusive": MOMENTUM_MAX_EXCLUSIVE,
            "universe_slot": self.universe_slot,
            "universe_bucket": self.universe_bucket,
            "reject_reason": self.reject_reason
            or REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD,
        }


@dataclass
class NearDayHighLowMomentumDynamic40GuardState:
    config: NearDayHighLowMomentumDynamic40GuardConfig
    reject_count: int = 0
    rejected_symbols: set[str] = field(default_factory=set)

    def summary_fields(self) -> dict[str, Any]:
        return {
            "near_day_high_low_momentum_dynamic40_guard_enabled": self.config.enabled,
            "near_day_high_low_momentum_dynamic40_reject_count": self.reject_count,
            "near_day_high_low_momentum_dynamic40_reject_symbols": sorted(
                self.rejected_symbols
            ),
        }

    def check(self, trade: Mapping[str, Any]) -> NearDayHighLowMomentumDynamic40GuardCheck:
        if not self.config.enabled:
            return NearDayHighLowMomentumDynamic40GuardCheck(blocked=False)

        dist = _day_high_distance(trade)
        mom = _entry_momentum(trade)
        slot = str(trade.get("universe_slot") or "")
        bucket = str(trade.get("universe_bucket") or "")

        if not is_dynamic40_universe(trade):
            return NearDayHighLowMomentumDynamic40GuardCheck(
                blocked=False,
                day_high_distance_pct=dist,
                entry_momentum_score=mom,
                universe_slot=slot,
                universe_bucket=bucket,
            )

        blocked = would_block_near_day_high_low_mom_guard(
            {
                "day_high_distance_pct": dist,
                "entry_near_day_high_pct": dist,
                "entry_momentum_score": mom,
                "entry_momentum_continuation_score": _float(
                    trade.get("entry_momentum_continuation_score")
                ),
                "momentum_continuation_score": mom,
            }
        )
        return NearDayHighLowMomentumDynamic40GuardCheck(
            blocked=blocked,
            day_high_distance_pct=dist,
            entry_momentum_score=mom,
            universe_slot=slot,
            universe_bucket=bucket,
            reject_reason=REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD
            if blocked
            else "",
        )


def config_from_pilot(pilot_config: Any) -> NearDayHighLowMomentumDynamic40GuardConfig:
    return NearDayHighLowMomentumDynamic40GuardConfig(
        enabled=bool(
            getattr(
                pilot_config,
                "enable_near_day_high_low_momentum_dynamic40_guard",
                True,
            )
        ),
    )


def build_near_day_high_low_momentum_dynamic40_guard_state(
    pilot_config: Any,
) -> Optional[NearDayHighLowMomentumDynamic40GuardState]:
    cfg = config_from_pilot(pilot_config)
    if not cfg.enabled:
        return None
    return NearDayHighLowMomentumDynamic40GuardState(config=cfg)
