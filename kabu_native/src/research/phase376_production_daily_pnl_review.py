"""
Phase376: Production stack daily PnL and equity curve review.

Aggregation/visualization only — no ENTRY/EXIT/Discord/canonical changes.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase365_production_stack_validation import (
    MIN_DAY as PHASE365_MIN_DAY,
    STACK_LABELS,
    STACK_VARIANTS,
    _pf,
    load_session_production_stack_trades,
    stack_blocked,
)
from research.phase374_dynamic40_universe_quality_review import (
    discover_session_roots,
    discover_sessions_for_phase374,
)

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_MIN_DAY = PHASE365_MIN_DAY
LOW_MFE_THRESHOLD_PCT = 0.3
PRIMARY_STACK = "C_phase355_plus_phase364"
SINGLE_DAY_DEPENDENCY_THRESHOLD = 0.5
FOCUS_EXCLUDE_DAY = "20260612"

DAILY_PNL_FIELDS = [
    "day",
    "stack_id",
    "trade_count",
    "win_count",
    "loss_count",
    "win_rate",
    "total_pnl_yen_100",
    "total_pnl_pct",
    "avg_pnl_yen_100",
    "avg_pnl_pct",
    "profit_factor",
    "stop_hit_count",
    "trailing_mfe_exit_count",
    "overlap_replaced_count",
    "session_end_count",
    "low_mfe_stop_hit_count",
    "dynamic40_trade_count",
    "core10_trade_count",
    "dynamic40_pnl_yen_100",
    "core10_pnl_yen_100",
    "am_pnl_yen_100",
    "pm_pnl_yen_100",
]

EQUITY_CURVE_FIELDS = [
    "day",
    "stack_id",
    "daily_pnl_yen_100",
    "cumulative_pnl_yen_100",
    "drawdown_yen_100",
    "running_peak_yen_100",
]

CHART_FIELDS = [
    "day",
    "stack_id",
    "daily_pnl_yen_100",
    "cumulative_pnl_yen_100",
    "drawdown_yen_100",
]


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _session_day_key(session_result: Mapping[str, Any]) -> str:
    day = str(session_result.get("day_key") or "")
    if day:
        return day
    meta = session_result.get("session_meta") or {}
    day = str(meta.get("day_key") or meta.get("day") or "")
    if day:
        return day
    sid = str(session_result.get("session_id") or "")
    if "/" in sid:
        return sid.split("/")[0]
    for trade in session_result.get("trades") or []:
        trade_day = str(trade.get("day_key") or "")
        if trade_day:
            return trade_day
    return ""


def discover_sessions_for_phase376(
    roots: Sequence[Path],
    *,
    min_day: Optional[str] = None,
    max_day: Optional[str] = None,
    all_available: bool = True,
) -> list[dict[str, Any]]:
    return discover_sessions_for_phase374(
        roots,
        min_day=min_day or DEFAULT_MIN_DAY,
        max_day=max_day,
        all_available=all_available,
    )


def kept_trades_for_stack(
    session_result: Mapping[str, Any],
    stack_id: str,
) -> list[dict[str, Any]]:
    session_kind = str(session_result.get("session_kind") or "")
    kept: list[dict[str, Any]] = []
    for trade in session_result.get("trades") or []:
        if stack_blocked(stack_id, trade, session_kind=session_kind):
            continue
        row = dict(trade)
        row["day_key"] = row.get("day_key") or session_result.get("day_key") or ""
        row["session_kind"] = row.get("session_kind") or session_kind
        row["universe_group"] = row.get("universe_group") or "other"
        kept.append(row)
    return kept


def _is_low_mfe_stop(trade: Mapping[str, Any]) -> bool:
    if str(trade.get("exit_reason_canonical") or "") != "stop_hit":
        return False
    peak = _float(trade.get("peak_mfe_pct"))
    return (peak if peak is not None else 0.0) < LOW_MFE_THRESHOLD_PCT


def daily_metrics_from_trades(
    trades: Sequence[Mapping[str, Any]],
    *,
    day: str,
    stack_id: str,
) -> dict[str, Any]:
    yens: list[float] = []
    pcts: list[float] = []
    wins = losses = 0
    stop_hit = trailing = overlap = session_end = low_mfe_stop = 0
    dyn_count = core_count = 0
    dyn_pnl = core_pnl = am_pnl = pm_pnl = 0.0
    has_dyn = has_core = has_am = has_pm = False

    for t in trades:
        yen = _float(t.get("pnl_yen_100"))
        pct = _float(t.get("pnl_pct"))
        if yen is None:
            continue
        y = float(yen)
        yens.append(y)
        if y > 0:
            wins += 1
        elif y < 0:
            losses += 1
        if pct is not None:
            pcts.append(float(pct))

        reason = str(t.get("exit_reason_canonical") or "")
        if reason == "stop_hit":
            stop_hit += 1
            if _is_low_mfe_stop(t):
                low_mfe_stop += 1
        elif reason == "trailing_mfe_exit":
            trailing += 1
        elif reason == "overlap_replaced":
            overlap += 1
        elif reason == "session_end":
            session_end += 1

        ug = str(t.get("universe_group") or "")
        if ug == "dynamic40":
            dyn_count += 1
            dyn_pnl += y
            has_dyn = True
        elif ug == "core10":
            core_count += 1
            core_pnl += y
            has_core = True

        sk = str(t.get("session_kind") or "").lower()
        if sk == "am":
            am_pnl += y
            has_am = True
        elif sk == "pm":
            pm_pnl += y
            has_pm = True

    n = len(yens)
    total_yen = round(sum(yens), 2) if yens else None
    return {
        "day": day,
        "stack_id": stack_id,
        "trade_count": n,
        "win_count": wins,
        "loss_count": losses,
        "win_rate": round(wins / n, 4) if n else None,
        "total_pnl_yen_100": total_yen,
        "total_pnl_pct": round(sum(pcts), 4) if pcts else None,
        "avg_pnl_yen_100": round(total_yen / n, 2) if total_yen is not None and n else None,
        "avg_pnl_pct": round(sum(pcts) / len(pcts), 4) if pcts else None,
        "profit_factor": _pf(yens),
        "stop_hit_count": stop_hit,
        "trailing_mfe_exit_count": trailing,
        "overlap_replaced_count": overlap,
        "session_end_count": session_end,
        "low_mfe_stop_hit_count": low_mfe_stop,
        "dynamic40_trade_count": dyn_count,
        "core10_trade_count": core_count,
        "dynamic40_pnl_yen_100": round(dyn_pnl, 2) if has_dyn else None,
        "core10_pnl_yen_100": round(core_pnl, 2) if has_core else None,
        "am_pnl_yen_100": round(am_pnl, 2) if has_am else None,
        "pm_pnl_yen_100": round(pm_pnl, 2) if has_pm else None,
    }


def build_equity_curve_rows(daily_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_stack: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in daily_rows:
        pnl = _float(row.get("total_pnl_yen_100"))
        if pnl is None:
            continue
        by_stack[str(row.get("stack_id") or "")].append(
            {"day": row.get("day"), "daily_pnl_yen_100": float(pnl)}
        )

    out: list[dict[str, Any]] = []
    for stack_id, rows in sorted(by_stack.items()):
        rows.sort(key=lambda r: str(r.get("day") or ""))
        cumulative = 0.0
        peak = 0.0
        for r in rows:
            daily = float(r["daily_pnl_yen_100"])
            cumulative = round(cumulative + daily, 2)
            peak = max(peak, cumulative)
            drawdown = round(cumulative - peak, 2)
            out.append(
                {
                    "day": r["day"],
                    "stack_id": stack_id,
                    "daily_pnl_yen_100": round(daily, 2),
                    "cumulative_pnl_yen_100": cumulative,
                    "drawdown_yen_100": drawdown,
                    "running_peak_yen_100": round(peak, 2),
                }
            )
    return out


def _streaks(day_pnls: Sequence[tuple[str, float]]) -> tuple[int, int]:
    best_win = best_loss = cur_win = cur_loss = 0
    for _, pnl in day_pnls:
        if pnl > 0:
            cur_win += 1
            cur_loss = 0
        elif pnl < 0:
            cur_loss += 1
            cur_win = 0
        else:
            cur_win = 0
            cur_loss = 0
        best_win = max(best_win, cur_win)
        best_loss = max(best_loss, cur_loss)
    return best_win, best_loss


def _max_drawdown(equity_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not equity_rows:
        return {
            "max_drawdown_yen_100": None,
            "max_drawdown_start_day": None,
            "max_drawdown_end_day": None,
        }
    worst = 0.0
    worst_end = ""
    peak_day = ""
    peak_cum = 0.0
    start_day = ""
    for row in equity_rows:
        day = str(row.get("day") or "")
        cum = _float(row.get("cumulative_pnl_yen_100")) or 0.0
        dd = _float(row.get("drawdown_yen_100")) or 0.0
        if cum >= peak_cum:
            peak_cum = cum
            peak_day = day
        if dd < worst:
            worst = dd
            worst_end = day
            start_day = peak_day
    return {
        "max_drawdown_yen_100": round(worst, 2) if equity_rows else None,
        "max_drawdown_start_day": start_day or None,
        "max_drawdown_end_day": worst_end or None,
    }


def dependency_check(daily_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [
        r
        for r in daily_rows
        if str(r.get("stack_id") or "") == PRIMARY_STACK
        and _float(r.get("total_pnl_yen_100")) is not None
    ]
    if not rows:
        return {
            "top_day_pnl_share": None,
            "top_day_abs_pnl_share": None,
            "pnl_without_best_day": None,
            "pnl_without_worst_day": None,
            "is_single_day_dependent": None,
        }
    total = sum(float(_float(r.get("total_pnl_yen_100")) or 0.0) for r in rows)
    best_day = max(rows, key=lambda r: float(_float(r.get("total_pnl_yen_100")) or 0.0))
    worst_day = min(rows, key=lambda r: float(_float(r.get("total_pnl_yen_100")) or 0.0))
    abs_best = max(rows, key=lambda r: abs(float(_float(r.get("total_pnl_yen_100")) or 0.0)))
    best_pnl = float(_float(best_day.get("total_pnl_yen_100")) or 0.0)
    worst_pnl = float(_float(worst_day.get("total_pnl_yen_100")) or 0.0)
    abs_pnl = float(_float(abs_best.get("total_pnl_yen_100")) or 0.0)
    top_share = round(best_pnl / total, 4) if abs(total) > 1e-6 else None
    abs_share = round(abs_pnl / abs(total), 4) if abs(total) > 1e-6 else None
    return {
        "top_day_pnl_share": top_share,
        "top_day_abs_pnl_share": abs_share,
        "top_profit_day": best_day.get("day"),
        "top_profit_day_pnl": best_pnl,
        "worst_loss_day": worst_day.get("day"),
        "worst_loss_day_pnl": worst_pnl,
        "pnl_without_best_day": round(total - best_pnl, 2),
        "pnl_without_worst_day": round(total - worst_pnl, 2),
        "is_single_day_dependent": bool(abs_share is not None and abs_share >= SINGLE_DAY_DEPENDENCY_THRESHOLD),
    }


def stop_hit_day_analysis(daily_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [
        r
        for r in daily_rows
        if str(r.get("stack_id") or "") == PRIMARY_STACK
        and _float(r.get("total_pnl_yen_100")) is not None
    ]
    if len(rows) < 2:
        return {"correlation_stop_hit_vs_pnl": None, "high_stop_hit_days_avg_pnl": None, "low_stop_hit_days_avg_pnl": None}
    stops = [int(r.get("stop_hit_count") or 0) for r in rows]
    pnls = [float(_float(r.get("total_pnl_yen_100")) or 0.0) for r in rows]
    median_stop = statistics.median(stops)
    high = [p for s, p in zip(stops, pnls) if s >= median_stop]
    low = [p for s, p in zip(stops, pnls) if s < median_stop]
    corr = None
    if len(set(stops)) > 1 and len(set(pnls)) > 1:
        mean_s = sum(stops) / len(stops)
        mean_p = sum(pnls) / len(pnls)
        num = sum((s - mean_s) * (p - mean_p) for s, p in zip(stops, pnls))
        den_s = math.sqrt(sum((s - mean_s) ** 2 for s in stops))
        den_p = math.sqrt(sum((p - mean_p) ** 2 for p in pnls))
        if den_s > 0 and den_p > 0:
            corr = round(num / (den_s * den_p), 4)
    return {
        "correlation_stop_hit_vs_pnl": corr,
        "median_stop_hit_count": median_stop,
        "high_stop_hit_days_avg_pnl": round(sum(high) / len(high), 2) if high else None,
        "low_stop_hit_days_avg_pnl": round(sum(low) / len(low), 2) if low else None,
        "stop_hit_heavy_days_lose_more": (
            (sum(high) / len(high)) < (sum(low) / len(low)) if high and low else None
        ),
    }


def build_stack_summary(
    daily_rows: Sequence[Mapping[str, Any]],
    equity_rows: Sequence[Mapping[str, Any]],
    *,
    stack_id: str,
    trade_yens: Optional[Sequence[float]] = None,
    trade_yens_excluding_day: Optional[Sequence[float]] = None,
) -> dict[str, Any]:
    days = [r for r in daily_rows if str(r.get("stack_id") or "") == stack_id]
    eq = [r for r in equity_rows if str(r.get("stack_id") or "") == stack_id]
    day_pnls = [
        (str(r.get("day") or ""), float(_float(r.get("total_pnl_yen_100")) or 0.0))
        for r in days
        if _float(r.get("total_pnl_yen_100")) is not None
    ]
    winning = [p for _, p in day_pnls if p > 0]
    losing = [p for _, p in day_pnls if p < 0]
    total_pnl = round(sum(p for _, p in day_pnls), 2) if day_pnls else None
    pf_all = _pf(list(trade_yens)) if trade_yens else None

    exclude_rows = [r for r in days if str(r.get("day") or "") != FOCUS_EXCLUDE_DAY]
    exclude_pnls = [
        float(_float(r.get("total_pnl_yen_100")) or 0.0)
        for r in exclude_rows
        if _float(r.get("total_pnl_yen_100")) is not None
    ]

    sorted_days = sorted(day_pnls, key=lambda x: x[1], reverse=True)
    dd = _max_drawdown(eq)
    win_streak, loss_streak = _streaks(day_pnls)

    dyn_total = sum(_float(r.get("dynamic40_pnl_yen_100")) or 0.0 for r in days)
    core_total = sum(_float(r.get("core10_pnl_yen_100")) or 0.0 for r in days)
    am_total = sum(_float(r.get("am_pnl_yen_100")) or 0.0 for r in days)
    pm_total = sum(_float(r.get("pm_pnl_yen_100")) or 0.0 for r in days)

    cumulative_positive_trend = None
    if len(eq) >= 2:
        cumulative_positive_trend = float(eq[-1].get("cumulative_pnl_yen_100") or 0) > float(
            eq[0].get("cumulative_pnl_yen_100") or 0
        )

    return {
        "stack_id": stack_id,
        "stack_label": STACK_LABELS.get(stack_id, stack_id),
        "total_days": len(day_pnls),
        "winning_days": len(winning),
        "losing_days": len(losing),
        "flat_days": len(day_pnls) - len(winning) - len(losing),
        "win_day_rate": round(len(winning) / len(day_pnls), 4) if day_pnls else None,
        "total_pnl_yen_100": total_pnl,
        "profit_factor": pf_all,
        "avg_daily_pnl_yen_100": round(total_pnl / len(day_pnls), 2) if total_pnl is not None and day_pnls else None,
        "median_daily_pnl_yen_100": round(statistics.median([p for _, p in day_pnls]), 2) if day_pnls else None,
        "max_daily_profit_yen_100": round(max((p for _, p in day_pnls), default=0.0), 2) if day_pnls else None,
        "max_daily_profit_day": sorted_days[0][0] if sorted_days else None,
        "max_daily_loss_yen_100": round(min((p for _, p in day_pnls), default=0.0), 2) if day_pnls else None,
        "max_daily_loss_day": sorted_days[-1][0] if sorted_days else None,
        "longest_winning_streak": win_streak,
        "longest_losing_streak": loss_streak,
        "total_trade_count": sum(int(r.get("trade_count") or 0) for r in days),
        "total_stop_hit_count": sum(int(r.get("stop_hit_count") or 0) for r in days),
        "total_low_mfe_stop_hit_count": sum(int(r.get("low_mfe_stop_hit_count") or 0) for r in days),
        "total_trailing_mfe_exit_count": sum(int(r.get("trailing_mfe_exit_count") or 0) for r in days),
        "total_dynamic40_pnl_yen_100": round(dyn_total, 2) if any(_float(r.get("dynamic40_pnl_yen_100")) is not None for r in days) else None,
        "total_core10_pnl_yen_100": round(core_total, 2) if any(_float(r.get("core10_pnl_yen_100")) is not None for r in days) else None,
        "total_am_pnl_yen_100": round(am_total, 2) if any(_float(r.get("am_pnl_yen_100")) is not None for r in days) else None,
        "total_pm_pnl_yen_100": round(pm_total, 2) if any(_float(r.get("pm_pnl_yen_100")) is not None for r in days) else None,
        "pnl_excluding_20260612": round(sum(exclude_pnls), 2) if exclude_pnls else None,
        "pf_excluding_20260612": _pf(list(trade_yens_excluding_day)) if trade_yens_excluding_day else None,
        "top_profit_days": [{"day": d, "pnl_yen_100": round(p, 2)} for d, p in sorted_days[:5]],
        "worst_loss_days": [{"day": d, "pnl_yen_100": round(p, 2)} for d, p in sorted(sorted_days, key=lambda x: x[1])[:5]],
        "cumulative_positive_trend": cumulative_positive_trend,
        **dd,
    }


def build_report_markdown(
    summary: Mapping[str, Any],
    *,
    primary: Mapping[str, Any],
    dependency: Mapping[str, Any],
    stop_hit: Mapping[str, Any],
) -> str:
    lines = [
        "# Phase376 Production Stack Daily PnL Report",
        "",
        "## 結論",
        "",
    ]
    total = _float(primary.get("total_pnl_yen_100"))
    exclude_pnl = _float(primary.get("pnl_excluding_20260612"))
    maintain = (
        total is not None
        and total > 0
        and (exclude_pnl is None or exclude_pnl > 0)
        and not dependency.get("is_single_day_dependent")
    )
    lines.append(
        f"- **本番維持可否:** {'維持' if maintain else '要再検討'}"
    )
    lines.append(
        f"- **右肩上がりか:** {primary.get('cumulative_positive_trend')}"
    )
    lines.append(
        f"- **6/12依存か:** {dependency.get('is_single_day_dependent')} "
        f"(top_day_abs_share={dependency.get('top_day_abs_pnl_share')})"
    )
    lines.append(
        f"- **6/12除外後もプラスか:** {exclude_pnl is not None and exclude_pnl > 0} "
        f"(pnl={exclude_pnl})"
    )
    lines.append(f"- **最大DD:** {primary.get('max_drawdown_yen_100')} yen "
                 f"({primary.get('max_drawdown_start_day')} → {primary.get('max_drawdown_end_day')})")
    lines.append(
        f"- **勝ち日/負け日:** {primary.get('winning_days')}/{primary.get('losing_days')} "
        f"(win_day_rate={primary.get('win_day_rate')})"
    )
    lines.append(
        f"- **AM/PM利益源:** AM={primary.get('total_am_pnl_yen_100')} / "
        f"PM={primary.get('total_pm_pnl_yen_100')}"
    )
    lines.append(
        f"- **Dynamic40/Core10利益源:** D40={primary.get('total_dynamic40_pnl_yen_100')} / "
        f"C10={primary.get('total_core10_pnl_yen_100')}"
    )
    lines.append(
        f"- **stop_hit多い日は悪いか:** {stop_hit.get('stop_hit_heavy_days_lose_more')} "
        f"(corr={stop_hit.get('correlation_stop_hit_vs_pnl')})"
    )
    lines.extend(["", "## 本番スタック概要 (C)", ""])
    for key in (
        "total_days",
        "total_pnl_yen_100",
        "profit_factor",
        "max_daily_profit_yen_100",
        "max_daily_loss_yen_100",
        "pnl_excluding_20260612",
    ):
        lines.append(f"- {key}: {primary.get(key)}")
    lines.extend(["", "## 日別損益 上位5日", ""])
    for row in primary.get("top_profit_days") or []:
        lines.append(f"- {row.get('day')}: {row.get('pnl_yen_100')}")
    lines.extend(["", "## 日別損益 下位5日", ""])
    for row in primary.get("worst_loss_days") or []:
        lines.append(f"- {row.get('day')}: {row.get('pnl_yen_100')}")
    if summary.get("stack_comparison"):
        lines.extend(["", "## スタック比較", ""])
        for sid, sm in (summary.get("stack_comparison") or {}).items():
            lines.append(
                f"- {sid}: pnl={sm.get('total_pnl_yen_100')} pf={sm.get('profit_factor')} "
                f"trades={sm.get('total_trade_count')}"
            )
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _try_write_png_charts(
    equity_rows: Sequence[Mapping[str, Any]],
    daily_rows: Sequence[Mapping[str, Any]],
    *,
    reports_dir: Path,
    stack_id: str = PRIMARY_STACK,
) -> dict[str, Optional[str]]:
    out: dict[str, Optional[str]] = {
        "equity_curve_png": None,
        "daily_pnl_bar_png": None,
    }
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return out

    eq = [r for r in equity_rows if str(r.get("stack_id") or "") == stack_id]
    daily = [r for r in daily_rows if str(r.get("stack_id") or "") == stack_id]
    if not eq:
        return out

    days = [str(r.get("day") or "") for r in eq]
    cumulative = [float(r.get("cumulative_pnl_yen_100") or 0.0) for r in eq]
    equity_path = reports_dir / "phase376_production_equity_curve.png"
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(days, cumulative, marker="o", linewidth=1.5)
    ax.set_title("Phase376 Production Equity Curve (Stack C)")
    ax.set_xlabel("day")
    ax.set_ylabel("cumulative_pnl_yen_100")
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    fig.savefig(equity_path, dpi=120)
    plt.close(fig)
    out["equity_curve_png"] = str(equity_path)

    if daily:
        ddays = [str(r.get("day") or "") for r in daily]
        dpnl = [float(_float(r.get("total_pnl_yen_100")) or 0.0) for r in daily]
        bar_path = reports_dir / "phase376_production_daily_pnl_bar.png"
        fig2, ax2 = plt.subplots(figsize=(10, 4))
        colors = ["#2ca02c" if v >= 0 else "#d62728" for v in dpnl]
        ax2.bar(ddays, dpnl, color=colors)
        ax2.set_title("Phase376 Daily PnL (Stack C)")
        ax2.set_xlabel("day")
        ax2.set_ylabel("daily_pnl_yen_100")
        ax2.tick_params(axis="x", rotation=45)
        fig2.tight_layout()
        fig2.savefig(bar_path, dpi=120)
        plt.close(fig2)
        out["daily_pnl_bar_png"] = str(bar_path)
    return out


@dataclass
class Phase376ProductionDailyPnlReview:
    reports_dir: Path
    session_results: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "daily_pnl": self.reports_dir / "phase376_production_daily_pnl.csv",
            "equity_curve": self.reports_dir / "phase376_production_equity_curve.csv",
            "chart": self.reports_dir / "phase376_production_daily_pnl_chart.csv",
            "summary": self.reports_dir / "phase376_production_daily_pnl_summary.json",
            "report": self.reports_dir / "phase376_production_daily_pnl_report.md",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        row = dict(result)
        day = _session_day_key(row)
        if day:
            row["day_key"] = day
        self.session_results.append(row)

    def aggregate(
        self,
        *,
        compare_stacks: bool = False,
    ) -> dict[str, Any]:
        stacks = list(STACK_VARIANTS) if compare_stacks else [PRIMARY_STACK]
        trades_by_day_stack: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        yens_by_stack: dict[str, list[float]] = defaultdict(list)
        yens_excl_by_stack: dict[str, list[float]] = defaultdict(list)

        for sr in self.session_results:
            day = _session_day_key(sr)
            if not day:
                continue
            for stack_id in stacks:
                for trade in kept_trades_for_stack(sr, stack_id):
                    yen = _float(trade.get("pnl_yen_100"))
                    if yen is None:
                        continue
                    trades_by_day_stack[(day, stack_id)].append(trade)
                    yens_by_stack[stack_id].append(float(yen))
                    if day != FOCUS_EXCLUDE_DAY:
                        yens_excl_by_stack[stack_id].append(float(yen))

        daily_rows: list[dict[str, Any]] = []
        for (day, stack_id) in sorted(trades_by_day_stack.keys()):
            daily_rows.append(
                daily_metrics_from_trades(
                    trades_by_day_stack[(day, stack_id)],
                    day=day,
                    stack_id=stack_id,
                )
            )

        equity_rows = build_equity_curve_rows(daily_rows)
        chart_rows = [
            {
                "day": r["day"],
                "stack_id": r["stack_id"],
                "daily_pnl_yen_100": r["daily_pnl_yen_100"],
                "cumulative_pnl_yen_100": r["cumulative_pnl_yen_100"],
                "drawdown_yen_100": r["drawdown_yen_100"],
            }
            for r in equity_rows
        ]

        primary_summary = build_stack_summary(
            daily_rows,
            equity_rows,
            stack_id=PRIMARY_STACK,
            trade_yens=yens_by_stack.get(PRIMARY_STACK),
            trade_yens_excluding_day=yens_excl_by_stack.get(PRIMARY_STACK),
        )
        primary_summary["dependency_check"] = dependency_check(daily_rows)
        primary_summary["stop_hit_day_analysis"] = stop_hit_day_analysis(daily_rows)

        stack_comparison = {}
        if compare_stacks:
            for sid in STACK_VARIANTS:
                if sid != PRIMARY_STACK:
                    stack_comparison[sid] = build_stack_summary(
                        daily_rows,
                        equity_rows,
                        stack_id=sid,
                        trade_yens=yens_by_stack.get(sid),
                        trade_yens_excluding_day=yens_excl_by_stack.get(sid),
                    )

        return {
            "daily_rows": daily_rows,
            "equity_rows": equity_rows,
            "chart_rows": chart_rows,
            "primary_summary": primary_summary,
            "stack_comparison": stack_comparison,
        }

    def finalize_outputs(
        self,
        *,
        wall_runtime_sec: float,
        sessions_discovered: int,
        sessions_evaluated: int,
        compare_stacks: bool = False,
        write_png: bool = True,
        min_day: Optional[str] = None,
    ) -> dict[str, Path]:
        paths = self.paths()
        agg = self.aggregate(compare_stacks=compare_stacks)
        primary = agg["primary_summary"]
        dependency = primary.get("dependency_check") or {}
        stop_hit = primary.get("stop_hit_day_analysis") or {}

        summary = {
            "phase": 376,
            "title": "Production stack daily PnL & equity curve review",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "population": {
                "min_day": min_day or DEFAULT_MIN_DAY,
                "primary_stack": PRIMARY_STACK,
                "compare_stacks": compare_stacks,
                "sessions_discovered": sessions_discovered,
                "sessions_evaluated": sessions_evaluated,
            },
            **{k: primary.get(k) for k in primary if k not in ("dependency_check", "stop_hit_day_analysis")},
            "dependency_check": dependency,
            "stop_hit_day_analysis": stop_hit,
            "stack_comparison": agg["stack_comparison"] or None,
            "wall_runtime_sec": round(wall_runtime_sec, 2),
        }

        _write_csv(paths["daily_pnl"], agg["daily_rows"], DAILY_PNL_FIELDS)
        _write_csv(paths["equity_curve"], agg["equity_rows"], EQUITY_CURVE_FIELDS)
        _write_csv(paths["chart"], agg["chart_rows"], CHART_FIELDS)
        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        paths["report"].write_text(
            build_report_markdown(
                summary,
                primary=primary,
                dependency=dependency,
                stop_hit=stop_hit,
            ),
            encoding="utf-8",
        )

        if write_png:
            png_paths = _try_write_png_charts(
                agg["equity_rows"], agg["daily_rows"], reports_dir=self.reports_dir
            )
            summary["chart_png"] = png_paths
            paths["summary"].write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        return paths
