"""Mainline canonical kabu board normalizer (Buy1/Sell1 SoT).

Attaches additive canonical_* fields; never overwrites BidPrice/AskPrice/BidQty/AskQty.
Supports quote_semantic_mode: canonical (default Paper) | legacy (parity / dual-replay).
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any, Mapping, MutableMapping, Optional

_TRUE = frozenset({"1", "true", "TRUE", "yes", "YES", "on", "ON"})
_FALSE = frozenset({"0", "false", "FALSE", "no", "NO", "off", "OFF"})

QUOTE_MODE_ENV = "KABU_QUOTE_SEMANTIC_MODE"
# canonical | legacy
DEFAULT_QUOTE_MODE = "canonical"

BOARD_P33 = 0.437286
BOARD_P66 = 0.527869


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


def resolve_quote_semantic_mode(cfg: Any = None) -> str:
    """Return 'canonical' or 'legacy'."""
    env = os.environ.get(QUOTE_MODE_ENV)
    if env is not None and str(env).strip():
        s = str(env).strip().lower()
        if s in ("canonical", "legacy"):
            return s
    if cfg is not None:
        if isinstance(cfg, Mapping):
            m = cfg.get("quote_semantic_mode")
            if m in ("canonical", "legacy"):
                return str(m)
        else:
            m = getattr(cfg, "quote_semantic_mode", None)
            if m in ("canonical", "legacy"):
                return str(m)
    return DEFAULT_QUOTE_MODE


def is_canonical_mode(cfg: Any = None) -> bool:
    return resolve_quote_semantic_mode(cfg) == "canonical"


@dataclass(frozen=True)
class CanonicalBoard:
    canonical_best_bid: Optional[float]
    canonical_bid_qty: Optional[float]
    canonical_best_ask: Optional[float]
    canonical_ask_qty: Optional[float]
    canonical_spread: Optional[float]
    canonical_spread_bps: Optional[float]
    canonical_mid: Optional[float]
    canonical_top_imbalance: Optional[float]
    canonical_depth_imbalance: Optional[float]
    canonical_depth_bid_qty: Optional[float]
    canonical_depth_ask_qty: Optional[float]
    legacy_mixed_imbalance: Optional[float]
    canonical_quote_valid: bool
    canonical_quote_reason: str
    canonical_locked: bool
    canonical_crossed: bool
    kabu_bid_price_raw: Optional[float]
    kabu_ask_price_raw: Optional[float]
    kabu_bid_qty_raw: Optional[float]
    kabu_ask_qty_raw: Optional[float]
    canonical_source_event_id: str = ""
    canonical_exchange_time: str = ""
    canonical_received_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def legacy_mixed_imbalance(payload: Mapping[str, Any] | None) -> Optional[float]:
    """Pre-repair calc_board_imbalance: BidQty+Buy vs AskQty+Sell (inverted/mixed)."""
    op = payload if isinstance(payload, Mapping) else {}
    bid = _f(op.get("BidQty")) or 0.0
    ask = _f(op.get("AskQty")) or 0.0
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


def canonical_depth_qty(payload: Mapping[str, Any] | None, *, n: int = 10) -> tuple[float, float]:
    op = payload if isinstance(payload, Mapping) else {}
    bid = 0.0
    ask = 0.0
    for i in range(1, n + 1):
        buy = op.get(f"Buy{i}")
        if isinstance(buy, Mapping):
            bid += _f(buy.get("Qty")) or 0.0
        sell = op.get(f"Sell{i}")
        if isinstance(sell, Mapping):
            ask += _f(sell.get("Qty")) or 0.0
    return bid, ask


def normalize_kabu_board(
    payload: Mapping[str, Any] | None,
    *,
    event_id: str = "",
    exchange_time: str = "",
    received_at: str = "",
) -> CanonicalBoard:
    """Normalize one atomic payload. No cross-event merge, no forward fill, no raw overwrite."""
    op = payload if isinstance(payload, Mapping) else {}
    buy_px, buy_qty = _level(op, "Buy", 1)
    sell_px, sell_qty = _level(op, "Sell", 1)
    kabu_bid = _f(op.get("BidPrice"))
    kabu_ask = _f(op.get("AskPrice"))
    kabu_bq = _f(op.get("BidQty"))
    kabu_aq = _f(op.get("AskQty"))

    best_bid, bid_qty = buy_px, buy_qty
    best_ask, ask_qty = sell_px, sell_qty

    reason = "OK"
    quote_valid = True
    locked = False
    crossed = False
    spread: Optional[float] = None
    spread_bps: Optional[float] = None
    mid: Optional[float] = None
    top_imb: Optional[float] = None

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
        elif spread < 0:
            crossed = True
            quote_valid = False
            reason = "CROSSED_TRUE_BOOK"
        total_q = bid_qty + ask_qty
        if total_q > 0:
            top_imb = bid_qty / total_q

    depth_bid, depth_ask = canonical_depth_qty(op)
    depth_total = depth_bid + depth_ask
    depth_imb = (depth_bid / depth_total) if depth_total > 0 else None
    leg_imb = legacy_mixed_imbalance(op)

    # metadata from payload if not provided
    if not exchange_time:
        exchange_time = str(op.get("CurrentPriceTime") or op.get("BidTime") or "")
    if not received_at:
        received_at = str(op.get("recorded_at") or "")
    if not event_id:
        sym = str(op.get("Symbol") or "")
        seq = op.get("sequence") or op.get("Sequence") or ""
        event_id = f"{sym}:{exchange_time}:{seq}"

    return CanonicalBoard(
        canonical_best_bid=best_bid,
        canonical_bid_qty=bid_qty,
        canonical_best_ask=best_ask,
        canonical_ask_qty=ask_qty,
        canonical_spread=spread,
        canonical_spread_bps=spread_bps,
        canonical_mid=mid,
        canonical_top_imbalance=top_imb,
        canonical_depth_imbalance=depth_imb,
        canonical_depth_bid_qty=depth_bid if depth_total > 0 else None,
        canonical_depth_ask_qty=depth_ask if depth_total > 0 else None,
        legacy_mixed_imbalance=leg_imb,
        canonical_quote_valid=bool(quote_valid and not crossed),
        canonical_quote_reason=reason,
        canonical_locked=locked,
        canonical_crossed=crossed,
        kabu_bid_price_raw=kabu_bid,
        kabu_ask_price_raw=kabu_ask,
        kabu_bid_qty_raw=kabu_bq,
        kabu_ask_qty_raw=kabu_aq,
        canonical_source_event_id=event_id,
        canonical_exchange_time=exchange_time,
        canonical_received_at=received_at,
    )


def attach_canonical_board(
    enriched: MutableMapping[str, Any],
    payload: Mapping[str, Any] | None = None,
    *,
    event_id: str = "",
    received_at: str = "",
) -> CanonicalBoard:
    """Attach canonical_* onto enriched dict. Raw Bid/Ask keys untouched."""
    src = payload if payload is not None else enriched
    board = normalize_kabu_board(
        src,
        event_id=event_id,
        received_at=received_at or str(enriched.get("recorded_at") or ""),
    )
    enriched.update(board.as_dict())
    return board


def board_token_from_imbalance(imbalance: Optional[float], *, p33: float = BOARD_P33, p66: float = BOARD_P66) -> str:
    if imbalance is None:
        return "Board:unknown"
    if imbalance < p33:
        return "Board:low"
    if imbalance < p66:
        return "Board:mid"
    return "Board:high"


def entry_imbalance_for_mode(
    payload: Mapping[str, Any],
    *,
    mode: Optional[str] = None,
) -> Optional[float]:
    """Imbalance used for PBv2 Board token / entry_order_book_imbalance."""
    m = mode or resolve_quote_semantic_mode()
    if m == "legacy":
        if payload.get("legacy_mixed_imbalance") is not None:
            return _f(payload.get("legacy_mixed_imbalance"))
        return legacy_mixed_imbalance(payload)
    # canonical: depth imbalance (Buy1..N / Sell1..N) — replaces mixed formula
    if payload.get("canonical_depth_imbalance") is not None:
        return _f(payload.get("canonical_depth_imbalance"))
    board = normalize_kabu_board(payload)
    return board.canonical_depth_imbalance


def top_imbalance_for_mode(
    payload: Mapping[str, Any],
    *,
    mode: Optional[str] = None,
) -> Optional[float]:
    """Top-of-book imbalance for realtime board EXIT."""
    m = mode or resolve_quote_semantic_mode()
    if m == "legacy":
        bq = _f(payload.get("BidQty")) or 0.0
        aq = _f(payload.get("AskQty")) or 0.0
        total = bq + aq
        return (bq / total) if total > 0 else None
    if payload.get("canonical_top_imbalance") is not None:
        return _f(payload.get("canonical_top_imbalance"))
    board = normalize_kabu_board(payload)
    return board.canonical_top_imbalance


def best_bid_ask_for_mode(
    payload: Mapping[str, Any],
    *,
    mode: Optional[str] = None,
) -> tuple[Optional[float], Optional[float]]:
    m = mode or resolve_quote_semantic_mode()
    if m == "legacy":
        return _f(payload.get("BidPrice")), _f(payload.get("AskPrice"))
    bid = _f(payload.get("canonical_best_bid"))
    ask = _f(payload.get("canonical_best_ask"))
    if bid is not None and ask is not None:
        return bid, ask
    board = normalize_kabu_board(payload)
    return board.canonical_best_bid, board.canonical_best_ask


def bid_ask_qty_for_mode(
    payload: Mapping[str, Any],
    *,
    mode: Optional[str] = None,
) -> tuple[Optional[float], Optional[float]]:
    m = mode or resolve_quote_semantic_mode()
    if m == "legacy":
        return _f(payload.get("BidQty")), _f(payload.get("AskQty"))
    bq = _f(payload.get("canonical_bid_qty"))
    aq = _f(payload.get("canonical_ask_qty"))
    if bq is not None and aq is not None:
        return bq, aq
    board = normalize_kabu_board(payload)
    return board.canonical_bid_qty, board.canonical_ask_qty


def spread_bps_for_mode(
    payload: Mapping[str, Any],
    *,
    mode: Optional[str] = None,
) -> Optional[float]:
    m = mode or resolve_quote_semantic_mode()
    if m == "canonical":
        sb = _f(payload.get("canonical_spread_bps"))
        if sb is not None:
            return sb
        board = normalize_kabu_board(payload)
        return board.canonical_spread_bps
    # legacy abs spread from labeled Bid/Ask (same magnitude as canonical when both present)
    bid = _f(payload.get("BidPrice"))
    ask = _f(payload.get("AskPrice"))
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return abs(ask - bid) / mid * 10000.0


def buy_limit_price(payload: Mapping[str, Any], *, mode: Optional[str] = None) -> Optional[float]:
    """Buy at ask (canonical) or legacy AskPrice label."""
    m = mode or resolve_quote_semantic_mode()
    if m == "canonical":
        ask = _f(payload.get("canonical_best_ask"))
        if ask is None:
            ask = normalize_kabu_board(payload).canonical_best_ask
        if ask is not None and ask > 0:
            return round(ask, 1)
    else:
        ask = _f(payload.get("AskPrice"))
        if ask is not None and ask > 0:
            return round(ask, 1)
    px = _f(payload.get("CurrentPrice"))
    return round(px, 1) if px is not None and px > 0 else None


def sell_limit_price(payload: Mapping[str, Any], *, mode: Optional[str] = None) -> Optional[float]:
    """Sell at bid (canonical) or legacy BidPrice label."""
    m = mode or resolve_quote_semantic_mode()
    if m == "canonical":
        bid = _f(payload.get("canonical_best_bid"))
        if bid is None:
            bid = normalize_kabu_board(payload).canonical_best_bid
        if bid is not None and bid > 0:
            return round(bid, 1)
    else:
        bid = _f(payload.get("BidPrice"))
        if bid is not None and bid > 0:
            return round(bid, 1)
    px = _f(payload.get("CurrentPrice"))
    return round(px, 1) if px is not None and px > 0 else None


def has_board_prices(payload: Mapping[str, Any], *, mode: Optional[str] = None) -> bool:
    bid, ask = best_bid_ask_for_mode(payload, mode=mode)
    return bid is not None and ask is not None


# B2: top-only transform proof (legacy top = 1 - canonical top)
def top_imbalance_transform_threshold(legacy_threshold: float) -> float:
    return 1.0 - float(legacy_threshold)


DEPTH_MIXED_TRANSFORMABLE = False  # BidQty+Buy mix → NOT_TRANSFORMABLE
