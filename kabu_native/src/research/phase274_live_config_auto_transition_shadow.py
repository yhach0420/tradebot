"""
Phase274-Live-Config-Auto-Transition-Shadow.

Forward shadow: start at 1.5M and auto-switch to 2M+ cap/stop when equity >= 2M.
Observation only — no Runtime / Universe / Entry / Exit / YAML changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.equity_curve_shadow import (
    FIXED_SPEC,
    PERIOD_START,
    EquityCurveCapState,
    build_daily_equity_rows,
    compute_scenario_metrics,
    load_period_trades,
    pnl_for_actual_fixed_stop,
    pnl_for_dynamic_stop_risk_1p0,
)
from research.market_sector_heat import _write_csv
from research.phase269_portfolio_configuration_optimization import SHARES
from research.phase382_capital_constrained_backtest import _day_from_ts, _position_key
from research.phase383_realistic_credit_sizing_backtest import build_event_timeline
from research.phase385_cap_sensitivity_study import CapScenarioState
from research.research_output_layers import COMMON_RESEARCH_CONSTRAINTS

JST = ZoneInfo("Asia/Tokyo")
STARTING_EQUITY = 1_500_000
TRANSITION_EQUITY = 2_000_000
LEVERAGE = 2.0
MIN_FORWARD_DAY_COUNT = 10
DD_CAUTION_PCT = 20.0

STOP_RESOLVERS: dict[str, Callable[..., float]] = {
    "fixed_stop_1p2": pnl_for_actual_fixed_stop,
    "dynamic_stop_risk_1p0": pnl_for_dynamic_stop_risk_1p0,
}

EQUITY_CURVE_FIELDS = [
    "seq",
    "day",
    "timestamp",
    "event_type",
    "symbol",
    "current_equity",
    "active_policy_band",
    "cap_used",
    "stop_policy_used",
    "pnl_yen",
    "equity_after",
]

DAILY_EQUITY_FIELDS = [
    "day",
    "start_equity",
    "end_equity",
    "daily_pnl",
    "cumulative_return_pct",
    "drawdown_pct",
    "accepted_trade_count",
    "rejected_trade_count",
    "active_policy_band_end",
]

SCENARIO_ID = "live_config_auto_transition_1500k_to_2000k"


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def resolve_policy_band(current_equity: float) -> dict[str, Any]:
    if current_equity < TRANSITION_EQUITY:
        return {
            "active_policy_band": "1500k",
            "cap": 3,
            "stop_policy": "fixed_stop_1p2",
        }
    return {
        "active_policy_band": "2000k+",
        "cap": 5,
        "stop_policy": "dynamic_stop_risk_1p0",
    }


def compute_adoption_verdict(
    *,
    metrics: Mapping[str, Any],
    day_count: int,
    starting_equity: int = STARTING_EQUITY,
) -> dict[str, Any]:
    final_equity = float(metrics.get("final_equity") or starting_equity)
    days_below = int(metrics.get("days_below_50pct") or 0)
    max_dd = float(metrics.get("max_drawdown_pct") or 0.0)
    adopt_not_allowed = (
        day_count < MIN_FORWARD_DAY_COUNT
        or final_equity <= starting_equity
        or days_below > 0
    )
    caution = max_dd > DD_CAUTION_PCT
    if adopt_not_allowed:
        verdict = "observe" if day_count < MIN_FORWARD_DAY_COUNT else "reject"
    elif caution:
        verdict = "caution"
    else:
        verdict = "adopt"
    return {
        "adopt_not_allowed": adopt_not_allowed,
        "caution": caution,
        "adoption_verdict": verdict,
    }


@dataclass
class TransitionEquityCurveCapState(EquityCurveCapState):
    transition_equity: float = float(TRANSITION_EQUITY)
    transition_day: Optional[str] = None
    transition_equity_curve: list[dict[str, Any]] = field(default_factory=list)
    _transition_seq: int = 0
    _active_band_end: str = "1500k"

    def _maybe_mark_transition(self, day: str) -> None:
        if self.transition_day is None and self.current_equity() >= self.transition_equity:
            self.transition_day = day

    def _record_transition(
        self,
        *,
        ts: str,
        day: str,
        event_type: str,
        symbol: str = "",
        policy: Mapping[str, Any],
        pnl_yen: Optional[float] = None,
    ) -> None:
        eq_before = round(self.current_equity() if pnl_yen is None else self.current_equity() - float(pnl_yen or 0.0), 2)
        eq_after = round(self.current_equity(), 2)
        self._transition_seq += 1
        self.transition_equity_curve.append(
            {
                "seq": self._transition_seq,
                "day": day,
                "timestamp": ts,
                "event_type": event_type,
                "symbol": symbol,
                "current_equity": eq_before,
                "active_policy_band": policy.get("active_policy_band"),
                "cap_used": policy.get("cap"),
                "stop_policy_used": policy.get("stop_policy"),
                "pnl_yen": "" if pnl_yen is None else round(float(pnl_yen), 2),
                "equity_after": eq_after,
            }
        )
        self._active_band_end = str(policy.get("active_policy_band") or "1500k")
        self._maybe_mark_transition(day)

    def _close_position(self, key: str, ts: str, day: str, *, forced: bool = False, force_reason: str = "") -> None:
        pos = self.open_positions.get(key)
        if not pos:
            return
        trade = pos["trade"]
        shares = int(pos["shares"])
        entry_equity = float(pos.get("entry_equity") or self.current_equity())
        stop_policy = str(pos.get("stop_policy") or "fixed_stop_1p2")
        resolver = STOP_RESOLVERS.get(stop_policy, pnl_for_actual_fixed_stop)
        pnl = resolver(trade, shares=shares, entry_equity=entry_equity)
        policy = {
            "active_policy_band": pos.get("active_policy_band") or resolve_policy_band(entry_equity)["active_policy_band"],
            "cap": pos.get("cap_used") or resolve_policy_band(entry_equity)["cap"],
            "stop_policy": stop_policy,
        }

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
                "cap": policy["cap"],
                "day": day,
                "symbol": trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "exit_time": ts or trade.get("exit_time"),
                "pnl_yen": pnl,
                "exit_reason": force_reason or str(trade.get("close_reason") or trade.get("exit_reason") or ""),
                "stop_policy": stop_policy,
                "active_policy_band": policy["active_policy_band"],
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
        self._record_transition(
            ts=ts,
            day=day,
            event_type="force_exit" if forced else "exit",
            symbol=str(trade.get("symbol") or ""),
            policy=policy,
            pnl_yen=pnl,
        )
        self._maybe_mark_transition(day)
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
        policy = resolve_policy_band(self.current_equity())
        self.max_concurrent_positions = int(policy["cap"])
        before = self.accepted_trade_count
        CapScenarioState.try_entry(self, trade, ts, day)
        key = _position_key(trade)
        if key in self.open_positions and self.accepted_trade_count > before:
            entry_equity = self.current_equity()
            pos = self.open_positions[key]
            pos["entry_equity"] = entry_equity
            pos["stop_policy"] = policy["stop_policy"]
            pos["cap_used"] = policy["cap"]
            pos["active_policy_band"] = policy["active_policy_band"]
            self.daily_accepted[day] += 1
            self._record_equity(
                ts=ts,
                day=day,
                event_type="entry",
                symbol=str(trade.get("symbol") or ""),
            )
            self._record_transition(
                ts=ts,
                day=day,
                event_type="entry",
                symbol=str(trade.get("symbol") or ""),
                policy=policy,
            )


def simulate_auto_transition(
    trades: Sequence[Mapping[str, Any]],
    *,
    starting_equity: int = STARTING_EQUITY,
    leverage: float = LEVERAGE,
) -> dict[str, Any]:
    spec = {**FIXED_SPEC, "leverage_limit": leverage, "sizing": "fixed_100_only"}
    state = TransitionEquityCurveCapState(
        scenario_id=SCENARIO_ID,
        max_concurrent_positions=3,
        spec=spec,
        initial_equity=float(starting_equity),
        equity_floor=float(starting_equity) * 0.5,
        pnl_resolver=pnl_for_actual_fixed_stop,
    )
    events = build_event_timeline(trades)
    if events:
        first_day = _day_from_ts(events[0][0].isoformat())
        start_policy = resolve_policy_band(float(starting_equity))
        state._record_equity(ts="", day=first_day, event_type="start")
        state._record_transition(
            ts="",
            day=first_day,
            event_type="start",
            policy=start_policy,
        )

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
    for row in daily_rows:
        day = str(row.get("day") or "")
        end_eq = float(row.get("end_equity") or starting_equity)
        row["active_policy_band_end"] = resolve_policy_band(end_eq)["active_policy_band"]

    metrics = compute_scenario_metrics(state, daily_rows=daily_rows)
    current_policy = resolve_policy_band(float(metrics.get("final_equity") or starting_equity))

    return {
        **metrics,
        "starting_equity": starting_equity,
        "leverage": leverage,
        "shares": SHARES,
        "current_equity": metrics.get("final_equity"),
        "active_policy_band": current_policy["active_policy_band"],
        "cap_used": current_policy["cap"],
        "stop_policy_used": current_policy["stop_policy"],
        "accepted_count": metrics.get("accepted_trade_count"),
        "rejected_count": metrics.get("rejected_trade_count"),
        "transition_day_to_2000k": state.transition_day,
        "transition_to_2000k": state.transition_day is not None,
        "_daily_rows": daily_rows,
        "_equity_curve": state.transition_equity_curve,
        "_state": state,
    }


def build_report_markdown(result: Mapping[str, Any]) -> str:
    summary = result.get("transition_summary") or {}
    adoption = summary.get("adoption_verdict") or {}
    lines = [
        "# Phase274 Live Config Auto Transition Shadow",
        "",
        "Forward shadow: 1.5M start with auto transition to 2M+ policy at equity >= 2M.",
        "",
        f"- generated_at: {result.get('generated_at')}",
        f"- current_equity: {summary.get('current_equity')}",
        f"- active_policy_band: {summary.get('active_policy_band')}",
        f"- cap_used: {summary.get('cap_used')}",
        f"- stop_policy_used: {summary.get('stop_policy_used')}",
        f"- transition_day_to_2000k: {summary.get('transition_day_to_2000k')}",
        f"- adoption_verdict: {adoption.get('adoption_verdict')}",
        "",
        str((result.get("verdict") or {}).get("note")),
        "",
    ]
    return "\n".join(lines)


def run_transition_shadow(
    *,
    repo_root: Path,
    reports_dir: Path,
    day: Optional[str] = None,
) -> dict[str, Any]:
    day = day or datetime.now(JST).strftime("%Y%m%d")
    trades, pop_meta = load_period_trades(repo_root, period_start=PERIOD_START)
    period_days = list(pop_meta.get("period_days") or [])
    last_run: dict[str, Any] = {"day": day}

    if day not in period_days:
        last_run["status"] = "skipped_no_structural_trades"
    elif not trades:
        last_run["status"] = "skipped_no_period_trades"
    else:
        last_run["status"] = "logged_forward_shadow"
        last_run["trade_count"] = pop_meta.get("input_trade_count")

    sim: dict[str, Any] = {}
    if trades:
        sim = simulate_auto_transition(trades)

    day_count = len(period_days)
    adoption = compute_adoption_verdict(metrics=sim, day_count=day_count) if sim else {
        "adopt_not_allowed": True,
        "caution": False,
        "adoption_verdict": "observe",
    }

    transition_summary = {
        "day_count": day_count,
        "period_days": period_days,
        "starting_equity": STARTING_EQUITY,
        "transition_equity_threshold": TRANSITION_EQUITY,
        "current_equity": sim.get("current_equity"),
        "active_policy_band": sim.get("active_policy_band"),
        "cap_used": sim.get("cap_used"),
        "stop_policy_used": sim.get("stop_policy_used"),
        "accepted_count": sim.get("accepted_count"),
        "rejected_count": sim.get("rejected_count"),
        "final_equity": sim.get("final_equity"),
        "total_return_pct": sim.get("total_return_pct"),
        "max_drawdown_pct": sim.get("max_drawdown_pct"),
        "days_below_50pct": sim.get("days_below_50pct"),
        "transition_day_to_2000k": sim.get("transition_day_to_2000k"),
        "transition_to_2000k": sim.get("transition_to_2000k"),
        "adoption_verdict": adoption,
    }

    note = (
        "Forward shadow logging only; Runtime/Universe/Entry/Exit/YAML unchanged. "
        "Auto-transition policy: equity<2M → CAP3/fixed_stop; equity>=2M → CAP5/dynamic_stop."
    )

    paths = LiveConfigAutoTransitionShadow(repo_root=repo_root, reports_dir=reports_dir).paths()
    return {
        "phase": "274-Live-Config-Auto-Transition-Shadow",
        "title": "Live config auto transition forward shadow",
        "generated_at": _now_iso(),
        "purpose": "Shadow 1.5M live start with automatic 2M+ policy transition",
        "constraints": {
            **COMMON_RESEARCH_CONSTRAINTS,
            "forward_shadow_logging_only": True,
        },
        "policy": {
            "starting_equity": STARTING_EQUITY,
            "leverage": LEVERAGE,
            "shares": SHARES,
            "transition_equity_threshold": TRANSITION_EQUITY,
            "band_1500k": {"cap": 3, "stop_policy": "fixed_stop_1p2"},
            "band_2000k_plus": {"cap": 5, "stop_policy": "dynamic_stop_risk_1p0"},
        },
        "population": pop_meta,
        "output_paths": {k: str(v) for k, v in paths.items()},
        "transition_summary": transition_summary,
        "last_run": last_run,
        "verdict": {"note": note},
        "_equity_curve": sim.get("_equity_curve") or [],
        "_daily_rows": sim.get("_daily_rows") or [],
    }


@dataclass
class LiveConfigAutoTransitionShadow:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "equity_curve": self.reports_dir / "phase274_live_config_transition_equity_curve.csv",
            "daily_equity": self.reports_dir / "phase274_live_config_transition_daily_equity.csv",
            "summary": self.reports_dir / "phase274_live_config_transition_summary.json",
            "report": self.reports_dir / "phase274_live_config_transition_report.md",
        }

    def run(self, *, day: Optional[str] = None) -> dict[str, Any]:
        return run_transition_shadow(
            repo_root=self.repo_root,
            reports_dir=self.reports_dir,
            day=day,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["equity_curve"].parent.mkdir(parents=True, exist_ok=True)
        _write_csv(paths["equity_curve"], EQUITY_CURVE_FIELDS, result.get("_equity_curve") or [])
        _write_csv(paths["daily_equity"], DAILY_EQUITY_FIELDS, result.get("_daily_rows") or [])
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths["report"].write_text(build_report_markdown(result), encoding="utf-8")
        return paths
