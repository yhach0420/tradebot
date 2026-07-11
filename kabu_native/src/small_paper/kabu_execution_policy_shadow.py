"""Phase687W5B — Exchange / order-style shadow + compact fill simulation.

No production policy selection. No network submit.
Future path prices are evaluation-only — never fed into policy inputs.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from small_paper.kabu_order_execution_policy import dryrun_limit_policy, dryrun_market_policy
from small_paper.kabu_order_request_builder import OrderIntentContract, OrderRequestBuilder
from small_paper.kabu_sendorder_contract import ExchangePolicy


class EntryOrderStyleShadow(str, Enum):
    MARKET = "MARKET"
    LIMIT_AT_ASK = "LIMIT_AT_ASK"
    LIMIT_AT_LAST = "LIMIT_AT_LAST"
    LIMIT_TICK_OFFSET = "LIMIT_TICK_OFFSET"


class ExitOrderStyleShadow(str, Enum):
    MARKET = "MARKET"
    AGGRESSIVE_LIMIT = "AGGRESSIVE_LIMIT"
    BID_ASK_ALIGNED_LIMIT = "BID_ASK_ALIGNED_LIMIT"
    CONTROLLED_PASSIVE_LIMIT = "CONTROLLED_PASSIVE_LIMIT"
    DEADLINE_AWARE_AGGRESSIVE_LIMIT = "DEADLINE_AWARE_AGGRESSIVE_LIMIT"


@dataclass
class BoardSnapshotCompact:
    """Compact board slice — not raw PUSH."""

    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    last: Optional[float] = None
    spread: Optional[float] = None
    spread_bps: Optional[float] = None
    spread_ticks: Optional[float] = None
    tick_size: float = 1.0
    complete: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_board_snapshot(
    *,
    best_bid: Optional[float] = None,
    best_ask: Optional[float] = None,
    last: Optional[float] = None,
    tick_size: float = 1.0,
) -> BoardSnapshotCompact:
    snap = BoardSnapshotCompact(
        best_bid=best_bid,
        best_ask=best_ask,
        last=last,
        tick_size=float(tick_size or 1.0),
    )
    if best_bid is not None and best_ask is not None and best_ask >= best_bid:
        snap.spread = float(best_ask) - float(best_bid)
        mid = (float(best_ask) + float(best_bid)) / 2.0
        snap.spread_bps = (snap.spread / mid * 10000.0) if mid > 0 else None
        snap.spread_ticks = snap.spread / snap.tick_size if snap.tick_size > 0 else None
        snap.complete = True
    elif last is not None:
        snap.complete = False
    return snap


@dataclass
class FillSimResult:
    hypothetically_filled: bool = False
    fill_time_ms: Optional[float] = None
    fill_price: Optional[float] = None
    unfilled_at_1s: bool = True
    unfilled_at_3s: bool = True
    unfilled_at_5s: bool = True
    unfilled_at_10s: bool = True
    slippage_vs_accept_bps: Optional[float] = None
    slippage_vs_paper_fill_bps: Optional[float] = None
    adverse_move_after_fill: Optional[float] = None
    request_build_latency_ms: float = 0.0
    status: str = "UNKNOWN"  # FILLED | UNFILLED | UNKNOWN
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def simulate_hypothetical_fill(
    *,
    side: str,  # BUY / SELL
    order_style: str,
    limit_price: Optional[float],
    accept_price: float,
    paper_fill_price: Optional[float],
    price_path: Sequence[tuple[float, float]],  # (offset_ms, price) after accept — EVAL ONLY
    board: BoardSnapshotCompact,
    request_build_latency_ms: float = 0.0,
) -> FillSimResult:
    """Compact path simulation. Incomplete board/path → UNKNOWN (never invent fill)."""
    out = FillSimResult(request_build_latency_ms=float(request_build_latency_ms))
    if not price_path and not board.complete and order_style != EntryOrderStyleShadow.MARKET.value:
        out.status = "UNKNOWN"
        out.reason = "insufficient_board_or_path"
        return out

    # Determine reference fill condition
    is_buy = str(side).upper() in ("BUY", "2")
    filled = False
    fill_ms: Optional[float] = None
    fill_px: Optional[float] = None

    if order_style == EntryOrderStyleShadow.MARKET.value or order_style == ExitOrderStyleShadow.MARKET.value:
        if board.complete:
            fill_px = float(board.best_ask if is_buy else board.best_bid)  # type: ignore[arg-type]
            fill_ms = 0.0
            filled = True
        elif price_path:
            fill_ms, fill_px = price_path[0]
            filled = True
        else:
            out.status = "UNKNOWN"
            out.reason = "market_no_board_or_path"
            return out
    else:
        if limit_price is None or not price_path:
            out.status = "UNKNOWN"
            out.reason = "limit_missing_price_or_path"
            return out
        lim = float(limit_price)
        for ms, px in price_path:
            if is_buy and float(px) <= lim:
                filled, fill_ms, fill_px = True, float(ms), float(px)
                break
            if (not is_buy) and float(px) >= lim:
                filled, fill_ms, fill_px = True, float(ms), float(px)
                break

    out.hypothetically_filled = filled
    out.fill_time_ms = fill_ms
    out.fill_price = fill_px
    out.status = "FILLED" if filled else "UNFILLED"
    if fill_ms is not None:
        out.unfilled_at_1s = fill_ms > 1000
        out.unfilled_at_3s = fill_ms > 3000
        out.unfilled_at_5s = fill_ms > 5000
        out.unfilled_at_10s = fill_ms > 10000
    else:
        out.unfilled_at_1s = out.unfilled_at_3s = out.unfilled_at_5s = out.unfilled_at_10s = True

    if filled and fill_px is not None and accept_price > 0:
        # buy: higher fill = adverse; sell: lower fill = adverse
        if is_buy:
            out.slippage_vs_accept_bps = (fill_px - accept_price) / accept_price * 10000.0
        else:
            out.slippage_vs_accept_bps = (accept_price - fill_px) / accept_price * 10000.0
        if paper_fill_price and paper_fill_price > 0:
            if is_buy:
                out.slippage_vs_paper_fill_bps = (fill_px - paper_fill_price) / paper_fill_price * 10000.0
            else:
                out.slippage_vs_paper_fill_bps = (paper_fill_price - fill_px) / paper_fill_price * 10000.0
        # adverse move after fill from remaining path
        later = [px for ms, px in price_path if fill_ms is not None and ms > fill_ms]
        if later:
            if is_buy:
                out.adverse_move_after_fill = max(later) - fill_px
            else:
                out.adverse_move_after_fill = fill_px - min(later)
    return out


def shadow_entry_exchange_candidates(
    *,
    symbol: str,
    position_id: str,
    accepted_at: str,
    accept_price: float,
    quantity: int = 100,
    board: Optional[BoardSnapshotCompact] = None,
    builder: Optional[OrderRequestBuilder] = None,
) -> list[dict[str, Any]]:
    """Generate SOR and TSE+ dry-run request candidates. No production fallback between them."""
    b = builder or OrderRequestBuilder()
    board = board or BoardSnapshotCompact()
    rows = []
    for pol in (ExchangePolicy.SOR.value, ExchangePolicy.TSE_PLUS.value):
        t0 = time.perf_counter()
        intent = OrderIntentContract(
            intent_id=f"shadow-ex-{pol}-{position_id}",
            idempotency_key=f"shadow-ex-{pol}-{position_id}",
            side="BUY",
            symbol=symbol,
            quantity=quantity,
            position_id=position_id,
            entry_or_exit="ENTRY",
            limit_price=float(board.best_ask or accept_price),
            price_snapshot=float(accept_price),
            exchange_policy=pol,
            margin_trade_type_source="SHADOW_NOT_VERIFIED",
            intent_kind="actual",
        )
        result = b.build(intent, dryrun_limit_policy())
        build_ms = (time.perf_counter() - t0) * 1000.0
        rows.append(
            {
                "symbol": symbol,
                "accepted_at": accepted_at,
                "exchange_policy": pol,
                "request_valid": result.request_valid,
                "request_valid_for_submit": False,
                "production_authorized": False,
                "board_complete": board.complete,
                "reference_price": board.best_ask or accept_price,
                "spread": board.spread,
                "best_bid": board.best_bid,
                "best_ask": board.best_ask,
                "expected_fill_reference": board.best_ask,
                "policy_availability": result.request_valid,
                "data_completeness": "complete" if board.complete else "incomplete",
                "rejection_reason": result.error_category,
                "request_build_latency_ms": build_ms,
                "fingerprint": result.request_fingerprint,
            }
        )
    return rows


def shadow_entry_order_styles(
    *,
    symbol: str,
    position_id: str,
    accept_price: float,
    board: BoardSnapshotCompact,
    price_path_after_accept: Sequence[tuple[float, float]],
    paper_fill_price: Optional[float] = None,
    quantity: int = 100,
    tick_offset: int = 1,
) -> list[dict[str, Any]]:
    """ENTRY style shadows. price_path is EVAL ONLY — not used as policy input fields."""
    # Policy inputs: accept_price + board at accept time only
    policy_inputs = {
        "accept_price": accept_price,
        "best_bid": board.best_bid,
        "best_ask": board.best_ask,
        "last": board.last or accept_price,
    }
    # Explicitly exclude future path from policy inputs
    assert "price_path_after_accept" not in policy_inputs

    styles = [
        (EntryOrderStyleShadow.MARKET.value, None, dryrun_market_policy(entry_or_exit="ENTRY")),
        (EntryOrderStyleShadow.LIMIT_AT_ASK.value, board.best_ask or accept_price, dryrun_limit_policy()),
        (EntryOrderStyleShadow.LIMIT_AT_LAST.value, board.last or accept_price, dryrun_limit_policy()),
        (
            EntryOrderStyleShadow.LIMIT_TICK_OFFSET.value,
            (board.best_ask or accept_price) + tick_offset * board.tick_size,
            dryrun_limit_policy(),
        ),
    ]
    rows = []
    b = OrderRequestBuilder()
    for style, lim, pol in styles:
        t0 = time.perf_counter()
        intent = OrderIntentContract(
            intent_id=f"shadow-es-{style}-{position_id}",
            idempotency_key=f"shadow-es-{style}-{position_id}",
            side="BUY",
            symbol=symbol,
            quantity=quantity,
            position_id=position_id,
            entry_or_exit="ENTRY",
            limit_price=float(lim or accept_price),
            price_snapshot=float(accept_price),
            exchange_policy=ExchangePolicy.SOR.value,
            margin_trade_type_source="SHADOW_NOT_VERIFIED",
            intent_kind="actual",
        )
        built = b.build(intent, pol)
        build_ms = (time.perf_counter() - t0) * 1000.0
        sim = simulate_hypothetical_fill(
            side="BUY",
            order_style=style,
            limit_price=lim,
            accept_price=accept_price,
            paper_fill_price=paper_fill_price,
            price_path=price_path_after_accept,
            board=board,
            request_build_latency_ms=build_ms,
        )
        rows.append(
            {
                "symbol": symbol,
                "position_id": position_id,
                "order_style": style,
                "policy_inputs": policy_inputs,
                "future_data_used_as_policy_input": False,
                "request_valid": built.request_valid,
                "request_valid_for_submit": False,
                "production_policy_selected": False,
                "limit_price": lim,
                "spread_bps": board.spread_bps,
                "fill_sim": sim.to_dict(),
            }
        )
    return rows


def shadow_exit_order_styles(
    *,
    symbol: str,
    position_id: str,
    exit_reason: str,
    accept_price: float,
    board: BoardSnapshotCompact,
    price_path_after_signal: Sequence[tuple[float, float]],
    paper_fill_price: Optional[float] = None,
    quantity: int = 100,
    open_position_exchange: int = 1,
    margin_trade_type: int = 3,
) -> list[dict[str, Any]]:
    reason = str(exit_reason or "").lower()
    if "stop" in reason:
        candidates = [
            ExitOrderStyleShadow.MARKET.value,
            ExitOrderStyleShadow.AGGRESSIVE_LIMIT.value,
        ]
    elif "no_progress" in reason:
        candidates = [
            ExitOrderStyleShadow.MARKET.value,
            ExitOrderStyleShadow.BID_ASK_ALIGNED_LIMIT.value,
            ExitOrderStyleShadow.CONTROLLED_PASSIVE_LIMIT.value,
        ]
    elif "trailing" in reason or "mfe" in reason:
        candidates = [
            ExitOrderStyleShadow.MARKET.value,
            ExitOrderStyleShadow.AGGRESSIVE_LIMIT.value,
        ]
    elif "session" in reason or "close" in reason:
        candidates = [
            ExitOrderStyleShadow.MARKET.value,
            ExitOrderStyleShadow.DEADLINE_AWARE_AGGRESSIVE_LIMIT.value,
        ]
    else:
        candidates = [ExitOrderStyleShadow.MARKET.value, ExitOrderStyleShadow.AGGRESSIVE_LIMIT.value]

    rows = []
    b = OrderRequestBuilder()
    for style in candidates:
        is_mkt = style == ExitOrderStyleShadow.MARKET.value
        lim = None if is_mkt else (board.best_bid or accept_price)
        if style == ExitOrderStyleShadow.CONTROLLED_PASSIVE_LIMIT.value:
            lim = board.best_ask or accept_price
        t0 = time.perf_counter()
        intent = OrderIntentContract(
            intent_id=f"shadow-xs-{style}-{position_id}",
            idempotency_key=f"shadow-xs-{style}-{position_id}",
            side="SELL",
            symbol=symbol,
            quantity=quantity,
            position_id=position_id,
            entry_or_exit="EXIT",
            exit_reason=exit_reason,
            limit_price=lim,
            holding_qty=quantity,
            exchange_policy=ExchangePolicy.REPAY_MATCH_OPEN_POSITION_EXCHANGE.value,
            open_position_exchange=open_position_exchange,
            margin_trade_type=margin_trade_type,
            expected_margin_trade_type=margin_trade_type,
            margin_trade_type_source="BROKER_POSITION",
            intent_kind="actual",
        )
        pol = dryrun_market_policy() if is_mkt else dryrun_limit_policy(entry_or_exit="EXIT")
        built = b.build(intent, pol)
        build_ms = (time.perf_counter() - t0) * 1000.0
        sim = simulate_hypothetical_fill(
            side="SELL",
            order_style=style if not is_mkt else ExitOrderStyleShadow.MARKET.value,
            limit_price=lim,
            accept_price=accept_price,
            paper_fill_price=paper_fill_price,
            price_path=price_path_after_signal,
            board=board,
            request_build_latency_ms=build_ms,
        )
        stop_wait_risk = ("stop" in reason) and (not sim.hypothetically_filled or (sim.fill_time_ms or 0) > 1000)
        rows.append(
            {
                "symbol": symbol,
                "position_id": position_id,
                "exit_reason": exit_reason,
                "order_style": style,
                "request_valid": built.request_valid,
                "request_valid_for_submit": False,
                "production_policy_selected": False,
                "future_data_used_as_policy_input": False,
                "fill_sim": sim.to_dict(),
                "stop_unfilled_wait_risk": stop_wait_risk,
            }
        )
    return rows


def summarize_fill_simulations(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    fills = []
    times = []
    slips = []
    for r in rows:
        sim = r.get("fill_sim") or {}
        if sim.get("hypothetically_filled"):
            fills.append(1)
            if sim.get("fill_time_ms") is not None:
                times.append(float(sim["fill_time_ms"]))
            if sim.get("slippage_vs_accept_bps") is not None:
                slips.append(float(sim["slippage_vs_accept_bps"]))
        elif sim.get("status") == "UNKNOWN":
            fills.append(None)
        else:
            fills.append(0)
    known = [x for x in fills if x is not None]
    return {
        "n": len(rows),
        "known_n": len(known),
        "fill_rate": (sum(known) / len(known)) if known else None,
        "fill_time_ms_median": statistics.median(times) if times else None,
        "fill_time_ms_p95": sorted(times)[int(0.95 * (len(times) - 1))] if times else None,
        "slippage_bps_median": statistics.median(slips) if slips else None,
        "slippage_bps_p95": sorted(slips)[int(0.95 * (len(slips) - 1))] if slips else None,
        "production_policy_selected": False,
        "note": "audit slices only; not production rules",
    }


def soak_shadow_metrics(
    *,
    capability_status: str,
    margin_trade_type_status: str,
    observed_margin_trade_types: Sequence[int],
    identity_matches: Sequence[Mapping[str, Any]],
    close_decisions: Sequence[Mapping[str, Any]],
    exchange_shadow_count: int,
    execution_policy_shadow_count: int,
) -> dict[str, Any]:
    match_ok = sum(1 for m in identity_matches if m.get("match_status") == "UNIQUE")
    match_bad = sum(1 for m in identity_matches if m.get("match_status") not in ("UNIQUE", "MULTI"))
    exact_ok = sum(
        1
        for c in close_decisions
        if c.get("policy_id") in ("CLOSE_EXACT_HOLD_ID", "CLOSE_EXACT_MULTI_HOLD") and c.get("request_valid")
    )
    exact_na = sum(1 for c in close_decisions if c.get("policy_id") == "RECOVERY_REQUIRED")
    return {
        "account_capability_status": capability_status,
        "margin_trade_type_status": margin_trade_type_status,
        "observed_margin_trade_types": list(observed_margin_trade_types),
        "position_identity_match_count": match_ok,
        "position_identity_mismatch_count": match_bad,
        "exact_hold_close_candidate_count": exact_ok,
        "exact_hold_close_not_evaluable_count": exact_na,
        "entry_exchange_shadow_count": int(exchange_shadow_count),
        "execution_policy_shadow_count": int(execution_policy_shadow_count),
        "policy_feature_coverage": {
            "exchange_shadow": exchange_shadow_count > 0,
            "entry_style_shadow": execution_policy_shadow_count > 0,
            "close_exact": exact_ok > 0,
            "production_policy_selected": False,
        },
        "production_policy_selection_allowed": False,
        "min_w4s_sessions_before_policy_selection": 3,
    }
