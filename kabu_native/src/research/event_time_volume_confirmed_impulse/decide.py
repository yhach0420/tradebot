"""Apply the predeclared gates. No threshold is chosen from the outcomes."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.event_time_volume_confirmed_impulse import (
    ESSENTIALLY_ALL_SHARE,
    INFORMATION_NOT_ESTABLISHED,
    MIN_FULL_N,
    NEXT_PRECOMMIT,
    NEXT_STOP,
    VERDICT_NOT,
    VERDICT_SUPPORT,
    VERDICT_SUPPORTED,
)


def _values(rows: list[dict[str, Any]], key: str) -> np.ndarray:
    raw = [row.get(key) for row in rows]
    arr = np.asarray([np.nan if value is None else float(value) for value in raw], dtype=float)
    return arr[np.isfinite(arr)]


def describe(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    arr = _values(rows, key)
    n = int(arr.size)
    if n == 0:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "positive_fraction": None,
            "zero_fraction": None,
            "negative_fraction": None,
            "q25": None,
            "q75": None,
        }
    return {
        "n": n,
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "positive_fraction": float(np.mean(arr > 0)),
        "zero_fraction": float(np.mean(arr == 0)),
        "negative_fraction": float(np.mean(arr < 0)),
        "q25": float(np.percentile(arr, 25)),
        "q75": float(np.percentile(arr, 75)),
    }


def _gt(left: Optional[float], right: float) -> bool:
    return left is not None and left > right


def _ge(left: Optional[float], right: float) -> bool:
    return left is not None and left >= right


def _top(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    scores: dict[str, float] = {}
    total = 0.0
    for row in rows:
        value = row.get("bid_180")
        if value is None or float(value) <= 0:
            continue
        name = str(row.get(key))
        scores[name] = scores.get(name, 0.0) + float(value)
        total += float(value)
    if total <= 0 or not scores:
        return {"top": None, "top_share": None, "supplies_essentially_all_positive_edge": False}
    top = max(scores, key=lambda name: (scores[name], name))
    share = scores[top] / total
    return {
        "top": top,
        "top_share": float(share),
        "supplies_essentially_all_positive_edge": bool(share >= ESSENTIALLY_ALL_SHARE),
    }


def _horizon_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    block = {}
    for horizon in (30, 60, 180, 300):
        block[str(horizon)] = {
            "raw_mid": describe(rows, f"raw_{horizon}"),
            "bid_anchor": describe(rows, f"bid_{horizon}"),
            "ask_to_bid": describe(rows, f"atb_{horizon}"),
        }
    return block


def decide(scanned: dict[str, Any]) -> dict[str, Any]:
    rows = scanned["rows"]
    groups = {
        "FULL": [row for row in rows if row["population"] == "FULL"],
        "PRICE_BREAK_ONLY": [row for row in rows if row["population"] == "PRICE_BREAK_ONLY"],
        "VOLUME_BREAK_NO_BUY": [row for row in rows if row["population"] == "VOLUME_BREAK_NO_BUY"],
    }
    horizons = {name: _horizon_block(group) for name, group in groups.items()}
    full = groups["FULL"]
    full_bid = horizons["FULL"]["180"]["bid_anchor"]
    full_raw = horizons["FULL"]["180"]["raw_mid"]
    break_bid = horizons["PRICE_BREAK_ONLY"]["180"]["bid_anchor"]
    nobuy_bid = horizons["VOLUME_BREAK_NO_BUY"]["180"]["bid_anchor"]
    original = [row for row in full if row["lineage"] == "ORIGINAL18"]
    extension = [row for row in full if row["lineage"] == "EXTENSION17"]
    original_bid = describe(original, "bid_180")
    extension_bid = describe(extension, "bid_180")
    folds = []
    positive_folds = 0
    nonempty_folds = 0
    for fold in range(3):
        group = [row for row in full if int(row["fold"]) == fold]
        stat = describe(group, "bid_180")
        raw = describe(group, "raw_180")
        nonempty = len(group) > 0
        nonempty_folds += int(nonempty)
        positive = bool(stat["n"] > 0 and stat["mean"] is not None and stat["mean"] > 0)
        positive_folds += int(positive)
        folds.append({"fold": fold, "n": len(group), "positive_mean": positive, "bid_anchor": stat, "raw_mid": raw})
    day = _top(full, "date")
    symbol = _top(full, "symbol")
    sector = _top(full, "sector")
    fractions = [row["classified_fraction_10"] for row in full if row.get("classified_fraction_10") is not None]
    coverage = scanned["coverage"]
    windows = int(coverage["volume_windows"])
    classified = int(coverage["classified_windows"])
    support = (
        len(full) >= MIN_FULL_N
        and len(original) > 0
        and len(extension) > 0
        and nonempty_folds >= 2
    )
    gates = {
        "support": support,
        "raw_mean": _gt(full_raw["mean"], 0.0),
        "raw_median": _gt(full_raw["median"], 0.0),
        "bid_mean": _gt(full_bid["mean"], 0.0),
        "bid_median": _gt(full_bid["median"], 0.0),
        "bid_positive_gt_negative": (
            full_bid["positive_fraction"] is not None
            and full_bid["negative_fraction"] is not None
            and full_bid["positive_fraction"] > full_bid["negative_fraction"]
        ),
        "uplift_vs_break": _gt(full_bid["mean"], float(break_bid["mean"]) if break_bid["mean"] is not None else 1e99),
        "uplift_vs_no_buy": _gt(full_bid["mean"], float(nobuy_bid["mean"]) if nobuy_bid["mean"] is not None else 1e99),
        "original18": _ge(original_bid["mean"], 0.0),
        "extension17": _ge(extension_bid["mean"], 0.0),
        "folds": positive_folds >= 2,
        "no_day_monopoly": not day["supplies_essentially_all_positive_edge"],
        "no_symbol_monopoly": not symbol["supplies_essentially_all_positive_edge"],
    }
    # Uplift comparisons are strict only when the baseline mean exists. A missing baseline fails the gate.
    if break_bid["mean"] is None:
        gates["uplift_vs_break"] = False
    if nobuy_bid["mean"] is None:
        gates["uplift_vs_no_buy"] = False
    if not support:
        verdict, nxt, info = VERDICT_SUPPORT, NEXT_STOP, None
    elif all(gates.values()):
        verdict, nxt, info = VERDICT_SUPPORTED, NEXT_PRECOMMIT, None
    else:
        verdict, nxt, info = VERDICT_NOT, NEXT_STOP, INFORMATION_NOT_ESTABLISHED
    return {
        "verdict": verdict,
        "next": nxt,
        "information": info,
        "support": support,
        "pass": verdict == VERDICT_SUPPORTED,
        "n": {name: len(group) for name, group in groups.items()},
        "horizons": horizons,
        "original18": {"n": len(original), "bid_anchor_180": original_bid, "raw_mid_180": describe(original, "raw_180")},
        "extension17": {"n": len(extension), "bid_anchor_180": extension_bid, "raw_mid_180": describe(extension, "raw_180")},
        "folds": folds,
        "positive_folds": positive_folds,
        "day": day,
        "symbol": symbol,
        "sector": sector,
        "classified_volume_fraction_10s_mean": None if not fractions else float(np.mean(np.asarray(fractions, dtype=float))),
        "classified_window_fraction": None if windows == 0 else classified / windows,
        "classified_windows": classified,
        "volume_windows": windows,
        "gates": gates,
        "essentially_all_share": ESSENTIALLY_ALL_SHARE,
    }
