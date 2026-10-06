"""Paper / exchange operability rules. Same rule for every candidate. No specials."""
from __future__ import annotations

from typing import Any

from research.fixed_daytrade_universe_v1 import (
    DEFAULT_PAPER_EQUITY_YEN,
    MARGIN_LEVERAGE,
    MAX_POSITION_RATIO,
    PAPER_ACCOUNTING,
    PAPER_LOT_QTY,
    PAPER_MIN_ENTRY_PRICE,
)
from research.fixed_universe_historical_foundation_v1.universe import EXCLUDED_LOT_SIZE_BLOCKERS

HIGH_PRICE_CANDIDATES_MUST_USE_SAME_RULE = (
    "6857",
    "8035",
    "6920",
    "6367",
    "4063",
    "4519",
    "7974",
    "9983",
    "6861",
)


STATUS_RESEARCH_MEMBER = "FIXED_UNIVERSE_MEMBER"
STATUS_STRUCT = "STRUCTURALLY_INELIGIBLE"
PAPER_ELIGIBILITY_UNKNOWN = "PAPER_ELIGIBILITY_UNKNOWN"
PAPER_ELIGIBILITY_TRADE_ELIGIBLE = "FIXED_MEMBER_TRADE_ELIGIBLE"
PAPER_ELIGIBILITY_TEMP = "FIXED_MEMBER_TEMPORARILY_NOT_TRADE_ELIGIBLE"
PAPER_ELIGIBILITY_INCOMPATIBLE = "PAPER_NOT_ELIGIBLE"

# Back-compat aliases (paper statuses are not research membership)
STATUS_ELIGIBLE = PAPER_ELIGIBILITY_TRADE_ELIGIBLE
STATUS_TEMP = PAPER_ELIGIBILITY_TEMP


def paper_rules() -> dict[str, Any]:
    max_notional = float(DEFAULT_PAPER_EQUITY_YEN) * float(MAX_POSITION_RATIO)
    max_price_if_live_capital_100 = max_notional / float(PAPER_LOT_QTY) if PAPER_LOT_QTY else None
    return {
        "paper_accounting": PAPER_ACCOUNTING,
        "paper_lot_qty": int(PAPER_LOT_QTY),
        "this_is_not_exchange_trading_unit": True,
        "exchange_trading_unit_source": "official_listed_issue_master_when_available",
        "margin_leverage": float(MARGIN_LEVERAGE),
        "max_position_ratio": float(MAX_POSITION_RATIO),
        "default_paper_equity_yen": float(DEFAULT_PAPER_EQUITY_YEN),
        "live_capital_max_notional_yen": max_notional,
        "live_capital_max_price_if_qty_100": max_price_if_live_capital_100,
        "paper_min_entry_price": float(PAPER_MIN_ENTRY_PRICE),
        "current_paper_has_no_max_price_universe_filter": True,
        "do_not_mix_trading_unit_with_100_share_notional": True,
        "same_rule_all_candidates": True,
        "no_symbol_special_case": True,
        "high_price_names_same_rule": list(HIGH_PRICE_CANDIDATES_MUST_USE_SAME_RULE),
        "class_a_structurally_ineligible": (
            "not_common_stock_or_not_listed_or_listing_discontinuity; "
            "trading_unit_is_paper_gate_not_research_blocker"
        ),
        "class_b_temporarily_not_trade_eligible": (
            "paper_gate_only: unit_times_price_times_qty_exceeds_live_capital_gate; "
            "name remains FIXED_UNIVERSE_MEMBER if research gates pass"
        ),
        "complexity_choice": (
            "Research membership uses 60d Va/Vo, session coverage, listing continuity, "
            "common-stock/listing status, and data usability. "
            "Paper eligibility uses trading unit, 100-share notional, capital/risk, "
            "and execution compatibility. J-Quants official master has no trading-unit field; "
            "that absence is not a research freeze blocker. Unknown unit → PAPER_ELIGIBILITY_UNKNOWN. "
            "A separate trading-unit / 100-share audit of all Fixed Universe members is required before Paper."
        ),
        "operability_rule_defined": True,
        "operability_audit_complete": False,
        "applied_this_run": False,
        "reason_not_applied": "paper_eligibility_audit_not_complete_without_trading_unit_and_close",
        "high_100_share_notional_does_not_auto_exclude": True,
        "trading_unit_not_in_jquants_master": True,
        "trading_unit_missing_is_not_research_blocker": True,
        "paper_eligibility_separated_from_research_membership": True,
        "paper_preflight_required": True,
        "paper_preflight": (
            "all_fixed_universe_members_trading_unit_and_100_share_operability_audit_before_paper"
        ),
    }


