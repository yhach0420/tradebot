"""
Phase382: Capital-constrained backtest for Stack C (Phase355+Phase364).

Simulation only — no ENTRY/EXIT/Discord/canonical changes.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase365_production_stack_validation import load_session_production_stack_trades
from research.phase366_stophit_reclassification import production_kept_trades
from research.phase377_daily_regime_breakdown import PRIMARY_STACK

JST = ZoneInfo("Asia/Tokyo")

DEFAULT_MIN_DAY = "20260529"
DEFAULT_MAX_DAY = "20260612"
INITIAL_EQUITY = 500_000.0
EQUITY_FLOOR = 250_000.0
HARD_STOP_PCT = 1.20
HARD_STOP_ABS = HARD_STOP_PCT / 100.0
LOT_SIZE = 100

MAINT_WARNING = 0.40
MAINT_STOP_ENTRY = 0.35
MAINT_FORCE_EXIT = 0.30

SCENARIO_SPECS: dict[str, dict[str, Any]] = {
    "A_fixed_100_shares": {
        "label": "Fixed 100 shares",
        "leverage_limit": 3.0,
        "risk_per_trade": None,
        "maint_stop_entry": MAINT_STOP_ENTRY,
        "maint_force_exit": MAINT_FORCE_EXIT,
        "sizing": "fixed_100",
    },
    "B_risk_1pct": {
        "label": "Risk 1% per trade",
        "leverage_limit": 3.0,
        "risk_per_trade": 0.01,
        "maint_stop_entry": MAINT_STOP_ENTRY,
        "maint_force_exit": MAINT_FORCE_EXIT,
        "sizing": "risk_pct",
    },
    "C_risk_0p5pct": {
        "label": "Risk 0.5% per trade",
        "leverage_limit": 3.0,
        "risk_per_trade": 0.005,
        "maint_stop_entry": MAINT_STOP_ENTRY,
        "maint_force_exit": MAINT_FORCE_EXIT,
        "sizing": "risk_pct",
    },
    "D_equal_capital_3slots": {
        "label": "Equal capital 3 slots",
        "leverage_limit": 3.0,
        "risk_per_trade": None,
        "maint_stop_entry": MAINT_STOP_ENTRY,
        "maint_force_exit": MAINT_FORCE_EXIT,
        "sizing": "equal_slots",
    },
    "E_conservative": {
        "label": "Conservative 2x / 0.5% risk",
        "leverage_limit": 2.0,
        "risk_per_trade": 0.005,
        "maint_stop_entry": 0.45,
        "maint_force_exit": 0.35,
        "sizing": "risk_pct",
    },
    "F_no_margin_cash_only": {
        "label": "Cash only (no margin)",
        "leverage_limit": 1.0,
        "risk_per_trade": None,
        "maint_stop_entry": MAINT_STOP_ENTRY,
        "maint_force_exit": MAINT_FORCE_EXIT,
        "sizing": "cash_only",
    },
}

REFERENCE_SCENARIO_ID = "REFERENCE_unconstrained_100"

TRADE_LOG_FIELDS = [
    "scenario",
    "day",
    "symbol",
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "shares",
    "position_value",
    "pnl_yen",
    "pnl_pct",
    "exit_reason",
    "accepted_or_rejected",
    "reject_reason",
    "equity_before",
    "equity_after",
    "maintenance_ratio_before",
    "maintenance_ratio_after",
    "gross_position_value_before",
    "gross_position_value_after",
]

REJECT_FIELDS = [
    "scenario",
    "day",
    "symbol",
    "entry_time",
    "reject_reason",
    "entry_price",
    "requested_shares",
    "equity",
    "buying_power",
    "gross_position_value",
    "maintenance_ratio",
]

DAILY_EQUITY_FIELDS = [
    "day",
    "scenario",
    "start_equity",
    "end_equity",
    "daily_pnl",
    "cumulative_return_pct",
    "max_intraday_drawdown",
    "accepted_trade_count",
    "rejected_trade_count",
    "margin_warning_count",
    "force_exit_count",
    "min_maintenance_ratio",
    "max_gross_position_value",
]

EQUITY_CURVE_FIELDS = [
    "timestamp_or_day",
    "scenario",
    "equity",
    "drawdown_yen",
    "drawdown_pct",
    "gross_position_value",
    "maintenance_ratio",
]


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _pf(yens: Sequence[float]) -> Optional[float]:
    gp = sum(max(y, 0.0) for y in yens)
    gl = abs(sum(min(y, 0.0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def _parse_ts(val: Any) -> Optional[datetime]:
    if val in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except ValueError:
        return None


def _day_from_ts(val: Any) -> str:
    dt = _parse_ts(val)
    if dt is None:
        return ""
    return dt.astimezone(JST).strftime("%Y%m%d")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def validate_trade_row(trade: Mapping[str, Any]) -> tuple[bool, str]:
    symbol = str(trade.get("symbol") or "").strip()
    if not symbol:
        return False, "missing_symbol"
    entry_time = trade.get("entry_time")
    exit_time = trade.get("exit_time")
    if not entry_time:
        return False, "missing_entry_time"
    if not exit_time:
        return False, "missing_exit_time"
    if _parse_ts(entry_time) is None:
        return False, "invalid_entry_time"
    if _parse_ts(exit_time) is None:
        return False, "invalid_exit_time"
    entry_price = _float(trade.get("entry_price"))
    exit_price = _float(trade.get("exit_price"))
    if entry_price is None or entry_price <= 0:
        return False, "missing_entry_price"
    if exit_price is None or exit_price <= 0:
        return False, "missing_exit_price"
    pnl_pct = _float(trade.get("pnl_pct"))
    pnl_yen_100 = _float(trade.get("pnl_yen_100"))
    if pnl_pct is None and pnl_yen_100 is None:
        return False, "missing_pnl"
    return True, ""


def load_session_capital_backtest_trades(
    session_meta: Mapping[str, Any],
    *,
    reports_dir: Path,
    min_day: str,
    max_day: Optional[str],
) -> dict[str, Any]:
    day = str(session_meta.get("day_key") or session_meta.get("day") or "")
    if day < min_day or (max_day and day > max_day):
        return {"error": "outside_range", "valid_trades": [], "excluded_trades": []}

    base = load_session_production_stack_trades(session_meta, reports_dir=reports_dir)
    if base.get("error"):
        return {**base, "valid_trades": [], "excluded_trades": []}

    valid: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for t in production_kept_trades(base):
        row = dict(t)
        row["day_key"] = day
        ok, reason = validate_trade_row(row)
        if ok:
            ep = _float(row.get("entry_price"))
            xp = _float(row.get("exit_price"))
            if row.get("pnl_yen_100") is None and ep and xp:
                row["pnl_yen_100"] = round((xp - ep) * 100.0, 2)
            if row.get("pnl_pct") is None and ep and xp:
                row["pnl_pct"] = round((xp - ep) / ep * 100.0, 4)
            valid.append(row)
        else:
            excluded.append(
                {
                    "day_key": day,
                    "symbol": row.get("symbol"),
                    "entry_time": row.get("entry_time"),
                    "exclude_reason": reason,
                }
            )

    return {
        **base,
        "day_key": day,
        "valid_trades": valid,
        "excluded_trades": excluded,
        "valid_count": len(valid),
        "excluded_count": len(excluded),
        "error": "",
    }


def _gross_position_value(open_positions: Mapping[str, Mapping[str, Any]]) -> float:
    total = 0.0
    for p in open_positions.values():
        trade = p.get("trade") or p
        ep = float(_float(trade.get("entry_price")) or _float(p.get("entry_price")) or 0.0)
        total += ep * int(p.get("shares") or 0)
    return total


def _equity(initial_equity: float, realized_pnl: float) -> float:
    return initial_equity + realized_pnl


def _maintenance_ratio(equity: float, gross: float) -> Optional[float]:
    if gross <= 0:
        return None
    return equity / gross


def _buying_power(
    *,
    equity: float,
    gross: float,
    initial_equity: float,
    leverage_limit: float,
    sizing: str,
) -> float:
    if sizing == "cash_only":
        return max(0.0, equity - gross)
    limit = initial_equity * leverage_limit
    return max(0.0, limit - gross)


def _position_key(trade: Mapping[str, Any]) -> str:
    return f"{trade.get('symbol')}|{trade.get('entry_time')}"


def compute_requested_shares(
    *,
    scenario_id: str,
    spec: Mapping[str, Any],
    equity: float,
    entry_price: float,
    gross: float,
    buying_power: float,
) -> tuple[int, Optional[str]]:
    if entry_price <= 0:
        return 0, "invalid_price"

    sizing = str(spec.get("sizing") or "")
    shares = 0

    if sizing == "fixed_100":
        shares = LOT_SIZE
    elif sizing == "risk_pct":
        risk_pct = float(spec.get("risk_per_trade") or 0.01)
        risk_amount = equity * risk_pct
        risk_per_100 = entry_price * LOT_SIZE * HARD_STOP_ABS
        if risk_per_100 <= 0:
            return 0, "invalid_price"
        lots = int(risk_amount / risk_per_100)
        shares = max(lots, 1) * LOT_SIZE
    elif sizing == "equal_slots":
        slot_capital = equity * float(spec.get("leverage_limit") or 3.0) / 3.0
        lots = int(slot_capital / (entry_price * LOT_SIZE))
        shares = lots * LOT_SIZE
        if shares < LOT_SIZE:
            return 0, "invalid_size"
    elif sizing == "cash_only":
        lots = int(buying_power / (entry_price * LOT_SIZE))
        shares = lots * LOT_SIZE
        if shares < LOT_SIZE:
            return 0, "insufficient_buying_power"
    else:
        return 0, "invalid_size"

    max_affordable = int(buying_power / (entry_price * LOT_SIZE)) * LOT_SIZE
    if sizing != "cash_only":
        shares = min(shares, max_affordable) if max_affordable >= LOT_SIZE else 0
        if shares < LOT_SIZE:
            if max_affordable >= LOT_SIZE:
                shares = LOT_SIZE
            else:
                return 0, "insufficient_buying_power"

    if shares < LOT_SIZE:
        return 0, "invalid_size"
    if entry_price * shares > buying_power + 1e-6:
        return 0, "insufficient_buying_power"
    return shares, None


def _trade_pnl_yen(trade: Mapping[str, Any], shares: int) -> float:
    yen_100 = _float(trade.get("pnl_yen_100"))
    if yen_100 is not None:
        return round(yen_100 * shares / LOT_SIZE, 2)
    ep = float(_float(trade.get("entry_price")) or 0.0)
    xp = float(_float(trade.get("exit_price")) or 0.0)
    if ep > 0 and xp > 0:
        return round((xp - ep) * shares, 2)
    pct = _float(trade.get("pnl_pct"))
    if pct is not None and ep > 0:
        return round(ep * shares * pct / 100.0, 2)
    return 0.0


@dataclass
class ScenarioState:
    scenario_id: str
    spec: dict[str, Any]
    initial_equity: float = INITIAL_EQUITY
    realized_pnl: float = 0.0
    open_positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    accepted_keys: set[str] = field(default_factory=set)
    trading_halted: bool = False
    trading_halt_reason: Optional[str] = None
    equity_floor_breached: bool = False
    equity_floor_breach_time: Optional[str] = None
    halt_day: Optional[str] = None
    equity_floor_halt_mode: str = "period"
    margin_warning_count: int = 0
    maintenance_stop_count: int = 0
    force_exit_count: int = 0
    capital_block_count: int = 0
    accepted_trade_count: int = 0
    rejected_trade_count: int = 0
    maintenance_ratios: list[float] = field(default_factory=list)
    max_concurrent_positions_observed: int = 0
    max_gross_position_value: float = 0.0
    peak_equity: float = INITIAL_EQUITY
    min_equity: float = INITIAL_EQUITY
    trade_log: list[dict[str, Any]] = field(default_factory=list)
    reject_log: list[dict[str, Any]] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    realized_pnls: list[float] = field(default_factory=list)
    unconstrained: bool = False

    def current_equity(self) -> float:
        return _equity(self.initial_equity, self.realized_pnl)

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

    def _check_equity_floor(self, ts: str, day: str) -> None:
        if self.equity_floor_breached:
            return
        if self.current_equity() < EQUITY_FLOOR:
            self.equity_floor_breached = True
            self.equity_floor_breach_time = ts
            self.trading_halted = True
            self.trading_halt_reason = "equity_floor_breach"
            self.halt_day = day
            self._force_close_all(ts, day, reason="equity_floor_breach")

    def _force_close_all(self, ts: str, day: str, *, reason: str) -> None:
        for key in list(self.open_positions.keys()):
            pos = self.open_positions.get(key)
            if not pos:
                continue
            trade = pos["trade"]
            xp = _float(trade.get("exit_price"))
            if xp is None or xp <= 0:
                self.open_positions.pop(key, None)
                self.reject_log.append(
                    {
                        "scenario": self.scenario_id,
                        "day": day,
                        "symbol": trade.get("symbol"),
                        "entry_time": trade.get("entry_time"),
                        "reject_reason": "force_exit_unpriced",
                        "entry_price": trade.get("entry_price"),
                        "requested_shares": pos.get("shares"),
                        "equity": round(self.current_equity(), 2),
                        "buying_power": "",
                        "gross_position_value": round(_gross_position_value(self.open_positions), 2),
                        "maintenance_ratio": "",
                    }
                )
                continue
            self._close_position(key, ts, day, forced=True, force_reason=reason)

    def _close_position(
        self,
        key: str,
        ts: str,
        day: str,
        *,
        forced: bool = False,
        force_reason: str = "",
    ) -> None:
        pos = self.open_positions.pop(key, None)
        if not pos:
            return
        trade = pos["trade"]
        shares = int(pos["shares"])
        eq_before = self.current_equity()
        gross_before = _gross_position_value(self.open_positions) + (
            float(trade["entry_price"]) * shares
        )
        mr_before = _maintenance_ratio(eq_before, gross_before)
        pnl = _trade_pnl_yen(trade, shares)
        self.realized_pnl += pnl
        self.realized_pnls.append(pnl)
        eq_after = self.current_equity()
        gross_after = _gross_position_value(self.open_positions)
        mr_after = _maintenance_ratio(eq_after, gross_after)
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

    def try_entry(self, trade: Mapping[str, Any], ts: str, day: str) -> None:
        key = _position_key(trade)
        eq = self.current_equity()
        gross = _gross_position_value(self.open_positions)
        mr = _maintenance_ratio(eq, gross)
        if not self.unconstrained:
            if mr is not None:
                self.maintenance_ratios.append(mr)
                if mr < MAINT_WARNING:
                    self.margin_warning_count += 1

        if self.unconstrained:
            shares = LOT_SIZE
            reject_reason = None
            entry_price = float(_float(trade.get("entry_price")) or 0.0)
            gross_before = gross
            eq_before = eq
            mr_before = mr
            self.open_positions[key] = {
                "trade": trade,
                "shares": shares,
                "entry_time": trade.get("entry_time"),
                "scheduled_exit_time": trade.get("exit_time"),
            }
            self.accepted_keys.add(key)
            self.accepted_trade_count += 1
            gross_after = _gross_position_value(self.open_positions)
            self.max_gross_position_value = max(self.max_gross_position_value, gross_after)
            self.max_concurrent_positions_observed = max(
                self.max_concurrent_positions_observed, len(self.open_positions)
            )
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
                    "maintenance_ratio_before": "",
                    "maintenance_ratio_after": "",
                    "gross_position_value_before": round(gross_before, 2),
                    "gross_position_value_after": round(gross_after, 2),
                }
            )
            self.record_equity_point(ts, day)
            return

        if self.trading_halted:
            if self.equity_floor_halt_mode == "day" and self.halt_day and day != self.halt_day:
                self.trading_halted = False
                self.trading_halt_reason = None
            else:
                self._reject_entry(trade, ts, day, "equity_floor_breach")
                return

        maint_stop = float(self.spec.get("maint_stop_entry") or MAINT_STOP_ENTRY)
        maint_force = float(self.spec.get("maint_force_exit") or MAINT_FORCE_EXIT)
        if mr is not None and mr < maint_force:
            self._force_close_all(ts, day, reason="maintenance_ratio_force_exit")
            gross = _gross_position_value(self.open_positions)
            eq = self.current_equity()
            mr = _maintenance_ratio(eq, gross)
        elif mr is not None and mr < maint_stop:
            self.maintenance_stop_count += 1
            self._reject_entry(trade, ts, day, "maintenance_ratio_stop")
            return

        if len(self.open_positions) >= 3:
            self._reject_entry(trade, ts, day, "max_concurrent_positions")
            return

        entry_price = float(_float(trade.get("entry_price")) or 0.0)
        buying_power = _buying_power(
            equity=eq,
            gross=gross,
            initial_equity=self.initial_equity,
            leverage_limit=float(self.spec.get("leverage_limit") or 3.0),
            sizing=str(self.spec.get("sizing") or ""),
        )
        shares, reject_reason = compute_requested_shares(
            scenario_id=self.scenario_id,
            spec=self.spec,
            equity=eq,
            entry_price=entry_price,
            gross=gross,
            buying_power=buying_power,
        )

        if reject_reason:
            self._reject_entry(trade, ts, day, reject_reason, requested_shares=shares)
            return

        gross_before = gross
        eq_before = eq
        mr_before = mr
        self.open_positions[key] = {
            "trade": trade,
            "shares": shares,
            "entry_time": trade.get("entry_time"),
            "scheduled_exit_time": trade.get("exit_time"),
        }
        self.accepted_keys.add(key)
        self.accepted_trade_count += 1
        gross_after = _gross_position_value(self.open_positions)
        self.max_gross_position_value = max(self.max_gross_position_value, gross_after)
        self.max_concurrent_positions_observed = max(
            self.max_concurrent_positions_observed, len(self.open_positions)
        )
        mr_after = _maintenance_ratio(self.current_equity(), gross_after)
        if mr_after is not None:
            self.maintenance_ratios.append(mr_after)
            if mr_after < MAINT_WARNING:
                self.margin_warning_count += 1
            maint_force = float(self.spec.get("maint_force_exit") or MAINT_FORCE_EXIT)
            if mr_after < maint_force:
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
                "position_value": round(float(trade["entry_price"]) * shares, 2),
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

    def _reject_entry(
        self,
        trade: Mapping[str, Any],
        ts: str,
        day: str,
        reason: str,
        *,
        requested_shares: int = 0,
    ) -> None:
        self.rejected_trade_count += 1
        if reason in (
            "insufficient_buying_power",
            "max_concurrent_positions",
            "maintenance_ratio_stop",
            "equity_floor_breach",
            "invalid_size",
        ):
            self.capital_block_count += 1
        eq = self.current_equity()
        gross = _gross_position_value(self.open_positions)
        bp = _buying_power(
            equity=eq,
            gross=gross,
            initial_equity=self.initial_equity,
            leverage_limit=float(self.spec.get("leverage_limit") or 3.0),
            sizing=str(self.spec.get("sizing") or ""),
        )
        mr = _maintenance_ratio(eq, gross)
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

    def process_exit(self, trade: Mapping[str, Any], ts: str, day: str) -> None:
        key = _position_key(trade)
        if key not in self.accepted_keys:
            return
        if key not in self.open_positions:
            return
        self._close_position(key, ts, day)


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


def simulate_scenario(
    trades: Sequence[Mapping[str, Any]],
    *,
    scenario_id: str,
    spec: Optional[Mapping[str, Any]] = None,
    unconstrained: bool = False,
    equity_floor_halt_mode: str = "period",
) -> dict[str, Any]:
    if unconstrained:
        scenario_id = REFERENCE_SCENARIO_ID
        spec = {"sizing": "fixed_100", "leverage_limit": 999.0, "label": "Unconstrained 100 shares"}
    else:
        spec = dict(spec or SCENARIO_SPECS[scenario_id])

    state = ScenarioState(
        scenario_id=scenario_id,
        spec=spec,
        unconstrained=unconstrained,
        equity_floor_halt_mode=equity_floor_halt_mode,
    )
    events = build_event_timeline(trades)
    state.record_equity_point("", events[0][0].astimezone(JST).strftime("%Y%m%d") if events else "")

    for dt, _, kind, trade in events:
        ts = dt.isoformat()
        day = _day_from_ts(ts)
        if kind == "entry":
            state.try_entry(trade, ts, day)
        else:
            state.process_exit(trade, ts, day)

    if state.open_positions:
        last_ts = events[-1][0].isoformat() if events else ""
        last_day = _day_from_ts(last_ts)
        state._force_close_all(last_ts, last_day, reason="end_of_period")

    final_equity = state.current_equity()
    total_return_yen = round(final_equity - state.initial_equity, 2)
    total_return_pct = round(total_return_yen / state.initial_equity * 100.0, 4)
    max_dd_yen = round(state.peak_equity - state.min_equity, 2)
    max_dd_pct = round(max_dd_yen / state.peak_equity * 100.0, 4) if state.peak_equity > 0 else 0.0

    am_pnl = pm_pnl = d40_pnl = c10_pnl = 0.0
    wins = 0
    for row in state.trade_log:
        if row.get("accepted_or_rejected") != "accepted" or row.get("pnl_yen") in ("", None):
            continue
        pnl = float(row.get("pnl_yen") or 0.0)
        if pnl > 0:
            wins += 1
        sym_trade = next(
            (t for t in trades if t.get("symbol") == row.get("symbol") and t.get("entry_time") == row.get("entry_time")),
            {},
        )
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

    accepted_logs = [
        r for r in state.trade_log if r.get("accepted_or_rejected") == "accepted" and r.get("pnl_yen") not in ("", None)
    ]
    daily_pnls: dict[str, float] = defaultdict(float)
    for row in accepted_logs:
        daily_pnls[str(row.get("day") or "")] += float(row.get("pnl_yen") or 0.0)
    daily_win_days = sum(1 for v in daily_pnls.values() if v > 0)
    daily_total_days = len(daily_pnls)

    return {
        "scenario_id": scenario_id,
        "label": spec.get("label"),
        "initial_equity": state.initial_equity,
        "final_equity": round(final_equity, 2),
        "total_return_yen": total_return_yen,
        "total_return_pct": total_return_pct,
        "realized_pnl": round(state.realized_pnl, 2),
        "unrealized_pnl_final": 0.0,
        "max_drawdown_yen": max_dd_yen,
        "max_drawdown_pct": max_dd_pct,
        "min_equity": round(state.min_equity, 2),
        "equity_floor_breached": state.equity_floor_breached,
        "equity_floor_breach_time": state.equity_floor_breach_time,
        "trading_halted": state.trading_halted,
        "trading_halt_reason": state.trading_halt_reason,
        "accepted_trade_count": state.accepted_trade_count,
        "rejected_trade_count": state.rejected_trade_count,
        "capital_block_count": state.capital_block_count,
        "margin_warning_count": state.margin_warning_count,
        "maintenance_stop_count": state.maintenance_stop_count,
        "force_exit_count": state.force_exit_count,
        "max_margin_usage": round(
            state.max_gross_position_value / (state.initial_equity * float(spec.get("leverage_limit") or 3.0)),
            4,
        )
        if state.initial_equity > 0
        else 0.0,
        "min_maintenance_ratio": round(min(state.maintenance_ratios), 4) if state.maintenance_ratios else None,
        "avg_maintenance_ratio": round(statistics.mean(state.maintenance_ratios), 4)
        if state.maintenance_ratios
        else None,
        "max_concurrent_positions_observed": state.max_concurrent_positions_observed,
        "total_gross_position_value_max": round(state.max_gross_position_value, 2),
        "profit_factor": _pf(state.realized_pnls),
        "win_rate": round(wins / len(accepted_logs), 4) if accepted_logs else 0.0,
        "daily_win_rate": round(daily_win_days / daily_total_days, 4) if daily_total_days else 0.0,
        "AM_pnl": round(am_pnl, 2),
        "PM_pnl": round(pm_pnl, 2),
        "Dynamic40_pnl": round(d40_pnl, 2),
        "Core10_pnl": round(c10_pnl, 2),
        "_trade_log": state.trade_log,
        "_reject_log": state.reject_log,
        "_equity_curve": state.equity_curve,
        "_daily_pnls": dict(daily_pnls),
    }


def build_daily_equity_rows(scenario_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    curve = list(scenario_result.get("_equity_curve") or [])
    daily_pnls = scenario_result.get("_daily_pnls") or {}
    if not curve:
        return []
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pt in curve:
        day = str(pt.get("timestamp_or_day") or "")[:8]
        if len(day) == 8 and day.isdigit():
            by_day[day].append(pt)
        else:
            ts = str(pt.get("timestamp_or_day") or "")
            day = _day_from_ts(ts) or "unknown"
            by_day[day].append(pt)

    rows: list[dict[str, Any]] = []
    cumulative_start = float(scenario_result.get("initial_equity") or INITIAL_EQUITY)
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
                "cumulative_return_pct": round((end_eq - cumulative_start) / cumulative_start * 100.0, 4),
                "max_intraday_drawdown": round(max_intra_dd, 2),
                "accepted_trade_count": "",
                "rejected_trade_count": "",
                "margin_warning_count": "",
                "force_exit_count": "",
                "min_maintenance_ratio": round(min(mrs), 4) if mrs else "",
                "max_gross_position_value": round(max(gross_vals), 2) if gross_vals else 0.0,
            }
        )
    return rows


def build_report(summary: Mapping[str, Any]) -> str:
    scenarios = list(summary.get("scenarios") or [])
    ref = summary.get("reference_unconstrained") or {}
    pop = summary.get("population") or {}
    best_pnl = max(scenarios, key=lambda s: float(s.get("final_equity") or 0.0)) if scenarios else {}
    best_dd = min(scenarios, key=lambda s: float(s.get("max_drawdown_yen") or 1e18)) if scenarios else {}
    lines = [
        "# Phase382 Capital-Constrained Backtest",
        "",
        f"**期間:** {pop.get('min_day')}–{pop.get('max_day')}",
        f"**Stack:** {summary.get('stack_id')}",
        f"**初期元本:** {INITIAL_EQUITY:,.0f}円",
        "",
        "## 結論",
        "",
        f"- **参照（制約なし100株）:** final_equity={ref.get('final_equity')} pnl={ref.get('total_return_yen')}",
        f"- **最高final_equity:** {best_pnl.get('scenario_id')} = {best_pnl.get('final_equity')}円 ({best_pnl.get('total_return_pct')}%)",
        f"- **最小DD:** {best_dd.get('scenario_id')} max_dd={best_dd.get('max_drawdown_yen')}円 ({best_dd.get('max_drawdown_pct')}%)",
        "",
        "## シナリオ別サマリー",
        "",
        "| scenario | final_equity | return% | max_dd | min_equity | floor_breach | min_maint | accepted | rejected |",
        "|---|---:|---:|---:|---:|---|---:|---:|---:|",
    ]
    for s in scenarios:
        lines.append(
            f"| {s.get('scenario_id')} | {s.get('final_equity')} | {s.get('total_return_pct')} | "
            f"{s.get('max_drawdown_yen')} | {s.get('min_equity')} | {s.get('equity_floor_breached')} | "
            f"{s.get('min_maintenance_ratio')} | {s.get('accepted_trade_count')} | {s.get('rejected_trade_count')} |"
        )
    lines.extend(
        [
            "",
            "## 判定",
            "",
            f"- **元本50万→最良final:** {best_pnl.get('final_equity')}円",
            f"- **equity_floor(25万)割れ:** {[s.get('scenario_id') for s in scenarios if s.get('equity_floor_breached')]}",
            f"- **固定100株(A)の危険度:** {'高' if best_pnl.get('scenario_id') == 'A_fixed_100_shares' and any(s.get('equity_floor_breached') for s in scenarios if s.get('scenario_id')=='A_fixed_100_shares') else '要確認'}",
            f"- **信用3倍の危険度:** min_maint={[s.get('scenario_id')+':'+str(s.get('min_maintenance_ratio')) for s in scenarios]}",
            f"- **推奨:** {summary.get('recommended_scenario')}",
            "",
            "## 除外",
            "",
            f"- input_trades: {pop.get('input_trade_count')}",
            f"- excluded_missing_data: {pop.get('excluded_trade_count')}",
            f"- exclusion_reasons: {pop.get('exclusion_reason_counts')}",
            "",
            "## 禁止事項",
            "",
            "- ENTRY/EXITロジック変更なし",
            "- Discord / canonical summary 変更なし",
            "- 新規ガード / Universe縮小なし",
        ]
    )
    return "\n".join(lines) + "\n"


def _recommend_scenario(scenarios: Sequence[Mapping[str, Any]]) -> str:
    viable = [
        s
        for s in scenarios
        if not s.get("equity_floor_breached")
        and float(s.get("min_maintenance_ratio") or 1.0) >= MAINT_FORCE_EXIT
        and float(s.get("final_equity") or 0.0) >= EQUITY_FLOOR
    ]
    if not viable:
        viable = list(scenarios)
    ranked = sorted(
        viable,
        key=lambda s: (
            -float(s.get("total_return_yen") or 0.0),
            float(s.get("max_drawdown_yen") or 1e18),
        ),
    )
    top = ranked[0] if ranked else {}
    return str(top.get("scenario_id") or "E_conservative")


def _try_plot_equity_curves(
    equity_curve_rows: Sequence[Mapping[str, Any]], reports_dir: Path
) -> list[str]:
    paths: list[str] = []
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return paths

    by_scenario: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for row in equity_curve_rows:
        sid = str(row.get("scenario") or "")
        ts = str(row.get("timestamp_or_day") or "")
        eq = float(row.get("equity") or 0.0)
        by_scenario[sid].append((ts, eq))

    if not by_scenario:
        return paths

    fig, ax = plt.subplots(figsize=(10, 5))
    for sid, pts in sorted(by_scenario.items()):
        if sid == REFERENCE_SCENARIO_ID:
            continue
        xs = list(range(len(pts)))
        ys = [p[1] for p in pts]
        ax.plot(xs, ys, label=sid)
    ax.axhline(EQUITY_FLOOR, color="red", linestyle="--", linewidth=1, label="equity_floor")
    ax.set_title("Phase382 Capital-Constrained Equity Curve")
    ax.set_xlabel("event_index")
    ax.set_ylabel("equity_yen")
    ax.legend(fontsize=7)
    fig.tight_layout()
    p1 = reports_dir / "phase382_capital_constrained_equity_curve.png"
    fig.savefig(p1, dpi=120)
    plt.close(fig)
    paths.append(str(p1))

    fig, ax = plt.subplots(figsize=(10, 4))
    for sid, pts in sorted(by_scenario.items()):
        if sid == REFERENCE_SCENARIO_ID:
            continue
        peak = INITIAL_EQUITY
        dd = []
        for _, eq in pts:
            peak = max(peak, eq)
            dd.append(peak - eq)
        ax.plot(range(len(dd)), dd, label=sid)
    ax.set_title("Phase382 Drawdown")
    ax.set_xlabel("event_index")
    ax.set_ylabel("drawdown_yen")
    ax.legend(fontsize=7)
    fig.tight_layout()
    p2 = reports_dir / "phase382_capital_constrained_drawdown.png"
    fig.savefig(p2, dpi=120)
    plt.close(fig)
    paths.append(str(p2))
    return paths


def dedupe_trades(trades: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    by_key: dict[str, dict[str, Any]] = {}
    removed = 0
    for trade in sorted(trades, key=lambda t: str(t.get("entry_time") or "")):
        key = _position_key(trade)
        if key in by_key:
            removed += 1
            continue
        by_key[key] = dict(trade)
    return list(by_key.values()), removed


def _scenario_worker(job: dict[str, Any]) -> dict[str, Any]:
    return simulate_scenario(
        job["trades"],
        scenario_id=job["scenario_id"],
        spec=job.get("spec"),
        unconstrained=bool(job.get("unconstrained")),
    )


@dataclass
class Phase382CapitalConstrainedBacktest:
    reports_dir: Path
    min_day: str = DEFAULT_MIN_DAY
    max_day: Optional[str] = DEFAULT_MAX_DAY
    all_trades: list[dict[str, Any]] = field(default_factory=list)
    excluded_trades: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase382_capital_constrained_summary.json",
            "daily_equity": self.reports_dir / "phase382_capital_constrained_daily_equity.csv",
            "trade_log": self.reports_dir / "phase382_capital_constrained_trade_log.csv",
            "rejects": self.reports_dir / "phase382_capital_constrained_rejects.csv",
            "equity_curve": self.reports_dir / "phase382_capital_constrained_equity_curve.csv",
            "report": self.reports_dir / "phase382_capital_constrained_report.md",
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
        trades = sorted(
            self.all_trades,
            key=lambda t: (_parse_ts(t.get("entry_time")) or datetime.min.replace(tzinfo=JST), str(t.get("symbol") or "")),
        )
        trades, duplicate_removed = dedupe_trades(trades)
        exclusion_counts: dict[str, int] = defaultdict(int)
        for ex in self.excluded_trades:
            exclusion_counts[str(ex.get("exclude_reason") or "unknown")] += 1

        jobs = [
            {"scenario_id": sid, "spec": SCENARIO_SPECS[sid], "trades": trades, "unconstrained": False}
            for sid in SCENARIO_SPECS
        ]
        jobs.append(
            {"scenario_id": REFERENCE_SCENARIO_ID, "spec": {}, "trades": trades, "unconstrained": True}
        )

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

        ref = next((r for r in results if r.get("scenario_id") == REFERENCE_SCENARIO_ID), {})
        scenarios = [r for r in results if r.get("scenario_id") != REFERENCE_SCENARIO_ID]
        scenarios.sort(key=lambda r: str(r.get("scenario_id") or ""))

        unconstrained_pnl = round(
            sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in trades), 2
        )
        consistency = {
            "input_trade_count_raw": len(self.all_trades),
            "duplicate_session_trades_removed": duplicate_removed,
            "input_trade_count": len(trades),
            "reference_trade_count": ref.get("accepted_trade_count"),
            "reference_pnl_yen_100_sum": unconstrained_pnl,
            "reference_realized_pnl": ref.get("realized_pnl"),
            "pnl_matches_unconstrained": ref.get("realized_pnl") == unconstrained_pnl,
        }
        p377_path = self.reports_dir / "phase377_daily_regime_breakdown_summary.json"
        if p377_path.is_file():
            p377 = json.loads(p377_path.read_text(encoding="utf-8"))
            stack_b = (p377.get("period_metrics") or {}).get("period_b_20260528_20260612", {}).get(PRIMARY_STACK, {})
            full_b_pnl = _float(stack_b.get("total_pnl_yen_100"))
            if full_b_pnl is not None and self.min_day > "20260528":
                consistency["phase377_period_b_note"] = (
                    "subset_period_excludes_20260528; compare reference_pnl to phase377 slice"
                )
            else:
                consistency["phase377_period_b_pnl"] = full_b_pnl
                consistency["phase377_trade_count"] = stack_b.get("trade_count")

        recommended = _recommend_scenario(scenarios)
        return {
            "phase": 382,
            "title": "Capital-constrained backtest",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "stack_id": PRIMARY_STACK,
            "hard_stop_pct": HARD_STOP_PCT,
            "initial_equity": INITIAL_EQUITY,
            "equity_floor": EQUITY_FLOOR,
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
            "reference_unconstrained": {k: v for k, v in ref.items() if not str(k).startswith("_")},
            "scenarios": [{k: v for k, v in s.items() if not str(k).startswith("_")} for s in scenarios],
            "recommended_scenario": recommended,
            "consistency_checks": consistency,
            "wall_runtime_sec": round(wall_runtime_sec, 2),
            "_scenario_results": results,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        trade_logs: list[dict[str, Any]] = []
        reject_logs: list[dict[str, Any]] = []
        equity_curve: list[dict[str, Any]] = []
        daily_equity: list[dict[str, Any]] = []
        for sr in result.get("_scenario_results") or []:
            trade_logs.extend(sr.get("_trade_log") or [])
            reject_logs.extend(sr.get("_reject_log") or [])
            equity_curve.extend(sr.get("_equity_curve") or [])
            daily_equity.extend(build_daily_equity_rows(sr))

        payload = {k: v for k, v in result.items() if not str(k).startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_csv(paths["trade_log"], trade_logs, TRADE_LOG_FIELDS)
        _write_csv(paths["rejects"], reject_logs, REJECT_FIELDS)
        _write_csv(paths["daily_equity"], daily_equity, DAILY_EQUITY_FIELDS)
        _write_csv(paths["equity_curve"], equity_curve, EQUITY_CURVE_FIELDS)
        paths["report"].write_text(build_report(payload), encoding="utf-8")
        plot_paths = _try_plot_equity_curves(equity_curve, self.reports_dir)
        if plot_paths:
            payload["plot_paths"] = plot_paths
            paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return paths
