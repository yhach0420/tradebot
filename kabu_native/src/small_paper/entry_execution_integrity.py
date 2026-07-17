"""ENTRY execution-stage integrity (post-gate, pre-notify/order).

Logging / ordering guards only — does not change ExposureGate ENTRY conditions.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

STAGE_GATE_ACCEPTED = "gate_accepted"
STAGE_EXECUTION_PAYLOAD_VALIDATED = "execution_payload_validated"
STAGE_QUEUE_SELECTED = "queue_selected"
STAGE_POSITION_REGISTERED = "position_registered"
STAGE_OFFICIAL_ENTRY = "official_entry"
STAGE_ACCEPT_ABORTED = "accept_aborted_execution_payload_invalid"

ENTRY_STAGES = (
    STAGE_GATE_ACCEPTED,
    STAGE_EXECUTION_PAYLOAD_VALIDATED,
    STAGE_QUEUE_SELECTED,
    STAGE_POSITION_REGISTERED,
    STAGE_OFFICIAL_ENTRY,
    STAGE_ACCEPT_ABORTED,
)


def finite_positive(val: Any) -> Optional[float]:
    """Return float if finite and > 0; never coerce null/NaN to 0."""
    if val is None or val == "":
        return None
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return v


def _as_finite(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def make_decision_id(
    *,
    symbol: str,
    entry_time: Any,
    message_index: Any = None,
    scan_id: Any = None,
) -> str:
    raw = f"{symbol}|{entry_time}|{message_index or ''}|{scan_id or ''}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"dec_{digest}"


@dataclass
class ExecutionPayloadValidation:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    current_price: Optional[float] = None
    entry_price: Optional[float] = None
    quantity: Optional[float] = None
    side: str = ""
    event_time: str = ""

    def to_fields(self) -> dict[str, Any]:
        return {
            "execution_payload_validated": self.ok,
            "validation_result": "ok" if self.ok else "failed",
            "failure_reason": ",".join(self.reasons) if self.reasons else "",
            "validation_failure_reasons": list(self.reasons),
            "validated_current_price": self.current_price,
            "validated_entry_price": self.entry_price,
            "validated_quantity": self.quantity,
            "validated_side": self.side,
        }


def _classify_price(val: Any, *, label: str) -> tuple[Optional[float], Optional[str]]:
    """Return (price, failure_reason). Never coerce null/NaN to 0."""
    if val is None or val == "":
        return None, f"{label}_missing"
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None, f"{label}_non_finite"
    if math.isnan(v) or math.isinf(v):
        return None, f"{label}_non_finite"
    if v <= 0:
        return None, f"{label}_non_positive"
    return v, None


def resolve_entry_prices(
    trade: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> tuple[Optional[float], Optional[float], list[str]]:
    """Strict price resolution — no null→0, no Ask/Calc/ring fallback."""
    notes: list[str] = []
    cur, cur_err = _classify_price(payload.get("CurrentPrice"), label="current_price")
    if cur is None and payload.get("CurrentPrice") in (None, ""):
        cur, cur_err = _classify_price(trade.get("current_price"), label="current_price")
    if cur is None:
        notes.append(cur_err or "current_price_missing")

    ent_raw = trade.get("entry_price")
    if ent_raw in (None, ""):
        ent = cur
        if ent is None:
            notes.append("entry_price_missing")
    else:
        ent, ent_err = _classify_price(ent_raw, label="entry_price")
        if ent is None:
            notes.append(ent_err or "entry_price_missing")
    return cur, ent, notes


def validate_execution_payload(
    *,
    symbol: str,
    trade: Mapping[str, Any],
    payload: Mapping[str, Any],
    event_time: Any,
    quantity: Any = 100,
    side: Any = "2",
    session_entry_allowed: bool = True,
) -> ExecutionPayloadValidation:
    reasons: list[str] = []
    sym = str(symbol or trade.get("symbol") or "").strip()
    if not sym or not (sym.endswith(".T") or sym.isdigit() or "." in sym):
        reasons.append("symbol_invalid")

    cur, ent, price_notes = resolve_entry_prices(trade, payload)
    reasons.extend(price_notes)
    if cur is not None and not math.isfinite(cur):
        reasons.append("current_price_non_finite")
        cur = None

    qty = finite_positive(quantity if quantity is not None else trade.get("quantity") or 100)
    if qty is None:
        reasons.append("quantity_invalid")

    side_s = str(side or trade.get("side") or "2").strip()
    if side_s not in ("1", "2", "buy", "sell", "BUY", "SELL", "long", "LONG"):
        reasons.append("side_invalid")

    et = str(event_time or trade.get("entry_time") or trade.get("event_time") or "").strip()
    if not et:
        reasons.append("event_time_missing")

    if not session_entry_allowed:
        reasons.append("session_entry_not_allowed")

    # Deduplicate while preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            uniq.append(r)

    return ExecutionPayloadValidation(
        ok=len(uniq) == 0,
        reasons=uniq,
        current_price=cur,
        entry_price=ent,
        quantity=qty,
        side=side_s,
        event_time=et,
    )


def is_official_entry_ready(acc: Mapping[str, Any]) -> bool:
    stage = str(acc.get("accept_stage") or "")
    if stage not in (STAGE_POSITION_REGISTERED, STAGE_OFFICIAL_ENTRY):
        if not bool(acc.get("position_registered")):
            return False
    pid = str(acc.get("position_id") or acc.get("observer_position_id") or "")
    if not pid:
        return False
    px = finite_positive(acc.get("entry_price") or acc.get("validated_entry_price") or acc.get("current_price"))
    return px is not None


def stage_event_row(
    *,
    decision_id: str,
    stage: str,
    symbol: str,
    event_time: str,
    session_key: str = "",
    position_id: str = "",
    current_price: Any = None,
    entry_price: Any = None,
    validation_result: str = "",
    failure_reason: str = "",
    extra: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    now = datetime.now(JST).isoformat(timespec="milliseconds")
    row = {
        "event_type": "entry_stage",
        "decision_id": decision_id,
        "position_id": position_id or None,
        "symbol": symbol,
        "event_time": event_time,
        "stage": stage,
        "accept_stage": stage,
        "stage_time": now,
        "current_price": current_price,
        "entry_price": entry_price,
        "validation_result": validation_result,
        "failure_reason": failure_reason,
        "session_key": session_key,
    }
    if extra:
        row.update({k: v for k, v in extra.items() if v is not None})
    return row


@dataclass
class EntryStageCounters:
    gate_accepted_count: int = 0
    execution_payload_validated_count: int = 0
    queue_selected_count: int = 0
    position_registered_count: int = 0
    official_entry_count: int = 0
    accept_aborted_count: int = 0
    ghost_accept_count: int = 0
    _seen_stage_keys: set[str] = field(default_factory=set)

    def record(self, decision_id: str, stage: str) -> bool:
        """Record stage once per decision_id+stage. Returns False if duplicate."""
        key = f"{decision_id}|{stage}"
        if key in self._seen_stage_keys:
            return False
        self._seen_stage_keys.add(key)
        if stage == STAGE_GATE_ACCEPTED:
            self.gate_accepted_count += 1
        elif stage == STAGE_EXECUTION_PAYLOAD_VALIDATED:
            self.execution_payload_validated_count += 1
        elif stage == STAGE_QUEUE_SELECTED:
            self.queue_selected_count += 1
        elif stage == STAGE_POSITION_REGISTERED:
            self.position_registered_count += 1
        elif stage == STAGE_OFFICIAL_ENTRY:
            self.official_entry_count += 1
        elif stage == STAGE_ACCEPT_ABORTED:
            self.accept_aborted_count += 1
            self.ghost_accept_count += 1
        return True

    def summary_fields(self) -> dict[str, Any]:
        return {
            "gate_accepted_count": self.gate_accepted_count,
            "execution_payload_validated_count": self.execution_payload_validated_count,
            "queue_selected_count": self.queue_selected_count,
            "position_registered_count": self.position_registered_count,
            "official_entry_count": self.official_entry_count,
            "accept_aborted_count": self.accept_aborted_count,
            "ghost_accept_count": self.ghost_accept_count,
            "accepted_count_source": "gate_accepted",
        }
