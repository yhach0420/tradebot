"""Aggregate gross budget. Research contract only. Runtime is unchanged.

Paper G is derived: BASE_CAPITAL_YEN * MAX_LEVERAGE_MULTIPLIER.
MAX_CONCURRENT remains an occupancy cap. It does not split G.
"""
from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

POLICY_ID = "AGGREGATE_GROSS_BUDGET_POLICY_V2"
SUPERSEDED_POLICY_ID = "CAPITAL_SLOT_ADMISSION_POLICY"
SUPERSEDED_BY = "SUPERSEDED_BY_AGGREGATE_GROSS_BUDGET_POLICY_V2"
SUPERSEDED_RULE = "MAX_POSITION_NOTIONAL = G / 5"
SELECTOR_ID = "DAYTRADE50_SELECTOR_V2"
QTY = 100
MAX_CONCURRENT = 5
RUNTIME_APPLIED = False
SCORE_PRECISION = 6

BUDGET_INELIGIBLE_PRESELECT = "BUDGET_INELIGIBLE_PRESELECT"
BUDGET_INELIGIBLE_AT_ENTRY = "BUDGET_INELIGIBLE_AT_ENTRY"
NOTIONAL_AGGREGATE_CAP = "NOTIONAL_AGGREGATE_CAP"
AWAIT_USER_G = "AWAIT_USER_G"
ADMIT = "ADMIT"


def policy_document() -> dict[str, Any]:
    return {
        "policy_id": POLICY_ID,
        "selector_id": SELECTOR_ID,
        "supersedes": SUPERSEDED_POLICY_ID,
        "superseded_by": SUPERSEDED_BY,
        "superseded_rule": SUPERSEDED_RULE,
        "user_capital_parameter": "SYSTEM_GROSS_EXPOSURE_BUDGET_YEN",
        "qty": QTY,
        "max_concurrent": MAX_CONCURRENT,
        "max_concurrent_meaning": "occupancy_count_cap",
        "max_aggregate_gross_yen": "G",
        "per_position_cap": None,
        "per_slot_budget": None,
        "dynamic_position_sizing": False,
        "runtime_applied": RUNTIME_APPLIED,
        "score_precision": SCORE_PRECISION,
    }


