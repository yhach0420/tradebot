"""Canonical board normalizer from kabu original_payload (Buy1/Sell1 SoT).

Research-only. Does not mutate raw fields. Not wired into mainline Stage0.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _level(op: Mapping[str, Any], side: str, i: int = 1) -> tuple[Optional[float], Optional[float]]:
    lv = op.get(f"{side}{i}")
    if not isinstance(lv, Mapping):
        return None, None
    return _f(lv.get("Price")), _f(lv.get("Qty"))


@dataclass(frozen=True)
class CanonicalBoard:
    canonical_best_bid: Optional[float]
    canonical_bid_qty: Optional[float]
    canonical_best_ask: Optional[float]
    canonical_ask_qty: Optional[float]
    canonical_spread: Optional[float]
    canonical_spread_bps: Optional[float]
    canonical_imbalance: Optional[float]
    quote_valid: bool
    quote_reason: str
    locked: bool
    crossed: bool
    kabu_bid_price_raw: Optional[float]
    kabu_ask_price_raw: Optional[float]
    kabu_bid_qty_raw: Optional[float]
    kabu_ask_qty_raw: Optional[float]
    mid: Optional[float]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_kabu_board(payload: Mapping[str, Any] | None) -> CanonicalBoard:
    """Normalize a single atomic payload. No cross-event merge, no forward fill."""
    op = payload if isinstance(payload, Mapping) else {}
    buy_px, buy_qty = _level(op, "Buy", 1)
    sell_px, sell_qty = _level(op, "Sell", 1)
    kabu_bid = _f(op.get("BidPrice"))
    kabu_ask = _f(op.get("AskPrice"))
    kabu_bq = _f(op.get("BidQty"))
    kabu_aq = _f(op.get("AskQty"))

    # Source of Truth: Buy1 / Sell1 only (same payload). No fallback remap into raw names.
    best_bid, bid_qty = buy_px, buy_qty
    best_ask, ask_qty = sell_px, sell_qty

    reason = "OK"
    quote_valid = True
    locked = False
    crossed = False
    spread: Optional[float] = None
    spread_bps: Optional[float] = None
    imb: Optional[float] = None
    mid: Optional[float] = None

    if best_bid is None or best_ask is None:
        quote_valid = False
        reason = "NOT_EVALUABLE_MISSING_SIDE"
    elif bid_qty is None or ask_qty is None:
        quote_valid = False
        reason = "NOT_EVALUABLE_MISSING_QTY"
    else:
        spread = best_ask - best_bid
        mid = (best_ask + best_bid) / 2.0
        if mid and mid > 0:
            spread_bps = abs(spread) / mid * 10000.0
        if abs(spread) < 1e-12:
            locked = True
            reason = "LOCKED"
            # locked is classified separately; still evaluable for width=0
        elif spread < 0:
            crossed = True
            quote_valid = False
            reason = "CROSSED_TRUE_BOOK"
        total_q = bid_qty + ask_qty
        if total_q > 0:
            imb = bid_qty / total_q
        if best_ask < best_bid:
            # already handled
            pass
        elif not locked and not crossed and best_ask < best_bid:
            quote_valid = False
            reason = "ASK_LT_BID"

    return CanonicalBoard(
        canonical_best_bid=best_bid,
        canonical_bid_qty=bid_qty,
        canonical_best_ask=best_ask,
        canonical_ask_qty=ask_qty,
        canonical_spread=spread,
        canonical_spread_bps=spread_bps,
        canonical_imbalance=imb,
        quote_valid=quote_valid and not crossed,
        quote_reason=reason,
        locked=locked,
        crossed=crossed,
        kabu_bid_price_raw=kabu_bid,
        kabu_ask_price_raw=kabu_ask,
        kabu_bid_qty_raw=kabu_bq,
        kabu_ask_qty_raw=kabu_aq,
        mid=mid,
    )


def r0_current_from_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Current mainline interpretation: BidPrice=bid, AskPrice=ask, BidQty as bid."""
    op = payload if isinstance(payload, Mapping) else {}
    bid = _f(op.get("BidPrice"))
    ask = _f(op.get("AskPrice"))
    bq = _f(op.get("BidQty")) or 0.0
    aq = _f(op.get("AskQty")) or 0.0
    # mirror morning_screen.calc_board_imbalance
    bid_sum = bq
    ask_sum = aq
    for i in range(1, 11):
        buy = op.get(f"Buy{i}")
        if isinstance(buy, Mapping):
            bid_sum += _f(buy.get("Qty")) or 0.0
        sell = op.get(f"Sell{i}")
        if isinstance(sell, Mapping):
            ask_sum += _f(sell.get("Qty")) or 0.0
    total = bid_sum + ask_sum
    imb = (bid_sum / total) if total > 0 else None
    top_total = (bq or 0.0) + (aq or 0.0)
    top_imb = (bq / top_total) if top_total > 0 else None
    spread = None
    spread_bps = None
    mid = None
    if bid is not None and ask is not None:
        spread = ask - bid  # labeled; often negative under kabu names
        mid = (ask + bid) / 2.0
        if mid and mid > 0:
            spread_bps = abs(ask - bid) / mid * 10000.0
    return {
        "r0_best_bid_labeled": bid,
        "r0_best_ask_labeled": ask,
        "r0_bid_qty_labeled": bq,
        "r0_ask_qty_labeled": aq,
        "r0_spread_signed": spread,
        "r0_spread_bps_abs": spread_bps,
        "r0_imbalance_depth": imb,
        "r0_imbalance_top": top_imb,
        "r0_mid": mid,
    }


def board_token(imbalance: Optional[float], *, p33: float, p66: float) -> str:
    if imbalance is None:
        return "Board:unknown"
    if imbalance < p33:
        return "Board:low"
    if imbalance < p66:
        return "Board:mid"
    return "Board:high"


def r1_from_canonical(c: CanonicalBoard, *, p33: float, p66: float) -> dict[str, Any]:
    # depth-style canonical imbalance (Buy1..10 / Sell1..10) — caller may pass top-only c.imb
    return {
        "r1_best_bid": c.canonical_best_bid,
        "r1_best_ask": c.canonical_best_ask,
        "r1_bid_qty": c.canonical_bid_qty,
        "r1_ask_qty": c.canonical_ask_qty,
        "r1_spread": c.canonical_spread,
        "r1_spread_bps": c.canonical_spread_bps,
        "r1_imbalance_top": c.canonical_imbalance,
        "r1_board_token": board_token(c.canonical_imbalance, p33=p33, p66=p66),
        "r1_quote_valid": c.quote_valid,
        "r1_quote_reason": c.quote_reason,
        "r1_mid": c.mid,
    }


def canonical_depth_imbalance(payload: Mapping[str, Any] | None) -> Optional[float]:
    """Buy1..Buy10 vs Sell1..Sell10 only (no BidQty/AskQty mixing)."""
    op = payload if isinstance(payload, Mapping) else {}
    bid = 0.0
    ask = 0.0
    for i in range(1, 11):
        buy = op.get(f"Buy{i}")
        if isinstance(buy, Mapping):
            bid += _f(buy.get("Qty")) or 0.0
        sell = op.get(f"Sell{i}")
        if isinstance(sell, Mapping):
            ask += _f(sell.get("Qty")) or 0.0
    total = bid + ask
    if total <= 0:
        return None
    return bid / total
