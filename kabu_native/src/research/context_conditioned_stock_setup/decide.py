"""Frozen interaction gates. Median is reported and is not the pass test."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

import numpy as np

from research.context_conditioned_stock_setup import (
    C1_FIRST,
    DEV_LAST,
    INFORMATION_INSUFFICIENT,
    MIN_ALIGNED_N,
    NEXT_DISCOVERY_STOP,
    NEXT_PRECOMMIT,
    NEXT_STOP,
    VERDICT_NOT,
    VERDICT_SUPPORT,
    VERDICT_SUPPORTED,
)
from research.stock_specific_sequential_setup.decide import _folds


def _arr(rows: list[dict[str, Any]], key: str) -> np.ndarray:
    vals = [row.get(key) for row in rows]
    out = np.asarray([np.nan if v is None else float(v) for v in vals], dtype=float)
    return out[np.isfinite(out)]


def describe(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    arr = _arr(rows, key)
    n = int(arr.size)
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "positive_fraction": None, "zero_fraction": None, "negative_fraction": None}
    return {
        "n": n,
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "positive_fraction": float(np.mean(arr > 0)),
        "zero_fraction": float(np.mean(arr == 0)),
        "negative_fraction": float(np.mean(arr < 0)),
    }


def _period(day: str) -> str:
    return "DEV" if day <= DEV_LAST else "C1"


def _delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return float(a) - float(b)


def _top(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    buckets: dict[str, float] = defaultdict(float)
    total = 0.0
    for row in rows:
        val = row.get("ret5")
        if val is None or float(val) <= 0:
            continue
        buckets[str(row.get(key))] += float(val)
        total += float(val)
    if total <= 0 or not buckets:
        return {"top": None, "top_share": None, "supplies_all_positive_edge": False}
    top, amount = max(buckets.items(), key=lambda item: item[1])
    share = float(amount) / float(total)
    return {"top": top, "top_share": share, "supplies_all_positive_edge": share >= 1.0 - 1e-12}


def _fold_table(rows: list[dict[str, Any]], mapping: dict[str, int], period: str) -> list[dict[str, Any]]:
    grouped: dict[int, list] = {0: [], 1: [], 2: []}
    for row in rows:
        fold = mapping.get(row["date"])
        if fold is not None:
            grouped[int(fold)].append(row)
    out = []
    for fold in range(3):
        stat = describe(grouped[fold], "ret5")
        out.append({"period": period, "fold": fold, "positive_mean": stat["n"] > 0 and stat["mean"] is not None and stat["mean"] > 0, **stat})
    return out


def decide(scanned: dict[str, Any]) -> dict[str, Any]:
    aligned = list(scanned["aligned"])
    other = list(scanned["not_aligned"])
    dates = list(scanned["session_dates"])
    dev_map = _folds([d for d in dates if d <= DEV_LAST])
    c1_map = _folds([d for d in dates if d >= C1_FIRST])
    horizons = {}
    for label, rows in (("aligned", aligned), ("not_aligned", other)):
        horizons[label] = {"ret3": describe(rows, "ret3"), "ret5": describe(rows, "ret5"), "ret10": describe(rows, "ret10")}
    dev_a = [row for row in aligned if _period(row["date"]) == "DEV"]
    dev_b = [row for row in other if _period(row["date"]) == "DEV"]
    c1_a = [row for row in aligned if _period(row["date"]) == "C1"]
    c1_b = [row for row in other if _period(row["date"]) == "C1"]
    dev_folds = _fold_table(dev_a, dev_map, "DEV")
    c1_folds = _fold_table(c1_a, c1_map, "C1")
    a5 = horizons["aligned"]["ret5"]
    b5 = horizons["not_aligned"]["ret5"]
    dev5 = describe(dev_a, "ret5")
    dev_b5 = describe(dev_b, "ret5")
    c15 = describe(c1_a, "ret5")
    c1_b5 = describe(c1_b, "ret5")
    delta = _delta(a5["mean"], b5["mean"])
    dev_delta = _delta(dev5["mean"], dev_b5["mean"])
    c1_delta = _delta(c15["mean"], c1_b5["mean"])
    day = _top(aligned, "date")
    symbol = _top(aligned, "symbol")
    sector = _top(aligned, "sector")
    fold_support = all(row["n"] > 0 for row in dev_folds + c1_folds)
    support = len(aligned) >= MIN_ALIGNED_N and int(a5["n"]) >= MIN_ALIGNED_N and dev5["n"] > 0 and c15["n"] > 0 and fold_support
    gates = {
        "support": support,
        "mean_positive": a5["mean"] is not None and a5["mean"] > 0,
        "positive_gt_negative": a5["positive_fraction"] is not None and a5["negative_fraction"] is not None and a5["positive_fraction"] > a5["negative_fraction"],
        "interaction_overall": delta is not None and delta > 0,
        "dev_mean": dev5["mean"] is not None and dev5["mean"] >= 0,
        "c1_mean": c15["mean"] is not None and c15["mean"] >= 0,
        "one_period_strict": (dev5["mean"] is not None and dev5["mean"] > 0) or (c15["mean"] is not None and c15["mean"] > 0),
        "dev_interaction": dev_delta is not None and dev_delta > 0,
        "c1_interaction": c1_delta is not None and c1_delta > 0,
        "dev_folds": sum(1 for row in dev_folds if row["positive_mean"]) >= 2,
        "c1_folds": sum(1 for row in c1_folds if row["positive_mean"]) >= 2,
        "no_day_monopoly": not day["supplies_all_positive_edge"],
        "no_symbol_monopoly": not symbol["supplies_all_positive_edge"],
        "no_sector_monopoly": not sector["supplies_all_positive_edge"],
    }
    if not support:
        verdict, nxt = VERDICT_SUPPORT, NEXT_STOP
    elif all(gates.values()):
        verdict, nxt = VERDICT_SUPPORTED, NEXT_PRECOMMIT
    else:
        verdict, nxt = VERDICT_NOT, NEXT_DISCOVERY_STOP
    return {
        "verdict": verdict,
        "next": nxt,
        "information": INFORMATION_INSUFFICIENT if verdict == VERDICT_NOT else None,
        "support": support,
        "aligned_event_n": len(aligned),
        "not_aligned_event_n": len(other),
        "horizons": horizons,
        "dev": {"aligned": dev5, "not_aligned": dev_b5, "delta": dev_delta, "aligned_n": len(dev_a)},
        "c1": {"aligned": c15, "not_aligned": c1_b5, "delta": c1_delta, "aligned_n": len(c1_a)},
        "delta": delta,
        "dev_folds": dev_folds,
        "c1_folds": c1_folds,
        "relative": describe(aligned, "relative_ret5"),
        "day": day,
        "symbol": symbol,
        "sector": sector,
        "gates": gates,
        "pass": verdict == VERDICT_SUPPORTED,
        "fold_date_counts": {"DEV": dict(Counter(dev_map.values())), "C1": dict(Counter(c1_map.values()))},
    }
