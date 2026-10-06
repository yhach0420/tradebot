"""Score ask-classified dominance. Thresholds stay frozen."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.symbol_setup_directional_volume_mechanism import (
    BASELINE_BOARD_N,
    FROZEN_BASELINE_180,
    HORIZONS,
    NEXT_PASS,
    NEXT_STOP,
    NEXT_STOP_REPAIR,
    PRICE_ACTION_PASS_N,
    PRIMARY_HORIZON_SEC,
    RCI_PASS_N,
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
    arr = np.asarray(values, dtype=float)
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
    return got is not None and abs(float(got) - float(exp)) <= 1e-9


def _group_180(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    cell = _at(_response(rows, name), PRIMARY_HORIZON_SEC)
    return {
        "group": name,
        "signal_n": len(rows),
        "covered_n": cell["n"],
        "raw_mid_180_mean": cell["raw_mid_mean"],
        "raw_mid_180_median": cell["raw_mid_median"],
        "raw_mid_180_positive_fraction": cell["raw_mid_positive_fraction"],
        "bid_anchor_180_median": cell["bid_anchor_median"],
        "ask_to_bid_180_median": cell["ask_to_bid_median"],
        "positive_fraction": cell["raw_mid_positive_fraction"],
    }


def _dist(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    vals = [float(row["classification_fraction"]) for row in rows if row.get("classification_fraction") is not None]
    if not vals:
        return {"population": name, "n": 0, "mean": None, "median": None, "q10": None, "q25": None, "q75": None, "q90": None, "min": None}
    arr = np.asarray(vals, dtype=float)
    return {
        "population": name,
        "n": int(arr.size),
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "q10": float(np.percentile(arr, 10)),
        "q25": float(np.percentile(arr, 25)),
        "q75": float(np.percentile(arr, 75)),
        "q90": float(np.percentile(arr, 90)),
        "min": float(np.min(arr)),
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
    detail = [{"kind": key, "name": name, "positive_raw_mid_180_bps": value, "fraction": value / total} for name, value in ranked]
    return {"kind": key, "positive_total_bps": total, "top1": top_name, "top1_fraction": fraction, "monopoly": monopoly, "rows": detail}


def _diff(buy: list[dict[str, Any]], other: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for h in HORIZONS:
        b = _at(buy, h)
        o = _at(other, h)
        item = {"population": "BUY_MINUS_NOT", "horizon_sec": h}
        for key in ("raw_mid_mean", "raw_mid_median", "bid_anchor_mean", "bid_anchor_median", "ask_to_bid_mean", "ask_to_bid_median"):
            item[key] = None if b.get(key) is None or o.get(key) is None else float(b[key]) - float(o[key])
        rows.append(item)
    return rows


def _opposite(groups: list[dict[str, Any]]) -> bool:
    signs = []
    for row in groups:
        value = row.get("raw_mid_180_median")
        if value is None or float(value) == 0.0:
            continue
        signs.append(1 if float(value) > 0 else -1)
    return any(s > 0 for s in signs) and any(s < 0 for s in signs)


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
            bval: Optional[float] = float(PRICE_ACTION_PASS_N)
            rval: Optional[float] = float(len(repaired))
        else:
            bval = float(base[int(horizon)][key])
            raw = got[int(horizon)][key]
            rval = None if raw is None else float(raw)
        rows.append({"metric": metric, "baseline_121": bval, "repaired": rval, "delta_vs_baseline": None if rval is None else float(rval) - float(bval)})
    return rows


def analyze(scanned: dict[str, Any], frozen_table: list[dict[str, Any]]) -> dict[str, Any]:
    rows = list(scanned["volume_rows"])
    dominant = [row for row in rows if row.get("buy_volume_dominant")]
    not_dominant = [row for row in rows if not row.get("buy_volume_dominant")]
    baseline = [row for row in rows if row.get("price_action_pass")]
    repaired = [row for row in dominant if row.get("price_action_pass")]
    board_pass = [row for row in repaired if row.get("board_pass")]
    board_veto = [row for row in repaired if not row.get("board_pass")]
    parity = {
        "rci_pass_n": int(scanned["rci_pass_n"]),
        "rci_match": int(scanned["rci_pass_n"]) == int(RCI_PASS_N),
        "volume_pass_n": int(scanned["volume_pass_n"]),
        "volume_match": int(scanned["volume_pass_n"]) == int(VOLUME_PASS_N),
        "price_action_pass_n": int(scanned["price_action_pass_n"]),
        "price_match": int(scanned["price_action_pass_n"]) == int(PRICE_ACTION_PASS_N),
        "baseline_board_pass_n": int(scanned["baseline_board_pass_n"]),
        "baseline_board_match": int(scanned["baseline_board_pass_n"]) == int(BASELINE_BOARD_N),
        "split_sums_to_volume": len(dominant) + len(not_dominant) == len(rows),
        "repaired_subset_of_baseline": len(repaired) <= len(baseline),
    }
    baseline_table = _response(baseline, "BASELINE_RECOMPUTED")
    clock = {
        "recomputed_raw_mid_180_median": _median(baseline_table, 180, "raw_mid_median"),
        "recomputed_bid_anchor_180_median": _median(baseline_table, 180, "bid_anchor_median"),
        "recomputed_ask_to_bid_180_median": _median(baseline_table, 180, "ask_to_bid_median"),
        "match": False,
    }
    clock["match"] = (
        _close(clock["recomputed_raw_mid_180_median"], FROZEN_BASELINE_180["raw_mid_median"])
        and _close(clock["recomputed_bid_anchor_180_median"], FROZEN_BASELINE_180["bid_anchor_median"])
        and _close(clock["recomputed_ask_to_bid_180_median"], FROZEN_BASELINE_180["ask_to_bid_median"])
    )
    pre_table = _response(repaired, "REPAIRED_PRE_BOARD")
    buy_table = _response(dominant, "BUY_VOLUME_DOMINANT")
    not_table = _response(not_dominant, "NOT_BUY_VOLUME_DOMINANT")
    folds = {name: _group_180([row for row in repaired if row.get("fold") == name], name) for name in ("FOLD_A", "FOLD_B", "FOLD_C")}
    lineages = {
        "ORIGINAL18": _group_180([row for row in repaired if row.get("lineage") == "ORIGINAL18"], "ORIGINAL18"),
        "EXTENSION17": _group_180([row for row in repaired if row.get("lineage") == "EXTENSION17"], "EXTENSION17"),
    }
    day_conc = _concentration(repaired, "date")
    sym_conc = _concentration(repaired, "symbol")
    support_n = len(repaired) >= int(SUPPORT_MIN_N)
    support_folds = all(int(folds[name]["signal_n"]) > 0 for name in folds)
    support_ok = bool(support_n and support_folds)
    raw180 = _median(pre_table, 180, "raw_mid_median")
    bid180 = _median(pre_table, 180, "bid_anchor_median")
    fold_positive = sum(1 for name in folds if _gt(folds[name]["raw_mid_180_median"], 0.0))
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
    mechanism_keys = [key for key in gates if not key.startswith("support_")]
    mechanism_ok = all(bool(gates[key]) for key in mechanism_keys)
    buy180 = _at(buy_table, 180)
    not180 = _at(not_table, 180)
    better = _gt(buy180.get("raw_mid_median"), float(not180["raw_mid_median"]) if not180.get("raw_mid_median") is not None else 1e99) and _gt(
        buy180.get("bid_anchor_median"), float(not180["bid_anchor_median"]) if not180.get("bid_anchor_median") is not None else 1e99
    )
    if not180.get("raw_mid_median") is None or not180.get("bid_anchor_median") is None:
        better = False
    conflict = _opposite(list(folds.values()) + list(lineages.values()))
    actionable = bool(gates["raw_mid_180_median_gt_0"] and gates["bid_anchor_180_median_gt_0"])
    robustness_fail = not all(bool(gates[key]) for key in ("original18_raw_mid_180_median_ge_0", "extension17_raw_mid_180_median_ge_0", "folds_positive_at_least_2", "no_single_day_monopoly", "no_single_symbol_monopoly"))
    if not support_ok:
        interpretation = "D"
    elif conflict or (actionable and robustness_fail):
        interpretation = "C"
    elif better and not actionable:
        interpretation = "B"
    else:
        interpretation = "A"
    counts_ok = bool(parity["rci_match"] and parity["volume_match"] and parity["price_match"] and parity["split_sums_to_volume"] and parity["repaired_subset_of_baseline"])
    if not counts_ok:
        verdict, nxt = VERDICT_PARITY, NEXT_STOP
    elif not clock["match"] or not parity["baseline_board_match"]:
        verdict, nxt = VERDICT_CLOSED, NEXT_STOP
    elif not support_ok:
        verdict, nxt = VERDICT_SUPPORT, NEXT_STOP_REPAIR
    elif not mechanism_ok:
        verdict, nxt = VERDICT_FAIL, NEXT_STOP_REPAIR
    else:
        verdict, nxt = VERDICT_PASS, NEXT_PASS
        interpretation = None
    locked_n = float(sum(float(row["locked_or_crossed_event_n"]) for row in repaired))
    locked_vol = float(sum(float(row["locked_or_crossed_volume"]) for row in repaired))
    return {
        "verdict": verdict,
        "next": nxt,
        "parity": parity,
        "baseline_clock": clock,
        "buy_volume_dominant_n": len(dominant),
        "not_buy_volume_dominant_n": len(not_dominant),
        "repaired_pre_board_n": len(repaired),
        "repaired_board_pass_n": len(board_pass),
        "repaired_board_veto_n": len(board_veto),
        "pre_board": pre_table,
        "buy_dominant": buy_table,
        "not_buy_dominant": not_table,
        "split_delta": _diff(buy_table, not_table),
        "board_pass": _response(board_pass, "REPAIRED_BOARD_PASS"),
        "board_veto": _response(board_veto, "REPAIRED_BOARD_VETO"),
        "folds": folds,
        "lineages": lineages,
        "fold_positive_n": fold_positive,
        "concentration_day": {k: v for k, v in day_conc.items() if k != "rows"},
        "concentration_symbol": {k: v for k, v in sym_conc.items() if k != "rows"},
        "concentration_rows": day_conc["rows"] + sym_conc["rows"],
        "comparison": _comparison(repaired, frozen_table),
        "classification_quality": [
            _dist(rows, "BASELINE_VOLUME_PASS"),
            _dist(dominant, "BUY_VOLUME_DOMINANT"),
            _dist(repaired, "REPAIRED_PRE_BOARD"),
        ],
        "locked_or_crossed": {"event_n": locked_n, "volume": locked_vol, "removed": False, "note": "A print at or above the ask and at or below the bid is classified ask first and kept."},
        "gates": gates,
        "support_pass": support_ok,
        "mechanism_pass": bool(verdict == VERDICT_PASS),
        "interpretation": interpretation,
        "interpretation_rule": "D support insufficient; C opposite 180-second raw-mid signs or actionable but not robust; B ask-classified dominance beats the complement on both 180-second medians while repaired actionability fails; A otherwise. Passed runs have no failure label.",
        "board_cannot_rescue": True,
        "up_down_candidate_defined": False,
    }