def evaluate_operability(
    *,
    trading_unit: int | None,
    price: float | None,
) -> dict[str, Any]:
    rules = paper_rules()
    qty = int(PAPER_LOT_QTY)
    live_max = float(rules["live_capital_max_notional_yen"])
    if price is None or price <= 0:
        return {
            "evaluated": False,
            "structurally_ineligible": False,
            "paper_incompatible": False,
            "paper_eligibility": PAPER_ELIGIBILITY_UNKNOWN,
            "temporarily_not_trade_eligible": None,
            "paper_yen_100_compatible": None,
            "share_100_notional": None,
            "reason": "missing_price",
            "same_rule": True,
            "trading_unit_known": trading_unit is not None,
        }
    px = float(price)
    notional_100 = px * qty
    temp = notional_100 > live_max
    yen_100_ok = px >= float(PAPER_MIN_ENTRY_PRICE)
    if trading_unit is None or int(trading_unit) <= 0:
        return {
            "evaluated": True,
            "structurally_ineligible": False,
            "paper_incompatible": False,
            "paper_eligibility": PAPER_ELIGIBILITY_UNKNOWN,
            "temporarily_not_trade_eligible": None,
            "paper_yen_100_compatible": bool(yen_100_ok),
            "share_100_notional": notional_100,
            "notional_paper_qty_yen": notional_100,
            "trading_unit": None,
            "trading_unit_known": False,
            "price": px,
            "same_rule": True,
            "reason": "trading_unit_unknown_not_guessed",
        }
    unit = int(trading_unit)
    if qty % unit != 0:
        return {
            "evaluated": True,
            "structurally_ineligible": False,
            "paper_incompatible": True,
            "paper_eligibility": PAPER_ELIGIBILITY_INCOMPATIBLE,
            "temporarily_not_trade_eligible": False,
            "paper_yen_100_compatible": False,
            "share_100_notional": notional_100,
            "notional_paper_qty_yen": notional_100,
            "reason": "paper_qty_not_multiple_of_trading_unit",
            "trading_unit": unit,
            "trading_unit_known": True,
            "price": px,
            "same_rule": True,
        }
    return {
        "evaluated": True,
        "structurally_ineligible": False,
        "paper_incompatible": False,
        "paper_eligibility": PAPER_ELIGIBILITY_TEMP if temp else PAPER_ELIGIBILITY_TRADE_ELIGIBLE,
        "temporarily_not_trade_eligible": bool(temp),
        "paper_yen_100_compatible": bool(yen_100_ok),
        "share_100_notional": notional_100,
        "notional_paper_qty_yen": notional_100,
        "trading_unit": unit,
        "trading_unit_known": True,
        "price": px,
        "same_rule": True,
        "reason": "temp_capital_gate" if temp else "operable",
    }


def membership_status(
    *,
    listing_ok: bool,
    common_stock: bool,
    liquidity_ok: bool,
    operability: dict[str, Any],
    data_usable: bool = True,
) -> str | None:
    """Research membership only. Trading unit / notional / capital are Paper eligibility."""
    _ = operability
    if (not listing_ok) or (not common_stock):
        return STATUS_STRUCT
    if (not liquidity_ok) or (not data_usable):
        return None
    return STATUS_RESEARCH_MEMBER


def paper_eligibility_status(operability: dict[str, Any]) -> str:
    got = operability.get("paper_eligibility")
    if got:
        return str(got)
    if not operability.get("trading_unit_known") or operability.get("share_100_notional") is None:
        return PAPER_ELIGIBILITY_UNKNOWN
    if operability.get("paper_incompatible"):
        return PAPER_ELIGIBILITY_INCOMPATIBLE
    if operability.get("temporarily_not_trade_eligible"):
        return PAPER_ELIGIBILITY_TEMP
    return PAPER_ELIGIBILITY_TRADE_ELIGIBLE


def phase0_blocker_notes() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for code, name, old_reason in EXCLUDED_LOT_SIZE_BLOCKERS:
        rows.append(
            {
                "symbol": code,
                "name": name,
                "in_phase0_candidate_45": False,
                "phase0_label": "EXCLUDED_LOT_SIZE_BLOCKERS",
                "phase0_reason_raw": old_reason,
                "freeze_reason": (
                    "not_in_starting_pool; this retry does not revive 9983/6861; "
                    "Phase0 mixed trading-unit with 100-share notional; "
                    "universe expansion is a separate post-freeze study"
                ),
                "structurally_ineligible_judged": False,
                "temporarily_not_trade_eligible_judged": False,
                "special_case": False,
            }
        )
    return rows


assert paper_rules()["same_rule_all_candidates"] is True
assert paper_rules()["high_100_share_notional_does_not_auto_exclude"] is True
assert paper_rules()["operability_rule_defined"] is True
assert paper_rules()["operability_audit_complete"] is False
assert paper_rules()["applied_this_run"] is False
assert paper_rules()["trading_unit_missing_is_not_research_blocker"] is True
assert evaluate_operability(trading_unit=100, price=1500.0)["temporarily_not_trade_eligible"] is True
assert evaluate_operability(trading_unit=100, price=1500.0)["structurally_ineligible"] is False
assert evaluate_operability(trading_unit=1000, price=100.0)["structurally_ineligible"] is False
assert evaluate_operability(trading_unit=1000, price=100.0)["paper_incompatible"] is True
assert evaluate_operability(trading_unit=None, price=1500.0)["structurally_ineligible"] is False
assert evaluate_operability(trading_unit=None, price=1500.0)["paper_eligibility"] == PAPER_ELIGIBILITY_UNKNOWN
assert evaluate_operability(trading_unit=None, price=1500.0)["share_100_notional"] == 150000.0
assert membership_status(
    listing_ok=True, common_stock=True, liquidity_ok=True,
    operability=evaluate_operability(trading_unit=None, price=40000.0),
) == STATUS_RESEARCH_MEMBER
