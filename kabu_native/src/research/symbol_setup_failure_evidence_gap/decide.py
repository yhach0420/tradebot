"""Frozen classification. The historical not-identified verdict is not rewritten."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.symbol_setup_failure_evidence_gap import (
    BAR_HORIZONS,
    CANONICAL_STAGE_ORDER,
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    NEXT_FRICTION,
    NEXT_OPEN,
    NEXT_ORDER,
    NEXT_RAW,
    NEXT_TIME,
    QUOTE_HORIZONS,
    TIMING_BAR_MATERIAL_SEC,
    TIMING_QUOTE_MATERIAL_SEC,
    VERDICT_FRICTION,
    VERDICT_OPEN,
    VERDICT_ORDER,
    VERDICT_RAW,
    VERDICT_TIME,
)
from research.symbol_setup_failure_evidence_gap.scan import Bag, RCA_COMPARE_STAGES


def _stat(values: list[float], population: int) -> dict[str, Any]:
    if population <= 0:
        return {"n": 0, "population": 0, "coverage": None, "mean": None, "median": None, "positive_fraction": None, "q25": None, "q75": None}
    arr = np.asarray(values, dtype=float)
    n = int(arr.size)
    if n == 0:
        return {"n": 0, "population": population, "coverage": 0.0, "mean": None, "median": None, "positive_fraction": None, "q25": None, "q75": None}
    return {
        "n": n,
        "population": population,
        "coverage": n / float(population),
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "positive_fraction": float(np.mean(arr > 0.0)),
        "q25": float(np.percentile(arr, 25)),
        "q75": float(np.percentile(arr, 75)),
    }


def _delay_stat(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "p90": None, "max": None}
    arr = np.asarray(values, dtype=float)
    return {
        "n": int(arr.size),
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "p90": float(np.percentile(arr, 90)),
        "max": float(np.max(arr)),
    }


def _horizon_values(rows: list[dict[str, Any]], horizon: int, key: str) -> list[float]:
    out = []
    for row in rows:
        cell = (row.get("horizons") or {}).get(str(horizon)) or {}
        value = cell.get(key)
        if value is not None:
            out.append(float(value))
    return out


def _bar_values(rows: list[dict[str, Any]], name: str) -> list[float]:
    out = []
    for row in rows:
        value = ((row.get("bars") or {}).get(name) or {}).get("bar_bps")
        if value is not None:
            out.append(float(value))
    return out


def _response_table(rows: list[dict[str, Any]], population: str) -> list[dict[str, Any]]:
    table = []
    for h in QUOTE_HORIZONS:
        raw = _stat(_horizon_values(rows, h, "raw_mid_bps"), len(rows))
        entry = _stat(_horizon_values(rows, h, "entry_half_bps"), len(rows))
        exit_ = _stat(_horizon_values(rows, h, "exit_half_bps"), len(rows))
        atb = _stat(_horizon_values(rows, h, "ask_to_bid_bps"), len(rows))
        bid = _stat(_horizon_values(rows, h, "bid_anchor_bps"), len(rows))
        last = _stat(_horizon_values(rows, h, "last_to_last_bps"), len(rows))
        table.append(
            {
                "population": population,
                "horizon_sec": h,
                "n": raw["n"],
                "population_n": len(rows),
                "coverage": raw["coverage"],
                "raw_mid_mean": raw["mean"],
                "raw_mid_median": raw["median"],
                "raw_mid_positive_fraction": raw["positive_fraction"],
                "entry_half_mean": entry["mean"],
                "entry_half_median": entry["median"],
                "exit_half_mean": exit_["mean"],
                "exit_half_median": exit_["median"],
                "ask_to_bid_mean": atb["mean"],
                "ask_to_bid_median": atb["median"],
                "ask_to_bid_positive_fraction": atb["positive_fraction"],
                "bid_anchor_mean": bid["mean"],
                "bid_anchor_median": bid["median"],
                "bid_anchor_positive_fraction": bid["positive_fraction"],
                "last_to_last_mean": last["mean"],
                "last_to_last_median": last["median"],
                "last_to_last_n": last["n"],
                "last_to_last_coverage": last["coverage"],
            }
        )
    return table


def _bar_table(rows: list[dict[str, Any]], population: str) -> list[dict[str, Any]]:
    rows_out = []
    for name, sec in BAR_HORIZONS:
        st = _stat(_bar_values(rows, name), len(rows))
        rows_out.append({"population": population, "bar": name, "horizon_sec": sec, **st})
    return rows_out


def _nonpositive_horizons(table: list[dict[str, Any]]) -> int:
    return sum(1 for row in table if row.get("raw_mid_median") is not None and float(row["raw_mid_median"]) <= 0.0)


def _positive_horizons(table: list[dict[str, Any]]) -> int:
    return sum(1 for row in table if row.get("raw_mid_median") is not None and float(row["raw_mid_median"]) > 0.0)


def _atb_negative_horizons(table: list[dict[str, Any]]) -> int:
    return sum(1 for row in table if row.get("ask_to_bid_median") is not None and float(row["ask_to_bid_median"]) < 0.0)


def _spread_exceeds(table: list[dict[str, Any]]) -> int:
    n = 0
    for row in table:
        if None in (row.get("raw_mid_median"), row.get("entry_half_median"), row.get("exit_half_median")):
            continue
        if float(row["entry_half_median"]) + float(row["exit_half_median"]) >= float(row["raw_mid_median"]):
            n += 1
    return n


def _sign(value: Optional[float]) -> int:
    if value is None or float(value) == 0.0:
        return 0
    return 1 if float(value) > 0.0 else -1


def _bag_median(bag: Bag, key: str) -> Optional[float]:
    vals = bag.values.get(key) or []
    if not vals:
        return None
    return float(np.median(np.asarray(vals, dtype=float)))


def _stage_rows(stages: dict[str, dict[str, Bag]]) -> list[dict[str, Any]]:
    rows = []
    for name in CANONICAL_STAGE_ORDER:
        inp = stages[name]["input"]
        pas = stages[name]["pass"]
        row: dict[str, Any] = {
            "stage": name,
            "input_n": inp.n,
            "pass_n": pas.n,
            "pass_rate": (pas.n / inp.n) if inp.n else None,
        }
        for h in QUOTE_HORIZONS:
            for key, label in (("raw", "raw_mid"), ("atb", "ask_to_bid")):
                im = _bag_median(inp, f"{key}_{h}")
                pm = _bag_median(pas, f"{key}_{h}")
                row[f"{label}_{h}_input_median"] = im
                row[f"{label}_{h}_pass_median"] = pm
                row[f"{label}_{h}_pass_minus_input_median"] = None if im is None or pm is None else pm - im
        for name_b, _sec in BAR_HORIZONS:
            im = _bag_median(inp, f"bar_{name_b}")
            pm = _bag_median(pas, f"bar_{name_b}")
            row[f"bar_{name_b}_input_median"] = im
            row[f"bar_{name_b}_pass_median"] = pm
            row[f"bar_{name_b}_pass_minus_input_median"] = None if im is None or pm is None else pm - im
        rows.append(row)
    return rows


def _timing(rows: list[dict[str, Any]]) -> dict[str, Any]:
    q0 = [float(r["q0_delay_ms"]) / 1000.0 for r in rows if r.get("q0_delay_ms") is not None]
    out: dict[str, Any] = {"signal_finalize_to_q0_delay_sec": _delay_stat(q0)}
    quote_material = 0
    bar_material = 0
    for h in QUOTE_HORIZONS:
        vals = []
        for row in rows:
            cell = (row.get("horizons") or {}).get(str(h)) or {}
            if cell.get("qh_minus_target_sec") is not None:
                vals.append(float(cell["qh_minus_target_sec"]))
        st = _delay_stat(vals)
        out[f"quote_{h}_qh_minus_target_sec"] = st
        if st["median"] is not None and float(st["median"]) > float(TIMING_QUOTE_MATERIAL_SEC):
            quote_material += 1
    for name, _sec in BAR_HORIZONS:
        vals = []
        for row in rows:
            cell = (row.get("bars") or {}).get(name) or {}
            if cell.get("bar_minus_target_sec") is not None:
                vals.append(float(cell["bar_minus_target_sec"]))
        st = _delay_stat(vals)
        out[f"bar_{name}_available_minus_target_sec"] = st
        if st["median"] is not None and float(st["median"]) < -float(TIMING_BAR_MATERIAL_SEC):
            bar_material += 1
    q0_med = out["signal_finalize_to_q0_delay_sec"]["median"]
    material = bool(
        (q0_med is not None and float(q0_med) > float(TIMING_QUOTE_MATERIAL_SEC))
        or quote_material >= 3
        or bar_material >= 2
    )
    out["timing_material"] = material
    out["quote_horizons_over_material_delay"] = quote_material
    out["bar_horizons_over_material_staleness"] = bar_material
    return out


def _classify_table(table: list[dict[str, Any]], timing_material: bool) -> str:
    if timing_material:
        return CASE_C
    if _nonpositive_horizons(table) >= 3:
        return CASE_A
    if _positive_horizons(table) >= 3 and _atb_negative_horizons(table) >= 3 and _spread_exceeds(table) >= 3:
        return CASE_B
    return CASE_D


def _order_material(canon_rows: list[dict[str, Any]], rca: dict[str, dict[str, Bag]]) -> dict[str, Any]:
    by_name = {row["stage"]: row for row in canon_rows}
    detail = {}
    flipped = False
    for name in RCA_COMPARE_STAGES:
        canon = by_name[name]
        rca_in = _bag_median(rca[name]["input"], "atb_180")
        rca_ps = _bag_median(rca[name]["pass"], "atb_180")
        rca_delta = None if rca_in is None or rca_ps is None else rca_ps - rca_in
        canon_delta = canon.get("ask_to_bid_180_pass_minus_input_median")
        raw_in = _bag_median(rca[name]["input"], "raw_180")
        raw_ps = _bag_median(rca[name]["pass"], "raw_180")
        rca_raw = None if raw_in is None or raw_ps is None else raw_ps - raw_in
        canon_raw = canon.get("raw_mid_180_pass_minus_input_median")
        atb_flip = _sign(canon_delta) * _sign(rca_delta) < 0
        raw_flip = _sign(canon_raw) * _sign(rca_raw) < 0
        flipped = flipped or atb_flip or raw_flip
        detail[name] = {
            "canonical_ask_to_bid_180_delta": canon_delta,
            "rca_order_ask_to_bid_180_delta": rca_delta,
            "canonical_raw_mid_180_delta": canon_raw,
            "rca_order_raw_mid_180_delta": rca_raw,
            "sign_flip": bool(atb_flip or raw_flip),
        }
    return {"material_change": flipped, "stages": detail}


def analyze(scanned: dict[str, Any]) -> dict[str, Any]:
    signals = scanned["signals"]
    groups = {
        "ALL_TECHNICAL_SIGNALS": signals,
        "BOARD_PASS": [r for r in signals if r.get("board_pass")],
        "BOARD_VETO": [r for r in signals if not r.get("board_pass")],
        "ORIGINAL18": [r for r in signals if r.get("lineage") == "ORIGINAL18"],
        "EXTENSION17": [r for r in signals if r.get("lineage") == "EXTENSION17"],
        "FOLD_A": [r for r in signals if r.get("fold") == "FOLD_A"],
        "FOLD_B": [r for r in signals if r.get("fold") == "FOLD_B"],
        "FOLD_C": [r for r in signals if r.get("fold") == "FOLD_C"],
    }
    tables = {name: _response_table(rows, name) for name, rows in groups.items()}
    bars = {name: _bar_table(rows, name) for name, rows in groups.items()}
    timing = _timing(signals)
    stage_rows = _stage_rows(scanned["canon"])
    order = _order_material(stage_rows, scanned["rca"])
    all_table = tables["ALL_TECHNICAL_SIGNALS"]
    orig = tables["ORIGINAL18"]
    ext = tables["EXTENSION17"]
    fold_np = sum(1 for name in ("FOLD_A", "FOLD_B", "FOLD_C") if _nonpositive_horizons(tables[name]) >= 3)
    fold_pos = sum(1 for name in ("FOLD_A", "FOLD_B", "FOLD_C") if _positive_horizons(tables[name]) >= 3)
    # Case A/B use the precommitted cross-group rule. Case C is the timing gate.
    if timing["timing_material"]:
        classification = CASE_C
    elif _nonpositive_horizons(all_table) >= 3 and _nonpositive_horizons(orig) >= 3 and _nonpositive_horizons(ext) >= 3 and fold_np >= 2:
        classification = CASE_A
    elif (
        _positive_horizons(all_table) >= 3
        and _positive_horizons(orig) >= 3
        and _positive_horizons(ext) >= 3
        and fold_pos >= 2
        and _atb_negative_horizons(all_table) >= 3
        and _spread_exceeds(all_table) >= 3
    ):
        classification = CASE_B
    else:
        classification = CASE_D
    subgroup = {name: _classify_table(tables[name], False) for name in ("ORIGINAL18", "EXTENSION17", "FOLD_A", "FOLD_B", "FOLD_C", "BOARD_PASS", "BOARD_VETO")}
    if order["material_change"]:
        verdict, nxt, primary = VERDICT_ORDER, NEXT_ORDER, None
    elif classification == CASE_A:
        verdict, nxt, primary = VERDICT_RAW, NEXT_RAW, "SYMBOL_SETUP_EDGE_NOT_ESTABLISHED"
    elif classification == CASE_B:
        verdict, nxt, primary = VERDICT_FRICTION, NEXT_FRICTION, "EXECUTABLE_EDGE_TOO_SMALL"
    elif classification == CASE_C:
        verdict, nxt, primary = VERDICT_TIME, NEXT_TIME, None
    else:
        verdict, nxt, primary = VERDICT_OPEN, NEXT_OPEN, None
    return {
        "verdict": verdict,
        "next": nxt,
        "primary_deficiency": primary,
        "price_reference_classification": classification,
        "subgroup_classification": subgroup,
        "tables": tables,
        "bars": bars,
        "timing": timing,
        "stages": stage_rows,
        "order_effect": order,
        "fold_nonpositive_n": fold_np,
        "fold_positive_n": fold_pos,
        "denominator": "ask0",
        "bar_denominator": "signal_close",
        "historical_verdict_preserved": True,
    }
