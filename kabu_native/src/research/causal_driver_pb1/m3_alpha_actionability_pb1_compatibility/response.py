"""Execution-aligned M3 response. Not a portfolio and not a strategy."""
from __future__ import annotations

from statistics import mean, median, quantiles
from typing import Any

from research.causal_driver_pb1.m3_alpha_actionability_pb1_compatibility import COST_BPS, PRIMARY_HORIZON_MIN
from research.causal_driver_pb1.phase2_discovery.clock import hhmm_to_min, min_to_hhmm
from research.causal_driver_pb1.sector_state_alpha_complete_economic.economics import fold_of
from research.cause_first_mechanism_discovery_v1.clock import in_lunch, interval_crosses_lunch

TARGETS = ("6590", "6787", "6861", "6941", "6961")
FOLDS = ("EARLY", "MIDDLE", "LATE")


def _plus(hhmm: str, minutes: int) -> str:
    return min_to_hhmm(hhmm_to_min(hhmm) + int(minutes))


def _bps(entry: float, exit_px: float) -> float:
    return (float(exit_px) - float(entry)) / float(entry) * 10000.0


def observations(
    *,
    episodes: list[dict[str, str]],
    opens: dict[tuple[str, str, str], float],
    horizon: int,
) -> list[dict[str, Any]]:
    rows = []
    for ep in episodes:
        start = str(ep["start"])[:5]
        entry_t = _plus(start, 1)
        exit_t = _plus(entry_t, horizon)
        if in_lunch(entry_t) or in_lunch(exit_t) or interval_crosses_lunch(entry_t, exit_t):
            continue
        for symbol in TARGETS:
            entry = opens.get((ep["date"], symbol, entry_t))
            exit_px = opens.get((ep["date"], symbol, exit_t))
            if entry is None or exit_px is None or entry <= 0 or exit_px <= 0:
                continue
            gross = _bps(entry, exit_px)
            rows.append({
                "date": ep["date"],
                "symbol": symbol,
                "episode_id": ep["episode_id"],
                "decision_t": start,
                "entry_t": entry_t,
                "exit_t": exit_t,
                "gross_bps": gross,
                "adjusted_bps": gross - float(COST_BPS),
                "fold": fold_of(ep["date"]),
            })
    return rows


def _dist(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "q25": None, "q50": None, "q75": None, "positive_fraction": None}
    qs = quantiles(values, n=4) if len(values) >= 2 else [values[0], values[0], values[0]]
    return {
        "n": len(values),
        "mean": float(mean(values)),
        "median": float(median(values)),
        "q25": float(qs[0]),
        "q50": float(qs[1]),
        "q75": float(qs[2]),
        "positive_fraction": float(sum(1 for v in values if v > 0) / len(values)),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    gross = [float(r["gross_bps"]) for r in rows]
    adj = [float(r["adjusted_bps"]) for r in rows]
    folds = {}
    for name in FOLDS:
        chunk = [float(r["adjusted_bps"]) for r in rows if r["fold"] == name]
        folds[name] = {
            "fold": name,
            "n": len(chunk),
            "adjusted_mean_bps": float(mean(chunk)) if chunk else None,
            "adjusted_median_bps": float(median(chunk)) if chunk else None,
        }
    by_symbol = []
    sums = {}
    for symbol in TARGETS:
        chunk = [r for r in rows if r["symbol"] == symbol]
        vals = [float(r["gross_bps"]) for r in chunk]
        adjs = [float(r["adjusted_bps"]) for r in chunk]
        sums[symbol] = float(sum(adjs))
        by_symbol.append({
            "symbol": symbol,
            "n": len(chunk),
            "gross_mean_bps": float(mean(vals)) if vals else None,
            "gross_median_bps": float(median(vals)) if vals else None,
            "adjusted_mean_bps": float(mean(adjs)) if adjs else None,
            "adjusted_median_bps": float(median(adjs)) if adjs else None,
            "adjusted_sum_bps": sums[symbol],
        })
    gdist = _dist(gross)
    adist = _dist(adj)
    total = float(sum(sums.values()))
    one_target_all = bool(total > 0 and any(total - v <= 0 for v in sums.values()))
    positive_folds = [name for name, row in folds.items() if row["adjusted_mean_bps"] is not None and float(row["adjusted_mean_bps"]) > 0]
    gates = {
        "A1_adjusted_mean_gt_0": adist["mean"] is not None and float(adist["mean"]) > 0,
        "A2_adjusted_median_gte_0": adist["median"] is not None and float(adist["median"]) >= 0,
        "A3_two_folds_positive_mean": len(positive_folds) >= 2,
        "A4_no_single_target_supplies_all_positive_edge": not one_target_all,
    }
    return {
        "horizon_min": PRIMARY_HORIZON_MIN,
        "classification": "ALPHA_ACTIONABILITY_DIAGNOSTIC_ONLY",
        "observation_n": len(rows),
        "gross_mean_bps": gdist["mean"],
        "gross_median_bps": gdist["median"],
        "adjusted_mean_bps": adist["mean"],
        "adjusted_median_bps": adist["median"],
        "positive_observation_fraction_8bps": adist["positive_fraction"],
        "q25_adjusted_bps": adist["q25"],
        "q50_adjusted_bps": adist["q50"],
        "q75_adjusted_bps": adist["q75"],
        "folds": folds,
        "positive_folds": positive_folds,
        "symbols": by_symbol,
        "single_target_supplies_all_positive_edge": one_target_all,
        "gates": gates,
        "pass": all(gates.values()),
    }


def sensitivity(rows_by_h: dict[int, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out = []
    for horizon, rows in rows_by_h.items():
        gross = [float(r["gross_bps"]) for r in rows]
        adj = [float(r["adjusted_bps"]) for r in rows]
        out.append({
            "horizon_min": horizon,
            "role": "descriptive_sensitivity_not_selected",
            "observation_n": len(rows),
            "gross_mean_bps": float(mean(gross)) if gross else None,
            "gross_median_bps": float(median(gross)) if gross else None,
            "adjusted_mean_bps": float(mean(adj)) if adj else None,
            "adjusted_median_bps": float(median(adj)) if adj else None,
        })
    return out
