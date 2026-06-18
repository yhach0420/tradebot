"""
Phase439: Production ENTRY guard — High Drift pullback on Dynamic40 only.

Reject when universe is Dynamic40 AND:
  (day_high>=1.2% AND r10<-0.15% AND r5>r10)
  OR
  (day_high>=1.5% AND (r15<-0.5% OR r5<-0.5%))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from small_paper.pullback_misread_dynamic40_entry_guard import is_dynamic40_universe

REJECT_HIGH_DRIFT_PULLBACK = "high_drift_pullback"
LOG_EVENT_KIND = "high_drift_pullback_guard_triggered"

DAY_HIGH_A_MIN_PCT = 1.2
DAY_HIGH_B_MIN_PCT = 1.5
R10_THRESH_PCT = -0.15
R15_THRESH_PCT = -0.5
R5_B_THRESH_PCT = -0.5


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


def would_block_high_drift_pullback_guard(fields: Mapping[str, Any]) -> bool:
    if not is_dynamic40_universe(fields):
        return False
    dist = _day_high_distance(fields) or 0.0
    r5 = _float(fields.get("entry_rise_5min_pct"))
    r10 = _float(fields.get("entry_rise_10min_pct"))
    r15 = _float(fields.get("entry_rise_15min_pct"))
    if dist < DAY_HIGH_A_MIN_PCT:
        return False
    if r10 is not None and r10 < R10_THRESH_PCT:
        if r5 is None:
            return True
        if r5 > r10 and r5 <= 1.0:
            return True
    if dist >= DAY_HIGH_B_MIN_PCT:
        if r15 is not None and r15 < R15_THRESH_PCT and (r5 is None or r5 < 0.2):
            return True
        if r5 is not None and r5 < R5_B_THRESH_PCT and (r10 is None or r10 < -0.2):
            return True
    return False


def compute_high_drift_pullback_guard_fields(trade: Mapping[str, Any]) -> dict[str, Any]:
    dist = _day_high_distance(trade)
    r5 = _float(trade.get("entry_rise_5min_pct"))
    r10 = _float(trade.get("entry_rise_10min_pct"))
    r15 = _float(trade.get("entry_rise_15min_pct"))
    cond = would_block_high_drift_pullback_guard(trade)
    dyn40 = is_dynamic40_universe(trade)
    blocked = dyn40 and cond
    return {
        "entry_rise_5min_pct": r5,
        "entry_rise_10min_pct": r10,
        "entry_rise_15min_pct": r15,
        "day_high_distance_pct": dist,
        "high_drift_pullback_guard_candidate": bool(cond),
        "high_drift_pullback_guard_blocked": blocked,
        "universe_slot": trade.get("universe_slot"),
        "universe_bucket": trade.get("universe_bucket"),
    }


@dataclass
class HighDriftPullbackGuardConfig:
    enabled: bool = False


@dataclass
class HighDriftPullbackGuardCheck:
    blocked: bool
    entry_rise_5min_pct: Optional[float] = None
    entry_rise_10min_pct: Optional[float] = None
    entry_rise_15min_pct: Optional[float] = None
    day_high_distance_pct: Optional[float] = None
    universe_slot: str = ""
    universe_bucket: str = ""
    reject_reason: str = ""

    def log_fields(self, *, symbol: str) -> dict[str, Any]:
        return {
            "event_kind": LOG_EVENT_KIND,
            "symbol": symbol,
            "entry_rise_5min_pct": self.entry_rise_5min_pct,
            "entry_rise_10min_pct": self.entry_rise_10min_pct,
            "entry_rise_15min_pct": self.entry_rise_15min_pct,
            "day_high_distance_pct": self.day_high_distance_pct,
            "universe_slot": self.universe_slot,
            "universe_bucket": self.universe_bucket,
            "reject_reason": self.reject_reason or REJECT_HIGH_DRIFT_PULLBACK,
        }


@dataclass
class HighDriftPullbackGuardState:
    config: HighDriftPullbackGuardConfig
    reject_count: int = 0
    rejected_symbols: set[str] = field(default_factory=set)

    def summary_fields(self) -> dict[str, Any]:
        return {
            "high_drift_pullback_guard_enabled": self.config.enabled,
            "high_drift_pullback_reject_count": self.reject_count,
            "high_drift_pullback_reject_symbols": sorted(self.rejected_symbols),
        }

    def check(self, trade: Mapping[str, Any]) -> HighDriftPullbackGuardCheck:
        if not self.config.enabled:
            return HighDriftPullbackGuardCheck(blocked=False)

        dist = _day_high_distance(trade)
        r5 = _float(trade.get("entry_rise_5min_pct"))
        r10 = _float(trade.get("entry_rise_10min_pct"))
        r15 = _float(trade.get("entry_rise_15min_pct"))
        slot = str(trade.get("universe_slot") or "")
        bucket = str(trade.get("universe_bucket") or "")

        if not is_dynamic40_universe(trade):
            return HighDriftPullbackGuardCheck(
                blocked=False,
                entry_rise_5min_pct=r5,
                entry_rise_10min_pct=r10,
                entry_rise_15min_pct=r15,
                day_high_distance_pct=dist,
                universe_slot=slot,
                universe_bucket=bucket,
            )

        blocked = would_block_high_drift_pullback_guard(trade)
        return HighDriftPullbackGuardCheck(
            blocked=blocked,
            entry_rise_5min_pct=r5,
            entry_rise_10min_pct=r10,
            entry_rise_15min_pct=r15,
            day_high_distance_pct=dist,
            universe_slot=slot,
            universe_bucket=bucket,
            reject_reason=REJECT_HIGH_DRIFT_PULLBACK if blocked else "",
        )


def config_from_pilot(pilot_config: Any) -> HighDriftPullbackGuardConfig:
    return HighDriftPullbackGuardConfig(
        enabled=bool(getattr(pilot_config, "high_drift_guard_enabled", False)),
    )


def build_high_drift_pullback_guard_state(
    pilot_config: Any,
) -> Optional[HighDriftPullbackGuardState]:
    cfg = config_from_pilot(pilot_config)
    if not cfg.enabled:
        return None
    return HighDriftPullbackGuardState(config=cfg)
