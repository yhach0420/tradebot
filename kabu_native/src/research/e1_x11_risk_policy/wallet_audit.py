"""Wallet / capital field audit — read-only; no orders."""
from __future__ import annotations

from typing import Any


def wallet_field_inventory() -> list[dict[str, Any]]:
    """Document runtime wallet fields without calling order APIs."""
    return [
        {
            "field_name": "StockAccountWallet",
            "runtime_source": "live_capital_manager.py / live_order_safety_sm.KabuBrokerAdapter",
            "api_source": "GET /wallet/cash → StockAccountWallet (fallback Cash)",
            "definition": "Broker cash/stock wallet as returned",
            "includes_leverage": False,
            "includes_unrealized_pnl": "UNKNOWN_API_OPAQUE",
            "intraday_mutable": True,
            "read_only_safe": True,
            "available_in_Paper": "capital_check_logging_only",
            "available_in_Live": True,
            "risk_capital_candidate": True,
            "note": "safest cash-like base; no local ×2",
        },
        {
            "field_name": "MarginAccountWallet",
            "runtime_source": "live_capital_manager.py / KabuBrokerAdapter",
            "api_source": "GET /wallet/margin → MarginAccountWallet (fallback MarginAmount)",
            "definition": "Broker margin available / margin wallet",
            "includes_leverage": "POSSIBLY_ALREADY_IN_API",
            "includes_unrealized_pnl": "UNKNOWN_API_OPAQUE",
            "intraday_mutable": True,
            "read_only_safe": True,
            "available_in_Paper": "capital_check_logging_only",
            "available_in_Live": True,
            "risk_capital_candidate": "CONDITIONAL",
            "note": "order headroom; do not ×2 again",
        },
        {
            "field_name": "current_equity",
            "runtime_source": "live_capital_manager.LiveCapitalSnapshot",
            "api_source": "derived",
            "definition": "stock_wallet + margin_wallet",
            "includes_leverage": False,
            "includes_unrealized_pnl": "ONLY_IF_WALLETS_EMBED",
            "intraday_mutable": True,
            "read_only_safe": True,
            "available_in_Paper": "logging",
            "available_in_Live": "capital_manager_path",
            "risk_capital_candidate": False,
            "note": "inconsistent with Live SM _equity; easy to misuse",
        },
        {
            "field_name": "buying_power_capital_manager",
            "runtime_source": "live_capital_manager.compute_buying_power",
            "api_source": "derived wallets + /positions",
            "definition": "max(0, equity * MARGIN_LEVERAGE(2.0) - gross)",
            "includes_leverage": True,
            "includes_unrealized_pnl": "GROSS_USES_MARK",
            "intraday_mutable": True,
            "read_only_safe": True,
            "available_in_Paper": "logging",
            "available_in_Live": "capital_manager_only",
            "risk_capital_candidate": False,
            "note": "MUST NOT use as risk_capital_base without proof — includes leverage",
        },
        {
            "field_name": "buying_power_live_sm",
            "runtime_source": "live_order_safety_sm.KabuBrokerAdapter.get_buying_power",
            "api_source": "raw wallets",
            "definition": "margin_w if margin_w > 0 else stock_w",
            "includes_leverage": False,
            "includes_unrealized_pnl": False,
            "intraday_mutable": True,
            "read_only_safe": True,
            "available_in_Paper": False,
            "available_in_Live": True,
            "risk_capital_candidate": "AMBIGUOUS_MARGIN_VS_CASH",
            "note": "primary Live BP; not proven as deposited equity",
        },
        {
            "field_name": "net_liquidation_value",
            "runtime_source": "NOT_FOUND",
            "api_source": "NOT_FOUND",
            "definition": None,
            "includes_leverage": None,
            "includes_unrealized_pnl": None,
            "intraday_mutable": None,
            "read_only_safe": None,
            "available_in_Paper": False,
            "available_in_Live": False,
            "risk_capital_candidate": False,
        },
        {
            "field_name": "available_order_amount",
            "runtime_source": "NOT_FOUND",
            "api_source": "NOT_FOUND",
            "definition": None,
            "includes_leverage": None,
            "includes_unrealized_pnl": None,
            "intraday_mutable": None,
            "read_only_safe": None,
            "available_in_Paper": False,
            "available_in_Live": False,
            "risk_capital_candidate": False,
        },
        {
            "field_name": "configured_risk_capital_cap_yen",
            "runtime_source": "YAML",
            "api_source": "NONE",
            "definition": "user-configured risk capital cap",
            "includes_leverage": False,
            "includes_unrealized_pnl": False,
            "intraday_mutable": False,
            "read_only_safe": True,
            "available_in_Paper": False,
            "available_in_Live": False,
            "risk_capital_candidate": False,
            "note": "KEY_ABSENT — do not invent",
        },
        {
            "field_name": "available_trading_capital_yen",
            "runtime_source": "YAML",
            "api_source": "NONE",
            "definition": "fixed trading capital yen",
            "includes_leverage": False,
            "includes_unrealized_pnl": False,
            "intraday_mutable": False,
            "read_only_safe": True,
            "available_in_Paper": False,
            "available_in_Live": False,
            "risk_capital_candidate": False,
            "note": "KEY_ABSENT — do not invent",
        },
    ]


def resolve_capital_base() -> dict[str, Any]:
    """Section 7 priority: A configured → B safe field candidate → C unresolved."""
    fields = wallet_field_inventory()
    configured_cap = None  # absent
    if configured_cap is not None:
        return {
            "status": "RESOLVED",
            "path": "A_USER_CONFIGURED",
            "risk_capital_base_yen": configured_cap,
            "source": "configured_risk_capital_cap_yen",
        }
    # B: candidate exists but not configured — save candidate, do not change settings
    cash_candidate = next(f for f in fields if f["field_name"] == "StockAccountWallet")
    return {
        "status": "UNRESOLVED",
        "path": "C_BUYING_POWER_OR_UNCONFIGURED",
        "risk_capital_base_yen": None,
        "verdict_hint": "E1_X11_SAFE_CAPITAL_BASE_UNRESOLVED",
        "risk_capital_base_source_candidate": {
            "field": "StockAccountWallet",
            "reason": "safest cash-like read-only wallet field; no local leverage multiply",
            "includes_leverage": False,
            "configured": False,
            "note": "Policy candidate only — YAML not changed; live value intraday mutable",
        },
        "rejected_as_base": [
            "buying_power_capital_manager (includes MARGIN_LEVERAGE ×2)",
            "buying_power_live_sm (ambiguous margin vs cash; not proven equity)",
            "MarginAccountWallet (possibly leveraged already; CONDITIONAL)",
        ],
        "buying_power_not_used_as_equity": True,
        "wallet_fields": fields,
    }


def special_quote_audit() -> dict[str, Any]:
    return {
        "status": "NOT_AVAILABLE_IN_CAPTURE",
        "runtime_push_expected_fields": "SpecialQuote absent from EXPECTED_PUSH_FIELDS_STOCK",
        "board_openapi_schema": "SpecialQuote absent from BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS",
        "capture_stripping": False,
        "capture_note": "writer stores original_payload; field not observed",
        "api_presence": "NOT_MODELED_IN_REPO / NOT_OBSERVED",
        "dynamic_guard": "DYNAMIC_SPECIAL_QUOTE_GUARD_NOT_READY",
        "invented_implementation": False,
    }
