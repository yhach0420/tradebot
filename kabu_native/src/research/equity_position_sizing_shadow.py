"""
Phase260B-Equity-Aware-Position-Sizing-Shadow.

Shadow evaluation of equity-aware position sizing vs fixed 100-share baseline.
Observation only — no Runtime / Universe / Entry / YAML changes.
"""

from __future__ import annotations

import json
import math
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

EQUITY_LEVELS: tuple[int, ...] = (
    1_000_000,
    3_000_000,
    5_000_000,
    10_000_000,
)

SIZING_POLICIES: tuple[str, ...] = (
    "fixed_100_shares",
    "max_position_30pct",
    "max_position_50pct",
    "cap3_equal_budget",
    "min_lot_or_skip",
)

HIGH_PRICE_THRESHOLD = 3000.0
MIN_LOT = 100

ENTRY_SIZING_FIELDS = [
    "day",
    "symbol",
    "equity_yen",
    "sizing_policy",
    "entry_price",
    "shares_shadow",
    "position_value",
    "position_ratio",
    "pnl_yen_100",
    "pnl_yen_scaled",
    "skipped_due_to_min_lot",
]

POLICY_BY_EQUITY_FIELDS = [
    "equity_yen",
    "sizing_policy",
    "entry_count",
    "skipped_count",
    "total_pnl_yen_scaled",
    "profit_factor",
    "win_rate",
    "max_loss_yen_scaled",
    "avg_position_ratio",
    "p95_position_ratio",
    "capital_utilization_avg",
    "high_price_entry_count",
    "high_price_pnl_scaled",
    "skipped_high_price_count",
]

