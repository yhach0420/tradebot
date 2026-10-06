"""Phase 1 fail-closed errors. Does not mutate Phase 0 error types."""
from __future__ import annotations

from research.causal_driver_pb1.contracts.errors import FailClosedError, FirewallDenied


class UsdJpySourceUnavailable(FailClosedError):
    """Jetta historical source could not be read or extended. No silent substitute."""


class UsdJpyAdapterBlocked(FailClosedError):
    """Adapter integrity gate failed (duplicates, timezone, coverage, firewall)."""


class IngestDateDenied(FirewallDenied):
    """USDJPY historical ingest requested a sealed session date."""
