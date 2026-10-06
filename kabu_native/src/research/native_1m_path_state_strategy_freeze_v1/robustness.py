"""Post-freeze robustness diagnostics. Never retune thresholds."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.freeze_group_mechanism_definitions_v1.rca import subset_econ, symbol_contribution, winner_concentration
from research.native_1m_path_state_strategy_freeze_v1 import EVAL_BLOCKS, X1_TAX_BPS
from research.native_path_state_discrimination_v1.features import mins_from_open


def _x1(mean_x0: float | None) -> float | None:
    if mean_x0 is None:
        return None
    return float(mean_x0) - X1_TAX_BPS


def robustness(trades: list[dict[str, Any]]) -> dict[str, Any]:
    xs = [t for t in trades if str(t.get("block")) in EVAL_BLOCKS]
    if not xs:
        return {"ok": False, "acceptable": False}
    x0 = np.asarray([float(t["x0_bps"]) for t in xs], dtype=float)
    tail = winner_concentration(xs)

    def drop_idx(keep: list[bool]) -> dict[str, Any]:
        left = [t for t, k in zip(xs, keep) if k]
        e = subset_econ(left, label="remaining")
        return {
            "remaining_n": len(left),
            "mean_x0_bps": e.get("mean_x0_bps"),
            "mean_x1_bps": e.get("mean_x1_bps"),
            "PF": e.get("PF"),
        }

    best_i = int(np.argmax(x0))
    k1 = max(1, int(np.ceil(0.01 * x0.size)))
    k5 = max(1, int(np.ceil(0.05 * x0.size)))
    order = np.argsort(-x0)
    keep_best = [i != best_i for i in range(x0.size)]
    keep_1 = [True] * int(x0.size)
    keep_5 = [True] * int(x0.size)
    for i in order[:k1]:
        keep_1[int(i)] = False
    for i in order[:k5]:
        keep_5[int(i)] = False

    by_day: dict[str, float] = defaultdict(float)
    by_sym: dict[str, float] = defaultdict(float)
    by_sec: dict[str, float] = defaultdict(float)
    for t in xs:
        by_day[str(t["date"])] += float(t["x0_bps"])
        by_sym[str(t["symbol"])] += float(t["x0_bps"])
        by_sec[str(t.get("sector") or "UNKNOWN")] += float(t["x0_bps"])
    best_day = max(by_day.items(), key=lambda kv: kv[1])[0]
    top3_days = [k for k, _ in sorted(by_day.items(), key=lambda kv: -kv[1])[:3]]
    best_sym = max(by_sym.items(), key=lambda kv: kv[1])[0]
    top3_sym = [k for k, _ in sorted(by_sym.items(), key=lambda kv: -kv[1])[:3]]
    best_sec = max(by_sec.items(), key=lambda kv: kv[1])[0]

    drop_best_day = subset_econ([t for t in xs if str(t["date"]) != best_day], label="drop_best_day")
    drop_top3_days = subset_econ([t for t in xs if str(t["date"]) not in set(top3_days)], label="drop_top3_days")
    drop_best_sym = subset_econ([t for t in xs if str(t["symbol"]) != best_sym], label="drop_best_symbol")
    drop_top3_sym = subset_econ([t for t in xs if str(t["symbol"]) not in set(top3_sym)], label="drop_top3_symbols")
    drop_sec = subset_econ([t for t in xs if str(t.get("sector") or "UNKNOWN") != best_sec], label="drop_largest_sector")

    am_n = 0
    tod: dict[str, list[float]] = defaultdict(list)
    for t in xs:
        feat = str(t.get("feature_bar") or t.get("event_time") or "")
        m = mins_from_open(feat)
        if m == m and m < 150:
            am_n += 1
        hour = feat[:2] if feat else ""
        tod[hour].append(float(t["x0_bps"]))
    tod_rows = [
        {"hour": h, "trade_n": len(vs), "mean_x0_bps": float(np.mean(vs))}
        for h, vs in sorted(tod.items())
    ]
    drop_best = drop_idx(keep_best)
    drop_top1pct = drop_idx(keep_1)
    drop_top5pct = drop_idx(keep_5)

    def x1_pos(pack: dict[str, Any]) -> bool:
        v = pack.get("mean_x1_bps")
        return v is not None and float(v) > 0

    acceptable = bool(
        x1_pos(drop_top5pct)
        and x1_pos(drop_best_sym)
        and x1_pos(drop_best_day)
        and (drop_top3_days.get("mean_x0_bps") is not None and float(drop_top3_days["mean_x0_bps"]) > 0)
        and (drop_sec.get("mean_x0_bps") is not None and float(drop_sec["mean_x0_bps"]) > 0)
    )
    return {
        "ok": True,
        "diagnostic_only": True,
        "threshold_tweaked": False,
        "best_trade_removed": drop_best,
        "top_1pct_removed": drop_top1pct,
        "top_5pct_removed": drop_top5pct,
        "best_day_removed": {**drop_best_day, "removed": best_day},
        "top_3_days_removed": {**drop_top3_days, "removed": top3_days},
        "top_symbol_removed": {**drop_best_sym, "removed": best_sym},
        "top_3_symbols_removed": {**drop_top3_sym, "removed": top3_sym},
        "largest_sector_removed": {**drop_sec, "removed": best_sec},
        "am_distribution": {"am_n": am_n, "trade_n": len(xs), "am_share": am_n / max(len(xs), 1)},
        "time_of_day": tod_rows,
        "symbol_contribution": symbol_contribution(xs),
        "sector_contribution": [
            {"sector": k, "gross_x0": v, "trade_n": sum(1 for t in xs if str(t.get("sector") or "UNKNOWN") == k)}
            for k, v in sorted(by_sec.items(), key=lambda kv: -kv[1])
        ],
        "tail": tail,
        "acceptable": acceptable,
        "reject_if_unacceptable": True,
        "repair_forbidden": True,
    }
