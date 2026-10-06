"""Precommitted gates. Ordinal selection is earliest pass, not best PnL."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.event_time_impulse_v2_robustness_audit.metrics import _fold_of, _pack
from research.event_time_reconfirm_persistence_causal_map_v1 import ORDINALS, PRIMARY_MS, ROBUST_MS
from research.event_time_reconfirm_persistence_causal_map_v1.identity import bind
from research.event_time_volume_confirmed_impulse_entry.identity import identities as parent_identities
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18

CORE_HIGHER = ("mean_bps", "mae_mean")
CORE_LOWER = ("support_failure_rate",)


def _days(rows: list[dict[str, Any]], dates: list[str]) -> dict[str, Any]:
    counts = {day: 0 for day in dates}
    for row in rows:
        counts[row["date"]] = counts.get(row["date"], 0) + 1
    daily = [counts[day] for day in dates]
    return {
        "per_day_mean": float(np.mean(daily)) if daily else 0.0,
        "per_day_median": float(np.median(daily)) if daily else 0.0,
        "active_days": int(sum(value > 0 for value in daily)),
        "zero_days": int(sum(value == 0 for value in daily)),
    }


def _max_dd(rows: list[dict[str, Any]]) -> float:
    ordered = sorted(rows, key=lambda row: (row["date"], float(row["exit_t"]), row["symbol"]))
    equity = peak = worst = 0.0
    for row in ordered:
        equity += float(row["pnl_yen"])
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def _group(rows: list[dict[str, Any]], dates: list[str]) -> dict[str, Any]:
    fold = _fold_of(dates)
    original = set(ORIGINAL18)
    extension = set(EXTENSION17)
    folds = []
    for fold_id, name in enumerate(("FOLD_A", "FOLD_B", "FOLD_C")):
        one = _pack([row for row in rows if fold.get(row["date"]) == fold_id])
        one["fold"] = name
        folds.append(one)
    reasons = {}
    for reason in ("BREAK_SUPPORT_FAILURE", "IMPULSE_EXHAUSTED", "SESSION_FLAT", "FAIL_CLOSE_INVALID_DATA"):
        reasons[reason] = _pack([row for row in rows if row.get("reason") == reason])
    packed = _pack(rows)
    packed["max_dd"] = _max_dd(rows)
    n = int(packed["trade_n"])
    support_n = int(reasons["BREAK_SUPPORT_FAILURE"]["trade_n"])
    exhausted_n = int(reasons["IMPULSE_EXHAUSTED"]["trade_n"])
    mfe = np.asarray([float(row["mfe"]) for row in rows if row.get("mfe") is not None], dtype=float)
    mae = np.asarray([float(row["mae"]) for row in rows if row.get("mae") is not None], dtype=float)
    return {
        "all": packed,
        "ORIGINAL18": _pack([row for row in rows if row["date"] in original]),
        "EXTENSION17": _pack([row for row in rows if row["date"] in extension]),
        "folds": folds,
        "reasons": reasons,
        "support_failure_rate": None if n == 0 else support_n / n,
        "exhausted_rate": None if n == 0 else exhausted_n / n,
        "mfe_mean": None if mfe.size == 0 else float(mfe.mean()),
        "mfe_median": None if mfe.size == 0 else float(np.median(mfe)),
        "mae_mean": None if mae.size == 0 else float(mae.mean()),
        "mae_median": None if mae.size == 0 else float(np.median(mae)),
    }


def _finite(value: Any) -> Optional[float]:
    if value is None:
        return None
    number = float(value)
    if number == float("inf"):
        return 1e99
    if number != number:
        return None
    return number


def _monotonic(values: list[Optional[float]], *, higher: bool) -> bool:
    nums = [_finite(value) for value in values]
    if any(value is None for value in nums):
        return False
    pairs = zip(nums, nums[1:])
    if higher:
        return all(left <= right for left, right in pairs)
    return all(left >= right for left, right in pairs)


def _ordered(primary: dict[int, dict[str, Any]]) -> dict[str, Any]:
    blocks = [primary[ordinal] for ordinal in ORDINALS]
    quality = {
        "mean_bps": _monotonic([block["all"].get("mean_bps") for block in blocks], higher=True),
        "median_bps": _monotonic([block["all"].get("median_bps") for block in blocks], higher=True),
        "pf": _monotonic([block["all"].get("pf") for block in blocks], higher=True),
        "support_failure_rate": _monotonic([block["support_failure_rate"] for block in blocks], higher=False),
        "exhausted_rate": _monotonic([block["exhausted_rate"] for block in blocks], higher=True),
        "mae_mean": _monotonic([block["mae_mean"] for block in blocks], higher=True),
        "mfe_mean": _monotonic([block["mfe_mean"] for block in blocks], higher=True),
    }
    core = all(quality[name] for name in (*CORE_HIGHER, *CORE_LOWER))
    start = _finite(blocks[0]["all"].get("mean_bps"))
    end = _finite(blocks[-1]["all"].get("mean_bps"))
    rate_start = _finite(blocks[0]["support_failure_rate"])
    rate_end = _finite(blocks[-1]["support_failure_rate"])
    strict = bool(
        (start is not None and end is not None and end > start)
        or (rate_start is not None and rate_end is not None and rate_end < rate_start)
    )
    return {"metrics": quality, "ordered_persistence_improvement": bool(core and strict)}


def _passes(block100: dict[str, Any], block250: dict[str, Any], days: dict[str, Any]) -> bool:
    primary = block100["all"]
    robust = block250["all"]
    folds_ok = sum(1 for fold in block100["folds"] if fold["trade_n"] > 0 and float(fold["pnl"]) > 0) >= 2
    return bool(
        primary["trade_n"] >= 100
        and days["active_days"] >= 20
        and float(primary["pnl"]) > 0
        and primary.get("pf") is not None
        and float(primary["pf"]) > 1.0
        and primary.get("mean_yen") is not None
        and float(primary["mean_yen"]) > 0
        and float(block100["ORIGINAL18"]["pnl"]) >= 0
        and float(block100["EXTENSION17"]["pnl"]) >= 0
        and folds_ok
        and float(robust["pnl"]) >= 0
        and robust.get("pf") is not None
        and float(robust["pf"]) >= 1.0
    )


def build(scanned: dict[str, Any]) -> dict[str, Any]:
    dates = scanned["dates"]
    blocks: dict[str, dict[str, Any]] = {}
    frequency: dict[str, Any] = {}
    passed: dict[str, bool] = {}
    for ordinal in ORDINALS:
        for ms in (PRIMARY_MS, ROBUST_MS):
            rows = [row for row in scanned["trades"][(ordinal, ms)] if row.get("pnl_yen") is not None]
            blocks[f"R{ordinal}_{ms}"] = _group(rows, dates)
        frequency[f"R{ordinal}"] = _days(scanned["trades"][(ordinal, PRIMARY_MS)], dates)
        passed[f"R{ordinal}"] = _passes(blocks[f"R{ordinal}_100"], blocks[f"R{ordinal}_250"], frequency[f"R{ordinal}"])
    primary = {ordinal: blocks[f"R{ordinal}_100"] for ordinal in ORDINALS}
    order = _ordered(primary)
    earliest = next((f"R{ordinal}" for ordinal in ORDINALS if passed[f"R{ordinal}"]), None)
    if earliest is not None:
        verdict = "EVENT_TIME_RECONFIRM_PERSISTENCE_EXECUTABLE_STATE_FOUND_V1"
        nxt = "PRECOMMIT_EVENT_TIME_RECONFIRM_PERSISTENCE_ENTRY_V1"
    elif order["ordered_persistence_improvement"]:
        verdict = "EVENT_TIME_RECONFIRM_PERSISTENCE_MECHANISM_PRESENT_BUT_NOT_ACTIONABLE_V1"
        nxt = "STOP"
    else:
        verdict = "EVENT_TIME_RECONFIRM_PERSISTENCE_NOT_EXECUTABLY_SUPPORTED_V1"
        nxt = "STOP_EVENT_TIME_IMPULSE_FAMILY"
    parent = parent_identities()
    return {
        "verdict": verdict,
        "next": nxt,
        "earliest_executable_ordinal": earliest or "none",
        "passed": passed,
        "ordered": order,
        "blocks": blocks,
        "frequency": frequency,
        "counts": scanned["counts"],
        "identity": bind(parent["ENTRY_SIGNAL_SHA256"]),
        "parent_signal_sha256": parent["ENTRY_SIGNAL_SHA256"],
        "leakage": {
            "entry_decision_uses_only_reconfirmations_already_occurred": True,
            "future_total_ratchet_count": False,
            "future_exit_reason": False,
            "future_mfe": False,
            "future_mae": False,
            "future_hold_time": False,
            "future_profitability": False,
            "post_entry_ratchet_count_used_for_admission": False,
        },
    }
