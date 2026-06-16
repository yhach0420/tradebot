"""
Phase263-Equity-Position-Based-Dynamic-Stop-Shadow.

Shadow evaluation of equity/position-value-derived dynamic stops vs fixed -1.2%.
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
    _norm_symbol,
    _pf,
    _write_csv,
    load_trades_by_day,
)
from research.phase374_dynamic40_universe_quality_review import resolve_pnl_yen_100
from research.position_exposure_audit import _percentile, _win_rate

JST = ZoneInfo("Asia/Tokyo")

PERIOD_START = "20260529"
PERIOD_END: Optional[str] = None
MIN_FORWARD_PERIOD_DAYS = 10
FIXED_STOP_PCT = 1.2
SHARES = 100

EQUITY_LEVELS: tuple[int, ...] = (
    500_000,
    1_000_000,
    1_500_000,
    2_000_000,
    2_500_000,
    3_000_000,
    3_500_000,
    4_000_000,
    4_500_000,
    5_000_000,
    10_000_000,
)

RISK_PCT_VALUES: tuple[float, ...] = (0.0025, 0.005, 0.0075, 0.01)

RISK_PCT_TO_POLICY: dict[float, str] = {
    0.0025: "dynamic_stop_risk_0p25",
    0.005: "dynamic_stop_risk_0p5",
    0.0075: "dynamic_stop_risk_0p75",
    0.01: "dynamic_stop_risk_1p0",
}

FIXED_POLICY = "fixed_stop_1p2"

ENTRY_FIELDS = [
    "day",
    "symbol",
    "entry_price",
    "shares",
    "position_value_yen",
    "equity_yen",
    "risk_pct",
    "risk_budget_yen",
    "dynamic_stop_pct",
    "effective_stop_pct",
    "fixed_stop_pct",
    "stop_tightened",
    "pnl_yen_100_original",
    "pnl_yen_shadow_dynamic_stop",
    "max_loss_yen_allowed",
    "loss_ratio_to_equity",
]

SUMMARY_FIELDS = [
    "equity_yen",
    "risk_pct",
    "stop_policy",
    "entry_count",
    "stop_tightened_count",
    "stop_tightened_rate",
    "total_pnl_yen_shadow",
    "profit_factor",
    "win_rate",
    "max_loss_yen",
    "max_loss_ratio_to_equity",
    "avg_effective_stop_pct",
    "p10_effective_stop_pct",
    "p50_effective_stop_pct",
    "p90_effective_stop_pct",
    "delta_vs_fixed_stop",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _risk_pct_label(risk_pct: float) -> str:
    return f"{round(risk_pct * 100, 4)}%"


def resolve_period_days(
    trades_by_day: Mapping[str, Sequence[Any]],
    *,
    period_start: str = PERIOD_START,
    period_end: Optional[str] = PERIOD_END,
) -> list[str]:
    return sorted(
        d
        for d in trades_by_day
        if period_start <= d and (period_end is None or d <= period_end)
    )


def best_policy_for_equity(
    summary_rows: Sequence[Mapping[str, Any]],
    *,
    equity_yen: int,
) -> str:
    best_risk = RISK_PCT_VALUES[0]
    best_delta = -1e18
    for risk_pct in RISK_PCT_VALUES:
        row = _summary_row(summary_rows, equity_yen=equity_yen, risk_pct=risk_pct)
        delta = _float(row.get("delta_vs_fixed_stop")) or 0.0
        if delta > best_delta:
            best_delta = delta
            best_risk = risk_pct
    return RISK_PCT_TO_POLICY[best_risk]


def compute_stop_fields(
    *,
    entry_price: float,
    shares: int,
    equity_yen: int,
    risk_pct: float,
) -> dict[str, Any]:
    position_value_yen = round(entry_price * shares, 2)
    risk_budget_yen = round(equity_yen * risk_pct, 2)
    dynamic_stop_pct = round(risk_budget_yen / position_value_yen * 100.0, 6) if position_value_yen > 0 else FIXED_STOP_PCT
    effective_stop_pct = round(min(FIXED_STOP_PCT, dynamic_stop_pct), 6)
    max_loss_yen_allowed = round(position_value_yen * effective_stop_pct / 100.0, 2)
    loss_ratio_to_equity = round(max_loss_yen_allowed / equity_yen, 6) if equity_yen > 0 else None
    return {
        "position_value_yen": position_value_yen,
        "risk_budget_yen": risk_budget_yen,
        "dynamic_stop_pct": dynamic_stop_pct,
        "effective_stop_pct": effective_stop_pct,
        "fixed_stop_pct": FIXED_STOP_PCT,
        "stop_tightened": effective_stop_pct + 1e-9 < FIXED_STOP_PCT,
        "max_loss_yen_allowed": max_loss_yen_allowed,
        "loss_ratio_to_equity": loss_ratio_to_equity,
    }


def shadow_pnl_pct(
    *,
    actual_pnl_pct: float,
    mae_pct: Optional[float],
    effective_stop_pct: float,
) -> float:
    mae_abs = abs(mae_pct) if mae_pct is not None else 0.0
    if mae_abs >= effective_stop_pct:
        return -effective_stop_pct
    return actual_pnl_pct


def shadow_pnl_yen(
    *,
    entry_price: float,
    shares: int,
    actual_pnl_pct: float,
    mae_pct: Optional[float],
    effective_stop_pct: float,
) -> float:
    pct = shadow_pnl_pct(
        actual_pnl_pct=actual_pnl_pct,
        mae_pct=mae_pct,
        effective_stop_pct=effective_stop_pct,
    )
    return round(entry_price * shares * pct / 100.0, 2)


def load_period_entries(
    trades_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    period_days: Sequence[str],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for day in period_days:
        for row in trades_by_day.get(day) or []:
            entry_price = _float(row.get("entry_price"))
            if entry_price is None or entry_price <= 0:
                continue
            pnl_yen_100 = _float(row.get("pnl_yen_100"))
            if pnl_yen_100 is None:
                pnl_yen_100 = resolve_pnl_yen_100(dict(row))
            actual_pnl_pct = _float(row.get("realized_pnl_pct"))
            if actual_pnl_pct is None:
                actual_pnl_pct = pnl_yen_100 / (entry_price * SHARES) * 100.0
            entries.append(
                {
                    "day": day,
                    "symbol": _norm_symbol(str(row.get("symbol") or "")),
                    "entry_price": round(entry_price, 4),
                    "shares": SHARES,
                    "pnl_yen_100_original": round(pnl_yen_100, 2),
                    "actual_pnl_pct": round(actual_pnl_pct, 6),
                    "mae_pct": _float(row.get("mae_pct")),
                }
            )
    return entries


def build_entry_level_rows(base_entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for base in base_entries:
        entry_price = _float(base.get("entry_price")) or 0.0
        shares = int(base.get("shares") or SHARES)
        actual_pnl_pct = _float(base.get("actual_pnl_pct")) or 0.0
        mae_pct = _float(base.get("mae_pct"))
        pnl_original = _float(base.get("pnl_yen_100_original")) or 0.0

        for equity_yen in EQUITY_LEVELS:
            for risk_pct in RISK_PCT_VALUES:
                stop = compute_stop_fields(
                    entry_price=entry_price,
                    shares=shares,
                    equity_yen=equity_yen,
                    risk_pct=risk_pct,
                )
                pnl_shadow = shadow_pnl_yen(
                    entry_price=entry_price,
                    shares=shares,
                    actual_pnl_pct=actual_pnl_pct,
                    mae_pct=mae_pct,
                    effective_stop_pct=_float(stop.get("effective_stop_pct")) or FIXED_STOP_PCT,
                )
                rows.append(
                    {
                        "day": str(base.get("day") or ""),
                        "symbol": str(base.get("symbol") or ""),
                        "entry_price": entry_price,
                        "shares": shares,
                        "equity_yen": equity_yen,
                        "risk_pct": risk_pct,
                        "pnl_yen_100_original": pnl_original,
                        "pnl_yen_shadow_dynamic_stop": pnl_shadow,
                        **stop,
                    }
                )
    return rows


def _aggregate_subset(
    subset: Sequence[Mapping[str, Any]],
    *,
    equity_yen: int,
    risk_pct: float,
    stop_policy: str,
    fixed_total: Optional[float] = None,
) -> dict[str, Any]:
    yens = [_float(r.get("pnl_yen_shadow_dynamic_stop")) or 0.0 for r in subset]
    stops = [_float(r.get("effective_stop_pct")) or FIXED_STOP_PCT for r in subset]
    tightened = sum(1 for r in subset if bool(r.get("stop_tightened")))
    loss_ratios = [
        abs(min(0.0, _float(r.get("pnl_yen_shadow_dynamic_stop")) or 0.0)) / equity_yen
        for r in subset
        if (_float(r.get("pnl_yen_shadow_dynamic_stop")) or 0.0) < 0 and equity_yen > 0
    ]
    total_pnl = round(sum(yens), 2)
    max_loss = round(min(yens), 2) if yens else None
    max_loss_ratio = round(max(loss_ratios), 6) if loss_ratios else 0.0
    entry_count = len(subset)
    return {
        "equity_yen": equity_yen,
        "risk_pct": risk_pct,
        "stop_policy": stop_policy,
        "entry_count": entry_count,
        "stop_tightened_count": tightened,
        "stop_tightened_rate": round(tightened / entry_count, 6) if entry_count else None,
        "total_pnl_yen_shadow": total_pnl,
        "profit_factor": _pf(yens),
        "win_rate": _win_rate(yens),
        "max_loss_yen": max_loss,
        "max_loss_ratio_to_equity": max_loss_ratio,
        "avg_effective_stop_pct": round(sum(stops) / len(stops), 6) if stops else None,
        "p10_effective_stop_pct": _percentile(stops, 10),
        "p50_effective_stop_pct": _percentile(stops, 50),
        "p90_effective_stop_pct": _percentile(stops, 90),
        "delta_vs_fixed_stop": round(total_pnl - fixed_total, 2) if fixed_total is not None else 0.0,
    }


def _fixed_shadow_rows(
    base_entries: Sequence[Mapping[str, Any]],
    *,
    equity_yen: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for base in base_entries:
        entry_price = _float(base.get("entry_price")) or 0.0
        rows.append(
            {
                "effective_stop_pct": FIXED_STOP_PCT,
                "stop_tightened": False,
                "loss_ratio_to_equity": round(
                    (entry_price * SHARES * FIXED_STOP_PCT / 100.0) / equity_yen,
                    6,
                )
                if equity_yen > 0
                else 0.0,
                "pnl_yen_shadow_dynamic_stop": shadow_pnl_yen(
                    entry_price=entry_price,
                    shares=SHARES,
                    actual_pnl_pct=_float(base.get("actual_pnl_pct")) or 0.0,
                    mae_pct=_float(base.get("mae_pct")),
                    effective_stop_pct=FIXED_STOP_PCT,
                ),
            }
        )
    return rows


def aggregate_summary_rows(
    entry_rows: Sequence[Mapping[str, Any]],
    base_entries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    fixed_totals: dict[int, float] = {}

    for equity_yen in EQUITY_LEVELS:
        fixed_subset = _fixed_shadow_rows(base_entries, equity_yen=equity_yen)
        fixed_row = _aggregate_subset(
            fixed_subset,
            equity_yen=equity_yen,
            risk_pct=0.0,
            stop_policy=FIXED_POLICY,
            fixed_total=None,
        )
        fixed_row["delta_vs_fixed_stop"] = 0.0
        fixed_totals[equity_yen] = _float(fixed_row.get("total_pnl_yen_shadow")) or 0.0
        rows.append(fixed_row)

        for risk_pct in RISK_PCT_VALUES:
            subset = [
                r
                for r in entry_rows
                if int(r.get("equity_yen") or 0) == equity_yen and _float(r.get("risk_pct")) == risk_pct
            ]
            rows.append(
                _aggregate_subset(
                    subset,
                    equity_yen=equity_yen,
                    risk_pct=risk_pct,
                    stop_policy=RISK_PCT_TO_POLICY[risk_pct],
                    fixed_total=fixed_totals.get(equity_yen),
                )
            )
    return rows


def _summary_row(
    summary_rows: Sequence[Mapping[str, Any]],
    *,
    equity_yen: int,
    risk_pct: float,
    stop_policy: Optional[str] = None,
) -> dict[str, Any]:
    for row in summary_rows:
        if int(row.get("equity_yen") or 0) != equity_yen:
            continue
        if stop_policy is not None and str(row.get("stop_policy") or "") != stop_policy:
            continue
        if abs((_float(row.get("risk_pct")) or 0.0) - risk_pct) > 1e-9:
            continue
        return dict(row)
    return {}


def build_verdict(
    *,
    summary_rows: Sequence[Mapping[str, Any]],
    period_days: Sequence[str],
    entry_count: int,
) -> dict[str, Any]:
    primary_equity = 5_000_000
    equity_1p5m = 1_500_000
    fixed_5m = _summary_row(summary_rows, equity_yen=primary_equity, risk_pct=0.0, stop_policy=FIXED_POLICY)
    fixed_pnl = _float(fixed_5m.get("total_pnl_yen_shadow")) or 0.0
    fixed_pf = _float(fixed_5m.get("profit_factor")) or 0.0

    best_risk = RISK_PCT_VALUES[0]
    best_delta = -1e18
    for risk_pct in RISK_PCT_VALUES:
        row = _summary_row(summary_rows, equity_yen=primary_equity, risk_pct=risk_pct)
        delta = _float(row.get("delta_vs_fixed_stop")) or 0.0
        if delta > best_delta:
            best_delta = delta
            best_risk = risk_pct

    best_row = _summary_row(summary_rows, equity_yen=primary_equity, risk_pct=best_risk)
    best_policy_at_1p5m = best_policy_for_equity(summary_rows, equity_yen=equity_1p5m)
    adopt_not_allowed = len(period_days) < MIN_FORWARD_PERIOD_DAYS
    dynamic_stop_candidate = (
        entry_count >= 50
        and best_delta > 0
        and (_float(best_row.get("profit_factor")) or 0.0) >= fixed_pf
        if fixed_pf > 0
        else best_delta > 0
    )

    too_tight_flags: list[str] = []
    too_loose_flags: list[str] = []
    if entry_count > 0:
        for risk_pct in RISK_PCT_VALUES:
            row = _summary_row(summary_rows, equity_yen=primary_equity, risk_pct=risk_pct)
            tightened_rate = _float(row.get("stop_tightened_rate")) or 0.0
            delta = _float(row.get("delta_vs_fixed_stop")) or 0.0
            avg_stop = _float(row.get("avg_effective_stop_pct")) or FIXED_STOP_PCT
            if tightened_rate >= 0.50 and delta < -10_000:
                too_tight_flags.append(_risk_pct_label(risk_pct))
            if tightened_rate <= 0.05 and abs(delta) < 500:
                too_loose_flags.append(_risk_pct_label(risk_pct))
            if avg_stop <= 0.6 and tightened_rate >= 0.40:
                too_tight_flags.append(_risk_pct_label(risk_pct))

    row_1p5m = _summary_row(summary_rows, equity_yen=1_500_000, risk_pct=0.005)
    equity_1p5m_feasible = (
        int(row_1p5m.get("entry_count") or 0) > 0
        and (_float(row_1p5m.get("stop_tightened_rate")) or 1.0) < 0.90
        and (_float(row_1p5m.get("max_loss_ratio_to_equity")) or 0.0) <= 0.005 * 1.5
    )

    cap2_rows = [
        _summary_row(summary_rows, equity_yen=2_000_000, risk_pct=risk_pct)
        for risk_pct in RISK_PCT_VALUES
    ]
    cap2_double_stop_loss_ratio = any(
        (_float(row.get("max_loss_ratio_to_equity")) or 0.0) > 2.0 * (_float(row.get("risk_pct")) or 0.0)
        for row in cap2_rows
        if row
    )

    recommendation_parts: list[str] = []
    if not period_days:
        end_label = PERIOD_END or "open"
        recommendation_parts.append(
            f"No trades in period {PERIOD_START}-{end_label}; rerun when overlap sample exists."
        )
    elif entry_count < 50:
        recommendation_parts.append("Overlap sample is small; treat dynamic-stop shadows as indicative only.")
    if dynamic_stop_candidate:
        recommendation_parts.append(
            f"At 5M yen, {RISK_PCT_TO_POLICY[best_risk]} improves total shadow PnL vs fixed -1.2%."
        )
    if too_tight_flags:
        recommendation_parts.append(
            f"Risk budgets may be too tight at 5M: {', '.join(sorted(set(too_tight_flags)))}."
        )
    if too_loose_flags:
        recommendation_parts.append(
            f"Some risk budgets rarely tighten stops (capped at 1.2%): {', '.join(sorted(set(too_loose_flags)))}."
        )
    if cap2_double_stop_loss_ratio:
        recommendation_parts.append(
            "At 2M yen, realized max loss ratio can exceed 2× the configured risk budget on this sample."
        )
    if not recommendation_parts:
        recommendation_parts.append("Continue shadow logging; dynamic stop remains under observation.")

    return {
        "dynamic_stop_candidate": dynamic_stop_candidate,
        "best_risk_pct_at_5m": best_risk,
        "best_policy_at_5m": RISK_PCT_TO_POLICY[best_risk],
        "best_policy_at_1p5m": best_policy_at_1p5m,
        "adopt_not_allowed": adopt_not_allowed,
        "risk_pct_too_tight": bool(too_tight_flags),
        "risk_pct_too_tight_labels": sorted(set(too_tight_flags)),
        "risk_pct_too_loose": bool(too_loose_flags),
        "risk_pct_too_loose_labels": sorted(set(too_loose_flags)),
        "equity_1p5m_feasible": equity_1p5m_feasible,
        "cap2_double_stop_loss_ratio": cap2_double_stop_loss_ratio,
        "adoption_forbidden": True,
        "recommendation": " ".join(recommendation_parts),
    }


def build_report_markdown(result: Mapping[str, Any]) -> str:
    summary = result.get("summary") or {}
    verdict = result.get("verdict") or {}
    lines = [
        "# Phase263 Equity-Position-Based Dynamic Stop Shadow",
        "",
        "Shadow-only evaluation of equity/position-value-derived stops vs fixed -1.2%.",
        "",
        f"- period: {PERIOD_START} - {PERIOD_END or 'open'}",
        f"- period days: {', '.join(summary.get('period_days') or []) or '(none)'}",
        f"- base entries: {summary.get('base_entry_count')}",
        "",
        "## Verdict",
        "",
        f"- dynamic_stop_candidate: {verdict.get('dynamic_stop_candidate')}",
        f"- best_policy @ 1.5M: {verdict.get('best_policy_at_1p5m')}",
        f"- best_policy @ 5M: {verdict.get('best_policy_at_5m')}",
        f"- adopt_not_allowed: {verdict.get('adopt_not_allowed')}",
        f"- risk_pct_too_tight: {verdict.get('risk_pct_too_tight')}",
        f"- risk_pct_too_loose: {verdict.get('risk_pct_too_loose')}",
        f"- equity_1p5m_feasible: {verdict.get('equity_1p5m_feasible')}",
        f"- cap2_double_stop_loss_ratio: {verdict.get('cap2_double_stop_loss_ratio')}",
        f"- adoption_forbidden: {verdict.get('adoption_forbidden')}",
        "",
        "## Summary at 5,000,000 yen (shadow PnL vs fixed -1.2%)",
        "",
    ]
    for row in result.get("summary_by_equity_risk_pct") or []:
        if int(row.get("equity_yen") or 0) != 5_000_000:
            continue
        lines.append(
            f"- `{row.get('stop_policy')}`: pnl={row.get('total_pnl_yen_shadow')} "
            f"delta={row.get('delta_vs_fixed_stop')} tightened_rate={row.get('stop_tightened_rate')} "
            f"avg_stop={row.get('avg_effective_stop_pct')}"
        )
    lines.extend(["", str(verdict.get("recommendation") or ""), ""])
    return "\n".join(lines)


def run_equity_dynamic_stop_shadow(
    *,
    repo_root: Path,
    reports_dir: Path,
) -> dict[str, Any]:
    trades_by_day_raw = load_trades_by_day(repo_root)
    trades_by_day: dict[str, list[dict[str, Any]]] = {}
    for day, rows in trades_by_day_raw.items():
        norm_rows = []
        for row in rows:
            trade = dict(row)
            trade["symbol"] = _norm_symbol(str(trade.get("symbol") or ""))
            if trade.get("pnl_yen_100") is None:
                trade["pnl_yen_100"] = resolve_pnl_yen_100(trade)
            norm_rows.append(trade)
        trades_by_day[day] = norm_rows

    period_days = resolve_period_days(trades_by_day)
    base_entries = load_period_entries(trades_by_day, period_days=period_days)
    entry_rows = build_entry_level_rows(base_entries)
    summary_rows = aggregate_summary_rows(entry_rows, base_entries)
    verdict = build_verdict(
        summary_rows=summary_rows,
        period_days=period_days,
        entry_count=len(base_entries),
    )

    return {
        "phase": "263-Equity-Position-Based-Dynamic-Stop-Shadow",
        "title": "Equity-position-based dynamic stop shadow",
        "generated_at": _now_iso(),
        "purpose": "Shadow-validate dynamic stops derived from equity risk budget and position value",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "runtime_reflected": False,
            "universe_change_forbidden": True,
            "entry_change_forbidden": True,
            "adoption_forbidden": True,
        },
        "period": {"start": PERIOD_START, "end": PERIOD_END},
        "forward_shadow": {
            "min_period_days": MIN_FORWARD_PERIOD_DAYS,
            "open_ended": PERIOD_END is None,
        },
        "equity_levels_yen": list(EQUITY_LEVELS),
        "risk_pct_values": list(RISK_PCT_VALUES),
        "stop_policies": [FIXED_POLICY, *RISK_PCT_TO_POLICY.values()],
        "stop_model": {
            "fixed_stop_pct": FIXED_STOP_PCT,
            "risk_budget_yen": "equity_yen × risk_pct",
            "dynamic_stop_pct": "risk_budget_yen / position_value_yen × 100",
            "effective_stop_pct": f"min({FIXED_STOP_PCT}%, dynamic_stop_pct)",
            "shares": SHARES,
        },
        "summary": {
            "period_days": period_days,
            "base_entry_count": len(base_entries),
        },
        "verdict": verdict,
        "summary_by_equity_risk_pct": summary_rows,
        "_entry_rows": entry_rows,
    }


@dataclass
class EquityDynamicStopShadow:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "entry_level": self.reports_dir / "phase263_entry_level_dynamic_stop.csv",
            "summary_by_equity_risk_pct": self.reports_dir / "phase263_summary_by_equity_risk_pct.csv",
            "summary": self.reports_dir / "phase263_equity_dynamic_stop_summary.json",
            "report": self.reports_dir / "phase263_report.md",
        }

    def run(self) -> dict[str, Any]:
        return run_equity_dynamic_stop_shadow(repo_root=self.repo_root, reports_dir=self.reports_dir)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["entry_level"].parent.mkdir(parents=True, exist_ok=True)
        _write_csv(paths["entry_level"], ENTRY_FIELDS, result.get("_entry_rows") or [])
        _write_csv(
            paths["summary_by_equity_risk_pct"],
            SUMMARY_FIELDS,
            result.get("summary_by_equity_risk_pct") or [],
        )
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
