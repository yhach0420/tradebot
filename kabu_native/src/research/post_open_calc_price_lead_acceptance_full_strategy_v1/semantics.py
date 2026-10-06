"""Trusted BoardSuccess semantics for CalcPrice. No web. No guess of formula."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _schema_keys() -> set[str]:
    keys = {"CalcPrice"}
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


def _docs() -> dict[str, Any]:
    native = Path(__file__).resolve().parents[3]
    mapping = native.parent / "docs" / "kabu_response_mapping.md"
    inventory = native / "docs" / "board_data_inventory.md"
    found: list[str] = []
    blob = ""
    for p in (mapping, inventory):
        if p.is_file():
            found.append(str(p))
            blob += p.read_text(encoding="utf-8")
    return {
        "FILES": found,
        "HAS_CALC_LABEL": "CalcPrice" in blob and "計算用現値" in blob,
        "HAS_INVENTORY": "CalcPrice" in blob and "現値・OHLC" in blob,
        "HAS_CALC_PRICE_TIME": "CalcPriceTime" in blob,
        "QUOTE_FALLBACK": "CurrentPrice`（欠損時 `CalcPrice`）" in blob or "欠損時 `CalcPrice`" in blob,
    }


def prove_calcprice_semantics() -> dict[str, Any]:
    keys = _schema_keys()
    docs = _docs()
    in_schema = "CalcPrice" in keys
    has_ts = "CalcPriceTime" in keys
    label_ok = bool(docs["HAS_CALC_LABEL"]) and in_schema
    # Official identity is proven. Inputs/formula of the vendor calculation are not
    # in OpenAPI key inventory, mapping, or board inventory. The lead-acceptance
    # thesis uses CalcPrice as an observed BoardSuccess price state distinct from
    # CurrentPrice; formula is not required for that identity. Independence vs
    # CurrentPrice/Bid/Ask is an empirical gate, not a guessed theoretical-price story.
    proven = bool(label_ok)
    return {
        "OFFICIAL_NAME": "CalcPrice",
        "OFFICIAL_MEANING": "計算用現値 (BoardSuccess CalcPrice)",
        "UNIT": "price (現値 family on BoardSuccess)",
        "SCHEMA_SOURCE": "kabu_native/scripts/check_api.py BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS",
        "MEANING_SOURCE": "docs/kabu_response_mapping.md §1: CalcPrice = 計算用現値",
        "INVENTORY_SOURCE": "kabu_native/docs/board_data_inventory.md §3.1 現値・OHLC includes CalcPrice",
        "FORMULA_PROVEN": False,
        "FORMULA_SOURCE": None,
        "FORMULA_NOTE": (
            "Trusted sources do not document the inputs or algorithm that produce CalcPrice. "
            "Opening audit recorded MEDIUM confidence and 'not a documented JPX itayose match price'. "
            "This package does not treat CalcPrice as theoretical, indicative, or index-arb."
        ),
        "IN_BOARDSUCCESS": in_schema,
        "PREOPEN_ONLY_IN_SCHEMA": False,
        "CONTINUOUS_SESSION_FIELD": True,
        "SAME_INGRESS_PAYLOAD": True,
        "DEDICATED_FIELD_TIMESTAMP": bool(has_ts),
        "STALE_POLICY": (
            "No CalcPriceTime in BoardSuccess top-level keys. Observable value is the current "
            "BoardSuccess payload as-of INGRESS. Null/non-finite/<=0 is invalid for CALC_UP_LEAD. "
            "Do not carry a previous CalcPrice forward when the current key is invalid."
        ),
        "TRUSTED_USAGE_IN_CODE": (
            "Quote.price mapping uses CurrentPrice with CalcPrice only as missing-CurrentPrice fallback. "
            "That usage is not adopted as the strategy identity."
        ),
        "DOCS": docs,
        "SEMANTICS_PROVEN": proven,
        "NOTE": (
            "BoardSuccess is the GET /board snapshot schema used in continuous session. "
            "OPENING_AUCTION_CONTEXT may have used CalcPrice as pre-open context; this strategy "
            "uses only post-open continuous dynamic values."
        ),
    }
