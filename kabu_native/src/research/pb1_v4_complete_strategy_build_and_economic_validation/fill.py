"""Historical fill approximation. Does not invent Bid/Ask. Does not use mid."""
from __future__ import annotations

from typing import Any

from research.cause_first_mechanism_discovery_v1 import X1_TAX_BPS
from research.pb1_v4_complete_strategy_build_and_economic_validation import (
    COST_MODEL_ID,
    HISTORICAL_FILL_ID,
    SHARES,
)


def apply_x1_tax(*, gross_yen: float, entry_px: float) -> dict[str, Any]:
    tax = float(X1_TAX_BPS) / 10_000.0 * float(entry_px) * float(SHARES)
    return {
        "execution_cost_yen": float(tax),
        "net_pnl_yen": float(gross_yen) - float(tax),
        "cost_model": COST_MODEL_ID,
        "X1_TAX_BPS": float(X1_TAX_BPS),
    }


def signed_gross(*, side: str, entry_px: float, exit_px: float) -> float:
    long = str(side).lower() in {"bull", "long", "1"}
    signed = (float(exit_px) - float(entry_px)) if long else (float(entry_px) - float(exit_px))
    return float(signed) * float(SHARES)


def fill_stamp(*, px: float, t: str, side: str, kind: str) -> dict[str, Any]:
    return {
        "fill_model": HISTORICAL_FILL_ID,
        "RESEARCH_EXECUTION_APPROXIMATION": True,
        "used_mid": False,
        "used_future_quote": False,
        "same_bar_fill": False,
        "fill_px": float(px),
        "fill_t": str(t)[:5],
        "side": side,
        "kind": kind,
        "shares": int(SHARES),
        "long_uses_buy_price_proxy": str(side).lower() in {"bull", "long", "1"},
        "short_uses_sell_price_proxy": str(side).lower() in {"bear", "short", "-1"},
        "proxy": "next_1m_open",
    }
