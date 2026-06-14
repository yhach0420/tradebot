"""
Phase383: Realistic credit position sizing backtest (Stack C).

Refines Phase382 with strict 100-share-only scenarios and
buying_power = equity * leverage_limit - gross_position_value.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase377_daily_regime_breakdown import PRIMARY_STACK
from research.phase382_capital_constrained_backtest import (
    DAILY_EQUITY_FIELDS,
    EQUITY_CURVE_FIELDS,
    HARD_STOP_ABS,
    HARD_STOP_PCT,
    LOT_SIZE,
    MAINT_FORCE_EXIT,
    MAINT_STOP_ENTRY,
    MAINT_WARNING,
    REJECT_FIELDS,
    TRADE_LOG_FIELDS,
    _day_from_ts,
    _float,
    _gross_position_value,
    _parse_ts,
    _pf,
    _position_key,
    _trade_pnl_yen,
    _write_csv,
    dedupe_trades,
    load_session_capital_backtest_trades,
)

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_MIN_DAY = "20260529"
DEFAULT_MAX_DAY = "20260612"
DEFAULT_INITIAL_EQUITY = 500_000.0
DEFAULT_EQUITY_FLOOR = 250_000.0
MAX_CONCURRENT_POSITIONS = 3

SCENARIO_SPECS: dict[str, dict[str, Any]] = {
    "A_cash_100_only": {
        "label": "Cash 100 shares only",
        "leverage_limit": 1.0,
        "sizing": "fixed_100_only",
    },
    "B_credit2_100_only": {
        "label": "Credit 2x 100 shares only",
        "leverage_limit": 2.0,
        "sizing": "fixed_100_only",
    },
    "C_credit3_100_only": {
        "label": "Credit 3x 100 shares only",
        "leverage_limit": 3.0,
        "sizing": "fixed_100_only",
    },
    "D_credit2_variable_30pct": {
        "label": "Credit 2x variable 30% equity",
        "leverage_limit": 2.0,
        "sizing": "variable_30pct",
        "position_pct": 0.30,
    },
    "E_credit3_variable_30pct": {
        "label": "Credit 3x variable 30% equity",
        "leverage_limit": 3.0,
        "sizing": "variable_30pct",
        "position_pct": 0.30,
    },
    "F_credit2_equal_3slots": {
        "label": "Credit 2x equal 3 slots",
        "leverage_limit": 2.0,
        "sizing": "equal_3slots",
    },
    "G_credit3_equal_3slots": {
        "label": "Credit 3x equal 3 slots",
        "leverage_limit": 3.0,
        "sizing": "equal_3slots",
    },
    "H_credit2_risk_0p5pct_100max": {
        "label": "Credit 2x risk 0.5% max 100",
        "leverage_limit": 2.0,
        "sizing": "risk_capped_100",
        "risk_per_trade": 0.005,
    },
    "I_credit3_risk_0p5pct_100max": {
        "label": "Credit 3x risk 0.5% max 100",
        "leverage_limit": 3.0,
        "sizing": "risk_capped_100",
        "risk_per_trade": 0.005,
    },
    "J_credit2_risk_1pct_100max": {
        "label": "Credit 2x risk 1% max 100",
        "leverage_limit": 2.0,
        "sizing": "risk_capped_100",
        "risk_per_trade": 0.01,
    },
    "K_credit3_risk_1pct_100max": {
        "label": "Credit 3x risk 1% max 100",
        "leverage_limit": 3.0,
        "sizing": "risk_capped_100",
        "risk_per_trade": 0.01,
    },
}


def compute_buying_power(*, equity: float, gross: float, leverage_limit: float) -> float:
    return max(0.0, equity * leverage_limit - gross)


def compute_requested_shares(
    *,
    spec: Mapping[str, Any],
    equity: float,
    entry_price: float,
    buying_power: float,
) -> tuple[int, Optional[str]]:
    if entry_price <= 0:
        return 0, "invalid_price"

    sizing = str(spec.get("sizing") or "")
    leverage = float(spec.get("leverage_limit") or 1.0)
    shares = 0

    if sizing == "fixed_100_only":
        shares = LOT_SIZE
    elif sizing == "variable_30pct":
        position_yen = equity * float(spec.get("position_pct") or 0.30)
        shares = int(position_yen / (entry_price * LOT_SIZE)) * LOT_SIZE
    elif sizing == "equal_3slots":
        slot_cap = equity * leverage / 3.0
        shares = int(slot_cap / (entry_price * LOT_SIZE)) * LOT_SIZE
    elif sizing == "risk_capped_100":
        risk_pct = float(spec.get("risk_per_trade") or 0.005)
        risk_amount = equity * risk_pct
        risk_per_100 = entry_price * LOT_SIZE * HARD_STOP_ABS
        if risk_per_100 <= 0:
            return 0, "invalid_price"
        lots = int(risk_amount / risk_per_100)
        shares = min(max(lots, 1) * LOT_SIZE, LOT_SIZE)
    else:
        return 0, "invalid_size"

    if shares < LOT_SIZE:
        return 0, "invalid_size"

    max_affordable = int(buying_power / (entry_price * LOT_SIZE)) * LOT_SIZE
    if max_affordable < LOT_SIZE:
        return 0, "insufficient_buying_power"
    shares = min(shares, max_affordable)

    if shares < LOT_SIZE:
        return 0, "invalid_size"
    if entry_price * shares > buying_power + 1e-6:
        return 0, "insufficient_buying_power"
    return shares, None


def build_event_timeline(trades: Sequence[Mapping[str, Any]]) -> list[tuple[datetime, int, str, dict[str, Any]]]:
    events: list[tuple[datetime, int, str, dict[str, Any]]] = []
    for trade in trades:
        entry_dt = _parse_ts(trade.get("entry_time"))
        exit_dt = _parse_ts(trade.get("exit_time"))
        if entry_dt is None or exit_dt is None:
            continue
        events.append((entry_dt, 0, "entry", dict(trade)))
        events.append((exit_dt, 1, "exit", dict(trade)))
    events.sort(key=lambda x: (x[0], x[1], str(x[3].get("symbol") or "")))
    return events


@dataclass
class ScenarioState:
    scenario_id: str
    spec: dict[str, Any]
    initial_equity: float
    equity_floor: float
    realized_pnl: float = 0.0
    open_positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    accepted_keys: set[str] = field(default_factory=set)
    trading_halted: bool = False
    equity_floor_breached: bool = False
    equity_floor_breach_time: Optional[str] = None
    maintenance_warning_count: int = 0
    maintenance_stop_count: int = 0
    force_exit_count: int = 0
    capital_block_count: int = 0
    position_cap_reject_count: int = 0
    insufficient_buying_power_count: int = 0
    accepted_trade_count: int = 0
    rejected_trade_count: int = 0
    maintenance_ratios: list[float] = field(default_factory=list)
    gross_samples: list[float] = field(default_factory=list)
    max_concurrent_positions_observed: int = 0
    max_gross_position_value: float = 0.0
    peak_equity: float = 0.0
    min_equity: float = 0.0
    first_entry_equity: Optional[float] = None
    last_entry_equity: Optional[float] = None
    trade_log: list[dict[str, Any]] = field(default_factory=list)
    reject_log: list[dict[str, Any]] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    realized_pnls: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.peak_equity = self.initial_equity
        self.min_equity = self.initial_equity

    def current_equity(self) -> float:
        return self.initial_equity + self.realized_pnl

    def record_equity_point(self, ts: str, day: str) -> None:
        eq = self.current_equity()
        gross = _gross_position_value(self.open_positions)
        mr = _maintenance_ratio(eq, gross)
        self.peak_equity = max(self.peak_equity, eq)
        self.min_equity = min(self.min_equity, eq)
        dd_yen = round(self.peak_equity - eq, 2)
        dd_pct = round(dd_yen / self.peak_equity * 100.0, 4) if self.peak_equity > 0 else 0.0
        self.equity_curve.append(
            {
                "timestamp_or_day": ts or day,
                "scenario": self.scenario_id,
                "equity": round(eq, 2),
                "drawdown_yen": dd_yen,
                "drawdown_pct": dd_pct,
                "gross_position_value": round(gross, 2),
                "maintenance_ratio": round(mr, 4) if mr is not None else "",
            }
        )

    def _maintenance_ratio(self, equity: float, gross: float) -> Optional[float]:
        if gross <= 0:
            return None
        return equity / gross

    def _force_close_all(self, ts: str, day: str, *, reason: str) -> None:
        for key in list(self.open_positions.keys()):
            pos = self.open_positions.get(key)
            if not pos:
                continue
            trade = pos["trade"]
            xp = _float(trade.get("exit_price"))
            if xp is None or xp <= 0:
                self.open_positions.pop(key, None)
                continue
            self._close_position(key, ts, day, forced=True, force_reason=reason)

    def _close_position(self, key: str, ts: str, day: str, *, forced: bool = False, force_reason: str = "") -> None:
        pos = self.open_positions.pop(key, None)
        if not pos:
            return
        trade = pos["trade"]
        shares = int(pos["shares"])
        eq_before = self.current_equity()
        gross_before = _gross_position_value(self.open_positions) + float(trade["entry_price"]) * shares
        mr_before = self._maintenance_ratio(eq_before, gross_before)
        pnl = _trade_pnl_yen(trade, shares)
        self.realized_pnl += pnl
        self.realized_pnls.append(pnl)
        eq_after = self.current_equity()
        gross_after = _gross_position_value(self.open_positions)
        mr_after = self._maintenance_ratio(eq_after, gross_after)
        if forced:
            self.force_exit_count += 1
        self.trade_log.append(
            {
                "scenario": self.scenario_id,
                "day": day,
                "symbol": trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "exit_time": ts or trade.get("exit_time"),
                "entry_price": trade.get("entry_price"),
                "exit_price": trade.get("exit_price"),
                "shares": shares,
                "position_value": round(float(trade["entry_price"]) * shares, 2),
                "pnl_yen": pnl,
                "pnl_pct": trade.get("pnl_pct"),
                "exit_reason": force_reason or trade.get("exit_reason_canonical") or trade.get("exit_reason"),
                "accepted_or_rejected": "accepted",
                "reject_reason": "force_exit" if forced else "",
                "equity_before": round(eq_before, 2),
                "equity_after": round(eq_after, 2),
                "maintenance_ratio_before": round(mr_before, 4) if mr_before is not None else "",
                "maintenance_ratio_after": round(mr_after, 4) if mr_after is not None else "",
                "gross_position_value_before": round(gross_before, 2),
                "gross_position_value_after": round(gross_after, 2),
            }
        )
        self.record_equity_point(ts, day)
        self._check_equity_floor(ts, day)

    def _check_equity_floor(self, ts: str, day: str) -> None:
        if self.equity_floor_breached:
            return
        if self.current_equity() < self.equity_floor:
            self.equity_floor_breached = True
            self.equity_floor_breach_time = ts
            self.trading_halted = True
            self._force_close_all(ts, day, reason="equity_floor_breach")

    def _reject_entry(self, trade: Mapping[str, Any], ts: str, day: str, reason: str, *, requested_shares: int = 0) -> None:
        self.rejected_trade_count += 1
        if reason == "max_concurrent_positions":
            self.position_cap_reject_count += 1
            self.capital_block_count += 1
        elif reason in ("insufficient_buying_power", "invalid_size", "invalid_price"):
            self.insufficient_buying_power_count += 1
            self.capital_block_count += 1
        elif reason in ("maintenance_ratio_stop", "equity_floor_breach"):
            self.capital_block_count += 1
        eq = self.current_equity()
        gross = _gross_position_value(self.open_positions)
        bp = compute_buying_power(
            equity=eq,
            gross=gross,
            leverage_limit=float(self.spec.get("leverage_limit") or 1.0),
        )
        mr = self._maintenance_ratio(eq, gross)
        self.reject_log.append(
            {
                "scenario": self.scenario_id,
                "day": day,
                "symbol": trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "reject_reason": reason,
                "entry_price": trade.get("entry_price"),
                "requested_shares": requested_shares or LOT_SIZE,
                "equity": round(eq, 2),
                "buying_power": round(bp, 2),
                "gross_position_value": round(gross, 2),
                "maintenance_ratio": round(mr, 4) if mr is not None else "",
            }
        )
        self.trade_log.append(
            {
                "scenario": self.scenario_id,
                "day": day,
                "symbol": trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "exit_time": trade.get("exit_time"),
                "entry_price": trade.get("entry_price"),
                "exit_price": trade.get("exit_price"),
                "shares": requested_shares,
                "position_value": "",
                "pnl_yen": "",
                "pnl_pct": trade.get("pnl_pct"),
                "exit_reason": trade.get("exit_reason_canonical") or trade.get("exit_reason"),
                "accepted_or_rejected": "rejected",
                "reject_reason": reason,
                "equity_before": round(eq, 2),
                "equity_after": round(eq, 2),
                "maintenance_ratio_before": round(mr, 4) if mr is not None else "",
                "maintenance_ratio_after": round(mr, 4) if mr is not None else "",
                "gross_position_value_before": round(gross, 2),
                "gross_position_value_after": round(gross, 2),
            }
        )

    def try_entry(self, trade: Mapping[str, Any], ts: str, day: str) -> None:
        eq = self.current_equity()
        gross = _gross_position_value(self.open_positions)
        mr = self._maintenance_ratio(eq, gross)
        if mr is not None:
            self.maintenance_ratios.append(mr)
            if mr < MAINT_WARNING:
                self.maintenance_warning_count += 1

        if self.trading_halted or eq < self.equity_floor:
            self._reject_entry(trade, ts, day, "equity_floor_breach")
            return

        if mr is not None and mr < MAINT_FORCE_EXIT:
            self._force_close_all(ts, day, reason="maintenance_ratio_force_exit")
            gross = _gross_position_value(self.open_positions)
            eq = self.current_equity()
            mr = self._maintenance_ratio(eq, gross)
        elif mr is not None and mr < MAINT_STOP_ENTRY:
            self.maintenance_stop_count += 1
            self._reject_entry(trade, ts, day, "maintenance_ratio_stop")
            return

        if len(self.open_positions) >= MAX_CONCURRENT_POSITIONS:
            self._reject_entry(trade, ts, day, "max_concurrent_positions")
            return

        entry_price = float(_float(trade.get("entry_price")) or 0.0)
        leverage = float(self.spec.get("leverage_limit") or 1.0)
        buying_power = compute_buying_power(equity=eq, gross=gross, leverage_limit=leverage)
        shares, reject_reason = compute_requested_shares(
            spec=self.spec,
            equity=eq,
            entry_price=entry_price,
            buying_power=buying_power,
        )
        if reject_reason:
            self._reject_entry(trade, ts, day, reject_reason, requested_shares=shares)
            return

        if self.first_entry_equity is None:
            self.first_entry_equity = round(eq, 2)
        self.last_entry_equity = round(eq, 2)

        key = _position_key(trade)
        gross_before = gross
        eq_before = eq
        mr_before = mr
        self.open_positions[key] = {
            "trade": trade,
            "shares": shares,
            "entry_time": trade.get("entry_time"),
        }
        self.accepted_keys.add(key)
        self.accepted_trade_count += 1
        gross_after = _gross_position_value(self.open_positions)
        self.gross_samples.append(gross_after)
        self.max_gross_position_value = max(self.max_gross_position_value, gross_after)
        self.max_concurrent_positions_observed = max(
            self.max_concurrent_positions_observed, len(self.open_positions)
        )
        mr_after = self._maintenance_ratio(self.current_equity(), gross_after)
        if mr_after is not None:
            self.maintenance_ratios.append(mr_after)
            if mr_after < MAINT_WARNING:
                self.maintenance_warning_count += 1
            if mr_after < MAINT_FORCE_EXIT:
                self._force_close_all(ts, day, reason="maintenance_ratio_force_exit")

        self.trade_log.append(
            {
                "scenario": self.scenario_id,
                "day": day,
                "symbol": trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "exit_time": "",
                "entry_price": trade.get("entry_price"),
                "exit_price": "",
                "shares": shares,
                "position_value": round(entry_price * shares, 2),
                "pnl_yen": "",
                "pnl_pct": "",
                "exit_reason": "",
                "accepted_or_rejected": "accepted",
                "reject_reason": "",
                "equity_before": round(eq_before, 2),
                "equity_after": round(self.current_equity(), 2),
                "maintenance_ratio_before": round(mr_before, 4) if mr_before is not None else "",
                "maintenance_ratio_after": round(mr_after, 4) if mr_after is not None else "",
                "gross_position_value_before": round(gross_before, 2),
                "gross_position_value_after": round(gross_after, 2),
            }
        )
        self.record_equity_point(ts, day)

    def process_exit(self, trade: Mapping[str, Any], ts: str, day: str) -> None:
        key = _position_key(trade)
        if key not in self.accepted_keys or key not in self.open_positions:
            return
        self._close_position(key, ts, day)


def _maintenance_ratio(equity: float, gross: float) -> Optional[float]:
    if gross <= 0:
        return None
    return equity / gross


def simulate_scenario(
    trades: Sequence[Mapping[str, Any]],
    *,
    scenario_id: str,
    spec: Mapping[str, Any],
    initial_equity: float = DEFAULT_INITIAL_EQUITY,
    equity_floor: float = DEFAULT_EQUITY_FLOOR,
) -> dict[str, Any]:
    state = ScenarioState(
        scenario_id=scenario_id,
        spec=dict(spec),
        initial_equity=initial_equity,
        equity_floor=equity_floor,
    )
    events = build_event_timeline(trades)
    if events:
        state.record_equity_point("", events[0][0].astimezone(JST).strftime("%Y%m%d"))

    for dt, _, kind, trade in events:
        ts = dt.isoformat()
        day = _day_from_ts(ts)
        if kind == "entry":
            state.try_entry(trade, ts, day)
        else:
            state.process_exit(trade, ts, day)

    if state.open_positions and events:
        last_ts = events[-1][0].isoformat()
        last_day = _day_from_ts(last_ts)
        state._force_close_all(last_ts, last_day, reason="end_of_period")

    final_equity = round(state.current_equity(), 2)
    total_return_yen = round(final_equity - initial_equity, 2)
    total_return_pct = round(total_return_yen / initial_equity * 100.0, 4)
    max_dd_yen = round(state.peak_equity - state.min_equity, 2)
    max_dd_pct = round(max_dd_yen / state.peak_equity * 100.0, 4) if state.peak_equity > 0 else 0.0

    am_pnl = pm_pnl = d40_pnl = c10_pnl = 0.0
    wins = 0
    exit_rows = [
        r for r in state.trade_log if r.get("accepted_or_rejected") == "accepted" and r.get("pnl_yen") not in ("", None)
    ]
    trade_lookup = {(t.get("symbol"), t.get("entry_time")): t for t in trades}
    for row in exit_rows:
        pnl = float(row.get("pnl_yen") or 0.0)
        if pnl > 0:
            wins += 1
        sym_trade = trade_lookup.get((row.get("symbol"), row.get("entry_time")), {})
        sk = str(sym_trade.get("session_kind") or "").lower()
        ug = str(sym_trade.get("universe_group") or "")
        if sk == "am":
            am_pnl += pnl
        elif sk == "pm":
            pm_pnl += pnl
        if ug == "dynamic40":
            d40_pnl += pnl
        elif ug == "core10":
            c10_pnl += pnl

    daily_pnls: dict[str, float] = defaultdict(float)
    for row in exit_rows:
        daily_pnls[str(row.get("day") or "")] += float(row.get("pnl_yen") or 0.0)
    daily_win_days = sum(1 for v in daily_pnls.values() if v > 0)

    total_attempts = state.accepted_trade_count + state.rejected_trade_count
    return {
        "scenario_id": scenario_id,
        "label": spec.get("label"),
        "leverage_limit": spec.get("leverage_limit"),
        "initial_equity": initial_equity,
        "final_equity": final_equity,
        "total_return_yen": total_return_yen,
        "total_return_pct": total_return_pct,
        "realized_pnl": round(state.realized_pnl, 2),
        "max_drawdown_yen": max_dd_yen,
        "max_drawdown_pct": max_dd_pct,
        "min_equity": round(state.min_equity, 2),
        "equity_floor_breached": state.equity_floor_breached,
        "accepted_trade_count": state.accepted_trade_count,
        "rejected_trade_count": state.rejected_trade_count,
        "reject_rate": round(state.rejected_trade_count / total_attempts, 4) if total_attempts else 0.0,
        "capital_block_count": state.capital_block_count,
        "position_cap_reject_count": state.position_cap_reject_count,
        "insufficient_buying_power_count": state.insufficient_buying_power_count,
        "maintenance_warning_count": state.maintenance_warning_count,
        "maintenance_stop_count": state.maintenance_stop_count,
        "force_exit_count": state.force_exit_count,
        "min_maintenance_ratio": round(min(state.maintenance_ratios), 4) if state.maintenance_ratios else None,
        "avg_maintenance_ratio": round(statistics.mean(state.maintenance_ratios), 4) if state.maintenance_ratios else None,
        "max_gross_position_value": round(state.max_gross_position_value, 2),
        "avg_gross_position_value": round(statistics.mean(state.gross_samples), 2) if state.gross_samples else 0.0,
        "max_concurrent_positions_observed": state.max_concurrent_positions_observed,
        "profit_factor": _pf(state.realized_pnls),
        "win_rate": round(wins / len(exit_rows), 4) if exit_rows else 0.0,
        "daily_win_rate": round(daily_win_days / len(daily_pnls), 4) if daily_pnls else 0.0,
        "AM_pnl": round(am_pnl, 2),
        "PM_pnl": round(pm_pnl, 2),
        "Dynamic40_pnl": round(d40_pnl, 2),
        "Core10_pnl": round(c10_pnl, 2),
        "first_entry_equity": state.first_entry_equity,
        "last_entry_equity": state.last_entry_equity,
        "reinvestment_effective": (
            state.last_entry_equity is not None
            and state.first_entry_equity is not None
            and state.last_entry_equity >= state.first_entry_equity
        ),
        "_trade_log": state.trade_log,
        "_reject_log": state.reject_log,
        "_equity_curve": state.equity_curve,
        "_daily_pnls": dict(daily_pnls),
    }


def build_daily_equity_rows(scenario_result: Mapping[str, Any], *, initial_equity: float) -> list[dict[str, Any]]:
    curve = list(scenario_result.get("_equity_curve") or [])
    daily_pnls = scenario_result.get("_daily_pnls") or {}
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pt in curve:
        ts = str(pt.get("timestamp_or_day") or "")
        day = ts[:8] if len(ts) >= 8 and ts[:8].isdigit() else _day_from_ts(ts) or "unknown"
        by_day[day].append(pt)

    rows: list[dict[str, Any]] = []
    for day in sorted(by_day):
        pts = by_day[day]
        equities = [float(p.get("equity") or 0.0) for p in pts]
        start_eq = equities[0]
        end_eq = equities[-1]
        daily_pnl = round(float(daily_pnls.get(day, 0.0)), 2)
        peak = start_eq
        max_intra_dd = 0.0
        for eq in equities:
            peak = max(peak, eq)
            max_intra_dd = max(max_intra_dd, peak - eq)
        mrs = [float(p.get("maintenance_ratio")) for p in pts if p.get("maintenance_ratio") not in ("", None)]
        gross_vals = [float(p.get("gross_position_value") or 0.0) for p in pts]
        rows.append(
            {
                "day": day,
                "scenario": scenario_result.get("scenario_id"),
                "start_equity": round(start_eq, 2),
                "end_equity": round(end_eq, 2),
                "daily_pnl": daily_pnl,
                "cumulative_return_pct": round((end_eq - initial_equity) / initial_equity * 100.0, 4),
                "max_intraday_drawdown": round(max_intra_dd, 2),
                "accepted_trade_count": "",
                "rejected_trade_count": "",
                "min_maintenance_ratio": round(min(mrs), 4) if mrs else "",
                "max_gross_position_value": round(max(gross_vals), 2) if gross_vals else 0.0,
            }
        )
    return rows


def _recommend_scenario(scenarios: Sequence[Mapping[str, Any]]) -> str:
    viable = [
        s
        for s in scenarios
        if not s.get("equity_floor_breached")
        and float(s.get("min_maintenance_ratio") or 1.0) >= MAINT_FORCE_EXIT
    ]
    if not viable:
        viable = list(scenarios)
    ranked = sorted(
        viable,
        key=lambda s: (-float(s.get("total_return_yen") or 0.0), float(s.get("max_drawdown_yen") or 1e18)),
    )
    return str(ranked[0].get("scenario_id") or "B_credit2_100_only")


def build_report(summary: Mapping[str, Any]) -> str:
    scenarios = list(summary.get("scenarios") or [])
    pop = summary.get("population") or {}
    best = max(scenarios, key=lambda s: float(s.get("final_equity") or 0.0)) if scenarios else {}
    best_dd = min(scenarios, key=lambda s: float(s.get("max_drawdown_yen") or 1e18)) if scenarios else {}
    hundred = [s for s in scenarios if str(s.get("scenario_id", "")).endswith("_100_only")]
    credit2 = [s for s in scenarios if "_credit2_" in str(s.get("scenario_id", ""))]
    credit3 = [s for s in scenarios if "_credit3_" in str(s.get("scenario_id", ""))]
    lines = [
        "# Phase383 Realistic Credit Position Sizing Backtest",
        "",
        f"**期間:** {pop.get('min_day')}–{pop.get('max_day')}",
        f"**初期元本:** {summary.get('initial_equity'):,.0f}円",
        f"**equity_floor:** {summary.get('equity_floor'):,.0f}円",
        "",
        "## 結論",
        "",
        f"- **最良final_equity:** {best.get('scenario_id')} = {best.get('final_equity')}円 ({best.get('total_return_pct')}%)",
        f"- **最小DD:** {best_dd.get('scenario_id')} max_dd={best_dd.get('max_drawdown_yen')}円",
        f"- **推奨:** {summary.get('recommended_scenario')}",
        "",
        "## 100株のみ",
        "",
    ]
    for s in hundred:
        lines.append(
            f"- {s.get('scenario_id')}: final={s.get('final_equity')} accepted={s.get('accepted_trade_count')} "
            f"reject_rate={round(float(s.get('reject_rate') or 0)*100,1)}% min_maint={s.get('min_maintenance_ratio')}"
        )
    lines.extend(["", "## 信用2倍 vs 3倍（100株のみ）", ""])
    for s in [x for x in scenarios if x.get("scenario_id") in ("B_credit2_100_only", "C_credit3_100_only")]:
        lines.append(
            f"- {s.get('scenario_id')}: final={s.get('final_equity')} dd={s.get('max_drawdown_yen')} "
            f"accepted={s.get('accepted_trade_count')} min_maint={s.get('min_maintenance_ratio')}"
        )
    lines.extend(["", "## 全シナリオ", "", "| scenario | final | return% | max_dd | accepted | rejected | min_maint | force_exit |", "|---|---:|---:|---:|---:|---:|---:|---:|"])
    for s in scenarios:
        lines.append(
            f"| {s.get('scenario_id')} | {s.get('final_equity')} | {s.get('total_return_pct')} | "
            f"{s.get('max_drawdown_yen')} | {s.get('accepted_trade_count')} | {s.get('rejected_trade_count')} | "
            f"{s.get('min_maintenance_ratio')} | {s.get('force_exit_count')} |"
        )
    floor_breach = [s.get("scenario_id") for s in scenarios if s.get("equity_floor_breached")]
    near_margin = [s.get("scenario_id") for s in scenarios if float(s.get("min_maintenance_ratio") or 1) < 0.35]
    lines.extend(
        [
            "",
            "## 監査",
            "",
            f"- equity_floor割れ: {floor_breach or 'なし'}",
            f"- 追証ライン近接(min_maint<0.35): {near_margin or 'なし'}",
            f"- 利益再投資: 各シナリオの reinvestment_effective を summary JSON 参照",
            "",
            "## 禁止事項",
            "",
            "- ENTRY/EXIT/Universe/Discord/canonical 変更なし",
        ]
    )
    return "\n".join(lines) + "\n"


def _scenario_worker(job: dict[str, Any]) -> dict[str, Any]:
    return simulate_scenario(
        job["trades"],
        scenario_id=job["scenario_id"],
        spec=job["spec"],
        initial_equity=float(job["initial_equity"]),
        equity_floor=float(job["equity_floor"]),
    )


@dataclass
class Phase383RealisticCreditSizingBacktest:
    reports_dir: Path
    min_day: str = DEFAULT_MIN_DAY
    max_day: Optional[str] = DEFAULT_MAX_DAY
    initial_equity: float = DEFAULT_INITIAL_EQUITY
    equity_floor: float = DEFAULT_EQUITY_FLOOR
    all_trades: list[dict[str, Any]] = field(default_factory=list)
    excluded_trades: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase383_realistic_credit_sizing_summary.json",
            "daily_equity": self.reports_dir / "phase383_realistic_credit_sizing_daily_equity.csv",
            "trade_log": self.reports_dir / "phase383_realistic_credit_sizing_trade_log.csv",
            "rejects": self.reports_dir / "phase383_realistic_credit_sizing_rejects.csv",
            "equity_curve": self.reports_dir / "phase383_realistic_credit_sizing_equity_curve.csv",
            "report": self.reports_dir / "phase383_realistic_credit_sizing_report.md",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        self.all_trades.extend(result.get("valid_trades") or [])
        self.excluded_trades.extend(result.get("excluded_trades") or [])

    def run(
        self,
        *,
        parallel: bool = False,
        max_workers: int = 2,
        wall_runtime_sec: float = 0.0,
        sessions_discovered: int = 0,
        sessions_evaluated: int = 0,
    ) -> dict[str, Any]:
        trades, duplicate_removed = dedupe_trades(self.all_trades)
        trades = sorted(
            trades,
            key=lambda t: (_parse_ts(t.get("entry_time")) or datetime.min.replace(tzinfo=JST), str(t.get("symbol") or "")),
        )
        exclusion_counts: dict[str, int] = defaultdict(int)
        for ex in self.excluded_trades:
            exclusion_counts[str(ex.get("exclude_reason") or "unknown")] += 1

        jobs = [
            {
                "scenario_id": sid,
                "spec": SCENARIO_SPECS[sid],
                "trades": trades,
                "initial_equity": self.initial_equity,
                "equity_floor": self.equity_floor,
            }
            for sid in SCENARIO_SPECS
        ]
        results: list[dict[str, Any]] = []
        if parallel and len(jobs) > 1:
            from concurrent.futures import ProcessPoolExecutor, as_completed

            with ProcessPoolExecutor(max_workers=max(1, max_workers)) as pool:
                futures = {pool.submit(_scenario_worker, job): job for job in jobs}
                for fut in as_completed(futures):
                    results.append(fut.result())
        else:
            for job in jobs:
                results.append(_scenario_worker(job))
        results.sort(key=lambda r: str(r.get("scenario_id") or ""))

        phase382_note = {}
        p382 = self.reports_dir / "phase382_capital_constrained_summary.json"
        if p382.is_file():
            p382_data = json.loads(p382.read_text(encoding="utf-8"))
            f_old = next(
                (s for s in p382_data.get("scenarios", []) if s.get("scenario_id") == "F_no_margin_cash_only"),
                {},
            )
            phase382_note = {
                "phase382_F_accepted": f_old.get("accepted_trade_count"),
                "phase382_F_final_equity": f_old.get("final_equity"),
                "note": "Phase383 A_cash_100_only uses strict 100-share cap vs Phase382 multi-lot cash",
            }

        return {
            "phase": 383,
            "title": "Realistic credit position sizing backtest",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "stack_id": PRIMARY_STACK,
            "hard_stop_pct": HARD_STOP_PCT,
            "initial_equity": self.initial_equity,
            "equity_floor": self.equity_floor,
            "buying_power_model": "equity * leverage_limit - gross_position_value",
            "population": {
                "min_day": self.min_day,
                "max_day": self.max_day,
                "sessions_discovered": sessions_discovered,
                "sessions_evaluated": sessions_evaluated,
                "input_trade_count_raw": len(self.all_trades),
                "duplicate_session_trades_removed": duplicate_removed,
                "input_trade_count": len(trades),
                "excluded_trade_count": len(self.excluded_trades),
                "exclusion_reason_counts": dict(exclusion_counts),
            },
            "scenarios": [{k: v for k, v in r.items() if not str(k).startswith("_")} for r in results],
            "recommended_scenario": _recommend_scenario(results),
            "phase382_consistency": phase382_note,
            "wall_runtime_sec": round(wall_runtime_sec, 2),
            "_scenario_results": results,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        trade_logs: list[dict[str, Any]] = []
        reject_logs: list[dict[str, Any]] = []
        equity_curve: list[dict[str, Any]] = []
        daily_equity: list[dict[str, Any]] = []
        ie = float(result.get("initial_equity") or DEFAULT_INITIAL_EQUITY)
        for sr in result.get("_scenario_results") or []:
            trade_logs.extend(sr.get("_trade_log") or [])
            reject_logs.extend(sr.get("_reject_log") or [])
            equity_curve.extend(sr.get("_equity_curve") or [])
            daily_equity.extend(build_daily_equity_rows(sr, initial_equity=ie))

        payload = {k: v for k, v in result.items() if not str(k).startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_csv(paths["trade_log"], trade_logs, TRADE_LOG_FIELDS)
        _write_csv(paths["rejects"], reject_logs, REJECT_FIELDS)
        _write_csv(paths["daily_equity"], daily_equity, DAILY_EQUITY_FIELDS)
        _write_csv(paths["equity_curve"], equity_curve, EQUITY_CURVE_FIELDS)
        paths["report"].write_text(build_report(payload), encoding="utf-8")
        return paths
