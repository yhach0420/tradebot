"""Real USDJPY historical transport. Same DriverTransport interface as Phase 0 synthetic."""
from __future__ import annotations

from typing import Iterator, Sequence

from research.causal_driver_pb1.contracts.observation import DriverObservation
from research.causal_driver_pb1.transport.base import DriverTransport
from research.causal_driver_pb1.transport.order import stream_ordered
from research.causal_driver_pb1.transport.synthetic import HistoricalDriverTransport as Phase0HistoricalDriverTransport


class UsdJpyHistoricalDriverTransport:
    """Connects causally mapped USDJPY observations to Phase 0 HistoricalDriverTransport."""

    def __init__(self, rows: Sequence[DriverObservation]) -> None:
        self._inner = Phase0HistoricalDriverTransport(rows=list(rows))

    def stream(self) -> Iterator[DriverObservation]:
        yield from self._inner.stream()


def stream_usdjpy(rows: Sequence[DriverObservation]) -> Iterator[DriverObservation]:
    yield from stream_ordered(rows)


def assert_transport_protocol(obj: object) -> bool:
    return isinstance(obj, DriverTransport)
