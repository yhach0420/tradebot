"""Ask ENTRY / Bid EXIT fills on atomic quotes (100 shares, walk book if depth)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional, Sequence

from research.execution_grade_confirmation.board import AtomicQuote
from research.execution_grade_confirmation.constants import LATENCY_MS, SHARES
from research.pbv2_zero_base_revalidation.util import pnl_5bps


def _idx_at_or_after(quotes: Sequence[AtomicQuote], t: datetime) -> int:
    # binary search
    lo, hi = 0, len(quotes)
    while lo < hi:
        mid = (lo + hi) // 2
        if quotes[mid].received_at < t:
            lo = mid + 1
        else:
            hi = mid
    return lo


def find_quote_at(quotes: Sequence[AtomicQuote], t: datetime) -> Optional[AtomicQuote]:
    """Exact same received_at if present, else None (E0/X0 same-payload)."""
    i = _idx_at_or_after(quotes, t)
    if i < len(quotes) and quotes[i].received_at == t:
        return quotes[i]
    # allow ±1ms match
    if i < len(quotes) and abs((quotes[i].received_at - t).total_seconds()) < 0.001:
        return quotes[i]
    if i > 0 and abs((quotes[i - 1].received_at - t).total_seconds()) < 0.001:
        return quotes[i - 1]
    return None


def first_valid_after(
    quotes: Sequence[AtomicQuote],
    t0: datetime,
    *,
    side: str,
    min_delay_ms: float = 0.0,
) -> Optional[AtomicQuote]:
    start = t0 + timedelta(milliseconds=min_delay_ms)
    i = _idx_at_or_after(quotes, start)
    for q in quotes[i:]:
        if not q.quote_valid:
            continue
        if side == "ask" and q.best_ask is not None and q.best_ask > 0:
            return q
        if side == "bid" and q.best_bid is not None and q.best_bid > 0:
            return q
    return None


def walk_ask(q: AtomicQuote, shares: float = SHARES) -> dict[str, Any]:
    """Buy shares walking Sell depth (asks)."""
    levels = q.depth_asks if q.depth_asks else (
        [(q.best_ask, q.ask_qty or 0.0)] if q.best_ask is not None else []
    )
    need = float(shares)
    cost = 0.0
    filled = 0.0
    for px, qty in levels:
        if need <= 0:
            break
        take = min(need, float(qty))
        cost += take * float(px)
        filled += take
        need -= take
    if filled < shares - 1e-9:
        return {
            "fill_status": "NOT_FULLY_EVALUABLE",
            "fill_price": None,
            "fill_qty": filled,
            "reason": "insufficient_ask_depth",
        }
    return {
        "fill_status": "FILLED",
        "fill_price": cost / filled,
        "fill_qty": filled,
        "reason": "",
    }


def walk_bid(q: AtomicQuote, shares: float = SHARES) -> dict[str, Any]:
    """Sell shares walking Buy depth (bids)."""
    levels = q.depth_bids if q.depth_bids else (
        [(q.best_bid, q.bid_qty or 0.0)] if q.best_bid is not None else []
    )
    need = float(shares)
    proceeds = 0.0
    filled = 0.0
    for px, qty in levels:
        if need <= 0:
            break
        take = min(need, float(qty))
        proceeds += take * float(px)
        filled += take
        need -= take
    if filled < shares - 1e-9:
        return {
            "fill_status": "NOT_FULLY_EVALUABLE",
            "fill_price": None,
            "fill_qty": filled,
            "reason": "insufficient_bid_depth",
        }
    return {
        "fill_status": "FILLED",
        "fill_price": proceeds / filled,
        "fill_qty": filled,
        "reason": "",
    }


def entry_fill(
    quotes: Sequence[AtomicQuote],
    decision_time: datetime,
    *,
    scenario: str,
) -> dict[str, Any]:
    """E0 same event ask; E1+ delayed first valid ask. Never mid/current/bid."""
    lat_map = {"E0": None, "E1": 0, "E2": 100, "E3": 250, "E4": 500, "E5": 1000}
    if scenario not in lat_map:
        return {"fill_status": "NOT_EVALUABLE", "reason": "bad_scenario"}
    crossed_at = False
    if scenario == "E0":
        q = find_quote_at(quotes, decision_time)
        if q is None:
            # nearest event at or after decision within 0ms window only — E0 requires same payload
            return {
                "decision_time": decision_time.isoformat(),
                "fill_status": "NOT_EVALUABLE",
                "reason": "no_same_payload_event",
                "quote_valid": False,
                "crossed_at_decision": False,
                "scenario": scenario,
            }
        crossed_at = bool(q.crossed or (q.kabu_ask is not None and q.kabu_bid is not None and q.kabu_ask <= q.kabu_bid))
        if not q.quote_valid:
            return {
                "decision_time": decision_time.isoformat(),
                "fill_event_time": q.received_at.isoformat(),
                "fill_status": "NOT_EVALUABLE",
                "reason": q.quote_invalid_reason or "invalid_quote",
                "quote_valid": False,
                "crossed_at_decision": crossed_at,
                "scenario": scenario,
                "best_ask": q.best_ask,
                "ask_qty": q.ask_qty,
            }
        walk = walk_ask(q)
        delay = 0.0
    else:
        q0 = find_quote_at(quotes, decision_time)
        crossed_at = bool(
            q0
            and (
                q0.crossed
                or (q0.kabu_ask is not None and q0.kabu_bid is not None and q0.kabu_ask <= q0.kabu_bid)
            )
        )
        q = first_valid_after(quotes, decision_time, side="ask", min_delay_ms=float(lat_map[scenario]))
        if q is None:
            return {
                "decision_time": decision_time.isoformat(),
                "fill_status": "NOT_EVALUABLE",
                "reason": "no_valid_ask_after_delay",
                "quote_valid": False,
                "crossed_at_decision": crossed_at,
                "scenario": scenario,
                "first_valid_quote_delay_ms": None,
            }
        walk = walk_ask(q)
        delay = (q.received_at - decision_time).total_seconds() * 1000.0

    out = {
        "decision_time": decision_time.isoformat(),
        "order_submit_assumption_time": decision_time.isoformat(),
        "fill_event_time": q.received_at.isoformat(),
        "fill_delay_ms": round(delay, 3),
        "first_valid_quote_delay_ms": round(delay, 3),
        "fill_price": walk.get("fill_price"),
        "fill_qty": walk.get("fill_qty"),
        "fill_status": walk.get("fill_status"),
        "quote_valid": True,
        "crossed_at_decision": crossed_at,
        "scenario": scenario,
        "best_ask": q.best_ask,
        "ask_qty": q.ask_qty,
        "used_ask_not_bid": True,
        "used_mid": False,
        "used_current_price": False,
        "event_id": q.event_id,
    }
    if walk.get("fill_status") != "FILLED":
        out["reason"] = walk.get("reason")
    return out


def exit_fill(
    quotes: Sequence[AtomicQuote],
    decision_time: datetime,
    *,
    scenario: str,
) -> dict[str, Any]:
    lat_map = {"X0": None, "X1": 0, "X2": 100, "X3": 250, "X4": 500, "X5": 1000}
    if scenario not in lat_map:
        return {"fill_status": "NOT_EVALUABLE", "reason": "bad_scenario"}
    if scenario == "X0":
        q = find_quote_at(quotes, decision_time)
        if q is None or not q.quote_valid:
            return {
                "decision_time": decision_time.isoformat(),
                "fill_status": "NOT_EVALUABLE",
                "reason": "no_same_payload_valid_bid" if q is None else q.quote_invalid_reason,
                "scenario": scenario,
            }
        walk = walk_bid(q)
        delay = 0.0
    else:
        q = first_valid_after(quotes, decision_time, side="bid", min_delay_ms=float(lat_map[scenario]))
        if q is None:
            return {
                "decision_time": decision_time.isoformat(),
                "fill_status": "NOT_EVALUABLE",
                "reason": "no_valid_bid_after_delay",
                "scenario": scenario,
            }
        walk = walk_bid(q)
        delay = (q.received_at - decision_time).total_seconds() * 1000.0
    return {
        "decision_time": decision_time.isoformat(),
        "fill_event_time": q.received_at.isoformat(),
        "fill_delay_ms": round(delay, 3),
        "fill_price": walk.get("fill_price"),
        "fill_qty": walk.get("fill_qty"),
        "fill_status": walk.get("fill_status"),
        "quote_valid": True,
        "scenario": scenario,
        "best_bid": q.best_bid,
        "bid_qty": q.bid_qty,
        "used_bid_not_ask": True,
        "event_id": q.event_id,
        "reason": walk.get("reason") or "",
    }


def trade_pnl(entry_px: float, exit_px: float) -> float:
    return pnl_5bps(entry_px, exit_px)
