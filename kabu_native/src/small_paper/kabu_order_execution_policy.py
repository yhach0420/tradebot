"""Phase687W5 — ExecutionPolicy schema only (no production selection).

Separates:
  A. Intent — what to trade
  B. ExecutionPolicy — how to send (NOT selected for production)
  C. KabuOrderRequest — concrete API payload

All policies have production_authorized=false.
Strategy logic must NOT select a production policy in this phase.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


POLICY_SCHEMA_VERSION = "687W5.1"


class OrderStyle(str, Enum):
    NOT_SELECTED = "NOT_SELECTED"
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class ExecutionPolicyId(str, Enum):
    NOT_SELECTED = "NOT_SELECTED"
    DRYRUN_MARKET_REFERENCE = "DRYRUN_MARKET_REFERENCE"
    DRYRUN_LIMIT_REFERENCE = "DRYRUN_LIMIT_REFERENCE"
    PRODUCTION_FORBIDDEN = "PRODUCTION_FORBIDDEN"


class ReferencePriceSource(str, Enum):
    NONE = "NONE"
    ASK = "ASK"
    BID = "BID"
    LAST = "LAST"
    MID = "MID"
    SNAPSHOT = "SNAPSHOT"


@dataclass(frozen=True)
class ExecutionPolicy:
    """Configurable structure only — production order style is undecided."""

    policy_id: str = ExecutionPolicyId.NOT_SELECTED.value
    entry_or_exit: str = "ENTRY"  # ENTRY | EXIT
    order_style: str = OrderStyle.NOT_SELECTED.value
    reference_price_source: str = ReferencePriceSource.NONE.value
    price_offset_ticks: int = 0
    expiry: int = 0  # kabusapi ExpireDay; 0 = day
    allow_market: bool = False
    allow_limit: bool = False
    max_slippage_bps: Optional[float] = None
    cancel_after_sec: Optional[float] = None
    replace_allowed: bool = False
    production_authorized: bool = False
    schema_version: str = POLICY_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_selected_for_dryrun(self) -> bool:
        return self.policy_id in (
            ExecutionPolicyId.DRYRUN_MARKET_REFERENCE.value,
            ExecutionPolicyId.DRYRUN_LIMIT_REFERENCE.value,
        ) and not self.production_authorized

    @property
    def request_valid_for_submit(self) -> bool:
        """Never true in this phase — production submit is forbidden."""
        return False


def not_selected_policy(*, entry_or_exit: str = "ENTRY") -> ExecutionPolicy:
    return ExecutionPolicy(
        policy_id=ExecutionPolicyId.NOT_SELECTED.value,
        entry_or_exit=entry_or_exit,
        order_style=OrderStyle.NOT_SELECTED.value,
        production_authorized=False,
    )


def dryrun_limit_policy(*, entry_or_exit: str = "ENTRY") -> ExecutionPolicy:
    """Explicit test fixture policy — not for production."""
    return ExecutionPolicy(
        policy_id=ExecutionPolicyId.DRYRUN_LIMIT_REFERENCE.value,
        entry_or_exit=entry_or_exit,
        order_style=OrderStyle.LIMIT.value,
        reference_price_source=ReferencePriceSource.SNAPSHOT.value,
        allow_limit=True,
        allow_market=False,
        production_authorized=False,
    )


def dryrun_market_policy(*, entry_or_exit: str = "EXIT") -> ExecutionPolicy:
    """Explicit test fixture policy — not for production."""
    return ExecutionPolicy(
        policy_id=ExecutionPolicyId.DRYRUN_MARKET_REFERENCE.value,
        entry_or_exit=entry_or_exit,
        order_style=OrderStyle.MARKET.value,
        reference_price_source=ReferencePriceSource.NONE.value,
        allow_market=True,
        allow_limit=False,
        production_authorized=False,
    )


def production_forbidden_policy(*, entry_or_exit: str = "ENTRY") -> ExecutionPolicy:
    return ExecutionPolicy(
        policy_id=ExecutionPolicyId.PRODUCTION_FORBIDDEN.value,
        entry_or_exit=entry_or_exit,
        order_style=OrderStyle.NOT_SELECTED.value,
        production_authorized=False,
    )


def execution_policy_schema() -> dict[str, Any]:
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "policy_ids": [e.value for e in ExecutionPolicyId],
        "order_styles": [e.value for e in OrderStyle],
        "reference_price_sources": [e.value for e in ReferencePriceSource],
        "fields": [
            "policy_id",
            "entry_or_exit",
            "order_style",
            "reference_price_source",
            "price_offset_ticks",
            "expiry",
            "allow_market",
            "allow_limit",
            "max_slippage_bps",
            "cancel_after_sec",
            "replace_allowed",
            "production_authorized",
        ],
        "defaults": {
            "production_authorized": False,
            "policy_id": ExecutionPolicyId.NOT_SELECTED.value,
            "request_valid_for_submit": False,
        },
        "selection_status": "NOT_IMPLEMENTED",
        "notes": (
            "Production market vs limit is undecided. "
            "Mock/tests must pass an explicit dry-run policy."
        ),
    }


# Known enum sets for consistency checks
EXECUTION_POLICY_IDS = tuple(e.value for e in ExecutionPolicyId)
