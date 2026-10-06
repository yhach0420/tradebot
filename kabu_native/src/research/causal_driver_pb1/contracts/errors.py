"""Fail-closed errors. Silent fallback is forbidden."""
from __future__ import annotations


class CausalContractError(Exception):
    """Base fail-closed error."""


class FailClosedError(CausalContractError):
    """Use of a value is forbidden; do not substitute."""


class CausalTimeError(FailClosedError):
    """Timestamp / timezone / bar-availability violation."""


class FirewallDenied(FailClosedError):
    """Run mode × dataset role or economic payload denied."""


class IdentityError(FailClosedError):
    """Missing or mismatched source / universe / frozen identity."""


class TransportOrderError(FailClosedError):
    """Non-deterministic or decreasing available_at stream."""


class ImmutabilityError(FailClosedError):
    """Attempt to mutate a frozen contract object."""
