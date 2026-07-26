"""
Phase528: Production ENTRY quality guard (Phase527 G9).

Requires spread_bps <= threshold AND update_count_before_entry <= threshold at ENTRY.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from universe.filters import calc_spread_bps

REJECT_ENTRY_QUALITY_GUARD_SPREAD = "entry_quality_guard_spread"
REJECT_ENTRY_QUALITY_GUARD_UPDATE_COUNT = "entry_quality_guard_update_count"
LOG_EVENT_KIND_SPREAD = "entry_quality_guard_spread_triggered"
LOG_EVENT_KIND_UPDATE = "entry_quality_guard_update_count_triggered"

DEFAULT_MAX_SPREAD_BPS = 50.0
DEFAULT_MAX_UPDATE_COUNT = 5


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _int(val: Any) -> Optional[int]:
    try:
        if val is None or val == "":
            return None
        return int(val)
    except (TypeError, ValueError):
        return None


def _resample_1m_highs(
    series: Sequence[tuple[float, float]],
    *,
    until: float,
) -> list[float]:
    """Resample monotonic epoch-second ticks to 1m highs (live price ring format)."""
    if not series:
        return []
    origin = series[0][0]
    bars: dict[int, float] = {}
    for ts, px in series:
        if ts > until:
            break
        if px <= 0:
            continue
        minute_key = int((ts - origin) // 60)
        bars[minute_key] = max(bars.get(minute_key, px), px)
    return [bars[k] for k in sorted(bars.keys())]


def compute_update_count_before_entry(
    price_ring: Sequence[tuple[float, float]],
    *,
    entry_ts: float,
) -> int:
    highs = _resample_1m_highs(price_ring, until=entry_ts)
    if len(highs) < 2:
        return 0
    running_high = highs[0]
    updates = 0
    for h in highs[1:]:
        if h > running_high:
            updates += 1
            running_high = h
    return updates


def compute_spread_bps_from_payload(
    payload: Mapping[str, Any],
    *,
    entry_px: Optional[float] = None,
) -> Optional[float]:
    from small_paper.canonical_board import spread_bps_for_mode

    spread = spread_bps_for_mode(payload)
    if spread is not None:
        return round(float(spread), 4)
    # fallback: legacy labeled abs spread, then high/low range
    spread = calc_spread_bps(payload)
    if spread is not None:
        return round(float(spread), 4)
    px = _float(entry_px) or _float(payload.get("CurrentPrice"))
    hi = _float(payload.get("HighPrice"))
    lo = _float(payload.get("LowPrice"))
    if px and px > 0 and hi is not None and lo is not None and hi >= lo:
        return round((hi - lo) / px * 10000.0, 4)
    return None


def compute_entry_quality_guard_fields(
    trade: Mapping[str, Any],
    *,
    payload: Mapping[str, Any] | None = None,
    price_ring: Sequence[tuple[float, float]] | None = None,
    entry_ts: float | None = None,
    max_spread_bps: float = DEFAULT_MAX_SPREAD_BPS,
    max_update_count: int = DEFAULT_MAX_UPDATE_COUNT,
    enabled: bool = True,
) -> dict[str, Any]:
    spread_bps = _float(trade.get("spread_bps"))
    if spread_bps is None and payload is not None:
        spread_bps = compute_spread_bps_from_payload(
            payload,
            entry_px=_float(trade.get("current_price") or trade.get("entry_price")),
        )

    update_count = _int(trade.get("update_count_before_entry"))
    if update_count is None and price_ring is not None and entry_ts is not None:
        update_count = compute_update_count_before_entry(price_ring, entry_ts=entry_ts)

    spread_fail = spread_bps is None or spread_bps > max_spread_bps
    update_fail = update_count is None or update_count > max_update_count
    candidate = spread_fail or update_fail
    blocked = bool(enabled and candidate)
    reject_reason = ""
    if enabled and blocked:
        reject_reason = (
            REJECT_ENTRY_QUALITY_GUARD_SPREAD
            if spread_fail
            else REJECT_ENTRY_QUALITY_GUARD_UPDATE_COUNT
        )

    return {
        "spread_bps": spread_bps,
        "update_count_before_entry": update_count,
        "entry_quality_guard_pass": not blocked if enabled else True,
        "entry_quality_guard_candidate": bool(candidate),
        "entry_quality_guard_blocked": blocked,
        "entry_quality_guard_reject_reason": reject_reason,
        "entry_quality_max_spread_bps": max_spread_bps,
        "entry_quality_max_update_count": max_update_count,
    }


@dataclass
class EntryQualityGuardConfig:
    enabled: bool = False
    max_spread_bps: float = DEFAULT_MAX_SPREAD_BPS
    max_update_count: int = DEFAULT_MAX_UPDATE_COUNT


@dataclass
class EntryQualityGuardCheck:
    blocked: bool
    spread_bps: Optional[float] = None
    update_count_before_entry: Optional[int] = None
    reject_reason: str = ""

    def log_fields(self, *, symbol: str) -> dict[str, Any]:
        kind = (
            LOG_EVENT_KIND_SPREAD
            if self.reject_reason == REJECT_ENTRY_QUALITY_GUARD_SPREAD
            else LOG_EVENT_KIND_UPDATE
        )
        return {
            "event_kind": kind,
            "symbol": symbol,
            "spread_bps": self.spread_bps,
            "update_count_before_entry": self.update_count_before_entry,
            "reject_reason": self.reject_reason,
        }


@dataclass
class EntryQualityGuardState:
    config: EntryQualityGuardConfig
    reject_count: int = 0
    spread_reject_count: int = 0
    update_reject_count: int = 0
    rejected_symbols: set[str] = field(default_factory=set)

    def summary_fields(self) -> dict[str, Any]:
        return {
            "entry_quality_guard_enabled": self.config.enabled,
            "entry_quality_max_spread_bps": self.config.max_spread_bps,
            "entry_quality_max_update_count": self.config.max_update_count,
            "entry_quality_guard_reject_count": self.reject_count,
            "entry_quality_guard_spread_reject_count": self.spread_reject_count,
            "entry_quality_guard_update_reject_count": self.update_reject_count,
            "entry_quality_guard_reject_symbols": sorted(self.rejected_symbols),
        }

    def check(self, trade: Mapping[str, Any]) -> EntryQualityGuardCheck:
        spread_bps = _float(trade.get("spread_bps"))
        update_count = _int(trade.get("update_count_before_entry"))

        if not self.config.enabled:
            return EntryQualityGuardCheck(
                blocked=False,
                spread_bps=spread_bps,
                update_count_before_entry=update_count,
            )

        spread_fail = spread_bps is None or spread_bps > self.config.max_spread_bps
        if spread_fail:
            return EntryQualityGuardCheck(
                blocked=True,
                spread_bps=spread_bps,
                update_count_before_entry=update_count,
                reject_reason=REJECT_ENTRY_QUALITY_GUARD_SPREAD,
            )

        update_fail = update_count is None or update_count > self.config.max_update_count
        if update_fail:
            return EntryQualityGuardCheck(
                blocked=True,
                spread_bps=spread_bps,
                update_count_before_entry=update_count,
                reject_reason=REJECT_ENTRY_QUALITY_GUARD_UPDATE_COUNT,
            )

        return EntryQualityGuardCheck(
            blocked=False,
            spread_bps=spread_bps,
            update_count_before_entry=update_count,
        )


def config_from_pilot(pilot_config: Any) -> EntryQualityGuardConfig:
    return EntryQualityGuardConfig(
        enabled=bool(getattr(pilot_config, "entry_quality_guard_enabled", False)),
        max_spread_bps=float(
            getattr(pilot_config, "entry_quality_max_spread_bps", DEFAULT_MAX_SPREAD_BPS)
            or DEFAULT_MAX_SPREAD_BPS
        ),
        max_update_count=int(
            getattr(pilot_config, "entry_quality_max_update_count", DEFAULT_MAX_UPDATE_COUNT)
            or DEFAULT_MAX_UPDATE_COUNT
        ),
    )


def build_entry_quality_guard_state(pilot_config: Any) -> Optional[EntryQualityGuardState]:
    cfg = config_from_pilot(pilot_config)
    if not cfg.enabled:
        return None
    return EntryQualityGuardState(config=cfg)
