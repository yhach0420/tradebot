"""Position sizing families. Same causal formula on every symbol. Not a price filter."""
from __future__ import annotations

from typing import Any

from research.pb1_complete_strategy_causal_repair_mechanism_discovery import EQUAL_NOTIONAL_YEN, LOT, MAX_NOTIONAL_YEN
from research.pb1_v4_complete_strategy_build_and_economic_validation import SHARES


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f and f > 0


def s0_shares(_trade: dict[str, Any], **_kw: Any) -> float:
    return float(SHARES)


def s1_shares(trade: dict[str, Any], **_kw: Any) -> float:
    px = float(trade.get("entry_px") or 0)
    if not _finite(px):
        return float(SHARES)
    return float(EQUAL_NOTIONAL_YEN) / float(px)


def s2_shares(trade: dict[str, Any], *, atr_ref: float, atr: float | None, **_kw: Any) -> float:
    if not _finite(atr) or not _finite(atr_ref) or float(atr) <= 0:
        return float(SHARES)
    return float(SHARES) * (float(atr_ref) / float(atr))


def s3_shares(trade: dict[str, Any], **_kw: Any) -> float:
    """Equal-notional base, then ex-ante max notional. Never drops the symbol."""
    px = float(trade.get("entry_px") or 0)
    if not _finite(px):
        return s1_shares(trade)
    shares = s1_shares(trade)
    cap = float(MAX_NOTIONAL_YEN) / float(px)
    return float(min(shares, cap))


FORMULAS = {
    "S0_FIXED_100": s0_shares,
    "S1_EQUAL_NOTIONAL": s1_shares,
    "S2_ATR_RISK": s2_shares,
    "S3_EQUAL_NOTIONAL_MAX_NOTIONAL": s3_shares,
}


def apply_sizing(trade: dict[str, Any], *, family: str, atr_ref: float, atr: float | None) -> dict[str, Any]:
    shares = float(FORMULAS[family](trade, atr_ref=atr_ref, atr=atr))
    scale = shares / float(SHARES) if float(SHARES) else 0.0
    px = float(trade.get("entry_px") or 0)
    notional = shares * px
    v1_net = float(trade.get("net_pnl_yen") or 0)
    v1_gross = float(trade.get("gross_pnl_yen") or 0)
    return {
        "family": family,
        "shares": shares,
        "lot_rounded_shares": float(LOT) * round(shares / float(LOT)) if shares > 0 else 0.0,
        "notional_yen": float(notional),
        "net_yen": float(v1_net) * float(scale),
        "gross_yen": float(v1_gross) * float(scale),
        "net_bps": (10000.0 * v1_net / (float(SHARES) * px)) if _finite(px) else None,
        "atr": atr,
        "atr_ref": atr_ref,
    }
