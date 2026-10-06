"""Answer the slope-noise questions. No K is selected."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.symbol_setup_exit_noise_rca import (
    NEXT_INSUFFICIENT,
    NEXT_K1,
    NEXT_STOP,
    NEXT_WARRANT,
    VERDICT_INSUFFICIENT,
    VERDICT_K1,
    VERDICT_WARRANT,
)


def _vals(rows: list[dict[str, Any]], key: str) -> list[float]:
    return [float(row[key]) for row in rows if row.get(key) is not None]


def _stat(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "positive_fraction": None}
    arr = np.asarray(values, dtype=float)
    return {"n": int(arr.size), "mean": float(np.mean(arr)), "median": float(np.median(arr)), "positive_fraction": float(np.mean(arr > 0.0))}


def _duration(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "q25": None, "q75": None, "max": None, "duration_1": 0, "duration_2": 0, "duration_ge_3": 0}
    arr = np.asarray(values, dtype=float)
    return {
        "n": int(arr.size),
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "q25": float(np.percentile(arr, 25)),
        "q75": float(np.percentile(arr, 75)),
        "max": float(np.max(arr)),
        "duration_1": int(np.sum(arr == 1)),
        "duration_2": int(np.sum(arr == 2)),
        "duration_ge_3": int(np.sum(arr >= 3)),
    }


def _rate(n: int, d: int) -> Optional[float]:
    return None if d <= 0 else n / float(d)


def _price_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ["bid_plus_1bar_bps", "bid_plus_2bar_bps", "bid_plus_3bar_bps", "bid_60s_bps", "bid_180s_bps", "bid_300s_bps"]
    return [{"mark": key, **_stat(_vals(rows, key))} for key in keys]


def _recovery_by_reason(losses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for reason in ("SLOW_TREND_SLOPE_LOSS", "FAST_SLOW_CROSS_LOSS", "BOTH_TREND_COMPONENTS_LOST"):
        rows = [row for row in losses if row.get("loss_reason") == reason]
        rec = sum(1 for row in rows if row.get("recovered_within_3"))
        out.append({"loss_reason": reason, "n": len(rows), "recovered_within_3": rec, "rate": _rate(rec, len(rows))})
    return out


def analyze(scanned: dict[str, Any]) -> dict[str, Any]:
    episodes = list(scanned["episodes"])
    fills = list(scanned["fills"])
    losses = [row for row in episodes if row.get("loss_reason")]
    slope = [row for row in losses if row.get("slope_only")]
    temporary = [row for row in slope if row.get("temporary_slope_interruption")]
    price_recovered = [row for row in slope if row.get("temporary_and_price_recovered")]
    terminal = [row for row in slope if row.get("classification") == "TERMINAL_SLOPE_FAILURE"]
    ambiguous = [row for row in slope if row.get("classification") == "AMBIGUOUS_SLOPE_FAILURE"]
    actual_slope = []
    session_rows = []
    actual_rows = []
    for fill in fills:
        path = fill.get("path") or {}
        kind = fill.get("actual_exit_reason")
        label = None
        if kind == "SESSION_FAIL_CLOSE":
            label = "THESIS_TOO_PERMISSIVE_CANDIDATE"
            session_rows.append({**fill, "path": None, "separate_label": label})
        elif kind == "SLOW_TREND_SLOPE_LOSS":
            label = path.get("classification")
            actual_slope.append(fill)
        else:
            label = kind
        actual_rows.append(
            {
                "date": fill["date"],
                "symbol": fill["symbol"],
                "actual_exit_time": fill["actual_exit_time"],
                "actual_exit_price": fill["actual_exit_price"],
                "actual_exit_reason": kind,
                "actual_realized_bps": fill["actual_realized_bps"],
                "actual_pnl_yen": fill["actual_pnl_yen"],
                "classification": label,
                "role": "ECONOMIC_ANCHOR",
                "COUNTERFACTUAL_DIAGNOSTIC": False,
            }
        )
    actual_price = [row for row in actual_slope if (row.get("path") or {}).get("temporary_and_price_recovered")]
    actual_temporary = [row for row in actual_slope if (row.get("path") or {}).get("temporary_slope_interruption")]
    mark_180 = _stat(_vals(slope, "bid_180s_bps"))["median"]
    mark_3 = _stat(_vals(slope, "bid_plus_3bar_bps"))["median"]
    price_down = (mark_180 is not None and float(mark_180) < 0.0) or (mark_180 is None and mark_3 is not None and float(mark_3) < 0.0)
    repeated = len(temporary) >= 2 and len(temporary) > len(terminal)
    more_than_one_fill = len(actual_price) >= 2
    generally_no_recovery = len(slope) >= 5 and _rate(len(temporary), len(slope)) is not None and float(_rate(len(temporary), len(slope)) or 0) < 0.5
    if repeated and more_than_one_fill:
        verdict, nxt = VERDICT_WARRANT, NEXT_WARRANT
        hypothesis = "supported"
    elif generally_no_recovery and price_down and not more_than_one_fill:
        verdict, nxt = VERDICT_K1, NEXT_K1
        hypothesis = "not supported"
    else:
        verdict, nxt = VERDICT_INSUFFICIENT, NEXT_INSUFFICIENT
        hypothesis = "insufficient"
    by_reason = _recovery_by_reason(losses)
    slope_rate = next(row["rate"] for row in by_reason if row["loss_reason"] == "SLOW_TREND_SLOPE_LOSS")
    others = [row for row in by_reason if row["loss_reason"] != "SLOW_TREND_SLOPE_LOSS"]
    material = bool(
        slope_rate is not None
        and all(row["n"] >= 5 and row["rate"] is not None and float(slope_rate) - float(row["rate"]) >= 0.20 for row in others)
    )
    counterfactual = []
    for fill in fills:
        path = fill.get("path") or {}
        if fill.get("actual_exit_reason") == "SESSION_FAIL_CLOSE":
            continue
        counterfactual.append(
            {
                "date": fill["date"],
                "symbol": fill["symbol"],
                "label": "COUNTERFACTUAL_DIAGNOSTIC",
                "actual_exit_reason": fill["actual_exit_reason"],
                "actual_pnl_yen": fill["actual_pnl_yen"],
                "bid_plus_1bar_bps": path.get("bid_plus_1bar_bps"),
                "bid_plus_2bar_bps": path.get("bid_plus_2bar_bps"),
                "bid_plus_3bar_bps": path.get("bid_plus_3bar_bps"),
                "portfolio_pnl_included": False,
            }
        )
    return {
        "verdict": verdict,
        "next": nxt,
        "k1_noise_hypothesis": hypothesis,
        "persistence_mechanism_warranted": verdict == VERDICT_WARRANT,
        "k_selected": False,
        "slope_episode_n": len(slope),
        "temporary_n": len(temporary),
        "temporary_rate": _rate(len(temporary), len(slope)),
        "price_recovered_n": len(price_recovered),
        "price_recovered_rate": _rate(len(price_recovered), len(slope)),
        "terminal_n": len(terminal),
        "terminal_rate": _rate(len(terminal), len(slope)),
        "ambiguous_n": len(ambiguous),
        "ambiguous_rate": _rate(len(ambiguous), len(slope)),
        "recovered_within_1": sum(1 for row in slope if row.get("recovered_within_1")),
        "recovered_within_2": sum(1 for row in slope if row.get("recovered_within_2")),
        "recovered_within_3": sum(1 for row in slope if row.get("recovered_within_3")),
        "duration": _duration([int(row["consecutive_slope_loss_bars"]) for row in slope if row.get("consecutive_slope_loss_bars") is not None]),
        "price_response": _price_table(slope),
        "recovery_by_reason": by_reason,
        "slope_more_noise_prone": material,
        "actual_slope_n": len(actual_slope),
        "actual_temporary_n": len(actual_temporary),
        "actual_price_recovered_n": len(actual_price),
        "actual_terminal_n": sum(1 for row in actual_slope if (row.get("path") or {}).get("classification") == "TERMINAL_SLOPE_FAILURE"),
        "actual_ambiguous_n": sum(1 for row in actual_slope if (row.get("path") or {}).get("classification") == "AMBIGUOUS_SLOPE_FAILURE"),
        "actual_rows": actual_rows,
        "counterfactual_rows": counterfactual,
        "session_close_rows": [{k: v for k, v in row.items() if k != "path"} for row in session_rows],
        "questions": {
            "Q1_temporary_rate": _rate(len(temporary), len(slope)),
            "Q2_price_recovered_rate": _rate(len(price_recovered), len(slope)),
            "Q3_terminal_rate": _rate(len(terminal), len(slope)),
            "Q4_slope_more_noise_prone_than_other_losses": material,
            "Q5_k1_cuts_before_recoverable_continuation": len(actual_price) >= 2,
        },
    }
