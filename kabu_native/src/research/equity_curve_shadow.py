"""
Phase267-Equity-Curve-Shadow.

Simulate equity curves for 1.5M / credit 2x / 100 shares / CAP=2 under
actual fixed stop vs dynamic_stop_risk_1p0. Research only.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.equity_dynamic_stop_shadow import (
    PERIOD_START,
    compute_stop_fields,
    shadow_pnl_yen,
)
from research.market_sector_heat import _norm_symbol, load_trades_by_day
from research.phase374_dynamic40_universe_quality_review import resolve_pnl_yen_100
from research.phase382_capital_constrained_backtest import (
    _day_from_ts,
    _float,
    _gross_position_value,
    _parse_ts,
    _pf,
    _position_key,
    _trade_pnl_yen,
    _write_csv,
    dedupe_trades,
)
from research.phase383_realistic_credit_sizing_backtest import build_event_timeline
from research.phase385_cap_sensitivity_study import (
    DEFAULT_LEVERAGE,
    FIXED_SPEC,
    CapScenarioState,
)
from research.research_output_layers import (
    COMMON_RESEARCH_CONSTRAINTS,
    build_dual_layer_bundle,
    build_live_simulation_layer_from_equity_metrics,
    build_research_layer,
    format_dual_layer_markdown,
)

JST = ZoneInfo("Asia/Tokyo")

STARTING_EQUITY = 1_500_000.0
EQUITY_FLOOR = STARTING_EQUITY * 0.5
POSITION_CAP = 2
RISK_PCT_1P0 = 0.01

SCENARIO_ACTUAL = "actual_fixed_stop"
SCENARIO_DYNAMIC = "dynamic_stop_risk_1p0"

EQUITY_CURVE_FIELDS = [
    "scenario",
    "seq",
    "day",
    "timestamp",
    "event_type",
    "symbol",
    "equity",
    "drawdown_yen",
    "drawdown_pct",
    "pnl_yen",
    "gross_position_value",
]

DAILY_EQUITY_FIELDS = [
    "day",
    "scenario",
    "start_equity",
    "end_equity",
    "daily_pnl",
    "cumulative_return_pct",
    "drawdown_yen",
    "drawdown_pct",
    "accepted_trade_count",
    "rejected_trade_count",
]

DRAWDOWN_FIELDS = [
    "scenario",
    "day",
    "end_equity",
    "peak_equity",
    "drawdown_yen",
    "drawdown_pct",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _actual_pnl_pct(trade: Mapping[str, Any]) -> float:
    pct = _float(trade.get("realized_pnl_pct"))
    if pct is not None:
        return pct
    pct = _float(trade.get("pnl_pct"))
    if pct is not None:
        return pct
    ep = _float(trade.get("entry_price")) or 0.0
    pnl100 = _float(trade.get("pnl_yen_100"))
    if pnl100 is not None and ep > 0:
        return pnl100 / (ep * 100.0) * 100.0
    return 0.0


def pnl_for_actual_fixed_stop(trade: Mapping[str, Any], *, shares: int, entry_equity: float) -> float:
    del entry_equity
    return _trade_pnl_yen(trade, shares)


def pnl_for_dynamic_stop_risk_1p0(trade: Mapping[str, Any], *, shares: int, entry_equity: float) -> float:
    entry_price = _float(trade.get("entry_price")) or 0.0
    stop = compute_stop_fields(
        entry_price=entry_price,
        shares=shares,
        equity_yen=int(max(entry_equity, 0.0)),
        risk_pct=RISK_PCT_1P0,
    )
    return shadow_pnl_yen(
        entry_price=entry_price,
        shares=shares,
        actual_pnl_pct=_actual_pnl_pct(trade),
        mae_pct=_float(trade.get("mae_pct")),
        effective_stop_pct=_float(stop.get("effective_stop_pct")) or 1.2,
    )


PNL_RESOLVERS: dict[str, Callable[..., float]] = {
    SCENARIO_ACTUAL: pnl_for_actual_fixed_stop,
    SCENARIO_DYNAMIC: pnl_for_dynamic_stop_risk_1p0,
}


def normalize_structural_trade(row: Mapping[str, Any]) -> dict[str, Any]:
    trade = dict(row)
    trade["symbol"] = _norm_symbol(str(trade.get("symbol") or ""))
    if not trade.get("exit_time"):
        trade["exit_time"] = trade.get("close_time")
    if trade.get("realized_pnl_pct") is None:
        trade["realized_pnl_pct"] = trade.get("pnl_pct")
    if trade.get("pnl_yen_100") is None:
        trade["pnl_yen_100"] = resolve_pnl_yen_100(trade)
    return trade


def load_period_trades(
    repo_root: Path,
    *,
    period_start: str = PERIOD_START,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw = load_trades_by_day(repo_root)
    all_rows: list[dict[str, Any]] = []
    days_in_range: list[str] = []
    for day in sorted(raw.keys()):
        if day < period_start:
            continue
        days_in_range.append(day)
        for row in raw.get(day) or []:
            trade = normalize_structural_trade(row)
            if _parse_ts(trade.get("entry_time")) is None or _parse_ts(trade.get("exit_time")) is None:
                continue
            if (_float(trade.get("entry_price")) or 0.0) <= 0:
                continue
            all_rows.append(trade)
    deduped, removed = dedupe_trades(all_rows)
    deduped.sort(
        key=lambda t: (
            _parse_ts(t.get("entry_time")) or datetime.min.replace(tzinfo=JST),
            str(t.get("symbol") or ""),
        )
    )
    meta = {
        "period_start": period_start,
        "period_days": days_in_range,
        "period_day_count": len(days_in_range),
        "input_trade_count_raw": len(all_rows),
        "duplicate_trades_removed": removed,
        "input_trade_count": len(deduped),
    }
    return deduped, meta


@dataclass
class EquityCurveCapState(CapScenarioState):
    pnl_resolver: Callable[..., float] = pnl_for_actual_fixed_stop
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    daily_accepted: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    daily_rejected: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _seq: int = 0

    def _record_equity(self, *, ts: str, day: str, event_type: str, symbol: str = "", pnl_yen: Optional[float] = None) -> None:
        eq = round(self.current_equity(), 2)
        gross = round(_gross_position_value(self.open_positions), 2)
        dd_yen = round(self.peak_equity - eq, 2)
        dd_pct = round(dd_yen / self.peak_equity * 100.0, 4) if self.peak_equity > 0 else 0.0
        self._seq += 1
        self.equity_curve.append(
            {
                "scenario": self.scenario_id,
                "seq": self._seq,
                "day": day,
                "timestamp": ts,
                "event_type": event_type,
                "symbol": symbol,
                "equity": eq,
                "drawdown_yen": dd_yen,
                "drawdown_pct": dd_pct,
                "pnl_yen": "" if pnl_yen is None else round(pnl_yen, 2),
                "gross_position_value": gross,
            }
        )

    def _close_position(self, key: str, ts: str, day: str, *, forced: bool = False, force_reason: str = "") -> None:
        pos = self.open_positions.get(key)
        if not pos:
            return
        trade = pos["trade"]
        shares = int(pos["shares"])
        entry_equity = float(pos.get("entry_equity") or self.current_equity())
        pnl = self.pnl_resolver(trade, shares=shares, entry_equity=entry_equity)
        self.open_positions.pop(key, None)
        self.realized_pnl += pnl
        self.realized_pnls.append(pnl)
        self.daily_pnls[day] += pnl
        if forced:
            self.force_exit_count += 1
        eq = self.current_equity()
        self.peak_equity = max(self.peak_equity, eq)
        self.min_equity = min(self.min_equity, eq)
        self.trade_log.append(
            {
                "cap": self.max_concurrent_positions,
                "day": day,
                "symbol": trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "exit_time": ts or trade.get("exit_time"),
                "pnl_yen": pnl,
                "exit_reason": force_reason or str(trade.get("close_reason") or trade.get("exit_reason") or ""),
                "trade": trade,
            }
        )
        self._record_equity(
            ts=ts,
            day=day,
            event_type="force_exit" if forced else "exit",
            symbol=str(trade.get("symbol") or ""),
            pnl_yen=pnl,
        )
        if self.current_equity() < self.equity_floor and not self.equity_floor_breached:
            self.equity_floor_breached = True
            self.trading_halted = True
            self._force_close_all(ts, day, reason="equity_floor_breach")

    def _reject_entry(self, trade: Mapping[str, Any], reason: str) -> None:
        super()._reject_entry(trade, reason)
        day = _day_from_ts(str(trade.get("entry_time") or "")) or ""
        if day:
            self.daily_rejected[day] += 1

    def try_entry(self, trade: Mapping[str, Any], ts: str, day: str) -> None:
        before = self.accepted_trade_count
        super().try_entry(trade, ts, day)
        key = _position_key(trade)
        if key in self.open_positions and self.accepted_trade_count > before:
            self.open_positions[key]["entry_equity"] = self.current_equity()
            self.daily_accepted[day] += 1
            self._record_equity(
                ts=ts,
                day=day,
                event_type="entry",
                symbol=str(trade.get("symbol") or ""),
            )


def simulate_equity_curve_scenario(
    trades: Sequence[Mapping[str, Any]],
    *,
    scenario_id: str,
    pnl_resolver: Callable[..., float],
    initial_equity: float = STARTING_EQUITY,
    equity_floor: float = EQUITY_FLOOR,
    cap: int = POSITION_CAP,
    spec: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    run_spec = dict(spec if spec is not None else FIXED_SPEC)
    state = EquityCurveCapState(
        scenario_id=scenario_id,
        max_concurrent_positions=cap,
        spec=run_spec,
        initial_equity=initial_equity,
        equity_floor=equity_floor,
        pnl_resolver=pnl_resolver,
    )
    events = build_event_timeline(trades)
    if events:
        first_day = _day_from_ts(events[0][0].isoformat())
        state._record_equity(ts="", day=first_day, event_type="start")

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

    daily_rows = build_daily_equity_rows(state)
    drawdown_rows = build_drawdown_rows(state, daily_rows)
    metrics = compute_scenario_metrics(state, daily_rows=daily_rows)

    return {
        **metrics,
        "_equity_curve": state.equity_curve,
        "_daily_rows": daily_rows,
        "_drawdown_rows": drawdown_rows,
        "_state": state,
    }


def build_daily_equity_rows(state: EquityCurveCapState) -> list[dict[str, Any]]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pt in state.equity_curve:
        day = str(pt.get("day") or "")
        if len(day) == 8 and day.isdigit():
            by_day[day].append(pt)

    rows: list[dict[str, Any]] = []
    peak_equity = state.initial_equity
    for day in sorted(by_day):
        pts = by_day[day]
        equities = [float(p.get("equity") or 0.0) for p in pts]
        start_eq = equities[0]
        end_eq = equities[-1]
        peak_equity = max(peak_equity, end_eq)
        dd_yen = round(peak_equity - end_eq, 2)
        dd_pct = round(dd_yen / peak_equity * 100.0, 4) if peak_equity > 0 else 0.0
        rows.append(
            {
                "day": day,
                "scenario": state.scenario_id,
                "start_equity": round(start_eq, 2),
                "end_equity": round(end_eq, 2),
                "daily_pnl": round(float(state.daily_pnls.get(day, 0.0)), 2),
                "cumulative_return_pct": round((end_eq - state.initial_equity) / state.initial_equity * 100.0, 4),
                "drawdown_yen": dd_yen,
                "drawdown_pct": dd_pct,
                "accepted_trade_count": int(state.daily_accepted.get(day, 0)),
                "rejected_trade_count": int(state.daily_rejected.get(day, 0)),
            }
        )
    return rows


def build_drawdown_rows(state: EquityCurveCapState, daily_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    peak = state.initial_equity
    rows: list[dict[str, Any]] = []
    for row in daily_rows:
        end_eq = float(row.get("end_equity") or 0.0)
        peak = max(peak, end_eq)
        dd_yen = round(peak - end_eq, 2)
        dd_pct = round(dd_yen / peak * 100.0, 4) if peak > 0 else 0.0
        rows.append(
            {
                "scenario": state.scenario_id,
                "day": row.get("day"),
                "end_equity": round(end_eq, 2),
                "peak_equity": round(peak, 2),
                "drawdown_yen": dd_yen,
                "drawdown_pct": dd_pct,
            }
        )
    return rows


def compute_scenario_metrics(
    state: EquityCurveCapState,
    *,
    daily_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    initial = state.initial_equity
    final_equity = round(state.current_equity(), 2)
    total_return_pct = round((final_equity - initial) / initial * 100.0, 4) if initial else 0.0

    peak_eq = initial
    max_dd_pct = 0.0
    max_dd_yen = 0.0
    for pt in state.equity_curve:
        eq = float(pt.get("equity") or 0.0)
        peak_eq = max(peak_eq, eq)
        dd_yen = peak_eq - eq
        dd_pct = dd_yen / peak_eq * 100.0 if peak_eq > 0 else 0.0
        max_dd_yen = max(max_dd_yen, dd_yen)
        max_dd_pct = max(max_dd_pct, dd_pct)

    calmar_ratio = round(total_return_pct / max_dd_pct, 4) if max_dd_pct > 0 else None
    days_to_double: Optional[int] = None
    days_below_50pct = 0
    floor = initial * 0.5
    for idx, row in enumerate(daily_rows, start=1):
        end_eq = float(row.get("end_equity") or 0.0)
        if days_to_double is None and end_eq >= initial * 2.0:
            days_to_double = idx
        if end_eq < floor:
            days_below_50pct += 1

    wins = sum(1 for p in state.realized_pnls if p > 0)
    return {
        "scenario": state.scenario_id,
        "initial_equity": initial,
        "final_equity": final_equity,
        "total_return_pct": total_return_pct,
        "max_drawdown_yen": round(max_dd_yen, 2),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "calmar_ratio": calmar_ratio,
        "days_to_double": days_to_double,
        "days_below_50pct": days_below_50pct,
        "accepted_trade_count": state.accepted_trade_count,
        "rejected_trade_count": state.rejected_trade_count,
        "profit_factor": _pf(state.realized_pnls),
        "win_rate": round(wins / len(state.realized_pnls), 4) if state.realized_pnls else 0.0,
        "min_equity": round(state.min_equity, 2),
        "equity_floor_breached": state.equity_floor_breached,
    }


def build_report(result: Mapping[str, Any]) -> str:
    pop = result.get("population") or {}
    actual = result.get("scenarios", {}).get(SCENARIO_ACTUAL) or {}
    dynamic = result.get("scenarios", {}).get(SCENARIO_DYNAMIC) or {}
    cmp_ = result.get("comparison") or {}
    dual = result.get("dual_layer") or {}
    actual_dual = dual.get(SCENARIO_ACTUAL) or {}
    dynamic_dual = dual.get(SCENARIO_DYNAMIC) or {}
    lines = [
        "# Phase267 Equity Curve Shadow",
        "",
        "Shadow equity curve for 1.5M / credit 2x / 100 shares / CAP=2.",
        "",
        f"- period: {pop.get('period_start')} onward ({pop.get('period_day_count')} days)",
        f"- trades: {pop.get('input_trade_count')}",
        "",
    ]
    lines.extend(format_dual_layer_markdown(actual_dual, title="actual_fixed_stop"))
    lines.extend(format_dual_layer_markdown(dynamic_dual, title="dynamic_stop_risk_1p0"))
    lines.extend(
        [
        "## Legacy scenario metrics",
        "",
        "## actual_fixed_stop",
        "",
        f"- final_equity: {actual.get('final_equity')}",
        f"- total_return_pct: {actual.get('total_return_pct')}",
        f"- max_drawdown_pct: {actual.get('max_drawdown_pct')}",
        f"- calmar_ratio: {actual.get('calmar_ratio')}",
        f"- days_to_double: {actual.get('days_to_double')}",
        f"- days_below_50pct: {actual.get('days_below_50pct')}",
        "",
        "## dynamic_stop_risk_1p0",
        "",
        f"- final_equity: {dynamic.get('final_equity')}",
        f"- total_return_pct: {dynamic.get('total_return_pct')}",
        f"- max_drawdown_pct: {dynamic.get('max_drawdown_pct')}",
        f"- calmar_ratio: {dynamic.get('calmar_ratio')}",
        f"- days_to_double: {dynamic.get('days_to_double')}",
        f"- days_below_50pct: {dynamic.get('days_below_50pct')}",
        "",
        "## Comparison",
        "",
        f"- delta_final_equity: {cmp_.get('delta_final_equity_yen')}",
        f"- delta_return_pct: {cmp_.get('delta_return_pct')}",
        f"- delta_max_drawdown_pct: {cmp_.get('delta_max_drawdown_pct')}",
        "",
        "Observation only; Runtime/Universe/Entry/Exit/YAML unchanged.",
        "Adoption uses Live Simulation final_equity, not Research PF.",
        "",
        ]
    )
    return "\n".join(lines)


def run_equity_curve_shadow(
    *,
    repo_root: Path,
    reports_dir: Path,
    period_start: str = PERIOD_START,
) -> dict[str, Any]:
    trades, population = load_period_trades(repo_root, period_start=period_start)
    actual = simulate_equity_curve_scenario(
        trades,
        scenario_id=SCENARIO_ACTUAL,
        pnl_resolver=pnl_for_actual_fixed_stop,
    )
    dynamic = simulate_equity_curve_scenario(
        trades,
        scenario_id=SCENARIO_DYNAMIC,
        pnl_resolver=pnl_for_dynamic_stop_risk_1p0,
    )

    public_actual = {k: v for k, v in actual.items() if not str(k).startswith("_")}
    public_dynamic = {k: v for k, v in dynamic.items() if not str(k).startswith("_")}

    all_static_pnls = [pnl_for_actual_fixed_stop(t, shares=100, entry_equity=STARTING_EQUITY) for t in trades]
    research_layer = build_research_layer(all_static_pnls, label="all_trades_static_actual_stop")
    dual_layer = {
        SCENARIO_ACTUAL: build_dual_layer_bundle(
            research_layer=research_layer,
            live_simulation_layer=build_live_simulation_layer_from_equity_metrics(
                public_actual,
                cap=POSITION_CAP,
                daily_rows=actual.get("_daily_rows") or [],
                starting_equity=STARTING_EQUITY,
                leverage=DEFAULT_LEVERAGE,
            ),
        ),
        SCENARIO_DYNAMIC: build_dual_layer_bundle(
            research_layer=research_layer,
            live_simulation_layer=build_live_simulation_layer_from_equity_metrics(
                public_dynamic,
                cap=POSITION_CAP,
                daily_rows=dynamic.get("_daily_rows") or [],
                starting_equity=STARTING_EQUITY,
                leverage=DEFAULT_LEVERAGE,
            ),
        ),
    }

    comparison = {
        "delta_final_equity_yen": round(
            float(dynamic.get("final_equity") or 0.0) - float(actual.get("final_equity") or 0.0),
            2,
        ),
        "delta_return_pct": round(
            float(dynamic.get("total_return_pct") or 0.0) - float(actual.get("total_return_pct") or 0.0),
            4,
        ),
        "delta_max_drawdown_pct": round(
            float(dynamic.get("max_drawdown_pct") or 0.0) - float(actual.get("max_drawdown_pct") or 0.0),
            4,
        ),
        "delta_calmar_ratio": (
            round(float(dynamic.get("calmar_ratio") or 0.0) - float(actual.get("calmar_ratio") or 0.0), 4)
            if dynamic.get("calmar_ratio") is not None and actual.get("calmar_ratio") is not None
            else None
        ),
    }

    return {
        "phase": "267-Equity-Curve-Shadow",
        "title": "Equity curve shadow",
        "generated_at": _now_iso(),
        "purpose": "Simulate 1.5M credit-2x CAP=2 equity curves under fixed vs dynamic stop",
        "constraints": dict(COMMON_RESEARCH_CONSTRAINTS),
        "output_standard": {
            "research_layer_fields": ["profit_factor", "total_pnl_yen", "win_rate"],
            "live_simulation_layer_fields": [
                "starting_equity",
                "leverage",
                "shares",
                "cap",
                "final_equity",
                "total_return_pct",
                "max_drawdown_pct",
                "days_below_50pct",
                "accepted_count",
                "rejected_count",
            ],
            "adoption_primary_metric": "final_equity",
        },
        "config": {
            "starting_equity": STARTING_EQUITY,
            "leverage_limit": DEFAULT_LEVERAGE,
            "shares": 100,
            "position_cap": POSITION_CAP,
            "equity_floor": EQUITY_FLOOR,
        },
        "population": population,
        "scenarios": {
            SCENARIO_ACTUAL: public_actual,
            SCENARIO_DYNAMIC: public_dynamic,
        },
        "dual_layer": dual_layer,
        "comparison": comparison,
        "_equity_curve_rows": (actual.get("_equity_curve") or []) + (dynamic.get("_equity_curve") or []),
        "_daily_rows": (actual.get("_daily_rows") or []) + (dynamic.get("_daily_rows") or []),
        "_drawdown_rows": (actual.get("_drawdown_rows") or []) + (dynamic.get("_drawdown_rows") or []),
    }


@dataclass
class EquityCurveShadow:
    repo_root: Path
    reports_dir: Path
    period_start: str = PERIOD_START

    def paths(self) -> dict[str, Path]:
        return {
            "equity_curve": self.reports_dir / "phase267_equity_curve.csv",
            "daily_equity": self.reports_dir / "phase267_daily_equity.csv",
            "drawdown": self.reports_dir / "phase267_drawdown.csv",
            "summary": self.reports_dir / "phase267_equity_curve_summary.json",
            "report": self.reports_dir / "phase267_report.md",
        }

    def run(self) -> dict[str, Any]:
        return run_equity_curve_shadow(
            repo_root=self.repo_root,
            reports_dir=self.reports_dir,
            period_start=self.period_start,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["equity_curve"].parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in result.items() if not str(k).startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _write_csv(paths["equity_curve"], list(result.get("_equity_curve_rows") or []), EQUITY_CURVE_FIELDS)
        _write_csv(paths["daily_equity"], list(result.get("_daily_rows") or []), DAILY_EQUITY_FIELDS)
        _write_csv(paths["drawdown"], list(result.get("_drawdown_rows") or []), DRAWDOWN_FIELDS)
        paths["report"].write_text(build_report(result), encoding="utf-8")
        return paths
