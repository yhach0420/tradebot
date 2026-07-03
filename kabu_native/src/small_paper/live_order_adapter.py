"""
Phase594: Live order adapter — capital check + payload dry-run + notifier pipeline.

Paper Runtime drives signals only. No kabusapi sendOrder in this phase.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional

from small_paper.live_capital_manager import (
    LiveCapitalManagerSession,
    LiveCapitalSnapshot,
    capital_manager_enabled,
    check_entry_capital_on_paper_accept,
)
from small_paper.live_order_api_wiring import (
    build_entry_sendorder_payload,
    build_exit_sendorder_payload,
    make_client_order_id,
)
from small_paper.live_order_dry_run_adapter import (
    LOT_SIZE,
    DEFAULT_ENTRY_TIMEOUT_SEC,
    _exit_order_type,
    _limit_entry_price,
    _paper_trade_id,
)
from small_paper.live_order_notifier import (
    EVENT_CAPITAL_CHECK_BLOCK,
    EVENT_CAPITAL_CHECK_PASS,
    EVENT_CLOSED_DRYRUN,
    EVENT_ENTRY_SIGNAL,
    EVENT_EXIT_FILLED_DRYRUN,
    EVENT_EXIT_ORDER_PREPARED,
    EVENT_EXIT_SIGNAL,
    EVENT_EXIT_WOULD_SEND,
    EVENT_FILLED_DRYRUN,
    EVENT_OPEN_POSITION_DRYRUN,
    EVENT_ORDER_ACCEPTED_DRYRUN,
    EVENT_ORDER_PREPARED,
    EVENT_ORDER_WOULD_SEND,
    EVENT_SAFE_STOP,
    LiveOrderNotifier,
    notifier_enabled,
    notifier_summary_fields,
)

PHASE594_ORDER_ENABLED_FORBIDDEN = "phase594: order_enabled=true is forbidden until live send pilot"


class AdapterState(str, Enum):
    NONE = "NONE"
    ENTRY_SIGNAL = "ENTRY_SIGNAL"
    CAPITAL_CHECK_PASS = "CAPITAL_CHECK_PASS"
    CAPITAL_CHECK_BLOCK = "CAPITAL_CHECK_BLOCK"
    ORDER_PREPARED = "ORDER_PREPARED"
    ORDER_WOULD_SEND = "ORDER_WOULD_SEND"
    ORDER_ACCEPTED_DRYRUN = "ORDER_ACCEPTED_DRYRUN"
    FILLED_DRYRUN = "FILLED_DRYRUN"
    OPEN_POSITION_DRYRUN = "OPEN_POSITION_DRYRUN"
    EXIT_SIGNAL = "EXIT_SIGNAL"
    EXIT_ORDER_PREPARED = "EXIT_ORDER_PREPARED"
    EXIT_WOULD_SEND = "EXIT_WOULD_SEND"
    EXIT_FILLED_DRYRUN = "EXIT_FILLED_DRYRUN"
    CLOSED_DRYRUN = "CLOSED_DRYRUN"
    SAFE_STOP = "SAFE_STOP"


def live_order_adapter_enabled(config: Any) -> bool:
    if bool(getattr(config, "live_trading_enabled", False)):
        return False
    if bool(getattr(config, "order_enabled", False)):
        return False
    return bool(getattr(config, "live_order_adapter_enabled", True))


def phase594_preflight_check(config: Any) -> tuple[bool, str]:
    if bool(getattr(config, "order_enabled", False)):
        return False, PHASE594_ORDER_ENABLED_FORBIDDEN
    if bool(getattr(config, "live_trading_enabled", False)):
        return False, "live_trading_enabled must be false in phase594"
    if not bool(getattr(config, "dry_run", True)):
        return False, "dry_run must be true in phase594"
    return True, "ok"


@dataclass
class SymbolAdapterTrack:
    symbol: str
    state: AdapterState = AdapterState.NONE
    paper_trade_id: str = ""
    quantity: int = LOT_SIZE
    entry_price: Optional[float] = None
    exit_reason: str = ""
    client_order_id: str = ""
    last_payload: Optional[dict[str, Any]] = None


@dataclass
class LiveOrderAdapterSession:
    position_cap: int = 5
    entry_timeout_sec: float = DEFAULT_ENTRY_TIMEOUT_SEC
    tracks: dict[str, SymbolAdapterTrack] = field(default_factory=dict)
    notifier: LiveOrderNotifier = field(default_factory=LiveOrderNotifier)
    entry_count: int = 0
    exit_count: int = 0
    capital_block_count: int = 0
    would_send_count: int = 0
    safe_stop: bool = False
    safe_stop_reason: str = ""

    def trigger_safe_stop(self, reason: str) -> None:
        self.safe_stop = True
        self.safe_stop_reason = reason


def _guard_sendorder(config: Any) -> None:
    if bool(getattr(config, "order_enabled", False)):
        raise NotImplementedError(PHASE594_ORDER_ENABLED_FORBIDDEN)


def _transition(track: SymbolAdapterTrack, new_state: AdapterState) -> None:
    track.state = new_state


def process_paper_entry(
    session: LiveOrderAdapterSession,
    *,
    symbol: str,
    trade: Mapping[str, Any],
    payload: Mapping[str, Any],
    timestamp: str,
    writer: Any,
    config: Any,
    capital_session: Optional[LiveCapitalManagerSession] = None,
    capital_client: Any = None,
    capital_token: str = "",
    day_pnl_pct: Optional[float] = None,
    repo_root: Optional[Path] = None,
    discord_send: Optional[Any] = None,
) -> dict[str, Any]:
    if session.safe_stop:
        session.notifier.emit(
            EVENT_SAFE_STOP,
            {"symbol": symbol, "reason": session.safe_stop_reason, "timestamp": timestamp},
            writer=writer,
            config=config,
            discord_send=discord_send,
        )
        return {"ok": False, "blocked": True, "reason": "safe_stop"}

    paper_id = _paper_trade_id(trade, symbol)
    track = SymbolAdapterTrack(symbol=symbol, paper_trade_id=paper_id, quantity=LOT_SIZE)
    session.tracks[symbol] = track
    _transition(track, AdapterState.ENTRY_SIGNAL)
    session.notifier.emit(
        EVENT_ENTRY_SIGNAL,
        {
            "symbol": symbol,
            "side": "ENTRY",
            "timestamp": timestamp,
            "state_from": AdapterState.NONE.value,
            "state_to": AdapterState.ENTRY_SIGNAL.value,
            "linked_paper_trade_id": paper_id,
        },
        writer=writer,
        config=config,
        discord_send=discord_send,
    )

    t0 = time.perf_counter()
    cap_row: dict[str, Any] = {"can_enter": True, "skipped": True}
    if (
        capital_session is not None
        and capital_client is not None
        and capital_token
        and capital_manager_enabled(config)
    ):
        cap_row = check_entry_capital_on_paper_accept(
            capital_session,
            symbol=symbol,
            trade=trade,
            payload=payload,
            writer=writer,
            config=config,
            client=capital_client,
            token=capital_token,
            repo_root=repo_root,
            day_pnl_pct=day_pnl_pct,
            linked_paper_trade_id=paper_id,
        )
    elif capital_manager_enabled(config):
        cap_row = _mock_capital_pass(payload, symbol=symbol)

    latency_ms = round((time.perf_counter() - t0) * 1000.0, 3)
    entry_price = float(cap_row.get("price") or _limit_entry_price(payload) or 0)

    if not cap_row.get("can_enter"):
        _transition(track, AdapterState.CAPITAL_CHECK_BLOCK)
        session.capital_block_count += 1
        session.notifier.emit(
            EVENT_CAPITAL_CHECK_BLOCK,
            {
                **cap_row,
                "symbol": symbol,
                "side": "ENTRY",
                "timestamp": timestamp,
                "state_from": AdapterState.ENTRY_SIGNAL.value,
                "state_to": AdapterState.CAPITAL_CHECK_BLOCK.value,
                "latency_ms": latency_ms,
                "linked_paper_trade_id": paper_id,
            },
            writer=writer,
            config=config,
            discord_send=discord_send,
        )
        return {"ok": False, "blocked": True, "reason": cap_row.get("reject_reason")}

    _transition(track, AdapterState.CAPITAL_CHECK_PASS)
    session.notifier.emit(
        EVENT_CAPITAL_CHECK_PASS,
        {
            **cap_row,
            "symbol": symbol,
            "side": "ENTRY",
            "timestamp": timestamp,
            "state_from": AdapterState.ENTRY_SIGNAL.value,
            "state_to": AdapterState.CAPITAL_CHECK_PASS.value,
            "order_phase": "WOULD_SEND",
            "qty": LOT_SIZE,
            "price": entry_price,
            "latency_ms": latency_ms,
            "linked_paper_trade_id": paper_id,
        },
        writer=writer,
        config=config,
        discord_send=discord_send,
    )

    _guard_sendorder(config)
    limit_px = _limit_entry_price(payload) or entry_price
    track.entry_price = limit_px
    track.client_order_id = make_client_order_id(symbol, suffix="entry")
    order_payload = build_entry_sendorder_payload(
        symbol=symbol,
        exchange=1,
        limit_price=float(limit_px or 0),
        quantity=LOT_SIZE,
        client_order_id=track.client_order_id,
        linked_paper_trade_id=paper_id,
        timeout_sec=session.entry_timeout_sec,
    )
    track.last_payload = order_payload

    _transition(track, AdapterState.ORDER_PREPARED)
    session.notifier.emit(
        EVENT_ORDER_PREPARED,
        {
            "symbol": symbol,
            "side": "ENTRY",
            "timestamp": timestamp,
            "state_from": AdapterState.CAPITAL_CHECK_PASS.value,
            "state_to": AdapterState.ORDER_PREPARED.value,
            "qty": LOT_SIZE,
            "price": limit_px,
            "payload": order_payload,
            "linked_paper_trade_id": paper_id,
        },
        writer=writer,
        config=config,
        discord_send=discord_send,
    )

    _transition(track, AdapterState.ORDER_WOULD_SEND)
    session.would_send_count += 1
    session.notifier.emit(
        EVENT_ORDER_WOULD_SEND,
        {
            "symbol": symbol,
            "side": "ENTRY",
            "timestamp": timestamp,
            "state_from": AdapterState.ORDER_PREPARED.value,
            "state_to": AdapterState.ORDER_WOULD_SEND.value,
            "qty": LOT_SIZE,
            "price": limit_px,
            "payload": order_payload,
            "detail": "sendorder blocked — order_enabled=false",
            "linked_paper_trade_id": paper_id,
        },
        writer=writer,
        config=config,
        discord_send=discord_send,
    )

    for st, ev in (
        (AdapterState.ORDER_ACCEPTED_DRYRUN, EVENT_ORDER_ACCEPTED_DRYRUN),
        (AdapterState.FILLED_DRYRUN, EVENT_FILLED_DRYRUN),
        (AdapterState.OPEN_POSITION_DRYRUN, EVENT_OPEN_POSITION_DRYRUN),
    ):
        _transition(track, st)
        session.notifier.emit(
            ev,
            {
                "symbol": symbol,
                "timestamp": timestamp,
                "state_to": st.value,
                "qty": LOT_SIZE,
                "price": limit_px,
                "linked_paper_trade_id": paper_id,
            },
            writer=writer,
            config=config,
            discord_send=discord_send,
        )

    session.entry_count += 1
    return {"ok": True, "paper_trade_id": paper_id, "payload": order_payload}


def process_paper_exit(
    session: LiveOrderAdapterSession,
    *,
    symbol: str,
    context: Mapping[str, Any],
    timestamp: str,
    writer: Any,
    config: Any,
    discord_send: Optional[Any] = None,
) -> Optional[dict[str, Any]]:
    track = session.tracks.get(symbol)
    if track is None or track.state != AdapterState.OPEN_POSITION_DRYRUN:
        return None

    exit_reason = str(context.get("exit_reason") or context.get("reason") or "structural_exit")
    track.exit_reason = exit_reason
    _transition(track, AdapterState.EXIT_SIGNAL)
    session.notifier.emit(
        EVENT_EXIT_SIGNAL,
        {
            "symbol": symbol,
            "side": "EXIT",
            "timestamp": timestamp,
            "state_to": AdapterState.EXIT_SIGNAL.value,
            "detail": exit_reason,
            "linked_paper_trade_id": track.paper_trade_id,
        },
        writer=writer,
        config=config,
        discord_send=discord_send,
    )

    _guard_sendorder(config)
    order_type = _exit_order_type(exit_reason)
    try:
        exit_px = float(context.get("exit_price") or context.get("current_price") or 0)
    except (TypeError, ValueError):
        exit_px = 0.0
    limit_px = round(exit_px, 1) if exit_px > 0 and order_type != "market" else None
    cid = make_client_order_id(symbol, suffix="exit")
    order_payload = build_exit_sendorder_payload(
        symbol=symbol,
        exchange=1,
        exit_reason=exit_reason,
        limit_price=limit_px,
        client_order_id=cid,
        linked_paper_trade_id=track.paper_trade_id,
    )
    track.last_payload = order_payload

    _transition(track, AdapterState.EXIT_ORDER_PREPARED)
    session.notifier.emit(
        EVENT_EXIT_ORDER_PREPARED,
        {
            "symbol": symbol,
            "timestamp": timestamp,
            "state_to": AdapterState.EXIT_ORDER_PREPARED.value,
            "price": limit_px,
            "payload": order_payload,
            "detail": f"order_type={order_type}",
            "linked_paper_trade_id": track.paper_trade_id,
        },
        writer=writer,
        config=config,
        discord_send=discord_send,
    )

    _transition(track, AdapterState.EXIT_WOULD_SEND)
    session.would_send_count += 1
    session.notifier.emit(
        EVENT_EXIT_WOULD_SEND,
        {
            "symbol": symbol,
            "timestamp": timestamp,
            "state_to": AdapterState.EXIT_WOULD_SEND.value,
            "qty": track.quantity,
            "price": limit_px,
            "payload": order_payload,
            "linked_paper_trade_id": track.paper_trade_id,
        },
        writer=writer,
        config=config,
        discord_send=discord_send,
    )

    _transition(track, AdapterState.EXIT_FILLED_DRYRUN)
    session.notifier.emit(
        EVENT_EXIT_FILLED_DRYRUN,
        {
            "symbol": symbol,
            "timestamp": timestamp,
            "state_to": AdapterState.EXIT_FILLED_DRYRUN.value,
            "linked_paper_trade_id": track.paper_trade_id,
        },
        writer=writer,
        config=config,
        discord_send=discord_send,
    )

    _transition(track, AdapterState.CLOSED_DRYRUN)
    session.notifier.emit(
        EVENT_CLOSED_DRYRUN,
        {
            "symbol": symbol,
            "timestamp": timestamp,
            "state_to": AdapterState.CLOSED_DRYRUN.value,
            "linked_paper_trade_id": track.paper_trade_id,
        },
        writer=writer,
        config=config,
        discord_send=discord_send,
    )
    session.exit_count += 1
    return {"ok": True, "payload": order_payload, "order_type": order_type}


def _mock_capital_pass(payload: Mapping[str, Any], *, symbol: str) -> dict[str, Any]:
    px = float(_limit_entry_price(payload) or 0)
    snap = LiveCapitalSnapshot.mock(stock_wallet=1_500_000, margin_wallet=1_500_000)
    req = px * LOT_SIZE / 2.0 if px > 0 else 138400.0
    return {
        "symbol": symbol,
        "price": round(px, 2),
        "required_margin": round(req, 2),
        "available_margin": snap.available_margin,
        "buying_power": snap.buying_power,
        "margin_wallet": snap.margin_wallet,
        "cap_used": 0,
        "cap_limit": 5,
        "can_enter": True,
        "reject_reason": "",
        "dry_run": True,
    }


def adapter_summary_fields(session: Optional[LiveOrderAdapterSession]) -> dict[str, Any]:
    if session is None:
        return {"live_order_adapter_enabled": False}
    out = {
        "live_order_adapter_enabled": True,
        "live_order_adapter_entry_count": session.entry_count,
        "live_order_adapter_exit_count": session.exit_count,
        "live_order_adapter_capital_blocks": session.capital_block_count,
        "live_order_adapter_would_send_count": session.would_send_count,
        "live_order_adapter_safe_stop": session.safe_stop,
    }
    out.update(notifier_summary_fields(session.notifier))
    return out


def run_demo_scenarios(*, writer: Any, config: Any) -> list[dict[str, Any]]:
    """Mock demos for phase594 reports."""
    rows: list[dict[str, Any]] = []
    session = LiveOrderAdapterSession(position_cap=2)

    pass_cfg = config
    block_snap = LiveCapitalSnapshot.mock(stock_wallet=20_000, margin_wallet=0)
    from small_paper.live_capital_manager import evaluate_entry_capital

    # ENTRY pass
    r1 = process_paper_entry(
        session,
        symbol="6981.T",
        trade={"entry_time": "2026-06-18T09:05:00+09:00"},
        payload={"AskPrice": 2768.0},
        timestamp="2026-06-18T09:05:00+09:00",
        writer=writer,
        config=pass_cfg,
    )
    rows.append({"scenario": "entry_pass", **{k: r1.get(k) for k in ("ok", "blocked", "reason")}})

    # ENTRY block via mock capital
    block_session = LiveOrderAdapterSession(position_cap=2)
    cap_block = evaluate_entry_capital(
        block_snap, symbol="6981.T", entry_price=2768.0, cap_limit=2
    )
    block_session.notifier.emit(
        EVENT_CAPITAL_CHECK_BLOCK,
        {**cap_block, "symbol": "6981.T", "side": "ENTRY", "timestamp": "2026-06-18T09:06:00+09:00"},
        writer=writer,
        config=pass_cfg,
    )
    rows.append({"scenario": "entry_block", "ok": False, "reason": cap_block.get("reject_reason")})

    # EXIT stop
    exit_session = LiveOrderAdapterSession()
    exit_session.tracks["6981.T"] = SymbolAdapterTrack(
        symbol="6981.T",
        state=AdapterState.OPEN_POSITION_DRYRUN,
        paper_trade_id="6981.T:demo",
        entry_price=2768.0,
    )
    r3 = process_paper_exit(
        exit_session,
        symbol="6981.T",
        context={"exit_reason": "hard_stop", "exit_price": 2700.0},
        timestamp="2026-06-18T09:30:00+09:00",
        writer=writer,
        config=pass_cfg,
    )
    rows.append({"scenario": "exit_stop", "order_type": (r3 or {}).get("order_type")})

    # SAFE_STOP
    ss = LiveOrderAdapterSession()
    ss.trigger_safe_stop("position_mismatch")
    ss.notifier.emit(
        EVENT_SAFE_STOP,
        {"reason": "position_mismatch", "timestamp": "2026-06-18T10:00:00+09:00"},
        writer=writer,
        config=pass_cfg,
    )
    rows.append({"scenario": "safe_stop", "reason": "position_mismatch"})
    return rows
