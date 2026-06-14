"""
Phase387: CAP2 production shadow validation (Stack C).

Monitors actual CAP=3 vs shadow CAP=2 on live sessions without production changes.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase377_daily_regime_breakdown import PRIMARY_STACK
from research.phase379_380_period_b_eval import is_low_mfe_stop, is_stop_hit
from research.phase382_capital_constrained_backtest import (
    _day_from_ts,
    _float,
    _parse_ts,
    _position_key,
    _write_csv,
    dedupe_trades,
)
from research.phase385_cap_sensitivity_study import DEFAULT_EQUITY_FLOOR, DEFAULT_INITIAL_EQUITY
from research.phase386_third_position_quality_review import (
    CAP2,
    CAP3,
    cohort_metrics,
    enrich_trade_row,
    simulate_cap_acceptance,
)

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_SHADOW_START_DAY = "20260613"
PHASE386_REFERENCE_JSON = "phase386_third_position_quality_summary.json"

BY_DAY_FIELDS = [
    "day",
    "actual_accepted_count",
    "actual_rejected_count",
    "shadow_accepted_count",
    "shadow_rejected_count",
    "actual_total_pnl_yen_100",
    "shadow_total_pnl_yen_100",
    "cap3_additional_pnl_yen_100",
    "delta_shadow_minus_actual_pnl",
    "actual_profit_factor",
    "shadow_profit_factor",
    "actual_win_rate",
    "shadow_win_rate",
    "actual_stop_hit_count",
    "shadow_stop_hit_count",
    "actual_low_mfe_stop_count",
    "shadow_low_mfe_stop_count",
    "actual_trailing_mfe_exit_count",
    "shadow_trailing_mfe_exit_count",
    "actual_overlap_replaced_count",
    "shadow_overlap_replaced_count",
    "cap2_better_day",
    "cap3_additional_negative_day",
]

TRADE_FIELDS = [
    "lane",
    "day",
    "symbol",
    "entry_time",
    "exit_time",
    "exit_reason",
    "pnl_yen_100",
    "dynamic40_rank_bucket",
    "board_tier",
    "cap2_reject_reason",
]


def _lane_for_key(key: str, *, cap2_keys: set[str], cap3_keys: set[str]) -> str:
    in2 = key in cap2_keys
    in3 = key in cap3_keys
    if in3 and not in2:
        return "cap3_additional"
    if in2:
        return "shadow_cap2"
    if in3:
        return "actual_cap3"
    return "unknown"


def daily_cohort_metrics(trades: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        day = str(t.get("day_key") or _day_from_ts(str(t.get("entry_time") or "")) or "")
        if day:
            by_day[day].append(t)
    return {day: cohort_metrics(rows) for day, rows in sorted(by_day.items())}


def build_daily_rows(
    *,
    cap2_trades: Sequence[Mapping[str, Any]],
    cap3_trades: Sequence[Mapping[str, Any]],
    cap3_additional_trades: Sequence[Mapping[str, Any]],
    cap2_daily: Mapping[str, Mapping[str, Any]],
    cap3_daily: Mapping[str, Mapping[str, Any]],
    add_daily: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    days = sorted(set(cap2_daily) | set(cap3_daily) | set(add_daily))
    rows: list[dict[str, Any]] = []
    for day in days:
        a = cap3_daily.get(day) or {}
        s = cap2_daily.get(day) or {}
        add = add_daily.get(day) or {}
        a_pnl = float(a.get("total_pnl_yen_100") or 0.0)
        s_pnl = float(s.get("total_pnl_yen_100") or 0.0)
        add_pnl = float(add.get("total_pnl_yen_100") or 0.0)
        rows.append(
            {
                "day": day,
                "actual_accepted_count": a.get("trade_count", 0),
                "actual_rejected_count": "",
                "shadow_accepted_count": s.get("trade_count", 0),
                "shadow_rejected_count": "",
                "actual_total_pnl_yen_100": a_pnl,
                "shadow_total_pnl_yen_100": s_pnl,
                "cap3_additional_pnl_yen_100": add_pnl,
                "delta_shadow_minus_actual_pnl": round(s_pnl - a_pnl, 2),
                "actual_profit_factor": a.get("profit_factor"),
                "shadow_profit_factor": s.get("profit_factor"),
                "actual_win_rate": a.get("win_rate"),
                "shadow_win_rate": s.get("win_rate"),
                "actual_stop_hit_count": a.get("stop_hit_count", 0),
                "shadow_stop_hit_count": s.get("stop_hit_count", 0),
                "actual_low_mfe_stop_count": a.get("low_mfe_stop_count", 0),
                "shadow_low_mfe_stop_count": s.get("low_mfe_stop_count", 0),
                "actual_trailing_mfe_exit_count": a.get("trailing_mfe_exit_count", 0),
                "shadow_trailing_mfe_exit_count": s.get("trailing_mfe_exit_count", 0),
                "actual_overlap_replaced_count": a.get("overlap_replaced_count", 0),
                "shadow_overlap_replaced_count": s.get("overlap_replaced_count", 0),
                "cap2_better_day": s_pnl > a_pnl,
                "cap3_additional_negative_day": add_pnl < 0,
            }
        )
    return rows


def load_phase386_reference(reports_dir: Path) -> dict[str, Any]:
    path = reports_dir / PHASE386_REFERENCE_JSON
    if not path.is_file():
        return {"loaded": False}
    data = json.loads(path.read_text(encoding="utf-8"))
    cmp_ = data.get("cohort_comparison") or {}
    return {
        "loaded": True,
        "period": data.get("population"),
        "cap2_accepted_pnl": (cmp_.get("cap2_accepted") or {}).get("total_pnl_yen_100"),
        "cap3_additional_pnl": (cmp_.get("cap3_additional") or {}).get("total_pnl_yen_100"),
        "cap3_additional_pf": (cmp_.get("cap3_additional") or {}).get("profit_factor"),
        "delta_trade_count": (data.get("conclusions") or {}).get("delta_trade_count"),
    }


def build_required_answers(
    *,
    actual_metrics: Mapping[str, Any],
    shadow_metrics: Mapping[str, Any],
    additional_metrics: Mapping[str, Any],
    daily_rows: Sequence[Mapping[str, Any]],
    phase386_ref: Mapping[str, Any],
) -> dict[str, Any]:
    actual_pnl = float(actual_metrics.get("total_pnl_yen_100") or 0.0)
    shadow_pnl = float(shadow_metrics.get("total_pnl_yen_100") or 0.0)
    add_pnl = float(additional_metrics.get("total_pnl_yen_100") or 0.0)

    cap2_better_days = sum(1 for r in daily_rows if r.get("cap2_better_day"))
    cap3_add_neg_days = sum(1 for r in daily_rows if r.get("cap3_additional_negative_day"))
    total_days = len(daily_rows)

    return {
        "cap2_superiority_continues": shadow_pnl > actual_pnl,
        "cap2_pnl_delta_vs_actual": round(shadow_pnl - actual_pnl, 2),
        "cap3_additional_still_negative": add_pnl < 0,
        "cap3_additional_pnl_yen_100": add_pnl,
        "cap3_additional_pf": additional_metrics.get("profit_factor"),
        "daily_cap2_better_days": cap2_better_days,
        "daily_cap2_better_rate": round(cap2_better_days / total_days, 4) if total_days else None,
        "daily_cap3_additional_negative_days": cap3_add_neg_days,
        "regime_robustness_note": (
            "multi_day_cap2_lead"
            if total_days >= 2 and cap2_better_days >= max(1, total_days // 2)
            else "insufficient_or_mixed_days"
        ),
        "consistent_with_phase386": (
            bool(phase386_ref.get("loaded"))
            and add_pnl < 0
            and shadow_pnl > actual_pnl
        ),
        "phase386_reference_loaded": bool(phase386_ref.get("loaded")),
    }


@dataclass
class Phase387Cap2ShadowValidation:
    reports_dir: Path
    min_day: str = DEFAULT_SHADOW_START_DAY
    max_day: Optional[str] = None
    initial_equity: float = DEFAULT_INITIAL_EQUITY
    equity_floor: float = DEFAULT_EQUITY_FLOOR
    all_trades: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase387_cap2_shadow_summary.json",
            "by_day": self.reports_dir / "phase387_cap2_shadow_by_day.csv",
            "trades": self.reports_dir / "phase387_cap2_shadow_trades.csv",
            "state": self.reports_dir / "phase387_cap2_shadow_state.json",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        self.all_trades.extend(result.get("all_trades") or result.get("valid_trades") or [])

    def run(
        self,
        *,
        sessions_discovered: int = 0,
        sessions_evaluated: int = 0,
        wall_runtime_sec: float = 0.0,
    ) -> dict[str, Any]:
        trades, duplicate_removed = dedupe_trades(self.all_trades)
        trades = sorted(
            trades,
            key=lambda t: (_parse_ts(t.get("entry_time")) or datetime.min.replace(tzinfo=JST), str(t.get("symbol") or "")),
        )
        lookup = {_position_key(t): dict(t) for t in trades}

        sim2 = simulate_cap_acceptance(
            trades, cap=CAP2, initial_equity=self.initial_equity, equity_floor=self.equity_floor
        )
        sim3 = simulate_cap_acceptance(
            trades, cap=CAP3, initial_equity=self.initial_equity, equity_floor=self.equity_floor
        )
        cap2_keys = sim2["accepted_keys"]
        cap3_keys = sim3["accepted_keys"]
        additional_keys = cap3_keys - cap2_keys

        cap2_trades = [lookup[k] for k in sorted(cap2_keys) if k in lookup]
        cap3_trades = [lookup[k] for k in sorted(cap3_keys) if k in lookup]
        cap3_additional_trades = [lookup[k] for k in sorted(additional_keys) if k in lookup]

        actual_metrics = cohort_metrics(cap3_trades)
        shadow_metrics = cohort_metrics(cap2_trades)
        additional_metrics = cohort_metrics(cap3_additional_trades)

        cap2_daily = daily_cohort_metrics(cap2_trades)
        cap3_daily = daily_cohort_metrics(cap3_trades)
        add_daily = daily_cohort_metrics(cap3_additional_trades)
        daily_rows = build_daily_rows(
            cap2_trades=cap2_trades,
            cap3_trades=cap3_trades,
            cap3_additional_trades=cap3_additional_trades,
            cap2_daily=cap2_daily,
            cap3_daily=cap3_daily,
            add_daily=add_daily,
        )

        phase386_ref = load_phase386_reference(self.reports_dir)
        required_answers = build_required_answers(
            actual_metrics=actual_metrics,
            shadow_metrics=shadow_metrics,
            additional_metrics=additional_metrics,
            daily_rows=daily_rows,
            phase386_ref=phase386_ref,
        )

        trade_rows: list[dict[str, Any]] = []
        cap2_reject = sim2["rejected_entries"]
        for key in sorted(cap3_keys | cap2_keys):
            if key not in lookup:
                continue
            trade = lookup[key]
            lane = _lane_for_key(key, cap2_keys=cap2_keys, cap3_keys=cap3_keys)
            cohort = "cap2_accepted" if lane == "shadow_cap2" else ("cap3_additional" if lane == "cap3_additional" else "actual_cap3")
            row = enrich_trade_row(
                trade,
                cohort=cohort,
                cap2_reject_reason=cap2_reject.get(key, ""),
            )
            row["lane"] = lane
            row["exit_time"] = trade.get("exit_time")
            public = {k: v for k, v in row.items() if k in TRADE_FIELDS}
            trade_rows.append(public)

        return {
            "phase": 387,
            "title": "CAP2 production shadow validation",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "stack_id": PRIMARY_STACK,
            "monitoring_only": True,
            "production_cap_unchanged": 3,
            "shadow_cap": 2,
            "initial_equity": self.initial_equity,
            "leverage_limit": 2.0,
            "population": {
                "min_day": self.min_day,
                "max_day": self.max_day,
                "sessions_discovered": sessions_discovered,
                "sessions_evaluated": sessions_evaluated,
                "input_trade_count_raw": len(self.all_trades),
                "duplicate_session_trades_removed": duplicate_removed,
                "input_trade_count": len(trades),
                "trade_days": sorted({str(r.get("day")) for r in daily_rows}),
            },
            "acceptance": {
                "actual_cap3_accepted": sim3["accepted_trade_count"],
                "actual_cap3_rejected": sim3["rejected_trade_count"],
                "shadow_cap2_accepted": sim2["accepted_trade_count"],
                "shadow_cap2_rejected": sim2["rejected_trade_count"],
                "cap3_additional_count": len(additional_keys),
            },
            "actual_cap3": actual_metrics,
            "shadow_cap2": shadow_metrics,
            "cap3_additional": additional_metrics,
            "comparison": {
                "delta_accepted_shadow_minus_actual": sim2["accepted_trade_count"] - sim3["accepted_trade_count"],
                "delta_pnl_shadow_minus_actual": round(
                    float(shadow_metrics.get("total_pnl_yen_100") or 0.0)
                    - float(actual_metrics.get("total_pnl_yen_100") or 0.0),
                    2,
                ),
                "delta_pf_shadow_minus_actual": (
                    round(float(shadow_metrics.get("profit_factor") or 0.0) - float(actual_metrics.get("profit_factor") or 0.0), 4)
                    if shadow_metrics.get("profit_factor") is not None and actual_metrics.get("profit_factor") is not None
                    else None
                ),
            },
            "phase386_reference": phase386_ref,
            "required_answers": required_answers,
            "by_day": daily_rows,
            "trade_rows": trade_rows,
            "wall_runtime_sec": round(wall_runtime_sec, 2),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        payload = {k: v for k, v in result.items() if k not in ("trade_rows",)}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_csv(paths["by_day"], list(result.get("by_day") or []), BY_DAY_FIELDS)
        _write_csv(paths["trades"], list(result.get("trade_rows") or []), TRADE_FIELDS)
        state = {
            "last_run_at": result.get("generated_at"),
            "min_day": result.get("population", {}).get("min_day"),
            "max_day": result.get("population", {}).get("max_day"),
            "sessions_evaluated": result.get("population", {}).get("sessions_evaluated"),
            "trade_days": result.get("population", {}).get("trade_days"),
            "required_answers": result.get("required_answers"),
        }
        paths["state"].write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return paths


__all__ = ["DEFAULT_SHADOW_START_DAY", "Phase387Cap2ShadowValidation"]
