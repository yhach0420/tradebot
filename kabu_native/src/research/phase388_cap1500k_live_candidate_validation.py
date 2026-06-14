"""
Phase388: 1.5M live candidate validation (Stack C).

Validates initial_equity=1.5M, credit 2x, 100 shares, CAP=2 vs Phase385 2M baseline.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase377_daily_regime_breakdown import PRIMARY_STACK
from research.phase382_capital_constrained_backtest import (
    HARD_STOP_PCT,
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
from research.phase383_realistic_credit_sizing_backtest import build_event_timeline
from research.phase385_cap_sensitivity_study import (
    DEFAULT_LEVERAGE,
    FIXED_SPEC,
    CapScenarioState,
    _count_exit_reasons,
    _exit_reason,
    simulate_cap,
)

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_MIN_DAY = "20260529"
DEFAULT_MAX_DAY = "20260612"
CANDIDATE_EQUITY = 1_500_000.0
CANDIDATE_CAP = 2
REFERENCE_EQUITY = 2_000_000.0
PHASE385_REFERENCE_JSON = "phase385_cap_sensitivity_summary.json"

TRADE_LOG_FIELDS = [
    "scenario",
    "day",
    "symbol",
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "shares",
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

DAILY_EQUITY_FIELDS = [
    "day",
    "scenario",
    "start_equity",
    "end_equity",
    "daily_pnl",
    "cumulative_return_pct",
    "min_maintenance_ratio",
    "max_gross_position_value",
    "accepted_trade_count",
    "rejected_trade_count",
]


@dataclass
class DetailedCapState(CapScenarioState):
    reject_reason_counts: Counter[str] = field(default_factory=Counter)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    detailed_trade_log: list[dict[str, Any]] = field(default_factory=list)
    daily_accepted: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    daily_rejected: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def record_equity(self, ts: str, day: str) -> None:
        eq = self.current_equity()
        gross = _gross_position_value(self.open_positions)
        mr = self._maintenance_ratio(eq, gross)
        dd_yen = round(self.peak_equity - eq, 2)
        dd_pct = round(dd_yen / self.peak_equity * 100.0, 4) if self.peak_equity > 0 else 0.0
        self.equity_curve.append(
            {
                "timestamp_or_day": ts or day,
                "day": day,
                "scenario": self.scenario_id,
                "equity": round(eq, 2),
                "drawdown_yen": dd_yen,
                "drawdown_pct": dd_pct,
                "gross_position_value": round(gross, 2),
                "maintenance_ratio": round(mr, 4) if mr is not None else "",
            }
        )

    def _reject_entry(self, trade: Mapping[str, Any], reason: str) -> None:
        super()._reject_entry(trade, reason)
        self.reject_reason_counts[reason] += 1
        day = _day_from_ts(str(trade.get("entry_time") or "")) or ""
        if day:
            self.daily_rejected[day] += 1
        eq = self.current_equity()
        gross = _gross_position_value(self.open_positions)
        mr = self._maintenance_ratio(eq, gross)
        self.detailed_trade_log.append(
            {
                "scenario": self.scenario_id,
                "day": day,
                "symbol": trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "exit_time": trade.get("exit_time"),
                "entry_price": trade.get("entry_price"),
                "exit_price": trade.get("exit_price"),
                "shares": "",
                "pnl_yen": "",
                "pnl_pct": trade.get("pnl_pct"),
                "exit_reason": _exit_reason(trade),
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

    def _close_position(self, key: str, ts: str, day: str, *, forced: bool = False, force_reason: str = "") -> None:
        pos = self.open_positions.get(key)
        if not pos:
            return
        trade = pos["trade"]
        shares = int(pos["shares"])
        eq_before = self.current_equity()
        gross_before = _gross_position_value(self.open_positions)
        mr_before = self._maintenance_ratio(eq_before, gross_before)
        self.open_positions.pop(key, None)
        pnl = _trade_pnl_yen(trade, shares)
        self.realized_pnl += pnl
        self.realized_pnls.append(pnl)
        self.daily_pnls[day] += pnl
        if forced:
            self.force_exit_count += 1
        eq_after = self.current_equity()
        self.peak_equity = max(self.peak_equity, eq_after)
        self.min_equity = min(self.min_equity, eq_after)
        gross_after = _gross_position_value(self.open_positions)
        mr_after = self._maintenance_ratio(eq_after, gross_after)
        self.detailed_trade_log.append(
            {
                "scenario": self.scenario_id,
                "day": day,
                "symbol": trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "exit_time": ts or trade.get("exit_time"),
                "entry_price": trade.get("entry_price"),
                "exit_price": trade.get("exit_price"),
                "shares": shares,
                "pnl_yen": pnl,
                "pnl_pct": trade.get("pnl_pct"),
                "exit_reason": force_reason or _exit_reason(trade),
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
        self.record_equity(ts, day)
        if self.current_equity() < self.equity_floor and not self.equity_floor_breached:
            self.equity_floor_breached = True
            self.trading_halted = True
            self._force_close_all(ts, day, reason="equity_floor_breach")

    def try_entry(self, trade: Mapping[str, Any], ts: str, day: str) -> None:
        eq_before = self.current_equity()
        gross_before = _gross_position_value(self.open_positions)
        mr_before = self._maintenance_ratio(eq_before, gross_before)
        super().try_entry(trade, ts, day)
        key = _position_key(trade)
        if key in self.accepted_keys and key in self.open_positions:
            self.daily_accepted[day] += 1
            pos = self.open_positions[key]
            entry_price = float(_float(trade.get("entry_price")) or 0.0)
            shares = int(pos["shares"])
            gross_after = _gross_position_value(self.open_positions)
            mr_after = self._maintenance_ratio(self.current_equity(), gross_after)
            self.detailed_trade_log.append(
                {
                    "scenario": self.scenario_id,
                    "day": day,
                    "symbol": trade.get("symbol"),
                    "entry_time": trade.get("entry_time"),
                    "exit_time": "",
                    "entry_price": trade.get("entry_price"),
                    "exit_price": "",
                    "shares": shares,
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
            self.record_equity(ts, day)


def simulate_detailed(
    trades: Sequence[Mapping[str, Any]],
    *,
    scenario_id: str,
    cap: int,
    initial_equity: float,
    equity_floor: Optional[float] = None,
) -> dict[str, Any]:
    floor = equity_floor if equity_floor is not None else initial_equity * 0.5
    state = DetailedCapState(
        scenario_id=scenario_id,
        max_concurrent_positions=cap,
        spec=dict(FIXED_SPEC),
        initial_equity=initial_equity,
        equity_floor=floor,
    )
    events = build_event_timeline(trades)
    if events:
        state.record_equity("", events[0][0].astimezone(JST).strftime("%Y%m%d"))

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
    wins = sum(1 for p in state.realized_pnls if p > 0)

    exit_rows = [r for r in state.detailed_trade_log if r.get("accepted_or_rejected") == "accepted" and r.get("pnl_yen") not in ("", None)]
    trade_lookup = {(t.get("symbol"), t.get("entry_time")): t for t in trades}
    exit_stats = _count_exit_reasons(exit_rows, trade_lookup)

    return {
        "scenario_id": scenario_id,
        "initial_equity": initial_equity,
        "position_cap": cap,
        "leverage_limit": DEFAULT_LEVERAGE,
        "accepted_trade_count": state.accepted_trade_count,
        "rejected_trade_count": state.rejected_trade_count,
        "reject_rate": round(state.rejected_trade_count / total_attempts, 4) if total_attempts else 0.0,
        "reject_reason_breakdown": dict(state.reject_reason_counts),
        "total_pnl_yen": total_pnl,
        "return_pct": total_return_pct,
        "profit_factor": _pf(state.realized_pnls),
        "win_rate": round(wins / len(state.realized_pnls), 4) if state.realized_pnls else 0.0,
        "max_drawdown_yen": max_dd_yen,
        "max_drawdown_pct": max_dd_pct,
        "min_equity": round(state.min_equity, 2),
        "final_equity": final_equity,
        "min_maintenance_ratio": round(min(state.maintenance_ratios), 4) if state.maintenance_ratios else None,
        "maintenance_warning_count": state.maintenance_warning_count,
        "maintenance_stop_count": state.maintenance_stop_count,
        "force_exit_count": state.force_exit_count,
        "equity_floor_breached": state.equity_floor_breached,
        **exit_stats,
        "_equity_curve": state.equity_curve,
        "_trade_log": state.detailed_trade_log,
        "_daily_pnls": dict(state.daily_pnls),
        "_daily_accepted": dict(state.daily_accepted),
        "_daily_rejected": dict(state.daily_rejected),
    }


def build_daily_equity_rows(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    curve = list(result.get("_equity_curve") or [])
    daily_pnls = result.get("_daily_pnls") or {}
    daily_acc = result.get("_daily_accepted") or {}
    daily_rej = result.get("_daily_rejected") or {}
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pt in curve:
        day = str(pt.get("day") or pt.get("timestamp_or_day") or "")[:8]
        if len(day) == 8 and day.isdigit():
            by_day[day].append(pt)

    ie = float(result.get("initial_equity") or 0.0)
    rows: list[dict[str, Any]] = []
    for day in sorted(by_day):
        pts = by_day[day]
        equities = [float(p.get("equity") or 0.0) for p in pts]
        mrs = [float(p.get("maintenance_ratio")) for p in pts if p.get("maintenance_ratio") not in ("", None)]
        gross_vals = [float(p.get("gross_position_value") or 0.0) for p in pts]
        rows.append(
            {
                "day": day,
                "scenario": result.get("scenario_id"),
                "start_equity": round(equities[0], 2),
                "end_equity": round(equities[-1], 2),
                "daily_pnl": round(float(daily_pnls.get(day, 0.0)), 2),
                "cumulative_return_pct": round((equities[-1] - ie) / ie * 100.0, 4) if ie else 0.0,
                "min_maintenance_ratio": round(min(mrs), 4) if mrs else "",
                "max_gross_position_value": round(max(gross_vals), 2) if gross_vals else 0.0,
                "accepted_trade_count": int(daily_acc.get(day, 0)),
                "rejected_trade_count": int(daily_rej.get(day, 0)),
            }
        )
    return rows


def load_phase385_cap2_reference(reports_dir: Path) -> dict[str, Any]:
    path = reports_dir / PHASE385_REFERENCE_JSON
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        row = next((r for r in data.get("by_cap") or [] if int(r.get("cap") or 0) == CANDIDATE_CAP), None)
        if row:
            return {"loaded": True, "source": "phase385_summary", **row}
    return {"loaded": False}


def build_required_answers(
    candidate: Mapping[str, Any],
    reference: Mapping[str, Any],
) -> dict[str, Any]:
    c_pnl = float(candidate.get("total_pnl_yen") or 0.0)
    r_pnl = float(reference.get("total_pnl_yen_100") or reference.get("total_pnl_yen") or 0.0)
    c_min_mr = candidate.get("min_maintenance_ratio")
    min_mr_f = float(c_min_mr) if c_min_mr is not None else 1.0
    force_exit = int(candidate.get("force_exit_count") or 0)
    maint_stop = int(candidate.get("maintenance_stop_count") or 0)
    maint_warn = int(candidate.get("maintenance_warning_count") or 0)

    maintenance_safe = min_mr_f >= MAINT_STOP_ENTRY and force_exit == 0
    margin_call_risk = (
        force_exit > 0
        or min_mr_f < MAINT_FORCE_EXIT
        or maint_stop > 0
        or min_mr_f < MAINT_WARNING
    )

    live_viable = c_pnl > 0 and force_exit == 0 and not candidate.get("equity_floor_breached")
    recommend = "2000000"
    if c_pnl > 0 and maintenance_safe and (r_pnl - c_pnl) < c_pnl * 0.5:
        recommend = "1500000"
    elif c_pnl <= 0 or not maintenance_safe:
        recommend = "2000000"
    elif (r_pnl - c_pnl) >= 50000:
        recommend = "2000000"

    return {
        "is_1500k_profitable": c_pnl > 0,
        "pnl_delta_vs_2m_cap2_yen": round(c_pnl - r_pnl, 2),
        "pnl_delta_vs_2m_cap2_pct_of_reference": round(c_pnl / r_pnl * 100.0, 2) if r_pnl else None,
        "maintenance_safe": maintenance_safe,
        "min_maintenance_ratio": c_min_mr,
        "margin_call_risk": margin_call_risk,
        "margin_call_risk_detail": {
            "force_exit_count": force_exit,
            "maintenance_stop_count": maint_stop,
            "maintenance_warning_count": maint_warn,
            "below_warning_threshold_0p40": min_mr_f < MAINT_WARNING,
        },
        "live_operation_viable": live_viable,
        "recommended_capital_yen": int(recommend),
        "recommended_capital_label": "150万円" if recommend == "1500000" else "200万円",
    }


def build_report(summary: Mapping[str, Any]) -> str:
    cand = summary.get("candidate") or {}
    ref = summary.get("phase385_reference_cap2_2m") or {}
    ans = summary.get("required_answers") or {}
    cmp_ = summary.get("comparison_vs_phase385") or {}
    lines = [
        "# Phase388 1.5M Live Candidate Validation",
        "",
        f"**期間:** {summary.get('population', {}).get('min_day')}–{summary.get('population', {}).get('max_day')}",
        "**候補:** 150万円 / 信用2倍 / 100株 / CAP=2 / Stack C",
        "",
        "## 必須回答",
        "",
        f"- **150万円で黒字か:** {'はい' if ans.get('is_1500k_profitable') else 'いいえ'} ({cand.get('total_pnl_yen')}円)",
        f"- **200万円CAP2との差額:** {ans.get('pnl_delta_vs_2m_cap2_yen')}円 ({ans.get('pnl_delta_vs_2m_cap2_pct_of_reference')}% of 2M)",
        f"- **維持率は安全か:** {'はい' if ans.get('maintenance_safe') else 'いいえ'} (min_maint={ans.get('min_maintenance_ratio')})",
        f"- **追証リスク:** {'あり' if ans.get('margin_call_risk') else 'なし'}",
        f"- **ライブ運用可能か:** {'はい' if ans.get('live_operation_viable') else 'いいえ'}",
        f"- **推奨元本:** **{ans.get('recommended_capital_label')}**",
        "",
        "## 候補成績",
        "",
        f"- accepted={cand.get('accepted_trade_count')} rejected={cand.get('rejected_trade_count')}",
        f"- PnL={cand.get('total_pnl_yen')} return={cand.get('return_pct')}% PF={cand.get('profit_factor')}",
        f"- max_dd={cand.get('max_drawdown_yen')} min_equity={cand.get('min_equity')}",
        f"- reject_breakdown: {cand.get('reject_reason_breakdown')}",
        "",
        "## Phase385比較 (200万 CAP2)",
        "",
        f"- reference_pnl={ref.get('total_pnl_yen_100')} PF={ref.get('profit_factor')}",
        f"- delta_pnl={cmp_.get('delta_pnl_yen')}",
        f"- delta_accepted={cmp_.get('delta_accepted')}",
        "",
        "## 禁止事項",
        "",
        "- ENTRY/EXIT/Universe変更なし",
    ]
    return "\n".join(lines) + "\n"


@dataclass
class Phase388Cap1500kLiveCandidateValidation:
    reports_dir: Path
    min_day: str = DEFAULT_MIN_DAY
    max_day: Optional[str] = DEFAULT_MAX_DAY
    all_trades: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase388_cap1500k_validation_summary.json",
            "daily_equity": self.reports_dir / "phase388_cap1500k_daily_equity.csv",
            "trade_log": self.reports_dir / "phase388_cap1500k_trade_log.csv",
            "report": self.reports_dir / "phase388_cap1500k_report.md",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        self.all_trades.extend(result.get("all_trades") or result.get("valid_trades") or [])

    def run(
        self,
        *,
        wall_runtime_sec: float = 0.0,
        sessions_discovered: int = 0,
        sessions_evaluated: int = 0,
    ) -> dict[str, Any]:
        trades, duplicate_removed = dedupe_trades(self.all_trades)
        trades = sorted(
            trades,
            key=lambda t: (_parse_ts(t.get("entry_time")) or datetime.min.replace(tzinfo=JST), str(t.get("symbol") or "")),
        )

        candidate = simulate_detailed(
            trades,
            scenario_id="candidate_1500k_cap2",
            cap=CANDIDATE_CAP,
            initial_equity=CANDIDATE_EQUITY,
        )
        reference_sim = simulate_detailed(
            trades,
            scenario_id="reference_2000k_cap2",
            cap=CANDIDATE_CAP,
            initial_equity=REFERENCE_EQUITY,
        )
        phase385_ref = load_phase385_cap2_reference(self.reports_dir)

        ref_pnl = float(reference_sim.get("total_pnl_yen") or 0.0)
        cand_pnl = float(candidate.get("total_pnl_yen") or 0.0)
        comparison = {
            "reference_initial_equity": REFERENCE_EQUITY,
            "reference_accepted": reference_sim.get("accepted_trade_count"),
            "reference_pnl_yen": ref_pnl,
            "reference_profit_factor": reference_sim.get("profit_factor"),
            "reference_min_maintenance_ratio": reference_sim.get("min_maintenance_ratio"),
            "delta_pnl_yen": round(cand_pnl - ref_pnl, 2),
            "delta_accepted": int(candidate.get("accepted_trade_count") or 0) - int(reference_sim.get("accepted_trade_count") or 0),
            "delta_return_pct": round(float(candidate.get("return_pct") or 0.0) - float(reference_sim.get("return_pct") or 0.0), 4),
            "phase385_pnl_match": (
                round(ref_pnl, 2) == round(float(phase385_ref.get("total_pnl_yen_100") or 0.0), 2)
                if phase385_ref.get("loaded")
                else None
            ),
        }

        required_answers = build_required_answers(candidate, reference_sim)

        public_candidate = {k: v for k, v in candidate.items() if not str(k).startswith("_")}
        public_reference = {k: v for k, v in reference_sim.items() if not str(k).startswith("_")}

        daily_rows = build_daily_equity_rows(candidate) + build_daily_equity_rows(reference_sim)

        return {
            "phase": 388,
            "title": "1.5M live candidate validation",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "stack_id": PRIMARY_STACK,
            "hard_stop_pct": HARD_STOP_PCT,
            "candidate_config": {
                "initial_equity": CANDIDATE_EQUITY,
                "leverage_limit": DEFAULT_LEVERAGE,
                "shares": 100,
                "position_cap": CANDIDATE_CAP,
                "reinvestment": True,
            },
            "population": {
                "min_day": self.min_day,
                "max_day": self.max_day,
                "sessions_discovered": sessions_discovered,
                "sessions_evaluated": sessions_evaluated,
                "input_trade_count_raw": len(self.all_trades),
                "duplicate_session_trades_removed": duplicate_removed,
                "input_trade_count": len(trades),
            },
            "candidate": public_candidate,
            "reference_2000k_cap2_sim": public_reference,
            "phase385_reference_cap2_2m": phase385_ref,
            "comparison_vs_phase385": comparison,
            "required_answers": required_answers,
            "wall_runtime_sec": round(wall_runtime_sec, 2),
            "_candidate": candidate,
            "_reference": reference_sim,
            "_daily_rows": daily_rows,
            "_trade_log": (candidate.get("_trade_log") or []) + (reference_sim.get("_trade_log") or []),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        payload = {k: v for k, v in result.items() if not str(k).startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_csv(paths["daily_equity"], list(result.get("_daily_rows") or []), DAILY_EQUITY_FIELDS)
        _write_csv(paths["trade_log"], list(result.get("_trade_log") or []), TRADE_LOG_FIELDS)
        paths["report"].write_text(build_report(payload), encoding="utf-8")
        return paths


__all__ = ["CANDIDATE_EQUITY", "Phase388Cap1500kLiveCandidateValidation", "simulate_detailed"]
