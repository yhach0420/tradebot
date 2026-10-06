"""Family D gates. No threshold is searched here."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.relative_strength_family_d import (
    C1_FIRST,
    C1_LAST,
    DEV_FIRST,
    DEV_LAST,
    MIN_FULL_N,
    NEXT_FAMILY_C,
    NEXT_PRECOMMIT,
    NEXT_STOP,
    VERDICT_EXECUTABLE,
    VERDICT_HISTORICAL,
    VERDICT_NOT,
    VERDICT_SUPPORT,
)
from research.stock_specific_sequential_setup.decide import _folds


def _stat(values: np.ndarray) -> dict[str, Any]:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = int(arr.size)
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "positive_fraction": None, "zero_fraction": None, "negative_fraction": None, "q25": None, "q75": None}
    return {
        "n": n,
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "positive_fraction": float(np.mean(arr > 0)),
        "zero_fraction": float(np.mean(arr == 0)),
        "negative_fraction": float(np.mean(arr < 0)),
        "q25": float(np.quantile(arr, 0.25)),
        "q75": float(np.quantile(arr, 0.75)),
    }


def _period(dates: np.ndarray) -> np.ndarray:
    out = np.full(dates.shape, "", dtype=object)
    out[(dates >= DEV_FIRST) & (dates <= DEV_LAST)] = "DEV"
    out[(dates >= C1_FIRST) & (dates <= C1_LAST)] = "C1"
    return out


def _mean_positive(stat: dict[str, Any], strict: bool) -> bool:
    if stat["n"] <= 0 or stat["mean"] is None:
        return False
    return float(stat["mean"]) > 0 if strict else float(stat["mean"]) >= 0


def _share(keys: np.ndarray, values: np.ndarray) -> dict[str, Any]:
    total = float(np.sum(values)) if values.size else 0.0
    if values.size == 0 or total <= 0:
        return {"top": None, "share": None, "supplies_all": False}
    best_key = None
    best = -1.0
    for key in np.unique(keys):
        got = float(np.sum(values[keys == key]))
        if got > best:
            best = got
            best_key = str(key)
    share = best / total
    return {"top": best_key, "share": share, "supplies_all": bool(share >= 1.0 - 1e-12)}


def decide(scan: dict[str, Any]) -> dict[str, Any]:
    ev = scan["events"]
    full = ev["population"] == "FULL"
    control = ev["population"] == "CONTROL"
    period = _period(ev["date"])
    horizons = {}
    for name, mask in (("FULL", full), ("CONTROL", control)):
        horizons[name] = {
            "3": _stat(ev["ret3"][mask]),
            "5": _stat(ev["ret5"][mask]),
            "10": _stat(ev["ret10"][mask]),
        }
    primary = horizons["FULL"]["5"]
    control5 = horizons["CONTROL"]["5"]
    relative = _stat(ev["rel5"][full])
    dev_dates = [day for day in scan["dates"] if DEV_FIRST <= day <= DEV_LAST]
    c1_dates = [day for day in scan["dates"] if C1_FIRST <= day <= C1_LAST]
    dev_fold = _folds(dev_dates)
    c1_fold = _folds(c1_dates)

    def _fold_rows(label: str, fold_of: dict[str, int]) -> list[dict[str, Any]]:
        rows = []
        for fold in range(3):
            days = {day for day, got in fold_of.items() if got == fold}
            mask = full & np.isin(ev["date"], list(days))
            stat = _stat(ev["ret5"][mask])
            rows.append({"period": label, "fold": fold, "events": int(mask.sum()), **stat, "mean_positive": _mean_positive(stat, True)})
        return rows

    dev_folds = _fold_rows("DEV", dev_fold)
    c1_folds = _fold_rows("C1", c1_fold)
    dev = _stat(ev["ret5"][full & (period == "DEV")])
    c1 = _stat(ev["ret5"][full & (period == "C1")])
    dev_control = _stat(ev["ret5"][control & (period == "DEV")])
    c1_control = _stat(ev["ret5"][control & (period == "C1")])
    positive = full & np.isfinite(ev["ret5"]) & (ev["ret5"] > 0)
    concentration = {
        "day": _share(ev["date"][positive], ev["ret5"][positive]),
        "symbol": _share(ev["symbol"][positive], ev["ret5"][positive]),
        "sector": _share(ev["sector"][positive], ev["ret5"][positive]),
    }
    represented = all(row["events"] > 0 and row["n"] > 0 for row in dev_folds + c1_folds)
    support = bool(int(full.sum()) >= MIN_FULL_N and primary["n"] >= MIN_FULL_N and dev["n"] > 0 and c1["n"] > 0 and represented)
    delta = None if primary["mean"] is None or control5["mean"] is None else float(primary["mean"]) - float(control5["mean"])
    dev_delta = None if dev["mean"] is None or dev_control["mean"] is None else float(dev["mean"]) - float(dev_control["mean"])
    c1_delta = None if c1["mean"] is None or c1_control["mean"] is None else float(c1["mean"]) - float(c1_control["mean"])
    direction = primary["positive_fraction"] is not None and primary["positive_fraction"] > primary["negative_fraction"]
    beats = delta is not None and delta > 0 and dev_delta is not None and dev_delta > 0 and c1_delta is not None and c1_delta > 0
    relative_ok = relative["mean"] is not None and float(relative["mean"]) > 0
    dev_ok = _mean_positive(dev, False)
    c1_ok = _mean_positive(c1, False)
    one_strict = _mean_positive(dev, True) or _mean_positive(c1, True)
    dev_fold_ok = sum(int(row["mean_positive"]) for row in dev_folds) >= 2
    c1_fold_ok = sum(int(row["mean_positive"]) for row in c1_folds) >= 2
    monopoly = bool(concentration["day"]["supplies_all"] or concentration["symbol"]["supplies_all"])
    historical = bool(
        support
        and primary["mean"] is not None
        and float(primary["mean"]) > 0
        and direction
        and beats
        and relative_ok
        and dev_ok
        and c1_ok
        and one_strict
        and dev_fold_ok
        and c1_fold_ok
        and not monopoly
    )
    if not support:
        verdict, nxt = VERDICT_SUPPORT, NEXT_STOP
    elif historical:
        verdict, nxt = VERDICT_HISTORICAL, NEXT_PRECOMMIT
    else:
        verdict, nxt = VERDICT_NOT, NEXT_FAMILY_C
    return {
        "verdict": verdict,
        "next": nxt,
        "historical_pass": historical,
        "support": support,
        "full_n": int(full.sum()),
        "control_n": int(control.sum()),
        "horizons": horizons,
        "primary": primary,
        "control5": control5,
        "delta": delta,
        "relative": relative,
        "dev": dev,
        "c1": c1,
        "dev_control": dev_control,
        "c1_control": c1_control,
        "dev_delta": dev_delta,
        "c1_delta": c1_delta,
        "dev_folds": dev_folds,
        "c1_folds": c1_folds,
        "concentration": concentration,
        "gates": {
            "mean_positive": bool(primary["mean"] is not None and float(primary["mean"]) > 0),
            "positive_fraction_beats_negative": bool(direction),
            "full_beats_control_overall_dev_c1": bool(beats),
            "relative_mean_positive": bool(relative_ok),
            "period_nonnegative_and_one_strict": bool(dev_ok and c1_ok and one_strict),
            "dev_folds": bool(dev_fold_ok),
            "c1_folds": bool(c1_fold_ok),
            "no_day_or_symbol_monopoly": not monopoly,
        },
        "path": {
            "mfe5": _stat(ev["mfe5"][full]),
            "mae5": _stat(ev["mae5"][full]),
        },
        "capture_ran": False,
        "actionability_pass": False,
        "executable_constants": {"VERDICT_EXECUTABLE": VERDICT_EXECUTABLE},
    }


def apply_capture(decision: dict[str, Any], capture: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not decision["historical_pass"]:
        decision["capture_ran"] = False
        decision["actionability_pass"] = False
        return decision
    if not capture or not capture.get("ran"):
        decision["historical_pass"] = False
        decision["verdict"] = VERDICT_NOT
        decision["next"] = NEXT_FAMILY_C
        decision["capture_ran"] = False
        decision["actionability_pass"] = False
        return decision
    ask = (capture.get("ask_to_bid") or {}).get("180") or {}
    original = (capture.get("ORIGINAL18") or {}).get("ask_180") or {}
    extension = (capture.get("EXTENSION17") or {}).get("ask_180") or {}
    folds = capture.get("folds") or []
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
    decision["capture_ran"] = True
    decision["actionability_pass"] = passed
    if passed:
        decision["verdict"] = VERDICT_EXECUTABLE
        decision["next"] = NEXT_PRECOMMIT
    else:
        decision["verdict"] = VERDICT_NOT
        decision["next"] = NEXT_FAMILY_C
    return decision
