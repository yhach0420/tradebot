"""Target-blind evaluation and capital audit. Missing targets stay in the book."""
from __future__ import annotations

import csv
import itertools
import math
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from datetime import datetime

from research.daytrade50_capital_efficient_selector_v2.features import (
    group_features,
    snapshot_days,
    window_for_target,
    load_feature_file,
)
from research.daytrade50_capital_efficient_selector_v2.policy import AWAIT_USER_G

TARGET_MISSING = "TARGET_MISSING"


def _quantile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return ordered[low]
    weight = pos - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def evaluate_book(
    symbols: Sequence[str],
    targets: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Next-day fields are evaluation only. A missing target does not remove the symbol."""
    ranges: list[float] = []
    trading_values: list[float] = []
    active_minutes: list[float] = []
    gaps: list[float] = []
    tick_ratios: list[float] = []
    missing = 0
    illiquid = 0
    for symbol in symbols:
        target = targets.get(symbol)
        if target is None:
            missing += 1
            continue
        trading_value = target.get("trading_value")
        range_pct = target.get("range_pct")
        if trading_value is None or float(trading_value) <= 0:
            illiquid += 1
        else:
            trading_values.append(float(trading_value))
        if range_pct is not None:
            ranges.append(float(range_pct))
        if target.get("active_minutes") is not None:
            active_minutes.append(float(target["active_minutes"]))
        if target.get("max_no_trade_gap") is not None:
            gaps.append(float(target["max_no_trade_gap"]))
        if target.get("tick_ratio") is not None:
            tick_ratios.append(float(target["tick_ratio"]))
    count = len(list(symbols))
    return {
        "coverage_n": count,
        "coverage_50": count == 50,
        "target_missing_n": missing,
        "target_missing_label": TARGET_MISSING,
        "illiquid_n": illiquid,
        "illiquid_rate": (illiquid / count) if count else None,
        "next_range_pct_median": _quantile(ranges, 0.5),
        "next_trading_value_median": _quantile(trading_values, 0.5),
        "active_minutes_median": _quantile(active_minutes, 0.5) if active_minutes else None,
        "max_no_trade_gap_median": _quantile(gaps, 0.5) if gaps else None,
        "tick_ratio_median": _quantile(tick_ratios, 0.5),
        "symbols_unchanged": list(symbols),
    }


def capital_efficiency_audit(
    one_lot_notionals: Sequence[float],
    *,
    gross_budget_yen: Optional[float],
) -> dict[str, Any]:
    if gross_budget_yen is None:
        return {"status": AWAIT_USER_G, "gross_budget_yen": None}
    notionals = [float(value) for value in one_lot_notionals]
    shares = [value / float(gross_budget_yen) for value in notionals]
    eligible = sum(1 for value in notionals if value <= float(gross_budget_yen))
    return {
        "status": "AUDITED",
        "gross_budget_yen": float(gross_budget_yen),
        "n": len(notionals),
        "one_lot_p10": _quantile(notionals, 0.10),
        "one_lot_p50": _quantile(notionals, 0.50),
        "one_lot_p90": _quantile(notionals, 0.90),
        "one_lot_max": max(notionals) if notionals else None,
        "budget_share_p50": _quantile(shares, 0.50),
        "budget_share_max": max(shares) if shares else None,
        "eligible_rate": (eligible / len(notionals)) if notionals else None,
    }


def combination_budget_fit_rate(
    one_lot_notionals: Sequence[float],
    *,
    gross_budget_yen: Optional[float],
    width: int = 5,
) -> dict[str, Any]:
    """Exact fit rate of width-name sums. Uses no future winner labels."""
    if gross_budget_yen is None:
        return {"status": AWAIT_USER_G}
    values = [float(value) for value in one_lot_notionals]
    if len(values) < width:
        return {"status": "INSUFFICIENT_NAMES", "fit_rate": None, "combination_n": 0}
    total = 0
    fit = 0
    for combo in itertools.combinations(values, width):
        total += 1
        if sum(combo) <= float(gross_budget_yen):
            fit += 1
    return {"status": "AUDITED", "combination_n": total, "fit_n": fit, "fit_rate": fit / total}


def causal_open_gross_ok(decisions: Sequence[Mapping[str, Any]], gross_budget_yen: float) -> bool:
    for row in decisions:
        if row.get("admit") and float(row["open_gross"]) > float(gross_budget_yen) + 0.01:
            return False
    return True


def _artifact_row(reports_dir: Path, day: str, target_trading_date: str) -> dict[str, Any]:
    path = reports_dir / f"features_{day}.csv"
    stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    trade_dates = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            trade_dates.add(str(raw.get("trade_date") or ""))
            break
    written_before_open = stamp < f"{day[:4]}-{day[4:6]}-{day[6:8]} 09:00:00"
    return {
        "snapshot_file_date": day,
        "trade_date_column": next(iter(trade_dates), ""),
        "available_at": stamp,
        "written_before_session_open": written_before_open,
        "session_date_stored": False,
        "target_trading_date": target_trading_date,
        "used_for_selection": day < target_trading_date,
    }


def feature_store_audit(reports_dir: Path) -> dict[str, Any]:
    """Coverage for the latest artifact's target date. That file is not a session input."""
    days = snapshot_days(reports_dir)
    if not days:
        return {"status": "INSUFFICIENT_FEATURE_FILES", "calendar_n": 0, "top50_published": False}
    target = days[-1]
    used_days = window_for_target(days, target, 20)
    if len(used_days) < 20:
        return {
            "status": "INSUFFICIENT_FEATURE_FILES",
            "TARGET_TRADING_DATE": target,
            "calendar_n": len(used_days),
            "top50_published": False,
        }
    observations = []
    for day in used_days:
        observations.extend(load_feature_file(reports_dir / f"features_{day}.csv"))
    rows = group_features(observations, selection_day=used_days[-1])
    eligible = [row for row in rows if row.get("eligible")]
    audited = [_artifact_row(reports_dir, day, target) for day in used_days]
    audited.append(_artifact_row(reports_dir, target, target))
    used = [row for row in audited if row["used_for_selection"]]
    return {
        "status": AWAIT_USER_G,
        "date_role": "TARGET_TRADING_DATE",
        "TARGET_TRADING_DATE": target,
        "LATEST_FEATURE_ARTIFACT_USED": used_days[-1],
        "LATEST_COMPLETED_SESSION_USED": None,
        "latest_completed_session_bound": f"before {used_days[-1]} session open; bar date is not stored in the CSV",
        "snapshots": audited,
        "snapshot_n": len(used),
        "used_snapshot_max_date": max(row["snapshot_file_date"] for row in used),
        "future_snapshot_n": sum(1 for row in used if row["snapshot_file_date"] >= target),
        "target_day_data_used": sum(1 for row in used if row["snapshot_file_date"] == target),
        "symbol_n": len(rows),
        "pre_budget_eligible_n": len(eligible),
        "insufficient_history_n": sum(1 for row in rows if row.get("reject") == "INSUFFICIENT_HISTORY"),
        "top50_published": False,
        "gross_budget_yen": None,
        "capital_audit": {"status": AWAIT_USER_G},
        "comparison": {"status": AWAIT_USER_G, "reason": "V2 membership requires user G"},
    }
