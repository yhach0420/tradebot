"""
Phase377: Daily regime breakdown from Phase376 production daily PnL outputs.

Aggregation only — reads phase376 CSV/JSON; no ENTRY/EXIT changes.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

PRIMARY_STACK = "C_phase355_plus_phase364"
STACK_A = "A_baseline_no_guard"
STACK_B = "B_phase355_only"
STACK_C = PRIMARY_STACK
STACK_VARIANTS = (STACK_A, STACK_B, STACK_C)

PERIOD_A_ID = "period_a_20260518_20260527"
PERIOD_B_ID = "period_b_20260528_20260612"
PERIOD_A_START = "20260518"
PERIOD_A_END = "20260527"
PERIOD_B_START = "20260528"
PERIOD_B_END = "20260612"

PHASE376_DAILY_CSV = "phase376_production_daily_pnl.csv"
PHASE376_EQUITY_CSV = "phase376_production_equity_curve.csv"
PHASE376_SUMMARY_JSON = "phase376_production_daily_pnl_summary.json"

BY_DAY_FIELDS = [
    "day",
    "period_id",
    "stack_id",
    "daily_pnl_yen_100",
    "cumulative_pnl_yen_100",
    "drawdown_yen_100",
    "running_peak_yen_100",
]

BY_PERIOD_FIELDS = [
    "period_id",
    "period_start",
    "period_end",
    "stack_id",
    "day_count",
    "trade_count",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
    "stop_hit_count",
    "low_mfe_stop_hit_count",
    "trailing_mfe_exit_count",
    "dynamic40_pnl_yen_100",
    "core10_pnl_yen_100",
    "am_pnl_yen_100",
    "pm_pnl_yen_100",
    "avg_daily_pnl_yen_100",
    "median_daily_pnl_yen_100",
    "max_daily_profit",
    "max_daily_loss",
    "max_drawdown_yen_100",
    "max_drawdown_end_day",
]

COMPARISON_DELTA_FIELDS = [
    "period_id",
    "comparison",
    "pnl_delta",
    "pf_delta",
    "stop_hit_delta",
    "trade_count_delta",
]


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _int(val: Any) -> int:
    try:
        if val is None or val == "":
            return 0
        return int(float(val))
    except (TypeError, ValueError):
        return 0


def _pf_from_gross(gross_profit: float, gross_loss: float) -> Optional[float]:
    if gross_loss <= 0:
        return None if gross_profit <= 0 else float("inf")
    return round(gross_profit / gross_loss, 4)


def _gross_profit_loss_from_day(
    total_pnl: Optional[float], profit_factor: Optional[float]
) -> tuple[Optional[float], Optional[float]]:
    if total_pnl is None:
        return None, None
    t = float(total_pnl)
    if abs(t) < 1e-9:
        return 0.0, 0.0
    pf = profit_factor
    if pf is None:
        return (t, 0.0) if t > 0 else (0.0, abs(t))
    if pf == float("inf"):
        return t, 0.0
    if pf <= 0:
        return 0.0, abs(t)
    if abs(float(pf) - 1.0) < 1e-9:
        return (max(t, 0.0), abs(min(t, 0.0)))
    denom = float(pf) - 1.0
    if abs(denom) < 1e-9:
        return (max(t, 0.0), abs(min(t, 0.0)))
    gross_loss = t / denom
    if gross_loss < 0:
        gross_loss = abs(gross_loss)
    gross_profit = float(pf) * gross_loss
    return gross_profit, gross_loss


def _period_id_for_day(day: str) -> Optional[str]:
    if PERIOD_A_START <= day <= PERIOD_A_END:
        return PERIOD_A_ID
    if PERIOD_B_START <= day <= PERIOD_B_END:
        return PERIOD_B_ID
    return None


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_phase376_inputs(reports_dir: Path) -> dict[str, Any]:
    daily_path = reports_dir / PHASE376_DAILY_CSV
    equity_path = reports_dir / PHASE376_EQUITY_CSV
    summary_path = reports_dir / PHASE376_SUMMARY_JSON
    for p in (daily_path, equity_path, summary_path):
        if not p.is_file():
            raise FileNotFoundError(f"missing phase376 input: {p}")
    return {
        "daily_rows": _read_csv(daily_path),
        "equity_rows": _read_csv(equity_path),
        "phase376_summary": json.loads(summary_path.read_text(encoding="utf-8")),
    }


def _sum_optional(values: Sequence[Optional[float]]) -> Optional[float]:
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    return round(sum(nums), 2)


def _rows_for_period_stack(
    daily_rows: Sequence[Mapping[str, str]],
    *,
    period_id: str,
    stack_id: str,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in daily_rows:
        day = str(row.get("day") or "")
        if str(row.get("stack_id") or "") != stack_id:
            continue
        if _period_id_for_day(day) != period_id:
            continue
        out.append(dict(row))
    out.sort(key=lambda r: str(r.get("day") or ""))
    return out


def aggregate_period_metrics(
    daily_rows: Sequence[Mapping[str, str]],
    equity_rows: Sequence[Mapping[str, str]],
    *,
    period_id: str,
    stack_id: str,
) -> dict[str, Any]:
    rows = _rows_for_period_stack(daily_rows, period_id=period_id, stack_id=stack_id)
    if not rows:
        return {
            "period_id": period_id,
            "period_start": PERIOD_A_START if period_id == PERIOD_A_ID else PERIOD_B_START,
            "period_end": PERIOD_A_END if period_id == PERIOD_A_ID else PERIOD_B_END,
            "stack_id": stack_id,
            "day_count": 0,
            "trade_count": 0,
            "total_pnl_yen_100": None,
            "profit_factor": None,
            "win_rate": None,
            "stop_hit_count": 0,
            "low_mfe_stop_hit_count": 0,
            "trailing_mfe_exit_count": 0,
            "dynamic40_pnl_yen_100": None,
            "core10_pnl_yen_100": None,
            "am_pnl_yen_100": None,
            "pm_pnl_yen_100": None,
            "avg_daily_pnl_yen_100": None,
            "median_daily_pnl_yen_100": None,
            "max_daily_profit": None,
            "max_daily_loss": None,
            "max_drawdown_yen_100": None,
            "max_drawdown_end_day": None,
        }

    day_pnls = [_float(r.get("total_pnl_yen_100")) for r in rows]
    valid_pnls = [float(p) for p in day_pnls if p is not None]
    trade_count = sum(_int(r.get("trade_count")) for r in rows)
    win_count = sum(_int(r.get("win_count")) for r in rows)

    gp_total = 0.0
    gl_total = 0.0
    has_pf = False
    for r in rows:
        t = _float(r.get("total_pnl_yen_100"))
        pf = _float(r.get("profit_factor"))
        gp, gl = _gross_profit_loss_from_day(t, pf)
        if gp is None or gl is None:
            continue
        gp_total += float(gp)
        gl_total += float(gl)
        has_pf = True

    eq_rows = [
        r
        for r in equity_rows
        if str(r.get("stack_id") or "") == stack_id
        and _period_id_for_day(str(r.get("day") or "")) == period_id
    ]
    eq_rows.sort(key=lambda r: str(r.get("day") or ""))
    worst_dd = None
    worst_dd_day = None
    if eq_rows:
        worst_dd = min((_float(r.get("drawdown_yen_100")) or 0.0) for r in eq_rows)
        worst_row = min(eq_rows, key=lambda r: _float(r.get("drawdown_yen_100")) or 0.0)
        worst_dd_day = str(worst_row.get("day") or "") or None

    dyn_vals = [_float(r.get("dynamic40_pnl_yen_100")) for r in rows]
    core_vals = [_float(r.get("core10_pnl_yen_100")) for r in rows]
    am_vals = [_float(r.get("am_pnl_yen_100")) for r in rows]
    pm_vals = [_float(r.get("pm_pnl_yen_100")) for r in rows]

    return {
        "period_id": period_id,
        "period_start": PERIOD_A_START if period_id == PERIOD_A_ID else PERIOD_B_START,
        "period_end": PERIOD_A_END if period_id == PERIOD_A_ID else PERIOD_B_END,
        "stack_id": stack_id,
        "day_count": len(rows),
        "trade_count": trade_count,
        "total_pnl_yen_100": _sum_optional(day_pnls),
        "profit_factor": _pf_from_gross(gp_total, gl_total) if has_pf else None,
        "win_rate": round(win_count / trade_count, 4) if trade_count else None,
        "stop_hit_count": sum(_int(r.get("stop_hit_count")) for r in rows),
        "low_mfe_stop_hit_count": sum(_int(r.get("low_mfe_stop_hit_count")) for r in rows),
        "trailing_mfe_exit_count": sum(_int(r.get("trailing_mfe_exit_count")) for r in rows),
        "dynamic40_pnl_yen_100": _sum_optional(dyn_vals),
        "core10_pnl_yen_100": _sum_optional(core_vals),
        "am_pnl_yen_100": _sum_optional(am_vals),
        "pm_pnl_yen_100": _sum_optional(pm_vals),
        "avg_daily_pnl_yen_100": round(sum(valid_pnls) / len(valid_pnls), 2) if valid_pnls else None,
        "median_daily_pnl_yen_100": round(statistics.median(valid_pnls), 2) if valid_pnls else None,
        "max_daily_profit": round(max(valid_pnls), 2) if valid_pnls else None,
        "max_daily_loss": round(min(valid_pnls), 2) if valid_pnls else None,
        "max_drawdown_yen_100": round(worst_dd, 2) if worst_dd is not None else None,
        "max_drawdown_end_day": worst_dd_day,
    }


def _worker_period_job(job: dict[str, Any]) -> dict[str, Any]:
    return aggregate_period_metrics(
        job["daily_rows"],
        job["equity_rows"],
        period_id=job["period_id"],
        stack_id=job["stack_id"],
    )


def build_by_period_rows_parallel(
    daily_rows: Sequence[Mapping[str, str]],
    equity_rows: Sequence[Mapping[str, str]],
    *,
    max_workers: int = 2,
) -> list[dict[str, Any]]:
    jobs = [
        {"period_id": pid, "stack_id": sid, "daily_rows": daily_rows, "equity_rows": equity_rows}
        for pid in (PERIOD_A_ID, PERIOD_B_ID)
        for sid in STACK_VARIANTS
    ]
    if max_workers <= 1 or len(jobs) <= 1:
        return [_worker_period_job(j) for j in jobs]
    out: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_worker_period_job, j): j for j in jobs}
        for fut in as_completed(futures):
            out.append(fut.result())
    out.sort(key=lambda r: (str(r.get("period_id") or ""), str(r.get("stack_id") or "")))
    return out


def build_by_day_rows(equity_rows: Sequence[Mapping[str, str]], *, stack_id: str = PRIMARY_STACK) -> list[dict[str, Any]]:
    rows = [r for r in equity_rows if str(r.get("stack_id") or "") == stack_id]
    rows.sort(key=lambda r: str(r.get("day") or ""))
    out: list[dict[str, Any]] = []
    for r in rows:
        day = str(r.get("day") or "")
        out.append(
            {
                "day": day,
                "period_id": _period_id_for_day(day),
                "stack_id": stack_id,
                "daily_pnl_yen_100": _float(r.get("daily_pnl_yen_100")),
                "cumulative_pnl_yen_100": _float(r.get("cumulative_pnl_yen_100")),
                "drawdown_yen_100": _float(r.get("drawdown_yen_100")),
                "running_peak_yen_100": _float(r.get("running_peak_yen_100")),
            }
        )
    return out


def stack_comparison_deltas(by_period_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_key = {(str(r["period_id"]), str(r["stack_id"])): r for r in by_period_rows}
    comparisons = (
        ("C_vs_A", STACK_C, STACK_A),
        ("C_vs_B", STACK_C, STACK_B),
        ("B_vs_A", STACK_B, STACK_A),
    )
    out: list[dict[str, Any]] = []
    for period_id in (PERIOD_A_ID, PERIOD_B_ID):
        for label, hi, lo in comparisons:
            hi_row = by_key.get((period_id, hi), {})
            lo_row = by_key.get((period_id, lo), {})
            hi_pnl = _float(hi_row.get("total_pnl_yen_100"))
            lo_pnl = _float(lo_row.get("total_pnl_yen_100"))
            hi_pf = _float(hi_row.get("profit_factor"))
            lo_pf = _float(lo_row.get("profit_factor"))
            pnl_delta = round(hi_pnl - lo_pnl, 2) if hi_pnl is not None and lo_pnl is not None else None
            pf_delta = round(hi_pf - lo_pf, 4) if hi_pf is not None and lo_pf is not None else None
            stop_delta = _int(hi_row.get("stop_hit_count")) - _int(lo_row.get("stop_hit_count"))
            out.append(
                {
                    "period_id": period_id,
                    "comparison": label,
                    "pnl_delta": pnl_delta,
                    "pf_delta": pf_delta,
                    "stop_hit_delta": stop_delta,
                    "trade_count_delta": _int(hi_row.get("trade_count")) - _int(lo_row.get("trade_count")),
                }
            )
    return out


def loss_concentration_analysis(
    by_period_rows: Sequence[Mapping[str, Any]],
    by_day_rows: Sequence[Mapping[str, Any]],
    phase376_summary: Mapping[str, Any],
) -> dict[str, Any]:
    by_key = {(str(r["period_id"]), str(r["stack_id"])): r for r in by_period_rows}
    a = by_key.get((PERIOD_A_ID, STACK_C), {})
    b = by_key.get((PERIOD_B_ID, STACK_C), {})

    global_max_dd = _float(phase376_summary.get("max_drawdown_yen_100"))
    global_dd_end = phase376_summary.get("max_drawdown_end_day")
    period_a_dd = _float(a.get("max_drawdown_yen_100"))
    period_b_dd = _float(b.get("max_drawdown_yen_100"))

    max_dd_in_period_a = (
        global_dd_end is not None
        and PERIOD_A_START <= str(global_dd_end) <= PERIOD_A_END
    )
    max_dd_majority_in_a = None
    if global_max_dd is not None and period_a_dd is not None and abs(global_max_dd) > 1e-6:
        max_dd_majority_in_a = abs(period_a_dd) >= abs(global_max_dd) * 0.5

    period_b_pnl = _float(b.get("total_pnl_yen_100"))
    period_b_pf = _float(b.get("profit_factor"))

    a_stop = _int(a.get("stop_hit_count"))
    b_stop = _int(b.get("stop_hit_count"))
    a_low = _int(a.get("low_mfe_stop_hit_count"))
    b_low = _int(b.get("low_mfe_stop_hit_count"))
    a_dyn = _float(a.get("dynamic40_pnl_yen_100"))
    b_dyn = _float(b.get("dynamic40_pnl_yen_100"))
    a_core = _float(a.get("core10_pnl_yen_100"))
    b_core = _float(b.get("core10_pnl_yen_100"))

    def _count_concentration(a_val: int, b_val: int) -> Optional[str]:
        if a_val == b_val:
            return "equal"
        return PERIOD_A_ID if a_val > b_val else PERIOD_B_ID

    def _dynamic40_loss_concentration(
        a_dyn: Optional[float], b_dyn: Optional[float]
    ) -> Optional[str]:
        if a_dyn is None or b_dyn is None:
            return None
        if a_dyn < 0 and b_dyn >= 0:
            return PERIOD_A_ID
        if b_dyn < 0 and a_dyn >= 0:
            return PERIOD_B_ID
        return PERIOD_A_ID if a_dyn < b_dyn else PERIOD_B_ID

    return {
        "q1_max_dd_majority_in_period_a": max_dd_majority_in_a,
        "q1_max_dd_trough_day_in_period_a": max_dd_in_period_a,
        "q1_global_max_drawdown_yen_100": global_max_dd,
        "q1_global_max_drawdown_end_day": global_dd_end,
        "q2_period_b_total_pnl_positive": (period_b_pnl > 0) if period_b_pnl is not None else None,
        "q2_period_b_total_pnl_yen_100": period_b_pnl,
        "q3_period_b_pf_above_1": (period_b_pf > 1.0) if period_b_pf is not None else None,
        "q3_period_b_profit_factor": period_b_pf,
        "q4_stop_hit_concentration": _count_concentration(a_stop, b_stop),
        "q4_period_a_stop_hit_count": a_stop,
        "q4_period_b_stop_hit_count": b_stop,
        "q5_low_mfe_stop_concentration": _count_concentration(a_low, b_low),
        "q5_period_a_low_mfe_stop_hit_count": a_low,
        "q5_period_b_low_mfe_stop_hit_count": b_low,
        "q6_dynamic40_loss_concentration": _dynamic40_loss_concentration(a_dyn, b_dyn),
        "q6_period_a_dynamic40_pnl_yen_100": a_dyn,
        "q6_period_b_dynamic40_pnl_yen_100": b_dyn,
        "q7_period_a_core10_pnl_yen_100": a_core,
        "q7_period_b_core10_pnl_yen_100": b_core,
        "q7_core10_better_period": (
            PERIOD_B_ID
            if a_core is not None and b_core is not None and b_core > a_core
            else PERIOD_A_ID
            if a_core is not None and b_core is not None and a_core > b_core
            else "equal"
            if a_core is not None and b_core is not None and a_core == b_core
            else None
        ),
    }


def _consistency_checks(
    by_period_rows: Sequence[Mapping[str, Any]],
    phase376_summary: Mapping[str, Any],
) -> dict[str, Any]:
    c_rows = [r for r in by_period_rows if str(r.get("stack_id")) == STACK_C]
    total_from_periods = _sum_optional([_float(r.get("total_pnl_yen_100")) for r in c_rows])
    ref_total = _float(phase376_summary.get("total_pnl_yen_100"))
    trade_sum = sum(_int(r.get("trade_count")) for r in c_rows)
    ref_trades = _int(phase376_summary.get("total_trade_count"))
    return {
        "stack_c_total_pnl_from_periods": total_from_periods,
        "phase376_stack_c_total_pnl": ref_total,
        "total_pnl_matches": (
            total_from_periods == ref_total
            if total_from_periods is not None and ref_total is not None
            else None
        ),
        "stack_c_trade_count_from_periods": trade_sum,
        "phase376_stack_c_trade_count": ref_trades,
        "trade_count_matches": trade_sum == ref_trades if ref_trades else None,
    }


def final_verdict(
    by_period_rows: Sequence[Mapping[str, Any]],
    stack_deltas: Sequence[Mapping[str, Any]],
    loss_analysis: Mapping[str, Any],
    phase376_summary: Mapping[str, Any],
) -> dict[str, Any]:
    by_key = {(str(r["period_id"]), str(r["stack_id"])): r for r in by_period_rows}
    c_a = by_key.get((PERIOD_A_ID, STACK_C), {})
    c_b = by_key.get((PERIOD_B_ID, STACK_C), {})
    a_a = by_key.get((PERIOD_A_ID, STACK_A), {})
    a_b = by_key.get((PERIOD_B_ID, STACK_A), {})

    delta_by = {(str(d["period_id"]), str(d["comparison"])): d for d in stack_deltas}
    c_vs_a_a = delta_by.get((PERIOD_A_ID, "C_vs_A"), {})
    c_vs_a_b = delta_by.get((PERIOD_B_ID, "C_vs_A"), {})

    loss_period = (
        PERIOD_A_ID
        if _float(c_a.get("total_pnl_yen_100")) is not None
        and _float(c_b.get("total_pnl_yen_100")) is not None
        and float(c_a.get("total_pnl_yen_100")) < float(c_b.get("total_pnl_yen_100"))
        else None
    )

    period_a_problem_remains = None
    if _float(c_a.get("total_pnl_yen_100")) is not None:
        period_a_problem_remains = float(c_a.get("total_pnl_yen_100")) < 0

    stack_improved = None
    if _float(phase376_summary.get("total_pnl_yen_100")) is not None:
        stack_improved = float(phase376_summary.get("total_pnl_yen_100")) > 0

    pf_sustained = None
    b_pf = _float(c_b.get("profit_factor"))
    a_pf = _float(c_a.get("profit_factor"))
    if b_pf is not None and a_pf is not None:
        pf_sustained = b_pf > 1.0 and b_pf >= a_pf

    entry_improvement_needed = None
    exit_improvement_needed = None
    if period_a_problem_remains is not None:
        guards_helped_in_a = (
            _float(c_vs_a_a.get("pnl_delta")) is not None and float(c_vs_a_a.get("pnl_delta")) > 0
        )
        guards_helped_in_b = (
            _float(c_vs_a_b.get("pnl_delta")) is not None and float(c_vs_a_b.get("pnl_delta")) > 0
        )
        entry_improvement_needed = period_a_problem_remains and not (guards_helped_in_a and guards_helped_in_b)
    if loss_analysis.get("q1_max_dd_trough_day_in_period_a") and period_a_problem_remains:
        exit_improvement_needed = True

    return {
        "loss_concentration_period": loss_period,
        "phase355_364_problem_remains_in_period_a": period_a_problem_remains,
        "current_stack_net_improved_vs_baseline": stack_improved,
        "pf_above_1_sustained_in_period_b": pf_sustained,
        "period_a_stack_c_pnl": _float(c_a.get("total_pnl_yen_100")),
        "period_b_stack_c_pnl": _float(c_b.get("total_pnl_yen_100")),
        "period_a_c_vs_a_pnl_delta": _float(c_vs_a_a.get("pnl_delta")),
        "period_b_c_vs_a_pnl_delta": _float(c_vs_a_b.get("pnl_delta")),
        "entry_improvement_needed": entry_improvement_needed,
        "exit_improvement_needed": exit_improvement_needed,
        "recommendation": _build_recommendation_text(
            loss_period=loss_period,
            period_a_problem_remains=period_a_problem_remains,
            stack_improved=stack_improved,
            pf_sustained=pf_sustained,
            entry_improvement_needed=entry_improvement_needed,
            exit_improvement_needed=exit_improvement_needed,
            loss_analysis=loss_analysis,
        ),
    }


def _build_recommendation_text(**kwargs: Any) -> str:
    parts: list[str] = []
    if kwargs.get("loss_period") == PERIOD_A_ID:
        parts.append("損失は Period A (20260518-20260527) に集中")
    elif kwargs.get("loss_period") == PERIOD_B_ID:
        parts.append("損失は Period B (20260528-20260612) に集中")
    if kwargs.get("period_a_problem_remains"):
        parts.append("Period A の深い赤字はガード導入後も残存")
    if kwargs.get("stack_improved"):
        parts.append("現行スタック全体ではベースライン比改善済み")
    else:
        parts.append("現行スタック全体では依然マージン僅少")
    if kwargs.get("pf_sustained"):
        parts.append("PF>1は Period B で継続的")
    else:
        parts.append("PF>1は Period B のみまたは一時的")
    la = kwargs.get("loss_analysis") or {}
    if la.get("q4_stop_hit_concentration") == PERIOD_A_ID:
        parts.append("stop_hit集中は Period A — EXIT改善検討余地")
    if kwargs.get("entry_improvement_needed"):
        parts.append("Period AではENTRY追加改善も検討")
    return " / ".join(parts) if parts else ""


def build_report_markdown(summary: Mapping[str, Any]) -> str:
    loss = summary.get("loss_concentration") or {}
    verdict = summary.get("final_verdict") or {}
    period_rows = summary.get("period_metrics") or {}
    c_a = (period_rows.get(PERIOD_A_ID) or {}).get(STACK_C) or {}
    c_b = (period_rows.get(PERIOD_B_ID) or {}).get(STACK_C) or {}

    lines = [
        "# Phase377 Daily Regime Breakdown Report",
        "",
        "## 結論",
        "",
        f"- **損失集中期間:** {verdict.get('loss_concentration_period')}",
        f"- **Phase355/364導入後もPeriod A問題残存:** {verdict.get('phase355_364_problem_remains_in_period_a')}",
        f"- **現行スタック改善済みか:** {verdict.get('current_stack_net_improved_vs_baseline')}",
        f"- **PF>1継続的か:** {verdict.get('pf_above_1_sustained_in_period_b')}",
        f"- **ENTRY改善必要か:** {verdict.get('entry_improvement_needed')}",
        f"- **EXIT改善へ移行すべきか:** {verdict.get('exit_improvement_needed')}",
        f"- **推奨:** {verdict.get('recommendation')}",
        "",
        "## Period A (20260518-20260527) — Stack C",
        "",
        f"- total_pnl: {c_a.get('total_pnl_yen_100')}",
        f"- profit_factor: {c_a.get('profit_factor')}",
        f"- trade_count: {c_a.get('trade_count')}",
        f"- stop_hit: {c_a.get('stop_hit_count')}",
        f"- max_drawdown: {c_a.get('max_drawdown_yen_100')}",
        "",
        "## Period B (20260528-20260612) — Stack C",
        "",
        f"- total_pnl: {c_b.get('total_pnl_yen_100')}",
        f"- profit_factor: {c_b.get('profit_factor')}",
        f"- trade_count: {c_b.get('trade_count')}",
        f"- stop_hit: {c_b.get('stop_hit_count')}",
        "",
        "## 損失集中分析（必須7問）",
        "",
        f"1. 最大DDの大部分はPeriod Aか: {loss.get('q1_max_dd_majority_in_period_a')} "
        f"(trough={loss.get('q1_global_max_drawdown_end_day')})",
        f"2. Period Bのみ総損益プラスか: {loss.get('q2_period_b_total_pnl_positive')} "
        f"({loss.get('q2_period_b_total_pnl_yen_100')})",
        f"3. Period BのみPF>1か: {loss.get('q3_period_b_pf_above_1')} ({loss.get('q3_period_b_profit_factor')})",
        f"4. stop_hit集中: {loss.get('q4_stop_hit_concentration')} "
        f"(A={loss.get('q4_period_a_stop_hit_count')}, B={loss.get('q4_period_b_stop_hit_count')})",
        f"5. low_mfe_stop集中: {loss.get('q5_low_mfe_stop_concentration')} "
        f"(A={loss.get('q5_period_a_low_mfe_stop_hit_count')}, B={loss.get('q5_period_b_low_mfe_stop_hit_count')})",
        f"6. Dynamic40損失集中: {loss.get('q6_dynamic40_loss_concentration')} "
        f"(A={loss.get('q6_period_a_dynamic40_pnl_yen_100')}, B={loss.get('q6_period_b_dynamic40_pnl_yen_100')})",
        f"7. Core10期間別: A={loss.get('q7_period_a_core10_pnl_yen_100')}, "
        f"B={loss.get('q7_period_b_core10_pnl_yen_100')}, better={loss.get('q7_core10_better_period')}",
        "",
        "## スタック比較デルタ",
        "",
    ]
    for row in summary.get("stack_comparison_deltas") or []:
        lines.append(
            f"- {row.get('period_id')} {row.get('comparison')}: "
            f"pnl_delta={row.get('pnl_delta')} pf_delta={row.get('pf_delta')} "
            f"stop_hit_delta={row.get('stop_hit_delta')}"
        )
    lines.append("")
    cons = summary.get("consistency_checks") or {}
    lines.extend(
        [
            "## Phase376整合",
            "",
            f"- total_pnl_matches: {cons.get('total_pnl_matches')}",
            f"- trade_count_matches: {cons.get('trade_count_matches')}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


@dataclass
class Phase377DailyRegimeBreakdown:
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase377_daily_regime_breakdown_summary.json",
            "by_day": self.reports_dir / "phase377_daily_regime_breakdown_by_day.csv",
            "by_period": self.reports_dir / "phase377_daily_regime_breakdown_by_period.csv",
            "report": self.reports_dir / "phase377_daily_regime_breakdown_report.md",
        }

    def run(self, *, max_workers: int = 2) -> dict[str, Any]:
        inputs = load_phase376_inputs(self.reports_dir)
        daily_rows = inputs["daily_rows"]
        equity_rows = inputs["equity_rows"]
        phase376_summary = inputs["phase376_summary"]

        by_period_rows = build_by_period_rows_parallel(
            daily_rows, equity_rows, max_workers=max_workers
        )
        by_day_rows = build_by_day_rows(equity_rows, stack_id=PRIMARY_STACK)
        stack_deltas = stack_comparison_deltas(by_period_rows)
        loss_analysis = loss_concentration_analysis(by_period_rows, by_day_rows, phase376_summary)
        consistency = _consistency_checks(by_period_rows, phase376_summary)
        verdict = final_verdict(by_period_rows, stack_deltas, loss_analysis, phase376_summary)

        period_metrics: dict[str, dict[str, dict[str, Any]]] = {}
        for row in by_period_rows:
            pid = str(row["period_id"])
            sid = str(row["stack_id"])
            period_metrics.setdefault(pid, {})[sid] = row

        return {
            "phase": 377,
            "title": "Daily regime breakdown from Phase376",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "primary_stack": PRIMARY_STACK,
            "periods": {
                PERIOD_A_ID: {"start": PERIOD_A_START, "end": PERIOD_A_END},
                PERIOD_B_ID: {"start": PERIOD_B_START, "end": PERIOD_B_END},
            },
            "period_metrics": period_metrics,
            "stack_comparison_deltas": stack_deltas,
            "loss_concentration": loss_analysis,
            "consistency_checks": consistency,
            "final_verdict": verdict,
            "phase376_reference": {
                "total_pnl_yen_100": phase376_summary.get("total_pnl_yen_100"),
                "profit_factor": phase376_summary.get("profit_factor"),
                "max_drawdown_yen_100": phase376_summary.get("max_drawdown_yen_100"),
                "max_drawdown_end_day": phase376_summary.get("max_drawdown_end_day"),
            },
            "_by_period_rows": by_period_rows,
            "_by_day_rows": by_day_rows,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        _write_csv(paths["by_period"], list(result["_by_period_rows"]), BY_PERIOD_FIELDS)
        _write_csv(paths["by_day"], list(result["_by_day_rows"]), BY_DAY_FIELDS)

        summary_payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(
            json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        paths["report"].write_text(build_report_markdown(summary_payload), encoding="utf-8")
        return paths