def policy_spec_sha() -> str:
    """Hash of the G-free policy structure. This is not an instance hash."""
    raw = json.dumps(policy_document(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def policy_sha() -> str:
    """Deprecated name for policy_spec_sha. Kept so the digest stays addressable."""
    return policy_spec_sha()


def canonical_gross_budget_yen(gross_budget_yen: float) -> int:
    """Whole yen only. The same amount always enters the instance document as an int."""
    if isinstance(gross_budget_yen, bool) or gross_budget_yen is None:
        raise ValueError("G must be a positive whole yen amount")
    number = float(gross_budget_yen)
    if not math.isfinite(number) or number <= 0 or not number.is_integer():
        raise ValueError("G must be a positive whole yen amount")
    return int(number)


_ADMISSION = (
    "candidate_notional > 0 and candidate_notional <= G and "
    "open_entry_notional_sum + candidate_notional <= G and "
    "open_count < MAX_CONCURRENT and same-symbol, reentry, and fill gates pass"
)
_SLOT_RELEASE = "on EXIT, release that position entry_notional from OPEN_GROSS"
BROKER_BUYING_POWER_UNAVAILABLE = "BROKER_BUYING_POWER_UNAVAILABLE"


def legacy_direct_g_document(gross_budget_yen: float) -> dict[str, Any]:
    """Previous direct-G instance. Used only to recognize an already stored file."""
    g_yen = canonical_gross_budget_yen(gross_budget_yen)
    return {
        "policy_id": POLICY_ID,
        "policy_spec_sha": policy_spec_sha(),
        "SYSTEM_GROSS_EXPOSURE_BUDGET_YEN": g_yen,
        "qty": QTY,
        "max_concurrent": MAX_CONCURRENT,
        "max_concurrent_meaning": "occupancy_count_cap",
        "admission": _ADMISSION,
        "slot_release": _SLOT_RELEASE,
        "event_order": "preserve the supplied Frozen X1 order",
        "fail_closed": "fewer than 50 eligible names publishes no symbols",
        "per_position_cap": None,
        "per_slot_budget": None,
        "dynamic_position_sizing": False,
        "qty_resize": False,
    }


def legacy_direct_g_instance_sha(gross_budget_yen: float) -> str:
    raw = json.dumps(
        legacy_direct_g_document(gross_budget_yen),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def canonical_leverage(value: object) -> str:
    """Positive finite multiplier. 2 and 2.0 share one canonical text."""
    if isinstance(value, bool) or value is None:
        raise ValueError("leverage must be a positive finite multiplier")
    if isinstance(value, str):
        text = value.strip()
        if not text or any(ch in text for ch in "eE"):
            raise ValueError("leverage must be a positive finite multiplier")
        try:
            number = Decimal(text)
        except Exception as exc:
            raise ValueError("leverage must be a positive finite multiplier") from exc
    elif isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("leverage must be a positive finite multiplier")
        number = Decimal(str(value))
    else:
        raise ValueError("leverage must be a positive finite multiplier")
    if not number.is_finite() or number <= 0:
        raise ValueError("leverage must be a positive finite multiplier")
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered in {"", "0"}:
        raise ValueError("leverage must be a positive finite multiplier")
    return rendered


def derived_requested_gross_yen(base_capital_yen: int, max_leverage_multiplier: object) -> int:
    base = canonical_gross_budget_yen(base_capital_yen)
    leverage = canonical_leverage(max_leverage_multiplier)
    product = Decimal(base) * Decimal(leverage)
    if product != product.to_integral_value():
        raise ValueError("derived G must be a whole yen amount")
    return int(product)


def policy_instance_document(base_capital_yen: float, max_leverage_multiplier: object) -> dict[str, Any]:
    base = canonical_gross_budget_yen(base_capital_yen)
    leverage = canonical_leverage(max_leverage_multiplier)
    g_yen = derived_requested_gross_yen(base, leverage)
    return {
        "policy_id": POLICY_ID,
        "policy_spec_sha": policy_spec_sha(),
        "BASE_CAPITAL_YEN": base,
        "MAX_LEVERAGE_MULTIPLIER": leverage,
        "REQUESTED_GROSS_EXPOSURE_BUDGET_YEN": g_yen,
        "SYSTEM_GROSS_EXPOSURE_BUDGET_YEN": g_yen,
        "g_source": "BASE_CAPITAL_YEN * MAX_LEVERAGE_MULTIPLIER",
        "qty": QTY,
        "max_concurrent": MAX_CONCURRENT,
        "max_concurrent_meaning": "occupancy_count_cap",
        "admission": _ADMISSION,
        "slot_release": _SLOT_RELEASE,
        "event_order": "preserve the supplied Frozen X1 order",
        "fail_closed": "fewer than 50 eligible names publishes no symbols",
        "per_position_cap": None,
        "per_slot_budget": None,
        "dynamic_position_sizing": False,
        "qty_resize": False,
    }


def policy_instance_sha(
    base_capital_yen: Optional[float] = None,
    max_leverage_multiplier: Optional[object] = None,
) -> Optional[str]:
    if base_capital_yen is None or max_leverage_multiplier is None:
        return None
    raw = json.dumps(
        policy_instance_document(base_capital_yen, max_leverage_multiplier),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def paper_effective_g(requested_gross_yen: Optional[int]) -> Optional[int]:
    """Paper uses the derived request. Broker buying power is not consulted."""
    if requested_gross_yen is None:
        return None
    return canonical_gross_budget_yen(requested_gross_yen)


def live_session_effective_g(
    requested_gross_yen: Optional[int],
    broker_available_buying_power_yen: Optional[int],
) -> dict[str, Any]:
    """Future live cap. Missing broker buying power fails closed. Not wired now."""
    if requested_gross_yen is None or broker_available_buying_power_yen is None:
        return {"status": BROKER_BUYING_POWER_UNAVAILABLE, "SESSION_EFFECTIVE_G": None}
    requested = canonical_gross_budget_yen(requested_gross_yen)
    power = canonical_gross_budget_yen(broker_available_buying_power_yen)
    return {"status": "LIVE_CAPPED", "SESSION_EFFECTIVE_G": min(requested, power)}


def one_lot_notional(price: float) -> float:
    return round(float(price) * QTY, 2)


def budget_share(price: float, gross_budget_yen: float) -> float:
    if gross_budget_yen <= 0:
        raise ValueError("G must be positive")
    return one_lot_notional(price) / float(gross_budget_yen)


def preselect_status(prior_close: float, gross_budget_yen: Optional[float]) -> str:
    if gross_budget_yen is None:
        return AWAIT_USER_G
    if one_lot_notional(prior_close) <= float(gross_budget_yen):
        return "ELIGIBLE"
    return BUDGET_INELIGIBLE_PRESELECT


def _book(open_positions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in open_positions]


def open_gross(open_positions: Sequence[Mapping[str, Any]]) -> float:
    return round(sum(float(row["entry_notional"]) for row in open_positions), 2)


def admit_candidate(
    *,
    symbol: str,
    ask: float,
    open_positions: Sequence[Mapping[str, Any]],
    gross_budget_yen: Optional[float],
    same_symbol_blocked: bool = False,
    reentry_blocked: bool = False,
    fill_gate_pass: bool = True,
) -> dict[str, Any]:
    """Causal admission. Does not resize quantity and does not reorder signals."""
    book = _book(open_positions)
    if gross_budget_yen is None:
        return {"admit": False, "reason": AWAIT_USER_G, "qty": QTY, "open_positions": book}
    candidate = one_lot_notional(ask)
    gross = open_gross(book)
    if candidate <= 0:
        reason = "INVALID_NOTIONAL"
    elif candidate > float(gross_budget_yen):
        reason = BUDGET_INELIGIBLE_AT_ENTRY
    elif gross + candidate > float(gross_budget_yen):
        reason = NOTIONAL_AGGREGATE_CAP
    elif len(book) >= MAX_CONCURRENT:
        reason = "MAX_CONCURRENT"
    elif same_symbol_blocked or any(str(row["symbol"]) == str(symbol) for row in book):
        reason = "SAME_SYMBOL"
    elif reentry_blocked:
        reason = "REENTRY"
    elif not fill_gate_pass:
        reason = "FILL_GATE"
    else:
        book.append({"symbol": str(symbol), "entry_notional": candidate, "qty": QTY})
        return {
            "admit": True,
            "reason": ADMIT,
            "qty": QTY,
            "candidate_notional": candidate,
            "open_gross": open_gross(book),
            "open_positions": book,
        }
    return {
        "admit": False,
        "reason": reason,
        "qty": QTY,
        "candidate_notional": candidate,
        "open_gross": gross,
        "open_positions": book,
    }


def release_position(open_positions: Sequence[Mapping[str, Any]], symbol: str) -> list[dict[str, Any]]:
    """Release that position's entry notional. No future-signal reservation."""
    kept: list[dict[str, Any]] = []
    released = False
    for row in open_positions:
        if not released and str(row["symbol"]) == str(symbol):
            released = True
            continue
        kept.append(dict(row))
    return kept


def admit_in_given_order(
    signals: Sequence[Mapping[str, Any]],
    *,
    gross_budget_yen: Optional[float],
) -> list[dict[str, Any]]:
    """Keep the supplied Frozen X1 order. Capital efficiency does not reorder."""
    book: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for signal in signals:
        if signal.get("event") == "EXIT":
            book = release_position(book, str(signal["symbol"]))
            decisions.append({"event": "EXIT", "symbol": str(signal["symbol"]), "open_gross": open_gross(book)})
            continue
        decision = admit_candidate(
            symbol=str(signal["symbol"]),
            ask=float(signal["ask"]),
            open_positions=book,
            gross_budget_yen=gross_budget_yen,
            same_symbol_blocked=bool(signal.get("same_symbol_blocked")),
            reentry_blocked=bool(signal.get("reentry_blocked")),
            fill_gate_pass=bool(signal.get("fill_gate_pass", True)),
        )
        book = list(decision["open_positions"])
        decisions.append({"event": "ENTRY", "symbol": str(signal["symbol"]), **decision})
    return decisions
