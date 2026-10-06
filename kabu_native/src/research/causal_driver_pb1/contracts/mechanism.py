"""Empty mechanism registry. Phase 0 must not freeze USDJPY or any family."""
from __future__ import annotations

from dataclasses import dataclass

from research.causal_driver_pb1.contracts.enums import DriverFamily, MechanismStatus, TargetScope
from research.causal_driver_pb1.contracts.errors import FailClosedError, IdentityError
from research.causal_driver_pb1.identity.ids import sha256_obj


@dataclass(frozen=True, slots=True)
class MechanismRecord:
    mechanism_id: str
    version: str
    driver_family: DriverFamily
    target_scope: TargetScope
    lead_window: str
    expected_horizon: str
    status: MechanismStatus
    config_sha256: str
    source_identity: str
    frozen: bool

    def __post_init__(self) -> None:
        if self.frozen and self.status is not MechanismStatus.FROZEN:
            raise FailClosedError("frozen_flag_requires_frozen_status")
        if self.status is MechanismStatus.FROZEN and not self.frozen:
            raise FailClosedError("frozen_status_requires_frozen_flag")
        if len(self.config_sha256) != 64:
            raise IdentityError("config_sha256_invalid")


class MechanismRegistry:
    """Phase 0: framework only. No USDJPY / NK / sector mechanism is frozen."""

    def __init__(self) -> None:
        self._rows: dict[str, MechanismRecord] = {}

    def get(self, mechanism_id: str) -> MechanismRecord | None:
        return self._rows.get(mechanism_id)

    def list(self) -> tuple[MechanismRecord, ...]:
        return tuple(sorted(self._rows.values(), key=lambda r: r.mechanism_id))

    def register(self, rec: MechanismRecord) -> None:
        if rec.frozen or rec.status is MechanismStatus.FROZEN:
            raise FailClosedError("phase0_must_not_freeze_mechanism")
        if rec.mechanism_id in {"USDJPY", "NK", "NK225", "SECTOR"}:
            raise FailClosedError("prior_research_evidence_is_not_a_frozen_mechanism")
        if rec.mechanism_id in self._rows:
            raise FailClosedError("mechanism_id_already_registered")
        self._rows[rec.mechanism_id] = rec

    def freeze(self, mechanism_id: str) -> None:
        raise FailClosedError("phase0_empty_registry_cannot_freeze")

    def fingerprint(self) -> str:
        return sha256_obj([r.mechanism_id for r in self.list()])
