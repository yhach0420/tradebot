"""Economic gates and one primary failure label. Thresholds are not searched."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.event_time_impulse_complete_strategy_v2 import (
    CONCENTRATION_CAP,
    MIN_TRADES,
    NEXT_AUDIT,
    NEXT_STOP,
    VERDICT_NOT,
    VERDICT_SUPPORT,
    VERDICT_SUPPORTED,
)
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18


def _finite(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("pnl_yen") is not None]


def _pf(rows: list[dict[str, Any]]) -> Optional[float]:
    profit = sum(float(row["pnl_yen"]) for row in rows if float(row["pnl_yen"]) > 0)
    loss = sum(float(row["pnl_yen"]) for row in rows if float(row["pnl_yen"]) < 0)
    if loss == 0:
        return None if profit == 0 else float("inf")
    return profit / abs(loss)


def _pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    priced = _finite(rows)
    yen = np.asarray([float(row["pnl_yen"]) for row in priced], dtype=float)
    bps = np.asarray([float(row["bps"]) for row in priced], dtype=float)
    hold = np.asarray([float(row["hold_sec"]) for row in priced if row.get("hold_sec") is not None], dtype=float)
    if yen.size == 0:
        return {"trade_n": 0, "pnl": 0.0, "pf": None, "mean_yen": None, "median_yen": None, "mean_bps": None, "median_bps": None, "win_n": 0, "loss_n": 0, "flat_n": 0, "win_rate": None, "mean_hold": None, "median_hold": None, "max_drawdown": None}
    order = np.argsort(np.asarray([float(row["exit_t"]) for row in priced], dtype=float), kind="mergesort")
    equity = np.cumsum(yen[order])
    peak = np.maximum.accumulate(equity)
    return {
        "trade_n": int(yen.size),
        "pnl": float(yen.sum()),
        "pf": _pf(priced),
        "mean_yen": float(yen.mean()),
        "median_yen": float(np.median(yen)),
        "mean_bps": float(bps.mean()),
        "median_bps": float(np.median(bps)),
        "win_n": int(np.sum(yen > 0)),
        "loss_n": int(np.sum(yen < 0)),
        "flat_n": int(np.sum(yen == 0)),
        "win_rate": float(np.mean(yen > 0)),
        "mean_hold": None if hold.size == 0 else float(hold.mean()),
        "median_hold": None if hold.size == 0 else float(np.median(hold)),
        "max_drawdown": float(np.max(peak - equity)) if equity.size else 0.0,
    }


def _share(pairs: list[tuple[str, float]]) -> dict[str, Any]:
    positive = [(key, value) for key, value in pairs if value > 0]
    total = sum(value for _key, value in positive)
    if total <= 0:
        return {"top": None, "share": None, "over_cap": False}
    best_key = None
    best = -1.0
    for key in {key for key, _value in positive}:
        got = sum(value for name, value in positive if name == key)
        if got > best:
            best = got
            best_key = key
    share = best / total
    return {"top": best_key, "share": share, "over_cap": bool(share >= CONCENTRATION_CAP)}


def _failure(priced: list[dict[str, Any]], counts: dict[str, int], pack: dict[str, Any]) -> Optional[str]:
    losers = [row for row in priced if float(row["pnl_yen"]) < 0]
    loss = abs(sum(float(row["pnl_yen"]) for row in losers))
    if loss <= 0:
        return "F_MIXED"
    session = abs(sum(float(row["pnl_yen"]) for row in losers if row["reason"] == "SESSION_FLAT"))
    adverse = abs(sum(float(row["pnl_yen"]) for row in losers if row["reason"] != "SESSION_FLAT" and (row.get("mfe") is None or float(row["mfe"]) <= 0)))
    giveback = abs(sum(float(row["pnl_yen"]) for row in losers if row["reason"] != "SESSION_FLAT" and row.get("mfe") is not None and float(row["mfe"]) > 0))
    shares = {"A_ENTRY_IMMEDIATE_ADVERSE": adverse / loss, "B_EXIT_GIVEBACK": giveback / loss, "session": session / loss}
    raw = max(int(counts.get("raw_entry") or 0), 1)
    if int(counts.get("cap") or 0) / raw >= 0.50 and float(pack["pnl"]) > 0:
        return "E_PORTFOLIO_OCCUPANCY"
    top = max(shares["A_ENTRY_IMMEDIATE_ADVERSE"], shares["B_EXIT_GIVEBACK"], shares["session"])
    if top < 0.50:
        return "F_MIXED"
    if shares["A_ENTRY_IMMEDIATE_ADVERSE"] >= 0.50 and shares["A_ENTRY_IMMEDIATE_ADVERSE"] >= shares["B_EXIT_GIVEBACK"]:
        return "A_ENTRY_IMMEDIATE_ADVERSE"
    if shares["B_EXIT_GIVEBACK"] >= 0.50:
        return "B_EXIT_GIVEBACK"
    return "F_MIXED"


def decide(scanned: dict[str, Any]) -> dict[str, Any]:
    trades = scanned["trades"]
    priced = _finite(trades)
    pack = _pack(priced)
    dates = list(scanned["dates"])
    cuts = [int(round(i * len(dates) / 3)) for i in range(4)]
    fold_of = {}
    for fold, (lo, hi) in enumerate(zip(cuts, cuts[1:])):
        for day in dates[lo:hi]:
            fold_of[day] = fold
    original_days = set(ORIGINAL18)
    extension_days = set(EXTENSION17)
    groups = {
        "ORIGINAL18": [row for row in priced if row["date"] in original_days],
        "EXTENSION17": [row for row in priced if row["date"] in extension_days],
    }
    folds = []
    positive_folds = 0
    for fold in range(3):
        rows = [row for row in priced if fold_of.get(row["date"]) == fold]
        one = _pack(rows)
        one["fold"] = fold
        positive_folds += int(one["pnl"] > 0 and one["trade_n"] > 0)
        folds.append(one)
    reasons = {}
    for reason in ("BREAK_SUPPORT_FAILURE", "IMPULSE_EXHAUSTED", "SESSION_FLAT", "FAIL_CLOSE_INVALID_DATA"):
        rows = [row for row in priced if row["reason"] == reason]
        reasons[reason] = {**_pack(rows), "mfe_mean": _mean(rows, "mfe"), "mae_mean": _mean(rows, "mae"), "giveback_mean": _mean(rows, "giveback")}
    mfe = np.asarray([float(row["mfe"]) for row in priced if row.get("mfe") is not None], dtype=float)
    mae = np.asarray([float(row["mae"]) for row in priced if row.get("mae") is not None], dtype=float)
    give = np.asarray([float(row["giveback"]) for row in priced if row.get("giveback") is not None], dtype=float)
    day_counts = {day: 0 for day in dates}
    for row in priced:
        day_counts[row["date"]] = day_counts.get(row["date"], 0) + 1
    streak = 0
    worst = 0
    for day in dates:
        if day_counts[day] == 0:
            streak += 1
            worst = max(worst, streak)
        else:
            streak = 0
    concentration = {
        "trade": _share([(str(i), float(row["pnl_yen"])) for i, row in enumerate(priced)]),
        "day": _share([(row["date"], float(row["pnl_yen"])) for row in priced]),
        "symbol": _share([(row["symbol"], float(row["pnl_yen"])) for row in priced]),
    }
    represented = len(groups["ORIGINAL18"]) > 0 and len(groups["EXTENSION17"]) > 0
    passed = bool(
        pack["trade_n"] >= MIN_TRADES
        and pack["pnl"] > 0
        and pack["pf"] is not None
        and pack["pf"] > 1.0
        and pack["mean_yen"] is not None
        and pack["mean_yen"] > 0
        and _pack(groups["ORIGINAL18"])["pnl"] >= 0
        and _pack(groups["EXTENSION17"])["pnl"] >= 0
        and represented
        and positive_folds >= 2
        and not any(item["over_cap"] for item in concentration.values())
    )
    if pack["trade_n"] < MIN_TRADES:
        verdict, nxt, failure = VERDICT_SUPPORT, NEXT_STOP, None
    elif passed:
        verdict, nxt, failure = VERDICT_SUPPORTED, NEXT_AUDIT, None
    else:
        verdict, nxt, failure = VERDICT_NOT, NEXT_STOP, _failure(priced, scanned["counts"], pack)
    daily = [day_counts[day] for day in dates]
    return {
        "verdict": verdict,
        "next": nxt,
        "pass": passed,
        "primary_failure": failure,
        "economics": pack,
        "original": _pack(groups["ORIGINAL18"]),
        "extension": _pack(groups["EXTENSION17"]),
        "folds": folds,
        "reasons": reasons,
        "concentration": concentration,
        "path": {
            "mfe_mean": None if mfe.size == 0 else float(mfe.mean()),
            "mfe_median": None if mfe.size == 0 else float(np.median(mfe)),
            "mae_mean": None if mae.size == 0 else float(mae.mean()),
            "mae_median": None if mae.size == 0 else float(np.median(mae)),
            "giveback_mean": None if give.size == 0 else float(give.mean()),
            "giveback_median": None if give.size == 0 else float(np.median(give)),
            "mfe_5": int(np.sum(mfe >= 5)) if mfe.size else 0,
            "mfe_10": int(np.sum(mfe >= 10)) if mfe.size else 0,
            "mfe_20": int(np.sum(mfe >= 20)) if mfe.size else 0,
            "mfe_30": int(np.sum(mfe >= 30)) if mfe.size else 0,
            "mae_5": int(np.sum(mae <= -5)) if mae.size else 0,
            "mae_10": int(np.sum(mae <= -10)) if mae.size else 0,
            "mae_20": int(np.sum(mae <= -20)) if mae.size else 0,
            "mae_30": int(np.sum(mae <= -30)) if mae.size else 0,
            "mfe_positive_realized_negative": sum(1 for row in priced if row.get("mfe") is not None and float(row["mfe"]) > 0 and float(row["bps"]) < 0),
            "mfe_20_realized_negative": sum(1 for row in priced if row.get("mfe") is not None and float(row["mfe"]) >= 20 and float(row["bps"]) < 0),
            "mfe_30_realized_negative": sum(1 for row in priced if row.get("mfe") is not None and float(row["mfe"]) >= 30 and float(row["bps"]) < 0),
            "profit_then_failure": sum(1 for row in priced if row.get("mfe") is not None and float(row["mfe"]) > 0 and row["reason"] in ("BREAK_SUPPORT_FAILURE", "IMPULSE_EXHAUSTED")),
        },
        "frequency": {
            "total_trades": pack["trade_n"],
            "per_day_mean": float(np.mean(daily)) if daily else 0.0,
            "per_day_median": float(np.median(daily)) if daily else 0.0,
            "active_days": int(sum(1 for value in daily if value > 0)),
            "zero_days": int(sum(1 for value in daily if value == 0)),
            "active_fraction": float(np.mean(np.asarray(daily) > 0)) if daily else 0.0,
            "max_zero_streak": worst,
            "daily": {day: day_counts[day] for day in dates},
        },
    }


def _mean(rows: list[dict[str, Any]], key: str) -> Optional[float]:
    vals = [float(row[key]) for row in rows if row.get(key) is not None]
    if not vals:
        return None
    return float(np.mean(vals))
