"""Phase687W5B — Broker position identity with HoldID masking.

kabusapi GET /positions uses ExecutionID as the repay HoldID source.
Artifacts never store raw HoldID; runtime may keep raw for local repay structure only.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
IDENTITY_SCHEMA_VERSION = "687W5B.1"


def mask_hold_id(raw_hold_id: str, *, salt: str = "kbn-w5b") -> str:
    """Deterministic mask for artifacts/logs/Discord. Never reversible from artifact alone."""
    h = str(raw_hold_id or "").strip()
    if not h:
        return ""
    digest = hashlib.sha256(f"{salt}:{h}".encode("utf-8")).hexdigest()[:16]
    prefix = h[:1] if h else "X"
    return f"{prefix}***{digest}"


def hold_id_from_position_row(row: Mapping[str, Any]) -> str:
    """Official positions sample uses ExecutionID as lot id for ClosePositions HoldID."""
    for key in ("ExecutionID", "HoldID", "hold_id", "execution_id"):
        v = row.get(key)
        if v:
            return str(v)
    return ""


@dataclass
class BrokerPositionLot:
    """Runtime-safe lot identity. raw_hold_id must not be written to research artifacts."""

    symbol: str
    side: str
    quantity: int
    leaves_quantity: int
    entry_price: Optional[float]
    exchange: Optional[int]
    margin_trade_type: Optional[int]
    account_type: Optional[int]
    position_open_date: Optional[str]
    source_timestamp: str
    masked_hold_id: str
    raw_hold_id: str = ""  # runtime/local only — strip for artifacts
    paper_position_id: str = ""
    provenance: str = "UNKNOWN"  # LIVE_API_POSITION_RESPONSE | FIXTURE | SYNTHETIC | ...

    def to_artifact_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw_hold_id", None)
        d["raw_hold_id_present"] = bool(self.raw_hold_id)
        d["no_secrets"] = True
        # Fixture HoldID must never be marked live-verified in artifacts
        d["hold_id_live_verified"] = (
            bool(self.masked_hold_id)
            and self.provenance == "LIVE_API_POSITION_RESPONSE"
        )
        return d

    def to_runtime_dict(self) -> dict[str, Any]:
        """Local journal / in-memory only — includes raw hold id."""
        return asdict(self)


def parse_position_lots(
    raw_positions: Sequence[Mapping[str, Any]],
    *,
    source_timestamp: str = "",
    provenance: str = "UNKNOWN",
) -> list[BrokerPositionLot]:
    ts = source_timestamp or datetime.now(JST).isoformat(timespec="seconds")
    lots: list[BrokerPositionLot] = []
    for row in raw_positions or []:
        code = str(row.get("Symbol") or "").strip()
        if not code:
            continue
        sym = code if code.endswith(".T") else f"{code}.T"
        leaves = int(float(row.get("LeavesQty") or row.get("Qty") or 0))
        if leaves <= 0:
            continue
        raw_hid = hold_id_from_position_row(row)
        side_raw = str(row.get("Side") or "")
        side = "SELL" if side_raw in ("1", "SELL") else "BUY"
        try:
            exchange = int(row["Exchange"]) if row.get("Exchange") is not None else None
        except (TypeError, ValueError):
            exchange = None
        try:
            mtt = int(row["MarginTradeType"]) if row.get("MarginTradeType") is not None else None
        except (TypeError, ValueError):
            mtt = None
        try:
            acct = int(row["AccountType"]) if row.get("AccountType") is not None else None
        except (TypeError, ValueError):
            acct = None
        try:
            px = float(row["Price"]) if row.get("Price") is not None else None
        except (TypeError, ValueError):
            px = None
        open_day = row.get("ExecutionDay") or row.get("ExpireDay")
        row_prov = str(row.get("provenance") or provenance or "UNKNOWN")
        lots.append(
            BrokerPositionLot(
                symbol=sym,
                side=side,
                quantity=leaves,
                leaves_quantity=leaves,
                entry_price=px,
                exchange=exchange,
                margin_trade_type=mtt,
                account_type=acct,
                position_open_date=str(open_day) if open_day is not None else None,
                source_timestamp=ts,
                masked_hold_id=mask_hold_id(raw_hid),
                raw_hold_id=raw_hid,
                provenance=row_prov,
            )
        )
    return lots


@dataclass
class PositionIdentityMatch:
    paper_position_id: str
    symbol: str
    match_status: str  # UNIQUE | MULTI | NONE | AMBIGUOUS | QUANTITY_MISMATCH
    matched_masked_hold_ids: list[str] = field(default_factory=list)
    matched_count: int = 0
    total_leaves: int = 0
    recovery_required: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def match_paper_to_broker_lots(
    *,
    paper_position_id: str,
    symbol: str,
    paper_qty: int,
    lots: Sequence[BrokerPositionLot],
) -> PositionIdentityMatch:
    """Exact HoldID mapping. No silent ClosePositionOrder fallback."""
    sym_lots = [L for L in lots if L.symbol == symbol]
    if not sym_lots:
        return PositionIdentityMatch(
            paper_position_id=paper_position_id,
            symbol=symbol,
            match_status="NONE",
            recovery_required=True,
            reason="no_broker_lots_for_symbol",
        )
    if len(sym_lots) == 1 and sym_lots[0].leaves_quantity == int(paper_qty):
        L = sym_lots[0]
        if not L.raw_hold_id:
            return PositionIdentityMatch(
                paper_position_id=paper_position_id,
                symbol=symbol,
                match_status="NONE",
                recovery_required=True,
                reason="hold_id_missing",
            )
        return PositionIdentityMatch(
            paper_position_id=paper_position_id,
            symbol=symbol,
            match_status="UNIQUE",
            matched_masked_hold_ids=[L.masked_hold_id],
            matched_count=1,
            total_leaves=L.leaves_quantity,
        )
    if len(sym_lots) > 1:
        total = sum(L.leaves_quantity for L in sym_lots)
        return PositionIdentityMatch(
            paper_position_id=paper_position_id,
            symbol=symbol,
            match_status="MULTI",
            matched_masked_hold_ids=[L.masked_hold_id for L in sym_lots],
            matched_count=len(sym_lots),
            total_leaves=total,
            recovery_required=total != int(paper_qty),
            reason="multiple_lots_require_CLOSE_EXACT_MULTI_HOLD",
        )
    # single lot qty mismatch
    return PositionIdentityMatch(
        paper_position_id=paper_position_id,
        symbol=symbol,
        match_status="QUANTITY_MISMATCH",
        matched_masked_hold_ids=[sym_lots[0].masked_hold_id],
        matched_count=1,
        total_leaves=sym_lots[0].leaves_quantity,
        recovery_required=True,
        reason="paper_qty_ne_broker_leaves",
    )


def artifact_has_raw_hold_id(blob: str, raw_ids: Sequence[str]) -> bool:
    for hid in raw_ids:
        if hid and hid in blob:
            return True
    return False
