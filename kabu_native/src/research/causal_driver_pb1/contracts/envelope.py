"""Common event envelope. Phase 0: DRIVER_OBSERVED / DRIVER_REJECTED / DATASET_ACCESS / IDENTITY_BOUND."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from research.causal_driver_pb1.contracts.enums import EventType
from research.causal_driver_pb1.contracts.time import assert_aware
from research.causal_driver_pb1.identity.ids import event_id


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: str
    event_type: EventType
    event_time: datetime
    available_at: datetime
    received_at: datetime | None
    source: str
    identity: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        assert_aware(self.event_time, field="event_time")
        assert_aware(self.available_at, field="available_at")
        if self.received_at is not None:
            assert_aware(self.received_at, field="received_at")
        expected = event_id(
            event_type=self.event_type.value,
            identity=self.identity,
            event_time=self.event_time,
            source=self.source,
        )
        if self.event_id != expected:
            from research.causal_driver_pb1.contracts.errors import IdentityError

            raise IdentityError("event_id_not_deterministic")
        object.__setattr__(self, "payload", dict(self.payload))


def envelope(
    *,
    event_type: EventType,
    event_time: datetime,
    available_at: datetime,
    received_at: datetime | None,
    source: str,
    identity: str,
    payload: Mapping[str, Any],
) -> EventEnvelope:
    eid = event_id(
        event_type=event_type.value,
        identity=identity,
        event_time=event_time,
        source=source,
    )
    return EventEnvelope(
        event_id=eid,
        event_type=event_type,
        event_time=event_time,
        available_at=available_at,
        received_at=received_at,
        source=source,
        identity=identity,
        payload=payload,
    )
