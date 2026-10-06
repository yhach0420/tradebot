"""Run-mode × dataset-role allowlist and economic payload deny. Fail-closed."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from research.causal_driver_pb1 import PHASE0_ALLOWED_RUN_MODE
from research.causal_driver_pb1.contracts.enums import AccessKind, DatasetRole, EventType, RunMode
from research.causal_driver_pb1.contracts.envelope import envelope
from research.causal_driver_pb1.contracts.errors import FirewallDenied
from research.causal_driver_pb1.contracts.time import JST
from research.causal_driver_pb1.datasets.roles import role_catalog
from research.causal_driver_pb1.identity.ids import sha256_obj

ECONOMIC_FIELDS = frozenset(
    {
        "pnl",
        "net_pnl",
        "net_pnl_yen",
        "gross_pnl_yen",
        "future_return",
        "future_return_bps",
        "mfe",
        "mae",
        "mfe_bps",
        "mae_bps",
        "trade_outcome",
        "complete_strategy_economics",
        "profit_factor",
        "net_bps",
        "gross_bps",
    }
)

# Payload (non-economic) allowlist. FOUNDATION_TEST may only read SYNTHETIC_TEST.
PAYLOAD_ALLOW: dict[RunMode, frozenset[DatasetRole]] = {
    RunMode.FOUNDATION_TEST: frozenset({DatasetRole.SYNTHETIC_TEST}),
    RunMode.DRIVER_DISCOVERY: frozenset({DatasetRole.DEVELOPMENT, DatasetRole.ECONOMIC_DEVELOPMENT_EXPOSED}),
    RunMode.ALPHA_DEVELOPMENT: frozenset({DatasetRole.DEVELOPMENT, DatasetRole.ECONOMIC_DEVELOPMENT_EXPOSED}),
    RunMode.FROZEN_VALIDATION: frozenset({DatasetRole.FROZEN_VALIDATION}),
    RunMode.PROSPECTIVE_PAPER: frozenset({DatasetRole.PROSPECTIVE, DatasetRole.RUNTIME_CAPTURE}),
}

# Metadata/manifest inspection: allowed without opening economics.
METADATA_ALLOW: dict[RunMode, frozenset[DatasetRole]] = {
    RunMode.FOUNDATION_TEST: frozenset(DatasetRole),
    RunMode.DRIVER_DISCOVERY: frozenset({DatasetRole.DEVELOPMENT, DatasetRole.ECONOMIC_DEVELOPMENT_EXPOSED}),
    RunMode.ALPHA_DEVELOPMENT: frozenset({DatasetRole.DEVELOPMENT, DatasetRole.ECONOMIC_DEVELOPMENT_EXPOSED}),
    RunMode.FROZEN_VALIDATION: frozenset({DatasetRole.FROZEN_VALIDATION}),
    RunMode.PROSPECTIVE_PAPER: frozenset({DatasetRole.PROSPECTIVE, DatasetRole.RUNTIME_CAPTURE}),
}


def _now() -> datetime:
    return datetime.now(JST)


def _norm_fields(fields: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(str(x).strip().lower() for x in fields)


def economic_fields_requested(fields: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(f for f in _norm_fields(fields) if f in ECONOMIC_FIELDS)


@dataclass
class AccessLedger:
    run_id: str
    run_mode: RunMode
    events: list[dict[str, Any]] = field(default_factory=list)

    def record(self, row: dict[str, Any]) -> None:
        self.events.append(dict(row))


@dataclass(frozen=True, slots=True)
class AccessDecision:
    allowed: bool
    reason: str
    event: dict[str, Any]


def assert_phase0_run_mode(mode: RunMode) -> None:
    if mode is not RunMode.FOUNDATION_TEST:
        raise FirewallDenied("phase0_allows_foundation_test_only")
    if mode.value != PHASE0_ALLOWED_RUN_MODE:
        raise FirewallDenied("phase0_run_mode_mismatch")


def request_dataset(
    *,
    ledger: AccessLedger,
    run_mode: RunMode,
    dataset_id: str,
    dataset_role: DatasetRole,
    requested_fields: tuple[str, ...] | list[str],
    access_kind: AccessKind,
) -> AccessDecision:
    fields = _norm_fields(requested_fields)
    econ = economic_fields_requested(fields)
    allowed = True
    reason = "ALLOW"
    if access_kind is AccessKind.ECONOMIC_PAYLOAD or econ:
        spec = next(s for s in role_catalog() if s.role is dataset_role)
        if spec.economic_payload_access == "DENY" or dataset_role in {
            DatasetRole.FROZEN_VALIDATION,
            DatasetRole.PROSPECTIVE,
        }:
            allowed = False
            reason = "ECONOMIC_PAYLOAD_DENIED"
        elif run_mode is RunMode.FOUNDATION_TEST:
            allowed = False
            reason = "FOUNDATION_TEST_NO_ECONOMIC_PAYLOAD"
        elif dataset_role not in PAYLOAD_ALLOW.get(run_mode, frozenset()):
            allowed = False
            reason = "RUN_MODE_DATASET_ROLE_DENIED"
    elif access_kind is AccessKind.METADATA:
        if dataset_role not in METADATA_ALLOW.get(run_mode, frozenset()):
            allowed = False
            reason = "METADATA_ROLE_DENIED"
    else:
        if dataset_role not in PAYLOAD_ALLOW.get(run_mode, frozenset()):
            allowed = False
            reason = "RUN_MODE_DATASET_ROLE_DENIED"

    row = {
        "event_type": EventType.DATASET_ACCESS.value,
        "run_id": ledger.run_id,
        "timestamp": _now().isoformat(),
        "run_mode": run_mode.value,
        "dataset_id": dataset_id,
        "dataset_role": dataset_role.value,
        "requested_fields": list(fields),
        "access_kind": access_kind.value,
        "allowed": allowed,
        "reason": reason,
    }
    ledger.record(row)
    env = envelope(
        event_type=EventType.DATASET_ACCESS,
        event_time=_now(),
        available_at=_now(),
        received_at=_now(),
        source="firewall",
        identity=f"{ledger.run_id}:{dataset_id}:{dataset_role.value}:{reason}",
        payload=row,
    )
    _ = env
    if not allowed:
        raise FirewallDenied(reason)
    return AccessDecision(allowed=True, reason=reason, event=row)


def firewall_config_sha256() -> str:
    payload = {
        "payload_allow": {k.value: sorted(x.value for x in v) for k, v in PAYLOAD_ALLOW.items()},
        "metadata_allow": {k.value: sorted(x.value for x in v) for k, v in METADATA_ALLOW.items()},
        "economic_fields": sorted(ECONOMIC_FIELDS),
        "phase0_allowed_run_mode": PHASE0_ALLOWED_RUN_MODE,
    }
    return sha256_obj(payload)
