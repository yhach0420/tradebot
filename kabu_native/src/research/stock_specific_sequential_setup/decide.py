"""Choose at most one mechanism. Clarity order is fixed before the scan."""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

import numpy as np

from research.stock_specific_sequential_setup import (
    C1_FIRST,
    DAY_MONOPOLY_MAX,
    DEV_LAST,
    FAMILIES,
    FOLD_COUNT,
    FOLDS_REQUIRED,
    MIN_FOLD_N,
    MIN_PERIOD_N,
    MIN_TOTAL_N,
    NEXT_PRECOMMIT,
    NEXT_STOP,
    SYMBOL_MONOPOLY_MAX,
    VERDICT_NOT,
    VERDICT_SUPPORTED,
)


def _finite(xs) -> np.ndarray:
    arr = np.asarray([np.nan if x is None else float(x) for x in xs], dtype=float)
    return arr[np.isfinite(arr)]


def _median(xs) -> Optional[float]:
    arr = _finite(xs)
    if arr.size == 0:
        return None
    return float(np.median(arr))


def _folds(dates: list[str]) -> dict[str, int]:
    ordered = sorted(set(dates))
    if not ordered:
        return {}
    cuts = [int(round(i * len(ordered) / FOLD_COUNT)) for i in range(FOLD_COUNT + 1)]
    out = {}
    for fold, (lo, hi) in enumerate(zip(cuts, cuts[1:])):
        for day in ordered[lo:hi]:
            out[day] = fold
    return out


def _period(day: str) -> str:
    return "DEV" if day <= DEV_LAST else "C1"


def _family(bucket: dict[str, list], dev_folds: dict[str, int], c1_folds: dict[str, int]) -> dict[str, Any]:
    dates = list(bucket["date"])
    symbols = list(bucket["symbol"])
    ret5 = list(bucket["ret5"])
    usable = [(d, s, r, bucket["ret3"][i], bucket["ret10"][i], bucket["mfe5"][i], bucket["mae5"][i]) for i, (d, s, r) in enumerate(zip(dates, symbols, ret5)) if r is not None]
    dev = [row for row in usable if _period(row[0]) == "DEV"]
    c1 = [row for row in usable if _period(row[0]) == "C1" and row[0] >= C1_FIRST]

    def fold_rows(rows, mapping):
        grouped = {i: [] for i in range(FOLD_COUNT)}
        for row in rows:
            fold = mapping.get(row[0])
            if fold is not None and row[2] is not None:
                grouped[fold].append(float(row[2]))
        return [
            {"fold": i, "n": len(grouped[i]), "median_ret5_bps": _median(grouped[i]), "positive": len(grouped[i]) >= MIN_FOLD_N and (_median(grouped[i]) or -1) > 0}
            for i in range(FOLD_COUNT)
        ]

    dev_fold = fold_rows(dev, dev_folds)
    c1_fold = fold_rows(c1, c1_folds)
    day_counts = Counter(row[0] for row in usable)
    sym_counts = Counter(row[1] for row in usable)
    n = len(usable)
    top_day = max(day_counts.values()) / n if n else None
    top_sym = max(sym_counts.values()) / n if n else None
    med5 = _median([row[2] for row in usable])
    dev_med = _median([row[2] for row in dev])
    c1_med = _median([row[2] for row in c1])
    gates = {
        "median_3_positive": (_median([row[3] for row in usable]) or -1) > 0,
        "median_5_positive": (med5 or -1) > 0,
        "median_10_positive": (_median([row[4] for row in usable]) or -1) > 0,
        "dev_non_negative": dev_med is not None and dev_med > 0,
        "c1_non_negative": c1_med is not None and c1_med > 0,
        "dev_folds": sum(1 for row in dev_fold if row["positive"]) >= FOLDS_REQUIRED,
        "c1_folds": sum(1 for row in c1_fold if row["positive"]) >= FOLDS_REQUIRED,
        "support": n >= MIN_TOTAL_N and len(dev) >= MIN_PERIOD_N and len(c1) >= MIN_PERIOD_N,
        "no_day_monopoly": top_day is not None and top_day < DAY_MONOPOLY_MAX,
        "no_symbol_monopoly": top_sym is not None and top_sym < SYMBOL_MONOPOLY_MAX,
    }
    return {
        "n": n,
        "dev_n": len(dev),
        "c1_n": len(c1),
        "median_ret3_bps": _median([row[3] for row in usable]),
        "median_ret5_bps": med5,
        "median_ret10_bps": _median([row[4] for row in usable]),
        "median_mfe5_bps": _median([row[5] for row in usable]),
        "median_mae5_bps": _median([row[6] for row in usable]),
        "dev_median_ret3_bps": _median([row[3] for row in dev]),
        "dev_median_ret5_bps": dev_med,
        "dev_median_ret10_bps": _median([row[4] for row in dev]),
        "dev_median_mfe5_bps": _median([row[5] for row in dev]),
        "dev_median_mae5_bps": _median([row[6] for row in dev]),
        "c1_median_ret3_bps": _median([row[3] for row in c1]),
        "c1_median_ret5_bps": c1_med,
        "c1_median_ret10_bps": _median([row[4] for row in c1]),
        "c1_median_mfe5_bps": _median([row[5] for row in c1]),
        "c1_median_mae5_bps": _median([row[6] for row in c1]),
        "dev_folds": dev_fold,
        "c1_folds": c1_fold,
        "top_day_share": top_day,
        "top_symbol_share": top_sym,
        "top_day": None if not day_counts else day_counts.most_common(1)[0][0],
        "top_symbol": None if not sym_counts else sym_counts.most_common(1)[0][0],
        "gates": gates,
        "pass": all(gates.values()),
    }


