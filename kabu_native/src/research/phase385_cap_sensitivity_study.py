"""
Phase385: Concurrent position cap sensitivity study (Stack C).

Tests whether max_concurrent_positions=3 is the accepted-rate bottleneck
under Phase384-recommended conditions (2M yen, credit 2x, 100 shares fixed).
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase377_daily_regime_breakdown import PRIMARY_STACK
from research.phase379_380_period_b_eval import is_low_mfe_stop
from research.phase382_capital_constrained_backtest import (
    HARD_STOP_PCT,
    LOT_SIZE,
    MAINT_FORCE_EXIT,
    MAINT_STOP_ENTRY,
    MAINT_WARNING,
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
from research.phase383_realistic_credit_sizing_backtest import (
    build_event_timeline,
    compute_buying_power,
    compute_requested_shares,
)

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_MIN_DAY = "20260529"
DEFAULT_MAX_DAY = "20260612"
DEFAULT_INITIAL_EQUITY = 2_000_000.0
DEFAULT_EQUITY_FLOOR = 1_000_000.0
DEFAULT_LEVERAGE = 2.0
BASELINE_CAP = 3

CAP_LEVELS: tuple[int, ...] = (1, 2, 3, 4, 5, 6)

FIXED_SPEC: dict[str, Any] = {
    "label": "Credit 2x 100 shares fixed",
    "leverage_limit": DEFAULT_LEVERAGE,
    "sizing": "fixed_100_only",
}


def cap_scenario_id(cap: int) -> str:
    return f"CAP_{cap}"


def _exit_reason(trade: Mapping[str, Any]) -> str:
    return str(trade.get("exit_reason_canonical") or trade.get("exit_reason") or "")


def _count_exit_reasons(
    exit_rows: Sequence[Mapping[str, Any]],
    trade_lookup: Mapping[tuple[Any, Any], Mapping[str, Any]],
) -> dict[str, Any]:
    stop_hit = low_mfe = trailing = overlap = 0
    stop_pnl = low_pnl = trail_pnl = overlap_pnl = 0.0
    for row in exit_rows:
        trade = trade_lookup.get((row.get("symbol"), row.get("entry_time")), {})
        reason = _exit_reason(trade)
        pnl = float(row.get("pnl_yen") or 0.0)
        if reason == "stop_hit":
            stop_hit += 1
            stop_pnl += pnl
            if is_low_mfe_stop(trade):
                low_mfe += 1
                low_pnl += pnl
        elif reason == "trailing_mfe_exit":
            trailing += 1
            trail_pnl += pnl
        elif reason == "overlap_replaced":
            overlap += 1
            overlap_pnl += pnl
    return {
        "stop_hit_count": stop_hit,
        "stop_hit_pnl_yen": round(stop_pnl, 2),
        "low_mfe_stop_count": low_mfe,
        "low_mfe_stop_pnl_yen": round(low_pnl, 2),
        "trailing_mfe_exit_count": trailing,
        "trailing_mfe_exit_pnl_yen": round(trail_pnl, 2),
        "overlap_replaced_count": overlap,
        "overlap_replaced_pnl_yen": round(overlap_pnl, 2),
    }


@dataclass
class CapScenarioState:
    scenario_id: str
    max_concurrent_positions: int
    spec: dict[str, Any]
    initial_equity: float
    equity_floor: float
    realized_pnl: float = 0.0
    open_positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    accepted_keys: set[str] = field(default_factory=set)
    trading_halted: bool = False
    equity_floor_breached: bool = False
    maintenance_warning_count: int = 0
    maintenance_stop_count: int = 0
    force_exit_count: int = 0
    position_cap_reject_count: int = 0
    insufficient_buying_power_count: int = 0
    accepted_trade_count: int = 0
    rejected_trade_count: int = 0
    maintenance_ratios: list[float] = field(default_factory=list)
    max_concurrent_positions_observed: int = 0
    max_gross_position_value: float = 0.0
    peak_equity: float = 0.0
    min_equity: float = 0.0
    trade_log: list[dict[str, Any]] = field(default_factory=list)
    realized_pnls: list[float] = field(default_factory=list)
    daily_pnls: dict[str, float] = field(default_factory=lambda: defaultdict(float))

    def __post_init__(self) -> None:
        self.peak_equity = self.initial_equity
        self.min_equity = self.initial_equity

    def current_equity(self) -> float:
        return self.initial_equity + self.realized_pnl

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
        pnl = _trade_pnl_yen(trade, shares)
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
                "exit_reason": force_reason or _exit_reason(trade),
                "trade": trade,
            }
        )
        if self.current_equity() < self.equity_floor and not self.equity_floor_breached:
            self.equity_floor_breached = True
            self.trading_halted = True
            self._force_close_all(ts, day, reason="equity_floor_breach")

    def _reject_entry(self, trade: Mapping[str, Any], reason: str) -> None:
        self.rejected_trade_count += 1
        if reason == "max_concurrent_positions":
            self.position_cap_reject_count += 1
        elif reason in ("insufficient_buying_power", "invalid_size", "invalid_price"):
            self.insufficient_buying_power_count += 1

    def try_entry(self, trade: Mapping[str, Any], ts: str, day: str) -> None:
        eq = self.current_equity()
        gross = _gross_position_value(self.open_positions)
        mr = self._maintenance_ratio(eq, gross)
        if mr is not None:
            self.maintenance_ratios.append(mr)
            if mr < MAINT_WARNING:
                self.maintenance_warning_count += 1

        if self.trading_halted or eq < self.equity_floor:
            self._reject_entry(trade, "equity_floor_breach")
            return

        if mr is not None and mr < MAINT_FORCE_EXIT:
            self._force_close_all(ts, day, reason="maintenance_ratio_force_exit")
            gross = _gross_position_value(self.open_positions)
            eq = self.current_equity()
            mr = self._maintenance_ratio(eq, gross)
        elif mr is not None and mr < MAINT_STOP_ENTRY:
            self.maintenance_stop_count += 1
            self._reject_entry(trade, "maintenance_ratio_stop")
            return

        if len(self.open_positions) >= self.max_concurrent_positions:
            self._reject_entry(trade, "max_concurrent_positions")
            return

        entry_price = float(_float(trade.get("entry_price")) or 0.0)
        buying_power = compute_buying_power(
            equity=eq,
            gross=gross,
            leverage_limit=float(self.spec.get("leverage_limit") or DEFAULT_LEVERAGE),
        )
        shares, reject_reason = compute_requested_shares(
            spec=self.spec,
            equity=eq,
            entry_price=entry_price,
            buying_power=buying_power,
        )
        if reject_reason:
            self._reject_entry(trade, reject_reason)
            return

        key = _position_key(trade)
        self.open_positions[key] = {"trade": trade, "shares": shares}
        self.accepted_keys.add(key)
        self.accepted_trade_count += 1
        gross_after = _gross_position_value(self.open_positions)
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

    def process_exit(self, trade: Mapping[str, Any], ts: str, day: str) -> None:
        key = _position_key(trade)
        if key not in self.accepted_keys or key not in self.open_positions:
            return
        self._close_position(key, ts, day)


def simulate_cap(
    trades: Sequence[Mapping[str, Any]],
    *,
    cap: int,
    initial_equity: float = DEFAULT_INITIAL_EQUITY,
    equity_floor: float = DEFAULT_EQUITY_FLOOR,
) -> dict[str, Any]:
    state = CapScenarioState(
        scenario_id=cap_scenario_id(cap),
        max_concurrent_positions=cap,
        spec=dict(FIXED_SPEC),
        initial_equity=initial_equity,
        equity_floor=equity_floor,
    )
    events = build_event_timeline(trades)
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
    total_pnl = round(state.realized_pnl, 2)
    total_return_pct = round(total_pnl / initial_equity * 100.0, 4) if initial_equity else 0.0
    max_dd_yen = round(state.peak_equity - state.min_equity, 2)
    max_dd_pct = round(max_dd_yen / state.peak_equity * 100.0, 4) if state.peak_equity > 0 else 0.0
    total_attempts = state.accepted_trade_count + state.rejected_trade_count
    reject_rate = round(state.rejected_trade_count / total_attempts, 4) if total_attempts else 0.0

    exit_rows = [r for r in state.trade_log if r.get("pnl_yen") not in ("", None)]
    trade_lookup = {(t.get("symbol"), t.get("entry_time")): t for t in trades}
    reason_stats = _count_exit_reasons(exit_rows, trade_lookup)
    wins = sum(1 for p in state.realized_pnls if p > 0)
    pf = _pf(state.realized_pnls)
    pnl_per_accepted = round(total_pnl / state.accepted_trade_count, 2) if state.accepted_trade_count else 0.0
    risk_adj = round(total_pnl / max_dd_yen, 4) if max_dd_yen > 0 else None

    return {
        "cap": cap,
        "scenario_id": cap_scenario_id(cap),
        "initial_equity": initial_equity,
        "leverage_limit": DEFAULT_LEVERAGE,
        "accepted_trade_count": state.accepted_trade_count,
        "rejected_trade_count": state.rejected_trade_count,
        "reject_rate": reject_rate,
        "accepted_rate": round(state.accepted_trade_count / total_attempts, 4) if total_attempts else 0.0,
        "position_cap_reject_count": state.position_cap_reject_count,
        "insufficient_buying_power_count": state.insufficient_buying_power_count,
        "total_pnl_yen_100": total_pnl,
        "return_pct": total_return_pct,
        "profit_factor": pf,
        "win_rate": round(wins / len(state.realized_pnls), 4) if state.realized_pnls else 0.0,
        "max_drawdown_yen": max_dd_yen,
        "max_drawdown_pct": max_dd_pct,
        "pnl_per_accepted_trade": pnl_per_accepted,
        "risk_adjusted_return": risk_adj,
        "min_equity": round(state.min_equity, 2),
        "final_equity": final_equity,
        "min_maintenance_ratio": round(min(state.maintenance_ratios), 4) if state.maintenance_ratios else None,
        "maintenance_warning_count": state.maintenance_warning_count,
        "maintenance_stop_count": state.maintenance_stop_count,
        "force_exit_count": state.force_exit_count,
        "equity_floor_breached": state.equity_floor_breached,
        "max_concurrent_positions_observed": state.max_concurrent_positions_observed,
        **reason_stats,
        "_daily_pnls": dict(state.daily_pnls),
        "_trade_log": state.trade_log,
    }


def build_cap3_comparison(results: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    baseline = next((r for r in results if int(r.get("cap") or 0) == BASELINE_CAP), {})
    base_pnl = float(baseline.get("total_pnl_yen_100") or 0.0)
    base_accepted = int(baseline.get("accepted_trade_count") or 0)
    out: dict[str, dict[str, Any]] = {}
    for row in results:
        cap = int(row.get("cap") or 0)
        pnl = float(row.get("total_pnl_yen_100") or 0.0)
        accepted = int(row.get("accepted_trade_count") or 0)
        delta_pnl = round(pnl - base_pnl, 2)
        delta_accepted = accepted - base_accepted
        gains = max(delta_pnl, 0.0)
        losses = abs(min(delta_pnl, 0.0))
        out[str(cap)] = {
            "delta_accepted_vs_cap3": delta_accepted,
            "delta_pnl_yen_vs_cap3": delta_pnl,
            "delta_gains_yen_vs_cap3": round(gains, 2),
            "delta_losses_yen_vs_cap3": round(losses, 2),
            "delta_stop_hit_vs_cap3": int(row.get("stop_hit_count") or 0) - int(baseline.get("stop_hit_count") or 0),
            "delta_low_mfe_stop_vs_cap3": int(row.get("low_mfe_stop_count") or 0) - int(baseline.get("low_mfe_stop_count") or 0),
            "delta_trailing_mfe_exit_vs_cap3": int(row.get("trailing_mfe_exit_count") or 0) - int(baseline.get("trailing_mfe_exit_count") or 0),
            "delta_overlap_replaced_vs_cap3": int(row.get("overlap_replaced_count") or 0) - int(baseline.get("overlap_replaced_count") or 0),
        }
    return out


def build_daily_rows(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    baseline = next((r for r in results if int(r.get("cap") or 0) == BASELINE_CAP), {})
    base_daily = baseline.get("_daily_pnls") or {}
    all_days = sorted({d for r in results for d in (r.get("_daily_pnls") or {})})
    rows: list[dict[str, Any]] = []
    for day in all_days:
        base_pnl = float(base_daily.get(day, 0.0))
        for row in results:
            cap = int(row.get("cap") or 0)
            daily = row.get("_daily_pnls") or {}
            pnl = float(daily.get(day, 0.0))
            delta = round(pnl - base_pnl, 2)
            rows.append(
                {
                    "day": day,
                    "cap": cap,
                    "daily_pnl_yen": round(pnl, 2),
                    "delta_pnl_vs_cap3": delta if cap != BASELINE_CAP else 0.0,
                    "improved_vs_cap3": cap != BASELINE_CAP and pnl > base_pnl,
                    "worsened_vs_cap3": cap != BASELINE_CAP and pnl < base_pnl,
                }
            )
    return rows


def build_robustness(daily_rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for cap in CAP_LEVELS:
        if cap == BASELINE_CAP:
            continue
        cap_rows = [r for r in daily_rows if int(r.get("cap") or 0) == cap]
        out[str(cap)] = {
            "improved_days": sum(1 for r in cap_rows if r.get("improved_vs_cap3")),
            "worsened_days": sum(1 for r in cap_rows if r.get("worsened_vs_cap3")),
            "unchanged_days": sum(1 for r in cap_rows if not r.get("improved_vs_cap3") and not r.get("worsened_vs_cap3")),
        }
    return out


def pick_best_caps(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    viable = [
        r
        for r in results
        if not r.get("equity_floor_breached")
        and int(r.get("force_exit_count") or 0) == 0
    ]
    pool = viable or list(results)

    best_pnl = max(pool, key=lambda r: float(r.get("total_pnl_yen_100") or 0.0))
    best_pf = max(
        [r for r in pool if r.get("profit_factor") is not None],
        key=lambda r: float(r.get("profit_factor") or 0.0),
        default=best_pnl,
    )
    best_risk = max(
        [r for r in pool if r.get("risk_adjusted_return") is not None],
        key=lambda r: float(r.get("risk_adjusted_return") or 0.0),
        default=best_pnl,
    )
    return {
        "best_pnl_cap": int(best_pnl.get("cap") or 0),
        "best_pf_cap": int(best_pf.get("cap") or 0),
        "best_risk_adjusted_cap": int(best_risk.get("cap") or 0),
    }


def build_recommendation(
    results: Sequence[Mapping[str, Any]],
    *,
    cap3_comparison: Mapping[str, Any],
    robustness: Mapping[str, Any],
    best_caps: Mapping[str, Any],
) -> dict[str, Any]:
    cap3 = next((r for r in results if int(r.get("cap") or 0) == BASELINE_CAP), {})
    cap4 = next((r for r in results if int(r.get("cap") or 0) == 4), {})
    cap5 = next((r for r in results if int(r.get("cap") or 0) == 5), {})
    cap6 = next((r for r in results if int(r.get("cap") or 0) == 6), {})

    cap3_pnl = float(cap3.get("total_pnl_yen_100") or 0.0)
    cap3_pf = float(cap3.get("profit_factor") or 0.0)
    cap3_dd = float(cap3.get("max_drawdown_yen") or 0.0)
    cap3_low_mfe = int(cap3.get("low_mfe_stop_count") or 0)

    def _viable_candidate(row: Mapping[str, Any]) -> bool:
        if not row:
            return False
        pf = float(row.get("profit_factor") or 0.0)
        dd = float(row.get("max_drawdown_yen") or 0.0)
        low_mfe = int(row.get("low_mfe_stop_count") or 0)
        if pf < cap3_pf * 0.95:
            return False
        if dd > cap3_dd * 1.35:
            return False
        if low_mfe > cap3_low_mfe + max(3, int(cap3_low_mfe * 0.25)):
            return False
        if int(row.get("force_exit_count") or 0) > 0:
            return False
        return True

    cap4_plus_better_pnl = any(
        float(r.get("total_pnl_yen_100") or 0.0) > cap3_pnl * 1.02
        for r in (cap4, cap5, cap6)
        if r
    )
    cap4_plus_viable = any(_viable_candidate(r) for r in (cap4, cap5, cap6) if r)

    recommended_cap = BASELINE_CAP
    candidates = [r for r in results if _viable_candidate(r)]
    if candidates:
        recommended_cap = int(
            max(candidates, key=lambda r: float(r.get("total_pnl_yen_100") or 0.0)).get("cap") or BASELINE_CAP
        )

    cap3_is_optimal_pnl = int(best_caps.get("best_pnl_cap") or 0) == BASELINE_CAP
    pf_maintained_at_best = True
    best_pnl_row = next((r for r in results if int(r.get("cap") or 0) == int(best_caps.get("best_pnl_cap") or 0)), {})
    if best_pnl_row and cap3_pf > 0:
        pf_maintained_at_best = float(best_pnl_row.get("profit_factor") or 0.0) >= cap3_pf * 0.98

    low_quality_increases = False
    for cap in (4, 5, 6):
        comp = cap3_comparison.get(str(cap)) or {}
        if int(comp.get("delta_low_mfe_stop_vs_cap3") or 0) > 2 and float(comp.get("delta_pnl_yen_vs_cap3") or 0) <= 0:
            low_quality_increases = True

    return {
        "is_cap3_optimal": cap3_is_optimal_pnl and recommended_cap == BASELINE_CAP,
        "cap4_plus_increases_profit": cap4_plus_better_pnl,
        "cap4_plus_viable": cap4_plus_viable,
        "pf_maintained": pf_maintained_at_best,
        "dd_increase_cap6_vs_cap3_yen": round(
            float(cap6.get("max_drawdown_yen") or 0.0) - cap3_dd, 2
        ) if cap6 else None,
        "dd_increase_cap6_vs_cap3_pct": round(
            (float(cap6.get("max_drawdown_pct") or 0.0) - float(cap3.get("max_drawdown_pct") or 0.0)), 4
        ) if cap6 and cap3 else None,
        "low_quality_trades_increase_with_cap": low_quality_increases,
        "recommended_live_cap": recommended_cap,
        "best_pnl_cap": best_caps.get("best_pnl_cap"),
        "best_pf_cap": best_caps.get("best_pf_cap"),
        "best_risk_adjusted_cap": best_caps.get("best_risk_adjusted_cap"),
        "robustness_vs_cap3": robustness,
    }


def build_trade_breakdown_rows(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in results:
        cap = int(row.get("cap") or 0)
        accepted = int(row.get("accepted_trade_count") or 0)
        total_pnl = float(row.get("total_pnl_yen_100") or 0.0)
        for reason_key, count_key, pnl_key in (
            ("stop_hit", "stop_hit_count", "stop_hit_pnl_yen"),
            ("low_mfe_stop", "low_mfe_stop_count", "low_mfe_stop_pnl_yen"),
            ("trailing_mfe_exit", "trailing_mfe_exit_count", "trailing_mfe_exit_pnl_yen"),
            ("overlap_replaced", "overlap_replaced_count", "overlap_replaced_pnl_yen"),
        ):
            cnt = int(row.get(count_key) or 0)
            pnl = float(row.get(pnl_key) or 0.0)
            rows.append(
                {
                    "cap": cap,
                    "exit_reason_group": reason_key,
                    "trade_count": cnt,
                    "pnl_yen": pnl,
                    "share_of_accepted": round(cnt / accepted, 4) if accepted else 0.0,
                    "share_of_pnl": round(pnl / total_pnl, 4) if total_pnl else 0.0,
                    "pnl_per_trade": round(pnl / cnt, 2) if cnt else 0.0,
                }
            )
    return rows


def build_report(summary: Mapping[str, Any]) -> str:
    rec = summary.get("recommendation") or {}
    results = list(summary.get("by_cap") or [])
    cap3 = next((r for r in results if int(r.get("cap") or 0) == BASELINE_CAP), {})
    lines = [
        "# Phase385 Concurrent Position Cap Sensitivity Study",
        "",
        f"**期間:** {summary.get('population', {}).get('min_day')}–{summary.get('population', {}).get('max_day')}",
        f"**条件:** 200万円 / 信用2倍 / 100株固定",
        "",
        "## 必須回答",
        "",
        f"- **CAP=3は最適か:** {'はい（PnL・推奨ともにCAP3）' if rec.get('is_cap3_optimal') else 'いいえ'}",
        f"- **CAP=4以上で利益は増えるか:** {'はい' if rec.get('cap4_plus_increases_profit') else 'いいえ'}（viable={rec.get('cap4_plus_viable')}）",
        f"- **PFは維持されるか:** {'はい' if rec.get('pf_maintained') else 'いいえ'}",
        f"- **DD悪化（CAP6 vs CAP3）:** {rec.get('dd_increase_cap6_vs_cap3_yen')}円 ({rec.get('dd_increase_cap6_vs_cap3_pct')}pt)",
        f"- **低品質トレード増加:** {'あり' if rec.get('low_quality_trades_increase_with_cap') else 'なし'}",
        f"- **ライブ運用推奨CAP:** **{rec.get('recommended_live_cap')}**",
        "",
        "## 最適CAP",
        "",
        f"- best_pnl_cap: {rec.get('best_pnl_cap')}",
        f"- best_pf_cap: {rec.get('best_pf_cap')}",
        f"- best_risk_adjusted_cap: {rec.get('best_risk_adjusted_cap')}",
        "",
        "## CAP=3基準",
        "",
        f"- accepted={cap3.get('accepted_trade_count')} reject_rate={round(float(cap3.get('reject_rate') or 0)*100,1)}%",
        f"- PnL={cap3.get('total_pnl_yen_100')} PF={cap3.get('profit_factor')} max_dd={cap3.get('max_drawdown_yen')}",
        "",
        "## 全CAP比較",
        "",
        "| CAP | accepted | reject% | PnL | PF | max_dd | pnl/trade | trailing | overlap | stop | low_mfe |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        lines.append(
            f"| {r.get('cap')} | {r.get('accepted_trade_count')} | {round(float(r.get('reject_rate') or 0)*100,1)}% | "
            f"{r.get('total_pnl_yen_100')} | {r.get('profit_factor')} | {r.get('max_drawdown_yen')} | "
            f"{r.get('pnl_per_accepted_trade')} | {r.get('trailing_mfe_exit_count')} | {r.get('overlap_replaced_count')} | "
            f"{r.get('stop_hit_count')} | {r.get('low_mfe_stop_count')} |"
        )
    lines.extend(["", "## CAP=3差分", ""])
    for cap, comp in sorted((summary.get("cap3_comparison") or {}).items(), key=lambda x: int(x[0])):
        if int(cap) == BASELINE_CAP:
            continue
        lines.append(
            f"- CAP={cap}: Δaccepted={comp.get('delta_accepted_vs_cap3')} ΔPnL={comp.get('delta_pnl_yen_vs_cap3')} "
            f"Δtrailing={comp.get('delta_trailing_mfe_exit_vs_cap3')} Δoverlap={comp.get('delta_overlap_replaced_vs_cap3')} "
            f"Δlow_mfe={comp.get('delta_low_mfe_stop_vs_cap3')}"
        )
    lines.extend(["", "## ロバスト性（日別 vs CAP3）", ""])
    for cap, rob in sorted((rec.get("robustness_vs_cap3") or {}).items(), key=lambda x: int(x[0])):
        lines.append(f"- CAP={cap}: improved_days={rob.get('improved_days')} worsened_days={rob.get('worsened_days')}")
    lines.extend(["", "## 禁止事項", "", "- ENTRY/EXIT/Universe/Discord/canonical 変更なし", ""])
    return "\n".join(lines) + "\n"


def _cap_worker(job: dict[str, Any]) -> dict[str, Any]:
    row = simulate_cap(
        job["trades"],
        cap=int(job["cap"]),
        initial_equity=float(job["initial_equity"]),
        equity_floor=float(job["equity_floor"]),
    )
    public = {k: v for k, v in row.items() if not str(k).startswith("_")}
    public["_daily_pnls"] = row.get("_daily_pnls")
    public["_trade_log"] = row.get("_trade_log")
    return public


@dataclass
class Phase385CapSensitivityStudy:
    reports_dir: Path
    min_day: str = DEFAULT_MIN_DAY
    max_day: Optional[str] = DEFAULT_MAX_DAY
    initial_equity: float = DEFAULT_INITIAL_EQUITY
    equity_floor: float = DEFAULT_EQUITY_FLOOR
    all_trades: list[dict[str, Any]] = field(default_factory=list)
    excluded_trades: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase385_cap_sensitivity_summary.json",
            "by_cap": self.reports_dir / "phase385_cap_sensitivity_by_cap.csv",
            "by_day": self.reports_dir / "phase385_cap_sensitivity_by_day.csv",
            "trade_breakdown": self.reports_dir / "phase385_cap_sensitivity_trade_breakdown.csv",
            "recommendation": self.reports_dir / "phase385_cap_sensitivity_recommendation.md",
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
                "cap": cap,
                "trades": trades,
                "initial_equity": self.initial_equity,
                "equity_floor": self.equity_floor,
            }
            for cap in CAP_LEVELS
        ]
        results: list[dict[str, Any]] = []
        if parallel and len(jobs) > 1:
            from concurrent.futures import ProcessPoolExecutor, as_completed

            with ProcessPoolExecutor(max_workers=max(1, max_workers)) as pool:
                futures = {pool.submit(_cap_worker, job): job for job in jobs}
                for fut in as_completed(futures):
                    results.append(fut.result())
        else:
            for job in jobs:
                results.append(_cap_worker(job))
        results.sort(key=lambda r: int(r.get("cap") or 0))

        cap3_comparison = build_cap3_comparison(results)
        daily_rows = build_daily_rows(results)
        robustness = build_robustness(daily_rows)
        best_caps = pick_best_caps(results)
        recommendation = build_recommendation(
            results,
            cap3_comparison=cap3_comparison,
            robustness=robustness,
            best_caps=best_caps,
        )
        trade_breakdown = build_trade_breakdown_rows(results)

        public_results = [{k: v for k, v in r.items() if not str(k).startswith("_")} for r in results]

        return {
            "phase": 385,
            "title": "Concurrent position cap sensitivity study",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "stack_id": PRIMARY_STACK,
            "hard_stop_pct": HARD_STOP_PCT,
            "initial_equity": self.initial_equity,
            "equity_floor": self.equity_floor,
            "leverage_limit": DEFAULT_LEVERAGE,
            "sizing": "fixed_100_only",
            "baseline_cap": BASELINE_CAP,
            "cap_levels": list(CAP_LEVELS),
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
            "cap3_comparison": cap3_comparison,
            "best_caps": best_caps,
            "recommendation": recommendation,
            "by_cap": public_results,
            "by_day": daily_rows,
            "trade_breakdown": trade_breakdown,
            "wall_runtime_sec": round(wall_runtime_sec, 2),
            "_raw_results": results,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        payload = {k: v for k, v in result.items() if not str(k).startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        by_cap_fields = [
            "cap",
            "accepted_trade_count",
            "rejected_trade_count",
            "reject_rate",
            "accepted_rate",
            "position_cap_reject_count",
            "insufficient_buying_power_count",
            "total_pnl_yen_100",
            "return_pct",
            "profit_factor",
            "win_rate",
            "max_drawdown_yen",
            "max_drawdown_pct",
            "pnl_per_accepted_trade",
            "risk_adjusted_return",
            "stop_hit_count",
            "low_mfe_stop_count",
            "trailing_mfe_exit_count",
            "overlap_replaced_count",
            "stop_hit_pnl_yen",
            "low_mfe_stop_pnl_yen",
            "trailing_mfe_exit_pnl_yen",
            "overlap_replaced_pnl_yen",
            "min_equity",
            "final_equity",
            "min_maintenance_ratio",
            "maintenance_warning_count",
            "maintenance_stop_count",
            "force_exit_count",
        ]
        _write_csv(paths["by_cap"], list(result.get("by_cap") or []), by_cap_fields)
        _write_csv(
            paths["by_day"],
            list(result.get("by_day") or []),
            ["day", "cap", "daily_pnl_yen", "delta_pnl_vs_cap3", "improved_vs_cap3", "worsened_vs_cap3"],
        )
        _write_csv(
            paths["trade_breakdown"],
            list(result.get("trade_breakdown") or []),
            ["cap", "exit_reason_group", "trade_count", "pnl_yen", "share_of_accepted", "share_of_pnl", "pnl_per_trade"],
        )
        paths["recommendation"].write_text(build_report(payload), encoding="utf-8")
        return paths


__all__ = [
    "CAP_LEVELS",
    "BASELINE_CAP",
    "Phase385CapSensitivityStudy",
    "simulate_cap",
]
