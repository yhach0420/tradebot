"""Dataset roles. Frozen Validation is semantically exposed, economically unopened."""
from __future__ import annotations

from dataclasses import dataclass

from research.causal_driver_pb1 import C1_FIRST, C1_LAST, DEV_FIRST, DEV_LAST, FV_FIRST, FV_LAST, PROSPECTIVE_FROM
from research.causal_driver_pb1.contracts.enums import DatasetRole
from research.causal_driver_pb1.identity.ids import sha256_obj


@dataclass(frozen=True, slots=True)
class DatasetRoleSpec:
    role: DatasetRole
    first: str | None
    last: str | None
    semantic_exposed: bool
    economic_opened: bool
    economic_payload_access: str
    not_fully_blind: bool
    description: str


def role_catalog() -> tuple[DatasetRoleSpec, ...]:
    return (
        DatasetRoleSpec(
            role=DatasetRole.DEVELOPMENT,
            first=DEV_FIRST,
            last=DEV_LAST,
            semantic_exposed=True,
            economic_opened=True,
            economic_payload_access="ALLOW_IN_DRIVER_DISCOVERY",
            not_fully_blind=True,
            description="PB1 development window; new-architecture discovery",
        ),
        DatasetRoleSpec(
            role=DatasetRole.ECONOMIC_DEVELOPMENT_EXPOSED,
            first=C1_FIRST,
            last=C1_LAST,
            semantic_exposed=True,
            economic_opened=True,
            economic_payload_access="ALLOW_IN_DRIVER_DISCOVERY",
            not_fully_blind=True,
            description="Confirmation 1; exposed for new architecture development",
        ),
        DatasetRoleSpec(
            role=DatasetRole.FROZEN_VALIDATION,
            first=FV_FIRST,
            last=FV_LAST,
            semantic_exposed=True,
            economic_opened=False,
            economic_payload_access="DENY",
            not_fully_blind=True,
            description="SEMANTICALLY_EXPOSED ECONOMICALLY_UNOPENED; not fully blind",
        ),
        DatasetRoleSpec(
            role=DatasetRole.PROSPECTIVE,
            first=PROSPECTIVE_FROM,
            last=None,
            semantic_exposed=False,
            economic_opened=False,
            economic_payload_access="DENY",
            not_fully_blind=False,
            description="Prospective economics sealed until a later freeze",
        ),
        DatasetRoleSpec(
            role=DatasetRole.RUNTIME_CAPTURE,
            first=None,
            last=None,
            semantic_exposed=False,
            economic_opened=False,
            economic_payload_access="DENY",
            not_fully_blind=False,
            description="Live/paper capture; not a research holdout open",
        ),
        DatasetRoleSpec(
            role=DatasetRole.SYNTHETIC_TEST,
            first=None,
            last=None,
            semantic_exposed=False,
            economic_opened=False,
            economic_payload_access="DENY",
            not_fully_blind=False,
            description="Foundation tests only",
        ),
    )


def role_for_session_date(yyyymmdd: str) -> DatasetRole:
    d = str(yyyymmdd)
    if DEV_FIRST <= d <= DEV_LAST:
        return DatasetRole.DEVELOPMENT
    if C1_FIRST <= d <= C1_LAST:
        return DatasetRole.ECONOMIC_DEVELOPMENT_EXPOSED
    if FV_FIRST <= d <= FV_LAST:
        return DatasetRole.FROZEN_VALIDATION
    if d >= PROSPECTIVE_FROM:
        return DatasetRole.PROSPECTIVE
    raise ValueError(f"unmapped_session_date:{d}")


def dataset_role_config_sha256() -> str:
    rows = []
    for spec in role_catalog():
        rows.append(
            {
                "role": spec.role.value,
                "first": spec.first,
                "last": spec.last,
                "semantic_exposed": spec.semantic_exposed,
                "economic_opened": spec.economic_opened,
                "economic_payload_access": spec.economic_payload_access,
                "not_fully_blind": spec.not_fully_blind,
            }
        )
    return sha256_obj(rows)
