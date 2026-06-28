"""
Phase567 — Capital requirement optimization study (research only).

Quantifies minimum capital for Phase558 Runtime (fixed 100-share) to reach full performance.
No Runtime changes.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts
from research.phase451_entry_shape_tournament import JST, _now_iso
from research.phase524_live_reentry_guard_and_stop_low_mfe import _latest_live_day
from research.phase561_trailing_shadow_validation import (
    FULL_END,
    FULL_START,
    LIVE_START,
    _load_full_period_accepted,
)
from research.phase566_position_sizing_optimization import (
    HIGH_PRICE_THRESHOLD,
    MIN_LOT,
    _prepare_trades,
    simulate_sizing_policy,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE567_VERDICT = "phase567_capital_requirement_optimization_done"

CAPITAL_LEVELS: tuple[int, ...] = (
    1_000_000,
    1_500_000,
    2_000_000,
    2_500_000,
    3_000_000,
    3_500_000,
    4_000_000,
    4_500_000,
    5_000_000,
    6_000_000,
    8_000_000,
    10_000_000,
)

PHASE567_PRICE_BANDS: tuple[tuple[str, float, Optional[float]], ...] = (
    ("lt_500", 0.0, 500.0),
    ("500_1000", 500.0, 1000.0),
    ("1000_3000", 1000.0, 3000.0),
    ("3000_5000", 3000.0, 5000.0),
    ("5000_10000", 5000.0, 10000.0),
    ("gte_10000", 10000.0, None),
)

SUMMARY_FIELDS = [
    "initial_equity_yen",
    "executed_trades",
    "capital_skip_count",
    "high_price_skip_count",
    "high_price_profit_loss_yen",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "cagr_equivalent_pct",
    "win_rate",
    "realized_profit_rate_pct",
    "capital_utilization_avg",
    "max_position_ratio",
    "final_equity_yen",
    "total_return_pct",
    "profit_recovery_rate_pct",
]

CAPITAL_CURVE_FIELDS = [
    "initial_equity_yen",
    "day",
    "equity_yen",
    "daily_pnl_yen",
    "drawdown_yen",
    "executed_trades_cum",
    "capital_skip_cum",
]

SKIP_ANALYSIS_FIELDS = [
    "initial_equity_yen",
    "skip_count",
    "would_profit_count",
    "would_loss_count",
    "would_profit_pct",
    "would_loss_pct",
    "avg_skipped_pnl_yen_100",
    "avg_skipped_entry_price",
    "high_price_skip_count",
    "skipped_pnl_yen_100_total",
    "top_skip_price_band",
]

PROFIT_RECOVERY_FIELDS = [
    "initial_equity_yen",
    "total_pnl_yen",
    "unlimited_pnl_yen",
    "profit_recovery_rate_pct",
    "capital_skip_count",
    "meets_95pct",
    "meets_98pct",
    "meets_99pct",
]

PRICE_BAND_DEPENDENCY_FIELDS = [
    "price_band",
    "trade_count",
    "executed_trade_count",
    "skipped_trade_count",
    "total_pnl_yen_100",
    "pnl_share_pct",
    "profit_factor",
    "win_rate",
    "avg_entry_price",
    "high_price_band",
]


def _num(v: Any) -> float:
    return _float(v) or 0.0


def phase567_price_band(entry_price: float) -> str:
    if entry_price <= 0:
        return "unknown"
    for label, lo, hi in PHASE567_PRICE_BANDS:
        if hi is None and entry_price >= lo:
            return label
        if hi is not None and lo <= entry_price < hi:
            return label
    return "unknown"


def _period_calendar_days(trades: Sequence[Mapping[str, Any]]) -> int:
    days = sorted({str(t.get("day") or "")[:8] for t in trades if t.get("day")})
    if len(days) < 2:
        return max(len(days), 1)
    d0 = datetime.strptime(days[0], "%Y%m%d")
    d1 = datetime.strptime(days[-1], "%Y%m%d")
    return max((d1 - d0).days + 1, 1)


def _cagr_equivalent(*, initial: float, final: float, calendar_days: int) -> float:
    if initial <= 0 or final <= 0 or calendar_days <= 0:
        return 0.0
    years = calendar_days / 365.0
    if years <= 0:
        return 0.0
    return round(((final / initial) ** (1.0 / years) - 1.0) * 100.0, 4)


def _unlimited_pnl(trades: Sequence[Mapping[str, Any]]) -> float:
    return round(sum(_num(t.get("pnl_yen_100")) for t in trades), 2)


def _min_capital_for(
    rows: Sequence[Mapping[str, Any]],
    *,
    predicate: Any,
) -> Optional[int]:
    for cap in CAPITAL_LEVELS:
        row = next((r for r in rows if int(r.get("initial_equity_yen") or 0) == cap), None)
        if row and predicate(row):
            return cap
    return None


def _skip_analysis_row(sim: Mapping[str, Any]) -> dict[str, Any]:
    equity = int(sim.get("initial_equity_yen") or 0)
    skipped = list(sim.get("_skip_rows") or [])
    pnls = [_num(r.get("pnl_yen_100")) for r in skipped]
    winners = sum(1 for p in pnls if p > 0)
    losers = sum(1 for p in pnls if p < 0)
    prices = [_num(r.get("entry_price")) for r in skipped]
    band_counts: dict[str, int] = {}
    for r in skipped:
        band = phase567_price_band(_num(r.get("entry_price")))
        band_counts[band] = band_counts.get(band, 0) + 1
    top_band = max(band_counts.items(), key=lambda kv: kv[1])[0] if band_counts else ""
    return {
        "initial_equity_yen": equity,
        "skip_count": len(skipped),
        "would_profit_count": winners,
        "would_loss_count": losers,
        "would_profit_pct": round(winners / len(skipped), 4) if skipped else 0.0,
        "would_loss_pct": round(losers / len(skipped), 4) if skipped else 0.0,
        "avg_skipped_pnl_yen_100": round(statistics.mean(pnls), 2) if pnls else 0.0,
        "avg_skipped_entry_price": round(statistics.mean(prices), 2) if prices else 0.0,
        "high_price_skip_count": sum(1 for p in prices if p >= HIGH_PRICE_THRESHOLD),
        "skipped_pnl_yen_100_total": round(sum(pnls), 2),
        "top_skip_price_band": top_band,
    }


def _price_band_dependency(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    total = sum(_num(t.get("pnl_yen_100")) for t in trades) or 1.0
    rows: list[dict[str, Any]] = []
    for label, _, _ in PHASE567_PRICE_BANDS:
        subset = [t for t in trades if phase567_price_band(_num(t.get("entry_price"))) == label]
        pnls = [_num(t.get("pnl_yen_100")) for t in subset]
        pnl_sum = sum(pnls)
        rows.append(
            {
                "price_band": label,
                "trade_count": len(subset),
                "executed_trade_count": len(subset),
                "skipped_trade_count": 0,
                "total_pnl_yen_100": round(pnl_sum, 2),
                "pnl_share_pct": round(pnl_sum / total * 100.0, 2),
                "profit_factor": _pf(pnls),
                "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
                "avg_entry_price": round(statistics.mean(_num(t.get("entry_price")) for t in subset), 2)
                if subset
                else 0.0,
                "high_price_band": label in ("3000_5000", "5000_10000", "gte_10000"),
            }
        )
    return rows


def _build_capital_curve_rows(sim: Mapping[str, Any]) -> list[dict[str, Any]]:
    equity = int(sim.get("initial_equity_yen") or 0)
    daily = list(sim.get("_equity_curve_daily") or [])
    exec_cum = 0
    skip_cum = int(sim.get("capital_skip_count") or 0)
    trade_curve = list(sim.get("_trade_curve") or [])
    skip_by_day: dict[str, int] = {}
    for row in sim.get("_skip_rows") or []:
        day = str(row.get("day") or "")
        skip_by_day[day] = skip_by_day.get(day, 0) + 1

    out: list[dict[str, Any]] = []
    trades_by_day: dict[str, int] = {}
    for tc in trade_curve:
        day = str(tc.get("day") or "")
        trades_by_day[day] = trades_by_day.get(day, 0) + 1

    for row in daily:
        day = str(row.get("day") or "")
        exec_cum += trades_by_day.get(day, 0)
        out.append(
            {
                "initial_equity_yen": equity,
                "day": day,
                "equity_yen": row.get("equity_yen"),
                "daily_pnl_yen": row.get("daily_pnl_yen"),
                "drawdown_yen": row.get("drawdown_yen"),
                "executed_trades_cum": exec_cum,
                "capital_skip_cum": skip_cum,
            }
        )
    return out


def _mandatory_answers(
    *,
    unlimited_pnl: float,
    summary_rows: Sequence[Mapping[str, Any]],
    recovery_rows: Sequence[Mapping[str, Any]],
    price_band_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    pnl_by_cap = {int(r["initial_equity_yen"]): r for r in summary_rows}
    recovery_by_cap = {int(r["initial_equity_yen"]): r for r in recovery_rows}

    min_95 = _min_capital_for(
        summary_rows, predicate=lambda r: _num(r.get("profit_recovery_rate_pct")) >= 95.0
    )
    min_98 = _min_capital_for(
        summary_rows, predicate=lambda r: _num(r.get("profit_recovery_rate_pct")) >= 98.0
    )
    min_99 = _min_capital_for(
        summary_rows, predicate=lambda r: _num(r.get("profit_recovery_rate_pct")) >= 99.0
    )
    min_skip5 = _min_capital_for(
        summary_rows, predicate=lambda r: int(r.get("capital_skip_count") or 0) <= 5
    )
    min_skip1 = _min_capital_for(
        summary_rows, predicate=lambda r: int(r.get("capital_skip_count") or 0) <= 1
    )
    min_skip0 = _min_capital_for(
        summary_rows, predicate=lambda r: int(r.get("capital_skip_count") or 0) == 0
    )

    sweet_spot = _min_capital_for(
        summary_rows,
        predicate=lambda r: _num(r.get("profit_recovery_rate_pct")) >= 99.0
        and int(r.get("capital_skip_count") or 0) <= 2,
    )
    recommended = sweet_spot or min_99 or min_98 or min_skip5 or 5_000_000
    rec_row = pnl_by_cap.get(recommended, {})

    high_price_pnl = sum(
        _num(r.get("total_pnl_yen_100"))
        for r in price_band_rows
        if r.get("high_price_band")
    )
    high_price_dep = round(high_price_pnl / unlimited_pnl * 100.0, 2) if unlimited_pnl else 0.0

    return {
        "1_unlimited_pnl_yen": unlimited_pnl,
        "2_pnl_by_capital": {
            str(cap): {
                "total_pnl_yen": pnl_by_cap.get(cap, {}).get("total_pnl_yen"),
                "profit_factor": pnl_by_cap.get(cap, {}).get("profit_factor"),
                "skip_count": pnl_by_cap.get(cap, {}).get("capital_skip_count"),
            }
            for cap in CAPITAL_LEVELS
        },
        "3_profit_recovery_by_capital": {
            str(cap): recovery_by_cap.get(cap, {}).get("profit_recovery_rate_pct")
            for cap in CAPITAL_LEVELS
        },
        "4_min_capital_95pct_recovery": min_95,
        "5_min_capital_98pct_recovery": min_98,
        "6_min_capital_99pct_recovery": min_99,
        "7_min_capital_skip_le_5": min_skip5,
        "8_min_capital_skip_le_1": min_skip1,
        "9_min_capital_skip_eq_0": min_skip0,
        "10_high_price_profit_dependency_pct": high_price_dep,
        "10_high_price_pnl_yen_100": round(high_price_pnl, 2),
        "11_recommended_operating_capital_yen": recommended,
        "11_recommended_recovery_pct": rec_row.get("profit_recovery_rate_pct"),
        "11_recommended_skip_count": rec_row.get("capital_skip_count"),
        "11_min_capital_98pct": min_98,
        "11_min_capital_skip_zero": min_skip0,
        "12_runtime_change_needed": False,
        "13_next_phase": "phase568_capital_requirement_shadow_monitor",
    }


@dataclass
class Phase567Job:
    repo_root: Path
    period_start: str = FULL_START
    live_start: str = LIVE_START
    period_end: str = FULL_END

    def run(self) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        end = min(self.period_end, _latest_live_day(repo))
        trades = _prepare_trades(
            _load_full_period_accepted(
                repo, full_start=self.period_start, live_start=self.live_start, end=end
            )
        )
        if not trades:
            raise RuntimeError("No Phase558 accepted trades for Phase567")

        unlimited_pnl = _unlimited_pnl(trades)
        calendar_days = _period_calendar_days(trades)

        summary_rows: list[dict[str, Any]] = []
        recovery_rows: list[dict[str, Any]] = []
        skip_rows: list[dict[str, Any]] = []
        capital_curve_rows: list[dict[str, Any]] = []
        sims: list[dict[str, Any]] = []

        for initial in CAPITAL_LEVELS:
            sim = simulate_sizing_policy(trades, initial_equity=initial, policy="fixed_100")
            sims.append(sim)
            skipped = list(sim.get("_skip_rows") or [])
            hp_skipped = [r for r in skipped if _num(r.get("entry_price")) >= HIGH_PRICE_THRESHOLD]
            hp_loss = round(sum(_num(r.get("pnl_yen_100")) for r in hp_skipped), 2)
            recovery_pct = round(_num(sim.get("total_pnl_yen")) / unlimited_pnl * 100.0, 2) if unlimited_pnl else 0.0
            ratios = [_num(tc.get("position_ratio")) for tc in sim.get("_trade_curve") or []]
            row = {
                "initial_equity_yen": initial,
                "executed_trades": sim.get("executed_trades"),
                "capital_skip_count": sim.get("capital_skip_count"),
                "high_price_skip_count": len(hp_skipped),
                "high_price_profit_loss_yen": hp_loss,
                "total_pnl_yen": sim.get("total_pnl_yen"),
                "profit_factor": sim.get("profit_factor"),
                "max_drawdown_yen": sim.get("max_drawdown_yen"),
                "cagr_equivalent_pct": _cagr_equivalent(
                    initial=float(initial),
                    final=_num(sim.get("final_equity_yen")),
                    calendar_days=calendar_days,
                ),
                "win_rate": sim.get("win_rate"),
                "realized_profit_rate_pct": recovery_pct,
                "capital_utilization_avg": sim.get("avg_position_ratio"),
                "max_position_ratio": round(max(ratios), 6) if ratios else 0.0,
                "final_equity_yen": sim.get("final_equity_yen"),
                "total_return_pct": sim.get("total_return_pct"),
                "profit_recovery_rate_pct": recovery_pct,
            }
            summary_rows.append(row)
            recovery_rows.append(
                {
                    "initial_equity_yen": initial,
                    "total_pnl_yen": sim.get("total_pnl_yen"),
                    "unlimited_pnl_yen": unlimited_pnl,
                    "profit_recovery_rate_pct": recovery_pct,
                    "capital_skip_count": sim.get("capital_skip_count"),
                    "meets_95pct": recovery_pct >= 95.0,
                    "meets_98pct": recovery_pct >= 98.0,
                    "meets_99pct": recovery_pct >= 99.0,
                }
            )
            skip_rows.append(_skip_analysis_row(sim))
            capital_curve_rows.extend(_build_capital_curve_rows(sim))

        price_band_rows = _price_band_dependency(trades)
        mandatory = _mandatory_answers(
            unlimited_pnl=unlimited_pnl,
            summary_rows=summary_rows,
            recovery_rows=recovery_rows,
            price_band_rows=price_band_rows,
        )

        return {
            "verdict": PHASE567_VERDICT,
            "generated_at": _now_iso(),
            "period": f"{self.period_start}-{end}",
            "calendar_days": calendar_days,
            "trade_count": len(trades),
            "sizing_policy": "fixed_100",
            "unlimited_pnl_yen": unlimited_pnl,
            "capital_levels": list(CAPITAL_LEVELS),
            "summary": summary_rows,
            "capital_curve": capital_curve_rows,
            "skip_analysis": skip_rows,
            "profit_recovery_curve": recovery_rows,
            "price_band_dependency": price_band_rows,
            "mandatory_answers": mandatory,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root.resolve())
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "summary": reports / "phase567_capital_requirement_summary.csv",
            "capital_curve": reports / "phase567_capital_curve.csv",
            "skip_analysis": reports / "phase567_skip_analysis.csv",
            "profit_recovery": reports / "phase567_profit_recovery_curve.csv",
            "price_band": reports / "phase567_price_band_dependency.csv",
            "report": reports / "phase567_report.json",
            "doc": resolve_kabu_root(self.repo_root) / "docs" / "operations" / "phase567_capital_requirement_optimization.md",
        }
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("summary") or []))
        _write_csv(paths["capital_curve"], CAPITAL_CURVE_FIELDS, list(result.get("capital_curve") or []))
        _write_csv(paths["skip_analysis"], SKIP_ANALYSIS_FIELDS, list(result.get("skip_analysis") or []))
        _write_csv(paths["profit_recovery"], PROFIT_RECOVERY_FIELDS, list(result.get("profit_recovery_curve") or []))
        _write_csv(paths["price_band"], PRICE_BAND_DEPENDENCY_FIELDS, list(result.get("price_band_dependency") or []))
        paths["report"].write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

        ma = result.get("mandatory_answers") or {}
        paths["doc"].parent.mkdir(parents=True, exist_ok=True)
        recovery = ma.get("3_profit_recovery_by_capital") or {}
        lines = [
            "# Phase567 — Capital Requirement Optimization Study",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period')}",
            f"**Trades:** {result.get('trade_count')} (fixed 100-share)",
            f"**Unlimited PnL:** {ma.get('1_unlimited_pnl_yen')} yen",
            "",
            "## Profit recovery curve",
            "",
        ]
        for cap in CAPITAL_LEVELS:
            pct = recovery.get(str(cap))
            if pct is not None:
                lines.append(f"- {cap // 10_000}万円: {pct}%")
        lines.extend(
            [
                "",
                "## Minimum capital thresholds",
                "",
                f"- 95% recovery: {ma.get('4_min_capital_95pct_recovery')} yen",
                f"- 98% recovery: {ma.get('5_min_capital_98pct_recovery')} yen",
                f"- 99% recovery: {ma.get('6_min_capital_99pct_recovery')} yen",
                f"- skip ≤5: {ma.get('7_min_capital_skip_le_5')} yen",
                f"- skip ≤1: {ma.get('8_min_capital_skip_le_1')} yen",
                f"- skip 0: {ma.get('9_min_capital_skip_eq_0')} yen",
                "",
                "## Mandatory answers",
                "",
                f"10. high-price profit dependency: {ma.get('10_high_price_profit_dependency_pct')}%",
                f"11. recommended operating capital: {ma.get('11_recommended_operating_capital_yen')} yen",
                f"12. runtime change needed: {ma.get('12_runtime_change_needed')}",
                f"13. next phase: {ma.get('13_next_phase')}",
            ]
        )
        paths["doc"].write_text("\n".join(lines) + "\n", encoding="utf-8")
        return paths
