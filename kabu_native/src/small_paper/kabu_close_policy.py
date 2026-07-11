"""Phase687W5B — EXIT close policy (exact HoldID preferred; no silent Order=0 fallback)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from small_paper.kabu_position_identity import BrokerPositionLot, PositionIdentityMatch, match_paper_to_broker_lots
from small_paper.kabu_sendorder_contract import ClosePositionMode, ExchangePolicy


class ClosePolicyId(str, Enum):
    CLOSE_EXACT_HOLD_ID = "CLOSE_EXACT_HOLD_ID"
    CLOSE_EXACT_MULTI_HOLD = "CLOSE_EXACT_MULTI_HOLD"
    CLOSE_POSITION_ORDER_0 = "CLOSE_POSITION_ORDER_0"
    NOT_SELECTED = "NOT_SELECTED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


@dataclass
class ClosePolicyDecision:
    policy_id: str
    request_valid: bool = False
    request_valid_for_submit: bool = False
    would_submit: bool = False
    production_authorized: bool = False
    recovery_required: bool = False
    close_position_mode: str = ClosePositionMode.NOT_APPLICABLE.value
    close_positions: list[dict[str, Any]] = field(default_factory=list)  # runtime may use raw HoldID
    close_positions_masked: list[dict[str, Any]] = field(default_factory=list)
    close_position_order: Optional[int] = None
    exchange_policy: str = ExchangePolicy.REPAY_MATCH_OPEN_POSITION_EXCHANGE.value
    open_position_exchange: Optional[int] = None
    margin_trade_type: Optional[int] = None
    quantity: int = 0
    reason: str = ""
    identity: dict[str, Any] = field(default_factory=dict)

    def to_artifact_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Never export raw HoldIDs in close_positions for artifacts
        d["close_positions"] = []
        d["has_runtime_close_positions"] = bool(self.close_positions)
        d["request_valid_for_submit"] = False
        d["production_authorized"] = False
        d["would_submit"] = False
        return d


def allocate_multi_hold_qty(
    lots: Sequence[BrokerPositionLot],
    *,
    target_qty: int,
) -> tuple[list[tuple[BrokerPositionLot, int]], str]:
    """Explicit FIFO by open date then masked id. No over-close."""
    remaining = int(target_qty)
    if remaining <= 0:
        return [], "quantity_non_positive"
    ordered = sorted(
        lots,
        key=lambda L: (str(L.position_open_date or ""), L.masked_hold_id),
    )
    alloc: list[tuple[BrokerPositionLot, int]] = []
    for L in ordered:
        if remaining <= 0:
            break
        take = min(L.leaves_quantity, remaining)
        if take <= 0:
            continue
        alloc.append((L, take))
        remaining -= take
    if remaining > 0:
        return [], "insufficient_broker_quantity"
    total = sum(q for _, q in alloc)
    if total > sum(L.leaves_quantity for L in lots):
        return [], "over_close"
    return alloc, ""


def decide_close_policy(
    *,
    paper_position_id: str,
    symbol: str,
    paper_qty: int,
    lots: Sequence[BrokerPositionLot],
    allow_close_position_order_0_as_test_candidate: bool = False,
    select_close_position_order_0: bool = False,
) -> ClosePolicyDecision:
    """Prefer CLOSE_EXACT_HOLD_ID. Never silently fall back to Order=0."""
    identity = match_paper_to_broker_lots(
        paper_position_id=paper_position_id,
        symbol=symbol,
        paper_qty=paper_qty,
        lots=lots,
    )
    base = ClosePolicyDecision(
        policy_id=ClosePolicyId.NOT_SELECTED.value,
        identity=identity.to_dict(),
        quantity=int(paper_qty),
    )

    if select_close_position_order_0:
        if not allow_close_position_order_0_as_test_candidate:
            base.policy_id = ClosePolicyId.RECOVERY_REQUIRED.value
            base.recovery_required = True
            base.reason = "CLOSE_POSITION_ORDER_0_not_production_authorized"
            return base
        # Test candidate only — still not production / not submit
        base.policy_id = ClosePolicyId.CLOSE_POSITION_ORDER_0.value
        base.close_position_mode = ClosePositionMode.CLOSE_POSITION_ORDER.value
        base.close_position_order = 0
        base.request_valid = True
        base.reason = "test_candidate_only_production_forbidden"
        return base

    if identity.match_status == "UNIQUE":
        sym_lots = [L for L in lots if L.symbol == symbol]
        lot = sym_lots[0]
        if lot.exchange is None or lot.margin_trade_type is None or not lot.raw_hold_id:
            base.policy_id = ClosePolicyId.RECOVERY_REQUIRED.value
            base.recovery_required = True
            base.reason = "unique_lot_missing_exchange_or_mtt_or_hold_id"
            return base
        if int(paper_qty) > lot.leaves_quantity:
            base.policy_id = ClosePolicyId.RECOVERY_REQUIRED.value
            base.recovery_required = True
            base.reason = "over_close"
            return base
        base.policy_id = ClosePolicyId.CLOSE_EXACT_HOLD_ID.value
        base.close_position_mode = ClosePositionMode.CLOSE_POSITIONS.value
        base.close_positions = [{"HoldID": lot.raw_hold_id, "Qty": int(paper_qty)}]
        base.close_positions_masked = [{"HoldID": lot.masked_hold_id, "Qty": int(paper_qty)}]
        base.open_position_exchange = lot.exchange
        base.margin_trade_type = lot.margin_trade_type
        base.request_valid = True
        base.reason = "exact_hold_id"
        return base

    if identity.match_status == "MULTI":
        sym_lots = [L for L in lots if L.symbol == symbol]
        # Exchange / MTT consistency
        exchanges = {L.exchange for L in sym_lots}
        mtts = {L.margin_trade_type for L in sym_lots}
        if None in exchanges or len(exchanges) != 1:
            base.policy_id = ClosePolicyId.RECOVERY_REQUIRED.value
            base.recovery_required = True
            base.reason = "multi_lot_exchange_inconsistent_or_unknown"
            return base
        if None in mtts or len(mtts) != 1:
            base.policy_id = ClosePolicyId.RECOVERY_REQUIRED.value
            base.recovery_required = True
            base.reason = "multi_lot_margin_trade_type_inconsistent_or_unknown"
            return base
        alloc, err = allocate_multi_hold_qty(sym_lots, target_qty=int(paper_qty))
        if err:
            base.policy_id = ClosePolicyId.RECOVERY_REQUIRED.value
            base.recovery_required = True
            base.reason = err
            return base
        base.policy_id = ClosePolicyId.CLOSE_EXACT_MULTI_HOLD.value
        base.close_position_mode = ClosePositionMode.CLOSE_POSITIONS.value
        base.close_positions = [{"HoldID": L.raw_hold_id, "Qty": q} for L, q in alloc]
        base.close_positions_masked = [{"HoldID": L.masked_hold_id, "Qty": q} for L, q in alloc]
        base.open_position_exchange = next(iter(exchanges))
        base.margin_trade_type = next(iter(mtts))
        base.request_valid = True
        base.reason = "exact_multi_hold_allocation"
        return base

    # NONE / QUANTITY_MISMATCH / AMBIGUOUS — never silent Order=0
    base.policy_id = ClosePolicyId.RECOVERY_REQUIRED.value
    base.recovery_required = True
    base.reason = identity.reason or identity.match_status
    return base


def close_policy_matrix() -> list[dict[str, Any]]:
    return [
        {
            "policy_id": ClosePolicyId.CLOSE_EXACT_HOLD_ID.value,
            "priority": 1,
            "production_authorized": False,
            "dryrun_recommended": True,
        },
        {
            "policy_id": ClosePolicyId.CLOSE_EXACT_MULTI_HOLD.value,
            "priority": 2,
            "production_authorized": False,
            "dryrun_recommended": True,
        },
        {
            "policy_id": ClosePolicyId.CLOSE_POSITION_ORDER_0.value,
            "priority": 99,
            "production_authorized": False,
            "dryrun_recommended": False,
            "note": "test candidate only; no silent fallback",
        },
        {
            "policy_id": ClosePolicyId.NOT_SELECTED.value,
            "priority": 0,
            "production_authorized": False,
            "dryrun_recommended": False,
        },
        {
            "policy_id": ClosePolicyId.RECOVERY_REQUIRED.value,
            "priority": -1,
            "production_authorized": False,
            "dryrun_recommended": False,
        },
    ]