def _feature_split(values: list, labels: list) -> dict[str, Any]:
    arr = np.asarray(values, dtype=float)
    lab = np.asarray(labels, dtype=float)
    ok = np.isfinite(arr) & np.isfinite(lab)
    arr, lab = arr[ok], lab[ok]
    up, dn = arr[lab > 0], arr[lab <= 0]
    return {
        "n": int(arr.size),
        "up_n": int(up.size),
        "down_n": int(dn.size),
        "up_median": None if up.size == 0 else float(np.median(up)),
        "down_median": None if dn.size == 0 else float(np.median(dn)),
    }


def decide(scanned: dict[str, Any]) -> dict[str, Any]:
    dates = list(scanned["session_dates"])
    dev_folds = _folds([d for d in dates if d <= DEV_LAST])
    c1_folds = _folds([d for d in dates if d >= C1_FIRST])
    families = {name: _family(scanned["signals"][name], dev_folds, c1_folds) for name in FAMILIES}
    selected = next((name for name in FAMILIES if families[name]["pass"]), None)
    stage_rows = []
    failed = None
    for name, bucket in scanned["stages"].items():
        med = _median(bucket["ret5"])
        stage_rows.append({"stage": name, "n": len(bucket["ret5"]), "median_ret3_bps": _median(bucket["ret3"]), "median_ret5_bps": med, "median_ret10_bps": _median(bucket["ret10"])})
        if failed is None and (med is None or med <= 0):
            failed = name
    if selected is None and failed is None:
        failed = "REACCELERATION"
    feature_rows = []
    feats = scanned["features"]
    for key in ("pullback_depth_bps", "distance_ema9_bps", "distance_ema21_bps", "distance_vwap_bps", "pullback_duration_bars", "lower_low", "close_below_ema21"):
        if key in feats:
            feature_rows.append({"feature": key, **_feature_split(feats[key], feats[key + "_up"])})
    verdict = VERDICT_SUPPORTED if selected else VERDICT_NOT
    return {
        "verdict": verdict,
        "next": NEXT_PRECOMMIT if selected else NEXT_STOP,
        "selected": selected,
        "failed_transition": None if selected else failed,
        "families": families,
        "stages": stage_rows,
        "features": feature_rows,
        "dev_fold_dates": {str(k): v for k, v in Counter(dev_folds.values()).items()},
        "c1_fold_dates": {str(k): v for k, v in Counter(c1_folds.values()).items()},
    }
