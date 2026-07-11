"""Phase687W5A — Official kabusapi sendorder contract helpers.

Source-of-truth priority:
  1. Official kabu Station API reference
  2. docs/live_trading/vendor/kabusapi_sendorder_contract.json
  3. live_order_api_wiring.py
  4. OrderRequestBuilder
  5. test fixtures

No network write APIs.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

# Official exchange codes (stock sendorder)
EXCHANGE_TSE = 1
EXCHANGE_SOR = 9
EXCHANGE_TSE_PLUS = 27

FUND_TYPE_MARGIN = "11"
FUND_TYPE_CASH_SELL_SPACES = "  "

CONTRACT_SNAPSHOT_REL = Path("docs/live_trading/vendor/kabusapi_sendorder_contract.json")
CONTRACT_VERSION = "687W5A.1"


class ExchangePolicy(str, Enum):
    NOT_SELECTED = "NOT_SELECTED"
    SOR = "SOR"
    TSE_PLUS = "TSE_PLUS"
    TSE_MAINTENANCE_EXCEPTION = "TSE_MAINTENANCE_EXCEPTION"
    REPAY_MATCH_OPEN_POSITION_EXCHANGE = "REPAY_MATCH_OPEN_POSITION_EXCHANGE"
    PRODUCTION_FORBIDDEN = "PRODUCTION_FORBIDDEN"


class TransactionType(str, Enum):
    CASH_BUY = "CASH_BUY"
    CASH_SELL = "CASH_SELL"
    MARGIN_NEW_BUY = "MARGIN_NEW_BUY"
    MARGIN_REPAY_SELL = "MARGIN_REPAY_SELL"


class FundTypeMode(str, Enum):
    OMIT_AUTO_11 = "OMIT_AUTO_11"  # intentional omission for margin
    EXPLICIT_11 = "EXPLICIT_11"
    REQUIRED_CASH = "REQUIRED_CASH"  # cash path — not implemented
    INVALID = "INVALID"


class ClosePositionMode(str, Enum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    CLOSE_POSITIONS = "CLOSE_POSITIONS"
    CLOSE_POSITION_ORDER = "CLOSE_POSITION_ORDER"
    BOTH_FORBIDDEN = "BOTH_FORBIDDEN"
    NEITHER = "NEITHER"


# ClosePositionOrder=0 official meaning (not an arbitrary TradeBot invention)
CLOSE_POSITION_ORDER_MEANINGS = {
    0: "date_asc_pnl_desc",
    1: "date_asc_pnl_asc",
    2: "date_desc_pnl_desc",
    3: "date_desc_pnl_asc",
    4: "pnl_desc_date_asc",
    5: "pnl_desc_date_desc",
    6: "pnl_asc_date_asc",
    7: "pnl_asc_date_desc",
}

SUPPORTED_FRONT_ORDER = {10: "MARKET", 20: "LIMIT"}
NOT_IMPLEMENTED_FRONT_ORDER = {
    13, 14, 15, 16, 17, 21, 22, 23, 24, 25, 26, 27, 30
}

TRANSACTION_STATUS = {
    TransactionType.CASH_BUY.value: "NOT_IMPLEMENTED",
    TransactionType.CASH_SELL.value: "NOT_IMPLEMENTED",
    TransactionType.MARGIN_NEW_BUY.value: "IMPLEMENTED_DRYRUN",
    TransactionType.MARGIN_REPAY_SELL.value: "IMPLEMENTED_DRYRUN",
}


def load_official_contract(repo_root: Optional[Path] = None) -> dict[str, Any]:
    root = repo_root or Path(__file__).resolve().parents[2]
    path = root / CONTRACT_SNAPSHOT_REL
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_exchange_code(
    *,
    exchange_policy: str,
    entry_or_exit: str,
    open_position_exchange: Optional[int] = None,
) -> tuple[Optional[int], list[str], str]:
    """Return (exchange_code|None, errors, final_state_hint)."""
    errs: list[str] = []
    pol = str(exchange_policy or ExchangePolicy.NOT_SELECTED.value)

    if pol == ExchangePolicy.PRODUCTION_FORBIDDEN.value:
        return None, ["exchange_policy_PRODUCTION_FORBIDDEN"], "PRECHECK_REJECTED"
    if pol == ExchangePolicy.NOT_SELECTED.value:
        if entry_or_exit == "ENTRY":
            return None, ["exchange_policy_NOT_SELECTED"], "ORDER_INTENT_CREATED"
        return None, ["exchange_policy_NOT_SELECTED"], "RECOVERY_REQUIRED"

    if entry_or_exit == "ENTRY":
        if pol == ExchangePolicy.SOR.value:
            return EXCHANGE_SOR, [], ""
        if pol == ExchangePolicy.TSE_PLUS.value:
            return EXCHANGE_TSE_PLUS, [], ""
        if pol == ExchangePolicy.TSE_MAINTENANCE_EXCEPTION.value:
            return EXCHANGE_TSE, [], ""
        if pol == ExchangePolicy.REPAY_MATCH_OPEN_POSITION_EXCHANGE.value:
            return None, ["exchange_policy_repay_only_on_entry"], "PRECHECK_REJECTED"
        return None, [f"unknown_exchange_policy:{pol}"], "PRECHECK_REJECTED"

    # EXIT / repay
    if pol == ExchangePolicy.REPAY_MATCH_OPEN_POSITION_EXCHANGE.value:
        if open_position_exchange is None:
            return None, ["open_position_exchange_unknown"], "RECOVERY_REQUIRED"
        if int(open_position_exchange) not in (EXCHANGE_TSE, EXCHANGE_SOR, EXCHANGE_TSE_PLUS):
            return None, ["open_position_exchange_unsupported"], "RECOVERY_REQUIRED"
        return int(open_position_exchange), [], ""

    # Do not silently remap repay to SOR/TSE+
    if pol in (ExchangePolicy.SOR.value, ExchangePolicy.TSE_PLUS.value, ExchangePolicy.TSE_MAINTENANCE_EXCEPTION.value):
        return None, ["repay_must_use_REPAY_MATCH_OPEN_POSITION_EXCHANGE"], "RECOVERY_REQUIRED"

    return None, [f"unknown_exchange_policy:{pol}"], "RECOVERY_REQUIRED"


def validate_new_order_exchange(exchange: int, exchange_policy: str) -> list[str]:
    errs: list[str] = []
    if exchange == EXCHANGE_TSE and exchange_policy != ExchangePolicy.TSE_MAINTENANCE_EXCEPTION.value:
        errs.append("normal_new_order_exchange_tse_forbidden")
    if exchange not in (EXCHANGE_TSE, EXCHANGE_SOR, EXCHANGE_TSE_PLUS):
        errs.append("unknown_exchange")
    return errs


def validate_close_position_xor(
    api: dict[str, Any], *, required: bool
) -> list[str]:
    has_pos = "ClosePositions" in api
    has_ord = "ClosePositionOrder" in api
    errs: list[str] = []
    if has_pos and has_ord:
        errs.append("close_position_both_specified")
    if required and not has_pos and not has_ord:
        errs.append("close_position_neither_specified")
    if has_ord:
        try:
            v = int(api["ClosePositionOrder"])
            if v not in CLOSE_POSITION_ORDER_MEANINGS:
                errs.append("unknown_ClosePositionOrder")
        except (TypeError, ValueError):
            errs.append("invalid_ClosePositionOrder")
    return errs


@dataclass(frozen=True)
class FundTypeAudit:
    mode: str
    fund_type_in_payload: Optional[str]
    intentional_omission: bool
    auto_11_assumed: bool
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def apply_fund_type_for_margin(
    api: dict[str, Any], *, mode: str
) -> tuple[dict[str, Any], FundTypeAudit, list[str]]:
    """Margin new/repay: omit or explicit 11. Never invent other values."""
    out = dict(api)
    errs: list[str] = []
    if mode == FundTypeMode.OMIT_AUTO_11.value:
        out.pop("FundType", None)
        audit = FundTypeAudit(
            mode=mode,
            fund_type_in_payload=None,
            intentional_omission=True,
            auto_11_assumed=True,
            notes="Official: omit FundType for margin → auto 11",
        )
        return out, audit, errs
    if mode == FundTypeMode.EXPLICIT_11.value:
        out["FundType"] = FUND_TYPE_MARGIN
        audit = FundTypeAudit(
            mode=mode,
            fund_type_in_payload=FUND_TYPE_MARGIN,
            intentional_omission=False,
            auto_11_assumed=False,
            notes="Explicit FundType=11 for margin",
        )
        return out, audit, errs
    errs.append("fund_type_mode_invalid_for_margin")
    return out, FundTypeAudit(mode=mode, fund_type_in_payload=None, intentional_omission=False, auto_11_assumed=False), errs


def exchange_policy_matrix() -> list[dict[str, Any]]:
    rows = []
    for pol in ExchangePolicy:
        for phase in ("ENTRY", "EXIT"):
            code, errs, hint = resolve_exchange_code(
                exchange_policy=pol.value,
                entry_or_exit=phase,
                open_position_exchange=EXCHANGE_TSE if phase == "EXIT" else None,
            )
            rows.append(
                {
                    "exchange_policy": pol.value,
                    "entry_or_exit": phase,
                    "resolved_exchange": code,
                    "errors": ";".join(errs),
                    "state_hint": hint,
                    "production_authorized": False,
                }
            )
    return rows


def transaction_type_matrix() -> list[dict[str, Any]]:
    rows = []
    for tt in TransactionType:
        rows.append(
            {
                "transaction_type": tt.value,
                "status": TRANSACTION_STATUS[tt.value],
                "request_valid_for_submit": False,
                "tradebot_primary": tt.value in (
                    TransactionType.MARGIN_NEW_BUY.value,
                    TransactionType.MARGIN_REPAY_SELL.value,
                ),
            }
        )
    return rows
