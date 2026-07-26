"""
Phase335-lite / Phase335: Realtime board adaptive EXIT shadow (no actual EXIT changes).

Phase335-lite: watch logging during holds.
Phase335: one virtual shadow EXIT per position vs actual Board Dynamic Trailing.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.research_exit_criteria import _as_float
from storage.intraday_recorder import parse_kabu_time
from universe.filters import calc_spread_bps

JST = ZoneInfo("Asia/Tokyo")

# Lite watch thresholds (bid/ask imbalance delta vs entry).
IMB_DETERIORATION_DELTA = 0.05
IMB_COLLAPSE_DELTA = 0.08
IMB_MAINTAIN_EPSILON = 0.001

# Phase335 shadow EXIT thresholds (board_imbalance_delta = current - entry).
SHADOW_COLLAPSE_DELTA = -0.08
SHADOW_PROFIT_PROTECT_DELTA = -0.05
SHADOW_LOSS_ACCELERATION_PNL_PCT = -0.4

SHADOW_EXIT_REASONS = (
    "loss_acceleration_exit",
    "board_collapse_profit_exit",
    "profit_protect_exit",
)

EXTEND_REASON = "board_strength_hold_extend"

WATCH_TYPES = (
    "board_collapse_watch",
    "profit_protect_watch",
    "loss_acceleration_watch",
    "board_strength_hold_watch",
)

TICK_FIELD_KEYS = (
    "symbol",
    "position_id",
    "entry_time",
    "tick_time",
    "current_price",
    "best_bid",
    "best_ask",
    "bid_qty",
    "ask_qty",
    "spread",
    "entry_imbalance_percentile",
    "entry_order_book_imbalance",
    "entry_bid_ask_imbalance",
    "current_board_imbalance",
    "board_imbalance_delta",
    "mfe_pct",
    "current_pnl_pct",
    "shadow_exit_reason",
    "actual_exit_reason",
    "actual_exit_time",
    "actual_pnl_yen_100",
    "board_collapse_watch",
    "profit_protect_watch",
    "loss_acceleration_watch",
    "board_strength_hold_watch",
)

EVENT_FIELD_KEYS = (
    "symbol",
    "position_id",
    "entry_time",
    "tick_time",
    "watch_type",
    "current_price",
    "current_board_imbalance",
    "board_imbalance_delta",
    "mfe_pct",
    "current_pnl_pct",
    "entry_bid_ask_imbalance",
    "entry_imbalance_percentile",
    "actual_exit_reason",
    "actual_exit_time",
    "actual_pnl_yen_100",
    "seconds_before_actual_exit",
)

TRADE_FIELD_KEYS = (
    "symbol",
    "position_id",
    "entry_time",
    "entry_price",
    "entry_bid_ask_imbalance",
    "entry_imbalance_percentile",
    "shadow_exit_reason",
    "shadow_exit_time",
    "shadow_exit_price",
    "shadow_pnl_pct",
    "shadow_pnl_yen_100",
    "actual_exit_reason",
    "actual_exit_time",
    "actual_exit_price",
    "actual_pnl_pct",
    "actual_pnl_yen_100",
    "actual_vs_realtime_board_delta_yen",
    "realtime_board_vs_actual_delta_yen",
    "board_strength_hold_extend",
    "no_shadow_exit",
)

DELTA_FIELD_KEYS = (
    "symbol",
    "position_id",
    "shadow_exit_reason",
    "actual_exit_reason",
    "shadow_pnl_yen_100",
    "actual_pnl_yen_100",
    "realtime_board_vs_actual_delta_yen",
    "actual_vs_realtime_board_delta_yen",
    "shadow_exit_before_actual",
    "actual_exit_before_shadow",
)


def calc_bid_ask_imbalance(payload: Mapping[str, Any]) -> float | None:
    """PUSH top-of-book imbalance (canonical bid share; legacy = labeled BidQty share)."""
    from small_paper.canonical_board import top_imbalance_for_mode

    return top_imbalance_for_mode(payload)


def _tick_time_from_payload(payload: Mapping[str, Any]) -> datetime:
    for key in ("CurrentPriceTime", "BidTime", "AskTime"):
        raw = payload.get(key)
        if raw:
            return parse_kabu_time(raw, fallback=datetime.now(JST))
    return datetime.now(JST)


def _best_bid_ask(payload: Mapping[str, Any]) -> tuple[Optional[float], Optional[float]]:
    from small_paper.canonical_board import best_bid_ask_for_mode

    return best_bid_ask_for_mode(payload)


def _bid_ask_qty(payload: Mapping[str, Any]) -> tuple[Optional[float], Optional[float]]:
    from small_paper.canonical_board import bid_ask_qty_for_mode

    return bid_ask_qty_for_mode(payload)


def _has_bid_ask_qty(payload: Mapping[str, Any]) -> bool:
    bid, ask = _bid_ask_qty(payload)
    return bid is not None and ask is not None


def _pnl_pct(entry_price: float, price: float) -> float:
    if entry_price <= 0:
        return 0.0
    return round((price - entry_price) / entry_price * 100.0, 4)


def _imbalance_deteriorated(
    entry_imb: Optional[float],
    current_imb: Optional[float],
    *,
    large: bool = False,
) -> bool:
    if entry_imb is None or current_imb is None:
        return False
    delta = IMB_COLLAPSE_DELTA if large else IMB_DETERIORATION_DELTA
    return float(current_imb) < float(entry_imb) - delta


def _imbalance_maintained_or_improved(
    entry_imb: Optional[float],
    current_imb: Optional[float],
) -> bool:
    if entry_imb is None or current_imb is None:
        return False
    return float(current_imb) >= float(entry_imb) - IMB_MAINTAIN_EPSILON


def evaluate_shadow_watches(
    *,
    entry_bid_ask_imbalance: Optional[float],
    current_board_imbalance: Optional[float],
    mfe_pct: float,
    current_pnl_pct: float,
) -> dict[str, bool]:
    deteriorated = _imbalance_deteriorated(
        entry_bid_ask_imbalance, current_board_imbalance, large=False
    )
    collapsed = _imbalance_deteriorated(
        entry_bid_ask_imbalance, current_board_imbalance, large=True
    )
    maintained = _imbalance_maintained_or_improved(
        entry_bid_ask_imbalance, current_board_imbalance
    )
    return {
        "board_collapse_watch": collapsed and current_pnl_pct > 0.0,
        "profit_protect_watch": mfe_pct >= 0.6 and deteriorated,
        "loss_acceleration_watch": current_pnl_pct < 0.0 and deteriorated,
        "board_strength_hold_watch": mfe_pct >= 1.0 and maintained,
    }


def evaluate_shadow_exit_reason(
    *,
    board_imbalance_delta: Optional[float],
    mfe_pct: float,
    current_pnl_pct: float,
) -> Optional[str]:
    """First matching shadow EXIT reason by priority (extend excluded)."""
    if board_imbalance_delta is None:
        return None
    delta = float(board_imbalance_delta)
    if (
        current_pnl_pct < 0.0
        and delta <= SHADOW_COLLAPSE_DELTA
        and current_pnl_pct <= SHADOW_LOSS_ACCELERATION_PNL_PCT
    ):
        return "loss_acceleration_exit"
    if current_pnl_pct > 0.0 and delta <= SHADOW_COLLAPSE_DELTA:
        return "board_collapse_profit_exit"
    if mfe_pct >= 0.6 and current_pnl_pct > 0.0 and delta <= SHADOW_PROFIT_PROTECT_DELTA:
        return "profit_protect_exit"
    return None


def evaluate_extend_candidate(
    *,
    board_imbalance_delta: Optional[float],
    mfe_pct: float,
) -> bool:
    if board_imbalance_delta is None:
        return False
    return mfe_pct >= 1.0 and float(board_imbalance_delta) >= 0.0


def make_position_id(symbol: str, entry_time: datetime) -> str:
    stamp = entry_time.strftime("%Y%m%dT%H%M%S%f")
    return f"{symbol}_{stamp}"


def _profit_factor(yens: Sequence[float]) -> Optional[float]:
    wins = sum(y for y in yens if y > 0)
    losses = abs(sum(y for y in yens if y < 0))
    if losses <= 0:
        return None
    return round(wins / losses, 4)


@dataclass
class _ShadowExitState:
    entry_bid_ask_imbalance: Optional[float] = None
    shadow_exit_reason: str = ""
    shadow_exit_time: str = ""
    shadow_exit_ts: float = 0.0
    shadow_exit_price: Optional[float] = None
    extend_recorded: bool = False


@dataclass
class _PositionExitInfo:
    actual_exit_reason: str = ""
    actual_exit_time: str = ""
    actual_exit_price: Optional[float] = None
    actual_pnl_pct: Optional[float] = None
    actual_pnl_yen_100: Optional[float] = None
    actual_exit_ts: float = 0.0


@dataclass
class _PositionTradeRecord:
    symbol: str
    position_id: str
    entry_time: str
    entry_price: float
    entry_bid_ask_imbalance: Optional[float] = None
    entry_imbalance_percentile: Optional[float] = None
    shadow_exit_reason: str = ""
    shadow_exit_time: str = ""
    shadow_exit_price: Optional[float] = None
    shadow_pnl_pct: Optional[float] = None
    shadow_pnl_yen_100: Optional[float] = None
    board_strength_hold_extend: bool = False
    actual_exit_reason: str = ""
    actual_exit_time: str = ""
    actual_exit_price: Optional[float] = None
    actual_pnl_pct: Optional[float] = None
    actual_pnl_yen_100: Optional[float] = None
    shadow_exit_ts: float = 0.0
    actual_exit_ts: float = 0.0

    def to_trade_row(self) -> dict[str, Any]:
        from replay.pnl_yen import compute_pnl_yen_100

        no_shadow = not self.shadow_exit_reason
        shadow_yen = self.shadow_pnl_yen_100
        actual_yen = self.actual_pnl_yen_100
        if no_shadow and self.actual_exit_price is not None:
            shadow_yen = round(
                compute_pnl_yen_100(self.entry_price, float(self.actual_exit_price)), 2
            )
            self.shadow_pnl_yen_100 = shadow_yen
            self.shadow_pnl_pct = self.actual_pnl_pct
        shadow_yen_f = float(shadow_yen or 0.0)
        actual_yen_f = float(actual_yen or 0.0)
        return {
            "symbol": self.symbol,
            "position_id": self.position_id,
            "entry_time": self.entry_time,
            "entry_price": self.entry_price,
            "entry_bid_ask_imbalance": self.entry_bid_ask_imbalance,
            "entry_imbalance_percentile": self.entry_imbalance_percentile,
            "shadow_exit_reason": self.shadow_exit_reason,
            "shadow_exit_time": self.shadow_exit_time,
            "shadow_exit_price": self.shadow_exit_price,
            "shadow_pnl_pct": self.shadow_pnl_pct,
            "shadow_pnl_yen_100": shadow_yen,
            "actual_exit_reason": self.actual_exit_reason,
            "actual_exit_time": self.actual_exit_time,
            "actual_exit_price": self.actual_exit_price,
            "actual_pnl_pct": self.actual_pnl_pct,
            "actual_pnl_yen_100": actual_yen,
            "actual_vs_realtime_board_delta_yen": round(actual_yen_f - shadow_yen_f, 2),
            "realtime_board_vs_actual_delta_yen": round(shadow_yen_f - actual_yen_f, 2),
            "board_strength_hold_extend": self.board_strength_hold_extend,
            "no_shadow_exit": no_shadow,
        }

    def to_delta_row(self) -> dict[str, Any]:
        row = self.to_trade_row()
        shadow_before = False
        actual_before = False
        if self.shadow_exit_ts > 0 and self.actual_exit_ts > 0:
            shadow_before = self.shadow_exit_ts < self.actual_exit_ts
            actual_before = self.actual_exit_ts < self.shadow_exit_ts
        return {
            "symbol": row["symbol"],
            "position_id": row["position_id"],
            "shadow_exit_reason": row["shadow_exit_reason"],
            "actual_exit_reason": row["actual_exit_reason"],
            "shadow_pnl_yen_100": row["shadow_pnl_yen_100"],
            "actual_pnl_yen_100": row["actual_pnl_yen_100"],
            "realtime_board_vs_actual_delta_yen": row["realtime_board_vs_actual_delta_yen"],
            "actual_vs_realtime_board_delta_yen": row["actual_vs_realtime_board_delta_yen"],
            "shadow_exit_before_actual": shadow_before,
            "actual_exit_before_shadow": actual_before,
        }


@dataclass
class RealtimeBoardExitShadowLogger:
    """Accumulates board shadow ticks/events and Phase335 virtual exits."""

    board_tick_received_count: int = 0
    board_tick_with_bid_ask_qty_count: int = 0
    symbol_board_ticks: Counter[str] = field(default_factory=Counter)
    symbol_board_ticks_with_qty: Counter[str] = field(default_factory=Counter)
    holding_board_tick_count: int = 0
    holding_board_tick_with_qty_count: int = 0
    symbol_holding_ticks: Counter[str] = field(default_factory=Counter)
    symbol_holding_ticks_with_qty: Counter[str] = field(default_factory=Counter)
    watch_counts: Counter[str] = field(default_factory=Counter)
    shadow_exit_reason_counts: Counter[str] = field(default_factory=Counter)
    _ticks: list[dict[str, Any]] = field(default_factory=list)
    _events: list[dict[str, Any]] = field(default_factory=list)
    _position_exits: dict[str, _PositionExitInfo] = field(default_factory=dict)
    _position_symbols: dict[str, str] = field(default_factory=dict)
    _shadow_states: dict[str, _ShadowExitState] = field(default_factory=dict)
    _trades: dict[str, _PositionTradeRecord] = field(default_factory=dict)

    def register_position(
        self,
        *,
        position_id: str,
        symbol: str,
        entry_time: datetime,
        entry_price: float,
        payload: Mapping[str, Any],
        entry_shadow: Mapping[str, Any],
    ) -> None:
        entry_imb = calc_bid_ask_imbalance(payload)
        entry_imb_r = round(float(entry_imb), 6) if entry_imb is not None else None
        self._shadow_states[position_id] = _ShadowExitState(entry_bid_ask_imbalance=entry_imb_r)
        self._trades[position_id] = _PositionTradeRecord(
            symbol=symbol,
            position_id=position_id,
            entry_time=entry_time.isoformat(timespec="seconds"),
            entry_price=float(entry_price),
            entry_bid_ask_imbalance=entry_imb_r,
            entry_imbalance_percentile=_as_float(entry_shadow.get("entry_imbalance_percentile")),
        )
        self._position_symbols[position_id] = symbol

    def record_push_board_tick(self, *, symbol: str, payload: Mapping[str, Any]) -> None:
        self.board_tick_received_count += 1
        self.symbol_board_ticks[symbol] += 1
        if _has_bid_ask_qty(payload):
            self.board_tick_with_bid_ask_qty_count += 1
            self.symbol_board_ticks_with_qty[symbol] += 1

    def record_holding_tick(
        self,
        *,
        symbol: str,
        position_id: str,
        entry_time: datetime,
        payload: Mapping[str, Any],
        current_price: float,
        entry_price: float,
        mfe_pct: float,
        entry_shadow: Mapping[str, Any],
    ) -> None:
        if position_id not in self._trades:
            self.register_position(
                position_id=position_id,
                symbol=symbol,
                entry_time=entry_time,
                entry_price=entry_price,
                payload=payload,
                entry_shadow=entry_shadow,
            )

        self.holding_board_tick_count += 1
        self.symbol_holding_ticks[symbol] += 1
        has_qty = _has_bid_ask_qty(payload)
        if has_qty:
            self.holding_board_tick_with_qty_count += 1
            self.symbol_holding_ticks_with_qty[symbol] += 1

        state = self._shadow_states[position_id]
        entry_imb = state.entry_bid_ask_imbalance
        entry_pct = _as_float(entry_shadow.get("entry_imbalance_percentile"))
        entry_imb_legacy = _as_float(entry_shadow.get("entry_order_book_imbalance"))

        current_imb = calc_bid_ask_imbalance(payload)
        current_imb_r = round(float(current_imb), 6) if current_imb is not None else None
        imb_delta: Optional[float] = None
        if entry_imb is not None and current_imb_r is not None:
            imb_delta = round(current_imb_r - float(entry_imb), 6)

        pnl_pct = _pnl_pct(entry_price, current_price)
        bid, ask = _best_bid_ask(payload)
        bid_qty, ask_qty = _bid_ask_qty(payload)
        spread = calc_spread_bps(payload)
        tick_dt = _tick_time_from_payload(payload)

        if not state.shadow_exit_reason:
            exit_reason = evaluate_shadow_exit_reason(
                board_imbalance_delta=imb_delta,
                mfe_pct=float(mfe_pct),
                current_pnl_pct=float(pnl_pct),
            )
            if exit_reason:
                state.shadow_exit_reason = exit_reason
                state.shadow_exit_time = tick_dt.isoformat(timespec="seconds")
                state.shadow_exit_ts = tick_dt.timestamp()
                state.shadow_exit_price = round(float(current_price), 4)
                self.shadow_exit_reason_counts[exit_reason] += 1
                trade = self._trades[position_id]
                trade.shadow_exit_reason = exit_reason
                trade.shadow_exit_time = state.shadow_exit_time
                trade.shadow_exit_price = state.shadow_exit_price
                trade.shadow_exit_ts = state.shadow_exit_ts
                trade.shadow_pnl_pct = pnl_pct
                from replay.pnl_yen import compute_pnl_yen_100

                trade.shadow_pnl_yen_100 = round(
                    compute_pnl_yen_100(entry_price, float(current_price)), 2
                )

        if not state.extend_recorded and not state.shadow_exit_reason:
            if evaluate_extend_candidate(board_imbalance_delta=imb_delta, mfe_pct=float(mfe_pct)):
                state.extend_recorded = True
                self._trades[position_id].board_strength_hold_extend = True

        watches = evaluate_shadow_watches(
            entry_bid_ask_imbalance=entry_imb,
            current_board_imbalance=current_imb_r,
            mfe_pct=float(mfe_pct),
            current_pnl_pct=float(pnl_pct),
        )
        if not state.shadow_exit_reason:
            for wt, active in watches.items():
                if active:
                    self.watch_counts[wt] += 1

        exit_info = self._position_exits.get(position_id)
        row: dict[str, Any] = {
            "symbol": symbol,
            "position_id": position_id,
            "entry_time": entry_time.isoformat(timespec="seconds"),
            "tick_time": tick_dt.isoformat(timespec="seconds"),
            "current_price": round(float(current_price), 4),
            "best_bid": bid,
            "best_ask": ask,
            "bid_qty": bid_qty,
            "ask_qty": ask_qty,
            "spread": round(float(spread), 4) if spread is not None else None,
            "entry_imbalance_percentile": entry_pct,
            "entry_order_book_imbalance": entry_imb_legacy,
            "entry_bid_ask_imbalance": entry_imb,
            "current_board_imbalance": current_imb_r,
            "board_imbalance_delta": imb_delta,
            "mfe_pct": round(float(mfe_pct), 4),
            "current_pnl_pct": pnl_pct,
            "shadow_exit_reason": state.shadow_exit_reason,
            "actual_exit_reason": exit_info.actual_exit_reason if exit_info else "",
            "actual_exit_time": exit_info.actual_exit_time if exit_info else "",
            "actual_pnl_yen_100": exit_info.actual_pnl_yen_100 if exit_info else None,
            **{k: bool(watches.get(k)) for k in WATCH_TYPES},
        }
        self._ticks.append(row)

        if not state.shadow_exit_reason:
            exit_ts = exit_info.actual_exit_ts if exit_info else 0.0
            for wt, active in watches.items():
                if not active:
                    continue
                sec_before: Optional[float] = None
                if exit_ts > 0:
                    sec_before = round(max(0.0, exit_ts - tick_dt.timestamp()), 1)
                self._events.append(
                    {
                        "symbol": symbol,
                        "position_id": position_id,
                        "entry_time": entry_time.isoformat(timespec="seconds"),
                        "tick_time": tick_dt.isoformat(timespec="seconds"),
                        "watch_type": wt,
                        "current_price": row["current_price"],
                        "current_board_imbalance": current_imb_r,
                        "board_imbalance_delta": imb_delta,
                        "mfe_pct": row["mfe_pct"],
                        "current_pnl_pct": row["current_pnl_pct"],
                        "entry_bid_ask_imbalance": entry_imb,
                        "entry_imbalance_percentile": entry_pct,
                        "actual_exit_reason": exit_info.actual_exit_reason if exit_info else "",
                        "actual_exit_time": exit_info.actual_exit_time if exit_info else "",
                        "actual_pnl_yen_100": exit_info.actual_pnl_yen_100 if exit_info else None,
                        "seconds_before_actual_exit": sec_before,
                    }
                )

    def finalize_position(
        self,
        *,
        position_id: str,
        actual_exit_reason: str,
        actual_exit_time: datetime,
        actual_exit_price: float,
        entry_price: float,
    ) -> None:
        from replay.pnl_yen import compute_pnl_yen_100

        actual_pct = _pnl_pct(entry_price, actual_exit_price)
        yen = round(compute_pnl_yen_100(entry_price, actual_exit_price), 2)
        info = _PositionExitInfo(
            actual_exit_reason=actual_exit_reason,
            actual_exit_time=actual_exit_time.isoformat(timespec="seconds"),
            actual_exit_price=round(float(actual_exit_price), 4),
            actual_pnl_pct=actual_pct,
            actual_pnl_yen_100=yen,
            actual_exit_ts=actual_exit_time.timestamp(),
        )
        self._position_exits[position_id] = info
        trade = self._trades.get(position_id)
        if trade is not None:
            trade.actual_exit_reason = actual_exit_reason
            trade.actual_exit_time = info.actual_exit_time
            trade.actual_exit_price = info.actual_exit_price
            trade.actual_pnl_pct = actual_pct
            trade.actual_pnl_yen_100 = yen
            trade.actual_exit_ts = info.actual_exit_ts

        exit_ts = info.actual_exit_ts
        exit_time_s = info.actual_exit_time
        for row in self._ticks:
            if row.get("position_id") != position_id:
                continue
            row["actual_exit_reason"] = actual_exit_reason
            row["actual_exit_time"] = exit_time_s
            row["actual_pnl_yen_100"] = yen
        for ev in self._events:
            if ev.get("position_id") != position_id:
                continue
            ev["actual_exit_reason"] = actual_exit_reason
            ev["actual_exit_time"] = exit_time_s
            ev["actual_pnl_yen_100"] = yen
            tick_ts = parse_kabu_time(ev.get("tick_time"), fallback=actual_exit_time).timestamp()
            ev["seconds_before_actual_exit"] = round(max(0.0, exit_ts - tick_ts), 1)

    def build_lite_summary(self) -> dict[str, Any]:
        recv = self.board_tick_received_count
        recv_qty = self.board_tick_with_bid_ask_qty_count
        hold = self.holding_board_tick_count
        hold_qty = self.holding_board_tick_with_qty_count

        symbol_coverage: dict[str, dict[str, Any]] = {}
        all_syms = set(self.symbol_board_ticks) | set(self.symbol_holding_ticks)
        for sym in sorted(all_syms):
            bt = self.symbol_board_ticks.get(sym, 0)
            btq = self.symbol_board_ticks_with_qty.get(sym, 0)
            ht = self.symbol_holding_ticks.get(sym, 0)
            htq = self.symbol_holding_ticks_with_qty.get(sym, 0)
            symbol_coverage[sym] = {
                "board_tick_received_count": bt,
                "board_tick_with_bid_ask_qty_count": btq,
                "board_tick_bid_ask_qty_coverage_pct": round(100.0 * btq / max(1, bt), 2),
                "holding_board_tick_count": ht,
                "holding_board_tick_with_bid_ask_qty_count": htq,
                "holding_board_tick_coverage_pct": round(100.0 * htq / max(1, ht), 2)
                if ht
                else None,
            }

        pre_exit_dist: list[float] = []
        pre_exit_by_watch: dict[str, list[float]] = defaultdict(list)
        for ev in self._events:
            sec = ev.get("seconds_before_actual_exit")
            if sec is None or ev.get("actual_exit_time") in (None, ""):
                continue
            try:
                val = float(sec)
            except (TypeError, ValueError):
                continue
            pre_exit_dist.append(val)
            wt = str(ev.get("watch_type") or "")
            pre_exit_by_watch[wt].append(val)

        exits_with_prior_watch = 0
        for pos_id, info in self._position_exits.items():
            if info.actual_exit_ts <= 0:
                continue
            had_prior = any(
                ev.get("position_id") == pos_id
                and ev.get("actual_exit_time")
                and _as_float(ev.get("seconds_before_actual_exit")) is not None
                and float(ev["seconds_before_actual_exit"]) > 0
                for ev in self._events
            )
            if had_prior:
                exits_with_prior_watch += 1

        return {
            "phase": 335,
            "variant": "lite",
            "title": "realtime_board_adaptive_exit_shadow",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "board_tick_received_count": recv,
            "board_tick_with_bid_ask_qty_count": recv_qty,
            "board_tick_bid_ask_qty_coverage_pct": round(100.0 * recv_qty / max(1, recv), 2),
            "holding_board_tick_count": hold,
            "holding_board_tick_with_bid_ask_qty_count": hold_qty,
            "holding_board_tick_coverage_pct": round(100.0 * hold_qty / max(1, hold), 2),
            "board_collapse_watch_count": int(self.watch_counts.get("board_collapse_watch", 0)),
            "profit_protect_watch_count": int(self.watch_counts.get("profit_protect_watch", 0)),
            "loss_acceleration_watch_count": int(
                self.watch_counts.get("loss_acceleration_watch", 0)
            ),
            "board_strength_hold_watch_count": int(
                self.watch_counts.get("board_strength_hold_watch", 0)
            ),
            "shadow_event_count": len(self._events),
            "actual_exit_count": len(self._position_exits),
            "actual_exit_with_prior_shadow_watch_count": exits_with_prior_watch,
            "symbol_coverage": symbol_coverage,
            "seconds_before_actual_exit_distribution": _distribution_stats(pre_exit_dist),
            "seconds_before_actual_exit_by_watch_type": {
                wt: _distribution_stats(vals) for wt, vals in sorted(pre_exit_by_watch.items())
            },
            "note": "seconds_before_actual_exit unreliable in push-replay (wall-clock actual exit)",
        }

    def build_phase335_summary(self) -> dict[str, Any]:
        trade_rows = [t.to_trade_row() for t in self._trades.values()]
        delta_rows = [t.to_delta_row() for t in self._trades.values() if t.actual_exit_reason]

        actual_yens = [float(r["actual_pnl_yen_100"] or 0) for r in trade_rows]
        shadow_yens = [float(r["shadow_pnl_yen_100"] or 0) for r in trade_rows]
        total_actual = round(sum(actual_yens), 2)
        total_shadow = round(sum(shadow_yens), 2)
        total_delta = round(total_shadow - total_actual, 2)

        shadow_exit_count = sum(1 for r in trade_rows if not r.get("no_shadow_exit"))
        no_shadow_count = sum(1 for r in trade_rows if r.get("no_shadow_exit"))

        actual_stop = sum(1 for r in trade_rows if r.get("actual_exit_reason") == "stop_hit")
        shadow_loss_accel = int(self.shadow_exit_reason_counts.get("loss_acceleration_exit", 0))

        shadow_before = sum(1 for r in delta_rows if r.get("shadow_exit_before_actual"))
        actual_before = sum(1 for r in delta_rows if r.get("actual_exit_before_shadow"))

        return {
            "phase": 335,
            "variant": "full",
            "title": "realtime_board_adaptive_exit_shadow_replay",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "imbalance_source": "push_bid_ask_qty_only",
            "actual_exit_count": len(self._position_exits),
            "shadow_exit_count": shadow_exit_count,
            "shadow_exit_reason_counts": dict(self.shadow_exit_reason_counts),
            "actual_total_pnl_yen_100": total_actual,
            "shadow_total_pnl_yen_100": total_shadow,
            "realtime_board_vs_actual_total_delta_yen": total_delta,
            "actual_pf": _profit_factor(actual_yens),
            "shadow_pf": _profit_factor(shadow_yens),
            "actual_stop_hit_count": actual_stop,
            "shadow_loss_acceleration_exit_count": shadow_loss_accel,
            "actual_exit_before_shadow_count": actual_before,
            "shadow_exit_before_actual_count": shadow_before,
            "no_shadow_exit_count": no_shadow_count,
            "board_strength_hold_extend_count": sum(
                1 for t in self._trades.values() if t.board_strength_hold_extend
            ),
            "timing_note": (
                "shadow_exit_before_actual / actual_exit_before_shadow use tick-time vs "
                "actual exit timestamp; unreliable in push-replay — do not use for adoption"
            ),
        }

    def write_outputs(self, reports_dir: Path, *, day_stamp: str) -> dict[str, str]:
        reports_dir.mkdir(parents=True, exist_ok=True)
        trade_rows = [t.to_trade_row() for t in self._trades.values()]
        delta_rows = [t.to_delta_row() for t in self._trades.values() if t.actual_exit_reason]

        paths = {
            "ticks": reports_dir / f"phase335_lite_realtime_board_shadow_ticks_{day_stamp}.csv",
            "events": reports_dir
            / f"phase335_lite_realtime_board_shadow_events_{day_stamp}.csv",
            "lite_summary": reports_dir
            / f"phase335_lite_realtime_board_shadow_summary_{day_stamp}.json",
            "trades": reports_dir / f"phase335_realtime_board_shadow_trades_{day_stamp}.csv",
            "delta": reports_dir / f"phase335_realtime_board_shadow_delta_{day_stamp}.csv",
            "summary": reports_dir / f"phase335_realtime_board_shadow_summary_{day_stamp}.json",
        }
        _write_csv(paths["ticks"], TICK_FIELD_KEYS, self._ticks)
        _write_csv(paths["events"], EVENT_FIELD_KEYS, self._events)
        _write_csv(paths["trades"], TRADE_FIELD_KEYS, trade_rows)
        _write_csv(paths["delta"], DELTA_FIELD_KEYS, delta_rows)
        paths["lite_summary"].write_text(
            json.dumps(self.build_lite_summary(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths["summary"].write_text(
            json.dumps(self.build_phase335_summary(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {k: str(v) for k, v in paths.items()}


def _distribution_stats(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "median": None, "p90": None, "max": None}
    sorted_v = sorted(values)
    n = len(sorted_v)

    def _pct(p: float) -> float:
        idx = min(n - 1, max(0, int(p * n)))
        return round(sorted_v[idx], 1)

    mid = sorted_v[n // 2] if n % 2 == 1 else (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2.0
    return {
        "count": n,
        "min": round(sorted_v[0], 1),
        "median": round(mid, 1),
        "p90": _pct(0.9),
        "max": round(sorted_v[-1], 1),
    }


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})


def export_trade_rows(logger: RealtimeBoardExitShadowLogger) -> list[dict[str, Any]]:
    """Export finalized position trade rows (actual exit required)."""
    return [
        t.to_trade_row()
        for t in logger._trades.values()
        if t.actual_exit_reason
    ]


def export_delta_rows(logger: RealtimeBoardExitShadowLogger) -> list[dict[str, Any]]:
    return [t.to_delta_row() for t in logger._trades.values() if t.actual_exit_reason]


def write_phase335_lite_outputs(
    logger: Optional[RealtimeBoardExitShadowLogger],
    *,
    repo_root: Path,
    day_stamp: Optional[str] = None,
) -> Optional[dict[str, str]]:
    if logger is None:
        return None
    stamp = day_stamp or datetime.now(JST).strftime("%Y%m%d")
    reports_dir = repo_root / "kabu_native" / "results" / "reports"
    out = logger.write_outputs(reports_dir, day_stamp=stamp)
    if out:
        from storage.results_paths import dual_write_output_paths

        dual_write_output_paths(repo_root, stamp, {k: Path(v) for k, v in out.items()})
    return out
