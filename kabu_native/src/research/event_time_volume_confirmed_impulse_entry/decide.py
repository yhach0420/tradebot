"""Entry gates. A failed fill markout is not rescued by the expired-signal comparison."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.event_time_volume_confirmed_impulse_entry import (
    ENTRY_ID,
    ESSENTIALLY_ALL_SHARE,
    MIN_FILLED_N,
    NEXT_EXIT,
    NEXT_STOP,
    NEXT_STOP_EXECUTION,
    PARENT_ATB180_MEAN,
    PARENT_ATB180_MEDIAN,
    PARENT_BID180_MEAN,
    PARENT_BID180_MEDIAN,
    PARENT_BID180_NEGATIVE,
    PARENT_BID180_POSITIVE,
    PARENT_FULL_N,
    VERDICT_MISMATCH,
    VERDICT_NOT,
    VERDICT_SUPPORT,
    VERDICT_SUPPORTED,
)
from research.event_time_volume_confirmed_impulse_entry.identity import identities


def _values(rows: list[dict[str, Any]], key: str) -> np.ndarray:
    arr = np.asarray([np.nan if row.get(key) is None else float(row[key]) for row in rows], dtype=float)
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
            "max": None,
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
        "max": float(np.max(arr)),
    }


def _close(got: Optional[float], expected: float) -> bool:
    return got is not None and abs(float(got) - float(expected)) <= 1e-6


def _gt(value: Optional[float], bound: float) -> bool:
    return value is not None and value > bound


def _ge(value: Optional[float], bound: float) -> bool:
    return value is not None and value >= bound


def _top(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    scores: dict[str, float] = {}
    total = 0.0
    for row in rows:
        value = row.get("fill_bid_180")
        if value is None or float(value) <= 0:
            continue
        name = str(row.get(key))
        scores[name] = scores.get(name, 0.0) + float(value)
        total += float(value)
    if total <= 0 or not scores:
        return {"top": None, "top_share": None, "supplies_essentially_all_positive_edge": False}
    top = max(scores, key=lambda name: (scores[name], name))
    share = scores[top] / total
    return {"top": top, "top_share": float(share), "supplies_essentially_all_positive_edge": bool(share >= ESSENTIALLY_ALL_SHARE)}


def _breakdown(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    names = sorted({str(row[key]) for row in rows})
    out = []
    for name in names:
        group = [row for row in rows if str(row[key]) == name]
        filled_n = sum(1 for row in group if row["filled"])
        out.append(
            {
                "scope": key,
                "name": name,
                "signal_n": len(group),
                "filled_n": filled_n,
                "expired_n": len(group) - filled_n,
                "fill_rate": filled_n / len(group),
            }
        )
    return out


def decide(scanned: dict[str, Any]) -> dict[str, Any]:
    ids = identities()
    rows = list(scanned.get("rows") or [])
    parent_bid = describe(rows, "signal_bid_180")
    parent_atb = describe(rows, "signal_atb_180")
    identity_stats = {
        "full_n": _close(float(len(rows)), float(PARENT_FULL_N)) and bool(scanned.get("identity_ok")),
        "bid_mean": _close(parent_bid["mean"], PARENT_BID180_MEAN),
        "bid_median": _close(parent_bid["median"], PARENT_BID180_MEDIAN),
        "bid_positive": _close(parent_bid["positive_fraction"], PARENT_BID180_POSITIVE),
        "bid_negative": _close(parent_bid["negative_fraction"], PARENT_BID180_NEGATIVE),
        "atb_mean": _close(parent_atb["mean"], PARENT_ATB180_MEAN),
        "atb_median": _close(parent_atb["median"], PARENT_ATB180_MEDIAN),
    }
    identity_ok = all(identity_stats.values())
    filled = [row for row in rows if row.get("filled")]
    expired = [row for row in rows if not row.get("filled")]
    horizons = {str(horizon): describe(filled, f"fill_bid_{horizon}") for horizon in (30, 60, 180, 300)}
    primary = horizons["180"]
    original = [row for row in filled if row.get("lineage") == "ORIGINAL18"]
    extension = [row for row in filled if row.get("lineage") == "EXTENSION17"]
    original_stat = describe(original, "fill_bid_180")
    extension_stat = describe(extension, "fill_bid_180")
    folds = []
    positive_folds = 0
    for fold in range(3):
        group = [row for row in filled if int(row.get("fold", -1)) == fold]
        stat = describe(group, "fill_bid_180")
        positive = bool(stat["n"] > 0 and stat["mean"] is not None and stat["mean"] > 0)
        positive_folds += int(positive)
        folds.append({"fold": fold, "filled_n": len(group), "positive_mean": positive, "fill_to_bid_180": stat})
    day = _top(filled, "date")
    symbol = _top(filled, "symbol")
    sector = _top(filled, "sector")
    support = (
        len(filled) >= MIN_FILLED_N
        and len(original) > 0
        and len(extension) > 0
        and all(item["filled_n"] > 0 for item in folds)
    )
    gates = {
        "support": support,
        "mean": _gt(primary["mean"], 0.0),
        "median": _gt(primary["median"], 0.0),
        "positive_gt_negative": (
            primary["positive_fraction"] is not None
            and primary["negative_fraction"] is not None
            and primary["positive_fraction"] > primary["negative_fraction"]
        ),
        "original18": _ge(original_stat["mean"], 0.0),
        "extension17": _ge(extension_stat["mean"], 0.0),
        "folds": positive_folds >= 2,
        "no_day_monopoly": not day["supplies_essentially_all_positive_edge"],
        "no_symbol_monopoly": not symbol["supplies_essentially_all_positive_edge"],
    }
    if not identity_ok:
        verdict, nxt, frozen = VERDICT_MISMATCH, NEXT_STOP, False
    elif not support:
        verdict, nxt, frozen = VERDICT_SUPPORT, NEXT_STOP, False
    elif all(gates.values()):
        verdict, nxt, frozen = VERDICT_SUPPORTED, NEXT_EXIT, True
    else:
        verdict, nxt, frozen = VERDICT_NOT, NEXT_STOP_EXECUTION, False
    mae = _values(filled, "pre_fill_mae_bps")
    return {
        "verdict": verdict,
        "next": nxt,
        "pass": frozen,
        "ENTRY_FROZEN": frozen,
        "ENTRY_ID": ENTRY_ID if frozen else None,
        "identities": ids,
        "identity_ok": identity_ok,
        "identity_stats": identity_stats,
        "parent_signal_bid_180": parent_bid,
        "parent_signal_atb_180": parent_atb,
        "signal_n": len(rows),
        "filled_n": len(filled),
        "expired_n": len(expired),
        "fill_rate": None if not rows else len(filled) / len(rows),
        "latency": describe(filled, "fill_latency_sec"),
        "horizons": horizons,
        "filled_signal_bid_180": describe(filled, "signal_bid_180"),
        "expired_signal_bid_180": describe(expired, "signal_bid_180"),
        "original18": {"filled_n": len(original), "fill_to_bid_180": original_stat},
        "extension17": {"filled_n": len(extension), "fill_to_bid_180": extension_stat},
        "folds": folds,
        "positive_folds": positive_folds,
        "day": day,
        "symbol": symbol,
        "sector": sector,
        "by_date": _breakdown(rows, "date"),
        "by_symbol": _breakdown(rows, "symbol"),
        "by_sector": _breakdown(rows, "sector"),
        "prefill": {
            "mfe": describe(filled, "pre_fill_mfe_bps"),
            "mae": describe(filled, "pre_fill_mae_bps"),
            "deterioration_fraction": None if int(mae.size) == 0 else float(np.mean(mae < 0)),
            "signal_bid_to_fill": describe(filled, "signal_bid_to_fill_bps"),
            "signal_mid_to_fill": describe(filled, "signal_mid_to_fill_bps"),
        },
        "support": support,
        "gates": gates,
    }
