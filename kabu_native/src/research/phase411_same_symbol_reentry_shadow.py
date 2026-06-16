"""
Phase411: same_symbol_open_reentry_reject forward shadow.

Rejects same-symbol ENTRY while an existing position is open (research only).
Compares baseline Runtime structural trades vs shadow-filtered trades.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf
from research.phase382_capital_constrained_backtest import _parse_ts, _write_csv
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase406_portfolio_adoption import load_phase405_boundary_policy
from research.phase409_boundary_forward_shadow import (
    FORWARD_PERIOD_START,
    MIN_ADOPTION_REVIEW_DAYS,
    MIN_OBSERVE_DAYS,
    evaluate_boundary_shadow_trade,
    forward_verdict,
    load_structural_trades_for_day,
)
from research.phase410_duplicate_reentry_audit import apply_counterfactual_policy
from research.research_output_layers import COMMON_RESEARCH_CONSTRAINTS
from research.structural_trade_normalize import copy_outputs_to_daily_research, resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")
POLICY = "same_symbol_open_reentry_reject"

TRADE_FIELDS = [
    "logged_at",
    "day",
    "session",
    "symbol",
    "entry_time",
    "exit_time",
    "hold_sec",
    "exit_reason",
    "baseline_included",
    "shadow_included",
    "shadow_reject_reason",
    "pnl_yen_100",
    "baseline_pnl_yen_100",
    "shadow_pnl_yen_100",
]

DAILY_FIELDS = [
    "day",
    "session_count",
    "baseline_trade_count",
    "shadow_trade_count",
    "trade_reduction_count",
    "same_symbol_reentry_reject_count",
    "overlap_replaced_review_reduction_count",
    "baseline_total_pnl_yen_100",
    "shadow_total_pnl_yen_100",
    "delta_pnl_yen_100",
    "baseline_pf",
    "shadow_pf",
    "baseline_maxdd_yen_100",
    "shadow_maxdd_yen_100",
    "avg_hold_sec_baseline",
    "avg_hold_sec_shadow",
    "median_hold_sec_baseline",
    "median_hold_sec_shadow",
    "baseline_boundary_eligible_count",
    "shadow_boundary_eligible_count",
    "baseline_boundary_exit_count",
    "shadow_boundary_exit_count",
    "verdict",
    "status",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def _replace_day_rows(
    existing: Sequence[Mapping[str, Any]],
    new_rows: Sequence[Mapping[str, Any]],
    *,
    day: str,
) -> list[dict[str, Any]]:
    kept = [dict(r) for r in existing if str(r.get("day") or "") != day]
    kept.extend(dict(r) for r in new_rows)
    return sorted(
        kept,
        key=lambda r: (
            str(r.get("day") or ""),
            str(r.get("session") or ""),
            str(r.get("entry_time") or ""),
            str(r.get("symbol") or ""),
        ),
    )


def _trade_pnl(trade: Mapping[str, Any]) -> float:
    return float(trade.get("pnl_yen_100_float") or trade.get("pnl_yen_100") or 0)


def _chronological_trade_pnls(rows: Sequence[Mapping[str, Any]]) -> list[float]:
    sort_keys = [
        (_parse_ts(str(r.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST), i)
        for i, r in enumerate(rows)
    ]
    order = [i for _, i in sorted(sort_keys, key=lambda x: (x[0], x[1]))]
    return [_trade_pnl(rows[i]) for i in order]


def _count_boundary_metrics(
    trades: Sequence[Mapping[str, Any]],
    *,
    repo_root: Path,
    boundary_rules: Mapping[int, Any],
) -> tuple[int, int]:
    session_cache: dict[str, Any] = {}
    eligible = 0
    exits = 0
    for trade in trades:
        hold = float(trade.get("hold_sec") or 0)
        row = evaluate_boundary_shadow_trade(
            trade,
            repo_root=repo_root,
            session_cache=session_cache,
            boundary_rules=boundary_rules,
        )
        if row is None:
            if hold >= 300:
                eligible += 1
            continue
        if hold >= 300 or "boundary" in str(row.get("shadow_exit_reason") or ""):
            eligible += 1
        if "boundary" in str(row.get("shadow_exit_reason") or ""):
            exits += 1
    return eligible, exits


def build_shadow_trade_rows(
    baseline_trades: Sequence[Mapping[str, Any]],
    shadow_trades: Sequence[Mapping[str, Any]],
    *,
    day: str,
    logged_at: str,
) -> list[dict[str, Any]]:
    shadow_keys = {
        (str(t.get("session") or ""), str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
        for t in shadow_trades
    }
    rows: list[dict[str, Any]] = []
    for t in baseline_trades:
        key = (str(t.get("session") or ""), str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
        included = key in shadow_keys
        pnl = float(t.get("pnl_yen_100_float") or t.get("pnl_yen_100") or 0)
        rows.append(
            {
                "logged_at": logged_at,
                "day": day,
                "session": t.get("session"),
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "exit_time": t.get("exit_time"),
                "hold_sec": float(t.get("hold_sec") or 0),
                "exit_reason": t.get("exit_reason"),
                "baseline_included": True,
                "shadow_included": included,
                "shadow_reject_reason": "" if included else POLICY,
                "pnl_yen_100": round(pnl, 2),
                "baseline_pnl_yen_100": round(pnl, 2),
                "shadow_pnl_yen_100": round(pnl, 2) if included else 0.0,
            }
        )
    return rows


def aggregate_day_metrics(
    baseline_trades: Sequence[Mapping[str, Any]],
    shadow_trades: Sequence[Mapping[str, Any]],
    trade_rows: Sequence[Mapping[str, Any]],
    *,
    day: str,
    repo_root: Path,
    boundary_rules: Mapping[int, Any],
    status: str = "logged_forward_shadow",
) -> dict[str, Any]:
    baseline_pnls = [_trade_pnl(t) for t in baseline_trades]
    shadow_pnls = [_trade_pnl(t) for t in shadow_trades]
    baseline_holds = [float(t.get("hold_sec") or 0) for t in baseline_trades]
    shadow_holds = [float(t.get("hold_sec") or 0) for t in shadow_trades]
    baseline_chron = _chronological_trade_pnls(baseline_trades)
    shadow_chron = _chronological_trade_pnls(shadow_trades)

    baseline_overlap = sum(
        1 for t in baseline_trades if str(t.get("exit_reason") or "") == "overlap_replaced_review"
    )
    shadow_overlap = sum(
        1 for t in shadow_trades if str(t.get("exit_reason") or "") == "overlap_replaced_review"
    )
    reject_count = sum(1 for r in trade_rows if not r.get("shadow_included"))
    sessions = {str(t.get("session") or "") for t in baseline_trades if t.get("session")}

    baseline_eligible, baseline_exits = _count_boundary_metrics(
        baseline_trades, repo_root=repo_root, boundary_rules=boundary_rules
    )
    shadow_eligible, shadow_exits = _count_boundary_metrics(
        shadow_trades, repo_root=repo_root, boundary_rules=boundary_rules
    )

    baseline_total = round(sum(baseline_pnls), 2)
    shadow_total = round(sum(shadow_pnls), 2)
    return {
        "day": day,
        "session_count": len(sessions),
        "baseline_trade_count": len(baseline_trades),
        "shadow_trade_count": len(shadow_trades),
        "trade_reduction_count": len(baseline_trades) - len(shadow_trades),
        "same_symbol_reentry_reject_count": reject_count,
        "overlap_replaced_review_reduction_count": baseline_overlap - shadow_overlap,
        "baseline_total_pnl_yen_100": baseline_total,
        "shadow_total_pnl_yen_100": shadow_total,
        "delta_pnl_yen_100": round(shadow_total - baseline_total, 2),
        "baseline_pf": _pf(baseline_chron),
        "shadow_pf": _pf(shadow_chron),
        "baseline_maxdd_yen_100": _max_drawdown_yen(baseline_chron),
        "shadow_maxdd_yen_100": _max_drawdown_yen(shadow_chron),
        "avg_hold_sec_baseline": round(sum(baseline_holds) / len(baseline_holds), 2) if baseline_holds else 0.0,
        "avg_hold_sec_shadow": round(sum(shadow_holds) / len(shadow_holds), 2) if shadow_holds else 0.0,
        "median_hold_sec_baseline": round(median(baseline_holds), 2) if baseline_holds else 0.0,
        "median_hold_sec_shadow": round(median(shadow_holds), 2) if shadow_holds else 0.0,
        "baseline_boundary_eligible_count": baseline_eligible,
        "shadow_boundary_eligible_count": shadow_eligible,
        "baseline_boundary_exit_count": baseline_exits,
        "shadow_boundary_exit_count": shadow_exits,
        "verdict": "observe",
        "status": status,
    }


def compute_cumulative_summary(daily_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    period_days = sorted(
        {
            str(r.get("day") or "")
            for r in daily_rows
            if str(r.get("day") or "") >= FORWARD_PERIOD_START
        }
    )
    period_rows = [r for r in daily_rows if str(r.get("day") or "") in period_days]
    day_count = len(period_days)
    session_count = sum(int(r.get("session_count") or 0) for r in period_rows)

    baseline_total = round(sum(float(r.get("baseline_total_pnl_yen_100") or 0) for r in period_rows), 2)
    shadow_total = round(sum(float(r.get("shadow_total_pnl_yen_100") or 0) for r in period_rows), 2)
    baseline_trades = sum(int(r.get("baseline_trade_count") or 0) for r in period_rows)
    shadow_trades = sum(int(r.get("shadow_trade_count") or 0) for r in period_rows)

    baseline_pf_vals = [r.get("baseline_pf") for r in period_rows if r.get("baseline_pf") is not None]
    shadow_pf_vals = [r.get("shadow_pf") for r in period_rows if r.get("shadow_pf") is not None]
    baseline_pf = round(sum(float(x) for x in baseline_pf_vals) / len(baseline_pf_vals), 4) if baseline_pf_vals else None
    shadow_pf = round(sum(float(x) for x in shadow_pf_vals) / len(shadow_pf_vals), 4) if shadow_pf_vals else None

    baseline_dd = max(float(r.get("baseline_maxdd_yen_100") or 0) for r in period_rows) if period_rows else 0.0
    shadow_dd = max(float(r.get("shadow_maxdd_yen_100") or 0) for r in period_rows) if period_rows else 0.0

    baseline_holds = [
        float(r.get("avg_hold_sec_baseline") or 0) for r in period_rows if int(r.get("baseline_trade_count") or 0) > 0
    ]
    shadow_holds = [
        float(r.get("avg_hold_sec_shadow") or 0) for r in period_rows if int(r.get("shadow_trade_count") or 0) > 0
    ]
    baseline_medians = [
        float(r.get("median_hold_sec_baseline") or 0)
        for r in period_rows
        if int(r.get("baseline_trade_count") or 0) > 0
    ]
    shadow_medians = [
        float(r.get("median_hold_sec_shadow") or 0)
        for r in period_rows
        if int(r.get("shadow_trade_count") or 0) > 0
    ]

    return {
        "day_count": day_count,
        "session_count": session_count,
        "period_days": period_days,
        "baseline_trade_count": baseline_trades,
        "shadow_trade_count": shadow_trades,
        "trade_reduction_count": baseline_trades - shadow_trades,
        "baseline_total_pnl_yen_100": baseline_total,
        "shadow_total_pnl_yen_100": shadow_total,
        "delta_pnl_yen_100": round(shadow_total - baseline_total, 2),
        "baseline_pf": baseline_pf,
        "shadow_pf": shadow_pf,
        "baseline_maxdd_yen_100": baseline_dd,
        "shadow_maxdd_yen_100": shadow_dd,
        "same_symbol_reentry_reject_count": sum(
            int(r.get("same_symbol_reentry_reject_count") or 0) for r in period_rows
        ),
        "overlap_replaced_review_reduction_count": sum(
            int(r.get("overlap_replaced_review_reduction_count") or 0) for r in period_rows
        ),
        "avg_hold_sec_baseline": round(sum(baseline_holds) / len(baseline_holds), 2) if baseline_holds else 0.0,
        "avg_hold_sec_shadow": round(sum(shadow_holds) / len(shadow_holds), 2) if shadow_holds else 0.0,
        "median_hold_sec_baseline": round(sum(baseline_medians) / len(baseline_medians), 2) if baseline_medians else 0.0,
        "median_hold_sec_shadow": round(sum(shadow_medians) / len(shadow_medians), 2) if shadow_medians else 0.0,
        "baseline_boundary_eligible_count": sum(
            int(r.get("baseline_boundary_eligible_count") or 0) for r in period_rows
        ),
        "shadow_boundary_eligible_count": sum(
            int(r.get("shadow_boundary_eligible_count") or 0) for r in period_rows
        ),
        "baseline_boundary_exit_count": sum(
            int(r.get("baseline_boundary_exit_count") or 0) for r in period_rows
        ),
        "shadow_boundary_exit_count": sum(
            int(r.get("shadow_boundary_exit_count") or 0) for r in period_rows
        ),
        "verdict": forward_verdict(day_count),
        "auto_adopt_forbidden": True,
        "policy": POLICY,
        "phase409_interaction": {
            "baseline_boundary_eligible_count": sum(
                int(r.get("baseline_boundary_eligible_count") or 0) for r in period_rows
            ),
            "shadow_boundary_eligible_count": sum(
                int(r.get("shadow_boundary_eligible_count") or 0) for r in period_rows
            ),
            "baseline_boundary_exit_count": sum(
                int(r.get("baseline_boundary_exit_count") or 0) for r in period_rows
            ),
            "shadow_boundary_exit_count": sum(
                int(r.get("shadow_boundary_exit_count") or 0) for r in period_rows
            ),
        },
    }


def apply_daily_verdicts(daily_rows: list[dict[str, Any]], period_days: Sequence[str]) -> None:
    for row in daily_rows:
        day = str(row.get("day") or "")
        if day not in period_days:
            continue
        idx = period_days.index(day) + 1
        row["verdict"] = forward_verdict(idx)


def run_same_symbol_reentry_shadow(
    *,
    repo_root: Path,
    reports_dir: Path,
    day: Optional[str] = None,
    phase405_policy_path: Optional[Path] = None,
) -> dict[str, Any]:
    day = day or datetime.now(JST).strftime("%Y%m%d")
    job = SameSymbolReentryShadowLogger(repo_root=repo_root, reports_dir=reports_dir)
    paths = job.paths()

    trade_rows = _read_csv_rows(paths["trades"])
    daily_rows = _read_csv_rows(paths["daily"])

    phase405_policy_path = phase405_policy_path or (reports_dir / "phase405_time_boundary_policy.csv")
    boundary_rules = load_phase405_boundary_policy(phase405_policy_path)

    last_run: dict[str, Any] = {"day": day}
    baseline_trades = load_structural_trades_for_day(repo_root, day)

    if day < FORWARD_PERIOD_START:
        last_run["status"] = "skipped_before_forward_period"
    elif not baseline_trades:
        last_run["status"] = "skipped_no_structural_trades"
    else:
        shadow_trades = apply_counterfactual_policy(baseline_trades, policy=POLICY)
        logged_at = _now_iso()
        new_trade_rows = build_shadow_trade_rows(
            baseline_trades, shadow_trades, day=day, logged_at=logged_at
        )
        trade_rows = _replace_day_rows(trade_rows, new_trade_rows, day=day)
        day_metrics = aggregate_day_metrics(
            baseline_trades,
            shadow_trades,
            new_trade_rows,
            day=day,
            repo_root=repo_root,
            boundary_rules=boundary_rules,
        )
        daily_rows = _replace_day_rows(daily_rows, [day_metrics], day=day)
        last_run["status"] = "logged_forward_shadow"
        last_run["baseline_trade_count"] = len(baseline_trades)
        last_run["shadow_trade_count"] = len(shadow_trades)
        last_run["same_symbol_reentry_reject_count"] = len(baseline_trades) - len(shadow_trades)

    forward_summary = compute_cumulative_summary(daily_rows)
    apply_daily_verdicts(daily_rows, forward_summary.get("period_days") or [])

    note = (
        "Forward shadow only; same_symbol_open_reentry_reject not applied to Runtime. "
        "Auto adoption forbidden; review after 5 business days, adoption review after 10."
    )

    return {
        "phase": "411-Same-Symbol-Reentry-Shadow",
        "title": "same_symbol_open_reentry_reject forward shadow",
        "generated_at": _now_iso(),
        "purpose": "Reject same-symbol re-entry while position open; compare vs baseline Runtime",
        "constraints": {
            **COMMON_RESEARCH_CONSTRAINTS,
            "forward_shadow_logging_only": True,
            "runtime_entry_unchanged": True,
            "auto_adopt_forbidden": True,
        },
        "policy": {
            "name": POLICY,
            "forward_period_start": FORWARD_PERIOD_START,
            "min_observe_days": MIN_OBSERVE_DAYS,
            "min_adoption_review_days": MIN_ADOPTION_REVIEW_DAYS,
        },
        "output_paths": {k: str(v) for k, v in paths.items()},
        "forward_summary": forward_summary,
        "last_run": last_run,
        "verdict": {"note": note},
        "_trade_rows": trade_rows,
        "_daily_rows": daily_rows,
    }


@dataclass
class SameSymbolReentryShadowLogger:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "trades": self.reports_dir / "phase411_same_symbol_reentry_shadow_trades.csv",
            "daily": self.reports_dir / "phase411_same_symbol_reentry_shadow_daily.csv",
            "summary": self.reports_dir / "phase411_same_symbol_reentry_shadow_summary.json",
        }

    def run(self, *, day: Optional[str] = None) -> dict[str, Any]:
        return run_same_symbol_reentry_shadow(
            repo_root=self.repo_root,
            reports_dir=self.reports_dir,
            day=day,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["trades"].parent.mkdir(parents=True, exist_ok=True)
        _write_csv(paths["trades"], list(result.get("_trade_rows") or []), TRADE_FIELDS)
        _write_csv(paths["daily"], list(result.get("_daily_rows") or []), DAILY_FIELDS)
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        from storage.results_paths import dual_write_output_paths, infer_day_from_result

        day = infer_day_from_result(result) or datetime.now(JST).strftime("%Y%m%d")
        dual_write_output_paths(self.repo_root, day, paths)
        copy_outputs_to_daily_research(self.repo_root, day, paths)
        return paths
