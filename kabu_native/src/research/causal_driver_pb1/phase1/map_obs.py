"""Map normalized USDJPY bars onto Phase 0 DriverObservation. Direction is always NEUTRAL."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from research.causal_driver_pb1.contracts.enums import Direction, DriverFamily, QualityStatus
from research.causal_driver_pb1.contracts.observation import DriverObservation, build_observation
from research.causal_driver_pb1.contracts.source import SourceIdentity
from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.phase1 import (
    DRIVER_FAMILY_ID,
    MID_IS_RAW_SOURCE,
    RECEIVED_AT_SEMANTICS,
    VALUE_SEMANTICS,
)
from research.causal_driver_pb1.phase1.bars import UsdJpyNormalizedBar


def bar_metadata(bar: UsdJpyNormalizedBar) -> dict[str, Any]:
    return {
        "BAR_START": True,
        "bar_semantics": "BAR_START",
        "BAR_END": "start_plus_59s",
        "AVAILABLE_AT": "bar_start_plus_1_minute",
        "driver_family_id": DRIVER_FAMILY_ID,
        "value_semantics": VALUE_SEMANTICS,
        "MID_IS_RAW_SOURCE": MID_IS_RAW_SOURCE,
        "bid_open": bar.bid_open,
        "bid_high": bar.bid_high,
        "bid_low": bar.bid_low,
        "bid_close": bar.bid_close,
        "ask_open": bar.ask_open,
        "ask_high": bar.ask_high,
        "ask_low": bar.ask_low,
        "ask_close": bar.ask_close,
        "spread": bar.spread_close,
        "source_timestamp": bar.source_timestamp.isoformat(),
        "source_timezone": bar.source_timezone,
        "normalized_timestamp_jst": bar.normalized_timestamp_jst.isoformat(),
        "received_at_semantics": RECEIVED_AT_SEMANTICS,
        "quality_reason": bar.quality_reason,
        "jst_date": bar.jst_date,
        "ts_utc_ms": bar.ts_utc_ms,
        "direction_rule": "NONE_PHASE1_NEUTRAL_ONLY",
        "return_features": False,
        "alpha_generated": False,
    }


def map_bar(bar: UsdJpyNormalizedBar, source: SourceIdentity) -> DriverObservation:
    value = bar.mid_close if bar.quality_status == QualityStatus.VALID else None
    return build_observation(
        source=source,
        driver_family=DriverFamily.FX,
        event_time=bar.bar_start,
        available_at=bar.available_at,
        received_at=bar.available_at,
        value=value,
        direction=Direction.NEUTRAL,
        strength=None,
        lookback_window="native_1m_bar",
        valid_until=bar.available_at + timedelta(minutes=1),
        quality_status=bar.quality_status,
        metadata=bar_metadata(bar),
    )


def map_bars(bars: list[UsdJpyNormalizedBar], source: SourceIdentity) -> list[DriverObservation]:
    return [map_bar(bar, source) for bar in bars]


def observation_stream_hash(rows) -> str:
    payload = [
        {
            "id": o.driver_observation_id,
            "event_time": o.event_time.isoformat(),
            "available_at": o.available_at.isoformat(),
            "received_at": None if o.received_at is None else o.received_at.isoformat(),
            "value": o.value,
            "direction": o.direction.value,
            "quality": o.quality_status.value,
        }
        for o in rows
    ]
    return sha256_obj(payload)
