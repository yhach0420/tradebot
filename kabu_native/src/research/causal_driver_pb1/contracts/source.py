"""Driver source identity. Semantics travel with the data."""
from __future__ import annotations

from dataclasses import dataclass

from research.causal_driver_pb1.contracts.errors import IdentityError
from research.causal_driver_pb1.identity.ids import sha256_obj


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    source_id: str
    provider: str
    instrument: str
    resolution: str
    timezone: str
    timestamp_semantics: str
    source_path_or_endpoint_identity: str
    inventory_sha256: str

    def __post_init__(self) -> None:
        if not self.source_id:
            raise IdentityError("source_id_missing")
        if not self.provider:
            raise IdentityError("provider_missing")
        if not self.instrument:
            raise IdentityError("instrument_missing")
        if not self.resolution:
            raise IdentityError("resolution_missing")
        if not self.timezone:
            raise IdentityError("timezone_missing")
        if not self.timestamp_semantics:
            raise IdentityError("timestamp_semantics_missing")
        if self.timestamp_semantics not in {"BAR_START", "BAR_END", "TICK", "EVENT"}:
            raise IdentityError("timestamp_semantics_unknown")
        if len(self.inventory_sha256) != 64:
            raise IdentityError("inventory_sha256_invalid")

    def fingerprint(self) -> str:
        return sha256_obj(
            {
                "source_id": self.source_id,
                "provider": self.provider,
                "instrument": self.instrument,
                "resolution": self.resolution,
                "timezone": self.timezone,
                "timestamp_semantics": self.timestamp_semantics,
                "source_path_or_endpoint_identity": self.source_path_or_endpoint_identity,
                "inventory_sha256": self.inventory_sha256,
            }
        )
