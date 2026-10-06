"""Score the repaired pre-board set against the frozen gates."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.symbol_setup_pullback_range_break_mechanism import (
    BASELINE_BOARD_N,
    BASELINE_SIGNAL_N,
    FROZEN_BASELINE_180,
    HORIZONS,
    NEXT_PASS,
    NEXT_STOP,
    NEXT_STOP_REPAIR,
    PRIMARY_HORIZON_SEC,
    SUPPORT_MIN_N,
    VERDICT_CLOSED,
    VERDICT_FAIL,
    VERDICT_PARITY,
    VERDICT_PASS,
    VERDICT_SUPPORT,
    VOLUME_PASS_N,
)


def _stat(values: list[float], population: int) -> dict[str, Any]:
    if population <= 0:
        return {"n": 0, "population_n": 0, "coverage": None, "mean": None, "median": None, "positive_fraction": None}
    arr = np.asarray([v for v in values if v is not None], dtype=float)
    n = int(arr.size)
    if n == 0:
        return {"n": 0, "population_n": population, "coverage": 0.0, "mean": None, "median": None, "positive_fraction": None}
    return {
        "n": n,
        "population_n": population,
        "coverage": n / float(population),
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "positive_fraction": float(np.mean(arr > 0.0)),
    }


def _values(rows: list[dict[str, Any]], horizon: int, kind: str) -> list[float]:
    key = f"h{horizon}_{kind}"
    return [float(row[key]) for row in rows if row.get(key) is not None]


def _horizon_row(rows: list[dict[str, Any]], horizon: int, population: str) -> dict[str, Any]:
    raw = _stat(_values(rows, horizon, "raw"), len(rows))
    bid = _stat(_values(rows, horizon, "bid"), len(rows))
    atb = _stat(_values(rows, horizon, "atb"), len(rows))
    return {
        "population": population,
        "horizon_sec": horizon,
        "n": raw["n"],
        "population_n": len(rows),
        "coverage": raw["coverage"],
        "raw_mid_mean": raw["mean"],
        "raw_mid_median": raw["median"],
        "raw_mid_positive_fraction": raw["positive_fraction"],
        "bid_anchor_mean": bid["mean"],
        "bid_anchor_median": bid["median"],
        "bid_anchor_positive_fraction": bid["positive_fraction"],
        "ask_to_bid_mean": atb["mean"],
        "ask_to_bid_median": atb["median"],
        "ask_to_bid_positive_fraction": atb["positive_fraction"],
    }


def _response(rows: list[dict[str, Any]], population: str) -> list[dict[str, Any]]:
    return [_horizon_row(rows, h, population) for h in HORIZONS]


def _at(table: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
    return next(row for row in table if int(row["horizon_sec"]) == int(horizon))


def _median(table: list[dict[str, Any]], horizon: int, key: str) -> Optional[float]:
    value = _at(table, horizon).get(key)
    return None if value is None else float(value)


def _gt(value: Optional[float], bound: float) -> bool:
    return value is not None and float(value) > float(bound)


def _ge(value: Optional[float], bound: float) -> bool:
    return value is not None and float(value) >= float(bound)


def _close(got: Optional[float], exp: float) -> bool:
    if got is None:
        return False
    return abs(float(got) - float(exp)) <= 1e-9


def _group_180(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    table = _response(rows, name)
    cell = _at(table, PRIMARY_HORIZON_SEC)
    return {
        "group": name,
        "signal_n": len(rows),
        "covered_n": cell["n"],
        "coverage": cell["coverage"],
        "raw_mid_180_mean": cell["raw_mid_mean"],
        "raw_mid_180_median": cell["raw_mid_median"],
        "raw_mid_180_positive_fraction": cell["raw_mid_positive_fraction"],
        "bid_anchor_180_mean": cell["bid_anchor_mean"],
        "bid_anchor_180_median": cell["bid_anchor_median"],
        "bid_anchor_180_positive_fraction": cell["bid_anchor_positive_fraction"],
        "ask_to_bid_180_mean": cell["ask_to_bid_mean"],
        "ask_to_bid_180_median": cell["ask_to_bid_median"],
        "ask_to_bid_180_positive_fraction": cell["ask_to_bid_positive_fraction"],
    }


def _concentration(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    buckets: dict[str, float] = {}
    for row in rows:
        value = row.get(f"h{PRIMARY_HORIZON_SEC}_raw")
        if value is None or float(value) <= 0.0:
            continue
        name = str(row[key])
        buckets[name] = buckets.get(name, 0.0) + float(value)
    total = float(sum(buckets.values()))
    ranked = sorted(buckets.items(), key=lambda item: (-item[1], item[0]))
    top_name = ranked[0][0] if ranked else None
    top_sum = ranked[0][1] if ranked else 0.0
    fraction = None if total <= 0.0 else top_sum / total
    monopoly = True if total <= 0.0 else bool(fraction is not None and fraction >= 1.0 - 1e-12)
    detail = [
        {"kind": key, "name": name, "positive_raw_mid_180_bps": value, "fraction": value / total}
        for name, value in ranked
    ]
    return {
        "kind": key,
        "positive_total_bps": total,
        "contributor_n": len(ranked),
        "top1": top_name,
        "top1_fraction": fraction,
        "monopoly": monopoly,
        "rows": detail,
    }


def _comparison(repaired: list[dict[str, Any]], frozen_table: list[dict[str, Any]]) -> list[dict[str, Any]]:
    got = {int(row["horizon_sec"]): row for row in _response(repaired, "REPAIRED_PRE_BOARD")}
    base = {int(row["horizon_sec"]): row for row in frozen_table}
    specs = [
        ("signal_n", None, "population_n"),
        ("raw_mid_30_median", 30, "raw_mid_median"),
        ("raw_mid_60_median", 60, "raw_mid_median"),
        ("raw_mid_180_median", 180, "raw_mid_median"),
        ("raw_mid_300_median", 300, "raw_mid_median"),
        ("bid_anchor_180_median", 180, "bid_anchor_median"),
        ("ask_to_bid_180_median", 180, "ask_to_bid_median"),
    ]
    rows = []
    for metric, horizon, key in specs:
        if metric == "signal_n":
            bval = float(BASELINE_SIGNAL_N)
            rval = float(len(repaired))
        else:
            bval = float(base[int(horizon)][key])
            rval_raw = got[int(horizon)][key]
            rval = None if rval_raw is None else float(rval_raw)
        rows.append(
            {
                "metric": metric,
                "baseline_121": bval,
                "repaired": rval,
                "delta_vs_baseline": None if rval is None else float(rval) - float(bval),
            }
        )
    return rows


def analyze(scanned: dict[str, Any], frozen_table: list[dict[str, Any]]) -> dict[str, Any]:
    repaired = list(scanned["repaired"])
    baseline_rows = list(scanned["baseline_rows"])
    parity = {
        "volume_pass_n": int(scanned["volume_pass_n"]),
        "volume_pass_expected": VOLUME_PASS_N,
        "volume_pass_match": int(scanned["volume_pass_n"]) == int(VOLUME_PASS_N),
        "baseline_signal_n": int(scanned["baseline_signal_n"]),
        "baseline_signal_expected": BASELINE_SIGNAL_N,
        "baseline_signal_match": int(scanned["baseline_signal_n"]) == int(BASELINE_SIGNAL_N),
        "baseline_board_pass_n": int(scanned["baseline_board_pass_n"]),
        "baseline_board_expected": BASELINE_BOARD_N,
        "baseline_board_match": int(scanned["baseline_board_pass_n"]) == int(BASELINE_BOARD_N),
        "structure_not_available_n": int(scanned["structure_not_available_n"]),
        "price_parity_fails": int(scanned["price_parity_fails"]),
        "repaired_subset_of_baseline": len(repaired) <= len(baseline_rows),
    }
    baseline_table = _response(baseline_rows, "BASELINE_RECOMPUTED")
    clock = {
        "recomputed_raw_mid_180_median": _median(baseline_table, 180, "raw_mid_median"),
        "recomputed_bid_anchor_180_median": _median(baseline_table, 180, "bid_anchor_median"),
        "recomputed_ask_to_bid_180_median": _median(baseline_table, 180, "ask_to_bid_median"),
        "frozen_raw_mid_180_median": FROZEN_BASELINE_180["raw_mid_median"],
        "frozen_bid_anchor_180_median": FROZEN_BASELINE_180["bid_anchor_median"],
        "frozen_ask_to_bid_180_median": FROZEN_BASELINE_180["ask_to_bid_median"],
    }
    clock["match"] = (
        _close(clock["recomputed_raw_mid_180_median"], FROZEN_BASELINE_180["raw_mid_median"])
        and _close(clock["recomputed_bid_anchor_180_median"], FROZEN_BASELINE_180["bid_anchor_median"])
        and _close(clock["recomputed_ask_to_bid_180_median"], FROZEN_BASELINE_180["ask_to_bid_median"])
    )
    pre = [row for row in repaired]
    board_pass = [row for row in repaired if row.get("board_pass")]
    board_veto = [row for row in repaired if not row.get("board_pass")]
    pre_table = _response(pre, "REPAIRED_PRE_BOARD")
    folds = {name: _group_180([row for row in pre if row.get("fold") == name], name) for name in ("FOLD_A", "FOLD_B", "FOLD_C")}
    lineages = {
        "ORIGINAL18": _group_180([row for row in pre if row.get("lineage") == "ORIGINAL18"], "ORIGINAL18"),
        "EXTENSION17": _group_180([row for row in pre if row.get("lineage") == "EXTENSION17"], "EXTENSION17"),
    }
    day_conc = _concentration(pre, "date")
    sym_conc = _concentration(pre, "symbol")
    support_n = len(pre) >= int(SUPPORT_MIN_N)
    support_folds = all(int(folds[name]["signal_n"]) > 0 for name in ("FOLD_A", "FOLD_B", "FOLD_C"))
    support_ok = bool(support_n and support_folds)
    raw180 = _median(pre_table, PRIMARY_HORIZON_SEC, "raw_mid_median")
    bid180 = _median(pre_table, PRIMARY_HORIZON_SEC, "bid_anchor_median")
    fold_positive = sum(1 for name in ("FOLD_A", "FOLD_B", "FOLD_C") if _gt(folds[name]["raw_mid_180_median"], 0.0))
    gates = {
        "support_n": support_n,
        "support_all_folds": support_folds,
        "raw_mid_180_median_gt_0": _gt(raw180, 0.0),
        "bid_anchor_180_median_gt_0": _gt(bid180, 0.0),
        "original18_raw_mid_180_median_ge_0": _ge(lineages["ORIGINAL18"]["raw_mid_180_median"], 0.0),
        "extension17_raw_mid_180_median_ge_0": _ge(lineages["EXTENSION17"]["raw_mid_180_median"], 0.0),
        "folds_positive_at_least_2": fold_positive >= 2,
        "raw_mid_180_beats_baseline": _gt(raw180, FROZEN_BASELINE_180["raw_mid_median"]),
        "bid_anchor_180_beats_baseline": _gt(bid180, FROZEN_BASELINE_180["bid_anchor_median"]),
        "no_single_day_monopoly": not bool(day_conc["monopoly"]),
        "no_single_symbol_monopoly": not bool(sym_conc["monopoly"]),
    }
    mechanism_keys = (
        "raw_mid_180_median_gt_0",
        "bid_anchor_180_median_gt_0",
        "original18_raw_mid_180_median_ge_0",
        "extension17_raw_mid_180_median_ge_0",
        "folds_positive_at_least_2",
        "raw_mid_180_beats_baseline",
        "bid_anchor_180_beats_baseline",
        "no_single_day_monopoly",
        "no_single_symbol_monopoly",
    )
    mechanism_ok = all(bool(gates[key]) for key in mechanism_keys)
    if not parity["volume_pass_match"] or not parity["baseline_signal_match"] or not parity["baseline_board_match"]:
        verdict, nxt = VERDICT_PARITY, NEXT_STOP
    elif not clock["match"] or not parity["repaired_subset_of_baseline"]:
        verdict, nxt = VERDICT_CLOSED, NEXT_STOP
    elif not support_ok:
        verdict, nxt = VERDICT_SUPPORT, NEXT_STOP_REPAIR
    elif not mechanism_ok:
        verdict, nxt = VERDICT_FAIL, NEXT_STOP_REPAIR
    else:
        verdict, nxt = VERDICT_PASS, NEXT_PASS
    return {
        "verdict": verdict,
        "next": nxt,
        "parity": parity,
        "baseline_clock": clock,
        "repaired_pre_board_n": len(pre),
        "repaired_board_pass_n": len(board_pass),
        "repaired_board_veto_n": len(board_veto),
        "pre_board": pre_table,
        "board_pass": _response(board_pass, "REPAIRED_BOARD_PASS"),
        "board_veto": _response(board_veto, "REPAIRED_BOARD_VETO"),
        "folds": folds,
        "lineages": lineages,
        "fold_positive_n": fold_positive,
        "concentration_day": {k: v for k, v in day_conc.items() if k != "rows"},
        "concentration_symbol": {k: v for k, v in sym_conc.items() if k != "rows"},
        "concentration_rows": day_conc["rows"] + sym_conc["rows"],
        "comparison": _comparison(pre, frozen_table),
        "gates": gates,
        "support_pass": support_ok,
        "mechanism_pass": bool(support_ok and mechanism_ok and verdict == VERDICT_PASS),
        "board_cannot_rescue": True,
    }
