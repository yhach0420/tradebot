"""
Phase472: Production ENTRY guard — Late Chase (PBv2-3).

Reject when:
  entry_rise_10min_pct < 0.3719
  AND day_high_distance_pct < 1.1872
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

REJECT_LATE_CHASE_GUARD = "late_chase_guard"
LOG_EVENT_KIND = "late_chase_guard_triggered"

LATE_CHASE_R10_MAX_PCT = 0.3719
LATE_CHASE_DAY_HIGH_MAX_PCT = 1.1872


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _day_high_distance(fields: Mapping[str, Any]) -> Optional[float]:
    raw = _float(fields.get("day_high_distance_pct")) or _float(
        fields.get("entry_near_day_high_pct")
    )
    if raw is None:
        return None
    return abs(raw)


def would_block_late_chase_guard(fields: Mapping[str, Any]) -> bool:
    r10 = _float(fields.get("entry_rise_10min_pct"))
    if r10 is None:
        return False
    dist = _day_high_distance(fields)
    if dist is None:
        return False
    return r10 < LATE_CHASE_R10_MAX_PCT and dist < LATE_CHASE_DAY_HIGH_MAX_PCT


def compute_late_chase_guard_fields(trade: Mapping[str, Any]) -> dict[str, Any]:
    r10 = _float(trade.get("entry_rise_10min_pct"))
    dist = _day_high_distance(trade)
    blocked = would_block_late_chase_guard(trade)
    return {
        "entry_rise_10min_pct": r10,
        "day_high_distance_pct": dist,
        "late_chase_guard_candidate": bool(blocked),
        "late_chase_guard_blocked": blocked,
    }


@dataclass
class LateChaseGuardConfig:
    enabled: bool = False


@dataclass
class LateChaseGuardCheck:
    blocked: bool
    entry_rise_10min_pct: Optional[float] = None
    day_high_distance_pct: Optional[float] = None
    reject_reason: str = ""

    def log_fields(self, *, symbol: str) -> dict[str, Any]:
        return {
            "event_kind": LOG_EVENT_KIND,
            "symbol": symbol,
            "entry_rise_10min_pct": self.entry_rise_10min_pct,
            "day_high_distance_pct": self.day_high_distance_pct,
            "late_chase_r10_max_pct": LATE_CHASE_R10_MAX_PCT,
            "late_chase_day_high_max_pct": LATE_CHASE_DAY_HIGH_MAX_PCT,
            "reject_reason": self.reject_reason or REJECT_LATE_CHASE_GUARD,
        }


@dataclass
class LateChaseGuardState:
    config: LateChaseGuardConfig
    reject_count: int = 0
    rejected_symbols: set[str] = field(default_factory=set)

    def summary_fields(self) -> dict[str, Any]:
        return {
            "late_chase_guard_enabled": self.config.enabled,
            "late_chase_reject_count": self.reject_count,
            "late_chase_reject_symbols": sorted(self.rejected_symbols),
        }

    def check(self, trade: Mapping[str, Any]) -> LateChaseGuardCheck:
        if not self.config.enabled:
            return LateChaseGuardCheck(blocked=False)

        r10 = _float(trade.get("entry_rise_10min_pct"))
        dist = _day_high_distance(trade)
        blocked = would_block_late_chase_guard(trade)
        return LateChaseGuardCheck(
            blocked=blocked,
            entry_rise_10min_pct=r10,
            day_high_distance_pct=dist,
            reject_reason=REJECT_LATE_CHASE_GUARD if blocked else "",
        )


def config_from_pilot(pilot_config: Any) -> LateChaseGuardConfig:
    return LateChaseGuardConfig(
        enabled=bool(getattr(pilot_config, "late_chase_guard_enabled", False)),
    )


def build_late_chase_guard_state(pilot_config: Any) -> Optional[LateChaseGuardState]:
    cfg = config_from_pilot(pilot_config)
    if not cfg.enabled:
        return None
    return LateChaseGuardState(config=cfg)
