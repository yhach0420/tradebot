"""
Phase452: Production ENTRY guard — Weak Shape Reject (forward-safe).

Rejects opening_peak / slow_opening_peak intraday shapes at ENTRY time.
No EOD close / lookahead — uses price ring + board high only.

Mirrors Phase445 EOD classification intent:
  opening_peak: day high by 09:20 + pullback from high
  slow_opening_peak: day high by 10:00 + deeper pullback
Uptrend pass: recent high update or positive r10/r15 momentum.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

REJECT_WEAK_SHAPE = "weak_shape_reject"
LOG_EVENT_KIND = "weak_shape_reject_guard_triggered"

OPENING_PEAK_MAX_MINS_FROM_OPEN = 20.0
OPENING_PEAK_MIN_DIST_PCT = 1.5
SLOW_OPENING_PEAK_MAX_MINS_FROM_OPEN = 60.0
SLOW_OPENING_PEAK_MIN_DIST_PCT = 2.0
UPTREND_RECENT_HIGH_UPDATE_MAX_MINS = 15.0


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


def _session_open_ts(entry_ts: float) -> float:
    dt = datetime.fromtimestamp(entry_ts, tz=JST)
    open_dt = dt.replace(hour=9, minute=0, second=0, microsecond=0)
    return open_dt.timestamp()


def compute_day_high_timing_fields(
    *,
    price_ring: Sequence[tuple[float, float]],
    entry_ts: float,
    entry_px: float,
    board_high: Optional[float] = None,
) -> dict[str, Any]:
    """Forward-safe day-high timing from intraday ticks (no session close)."""
    upto = [(t, px) for t, px in price_ring if t <= entry_ts]
    if not upto or entry_px <= 0:
        return {}
    ring_high = max(px for _, px in upto)
    day_high = max(ring_high, board_high or 0.0)
    if day_high <= 0:
        return {}
    tol = day_high * 0.9995
    high_ticks = [(t, px) for t, px in upto if px >= tol]
    if not high_ticks:
        return {}
    first_high_ts = min(t for t, _ in high_ticks)
    last_high_ts = max(t for t, _ in high_ticks)
    open_ts = _session_open_ts(entry_ts)
    dist = round((day_high - entry_px) / day_high * 100.0, 4) if day_high > 0 else None
    drawdown = round((entry_px - day_high) / day_high * 100.0, 4) if day_high > 0 else None
    return {
        "day_high_minutes_from_open": round(max(0.0, (first_high_ts - open_ts) / 60.0), 2),
        "minutes_since_day_high_update": round(max(0.0, (entry_ts - last_high_ts) / 60.0), 2),
        "day_high_distance_pct": dist,
        "high_to_now_drawdown_pct": drawdown,
    }


def is_uptrend_pass_at_entry(fields: Mapping[str, Any]) -> bool:
    mins_update = _float(fields.get("minutes_since_day_high_update"))
    if mins_update is not None and mins_update <= UPTREND_RECENT_HIGH_UPDATE_MAX_MINS:
        return True
    r15 = _float(fields.get("entry_rise_15min_pct"))
    r10 = _float(fields.get("entry_rise_10min_pct"))
    r30 = _float(fields.get("entry_rise_30min_pct"))
    if r15 is not None and r15 > 0.0:
        if r30 is not None and r30 > 0.0:
            return True
        if r10 is not None and r10 > 0.0:
            return True
    mins_open = _float(fields.get("day_high_minutes_from_open"))
    if mins_open is not None and mins_open > SLOW_OPENING_PEAK_MAX_MINS_FROM_OPEN:
        if r15 is not None and r15 > 0.0:
            return True
    return False


def classify_intraday_weak_shape(fields: Mapping[str, Any]) -> Optional[str]:
    """Return opening_peak, slow_opening_peak, or None (pass)."""
    if is_uptrend_pass_at_entry(fields):
        return None
    dist = _day_high_distance(fields)
    mins_open = _float(fields.get("day_high_minutes_from_open"))
    if dist is None or mins_open is None:
        return None
    if mins_open <= OPENING_PEAK_MAX_MINS_FROM_OPEN and dist >= OPENING_PEAK_MIN_DIST_PCT:
        return "opening_peak"
    if mins_open <= SLOW_OPENING_PEAK_MAX_MINS_FROM_OPEN and dist >= SLOW_OPENING_PEAK_MIN_DIST_PCT:
        return "slow_opening_peak"
    return None


def would_block_weak_shape_reject(fields: Mapping[str, Any]) -> bool:
    return classify_intraday_weak_shape(fields) is not None


def compute_weak_shape_reject_guard_fields(trade: Mapping[str, Any]) -> dict[str, Any]:
    shape = classify_intraday_weak_shape(trade)
    blocked = shape is not None
    return {
        "weak_shape_reject_guard_candidate": bool(shape),
        "weak_shape_reject_guard_blocked": blocked,
        "weak_shape_class": shape or "",
        "day_high_minutes_from_open": trade.get("day_high_minutes_from_open"),
        "minutes_since_day_high_update": trade.get("minutes_since_day_high_update"),
        "day_high_distance_pct": _day_high_distance(trade),
        "high_to_now_drawdown_pct": trade.get("high_to_now_drawdown_pct"),
    }


@dataclass
class WeakShapeRejectGuardConfig:
    enabled: bool = False


@dataclass
class WeakShapeRejectGuardCheck:
    blocked: bool
    shape_class: str = ""
    day_high_minutes_from_open: Optional[float] = None
    minutes_since_day_high_update: Optional[float] = None
    day_high_distance_pct: Optional[float] = None
    high_to_now_drawdown_pct: Optional[float] = None
    reject_reason: str = ""

    def log_fields(self, *, symbol: str) -> dict[str, Any]:
        return {
            "event_kind": LOG_EVENT_KIND,
            "symbol": symbol,
            "weak_shape_class": self.shape_class,
            "day_high_minutes_from_open": self.day_high_minutes_from_open,
            "minutes_since_day_high_update": self.minutes_since_day_high_update,
            "day_high_distance_pct": self.day_high_distance_pct,
            "high_to_now_drawdown_pct": self.high_to_now_drawdown_pct,
            "reject_reason": self.reject_reason or REJECT_WEAK_SHAPE,
        }


@dataclass
class WeakShapeRejectGuardState:
    config: WeakShapeRejectGuardConfig
    reject_count: int = 0
    rejected_symbols: set[str] = field(default_factory=set)

    def summary_fields(self) -> dict[str, Any]:
        return {
            "weak_shape_reject_enabled": self.config.enabled,
            "weak_shape_reject_count": self.reject_count,
            "weak_shape_reject_symbols": sorted(self.rejected_symbols),
        }

    def check(self, trade: Mapping[str, Any]) -> WeakShapeRejectGuardCheck:
        if not self.config.enabled:
            return WeakShapeRejectGuardCheck(blocked=False)
        shape = classify_intraday_weak_shape(trade)
        dist = _day_high_distance(trade)
        return WeakShapeRejectGuardCheck(
            blocked=shape is not None,
            shape_class=shape or "",
            day_high_minutes_from_open=_float(trade.get("day_high_minutes_from_open")),
            minutes_since_day_high_update=_float(trade.get("minutes_since_day_high_update")),
            day_high_distance_pct=dist,
            high_to_now_drawdown_pct=_float(trade.get("high_to_now_drawdown_pct")),
            reject_reason=REJECT_WEAK_SHAPE if shape else "",
        )


def config_from_pilot(pilot_config: Any) -> WeakShapeRejectGuardConfig:
    return WeakShapeRejectGuardConfig(
        enabled=bool(getattr(pilot_config, "weak_shape_reject_enabled", False)),
    )


def build_weak_shape_reject_guard_state(
    pilot_config: Any,
) -> Optional[WeakShapeRejectGuardState]:
    cfg = config_from_pilot(pilot_config)
    if not cfg.enabled:
        return None
    return WeakShapeRejectGuardState(config=cfg)
