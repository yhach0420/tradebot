"""
Phase260A-Position-Exposure-Audit.

Determine whether high-price band profits (Phase257-259) reflect price limits
or position sizing constraints at various equity levels. Observation only.
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
    _pf,
    _norm_symbol,
    _write_csv,
    load_trades_by_day,
)
from research.phase374_dynamic40_universe_quality_review import resolve_pnl_yen_100

JST = ZoneInfo("Asia/Tokyo")

EQUITY_LEVELS: tuple[int, ...] = (
    1_000_000,
    2_000_000,
    3_000_000,
    5_000_000,
    10_000_000,
)

HIGH_PRICE_BANDS = ("3000-5000", "5000-10000", "10000+")
HIGH_PRICE_THRESHOLD = 3000.0

PRICE_BANDS: tuple[tuple[str, float, Optional[float]], ...] = (
    ("<300", 0.0, 300.0),
    ("300-1000", 300.0, 1000.0),
    ("1000-3000", 1000.0, 3000.0),
    ("3000-5000", 3000.0, 5000.0),
    ("5000-10000", 5000.0, 10000.0),
    ("10000+", 10000.0, None),
)

EXPOSURE_DIST_FIELDS = [
    "equity_yen",
    "entry_count",
    "median_position_ratio",
    "p90_position_ratio",
    "p95_position_ratio",
    "max_position_ratio",
]

PRICE_BAND_EXPOSURE_FIELDS = [
    "equity_yen",
    "price_band",
    "entry_count",
    "pnl_yen_100",
    "profit_factor",
    "win_rate",
    "exposure_ratio_avg",
    "exposure_ratio_p95",
]

FEASIBILITY_FIELDS = [
    "equity_yen",
    "entry_count",
    "pct_position_ratio_gt_30",
    "pct_position_ratio_gt_50",
    "pct_position_ratio_gt_100",
    "high_price_entry_count",
    "high_price_pct_gt_30",
    "high_price_pct_gt_50",
    "high_price_pct_gt_100",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _win_rate(yens: Sequence[float]) -> Optional[float]:
    if not yens:
        return None
    return round(sum(1 for y in yens if y > 0) / len(yens), 4)


def _percentile(values: Sequence[float], pct: float) -> Optional[float]:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 6)
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct / 100.0
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    weight = rank - lo
    return round(ordered[lo] * (1.0 - weight) + ordered[hi] * weight, 6)


def price_band_label(entry_price: float) -> str:
    if entry_price <= 0:
        return "unknown"
    for label, lo, hi in PRICE_BANDS:
        if hi is None and entry_price >= lo:
            return label
        if hi is not None and lo <= entry_price < hi:
            return label
    return "unknown"


def position_value_100(entry_price: float) -> float:
    return round(entry_price * 100.0, 2)


def position_ratio(position_value: float, equity_yen: int) -> float:
    if equity_yen <= 0:
        return 0.0
    return round(position_value / float(equity_yen), 6)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_overlap_days(
    *,
    reports_dir: Path,
    phase257_path: Path,
    phase258_path: Path,
    phase259_path: Path,
) -> list[str]:
    for path in (phase259_path, phase258_path):
        payload = _load_json(path)
        days = list((payload.get("summary") or {}).get("trade_overlap_days") or payload.get("trade_overlap_days") or [])
        if days:
            return sorted(days)
    phase258 = _load_json(phase258_path)
    days = list(phase258.get("trade_overlap_days") or [])
    if days:
        return sorted(days)
    if phase257_path.is_file():
        import csv

        days_set: set[str] = set()
        with phase257_path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if str(row.get("pattern") or "") == "shadow_core10_dynamic40_pricecap_off":
                    day = str(row.get("day") or "")
                    if day:
                        days_set.add(day)
        if days_set:
            return sorted(days_set)
    return []


def enrich_entries(
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
            pv = position_value_100(ep)
            pnl = _float(row.get("pnl_yen_100"))
            if pnl is None:
                pnl = resolve_pnl_yen_100(dict(row))
            entry = {
                "day": day,
                "symbol": _norm_symbol(str(row.get("symbol") or "")),
                "entry_price": round(ep, 4),
                "position_value_100": pv,
                "price_band": price_band_label(ep),
                "pnl_yen_100": round(pnl or 0.0, 2),
                "close_reason": str(row.get("close_reason") or ""),
            }
            for equity in EQUITY_LEVELS:
                entry[f"position_ratio_{equity}"] = position_ratio(pv, equity)
            entries.append(entry)
    return entries


def build_exposure_distribution_rows(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for equity in EQUITY_LEVELS:
        ratios = [_float(e.get(f"position_ratio_{equity}")) or 0.0 for e in entries]
        if not ratios:
            continue
        rows.append(
            {
                "equity_yen": equity,
                "entry_count": len(ratios),
                "median_position_ratio": _percentile(ratios, 50),
                "p90_position_ratio": _percentile(ratios, 90),
                "p95_position_ratio": _percentile(ratios, 95),
                "max_position_ratio": round(max(ratios), 6),
            }
        )
    return rows


def build_price_band_exposure_rows(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for equity in EQUITY_LEVELS:
        for label, _, _ in PRICE_BANDS:
            subset = [e for e in entries if str(e.get("price_band") or "") == label]
            if not subset:
                rows.append(
                    {
                        "equity_yen": equity,
                        "price_band": label,
                        "entry_count": 0,
                        "pnl_yen_100": 0.0,
                        "profit_factor": None,
                        "win_rate": None,
                        "exposure_ratio_avg": None,
                        "exposure_ratio_p95": None,
                    }
                )
                continue
            yens = [_float(e.get("pnl_yen_100")) or 0.0 for e in subset]
            ratios = [_float(e.get(f"position_ratio_{equity}")) or 0.0 for e in subset]
            rows.append(
                {
                    "equity_yen": equity,
                    "price_band": label,
                    "entry_count": len(subset),
                    "pnl_yen_100": round(sum(yens), 2),
                    "profit_factor": _pf(yens),
                    "win_rate": _win_rate(yens),
                    "exposure_ratio_avg": round(sum(ratios) / len(ratios), 6) if ratios else None,
                    "exposure_ratio_p95": _percentile(ratios, 95),
                }
            )
    return rows


def _pct_over_threshold(ratios: Sequence[float], threshold: float) -> Optional[float]:
    if not ratios:
        return None
    return round(sum(1 for r in ratios if r > threshold) / len(ratios), 4)


def build_feasibility_rows(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for equity in EQUITY_LEVELS:
        ratios = [_float(e.get(f"position_ratio_{equity}")) or 0.0 for e in entries]
        high_entries = [e for e in entries if _float(e.get("entry_price")) >= HIGH_PRICE_THRESHOLD]
        high_ratios = [_float(e.get(f"position_ratio_{equity}")) or 0.0 for e in high_entries]
        rows.append(
            {
                "equity_yen": equity,
                "entry_count": len(ratios),
                "pct_position_ratio_gt_30": _pct_over_threshold(ratios, 0.30),
                "pct_position_ratio_gt_50": _pct_over_threshold(ratios, 0.50),
                "pct_position_ratio_gt_100": _pct_over_threshold(ratios, 1.00),
                "high_price_entry_count": len(high_ratios),
                "high_price_pct_gt_30": _pct_over_threshold(high_ratios, 0.30),
                "high_price_pct_gt_50": _pct_over_threshold(high_ratios, 0.50),
                "high_price_pct_gt_100": _pct_over_threshold(high_ratios, 1.00),
            }
        )
    return rows


def _high_price_pnl(entries: Sequence[Mapping[str, Any]]) -> float:
    return round(
        sum(_float(e.get("pnl_yen_100")) or 0.0 for e in entries if _float(e.get("entry_price")) >= HIGH_PRICE_THRESHOLD),
        2,
    )


def _nullable_float(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    return _float(val)


def build_verdict(
    *,
    entries: Sequence[Mapping[str, Any]],
    feasibility_rows: Sequence[Mapping[str, Any]],
    phase258: Mapping[str, Any],
    phase259: Mapping[str, Any],
) -> dict[str, Any]:
    high_pnl = _high_price_pnl(entries)
    high_price_delta_259 = 0.0
    for row in phase259.get("risk_metrics") or []:
        if str(row.get("policy") or "") == "allow_high_keep_low_filter":
            high_price_delta_259 = _float(row.get("high_price_delta")) or 0.0
            break

    by_equity = {int(r.get("equity_yen") or 0): r for r in feasibility_rows}
    eq1m = by_equity.get(1_000_000) or {}
    eq3m = by_equity.get(3_000_000) or {}
    eq5m = by_equity.get(5_000_000) or {}
    high_pnl_positive = high_pnl > 0 or high_price_delta_259 > 0
    high_pct_30_1m = _nullable_float(eq1m.get("high_price_pct_gt_30")) or 0.0
    high_pct_100_1m = _nullable_float(eq1m.get("high_price_pct_gt_100")) or 0.0
    high_pct_30_5m = _nullable_float(eq5m.get("high_price_pct_gt_30")) or 0.0
    high_pct_100_5m = _nullable_float(eq5m.get("high_price_pct_gt_100")) or 0.0

    price_cap_is_proxy_for_position_sizing = (
        high_pnl_positive
        and high_price_delta_259 > 0
        and (high_pct_30_1m >= 0.25 or high_pct_100_1m > 0)
    )

    high_price_edge_but_low_equity_problem = (
        high_pnl_positive
        and (high_pct_30_1m >= 0.30 or high_pct_100_1m > 0)
    )

    high_price_edge_and_large_equity_safe = (
        high_pnl_positive and high_pct_30_5m <= 0.20 and high_pct_100_5m == 0.0
    )

    min_equity_acceptable: Optional[int] = None
    for equity in EQUITY_LEVELS:
        row = by_equity.get(equity) or {}
        hp30 = _nullable_float(row.get("high_price_pct_gt_30"))
        hp100 = _nullable_float(row.get("high_price_pct_gt_100"))
        if hp30 is None or hp100 is None:
            continue
        if hp30 <= 0.20 and hp100 == 0.0:
            min_equity_acceptable = equity
            break

    recommendation_parts: list[str] = []
    if high_price_edge_but_low_equity_problem:
        recommendation_parts.append(
            "At 1,000,000 yen equity, high-price entries often exceed 30% position ratio; "
            "high-price shadow edge is hard to reproduce at 100-share fixed sizing."
        )
    if min_equity_acceptable == 3_000_000:
        recommendation_parts.append("At 3,000,000 yen, high-price exposure falls into a tolerable band.")
    elif min_equity_acceptable == 5_000_000:
        recommendation_parts.append(
            "At 5,000,000 yen and above, position sizing management matters more than price-cap removal."
        )
    elif min_equity_acceptable == 10_000_000:
        recommendation_parts.append("Even 5,000,000 yen leaves some high-price entries above 30%; 10M is safer.")
    elif min_equity_acceptable == 1_000_000:
        recommendation_parts.append("High-price exposure is already tolerable at 1,000,000 yen on this sample.")
    elif min_equity_acceptable == 2_000_000:
        recommendation_parts.append("At 2,000,000 yen, high-price exposure becomes mostly tolerable.")
    elif min_equity_acceptable is not None:
        recommendation_parts.append(f"Minimum tolerable equity on this sample: {min_equity_acceptable:,} yen.")
    if price_cap_is_proxy_for_position_sizing:
        recommendation_parts.append(
            "Phase257-259 high-price uplift likely proxies a position-sizing constraint, not a pure price-filter artifact."
        )
    if not recommendation_parts:
        recommendation_parts.append("Insufficient overlap sample; treat feasibility thresholds as indicative only.")

    return {
        "price_cap_is_proxy_for_position_sizing": price_cap_is_proxy_for_position_sizing,
        "high_price_edge_but_low_equity_problem": high_price_edge_but_low_equity_problem,
        "high_price_edge_and_large_equity_safe": high_price_edge_and_large_equity_safe,
        "high_price_pnl_yen_100_overlap": high_pnl,
        "phase259_allow_high_high_price_delta": high_price_delta_259,
        "phase258_high_band_delta_pnl_yen_100": _float((phase258.get("high_price_risk") or {}).get("band_delta_pnl_yen_100")),
        "min_equity_high_price_tolerable_yen": min_equity_acceptable,
        "recommendation": " ".join(recommendation_parts),
    }


def build_report_markdown(result: Mapping[str, Any]) -> str:
    verdict = result.get("verdict") or {}
    summary = result.get("summary") or {}
    lines = [
        "# Phase260A Position Exposure Audit",
        "",
        "Observation-only audit: price-cap vs position-sizing for high-price profit sources.",
        "",
        f"- overlap days: {', '.join(summary.get('trade_overlap_days') or [])}",
        f"- entries analyzed: {summary.get('entry_count')}",
        "",
        "## Verdict",
        "",
        f"- price_cap_is_proxy_for_position_sizing: {verdict.get('price_cap_is_proxy_for_position_sizing')}",
        f"- high_price_edge_but_low_equity_problem: {verdict.get('high_price_edge_but_low_equity_problem')}",
        f"- high_price_edge_and_large_equity_safe: {verdict.get('high_price_edge_and_large_equity_safe')}",
        f"- min_equity_high_price_tolerable_yen: {verdict.get('min_equity_high_price_tolerable_yen')}",
        "",
        "## Recommendation",
        "",
        str(verdict.get("recommendation") or ""),
        "",
        "## Exposure distribution",
        "",
    ]
    for row in result.get("exposure_distribution") or []:
        lines.append(
            f"- {row.get('equity_yen')} yen: median={row.get('median_position_ratio')} "
            f"p95={row.get('p95_position_ratio')} max={row.get('max_position_ratio')}"
        )
    lines.extend(["", "## Feasibility (high-price entries)", ""])
    for row in result.get("feasibility_by_equity") or []:
        lines.append(
            f"- {row.get('equity_yen')} yen: >30%={row.get('high_price_pct_gt_30')} "
            f">50%={row.get('high_price_pct_gt_50')} >100%={row.get('high_price_pct_gt_100')}"
        )
    lines.append("")
    return "\n".join(lines)


def run_position_exposure_audit(
    *,
    repo_root: Path,
    reports_dir: Path,
) -> dict[str, Any]:
    phase257_path = reports_dir / "phase257_trade_validation_by_pattern.csv"
    phase258_path = reports_dir / "phase258_pricecap_off_attribution_summary.json"
    phase259_path = reports_dir / "phase259_price_band_policy_shadow_summary.json"

    phase258 = _load_json(phase258_path)
    phase259 = _load_json(phase259_path)
    overlap_days = resolve_overlap_days(
        reports_dir=reports_dir,
        phase257_path=phase257_path,
        phase258_path=phase258_path,
        phase259_path=phase259_path,
    )

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

    entries = enrich_entries(trades_by_day, overlap_days=overlap_days)
    exposure_distribution = build_exposure_distribution_rows(entries)
    price_band_exposure = build_price_band_exposure_rows(entries)
    feasibility_by_equity = build_feasibility_rows(entries)
    verdict = build_verdict(
        entries=entries,
        feasibility_rows=feasibility_by_equity,
        phase258=phase258,
        phase259=phase259,
    )

    return {
        "phase": "260A-Position-Exposure-Audit",
        "title": "Position exposure audit for high-price profit sources",
        "generated_at": _now_iso(),
        "purpose": "Determine whether high-price edge is price-limit or position-sizing constrained",
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
            "phase257_trade_validation": str(phase257_path),
            "phase258_summary": str(phase258_path),
            "phase259_summary": str(phase259_path),
        },
        "equity_levels_yen": list(EQUITY_LEVELS),
        "position_sizing_model": "fixed_100_shares",
        "position_value_formula": "entry_price * 100",
        "position_ratio_formula": "position_value_100 / equity_yen",
        "summary": {
            "trade_overlap_days": overlap_days,
            "entry_count": len(entries),
            "high_price_entry_count": sum(
                1 for e in entries if _float(e.get("entry_price")) >= HIGH_PRICE_THRESHOLD
            ),
        },
        "exposure_distribution": exposure_distribution,
        "price_band_exposure": price_band_exposure,
        "feasibility_by_equity": feasibility_by_equity,
        "verdict": verdict,
        "phase_context": {
            "phase258_high_band_delta": verdict.get("phase258_high_band_delta_pnl_yen_100"),
            "phase259_allow_high_high_price_delta": verdict.get("phase259_allow_high_high_price_delta"),
        },
        "_entries_sample": entries[:5],
    }


@dataclass
class PositionExposureAudit:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase260a_position_exposure_audit_summary.json",
            "exposure_distribution": self.reports_dir / "phase260a_exposure_distribution.csv",
            "price_band_exposure": self.reports_dir / "phase260a_price_band_exposure.csv",
            "feasibility": self.reports_dir / "phase260a_feasibility_by_equity.csv",
            "report": self.reports_dir / "phase260a_report.md",
        }

    def run(self) -> dict[str, Any]:
        return run_position_exposure_audit(repo_root=self.repo_root, reports_dir=self.reports_dir)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["summary"].parent.mkdir(parents=True, exist_ok=True)
        _write_csv(paths["exposure_distribution"], EXPOSURE_DIST_FIELDS, result.get("exposure_distribution") or [])
        _write_csv(paths["price_band_exposure"], PRICE_BAND_EXPOSURE_FIELDS, result.get("price_band_exposure") or [])
        _write_csv(paths["feasibility"], FEASIBILITY_FIELDS, result.get("feasibility_by_equity") or [])
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths["report"].write_text(build_report_markdown(result), encoding="utf-8")
        return paths
