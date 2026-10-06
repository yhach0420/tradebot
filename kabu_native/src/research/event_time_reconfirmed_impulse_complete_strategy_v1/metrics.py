"""Gates for the first-reconfirm candidate. Latency cuts were fixed before the scan."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.event_time_impulse_v2_robustness_audit.metrics import _fold_of, _friction, _pack, _share
from research.event_time_reconfirmed_impulse_complete_strategy_v1.identity import bind
from research.event_time_volume_confirmed_impulse_entry.identity import identities as parent_identities
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18

PRIMARY_MS = 100
ROBUST_MS = 250


def _group(rows: list[dict[str, Any]], dates: list[str]) -> dict[str, Any]:
    fold = _fold_of(dates)
    original = set(ORIGINAL18)
    extension = set(EXTENSION17)
    folds = []
    for fold_id in range(3):
        one = _pack([row for row in rows if fold.get(row["date"]) == fold_id])
        one["fold"] = fold_id
        folds.append(one)
    reasons = {}
    for reason in ("BREAK_SUPPORT_FAILURE", "IMPULSE_EXHAUSTED", "SESSION_FLAT", "FAIL_CLOSE_INVALID_DATA"):
        reasons[reason] = _pack([row for row in rows if row.get("reason") == reason])
    ratchets = []
    for label, pred in (("0", lambda n: n == 0), ("1", lambda n: n == 1), ("2", lambda n: n == 2), ("3", lambda n: n == 3), ("4+", lambda n: n >= 4)):
        ratchets.append({"post_ratchets": label, **_pack([row for row in rows if pred(int(row.get("post_ratchets") or 0))])})
    mfe = np.asarray([float(row["mfe"]) for row in rows if row.get("mfe") is not None], dtype=float)
    mae = np.asarray([float(row["mae"]) for row in rows if row.get("mae") is not None], dtype=float)
    return {
        "all": _pack(rows),
        "ORIGINAL18": _pack([row for row in rows if row["date"] in original]),
        "EXTENSION17": _pack([row for row in rows if row["date"] in extension]),
        "folds": folds,
        "reasons": reasons,
        "ratchets": ratchets,
        "path": {
            "mfe_mean": None if mfe.size == 0 else float(mfe.mean()),
            "mfe_median": None if mfe.size == 0 else float(np.median(mfe)),
            "mae_mean": None if mae.size == 0 else float(mae.mean()),
            "mae_median": None if mae.size == 0 else float(np.median(mae)),
            "mfe_5": int(np.sum(mfe >= 5)) if mfe.size else 0,
            "mfe_10": int(np.sum(mfe >= 10)) if mfe.size else 0,
            "mfe_20": int(np.sum(mfe >= 20)) if mfe.size else 0,
            "mfe_30": int(np.sum(mfe >= 30)) if mfe.size else 0,
            "mfe_positive_realized_negative": sum(1 for row in rows if row.get("mfe") is not None and float(row["mfe"]) > 0 and float(row["bps"]) < 0),
            "mfe_20_realized_negative": sum(1 for row in rows if row.get("mfe") is not None and float(row["mfe"]) >= 20 and float(row["bps"]) < 0),
        },
    }


def _days(rows: list[dict[str, Any]], dates: list[str]) -> dict[str, Any]:
    counts = {day: 0 for day in dates}
    for row in rows:
        counts[row["date"]] = counts.get(row["date"], 0) + 1
    daily = [counts[day] for day in dates]
    streak = worst = 0
    for value in daily:
        if value == 0:
            streak += 1
            worst = max(worst, streak)
        else:
            streak = 0
    return {
        "per_day_mean": float(np.mean(daily)) if daily else 0.0,
        "per_day_median": float(np.median(daily)) if daily else 0.0,
        "active_days": int(sum(value > 0 for value in daily)),
        "zero_days": int(sum(value == 0 for value in daily)),
        "max_zero_streak": worst,
    }


def _positive(stat: dict[str, Any], strict_pf: bool) -> bool:
    pnl = float(stat.get("pnl") or 0.0)
    pf = stat.get("pf")
    mean = stat.get("mean_yen")
    if strict_pf:
        return bool(pnl > 0 and pf is not None and pf > 1.0 and mean is not None and mean > 0)
    return bool(pnl >= 0 and pf is not None and pf >= 1.0)


def build(scanned: dict[str, Any]) -> dict[str, Any]:
    dates = scanned["dates"]
    blocks = {str(ms): _group([row for row in scanned["by_latency"][ms] if row.get("pnl_yen") is not None], dates) for ms in scanned["by_latency"]}
    zero = blocks["0"]
    primary = blocks["100"]
    robust = blocks["250"]
    frequency = _days(list(scanned["by_latency"][0]), dates)
    support = zero["all"]["trade_n"] >= 100 and frequency["active_days"] >= 20
    folds_ok = sum(1 for fold in primary["folds"] if float(fold["pnl"]) > 0 and fold["trade_n"] > 0) >= 2
    primary_ok = _positive(primary["all"], True) and float(primary["ORIGINAL18"]["pnl"]) >= 0 and float(primary["EXTENSION17"]["pnl"]) >= 0 and folds_ok
    robust_ok = _positive(robust["all"], False)
    zero_ok = _positive(zero["all"], True)
    parent = parent_identities()
    bound = bind(parent["ENTRY_SIGNAL_SHA256"])
    if not support:
        verdict = "EVENT_TIME_RECONFIRMED_IMPULSE_INSUFFICIENT_SUPPORT_V1"
    elif not zero_ok:
        verdict = "EVENT_TIME_FIRST_RECONFIRM_MECHANISM_NOT_SUPPORTED_V1"
    elif not primary_ok:
        verdict = "EVENT_TIME_FIRST_RECONFIRM_ZERO_LATENCY_ONLY_V1"
    elif not robust_ok:
        verdict = "EVENT_TIME_FIRST_RECONFIRM_LATENCY_FRAGILE_V1"
    else:
        verdict = "EVENT_TIME_RECONFIRMED_IMPULSE_EXECUTABLE_COMPLETE_STRATEGY_SUPPORTED_V1"
    frozen = verdict.endswith("SUPPORTED_V1")
    rows100 = [row for row in scanned["by_latency"][100] if row.get("pnl_yen") is not None]
    return {
        "verdict": verdict,
        "next": "ROBUSTNESS_AUDIT_EVENT_TIME_RECONFIRMED_IMPULSE_V1" if frozen else "STOP",
        "frozen": frozen,
        "support": support,
        "blocks": blocks,
        "counts": {str(ms): scanned["counts"][ms] for ms in scanned["counts"]},
        "frequency": frequency,
        "friction_100": _friction(rows100),
        "concentration_100": {
            "trade": _share([{"pnl_yen": row["pnl_yen"], "trade": str(i)} for i, row in enumerate(rows100)], "trade"),
            "day": _share(rows100, "date"),
            "symbol": _share(rows100, "symbol"),
        },
        "identity": bound,
        "parent_signal_sha256": parent["ENTRY_SIGNAL_SHA256"],
    }
