"""DriverTransport: historical and runtime share the same consumer interface."""
from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable

from research.causal_driver_pb1.contracts.observation import DriverObservation


@runtime_checkable
class DriverTransport(Protocol):
    def stream(self) -> Iterator[DriverObservation]:
        ...
