"""Historical gates plus continuous premise diagnosis. No new thresholds."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.relative_strength_family_d import C1_FIRST, C1_LAST, DEV_FIRST, DEV_LAST
from research.relative_strength_family_d_discovery_v1 import (
    MIN_FULL_N,
    NEXT_PRECOMMIT,
    NEXT_STOP,
    VERDICT_EXECUTABLE,
    VERDICT_NOT,
    VERDICT_NOT_ACTIONABLE,
)
from research.stock_specific_sequential_setup.decide import _folds


def _stat(values: np.ndarray) -> dict[str, Any]:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = int(arr.size)
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "positive_fraction": None, "negative_fraction": None}
    return {
        "n": n,
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "positive_fraction": float(np.mean(arr > 0)),
        "negative_fraction": float(np.mean(arr < 0)),
    }


def _spearman(x: np.ndarray, y: np.ndarray) -> Optional[float]:
    mask = np.isfinite(x) & np.isfinite(y)
    if int(mask.sum()) < 30:
        return None
    xr = np.argsort(np.argsort(x[mask])).astype(float)
    yr = np.argsort(np.argsort(y[mask])).astype(float)
    if float(xr.std()) == 0.0 or float(yr.std()) == 0.0:
        return None
    return float(np.corrcoef(xr, yr)[0, 1])


def _days(dates: list[str], event_dates: np.ndarray) -> dict[str, Any]:
    counts = {day: 0 for day in dates}
    for day in event_dates.tolist():
        counts[str(day)] = counts.get(str(day), 0) + 1
    daily = np.asarray([counts[day] for day in dates], dtype=float)
    return {
        "per_day_mean": float(daily.mean()) if daily.size else 0.0,
        "per_day_median": float(np.median(daily)) if daily.size else 0.0,
        "active_days": int(np.sum(daily > 0)),
        "zero_days": int(np.sum(daily == 0)),
    }


def decide(scan: dict[str, Any]) -> dict[str, Any]:
    ev = scan["events"]
    full = ev["population"] == "FULL"
    control = ev["population"] == "CONTROL"
    primary = _stat(ev["ret5"][full])
    control5 = _stat(ev["ret5"][control])
    relative = _stat(ev["rel5"][full])
    dev_dates = [day for day in scan["dates"] if DEV_FIRST <= day <= DEV_LAST]
    c1_dates = [day for day in scan["dates"] if C1_FIRST <= day <= C1_LAST]
    dev_fold = _folds(dev_dates)
    c1_fold = _folds(c1_dates)

    def _fold_rows(label: str, fold_of: dict[str, int]) -> list[dict[str, Any]]:
        rows = []
        for fold, name in enumerate(("FOLD_A", "FOLD_B", "FOLD_C")):
            days = [day for day, got in fold_of.items() if got == fold]
            mask = full & np.isin(ev["date"], days)
            stat = _stat(ev["ret5"][mask])
            rows.append({"period": label, "fold": name, **stat, "mean_positive": bool(stat["mean"] is not None and float(stat["mean"]) > 0)})
        return rows

    dev_folds = _fold_rows("DEV", dev_fold)
    c1_folds = _fold_rows("C1", c1_fold)
    dev = _stat(ev["ret5"][full & np.isin(ev["date"], dev_dates)])
    c1 = _stat(ev["ret5"][full & np.isin(ev["date"], c1_dates)])
    dev_control = _stat(ev["ret5"][control & np.isin(ev["date"], dev_dates)])
    c1_control = _stat(ev["ret5"][control & np.isin(ev["date"], c1_dates)])
    delta = None if primary["mean"] is None or control5["mean"] is None else float(primary["mean"]) - float(control5["mean"])
    dev_delta = None if dev["mean"] is None or dev_control["mean"] is None else float(dev["mean"]) - float(dev_control["mean"])
    c1_delta = None if c1["mean"] is None or c1_control["mean"] is None else float(c1["mean"]) - float(c1_control["mean"])
    support = int(full.sum()) >= MIN_FULL_N and primary["n"] >= MIN_FULL_N and dev["n"] > 0 and c1["n"] > 0
    direction = primary["positive_fraction"] is not None and primary["positive_fraction"] > primary["negative_fraction"]
    beats = delta is not None and delta > 0 and dev_delta is not None and dev_delta > 0 and c1_delta is not None and c1_delta > 0
    relative_ok = relative["mean"] is not None and float(relative["mean"]) > 0
    dev_ok = dev["mean"] is not None and float(dev["mean"]) >= 0
    c1_ok = c1["mean"] is not None and float(c1["mean"]) >= 0
    one_strict = (dev["mean"] is not None and float(dev["mean"]) > 0) or (c1["mean"] is not None and float(c1["mean"]) > 0)
    dev_fold_ok = sum(int(row["mean_positive"]) for row in dev_folds) >= 2
    c1_fold_ok = sum(int(row["mean_positive"]) for row in c1_folds) >= 2
    historical = bool(support and primary["mean"] is not None and float(primary["mean"]) > 0 and direction and beats and relative_ok and dev_ok and c1_ok and one_strict and dev_fold_ok and c1_fold_ok)
    full_ret = ev["ret5"][full]
    anatomy = {
        "market_breadth_spearman": _spearman(ev["market_breadth"][full], full_ret),
        "sector_gap_spearman": _spearman(ev["sector_gap"][full], full_ret),
        "stock_rs_spearman": _spearman(ev["relative_strength"][full], full_ret),
        "participation_ratio_spearman": _spearman(ev["volume_ratio"][full], full_ret),
        "continuation_gap_spearman": _spearman(ev["break_gap"][full], full_ret),
        "market_off": {
            "n": int(scan["counts"].get("market_off_n", 0)),
            "mean": None if not scan["counts"].get("market_off_n") else float(scan["counts"]["market_off_sum"]) / float(scan["counts"]["market_off_n"]),
        },
        "sector_off": {
            "n": int(scan["counts"].get("sector_off_n", 0)),
            "mean": None if not scan["counts"].get("sector_off_n") else float(scan["counts"]["sector_off_sum"]) / float(scan["counts"]["sector_off_n"]),
        },
    }
    failed = []
    if not support:
        failed.append("support")
    if not (primary["mean"] is not None and float(primary["mean"]) > 0):
        failed.append("primary_mean")
    if not direction:
        failed.append("positive_fraction")
    if not beats:
        failed.append("stock_rs_versus_control")
    if not relative_ok:
        failed.append("sector_relative_return")
    if not (dev_ok and c1_ok and one_strict):
        failed.append("dev_c1_stability")
    if not dev_fold_ok:
        failed.append("dev_folds")
    if not c1_fold_ok:
        failed.append("c1_folds")
    market_supported = anatomy["market_breadth_spearman"] is not None and anatomy["market_breadth_spearman"] < 0
    sector_supported = anatomy["sector_gap_spearman"] is not None and anatomy["sector_gap_spearman"] > 0
    rs_supported = delta is not None and delta > 0 and anatomy["stock_rs_spearman"] is not None and anatomy["stock_rs_spearman"] > 0
    participation_supported = anatomy["participation_ratio_spearman"] is not None and anatomy["participation_ratio_spearman"] > 0
    continuation_supported = anatomy["continuation_gap_spearman"] is not None and anatomy["continuation_gap_spearman"] > 0
    if historical:
        verdict, nxt = VERDICT_EXECUTABLE, NEXT_PRECOMMIT
    else:
        verdict, nxt = VERDICT_NOT, NEXT_STOP
    return {
        "verdict": verdict,
        "next": nxt,
        "historical_pass": historical,
        "support": support,
        "full_n": int(full.sum()),
        "control_n": int(control.sum()),
        "primary": primary,
        "control5": control5,
        "delta": delta,
        "relative": relative,
        "horizons": {
            "FULL": {"3": _stat(ev["ret3"][full]), "5": primary, "10": _stat(ev["ret10"][full])},
            "CONTROL": {"3": _stat(ev["ret3"][control]), "5": control5, "10": _stat(ev["ret10"][control])},
        },
        "path": {"mfe5": _stat(ev["mfe5"][full]), "mae5": _stat(ev["mae5"][full]), "control_mfe5": _stat(ev["mfe5"][control]), "control_mae5": _stat(ev["mae5"][control])},
        "dev": dev,
        "c1": c1,
        "dev_control": dev_control,
        "c1_control": c1_control,
        "dev_delta": dev_delta,
        "c1_delta": c1_delta,
        "dev_folds": dev_folds,
        "c1_folds": c1_folds,
        "frequency": _days(scan["dates"], ev["date"][full]),
        "failed_premises": failed,
        "anatomy": anatomy,
        "premise_direction": {
            "market_context": market_supported,
            "sector_resilience": sector_supported,
            "stock_rs": rs_supported,
            "participation": participation_supported,
            "technical_continuation": continuation_supported,
        },
        "capture_ran": False,
        "actionability_pass": False,
        "family_closed": False,
    }


def apply_capture(decision: dict[str, Any], capture: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not decision["historical_pass"]:
        return decision
    ask = ((capture or {}).get("ask_to_bid") or {}).get("180") or {}
    original = ((capture or {}).get("ORIGINAL18") or {}).get("ask_180") or {}
    extension = ((capture or {}).get("EXTENSION17") or {}).get("ask_180") or {}
    folds = (capture or {}).get("folds") or []
    fold_ok = sum(int(bool(row.get("mean_positive"))) for row in folds) >= 2
    passed = bool(
        ask.get("mean") is not None
        and float(ask["mean"]) > 0
        and ask.get("median") is not None
        and float(ask["median"]) >= 0
        and ask.get("positive_fraction") is not None
        and float(ask["positive_fraction"]) > float(ask["negative_fraction"])
        and original.get("mean") is not None
        and float(original["mean"]) >= 0
        and extension.get("mean") is not None
        and float(extension["mean"]) >= 0
        and fold_ok
    )
    decision["capture_ran"] = bool(capture and capture.get("ran"))
    decision["actionability_pass"] = passed
    if passed:
        decision["verdict"] = VERDICT_EXECUTABLE
        decision["next"] = NEXT_PRECOMMIT
    else:
        decision["verdict"] = VERDICT_NOT_ACTIONABLE
        decision["next"] = NEXT_STOP
        decision["failed_premises"] = list(decision["failed_premises"]) + ["capture_180s_ask_to_bid"]
    return decision
