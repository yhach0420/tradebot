"""Order guarantee: non-decreasing available_at, deterministic tie-break. Not filesystem/dict order."""
from __future__ import annotations

from typing import Iterable, Iterator, Sequence

from research.causal_driver_pb1.contracts.errors import TransportOrderError
from research.causal_driver_pb1.contracts.observation import DriverObservation
from research.causal_driver_pb1.contracts.time import to_utc


def sort_key(obs: DriverObservation) -> tuple:
    return (
        to_utc(obs.available_at, field="available_at"),
        to_utc(obs.event_time, field="event_time"),
        obs.source_id,
        obs.driver_observation_id,
    )


def ordered(rows: Iterable[DriverObservation]) -> list[DriverObservation]:
    return sorted(rows, key=sort_key)


def assert_non_decreasing(rows: Sequence[DriverObservation]) -> None:
    prev = None
    for obs in rows:
        key = sort_key(obs)
        if prev is not None and key < prev:
            raise TransportOrderError("available_at_not_non_decreasing")
        prev = key


def stream_ordered(rows: Iterable[DriverObservation]) -> Iterator[DriverObservation]:
    seq = ordered(rows)
    assert_non_decreasing(seq)
    yield from seq
