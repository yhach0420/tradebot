"""
Phase262-Risk-Aware-Sizing-Forward-Shadow-Logger.

Daily forward shadow logging for risk-aware position sizing policies.
Observation only — no Runtime / Universe / Entry / YAML changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import (
    _float,
    _write_csv,
    load_trades_by_day,
)
from research.market_sector_heat_diagnostics import _read_csv
from research.market_sector_heat_forward_shadow_logger import _read_csv_rows
from research.phase374_dynamic40_universe_quality_review import resolve_pnl_yen_100
from research.risk_aware_sizing_shadow import (
    EQUITY_LEVELS,
    FORWARD_SIZING_POLICIES,
    MIN_TRADE_OVERLAP_DAYS,
    aggregate_forward_cumulative_rows,
    aggregate_forward_summary_rows,
    build_forward_entry_rows,
    compute_median_volatility,
    enrich_base_entries,
)

JST = ZoneInfo("Asia/Tokyo")
PRIMARY_EQUITY = 5_000_000

FORWARD_ENTRY_FIELDS = [
    "logged_at",
    "day",
    "symbol",
    "entry_price",
    "actual_pnl_yen_100",
    "sizing_policy",
    "equity_yen",
    "shares_shadow",
    "skipped_due_to_min_lot",
    "skipped_due_to_risk",
    "position_value",
    "position_ratio",
    "risk_per_100_shares_yen",
    "pnl_yen_scaled",
]

FORWARD_SUMMARY_BY_DAY_FIELDS = [
    "day",
    "equity_yen",
    "sizing_policy",
    "entry_count",
    "skipped_count",
    "total_pnl_yen_scaled",
    "profit_factor",
    "win_rate",
    "max_loss_yen_scaled",
    "pnl_stddev",
    "avg_position_ratio",
    "p95_position_ratio",
    "high_price_pnl_scaled",
    "low_price_pnl_scaled",
    "delta_vs_fixed_100",
]


def _bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if val in (None, ""):
        return False
    return str(val).lower() in {"1", "true", "yes", "y"}


def _normalize_forward_entry_row(row: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    normalized["skipped_due_to_min_lot"] = _bool(row.get("skipped_due_to_min_lot"))
    normalized["skipped_due_to_risk"] = _bool(row.get("skipped_due_to_risk"))
    return normalized


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


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
            str(r.get("symbol") or ""),
            int(r.get("equity_yen") or 0),
            str(r.get("sizing_policy") or ""),
        ),
    )


def _trade_overlap_days(entry_rows: Sequence[Mapping[str, Any]]) -> list[str]:
    days: set[str] = set()
    for row in entry_rows:
        day = str(row.get("day") or "")
        if not day:
            continue
        if _bool(row.get("skipped_due_to_min_lot")):
            continue
        days.add(day)
    return sorted(days)


def _row_for(
    rows: Sequence[Mapping[str, Any]],
    *,
    equity: int,
    policy: str,
    scope: str = "cumulative",
) -> dict[str, Any]:
    for row in rows:
        if (
            str(row.get("day") or "") == scope
            and int(row.get("equity_yen") or 0) == equity
            and str(row.get("sizing_policy") or "") == policy
        ):
            return dict(row)
    return {}


def compute_forward_summary(
    entry_rows: Sequence[Mapping[str, Any]],
    summary_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    overlap_days = _trade_overlap_days(entry_rows)
    cumulative = aggregate_forward_cumulative_rows(entry_rows)
    adopt_not_allowed = len(overlap_days) < MIN_TRADE_OVERLAP_DAYS

    fixed_5m = _row_for(cumulative, equity=PRIMARY_EQUITY, policy="fixed_100_shares")
    fixed_pf = _float(fixed_5m.get("profit_factor")) or 0.0
    fixed_max_loss = abs(_float(fixed_5m.get("max_loss_yen_scaled")) or 0.0)
    fixed_std = _float(fixed_5m.get("pnl_stddev")) or 0.0
    fixed_low = _float(fixed_5m.get("low_price_pnl_scaled")) or 0.0

    adoption_verdict: list[dict[str, Any]] = []
    best_policy = "fixed_100_shares"
    best_pnl = _float(fixed_5m.get("total_pnl_yen_scaled")) or 0.0

    for policy in FORWARD_SIZING_POLICIES:
        if policy == "fixed_100_shares":
            continue
        row = _row_for(cumulative, equity=PRIMARY_EQUITY, policy=policy)
        pf = _float(row.get("profit_factor")) or 0.0
        max_loss = abs(_float(row.get("max_loss_yen_scaled")) or 0.0)
        std = _float(row.get("pnl_stddev")) or 0.0
        low_pnl = _float(row.get("low_price_pnl_scaled")) or 0.0
        total_pnl = _float(row.get("total_pnl_yen_scaled")) or 0.0

        caution = False
        if policy == "risk_2pct_equity":
            if fixed_max_loss > 0 and max_loss > fixed_max_loss * 1.25:
                caution = True
            if fixed_std > 0 and std > fixed_std * 1.25:
                caution = True

        low_price_overexpansion = low_pnl < fixed_low - 50_000.0
        stable_candidate = pf >= fixed_pf if fixed_pf > 0 else total_pnl >= best_pnl

        if total_pnl > best_pnl:
            best_pnl = total_pnl
            best_policy = policy

        adoption_verdict.append(
            {
                "sizing_policy": policy,
                "equity_yen": PRIMARY_EQUITY,
                "stable_candidate": stable_candidate and not adopt_not_allowed,
                "caution": caution,
                "low_price_overexpansion": low_price_overexpansion,
                "adopt_not_allowed": adopt_not_allowed,
                "recommendation": "observe" if adopt_not_allowed else ("caution" if caution else "candidate"),
            }
        )

    risk_sizing_preferred = (
        not adopt_not_allowed
        and best_policy in ("risk_1pct_equity", "hybrid_equity30_risk1")
        and best_pnl > (_float(fixed_5m.get("total_pnl_yen_scaled")) or 0.0)
    )

    return {
        "trade_overlap_day_count": len(overlap_days),
        "trade_overlap_days": overlap_days,
        "adopt_not_allowed": adopt_not_allowed,
        "best_policy": best_policy,
        "best_policy_equity_yen": PRIMARY_EQUITY,
        "best_policy_total_pnl_yen_scaled": round(best_pnl, 2),
        "risk_sizing_preferred_over_price_cap": risk_sizing_preferred,
        "adoption_verdict_by_policy": adoption_verdict,
        "cumulative_by_equity_policy": cumulative,
    }


def backfill_from_phase261(
    *,
    reports_dir: Path,
    logged_at: Optional[str] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    path = reports_dir / "phase261_entry_level_risk_sizing.csv"
    if not path.is_file():
        return [], []
    logged_at = logged_at or _now_iso()
    entry_rows: list[dict[str, Any]] = []
    for row in _read_csv(path):
        policy = str(row.get("sizing_policy") or "")
        if policy not in FORWARD_SIZING_POLICIES:
            continue
        entry_rows.append(
            _normalize_forward_entry_row(
                {
                    "logged_at": logged_at,
                    "day": str(row.get("day") or ""),
                    "symbol": str(row.get("symbol") or ""),
                    "entry_price": row.get("entry_price"),
                    "actual_pnl_yen_100": row.get("pnl_yen_100"),
                    "sizing_policy": policy,
                    "equity_yen": row.get("equity_yen"),
                    "shares_shadow": row.get("shares_shadow"),
                    "skipped_due_to_min_lot": row.get("skipped_due_to_min_lot"),
                    "skipped_due_to_risk": row.get("skipped_due_to_risk"),
                    "position_value": row.get("position_value"),
                    "position_ratio": row.get("position_ratio"),
                    "risk_per_100_shares_yen": row.get("risk_per_100_shares_yen"),
                    "pnl_yen_scaled": row.get("pnl_yen_scaled"),
                }
            )
        )
    summary_rows = aggregate_forward_summary_rows(entry_rows)
    return entry_rows, summary_rows


def build_report_markdown(result: Mapping[str, Any]) -> str:
    summary = result.get("forward_summary") or {}
    lines = [
        "# Phase262 Risk-Aware Sizing Forward Shadow",
        "",
        "Daily forward shadow logging for risk-aware position sizing (observation only).",
        "",
        f"- trade overlap days: {summary.get('trade_overlap_day_count')} "
        f"({', '.join(summary.get('trade_overlap_days') or [])})",
        f"- adopt_not_allowed: {summary.get('adopt_not_allowed')}",
        f"- best_policy @ 5M: {summary.get('best_policy')} "
        f"(pnl={summary.get('best_policy_total_pnl_yen_scaled')})",
        "",
        "## Adoption verdict by policy (5M cumulative)",
        "",
    ]
    for row in summary.get("adoption_verdict_by_policy") or []:
        lines.append(
            f"- `{row.get('sizing_policy')}`: stable={row.get('stable_candidate')} "
            f"caution={row.get('caution')} low_price_overexpansion={row.get('low_price_overexpansion')}"
        )
    lines.extend(["", str((result.get("verdict") or {}).get("note")), ""])
    return "\n".join(lines)


def run_forward_shadow_logger(
    *,
    repo_root: Path,
    reports_dir: Path,
    day: Optional[str] = None,
    backfill_phase261: bool = False,
) -> dict[str, Any]:
    day = day or datetime.now(JST).strftime("%Y%m%d")
    paths = RiskSizingForwardShadowLogger(repo_root=repo_root, reports_dir=reports_dir).paths()

    entry_rows = [_normalize_forward_entry_row(r) for r in _read_csv_rows(paths["entry_log"])]
    summary_rows = _read_csv_rows(paths["summary_by_day"])

    if backfill_phase261 and not entry_rows:
        bf_e, bf_s = backfill_from_phase261(reports_dir=reports_dir)
        entry_rows = bf_e
        summary_rows = bf_s

    last_run: dict[str, Any] = {"day": day}
    trades_by_day = load_trades_by_day(repo_root)
    day_trades = []
    for row in trades_by_day.get(day) or []:
        trade = dict(row)
        trade["symbol"] = str(trade.get("symbol") or "")
        if trade.get("pnl_yen_100") is None:
            trade["pnl_yen_100"] = resolve_pnl_yen_100(trade)
        day_trades.append(trade)

    if not day_trades:
        last_run["status"] = "skipped_no_structural_trades"
    else:
        median_vol = compute_median_volatility(trades_by_day, repo_root=repo_root)
        base_entries = enrich_base_entries(
            {day: day_trades},
            overlap_days=[day],
            repo_root=repo_root,
            median_volatility=median_vol,
        )
        new_entries = build_forward_entry_rows(base_entries, logged_at=_now_iso())
        entry_rows = _replace_day_rows(entry_rows, new_entries, day=day)
        last_run["status"] = f"logged_{len(new_entries)}_entries"
        last_run["trade_count"] = len(day_trades)

    if entry_rows:
        summary_rows = aggregate_forward_summary_rows(entry_rows)

    forward_summary = compute_forward_summary(entry_rows, summary_rows)
    note = (
        "Forward shadow logging only; Runtime/Universe/Entry/YAML unchanged. "
        "Adoption blocked until trade_overlap_day_count >= 10."
    )

    return {
        "phase": "262-Risk-Aware-Sizing-Forward-Shadow",
        "title": "Risk-aware sizing forward shadow logger",
        "generated_at": _now_iso(),
        "purpose": "Accumulate daily risk-aware sizing shadow logs to reduce small-sample dependency",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "runtime_reflected": False,
            "universe_change_forbidden": True,
            "entry_change_forbidden": True,
            "forward_shadow_logging_only": True,
        },
        "sizing_policies": list(FORWARD_SIZING_POLICIES),
        "equity_levels_yen": list(EQUITY_LEVELS),
        "output_paths": {k: str(v) for k, v in paths.items()},
        "forward_summary": forward_summary,
        "last_run": last_run,
        "verdict": {"note": note},
        "_entry_rows": entry_rows,
        "_summary_rows": summary_rows,
    }


@dataclass
class RiskSizingForwardShadowLogger:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "entry_log": self.reports_dir / "phase262_risk_sizing_forward_entry_by_day.csv",
            "summary_by_day": self.reports_dir / "phase262_risk_sizing_forward_summary_by_day.csv",
            "summary": self.reports_dir / "phase262_risk_sizing_forward_summary.json",
            "report": self.reports_dir / "phase262_risk_sizing_report.md",
        }

    def run(
        self,
        *,
        day: Optional[str] = None,
        backfill_phase261: bool = False,
    ) -> dict[str, Any]:
        return run_forward_shadow_logger(
            repo_root=self.repo_root,
            reports_dir=self.reports_dir,
            day=day,
            backfill_phase261=backfill_phase261,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["entry_log"].parent.mkdir(parents=True, exist_ok=True)
        _write_csv(paths["entry_log"], FORWARD_ENTRY_FIELDS, result.get("_entry_rows") or [])
        _write_csv(paths["summary_by_day"], FORWARD_SUMMARY_BY_DAY_FIELDS, result.get("_summary_rows") or [])
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths["report"].write_text(build_report_markdown(result), encoding="utf-8")
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from storage.results_paths import dual_write_output_paths, infer_day_from_result

        day = infer_day_from_result(result) or datetime.now(ZoneInfo("Asia/Tokyo")).strftime(
            "%Y%m%d"
        )
        dual_write_output_paths(self.repo_root, day, paths)
        return paths
