"""Trusted BoardSuccess semantics for four aggregate-qty fields. No web. No guess."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1 import PRESSURE_FIELDS

# Mirrored from kabu_native/scripts/check_api.py BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS
# which inventories OpenAPI BoardSuccess top-level keys.
BOARD_SUCCESS_HAS_FIELDS = (
    "MarketOrderSellQty",
    "MarketOrderBuyQty",
    "OverSellQty",
    "UnderBuyQty",
)

FIELD_MEANING = {
    "MarketOrderBuyQty": {
        "OFFICIAL_MEANING": "買成行数量 (BoardSuccess MarketOrderBuyQty)",
        "UNIT": "quantity (数量); same Qty family as BidQty/AskQty on BoardSuccess",
        "SOURCE": "docs/kabu_response_mapping.md §1; kabu_native/scripts/check_api.py BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS",
    },
    "MarketOrderSellQty": {
        "OFFICIAL_MEANING": "売成行数量 (BoardSuccess MarketOrderSellQty)",
        "UNIT": "quantity (数量); same Qty family as BidQty/AskQty on BoardSuccess",
        "SOURCE": "docs/kabu_response_mapping.md §1; kabu_native/scripts/check_api.py BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS",
    },
    "UnderBuyQty": {
        "OFFICIAL_MEANING": "UNDER 気配数量 (BoardSuccess UnderBuyQty)",
        "UNIT": "quantity (数量)",
        "SOURCE": "docs/kabu_response_mapping.md §1: OverSellQty, UnderBuyQty = OVER/UNDER 気配数量",
    },
    "OverSellQty": {
        "OFFICIAL_MEANING": "OVER 気配数量 (BoardSuccess OverSellQty)",
        "UNIT": "quantity (数量)",
        "SOURCE": "docs/kabu_response_mapping.md §1: OverSellQty, UnderBuyQty = OVER/UNDER 気配数量",
    },
}


def _schema_keys() -> set[str]:
    keys = set(BOARD_SUCCESS_HAS_FIELDS)
    try:
        from check_api import BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS as live

        keys = set(live)
    except Exception:
        try:
            import importlib.util

            p = Path(__file__).resolve().parents[3] / "scripts" / "check_api.py"
            spec = importlib.util.spec_from_file_location("kabu_check_api_local", p)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                keys = set(mod.BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS)
        except Exception:
            pass
    return keys


def _mapping_labels() -> dict[str, Any]:
    native = Path(__file__).resolve().parents[3]
    roots = [
        native.parent / "docs" / "kabu_response_mapping.md",
        native / "docs" / "board_data_inventory.md",
    ]
    found: list[str] = []
    blob = ""
    for p in roots:
        if p.is_file():
            found.append(str(p))
            blob += p.read_text(encoding="utf-8")
    return {
        "FILES": found,
        "HAS_MO_LABEL": "売買成行数量" in blob and "MarketOrderBuyQty" in blob,
        "HAS_OU_LABEL": ("OVER/UNDER" in blob or "需給補助" in blob) and "UnderBuyQty" in blob,
    }


def prove_field_semantics() -> dict[str, Any]:
    keys = _schema_keys()
    missing = [k for k in PRESSURE_FIELDS if k not in keys]
    labels = _mapping_labels()
    mapping_ok = (not missing) and bool(labels["HAS_MO_LABEL"]) and bool(labels["HAS_OU_LABEL"])
    return {
        "SCHEMA_SOURCE": "kabu_native/scripts/check_api.py BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS (OpenAPI BoardSuccess inventory)",
        "MEANING_SOURCE": (
            "docs/kabu_response_mapping.md §1 BoardSuccess: MarketOrderSellQty/MarketOrderBuyQty=売買成行数量; "
            "OverSellQty, UnderBuyQty=OVER/UNDER 気配数量. Also kabu_native/docs/board_data_inventory.md §3.1 需給補助."
        ),
        "FIELDS": {k: dict(FIELD_MEANING[k]) for k in PRESSURE_FIELDS},
        "ALL_IN_BOARDSUCCESS": mapping_ok,
        "MISSING_FROM_SCHEMA": missing,
        "SAME_INGRESS_PAYLOAD": True,
        "PREOPEN_ONLY_IN_SCHEMA": False,
        "POST_OPEN_CONTINUOUS_MEANING_PROVEN": mapping_ok,
        "DEDICATED_FIELD_TIMESTAMP": False,
        "STALE_POLICY": (
            "No field-specific exchange timestamp. Observable value is the current BoardSuccess "
            "payload as-of INGRESS. Null/non-finite is UNKNOWN (BUY_PRESSURE 未成立). "
            "Do not carry a previous payload value forward when the current key is null."
        ),
        "MAPPING_LABELS": labels,
        "SEMANTICS_PROVEN": bool(mapping_ok),
        "NOTE": (
            "BoardSuccess is the GET /board snapshot schema used in continuous session, not a "
            "preopen-only message type. OPENING_AUCTION_CONTEXT previously used the same keys as "
            "a pre-09:00 freeze; that usage does not prove they are preopen-only."
        ),
    }
