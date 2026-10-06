"""Immutable DriverObservation. No forward-fill. No guessed direction."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from research.causal_driver_pb1.contracts.enums import Direction, DriverFamily, QualityStatus
from research.causal_driver_pb1.contracts.errors import FailClosedError, IdentityError
from research.causal_driver_pb1.contracts.source import SourceIdentity
from research.causal_driver_pb1.contracts.time import assert_aware, causal_ok, validate_timestamp_order
from research.causal_driver_pb1.identity.ids import driver_observation_id


@dataclass(frozen=True, slots=True)
class DriverObservation:
    driver_observation_id: str
    driver_family: DriverFamily
    source_id: str
    event_time: datetime
    available_at: datetime
    received_at: datetime | None
    value: float | None
    direction: Direction
    strength: float | None
    lookback_window: str
    valid_until: datetime
    freshness_sec: float | None
    quality_status: QualityStatus
    causal_ok: bool
    source_identity: SourceIdentity
    source_sha256: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source_id != self.source_identity.source_id:
            raise IdentityError("source_id_mismatch")
        if self.source_sha256 != self.source_identity.fingerprint():
            raise IdentityError("source_identity_mismatch")
        assert_aware(self.event_time, field="event_time")
        assert_aware(self.available_at, field="available_at")
        assert_aware(self.valid_until, field="valid_until")
        if self.received_at is not None:
            assert_aware(self.received_at, field="received_at")
        bar = None
        meta = dict(self.metadata)
        if "BAR_START" in meta or meta.get("bar_semantics") == "BAR_START":
            bar = "BAR_START"
        elif self.source_identity.timestamp_semantics == "BAR_START":
            bar = "BAR_START"
        validate_timestamp_order(
            event_time=self.event_time,
            available_at=self.available_at,
            received_at=self.received_at,
            bar_semantics=bar,
        )
        expected = driver_observation_id(
            source_id=self.source_id,
            instrument=self.source_identity.instrument,
            event_time=self.event_time,
            resolution=self.source_identity.resolution,
        )
        if self.driver_observation_id != expected:
            raise IdentityError("driver_observation_id_not_deterministic")
        object.__setattr__(self, "metadata", dict(meta))

    def usable_at(self, decision_time: datetime) -> bool:
        if self.quality_status in {QualityStatus.MISSING, QualityStatus.STALE, QualityStatus.REJECTED}:
            return False
        if self.quality_status == QualityStatus.DEGRADED:
            return False
        if self.value is None:
            return False
        return bool(self.causal_ok) and causal_ok(available_at=self.available_at, decision_time=decision_time)

    def require_usable_at(self, decision_time: datetime) -> None:
        if not self.usable_at(decision_time):
            raise FailClosedError("driver_observation_not_usable")


def build_observation(
    *,
    source: SourceIdentity,
    driver_family: DriverFamily,
    event_time: datetime,
    available_at: datetime,
    received_at: datetime | None,
    value: float | None,
    direction: Direction,
    strength: float | None,
    lookback_window: str,
    valid_until: datetime,
    quality_status: QualityStatus,
    decision_time: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> DriverObservation:
    oid = driver_observation_id(
        source_id=source.source_id,
        instrument=source.instrument,
        event_time=event_time,
        resolution=source.resolution,
    )
    freshness = None
    if decision_time is not None:
        freshness = (assert_aware(decision_time, field="decision_time") - assert_aware(available_at, field="available_at")).total_seconds()
    ok = True
    if decision_time is not None:
        ok = causal_ok(available_at=available_at, decision_time=decision_time)
    meta = dict(metadata or {})
    meta.setdefault("bar_semantics", source.timestamp_semantics)
    meta.setdefault("BAR_START", source.timestamp_semantics == "BAR_START")
    meta.setdefault("BAR_END", None)
    meta.setdefault("AVAILABLE_AT", "bar_start_plus_1_minute" if source.timestamp_semantics == "BAR_START" else "event_time")
    return DriverObservation(
        driver_observation_id=oid,
        driver_family=driver_family,
        source_id=source.source_id,
        event_time=event_time,
        available_at=available_at,
        received_at=received_at,
        value=value,
        direction=direction,
        strength=strength,
        lookback_window=lookback_window,
        valid_until=valid_until,
        freshness_sec=freshness,
        quality_status=quality_status,
        causal_ok=ok if decision_time is not None else True,
        source_identity=source,
        source_sha256=source.fingerprint(),
        metadata=meta,
    )
