"""
Phase384: Capital scaling study (Stack C).

Runs Phase383-style capital simulation across multiple account sizes
to identify the minimum capital needed to reproduce strategy performance.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase377_daily_regime_breakdown import PRIMARY_STACK
from research.phase382_capital_constrained_backtest import (
    EQUITY_CURVE_FIELDS,
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
    load_session_capital_backtest_trades,
)
from research.phase383_realistic_credit_sizing_backtest import (
    build_event_timeline,
    compute_buying_power,
    compute_requested_shares,
    simulate_scenario as simulate_constrained_scenario,
)

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_MIN_DAY = "20260529"
DEFAULT_MAX_DAY = "20260612"

CAPITAL_LEVELS: tuple[int, ...] = (
    500_000,
    1_000_000,
    1_500_000,
    2_000_000,
    3_000_000,
    5_000_000,
)

SCENARIO_LETTERS: tuple[str, ...] = ("A", "B", "C", "D", "E", "F")

SCENARIO_SPECS: dict[str, dict[str, Any]] = {
    "A": {
        "label": "Cash 100 shares fixed",
        "leverage_limit": 1.0,
        "sizing": "fixed_100_only",
    },
    "B": {
        "label": "Credit 2x 100 shares fixed",
        "leverage_limit": 2.0,
        "sizing": "fixed_100_only",
    },
    "C": {
        "label": "Credit 3x 100 shares fixed",
        "leverage_limit": 3.0,
        "sizing": "fixed_100_only",
    },
    "D": {
        "label": "Credit 2x equal 3 slots",
        "leverage_limit": 2.0,
        "sizing": "equal_3slots",
    },
    "E": {
        "label": "Credit 3x equal 3 slots",
        "leverage_limit": 3.0,
        "sizing": "equal_3slots",
    },
    "F": {
        "label": "Unconstrained reference 100 shares",
        "unconstrained": True,
    },
}

METRIC_FIELDS: tuple[str, ...] = (
    "accepted_trade_count",
    "rejected_trade_count",
    "reject_rate",
    "total_pnl_yen",
    "return_pct",
    "profit_factor",
    "win_rate",
    "max_drawdown_yen",
    "max_drawdown_pct",
    "min_equity",
    "min_maintenance_ratio",
    "force_exit_count",
    "maintenance_warning_count",
    "maintenance_stop_count",
)

ACCEPTED_RATE_THRESHOLDS: tuple[float, ...] = (0.25, 0.50, 0.75)
PNL_RECOVERY_THRESHOLDS: tuple[float, ...] = (0.50, 0.75, 0.90)
MIN_MAINT_SAFETY: float = 0.50


def scenario_key(letter: str) -> str:
    spec = SCENARIO_SPECS[letter]
    if letter == "A":
        return "A_cash_100_fixed"
    if letter == "B":
        return "B_credit2_100_fixed"
    if letter == "C":
        return "C_credit3_100_fixed"
    if letter == "D":
        return "D_credit2_equal_3slots"
    if letter == "E":
        return "E_credit3_equal_3slots"
    return "F_unconstrained_reference"


def simulate_unconstrained(
    trades: Sequence[Mapping[str, Any]],
    *,
    initial_equity: float,
) -> dict[str, Any]:
    scenario_id = scenario_key("F")
    realized_pnl = 0.0
    realized_pnls: list[float] = []
    trade_log: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    peak_equity = initial_equity
    min_equity = initial_equity
    wins = 0

    events = build_event_timeline(trades)
    open_count = 0
    for dt, _, kind, trade in events:
        ts = dt.isoformat()
        day = _day_from_ts(ts)
        if kind == "entry":
            open_count += 1
            eq = initial_equity + realized_pnl
            trade_log.append(
                {
                    "scenario": scenario_id,
                    "day": day,
                    "symbol": trade.get("symbol"),
                    "entry_time": trade.get("entry_time"),
                    "exit_time": "",
                    "entry_price": trade.get("entry_price"),
                    "exit_price": "",
                    "shares": LOT_SIZE,
                    "position_value": round(float(_float(trade.get("entry_price")) or 0.0) * LOT_SIZE, 2),
                    "pnl_yen": "",
                    "pnl_pct": "",
                    "exit_reason": "",
                    "accepted_or_rejected": "accepted",
                    "reject_reason": "",
                    "equity_before": round(eq, 2),
                    "equity_after": round(eq, 2),
                    "maintenance_ratio_before": "",
                    "maintenance_ratio_after": "",
                    "gross_position_value_before": "",
                    "gross_position_value_after": "",
                }
            )
        else:
            if open_count <= 0:
                continue
            open_count = max(0, open_count - 1)
            pnl = _trade_pnl_yen(trade, LOT_SIZE)
            realized_pnl += pnl
            realized_pnls.append(pnl)
            if pnl > 0:
                wins += 1
            eq = initial_equity + realized_pnl
            peak_equity = max(peak_equity, eq)
            min_equity = min(min_equity, eq)
            trade_log.append(
                {
                    "scenario": scenario_id,
                    "day": day,
                    "symbol": trade.get("symbol"),
                    "entry_time": trade.get("entry_time"),
                    "exit_time": trade.get("exit_time"),
                    "entry_price": trade.get("entry_price"),
                    "exit_price": trade.get("exit_price"),
                    "shares": LOT_SIZE,
                    "position_value": round(float(_float(trade.get("entry_price")) or 0.0) * LOT_SIZE, 2),
                    "pnl_yen": pnl,
                    "pnl_pct": trade.get("pnl_pct"),
                    "exit_reason": trade.get("exit_reason_canonical") or trade.get("exit_reason"),
                    "accepted_or_rejected": "accepted",
                    "reject_reason": "",
                    "equity_before": round(eq - pnl, 2),
                    "equity_after": round(eq, 2),
                    "maintenance_ratio_before": "",
                    "maintenance_ratio_after": "",
                    "gross_position_value_before": "",
                    "gross_position_value_after": "",
                }
            )
            dd_yen = round(peak_equity - eq, 2)
            dd_pct = round(dd_yen / peak_equity * 100.0, 4) if peak_equity > 0 else 0.0
            equity_curve.append(
                {
                    "timestamp_or_day": ts,
                    "initial_equity": int(initial_equity),
                    "scenario": scenario_id,
                    "equity": round(eq, 2),
                    "drawdown_yen": dd_yen,
                    "drawdown_pct": dd_pct,
                    "gross_position_value": 0.0,
                    "maintenance_ratio": "",
                }
            )

    accepted = len([r for r in trade_log if r.get("pnl_yen") not in ("", None)])
    total_attempts = len(events) // 2 if events else 0
    final_equity = round(initial_equity + realized_pnl, 2)
    total_return_yen = round(realized_pnl, 2)
    total_return_pct = round(total_return_yen / initial_equity * 100.0, 4) if initial_equity else 0.0
    max_dd_yen = round(peak_equity - min_equity, 2)
    max_dd_pct = round(max_dd_yen / peak_equity * 100.0, 4) if peak_equity > 0 else 0.0

    return {
        "scenario_letter": "F",
        "scenario_id": scenario_id,
        "label": SCENARIO_SPECS["F"]["label"],
        "initial_equity": initial_equity,
        "final_equity": final_equity,
        "total_return_yen": total_return_yen,
        "total_return_pct": total_return_pct,
        "realized_pnl": round(realized_pnl, 2),
        "max_drawdown_yen": max_dd_yen,
        "max_drawdown_pct": max_dd_pct,
        "min_equity": round(min_equity, 2),
        "equity_floor_breached": False,
        "accepted_trade_count": accepted,
        "rejected_trade_count": max(0, total_attempts - accepted),
        "reject_rate": 0.0,
        "capital_block_count": 0,
        "position_cap_reject_count": 0,
        "insufficient_buying_power_count": 0,
        "maintenance_warning_count": 0,
        "maintenance_stop_count": 0,
        "force_exit_count": 0,
        "min_maintenance_ratio": None,
        "avg_maintenance_ratio": None,
        "max_gross_position_value": 0.0,
        "profit_factor": _pf(realized_pnls),
        "win_rate": round(wins / accepted, 4) if accepted else 0.0,
        "total_pnl_yen": round(realized_pnl, 2),
        "return_pct": total_return_pct,
        "_trade_log": trade_log,
        "_equity_curve": equity_curve,
    }


def _normalize_result(result: Mapping[str, Any], *, letter: str, initial_equity: float) -> dict[str, Any]:
    accepted = int(result.get("accepted_trade_count") or 0)
    rejected = int(result.get("rejected_trade_count") or 0)
    total = accepted + rejected
    reject_rate = round(rejected / total, 4) if total else 0.0
    accepted_rate = round(accepted / total, 4) if total else 0.0
    min_mr = result.get("min_maintenance_ratio")
    return {
        "initial_equity": int(initial_equity),
        "scenario_letter": letter,
        "scenario_id": result.get("scenario_id") or scenario_key(letter),
        "label": result.get("label") or SCENARIO_SPECS[letter].get("label"),
        "leverage_limit": SCENARIO_SPECS[letter].get("leverage_limit"),
        "accepted_trade_count": accepted,
        "rejected_trade_count": rejected,
        "reject_rate": reject_rate,
        "accepted_rate": accepted_rate,
        "total_pnl_yen": round(float(result.get("realized_pnl") or result.get("total_pnl_yen") or 0.0), 2),
        "return_pct": round(float(result.get("total_return_pct") or result.get("return_pct") or 0.0), 4),
        "profit_factor": result.get("profit_factor"),
        "win_rate": result.get("win_rate"),
        "max_drawdown_yen": result.get("max_drawdown_yen"),
        "max_drawdown_pct": result.get("max_drawdown_pct"),
        "min_equity": result.get("min_equity"),
        "final_equity": result.get("final_equity"),
        "min_maintenance_ratio": min_mr,
        "min_maintenance_above_0p5": min_mr is None or float(min_mr) > MIN_MAINT_SAFETY,
        "force_exit_count": int(result.get("force_exit_count") or 0),
        "maintenance_warning_count": int(result.get("maintenance_warning_count") or 0),
        "maintenance_stop_count": int(result.get("maintenance_stop_count") or 0),
        "equity_floor_breached": bool(result.get("equity_floor_breached")),
        "_trade_log": list(result.get("_trade_log") or []),
        "_equity_curve": list(result.get("_equity_curve") or []),
    }


def _scenario_worker(job: dict[str, Any]) -> dict[str, Any]:
    letter = str(job["scenario_letter"])
    trades = job["trades"]
    initial_equity = float(job["initial_equity"])
    equity_floor = float(job["equity_floor"])

    if letter == "F":
        raw = simulate_unconstrained(trades, initial_equity=initial_equity)
    else:
        sid = scenario_key(letter)
        raw = simulate_constrained_scenario(
            trades,
            scenario_id=sid,
            spec=SCENARIO_SPECS[letter],
            initial_equity=initial_equity,
            equity_floor=equity_floor,
        )
        for pt in raw.get("_equity_curve") or []:
            pt["initial_equity"] = int(initial_equity)

    return _normalize_result(raw, letter=letter, initial_equity=initial_equity)


def _first_capital_meeting(
    rows: Sequence[Mapping[str, Any]],
    *,
    predicate,
) -> Optional[int]:
    for capital in CAPITAL_LEVELS:
        subset = [r for r in rows if int(r.get("initial_equity") or 0) == capital]
        if any(predicate(r) for r in subset):
            return capital
    return None


def _first_capital_for_scenario_metric(
    rows: Sequence[Mapping[str, Any]],
    *,
    scenario_letter: str,
    metric: str,
    threshold: float,
    comparator: str = "ge",
) -> Optional[int]:
    for capital in CAPITAL_LEVELS:
        row = next(
            (r for r in rows if int(r.get("initial_equity") or 0) == capital and r.get("scenario_letter") == scenario_letter),
            None,
        )
        if not row:
            continue
        value = float(row.get(metric) or 0.0)
        if comparator == "ge" and value >= threshold:
            return capital
        if comparator == "gt" and value > threshold:
            return capital
    return None


def build_scaling_analysis(rows: Sequence[Mapping[str, Any]], *, unconstrained_pnl: float) -> dict[str, Any]:
    total_trades = max(
        (int(r.get("accepted_trade_count") or 0) + int(r.get("rejected_trade_count") or 0) for r in rows),
        default=0,
    )

    accepted_rate_by_capital: dict[str, dict[str, Any]] = {}
    for capital in CAPITAL_LEVELS:
        cap_rows = [r for r in rows if int(r.get("initial_equity") or 0) == capital and r.get("scenario_letter") != "F"]
        accepted_rate_by_capital[str(capital)] = {
            r["scenario_letter"]: {
                "accepted_rate": r.get("accepted_rate"),
                "accepted_trade_count": r.get("accepted_trade_count"),
                "total_pnl_yen": r.get("total_pnl_yen"),
                "profit_factor": r.get("profit_factor"),
            }
            for r in cap_rows
        }

    accepted_threshold_hits: dict[str, dict[str, Optional[int]]] = {}
    for thr in ACCEPTED_RATE_THRESHOLDS:
        key = f"{int(thr * 100)}pct"
        accepted_threshold_hits[key] = {}
        for letter in ("A", "B", "C", "D", "E"):
            accepted_threshold_hits[key][letter] = _first_capital_for_scenario_metric(
                rows, scenario_letter=letter, metric="accepted_rate", threshold=thr
            )

    pnl_recovery_hits: dict[str, dict[str, Optional[int]]] = {}
    if unconstrained_pnl > 0:
        for thr in PNL_RECOVERY_THRESHOLDS:
            key = f"{int(thr * 100)}pct"
            target = unconstrained_pnl * thr
            pnl_recovery_hits[key] = {}
            for letter in ("A", "B", "C", "D", "E"):
                pnl_recovery_hits[key][letter] = _first_capital_for_scenario_metric(
                    rows, scenario_letter=letter, metric="total_pnl_yen", threshold=target
                )

    min_maint_safe: dict[str, list[str]] = {}
    for capital in CAPITAL_LEVELS:
        safe = [
            str(r.get("scenario_letter"))
            for r in rows
            if int(r.get("initial_equity") or 0) == capital
            and r.get("scenario_letter") in ("A", "B", "C", "D", "E")
            and r.get("min_maintenance_above_0p5")
        ]
        min_maint_safe[str(capital)] = safe

    credit3_reasonable = _first_capital_meeting(
        rows,
        predicate=lambda r: (
            r.get("scenario_letter") == "C"
            and float(r.get("total_pnl_yen") or 0.0) > 0
            and bool(r.get("min_maintenance_above_0p5"))
            and int(r.get("force_exit_count") or 0) == 0
            and float(r.get("accepted_rate") or 0.0) >= 0.25
        ),
    )

    constrained_rows = [r for r in rows if r.get("scenario_letter") != "F"]
    best_by_capital: dict[str, dict[str, Any]] = {}
    for capital in CAPITAL_LEVELS:
        cap_rows = [r for r in constrained_rows if int(r.get("initial_equity") or 0) == capital]
        if not cap_rows:
            continue
        viable = [
            r
            for r in cap_rows
            if not r.get("equity_floor_breached")
            and int(r.get("force_exit_count") or 0) == 0
            and bool(r.get("min_maintenance_above_0p5") or r.get("scenario_letter") == "A")
        ]
        pool = viable or cap_rows
        best = max(pool, key=lambda r: float(r.get("total_pnl_yen") or 0.0))
        best_by_capital[str(capital)] = {
            "scenario_letter": best.get("scenario_letter"),
            "scenario_id": best.get("scenario_id"),
            "total_pnl_yen": best.get("total_pnl_yen"),
            "accepted_rate": best.get("accepted_rate"),
            "min_maintenance_ratio": best.get("min_maintenance_ratio"),
        }

    return {
        "total_trade_attempts": total_trades,
        "unconstrained_pnl_yen": round(unconstrained_pnl, 2),
        "accepted_rate_by_capital": accepted_rate_by_capital,
        "accepted_rate_threshold_capital": accepted_threshold_hits,
        "pnl_recovery_threshold_capital": pnl_recovery_hits,
        "min_maintenance_above_0p5_by_capital": min_maint_safe,
        "credit3_reasonable_minimum_capital": credit3_reasonable,
        "best_scenario_by_capital": best_by_capital,
    }


def build_recommendations(
    rows: Sequence[Mapping[str, Any]],
    analysis: Mapping[str, Any],
) -> dict[str, Any]:
    unconstrained_pnl = float(analysis.get("unconstrained_pnl_yen") or 0.0)
    f_row = next((r for r in rows if r.get("scenario_letter") == "F" and int(r.get("initial_equity") or 0) == 500_000), {})
    if not f_row:
        f_row = next((r for r in rows if r.get("scenario_letter") == "F"), {})

    def _cap_row(capital: int, letter: str) -> Optional[dict[str, Any]]:
        return next(
            (r for r in rows if int(r.get("initial_equity") or 0) == capital and r.get("scenario_letter") == letter),
            None,
        )

    c_500 = _cap_row(500_000, "C")
    b_1m = _cap_row(1_000_000, "B")
    c_1m = _cap_row(1_000_000, "C")
    c_2m = _cap_row(2_000_000, "C")
    c_5m = _cap_row(5_000_000, "C")

    is_500k_insufficient = True
    if c_500:
        recovery_50 = float(c_500.get("total_pnl_yen") or 0.0) >= unconstrained_pnl * 0.5
        is_500k_insufficient = float(c_500.get("accepted_rate") or 0.0) < 0.25 or not recovery_50

    is_1m_sufficient = False
    if c_1m and unconstrained_pnl > 0:
        is_1m_sufficient = (
            float(c_1m.get("accepted_rate") or 0.0) >= 0.50
            and float(c_1m.get("total_pnl_yen") or 0.0) >= unconstrained_pnl * 0.75
            and bool(c_1m.get("min_maintenance_above_0p5"))
        )

    needs_2m = _first_capital_for_scenario_metric(
        rows, scenario_letter="C", metric="accepted_rate", threshold=0.75
    )
    is_2m_required = needs_2m is not None and needs_2m <= 2_000_000 and (not is_1m_sufficient)

    c_5m_recovery = float(c_5m.get("total_pnl_yen") or 0.0) if c_5m else 0.0
    is_5m_saturated = unconstrained_pnl > 0 and c_5m_recovery >= unconstrained_pnl * 0.95

    min_capital_25 = analysis.get("accepted_rate_threshold_capital", {}).get("25pct", {})
    min_capital_50 = analysis.get("accepted_rate_threshold_capital", {}).get("50pct", {})
    min_capital_pnl_75 = analysis.get("pnl_recovery_threshold_capital", {}).get("75pct", {})

    recommended_minimum = None
    for letter in ("B", "C", "A"):
        cap = min_capital_25.get(letter)
        if cap:
            recommended_minimum = cap
            break
    if recommended_minimum is None:
        recommended_minimum = 1_000_000

    recommended_operating = None
    for capital in CAPITAL_LEVELS:
        cap_rows = [r for r in rows if int(r.get("initial_equity") or 0) == capital and r.get("scenario_letter") in ("B", "C")]
        if not cap_rows:
            continue
        viable = [
            r
            for r in cap_rows
            if bool(r.get("min_maintenance_above_0p5"))
            and int(r.get("force_exit_count") or 0) == 0
            and float(r.get("accepted_rate") or 0.0) >= 0.50
            and (unconstrained_pnl <= 0 or float(r.get("total_pnl_yen") or 0.0) >= unconstrained_pnl * 0.75)
        ]
        if viable:
            recommended_operating = capital
            break
    if recommended_operating is None:
        for capital in (2_000_000, 1_500_000, 1_000_000, 3_000_000):
            cap_rows = [r for r in rows if int(r.get("initial_equity") or 0) == capital and r.get("scenario_letter") in ("B", "C")]
            if cap_rows:
                best = max(cap_rows, key=lambda r: float(r.get("total_pnl_yen") or 0.0))
                if float(best.get("accepted_rate") or 0.0) >= 0.25:
                    recommended_operating = capital
                    break
    if recommended_operating is None:
        recommended_operating = 2_000_000

    recommended_leverage = "credit2"
    if c_1m and b_1m:
        if (
            float(c_1m.get("total_pnl_yen") or 0.0) > float(b_1m.get("total_pnl_yen") or 0.0) * 1.1
            and bool(c_1m.get("min_maintenance_above_0p5"))
            and int(c_1m.get("force_exit_count") or 0) == 0
        ):
            recommended_leverage = "credit3"
    if _cap_row(int(recommended_operating), "B") and not (_cap_row(int(recommended_operating), "C") and recommended_leverage == "credit3"):
        recommended_leverage = "credit2"

    return {
        "is_500k_insufficient": is_500k_insufficient,
        "is_1m_sufficient": is_1m_sufficient,
        "is_2m_required": is_2m_required,
        "is_5m_saturated": is_5m_saturated,
        "recommended_minimum_capital": recommended_minimum,
        "recommended_operating_capital": recommended_operating,
        "recommended_leverage": recommended_leverage,
        "first_capital_accepted_rate_25pct": min_capital_25,
        "first_capital_accepted_rate_50pct": min_capital_50,
        "first_capital_pnl_recovery_75pct": min_capital_pnl_75,
        "credit3_reasonable_minimum_capital": analysis.get("credit3_reasonable_minimum_capital"),
    }


def build_report(summary: Mapping[str, Any]) -> str:
    rec = summary.get("recommendations") or {}
    analysis = summary.get("analysis") or {}
    pop = summary.get("population") or {}
    rows = list(summary.get("by_scenario") or [])
    unconstrained_pnl = float(analysis.get("unconstrained_pnl_yen") or 0.0)

    lines = [
        "# Phase384 Capital Scaling Study",
        "",
        f"**期間:** {pop.get('min_day')}–{pop.get('max_day')}",
        f"**元本レベル:** {', '.join(f'{c:,}' for c in CAPITAL_LEVELS)}円",
        f"**制約なし参考PnL:** {unconstrained_pnl:,.0f}円",
        "",
        "## 判定",
        "",
        f"- **50万円は不足か:** {'はい' if rec.get('is_500k_insufficient') else 'いいえ'}",
        f"- **100万円で十分か:** {'はい' if rec.get('is_1m_sufficient') else 'いいえ'}",
        f"- **200万円必要か:** {'はい' if rec.get('is_2m_required') else 'いいえ'}",
        f"- **500万円で飽和するか:** {'はい' if rec.get('is_5m_saturated') else 'いいえ'}",
        f"- **推奨最低元本:** {rec.get('recommended_minimum_capital'):,}円",
        f"- **推奨運用元本:** {rec.get('recommended_operating_capital'):,}円",
        f"- **推奨レバレッジ:** {rec.get('recommended_leverage')}",
        "",
        "## accepted率 閾値到達元本",
        "",
    ]
    for thr_key, mapping in (analysis.get("accepted_rate_threshold_capital") or {}).items():
        parts = [f"{k}={v:,}" if v else f"{k}=未到達" for k, v in (mapping or {}).items()]
        lines.append(f"- {thr_key}: {', '.join(parts)}")
    lines.extend(["", "## PnL再現率 閾値到達元本", ""])
    for thr_key, mapping in (analysis.get("pnl_recovery_threshold_capital") or {}).items():
        parts = [f"{k}={v:,}" if v else f"{k}=未到達" for k, v in (mapping or {}).items()]
        lines.append(f"- {thr_key}: {', '.join(parts)}")
    lines.extend(["", "## min_maintenance_ratio > 0.5 を維持できる組み合わせ", ""])
    for capital, letters in (analysis.get("min_maintenance_above_0p5_by_capital") or {}).items():
        lines.append(f"- {int(capital):,}円: {letters or 'なし'}")
    lines.extend(
        [
            "",
            "## 元本別ベスト（制約あり）",
            "",
            "| 元本 | シナリオ | PnL | accepted率 | min_maint |",
            "|---:|---|---:|---:|---:|",
        ]
    )
    for capital, info in (analysis.get("best_scenario_by_capital") or {}).items():
        lines.append(
            f"| {int(capital):,} | {info.get('scenario_letter')} | {info.get('total_pnl_yen')} | "
            f"{round(float(info.get('accepted_rate') or 0)*100,1)}% | {info.get('min_maintenance_ratio')} |"
        )
    lines.extend(["", "## 全結果", "", "| 元本 | シナリオ | PnL | return% | accepted | rejected | accepted率 | PF | max_dd | min_maint |", "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for r in sorted(rows, key=lambda x: (int(x.get("initial_equity") or 0), str(x.get("scenario_letter") or ""))):
        lines.append(
            f"| {r.get('initial_equity')} | {r.get('scenario_letter')} | {r.get('total_pnl_yen')} | {r.get('return_pct')} | "
            f"{r.get('accepted_trade_count')} | {r.get('rejected_trade_count')} | "
            f"{round(float(r.get('accepted_rate') or 0)*100,1)}% | {r.get('profit_factor')} | "
            f"{r.get('max_drawdown_yen')} | {r.get('min_maintenance_ratio')} |"
        )
    lines.extend(["", "## 禁止事項", "", "- ENTRY/EXIT/Universe/Discord/canonical 変更なし", "- capital simulationのみ", ""])
    return "\n".join(lines) + "\n"


@dataclass
class Phase384CapitalScalingStudy:
    reports_dir: Path
    min_day: str = DEFAULT_MIN_DAY
    max_day: Optional[str] = DEFAULT_MAX_DAY
    capital_levels: Sequence[int] = field(default_factory=lambda: CAPITAL_LEVELS)
    all_trades: list[dict[str, Any]] = field(default_factory=list)
    excluded_trades: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase384_capital_scaling_summary.json",
            "by_capital": self.reports_dir / "phase384_capital_scaling_by_capital.csv",
            "by_scenario": self.reports_dir / "phase384_capital_scaling_by_scenario.csv",
            "recommendation": self.reports_dir / "phase384_capital_scaling_recommendation.md",
            "equity_curves": self.reports_dir / "phase384_capital_scaling_equity_curves.csv",
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

        jobs: list[dict[str, Any]] = []
        for capital in self.capital_levels:
            equity_floor = capital * 0.5
            for letter in SCENARIO_LETTERS:
                jobs.append(
                    {
                        "scenario_letter": letter,
                        "trades": trades,
                        "initial_equity": float(capital),
                        "equity_floor": equity_floor,
                    }
                )

        raw_results: list[dict[str, Any]] = []
        if parallel and len(jobs) > 1:
            from concurrent.futures import ProcessPoolExecutor, as_completed

            with ProcessPoolExecutor(max_workers=max(1, max_workers)) as pool:
                futures = {pool.submit(_scenario_worker, job): job for job in jobs}
                for fut in as_completed(futures):
                    raw_results.append(fut.result())
        else:
            for job in jobs:
                raw_results.append(_scenario_worker(job))

        raw_results.sort(key=lambda r: (int(r.get("initial_equity") or 0), str(r.get("scenario_letter") or "")))

        f_ref = next((r for r in raw_results if r.get("scenario_letter") == "F"), {})
        unconstrained_pnl = float(f_ref.get("total_pnl_yen") or 0.0)
        analysis = build_scaling_analysis(raw_results, unconstrained_pnl=unconstrained_pnl)
        recommendations = build_recommendations(raw_results, analysis)

        by_capital_rows: list[dict[str, Any]] = []
        for capital in self.capital_levels:
            cap_rows = [r for r in raw_results if int(r.get("initial_equity") or 0) == capital]
            f_row = next((r for r in cap_rows if r.get("scenario_letter") == "F"), {})
            constrained = [r for r in cap_rows if r.get("scenario_letter") != "F"]
            best = max(constrained, key=lambda r: float(r.get("total_pnl_yen") or 0.0), default={})
            row: dict[str, Any] = {
                "initial_equity": capital,
                "unconstrained_pnl_yen": round(unconstrained_pnl, 2),
                "unconstrained_accepted_rate": f_row.get("accepted_rate"),
                "best_scenario_letter": best.get("scenario_letter"),
                "best_total_pnl_yen": best.get("total_pnl_yen"),
                "best_accepted_rate": best.get("accepted_rate"),
                "best_profit_factor": best.get("profit_factor"),
                "best_min_maintenance_ratio": best.get("min_maintenance_ratio"),
                "pnl_vs_unconstrained_pct": round(float(best.get("total_pnl_yen") or 0.0) / unconstrained_pnl * 100.0, 2)
                if unconstrained_pnl
                else 0.0,
            }
            for letter in ("A", "B", "C", "D", "E"):
                srow = next((r for r in cap_rows if r.get("scenario_letter") == letter), {})
                row[f"{letter}_accepted_rate"] = srow.get("accepted_rate")
                row[f"{letter}_total_pnl_yen"] = srow.get("total_pnl_yen")
                row[f"{letter}_profit_factor"] = srow.get("profit_factor")
                row[f"{letter}_min_maintenance_ratio"] = srow.get("min_maintenance_ratio")
            by_capital_rows.append(row)

        public_rows = [{k: v for k, v in r.items() if not str(k).startswith("_")} for r in raw_results]

        return {
            "phase": 384,
            "title": "Capital scaling study",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "stack_id": PRIMARY_STACK,
            "hard_stop_pct": HARD_STOP_PCT,
            "capital_levels": list(self.capital_levels),
            "maintenance_thresholds": {
                "warning": MAINT_WARNING,
                "stop_new_entry": MAINT_STOP_ENTRY,
                "force_exit": MAINT_FORCE_EXIT,
            },
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
            "analysis": analysis,
            "recommendations": recommendations,
            "by_capital": by_capital_rows,
            "by_scenario": public_rows,
            "wall_runtime_sec": round(wall_runtime_sec, 2),
            "_raw_results": raw_results,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        payload = {k: v for k, v in result.items() if not str(k).startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        by_capital_fields = [
            "initial_equity",
            "unconstrained_pnl_yen",
            "unconstrained_accepted_rate",
            "best_scenario_letter",
            "best_total_pnl_yen",
            "best_accepted_rate",
            "best_profit_factor",
            "best_min_maintenance_ratio",
            "pnl_vs_unconstrained_pct",
            "A_accepted_rate",
            "A_total_pnl_yen",
            "A_profit_factor",
            "A_min_maintenance_ratio",
            "B_accepted_rate",
            "B_total_pnl_yen",
            "B_profit_factor",
            "B_min_maintenance_ratio",
            "C_accepted_rate",
            "C_total_pnl_yen",
            "C_profit_factor",
            "C_min_maintenance_ratio",
            "D_accepted_rate",
            "D_total_pnl_yen",
            "D_profit_factor",
            "D_min_maintenance_ratio",
            "E_accepted_rate",
            "E_total_pnl_yen",
            "E_profit_factor",
            "E_min_maintenance_ratio",
        ]
        _write_csv(paths["by_capital"], list(result.get("by_capital") or []), by_capital_fields)

        by_scenario_fields = [
            "initial_equity",
            "scenario_letter",
            "scenario_id",
            "label",
            "leverage_limit",
            "accepted_trade_count",
            "rejected_trade_count",
            "reject_rate",
            "accepted_rate",
            "total_pnl_yen",
            "return_pct",
            "profit_factor",
            "win_rate",
            "max_drawdown_yen",
            "max_drawdown_pct",
            "min_equity",
            "final_equity",
            "min_maintenance_ratio",
            "min_maintenance_above_0p5",
            "force_exit_count",
            "maintenance_warning_count",
            "maintenance_stop_count",
            "equity_floor_breached",
        ]
        _write_csv(paths["by_scenario"], list(result.get("by_scenario") or []), by_scenario_fields)

        equity_fields = [
            "timestamp_or_day",
            "initial_equity",
            "scenario",
            "equity",
            "drawdown_yen",
            "drawdown_pct",
            "gross_position_value",
            "maintenance_ratio",
        ]
        equity_rows: list[dict[str, Any]] = []
        for sr in result.get("_raw_results") or []:
            equity_rows.extend(sr.get("_equity_curve") or [])
        _write_csv(paths["equity_curves"], equity_rows, equity_fields)

        paths["recommendation"].write_text(build_report(payload), encoding="utf-8")
        return paths


__all__ = [
    "CAPITAL_LEVELS",
    "Phase384CapitalScalingStudy",
    "SCENARIO_SPECS",
    "build_scaling_analysis",
    "simulate_unconstrained",
]
