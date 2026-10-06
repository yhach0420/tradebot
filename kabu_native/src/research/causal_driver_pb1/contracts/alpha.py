"""Immutable AlphaSignal. Downstream may AGREE or REJECT. Direction cannot be rewritten."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from research.causal_driver_pb1.contracts.enums import AdapterVerdict, AlphaStatus, Direction, RejectReason, TargetScope
from research.causal_driver_pb1.contracts.errors import FailClosedError, IdentityError
from research.causal_driver_pb1.contracts.time import assert_aware
from research.causal_driver_pb1.identity.ids import alpha_id


@dataclass(frozen=True, slots=True)
class AlphaSignal:
    alpha_id: str
    mechanism_id: str
    generated_at: datetime
    driver_family: str
    driver_observation_ids: tuple[str, ...]
    target_scope: TargetScope
    market_id: str | None
    sector_id: str | None
    symbol: str | None
    direction: Direction
    strength: float | None
    confidence: float | None
    valid_from: datetime
    valid_until: datetime
    causal_evidence: Mapping[str, Any]
    status: AlphaStatus
    source_identity: str

    def __post_init__(self) -> None:
        if not self.mechanism_id:
            raise IdentityError("mechanism_id_missing")
        assert_aware(self.generated_at, field="generated_at")
        assert_aware(self.valid_from, field="valid_from")
        assert_aware(self.valid_until, field="valid_until")
        if self.valid_until <= self.valid_from:
            raise FailClosedError("alpha_valid_until_not_after_valid_from")
        expected = alpha_id(
            mechanism_id=self.mechanism_id,
            driver_observation_ids=tuple(self.driver_observation_ids),
            generated_at=self.generated_at,
            direction=self.direction.value,
        )
        if self.alpha_id != expected:
            raise IdentityError("alpha_id_not_deterministic")
        object.__setattr__(self, "driver_observation_ids", tuple(self.driver_observation_ids))
        object.__setattr__(self, "causal_evidence", dict(self.causal_evidence))

    def flipped(self) -> Direction:
        """Diagnostic only. Does not mutate. LONG/SHORT invert; NEUTRAL stays."""
        if self.direction is Direction.LONG:
            return Direction.SHORT
        if self.direction is Direction.SHORT:
            return Direction.LONG
        return Direction.NEUTRAL


@dataclass(frozen=True, slots=True)
class Pb1AdapterDecision:
    """Phase 0 stub. Future adapter returns AGREE/REJECT only. Direction is copied from Alpha."""

    verdict: AdapterVerdict
    reason: RejectReason | None
    alpha_id: str
    direction: Direction

    def would_flip(self, alpha: AlphaSignal) -> bool:
        return self.direction is not alpha.direction


def pb1_adapter_decide(*, alpha: AlphaSignal, pb1_direction: Direction | None) -> Pb1AdapterDecision:
    """Cannot emit a flipped Alpha. Mismatch is REJECT with original Alpha.direction."""
    if pb1_direction is None:
        return Pb1AdapterDecision(
            verdict=AdapterVerdict.REJECT,
            reason=RejectReason.PB1_NOT_ACTIVE,
            alpha_id=alpha.alpha_id,
            direction=alpha.direction,
        )
    if pb1_direction is not alpha.direction:
        return Pb1AdapterDecision(
            verdict=AdapterVerdict.REJECT,
            reason=RejectReason.ALPHA_DIRECTION_MISMATCH,
            alpha_id=alpha.alpha_id,
            direction=alpha.direction,
        )
    return Pb1AdapterDecision(
        verdict=AdapterVerdict.AGREE,
        reason=None,
        alpha_id=alpha.alpha_id,
        direction=alpha.direction,
    )


def not_registered_is_not_no_alpha() -> dict[str, str]:
    return {
        "NOT_REGISTERED": RejectReason.NOT_REGISTERED.value,
        "NO_ALPHA": RejectReason.NO_ALPHA.value,
        "separation": "Paper slot absence is tradability, not missing alpha",
    }
