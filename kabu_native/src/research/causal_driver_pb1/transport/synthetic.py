"""Synthetic transports for Phase 0. USDJPY real adapter is Phase 1."""
from __future__ import annotations

from typing import Iterator

from research.causal_driver_pb1.contracts.enums import Direction, DriverFamily, QualityStatus
from research.causal_driver_pb1.contracts.observation import DriverObservation, build_observation
from research.causal_driver_pb1.contracts.source import SourceIdentity
from research.causal_driver_pb1.contracts.time import bar_start_available_at, jst
from research.causal_driver_pb1.identity.ids import sha256_text
from research.causal_driver_pb1.transport.order import stream_ordered


def synthetic_source() -> SourceIdentity:
    inv = sha256_text("SYNTHETIC_PHASE0_SOURCE_V1")
    return SourceIdentity(
        source_id="SYNTHETIC_FX_1M",
        provider="SYNTHETIC",
        instrument="SYN-USDJPY",
        resolution="1m",
        timezone="Asia/Tokyo",
        timestamp_semantics="BAR_START",
        source_path_or_endpoint_identity="synthetic://phase0",
        inventory_sha256=inv,
    )


def synthetic_observations() -> list[DriverObservation]:
    src = synthetic_source()
    rows: list[DriverObservation] = []
    # Intentionally constructed out of wall-clock insertion order.
    for hhmm, value, direction in (
        ((9, 32), 150.20, Direction.SHORT),
        ((9, 30), 150.10, Direction.LONG),
        ((9, 31), 150.15, Direction.LONG),
    ):
        event = jst(2024, 9, 17, hhmm[0], hhmm[1], 0)
        available = bar_start_available_at(event)
        rows.append(
            build_observation(
                source=src,
                driver_family=DriverFamily.SYNTHETIC,
                event_time=event,
                available_at=available,
                received_at=available,
                value=value,
                direction=direction,
                strength=1.0,
                lookback_window="1m",
                valid_until=available.replace(hour=15, minute=20, second=0),
                quality_status=QualityStatus.VALID,
                decision_time=available,
                metadata={"BAR_START": True, "bar_semantics": "BAR_START", "BAR_END": "start_plus_59s"},
            )
        )
    return rows


class HistoricalDriverTransport:
    """Phase 0: synthetic historical stream. Real USDJPY adapter is Phase 1."""

    def __init__(self, rows: list[DriverObservation] | None = None) -> None:
        self._rows = list(rows) if rows is not None else synthetic_observations()

    def stream(self) -> Iterator[DriverObservation]:
        yield from stream_ordered(self._rows)


class RuntimeDriverTransport:
    """Phase 0: same interface as historical. No live capture."""

    def __init__(self, rows: list[DriverObservation] | None = None) -> None:
        self._rows = list(rows) if rows is not None else synthetic_observations()

    def stream(self) -> Iterator[DriverObservation]:
        yield from stream_ordered(self._rows)
