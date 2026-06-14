"""
Phase273-Forward-Live-Configuration-Shadow-Logger.

Daily forward shadow equity curves for Phase272 provisional live configurations.
Observation only — no Runtime / Universe / Entry / Exit / YAML changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.equity_curve_shadow import PERIOD_START, load_period_trades
from research.market_sector_heat import _write_csv
from research.phase269_portfolio_configuration_optimization import (
    SHARES,
    build_research_layer_for_config,
    config_id,
)
from research.phase271_leverage_attribution_and_robustness import simulate_audited
from research.phase382_capital_constrained_backtest import _day_from_ts, _position_key
from research.research_output_layers import COMMON_RESEARCH_CONSTRAINTS

JST = ZoneInfo("Asia/Tokyo")
MIN_FORWARD_DAY_COUNT = 10
DD_CAUTION_PCT = 20.0

LIVE_CONFIG_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "candidate_key": "live_start_candidate_1500k",
        "starting_equity": 1_500_000,
        "leverage": 2.0,
        "shares": 100,
        "cap": 3,
        "stop_policy": "fixed_stop_1p2",
    },
    {
        "candidate_key": "scale_candidate_2000k_plus",
        "starting_equity": 2_000_000,
        "leverage": 2.0,
        "shares": 100,
        "cap": 5,
        "stop_policy": "dynamic_stop_risk_1p0",
    },
    {
        "candidate_key": "scale_candidate_3000k",
        "starting_equity": 3_000_000,
        "leverage": 2.0,
        "shares": 100,
        "cap": 5,
        "stop_policy": "dynamic_stop_risk_1p0",
    },
)

RECOMMENDATION_ORDER: tuple[str, ...] = (
    "scale_candidate_3000k",
    "scale_candidate_2000k_plus",
    "live_start_candidate_1500k",
)

DAILY_EQUITY_FIELDS = [
    "day",
    "candidate_key",
    "config_id",
    "starting_equity",
    "start_equity",
    "end_equity",
    "daily_pnl",
    "cumulative_return_pct",
    "drawdown_pct",
    "accepted_trade_count",
    "rejected_trade_count",
]

TRADE_EVENT_FIELDS = [
    "day",
    "candidate_key",
    "config_id",
    "symbol",
    "entry_time",
    "exit_time",
    "event_type",
    "reject_reason",
    "pnl_yen",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _candidate_config_id(candidate: Mapping[str, Any]) -> str:
    return config_id(
        starting_equity=int(candidate["starting_equity"]),
        leverage=float(candidate["leverage"]),
        cap=int(candidate["cap"]),
        stop_policy=str(candidate["stop_policy"]),
    )


def _verdict_label(*, adopt_not_allowed: bool, caution: bool, day_count: int) -> str:
    if adopt_not_allowed:
        return "observe" if day_count < MIN_FORWARD_DAY_COUNT else "reject"
    if caution:
        return "caution"
    return "adopt"


def compute_candidate_summary(
    sim: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any],
    period_days: Sequence[str],
    trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    starting_equity = int(candidate["starting_equity"])
    final_equity = float(sim.get("final_equity") or starting_equity)
    day_count = len(period_days)
    days_below = int(sim.get("days_below_50pct") or 0)
    max_dd = float(sim.get("max_drawdown_pct") or 0.0)

    adopt_not_allowed = (
        day_count < MIN_FORWARD_DAY_COUNT
        or final_equity <= starting_equity
        or days_below > 0
    )
    caution = max_dd > DD_CAUTION_PCT

    research = build_research_layer_for_config(
        trades,
        stop_policy=str(candidate["stop_policy"]),
        starting_equity=float(starting_equity),
    )

    cid = _candidate_config_id(candidate)
    return {
        "candidate_key": candidate["candidate_key"],
        "config_id": cid,
        "starting_equity": starting_equity,
        "leverage": candidate["leverage"],
        "shares": candidate["shares"],
        "cap": candidate["cap"],
        "stop_policy": candidate["stop_policy"],
        "day_count": day_count,
        "period_days": list(period_days),
        "final_equity": final_equity,
        "total_return_pct": sim.get("total_return_pct"),
        "max_drawdown_pct": max_dd,
        "days_below_50pct": days_below,
        "accepted_count": sim.get("accepted_trade_count"),
        "rejected_count": sim.get("rejected_trade_count"),
        "profit_factor": sim.get("profit_factor"),
        "win_rate": sim.get("win_rate"),
        "research_profit_factor": research.get("profit_factor"),
        "research_total_pnl_yen": research.get("total_pnl_yen"),
        "adopt_not_allowed": adopt_not_allowed,
        "caution": caution,
        "verdict": _verdict_label(
            adopt_not_allowed=adopt_not_allowed,
            caution=caution,
            day_count=day_count,
        ),
    }


def resolve_current_recommendation(candidate_summaries: Sequence[Mapping[str, Any]]) -> str:
    by_key = {str(c.get("candidate_key") or ""): c for c in candidate_summaries}
    for key in RECOMMENDATION_ORDER:
        row = by_key.get(key) or {}
        if not row.get("adopt_not_allowed"):
            return key
    return "live_start_candidate_1500k"


def build_daily_equity_rows(
    sim: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any],
) -> list[dict[str, Any]]:
    cid = _candidate_config_id(candidate)
    starting_equity = int(candidate["starting_equity"])
    rows: list[dict[str, Any]] = []
    for row in sim.get("_daily_rows") or []:
        rows.append(
            {
                "day": row.get("day"),
                "candidate_key": candidate["candidate_key"],
                "config_id": cid,
                "starting_equity": starting_equity,
                "start_equity": row.get("start_equity"),
                "end_equity": row.get("end_equity"),
                "daily_pnl": row.get("daily_pnl"),
                "cumulative_return_pct": row.get("cumulative_return_pct"),
                "drawdown_pct": row.get("drawdown_pct"),
                "accepted_trade_count": row.get("accepted_trade_count"),
                "rejected_trade_count": row.get("rejected_trade_count"),
            }
        )
    return rows


def build_trade_event_rows(
    trades: Sequence[Mapping[str, Any]],
    sim: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any],
) -> list[dict[str, Any]]:
    cid = _candidate_config_id(candidate)
    candidate_key = str(candidate["candidate_key"])
    state = sim.get("_state")
    if state is None:
        return []

    trade_by_key = {_position_key(t): t for t in trades}
    rows: list[dict[str, Any]] = []

    for log in state.trade_log:
        trade = log.get("trade") or {}
        day = str(log.get("day") or _day_from_ts(str(trade.get("entry_time") or "")) or "")
        rows.append(
            {
                "day": day,
                "candidate_key": candidate_key,
                "config_id": cid,
                "symbol": log.get("symbol") or trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "exit_time": log.get("exit_time") or trade.get("exit_time"),
                "event_type": "accepted",
                "reject_reason": "",
                "pnl_yen": log.get("pnl_yen"),
            }
        )

    for rej in sim.get("reject_log") or []:
        key = str(rej.get("key") or "")
        trade = trade_by_key.get(key, {})
        day = _day_from_ts(str(trade.get("entry_time") or "")) or ""
        rows.append(
            {
                "day": day,
                "candidate_key": candidate_key,
                "config_id": cid,
                "symbol": trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "exit_time": "",
                "event_type": "rejected",
                "reject_reason": rej.get("reason"),
                "pnl_yen": "",
            }
        )

    return sorted(
        rows,
        key=lambda r: (
            str(r.get("day") or ""),
            str(r.get("candidate_key") or ""),
            str(r.get("entry_time") or ""),
            str(r.get("event_type") or ""),
        ),
    )


def build_report_markdown(result: Mapping[str, Any]) -> str:
    summary = result.get("forward_summary") or {}
    lines = [
        "# Phase273 Live Configuration Forward Shadow",
        "",
        "Daily forward shadow equity curves for Phase272 provisional live configs.",
        "",
        f"- generated_at: {result.get('generated_at')}",
        f"- day_count: {summary.get('day_count')}",
        f"- current_recommendation: {summary.get('current_recommendation')}",
        f"- adopt_not_allowed (aggregate): {summary.get('adopt_not_allowed')}",
        "",
        "## Candidates",
        "",
    ]
    for row in summary.get("candidates") or []:
        lines.append(
            f"- `{row.get('candidate_key')}`: final={row.get('final_equity')} "
            f"DD={row.get('max_drawdown_pct')}% verdict={row.get('verdict')} "
            f"adopt_not_allowed={row.get('adopt_not_allowed')}"
        )
    lines.extend(
        [
            "",
            str((result.get("verdict") or {}).get("note")),
            "",
        ]
    )
    return "\n".join(lines)


def run_forward_shadow_logger(
    *,
    repo_root: Path,
    reports_dir: Path,
    day: Optional[str] = None,
) -> dict[str, Any]:
    day = day or datetime.now(JST).strftime("%Y%m%d")
    paths = LiveConfigForwardShadowLogger(repo_root=repo_root, reports_dir=reports_dir).paths()

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

    daily_rows: list[dict[str, Any]] = []
    trade_events: list[dict[str, Any]] = []
    candidate_summaries: list[dict[str, Any]] = []

    if trades:
        for candidate in LIVE_CONFIG_CANDIDATES:
            sim = simulate_audited(
                trades,
                starting_equity=int(candidate["starting_equity"]),
                leverage=float(candidate["leverage"]),
                cap=int(candidate["cap"]),
                stop_policy=str(candidate["stop_policy"]),
            )
            daily_rows.extend(build_daily_equity_rows(sim, candidate=candidate))
            trade_events.extend(build_trade_event_rows(trades, sim, candidate=candidate))
            candidate_summaries.append(
                compute_candidate_summary(
                    sim,
                    candidate=candidate,
                    period_days=period_days,
                    trades=trades,
                )
            )

    current_recommendation = resolve_current_recommendation(candidate_summaries)
    aggregate_adopt_not_allowed = any(c.get("adopt_not_allowed") for c in candidate_summaries)

    forward_summary = {
        "day_count": len(period_days),
        "period_days": period_days,
        "current_recommendation": current_recommendation,
        "adopt_not_allowed": aggregate_adopt_not_allowed,
        "candidates": candidate_summaries,
    }

    note = (
        "Forward shadow logging only; Runtime/Universe/Entry/Exit/YAML unchanged. "
        "Adoption requires day_count>=10, final_equity>starting_equity, days_below_50pct=0; "
        "Research PF alone must not drive adoption."
    )

    return {
        "phase": "273-Forward-Live-Configuration-Shadow-Logger",
        "title": "Live configuration forward shadow logger",
        "generated_at": _now_iso(),
        "purpose": "Accumulate daily equity curves for Phase272 provisional live configurations",
        "constraints": {
            **COMMON_RESEARCH_CONSTRAINTS,
            "forward_shadow_logging_only": True,
        },
        "policy": {
            "period_start": PERIOD_START,
            "min_forward_day_count": MIN_FORWARD_DAY_COUNT,
            "dd_caution_pct": DD_CAUTION_PCT,
            "candidates": list(LIVE_CONFIG_CANDIDATES),
        },
        "population": pop_meta,
        "output_paths": {k: str(v) for k, v in paths.items()},
        "forward_summary": forward_summary,
        "last_run": last_run,
        "verdict": {"note": note},
        "_daily_rows": daily_rows,
        "_trade_events": trade_events,
    }


@dataclass
class LiveConfigForwardShadowLogger:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "daily_equity": self.reports_dir / "phase273_live_config_shadow_daily_equity.csv",
            "trade_events": self.reports_dir / "phase273_live_config_shadow_trade_events.csv",
            "summary": self.reports_dir / "phase273_live_config_shadow_summary.json",
            "report": self.reports_dir / "phase273_live_config_shadow_report.md",
        }

    def run(self, *, day: Optional[str] = None) -> dict[str, Any]:
        return run_forward_shadow_logger(
            repo_root=self.repo_root,
            reports_dir=self.reports_dir,
            day=day,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["daily_equity"].parent.mkdir(parents=True, exist_ok=True)
        _write_csv(paths["daily_equity"], DAILY_EQUITY_FIELDS, result.get("_daily_rows") or [])
        _write_csv(paths["trade_events"], TRADE_EVENT_FIELDS, result.get("_trade_events") or [])
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths["report"].write_text(build_report_markdown(result), encoding="utf-8")
        return paths
