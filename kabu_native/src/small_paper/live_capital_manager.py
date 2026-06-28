"""
Phase593: Live capital manager — wallet/margin/CAP checks before live entry.

Logging-only during dry-run. Never blocks paper ENTRY/EXIT or sends orders.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from small_paper.live_order_dry_run_adapter import LOT_SIZE, MARGIN_LEVERAGE, _limit_entry_price

JST = ZoneInfo("Asia/Tokyo")

CASH_MARGIN_NEW = 2
SIDE_BUY = "2"

CAPITAL_CHECK_FIELDS = (
    "timestamp",
    "symbol",
    "price",
    "required_margin",
    "margin_wallet",
    "stock_wallet",
    "current_equity",
    "buying_power",
    "available_margin",
    "gross_position_value",
    "cap_used",
    "cap_limit",
    "pending_orders",
    "can_enter",
    "reject_reason",
    "dry_run",
    "order_enabled",
    "live_trading_enabled",
    "linked_paper_trade_id",
    "check_step_failed",
)


def capital_manager_enabled(config: Any) -> bool:
    if bool(getattr(config, "live_trading_enabled", False)):
        return False
    if bool(getattr(config, "order_enabled", False)):
        return False
    return bool(getattr(config, "live_capital_check_enabled", True))


def _iso_now() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _float(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def symbol_to_kabu_code(symbol: str) -> str:
    return str(symbol or "").replace(".T", "").strip()


def compute_required_margin(
    entry_price: float,
    *,
    leverage: float = MARGIN_LEVERAGE,
    lot_size: int = LOT_SIZE,
) -> float:
    if entry_price <= 0 or leverage <= 0:
        return 0.0
    return entry_price * lot_size / leverage


def compute_buying_power(*, equity: float, gross: float, leverage: float) -> float:
    return max(0.0, equity * leverage - gross)


def compute_gross_position_value(positions: Sequence[Mapping[str, Any]]) -> float:
    gross = 0.0
    for pos in positions:
        qty = _float(pos.get("LeavesQty")) or _float(pos.get("Qty")) or 0.0
        if qty <= 0:
            continue
        px = (
            _float(pos.get("CurrentPrice"))
            or _float(pos.get("Price"))
            or _float(pos.get("EntryPrice"))
            or 0.0
        )
        gross += abs(qty) * px
    return gross


def _pending_entry_orders(orders: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for order in orders:
        side = str(order.get("Side") or "")
        cash_margin = order.get("CashMargin")
        state = str(order.get("State") or order.get("OrderState") or "")
        if side != SIDE_BUY:
            continue
        if cash_margin not in (CASH_MARGIN_NEW, "2", 2):
            continue
        if state and state not in ("1", "2", "3", "4", "5", "1.0", "2.0", "3.0", "4.0", "5.0"):
            continue
        rows.append(dict(order))
    return rows


def count_cap_slots(
    positions: Sequence[Mapping[str, Any]],
    orders: Sequence[Mapping[str, Any]],
) -> tuple[int, int, int]:
    open_positions = sum(
        1
        for p in positions
        if (_float(p.get("LeavesQty")) or _float(p.get("Qty")) or 0.0) > 0
    )
    pending = _pending_entry_orders(orders)
    pending_slots = len(pending)
    return open_positions + pending_slots, open_positions, pending_slots


def open_symbols(
    positions: Sequence[Mapping[str, Any]],
    orders: Sequence[Mapping[str, Any]],
) -> set[str]:
    syms: set[str] = set()
    for pos in positions:
        qty = _float(pos.get("LeavesQty")) or _float(pos.get("Qty")) or 0.0
        if qty <= 0:
            continue
        code = str(pos.get("Symbol") or "")
        if code:
            syms.add(code)
    for order in _pending_entry_orders(orders):
        code = str(order.get("Symbol") or "")
        if code:
            syms.add(code)
    return syms


@dataclass
class LiveCapitalSnapshot:
    stock_wallet: float = 0.0
    margin_wallet: float = 0.0
    current_equity: float = 0.0
    gross_position_value: float = 0.0
    buying_power: float = 0.0
    available_margin: float = 0.0
    cap_used: int = 0
    open_positions: int = 0
    pending_orders: int = 0
    positions: list[dict[str, Any]] = field(default_factory=list)
    orders: list[dict[str, Any]] = field(default_factory=list)
    api_online: bool = False
    positions_sync_ok: bool = False
    fetch_error: str = ""
    fetched_at: str = ""

    @classmethod
    def from_api(
        cls,
        *,
        wallet_cash: Mapping[str, Any],
        wallet_margin: Mapping[str, Any],
        positions: Sequence[Mapping[str, Any]],
        orders: Sequence[Mapping[str, Any]],
        leverage: float = MARGIN_LEVERAGE,
    ) -> LiveCapitalSnapshot:
        stock = _float(wallet_cash.get("StockAccountWallet")) or _float(wallet_cash.get("Cash")) or 0.0
        margin = (
            _float(wallet_margin.get("MarginAccountWallet"))
            or _float(wallet_margin.get("MarginAmount"))
            or 0.0
        )
        gross = compute_gross_position_value(positions)
        equity = stock + margin
        buying_power = compute_buying_power(equity=equity, gross=gross, leverage=leverage)
        available = min(buying_power, margin) if margin > 0 else buying_power
        cap_used, open_count, pending_count = count_cap_slots(positions, orders)
        return cls(
            stock_wallet=round(stock, 2),
            margin_wallet=round(margin, 2),
            current_equity=round(equity, 2),
            gross_position_value=round(gross, 2),
            buying_power=round(buying_power, 2),
            available_margin=round(available, 2),
            cap_used=cap_used,
            open_positions=open_count,
            pending_orders=pending_count,
            positions=[dict(p) for p in positions],
            orders=[dict(o) for o in orders],
            api_online=True,
            positions_sync_ok=True,
            fetched_at=_iso_now(),
        )

    @classmethod
    def mock(
        cls,
        *,
        stock_wallet: float,
        margin_wallet: float = 0.0,
        leverage: float = MARGIN_LEVERAGE,
        gross: float = 0.0,
        cap_used: int = 0,
        pending_orders: int = 0,
    ) -> LiveCapitalSnapshot:
        equity = stock_wallet + margin_wallet
        buying_power = compute_buying_power(equity=equity, gross=gross, leverage=leverage)
        available = min(buying_power, margin_wallet) if margin_wallet > 0 else buying_power
        return cls(
            stock_wallet=round(stock_wallet, 2),
            margin_wallet=round(margin_wallet, 2),
            current_equity=round(equity, 2),
            gross_position_value=round(gross, 2),
            buying_power=round(buying_power, 2),
            available_margin=round(available, 2),
            cap_used=cap_used,
            open_positions=max(0, cap_used - pending_orders),
            pending_orders=pending_orders,
            api_online=True,
            positions_sync_ok=True,
            fetched_at=_iso_now(),
        )


def fetch_live_capital_snapshot(
    client: Any,
    *,
    token: str,
    leverage: float = MARGIN_LEVERAGE,
) -> LiveCapitalSnapshot:
    try:
        wallet_cash, _ = client.get_wallet_cash(token=token)
        wallet_margin, _ = client.get_wallet_margin(token=token)
        positions, _ = client.get_positions(token=token)
        orders, _ = client.get_orders(token=token)
        return LiveCapitalSnapshot.from_api(
            wallet_cash=wallet_cash,
            wallet_margin=wallet_margin,
            positions=positions,
            orders=orders,
            leverage=leverage,
        )
    except Exception as e:
        return LiveCapitalSnapshot(api_online=False, positions_sync_ok=False, fetch_error=str(e))


def evaluate_entry_capital(
    snapshot: LiveCapitalSnapshot,
    *,
    symbol: str,
    entry_price: float,
    cap_limit: int,
    leverage: float = MARGIN_LEVERAGE,
    kill_switch_active: bool = False,
    daily_loss_blocked: bool = False,
) -> dict[str, Any]:
    req = compute_required_margin(entry_price, leverage=leverage)
    sym_code = symbol_to_kabu_code(symbol)
    syms = open_symbols(snapshot.positions, snapshot.orders)

    row: dict[str, Any] = {
        "timestamp": _iso_now(),
        "symbol": symbol,
        "price": round(entry_price, 2),
        "required_margin": round(req, 2),
        "margin_wallet": snapshot.margin_wallet,
        "stock_wallet": snapshot.stock_wallet,
        "current_equity": snapshot.current_equity,
        "buying_power": snapshot.buying_power,
        "available_margin": snapshot.available_margin,
        "gross_position_value": snapshot.gross_position_value,
        "cap_used": snapshot.cap_used,
        "cap_limit": cap_limit,
        "pending_orders": snapshot.pending_orders,
        "can_enter": False,
        "reject_reason": "",
        "check_step_failed": "",
        "dry_run": True,
    }

    if kill_switch_active:
        row["reject_reason"] = "kill_switch_active"
        row["check_step_failed"] = "kill_switch"
        return row
    if not snapshot.api_online:
        row["reject_reason"] = "api_offline"
        row["check_step_failed"] = "api_online"
        return row
    if not snapshot.positions_sync_ok:
        row["reject_reason"] = "positions_sync_failed"
        row["check_step_failed"] = "positions_sync"
        return row
    if sym_code and sym_code in syms:
        row["reject_reason"] = "duplicate_symbol"
        row["check_step_failed"] = "duplicate_symbol"
        return row
    if snapshot.cap_used >= cap_limit:
        row["reject_reason"] = "max_concurrent_positions"
        row["check_step_failed"] = "cap_check"
        return row
    if req <= 0:
        row["reject_reason"] = "invalid_price"
        row["check_step_failed"] = "required_margin"
        return row
    if snapshot.margin_wallet < req:
        row["reject_reason"] = "insufficient_margin_or_buying_power"
        row["check_step_failed"] = "required_margin"
        return row
    if snapshot.buying_power < req:
        row["reject_reason"] = "insufficient_buying_power"
        row["check_step_failed"] = "buying_power"
        return row
    if daily_loss_blocked:
        row["reject_reason"] = "daily_loss_limit"
        row["check_step_failed"] = "daily_loss_limit"
        return row

    row["can_enter"] = True
    row["reject_reason"] = ""
    return row


def max_affordable_slots(
    snapshot: LiveCapitalSnapshot,
    *,
    entry_price: float,
    leverage: float = MARGIN_LEVERAGE,
) -> int:
    req = compute_required_margin(entry_price, leverage=leverage)
    if req <= 0:
        return 0
    by_margin = int(snapshot.margin_wallet // req) if snapshot.margin_wallet > 0 else 0
    by_bp = int(snapshot.buying_power // req)
    return max(0, min(by_margin, by_bp) if snapshot.margin_wallet > 0 else by_bp)


def operational_cap_ok(
    snapshot: LiveCapitalSnapshot,
    *,
    entry_price: float,
    cap_limit: int,
    leverage: float = MARGIN_LEVERAGE,
) -> bool:
    return max_affordable_slots(snapshot, entry_price=entry_price, leverage=leverage) >= cap_limit


def min_equity_for_cap(
    *,
    cap: int,
    entry_price: float,
    leverage: float = MARGIN_LEVERAGE,
    lot_size: int = LOT_SIZE,
) -> dict[str, Any]:
    req = compute_required_margin(entry_price, leverage=leverage, lot_size=lot_size)
    total_required = req * cap
    min_equity_buying_power = total_required / leverage if leverage > 0 else total_required
    return {
        "cap": cap,
        "entry_price": round(entry_price, 2),
        "required_margin_per_slot": round(req, 2),
        "total_required_margin": round(total_required, 2),
        "min_equity_for_buying_power": round(min_equity_buying_power, 2),
        "min_margin_wallet": round(total_required, 2),
    }


@dataclass
class LiveCapitalManagerSession:
    position_cap: int
    leverage: float = MARGIN_LEVERAGE
    kill_switch_path: Optional[Path] = None
    api_online: bool = False
    startup_snapshot: Optional[LiveCapitalSnapshot] = None
    check_rows: list[dict[str, Any]] = field(default_factory=list)
    reject_rows: list[dict[str, Any]] = field(default_factory=list)
    check_count: int = 0
    can_enter_count: int = 0
    cap_reject_count: int = 0
    margin_reject_count: int = 0
    duplicate_reject_count: int = 0
    last_snapshot: Optional[LiveCapitalSnapshot] = None
    last_fetch_error: str = ""

    def kill_switch_active(self, repo_root: Optional[Path] = None) -> bool:
        if self.kill_switch_path and self.kill_switch_path.is_file():
            return True
        if repo_root is not None:
            ks = repo_root / "kabu_native" / "configs" / "live_trading_kill_switch.flag"
            return ks.is_file()
        return False


def capital_summary_fields(session: Optional[LiveCapitalManagerSession]) -> dict[str, Any]:
    if session is None:
        return {"live_capital_check_enabled": False}
    return {
        "live_capital_check_enabled": True,
        "live_capital_check_count": session.check_count,
        "live_capital_can_enter_count": session.can_enter_count,
        "live_capital_cap_reject_count": session.cap_reject_count,
        "live_capital_margin_reject_count": session.margin_reject_count,
        "live_capital_duplicate_reject_count": session.duplicate_reject_count,
        "live_capital_api_online": session.api_online,
        "live_capital_last_margin_wallet": getattr(session.last_snapshot, "margin_wallet", None),
    }


def refresh_snapshot(
    session: LiveCapitalManagerSession,
    *,
    client: Any,
    token: str,
) -> LiveCapitalSnapshot:
    snap = fetch_live_capital_snapshot(client, token=token, leverage=session.leverage)
    session.last_snapshot = snap
    session.api_online = snap.api_online
    session.last_fetch_error = snap.fetch_error
    if session.startup_snapshot is None and snap.api_online:
        session.startup_snapshot = snap
    return snap


def check_entry_capital_on_paper_accept(
    session: LiveCapitalManagerSession,
    *,
    symbol: str,
    trade: Mapping[str, Any],
    payload: Mapping[str, Any],
    writer: Any,
    config: Any,
    client: Any,
    token: str,
    repo_root: Optional[Path] = None,
    day_pnl_pct: Optional[float] = None,
    linked_paper_trade_id: str = "",
) -> dict[str, Any]:
    if not capital_manager_enabled(config):
        return {"skipped": True}

    cap_limit = int(getattr(config, "max_concurrent_positions", session.position_cap))
    try:
        entry_price = _limit_entry_price(payload)
    except Exception:
        entry_price = _float(trade.get("entry_price")) or _float(payload.get("CurrentPrice")) or 0.0

    snap = refresh_snapshot(session, client=client, token=token)
    daily_blocked = False
    if day_pnl_pct is not None and getattr(config, "daily_loss_guard_enabled", True):
        threshold = float(getattr(config, "daily_loss_guard_pct", -2.5))
        daily_blocked = day_pnl_pct <= threshold

    row = evaluate_entry_capital(
        snap,
        symbol=symbol,
        entry_price=float(entry_price or 0.0),
        cap_limit=cap_limit,
        leverage=session.leverage,
        kill_switch_active=session.kill_switch_active(repo_root),
        daily_loss_blocked=daily_blocked,
    )
    row["order_enabled"] = bool(getattr(config, "order_enabled", False))
    row["live_trading_enabled"] = bool(getattr(config, "live_trading_enabled", False))
    row["linked_paper_trade_id"] = linked_paper_trade_id

    session.check_count += 1
    session.check_rows.append(row)
    if row.get("can_enter"):
        session.can_enter_count += 1
    else:
        session.reject_rows.append(row)
        reason = str(row.get("reject_reason") or "")
        if reason == "max_concurrent_positions":
            session.cap_reject_count += 1
        elif reason in ("insufficient_margin_or_buying_power", "insufficient_buying_power"):
            session.margin_reject_count += 1
        elif reason == "duplicate_symbol":
            session.duplicate_reject_count += 1

    try:
        writer.append_live_capital_check(row)
    except Exception:
        pass
    return row