HIGH_PRICE_IMPACT_FIELDS = [
    "equity_yen",
    "sizing_policy",
    "high_price_threshold",
    "executed_high_price_count",
    "skipped_high_price_count",
    "high_price_pnl_scaled",
    "high_price_pf",
    "high_price_win_rate",
    "fixed_100_high_price_pnl",
    "pnl_vs_fixed_100_pct",
    "skip_rate_high_price",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_overlap_days(*, reports_dir: Path) -> list[str]:
    phase260a = _load_json(reports_dir / "phase260a_position_exposure_audit_summary.json")
    days = list((phase260a.get("summary") or {}).get("trade_overlap_days") or [])
    if days:
        return sorted(days)
    phase259 = _load_json(reports_dir / "phase259_price_band_policy_shadow_summary.json")
    days = list((phase259.get("summary") or {}).get("trade_overlap_days") or [])
    return sorted(days)


def budget_yen(equity_yen: int, policy: str) -> float:
    if policy == "max_position_30pct":
        return equity_yen * 0.30
    if policy == "max_position_50pct":
        return equity_yen * 0.50
    if policy == "cap3_equal_budget":
        return equity_yen / 3.0
    if policy == "min_lot_or_skip":
        return float(equity_yen)
    return float(equity_yen)


def compute_shares_shadow(
    entry_price: float,
    *,
    equity_yen: int,
    policy: str,
) -> tuple[int, bool]:
    if entry_price <= 0:
        return 0, True
    if policy == "fixed_100_shares":
        return MIN_LOT, False
    budget = budget_yen(equity_yen, policy)
    lots = math.floor(budget / entry_price / MIN_LOT)
    shares = int(lots * MIN_LOT)
    if shares < MIN_LOT:
        return 0, True
    return shares, False


def scale_entry_row(
    *,
    day: str,
    symbol: str,
    entry_price: float,
    pnl_yen_100: float,
    equity_yen: int,
    policy: str,
) -> dict[str, Any]:
    shares, skipped = compute_shares_shadow(entry_price, equity_yen=equity_yen, policy=policy)
    position_value = round(entry_price * shares, 2) if not skipped else 0.0
    ratio = round(position_value / float(equity_yen), 6) if equity_yen > 0 and not skipped else 0.0
    pnl_scaled = round(pnl_yen_100 * shares / MIN_LOT, 2) if not skipped else 0.0
    return {
        "day": day,
        "symbol": symbol,
        "equity_yen": equity_yen,
        "sizing_policy": policy,
        "entry_price": round(entry_price, 4),
        "shares_shadow": shares,
        "position_value": position_value,
        "position_ratio": ratio,
        "pnl_yen_100": round(pnl_yen_100, 2),
        "pnl_yen_scaled": pnl_scaled,
        "skipped_due_to_min_lot": skipped,
    }


def load_overlap_entries(
    trades_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    overlap_days: Sequence[str],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for day in overlap_days:
        for row in trades_by_day.get(day) or []:
            ep = _float(row.get("entry_price"))
            if ep is None or ep <= 0:
                continue
            pnl = _float(row.get("pnl_yen_100"))
            if pnl is None:
                pnl = resolve_pnl_yen_100(dict(row))
            entries.append(
                {
                    "day": day,
                    "symbol": _norm_symbol(str(row.get("symbol") or "")),
                    "entry_price": ep,
                    "pnl_yen_100": round(pnl or 0.0, 2),
                }
            )
    return entries


def build_entry_level_rows(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for base in entries:
        for equity in EQUITY_LEVELS:
            for policy in SIZING_POLICIES:
                rows.append(
                    scale_entry_row(
                        day=str(base.get("day") or ""),
                        symbol=str(base.get("symbol") or ""),
                        entry_price=_float(base.get("entry_price")) or 0.0,
                        pnl_yen_100=_float(base.get("pnl_yen_100")) or 0.0,
                        equity_yen=equity,
                        policy=policy,
                    )
                )
    return rows


def aggregate_policy_rows(entry_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for equity in EQUITY_LEVELS:
        for policy in SIZING_POLICIES:
            subset = [
                r
                for r in entry_rows
                if int(r.get("equity_yen") or 0) == equity and str(r.get("sizing_policy") or "") == policy
            ]
            executed = [r for r in subset if not r.get("skipped_due_to_min_lot")]
            skipped = [r for r in subset if r.get("skipped_due_to_min_lot")]
            yens = [_float(r.get("pnl_yen_scaled")) or 0.0 for r in executed]
            ratios = [_float(r.get("position_ratio")) or 0.0 for r in executed]
            utilizations = ratios
            high_exec = [r for r in executed if (_float(r.get("entry_price")) or 0.0) >= HIGH_PRICE_THRESHOLD]
            high_skip = [r for r in skipped if (_float(r.get("entry_price")) or 0.0) >= HIGH_PRICE_THRESHOLD]
            high_yens = [_float(r.get("pnl_yen_scaled")) or 0.0 for r in high_exec]
            rows.append(
                {
                    "equity_yen": equity,
                    "sizing_policy": policy,
                    "entry_count": len(executed),
                    "skipped_count": len(skipped),
                    "total_pnl_yen_scaled": round(sum(yens), 2),
                    "profit_factor": _pf(yens),
                    "win_rate": _win_rate(yens),
                    "max_loss_yen_scaled": round(min(yens), 2) if yens else None,
                    "avg_position_ratio": round(sum(ratios) / len(ratios), 6) if ratios else None,
                    "p95_position_ratio": _percentile(ratios, 95),
                    "capital_utilization_avg": round(sum(utilizations) / len(utilizations), 6) if utilizations else None,
                    "high_price_entry_count": len(high_exec),
                    "high_price_pnl_scaled": round(sum(high_yens), 2),
                    "skipped_high_price_count": len(high_skip),
                }
            )
    return rows


def build_high_price_impact_rows(
    policy_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    fixed_by_equity: dict[int, float] = {}
    for row in policy_rows:
        if str(row.get("sizing_policy") or "") == "fixed_100_shares":
            fixed_by_equity[int(row.get("equity_yen") or 0)] = _float(row.get("high_price_pnl_scaled")) or 0.0

    rows: list[dict[str, Any]] = []
    for row in policy_rows:
        equity = int(row.get("equity_yen") or 0)
        policy = str(row.get("sizing_policy") or "")
        executed = int(row.get("high_price_entry_count") or 0)
        skipped_hp = int(row.get("skipped_high_price_count") or 0)
        total_hp = executed + skipped_hp
        high_pnl = _float(row.get("high_price_pnl_scaled")) or 0.0
        fixed_pnl = fixed_by_equity.get(equity, 0.0)
        skip_rate = round(skipped_hp / total_hp, 4) if total_hp else None
        pnl_vs_fixed = round(high_pnl / fixed_pnl, 4) if fixed_pnl else None
        rows.append(
            {
                "equity_yen": equity,
                "sizing_policy": policy,
                "high_price_threshold": HIGH_PRICE_THRESHOLD,
                "executed_high_price_count": executed,
                "skipped_high_price_count": skipped_hp,
                "high_price_pnl_scaled": round(high_pnl, 2),
                "high_price_pf": None,
                "high_price_win_rate": None,
                "fixed_100_high_price_pnl": round(fixed_pnl, 2),
                "pnl_vs_fixed_100_pct": pnl_vs_fixed,
                "skip_rate_high_price": skip_rate,
            }
        )
    return rows


def enrich_high_price_impact(
    impact_rows: list[dict[str, Any]],
    entry_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    for row in impact_rows:
        equity = int(row.get("equity_yen") or 0)
        policy = str(row.get("sizing_policy") or "")
        subset = [
            r
            for r in entry_rows
            if int(r.get("equity_yen") or 0) == equity
            and str(r.get("sizing_policy") or "") == policy
            and not r.get("skipped_due_to_min_lot")
            and (_float(r.get("entry_price")) or 0.0) >= HIGH_PRICE_THRESHOLD
        ]
        yens = [_float(r.get("pnl_yen_scaled")) or 0.0 for r in subset]
        row["high_price_pf"] = _pf(yens)
        row["high_price_win_rate"] = _win_rate(yens)
    return impact_rows


def _policy_row(
    policy_rows: Sequence[Mapping[str, Any]],
    *,
    equity: int,
    policy: str,
) -> dict[str, Any]:
    for row in policy_rows:
        if int(row.get("equity_yen") or 0) == equity and str(row.get("sizing_policy") or "") == policy:
            return dict(row)
    return {}


def build_verdict(
    *,
    policy_rows: Sequence[Mapping[str, Any]],
    impact_rows: Sequence[Mapping[str, Any]],
    phase259: Mapping[str, Any],
) -> dict[str, Any]:
    fixed_1m = _policy_row(policy_rows, equity=1_000_000, policy="fixed_100_shares")
    lot_1m = _policy_row(policy_rows, equity=1_000_000, policy="min_lot_or_skip")
    cap30_1m = _policy_row(policy_rows, equity=1_000_000, policy="max_position_30pct")
    fixed_5m = _policy_row(policy_rows, equity=5_000_000, policy="fixed_100_shares")
    cap30_5m = _policy_row(policy_rows, equity=5_000_000, policy="max_position_30pct")

    hp_skip_1m = max(
        _float(lot_1m.get("skipped_high_price_count")) or 0.0,
        _float(cap30_1m.get("skipped_high_price_count")) or 0.0,
    )
    hp_total_1m = (_float(fixed_1m.get("high_price_entry_count")) or 0.0) + hp_skip_1m
    equity_1m_high_price_not_feasible = hp_skip_1m / hp_total_1m >= 0.30 if hp_total_1m else False

    fixed_3m_hp = _float(_policy_row(policy_rows, equity=3_000_000, policy="fixed_100_shares").get("high_price_pnl_scaled")) or 0.0
    cap30_3m_hp = _float(_policy_row(policy_rows, equity=3_000_000, policy="max_position_30pct").get("high_price_pnl_scaled")) or 0.0
    equity_3m_partial_feasible = fixed_3m_hp > 0 and cap30_3m_hp >= fixed_3m_hp * 0.40

    lot_5m = _policy_row(policy_rows, equity=5_000_000, policy="min_lot_or_skip")
    fixed_5m_hp = _float(fixed_5m.get("high_price_pnl_scaled")) or 0.0
    cap30_5m_hp = _float(cap30_5m.get("high_price_pnl_scaled")) or 0.0
    equity_5m_high_price_feasible = fixed_5m_hp > 0 and cap30_5m_hp >= fixed_5m_hp * 0.75

    allow_high_delta = 0.0
    for row in phase259.get("risk_metrics") or []:
        if str(row.get("policy") or "") == "allow_high_keep_low_filter":
            allow_high_delta = _float(row.get("delta_vs_actual")) or 0.0
            break

    cap30_5m_max_loss = abs(_float(cap30_5m.get("max_loss_yen_scaled")) or 0.0)
    lot_5m_max_loss = abs(_float(lot_5m.get("max_loss_yen_scaled")) or 0.0)
    cap30_5m_avg_ratio = _float(cap30_5m.get("avg_position_ratio")) or 0.0
    sizing_preferred_over_price_cap = (
        equity_5m_high_price_feasible
        and allow_high_delta > 0
        and cap30_5m_avg_ratio <= 0.31
        and cap30_5m_max_loss <= lot_5m_max_loss
    )

    recommendation_parts: list[str] = []
    if equity_1m_high_price_not_feasible:
        recommendation_parts.append(
            "At 1,000,000 yen, high-price entries are largely skipped under equity-aware sizing; "
            "fixed-100-share shadow PnL overstates feasibility."
        )
    if equity_3m_partial_feasible:
        recommendation_parts.append(
            "At 3,000,000 yen, partial high-price PnL is recoverable with sizing caps."
        )
    if equity_5m_high_price_feasible:
        recommendation_parts.append(
            "At 5,000,000 yen and above, high-price edge is largely reproducible with max_position_30pct."
        )
    if sizing_preferred_over_price_cap:
        recommendation_parts.append(
            "Position-sizing control is more actionable than price-cap removal for high-price exposure."
        )
    if not recommendation_parts:
        recommendation_parts.append("Overlap sample remains small; treat sizing shadows as indicative only.")

    return {
        "equity_1m_high_price_not_feasible": equity_1m_high_price_not_feasible,
        "equity_3m_partial_feasible": equity_3m_partial_feasible,
        "equity_5m_high_price_feasible": equity_5m_high_price_feasible,
        "sizing_preferred_over_price_cap": sizing_preferred_over_price_cap,
        "adoption_forbidden": True,
        "recommendation": " ".join(recommendation_parts),
    }


def build_report_markdown(result: Mapping[str, Any]) -> str:
    verdict = result.get("verdict") or {}
    summary = result.get("summary") or {}
    lines = [
        "# Phase260B Equity-Aware Position Sizing Shadow",
        "",
        "Shadow-only evaluation of equity-aware sizing vs fixed 100-share baseline.",
        "",
        f"- overlap days: {', '.join(summary.get('trade_overlap_days') or [])}",
        f"- base entries: {summary.get('base_entry_count')}",
        "",
        "## Verdict",
        "",
        f"- equity_1m_high_price_not_feasible: {verdict.get('equity_1m_high_price_not_feasible')}",
        f"- equity_3m_partial_feasible: {verdict.get('equity_3m_partial_feasible')}",
        f"- equity_5m_high_price_feasible: {verdict.get('equity_5m_high_price_feasible')}",
        f"- sizing_preferred_over_price_cap: {verdict.get('sizing_preferred_over_price_cap')}",
        f"- adoption_forbidden: {verdict.get('adoption_forbidden')}",
        "",
        "## Policy by equity (total PnL scaled)",
        "",
    ]
    for row in result.get("policy_by_equity") or []:
        if str(row.get("sizing_policy")) == "fixed_100_shares":
            continue
        lines.append(
            f"- {row.get('equity_yen')} yen / `{row.get('sizing_policy')}`: "
            f"pnl={row.get('total_pnl_yen_scaled')} skipped={row.get('skipped_count')} "
            f"high_pnl={row.get('high_price_pnl_scaled')}"
        )
    lines.extend(
        [
            "",
            "## High-price sizing impact (5M yen)",
            "",
        ]
    )
    for row in result.get("high_price_sizing_impact") or []:
        if int(row.get("equity_yen") or 0) != 5_000_000:
            continue
        lines.append(
            f"- `{row.get('sizing_policy')}`: high_pnl={row.get('high_price_pnl_scaled')} "
            f"skip_rate={row.get('skip_rate_high_price')} vs_fixed={row.get('pnl_vs_fixed_100_pct')}"
        )
    lines.extend(["", str(verdict.get("recommendation") or ""), ""])
    return "\n".join(lines)


def run_equity_position_sizing_shadow(
    *,
    repo_root: Path,
    reports_dir: Path,
) -> dict[str, Any]:
    phase260a = _load_json(reports_dir / "phase260a_position_exposure_audit_summary.json")
    phase259 = _load_json(reports_dir / "phase259_price_band_policy_shadow_summary.json")
    overlap_days = resolve_overlap_days(reports_dir=reports_dir)

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

    base_entries = load_overlap_entries(trades_by_day, overlap_days=overlap_days)
    entry_rows = build_entry_level_rows(base_entries)
    policy_rows = aggregate_policy_rows(entry_rows)
    impact_rows = enrich_high_price_impact(build_high_price_impact_rows(policy_rows), entry_rows)
    verdict = build_verdict(policy_rows=policy_rows, impact_rows=impact_rows, phase259=phase259)

    return {
        "phase": "260B-Equity-Aware-Position-Sizing-Shadow",
        "title": "Equity-aware position sizing shadow",
        "generated_at": _now_iso(),
        "purpose": "Shadow-evaluate equity-aware sizing vs fixed 100-share and price-cap proxies",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "runtime_reflected": False,
            "universe_change_forbidden": True,
            "entry_change_forbidden": True,
            "adoption_forbidden": True,
        },
        "inputs": {
            "structural_trades": str(repo_root / "kabu_native" / "results" / "small_paper"),
            "phase260a_summary": str(reports_dir / "phase260a_position_exposure_audit_summary.json"),
            "phase259_summary": str(reports_dir / "phase259_price_band_policy_shadow_summary.json"),
        },
        "equity_levels_yen": list(EQUITY_LEVELS),
        "sizing_policies": list(SIZING_POLICIES),
        "summary": {
            "trade_overlap_days": overlap_days,
            "base_entry_count": len(base_entries),
            "phase260a_verdict": (phase260a.get("verdict") or {}),
        },
        "verdict": verdict,
        "policy_by_equity": policy_rows,
        "high_price_sizing_impact": impact_rows,
        "_entry_rows": entry_rows,
    }


@dataclass
class EquityPositionSizingShadow:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase260b_equity_position_sizing_summary.json",
            "entry_level": self.reports_dir / "phase260b_entry_level_sizing.csv",
            "policy_by_equity": self.reports_dir / "phase260b_policy_by_equity.csv",
            "high_price_impact": self.reports_dir / "phase260b_high_price_sizing_impact.csv",
            "report": self.reports_dir / "phase260b_report.md",
        }

    def run(self) -> dict[str, Any]:
        return run_equity_position_sizing_shadow(repo_root=self.repo_root, reports_dir=self.reports_dir)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["summary"].parent.mkdir(parents=True, exist_ok=True)
        _write_csv(paths["entry_level"], ENTRY_SIZING_FIELDS, result.get("_entry_rows") or [])
        _write_csv(paths["policy_by_equity"], POLICY_BY_EQUITY_FIELDS, result.get("policy_by_equity") or [])
        _write_csv(paths["high_price_impact"], HIGH_PRICE_IMPACT_FIELDS, result.get("high_price_sizing_impact") or [])
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths["report"].write_text(build_report_markdown(result), encoding="utf-8")
        return paths
