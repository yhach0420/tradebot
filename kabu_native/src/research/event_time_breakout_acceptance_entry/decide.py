"""Acceptance gates. The failed-break comparison cannot rescue a negative ask entry."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.event_time_breakout_acceptance_entry import (
    ENTRY_ID,
    ESSENTIALLY_ALL_SHARE,
    INFORMATION_NOT_CAPTURABLE,
    MIN_ACCEPTED_N,
    NEXT_EXIT,
    NEXT_STOP,
    NEXT_STOP_FAMILY,
    PARENT_FULL_N,
    VERDICT_MISMATCH,
    VERDICT_NOT,
    VERDICT_SUPPORT,
    VERDICT_SUPPORTED,
)
from research.event_time_breakout_acceptance_entry.contract import contract_ids
from research.event_time_volume_confirmed_impulse_entry import (
    PARENT_ATB180_MEAN,
    PARENT_ATB180_MEDIAN,
    PARENT_BID180_MEAN,
    PARENT_BID180_MEDIAN,
    PARENT_BID180_NEGATIVE,
    PARENT_BID180_POSITIVE,
)


def _values(rows: list[dict[str, Any]], key: str) -> np.ndarray:
    arr = np.asarray([np.nan if row.get(key) is None else float(row[key]) for row in rows], dtype=float)
    return arr[np.isfinite(arr)]


def describe(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    arr = _values(rows, key)
    n = int(arr.size)
    empty = {
        "n": 0,
        "mean": None,
        "median": None,
        "positive_fraction": None,
        "zero_fraction": None,
        "negative_fraction": None,
        "q25": None,
        "q75": None,
    }
    if n == 0:
        return empty
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
        value = row.get("ask_entry_180")
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


def decide(scanned: dict[str, Any]) -> dict[str, Any]:
    ids = contract_ids()
    rows = list(scanned.get("rows") or [])
    parent_bid = describe(rows, "signal_bid_180")
    parent_atb = describe(rows, "signal_atb_180")
    identity_ok = bool(scanned.get("identity_ok")) and ids["signal_sha_matches_required"] and all(
        [
            _close(float(len(rows)), float(PARENT_FULL_N)),
            _close(parent_bid["mean"], PARENT_BID180_MEAN),
            _close(parent_bid["median"], PARENT_BID180_MEDIAN),
            _close(parent_bid["positive_fraction"], PARENT_BID180_POSITIVE),
            _close(parent_bid["negative_fraction"], PARENT_BID180_NEGATIVE),
            _close(parent_atb["mean"], PARENT_ATB180_MEAN),
            _close(parent_atb["median"], PARENT_ATB180_MEDIAN),
        ]
    )
    accepted = [row for row in rows if row.get("state") == "BREAKOUT_ACCEPTED_5S"]
    failed = [row for row in rows if row.get("state") == "BREAKOUT_FAILED_5S"]
    unavailable = [row for row in rows if row.get("state") == "ACCEPTANCE_UNAVAILABLE"]
    not_accepted = [row for row in rows if row.get("state") == "BREAKOUT_NOT_ACCEPTED"]
    horizons = {str(horizon): describe(accepted, f"ask_entry_{horizon}") for horizon in (30, 60, 180, 300)}
    signal_compare = {
        str(horizon): {
            "accepted": describe(accepted, f"signal_bid_{horizon}"),
            "failed": describe(failed, f"signal_bid_{horizon}"),
        }
        for horizon in (30, 60, 180, 300)
    }
    primary = horizons["180"]
    accepted_signal = signal_compare["180"]["accepted"]
    failed_signal = signal_compare["180"]["failed"]
    original = [row for row in accepted if row.get("lineage") == "ORIGINAL18"]
    extension = [row for row in accepted if row.get("lineage") == "EXTENSION17"]
    original_stat = describe(original, "ask_entry_180")
    extension_stat = describe(extension, "ask_entry_180")
    folds = []
    positive_folds = 0
    for fold in range(3):
        group = [row for row in accepted if int(row.get("fold", -1)) == fold]
        stat = describe(group, "ask_entry_180")
        positive = bool(stat["n"] > 0 and stat["mean"] is not None and stat["mean"] > 0)
        positive_folds += int(positive)
        folds.append({"fold": fold, "accepted_n": len(group), "positive_mean": positive, "ask_entry_180": stat})
    day = _top(accepted, "date")
    symbol = _top(accepted, "symbol")
    sector = _top(accepted, "sector")
    reasons: dict[str, int] = {}
    for row in unavailable:
        reasons[str(row.get("reason"))] = reasons.get(str(row.get("reason")), 0) + 1
    support = len(accepted) >= MIN_ACCEPTED_N and len(original) > 0 and len(extension) > 0 and all(item["accepted_n"] > 0 for item in folds)
    separation = (
        accepted_signal["mean"] is not None
        and failed_signal["mean"] is not None
        and accepted_signal["mean"] > failed_signal["mean"]
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
        "separation": separation,
        "no_day_monopoly": not day["supplies_essentially_all_positive_edge"],
        "no_symbol_monopoly": not symbol["supplies_essentially_all_positive_edge"],
    }
    if not identity_ok:
        verdict, nxt, info, frozen = VERDICT_MISMATCH, NEXT_STOP, None, False
    elif not support:
        verdict, nxt, info, frozen = VERDICT_SUPPORT, NEXT_STOP, None, False
    elif all(gates.values()):
        verdict, nxt, info, frozen = VERDICT_SUPPORTED, NEXT_EXIT, None, True
    else:
        verdict, nxt, info, frozen = VERDICT_NOT, NEXT_STOP_FAMILY, INFORMATION_NOT_CAPTURABLE, False
    return {
        "verdict": verdict,
        "next": nxt,
        "information": info,
        "pass": frozen,
        "ENTRY_FROZEN": frozen,
        "ENTRY_ID": ENTRY_ID if frozen else None,
        "identities": ids,
        "identity_ok": identity_ok,
        "signal_n": len(rows),
        "accepted_n": len(accepted),
        "failed_n": len(failed),
        "unavailable_n": len(unavailable),
        "not_accepted_n": len(not_accepted),
        "accepted_rate": None if not rows else len(accepted) / len(rows),
        "unavailable_reasons": reasons,
        "confirm_latency": describe(accepted, "confirm_latency_sec"),
        "horizons": horizons,
        "signal_compare": signal_compare,
        "original18": {"accepted_n": len(original), "ask_entry_180": original_stat},
        "extension17": {"accepted_n": len(extension), "ask_entry_180": extension_stat},
        "folds": folds,
        "positive_folds": positive_folds,
        "day": day,
        "symbol": symbol,
        "sector": sector,
        "separation": separation,
        "support": support,
        "gates": gates,
    }
