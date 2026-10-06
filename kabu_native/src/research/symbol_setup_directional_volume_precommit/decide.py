"""Freeze the repair only after the volume contract and the baseline counts hold."""
from __future__ import annotations

from typing import Any, Optional

from research.symbol_setup_directional_volume_precommit import (
    CLASSIFICATION_FRACTION_MIN,
    FIELD_COVERAGE_MIN,
    NEXT_RESEARCH,
    NEXT_STOP,
    NEXT_STOP_REPAIR,
    PERIOD_GAP_MAX,
    PRICE_ACTION_PASS_N,
    RCI_PASS_N,
    VERDICT_INSUFFICIENT,
    VERDICT_PARITY,
    VERDICT_READY,
    VOLUME_PASS_N,
)


def _frac(num: float, den: float) -> Optional[float]:
    if den <= 0:
        return None
    return float(num) / float(den)


def _bucket(rows: list[dict[str, Any]], key: str, name: str) -> dict[str, Any]:
    picked = [row for row in rows if row.get(key) == name]
    volume = sum(float(row["volume"]) for row in picked)
    classified = sum(float(row["ask_vol"]) + float(row["bid_vol"]) for row in picked)
    eligible = sum(int(row["eligible_bar_n"]) for row in picked)
    both = sum(int(row["both_available_n"]) for row in picked)
    both_quote = sum(float(row["both_quote_volume"]) for row in picked)
    return {
        "scope": key,
        "name": name,
        "eligible_bar_n": eligible,
        "both_available_n": both,
        "coverage_fraction": _frac(both, eligible),
        "total_volume": volume,
        "classified_volume": classified,
        "unclassified_volume": volume - classified,
        "classification_fraction": _frac(classified, volume),
        "both_quote_volume_fraction": _frac(both_quote, volume),
    }


def _sum(rows: list[dict[str, Any]], key: str) -> float:
    return float(sum(float(row[key]) for row in rows))


def analyze(scanned: dict[str, Any]) -> dict[str, Any]:
    rows = list(scanned["days"])
    eligible = int(_sum(rows, "eligible_bar_n"))
    both = int(_sum(rows, "both_available_n"))
    volume = _sum(rows, "volume")
    ask = _sum(rows, "ask_vol")
    bid = _sum(rows, "bid_vol")
    up = _sum(rows, "up_vol")
    down = _sum(rows, "down_vol")
    classified = ask + bid
    both_quote = _sum(rows, "both_quote_volume")
    surface_frac = _frac(classified, volume)
    field_cov = _frac(both, eligible)
    groups = [_bucket(rows, "fold", name) for name in ("FOLD_A", "FOLD_B", "FOLD_C")]
    groups += [_bucket(rows, "lineage", name) for name in ("ORIGINAL18", "EXTENSION17")]
    dates = [_bucket(rows, "date", str(row["date"])) for row in rows]
    gaps = []
    for row in groups:
        frac = row["classification_fraction"]
        gap = None if frac is None or surface_frac is None else abs(float(frac) - float(surface_frac))
        gaps.append(gap)
        row["gap_vs_surface"] = gap
    comparable = all(gap is not None and gap <= float(PERIOD_GAP_MAX) for gap in gaps)
    sparse = (
        field_cov is None
        or float(field_cov) < float(FIELD_COVERAGE_MIN)
        or surface_frac is None
        or float(surface_frac) < float(CLASSIFICATION_FRACTION_MIN)
        or not comparable
    )
    rci = int(_sum(rows, "rci_pass_n"))
    vol = int(_sum(rows, "volume_pass_n"))
    price = int(_sum(rows, "price_action_pass_n"))
    parity = {
        "rci_pass_n": rci,
        "rci_expected": RCI_PASS_N,
        "rci_match": rci == int(RCI_PASS_N),
        "volume_pass_n": vol,
        "volume_expected": VOLUME_PASS_N,
        "volume_match": vol == int(VOLUME_PASS_N),
        "price_action_pass_n": price,
        "price_expected": PRICE_ACTION_PASS_N,
        "price_match": price == int(PRICE_ACTION_PASS_N),
    }
    parity_ok = bool(parity["rci_match"] and parity["volume_match"] and parity["price_match"])
    if not parity_ok:
        verdict, nxt, ready = VERDICT_PARITY, NEXT_STOP, False
    elif sparse:
        verdict, nxt, ready = VERDICT_INSUFFICIENT, NEXT_STOP_REPAIR, False
    else:
        verdict, nxt, ready = VERDICT_READY, NEXT_RESEARCH, True
    recon = {
        "total_volume": volume,
        "ask_vol": ask,
        "bid_vol": bid,
        "classified_volume": classified,
        "unclassified_volume": volume - classified,
        "classification_fraction": surface_frac,
        "up_vol": up,
        "down_vol": down,
        "up_down_candidate_defined": False,
        "both_quote_volume": both_quote,
        "both_quote_volume_fraction": _frac(both_quote, volume),
    }
    coverage = {
        "eligible_bar_n": eligible,
        "both_available_n": both,
        "coverage_fraction": field_cov,
        "both_available_definition": "completed bar whose ask_vol and bid_vol are both finite",
        "classification_fraction_min": CLASSIFICATION_FRACTION_MIN,
        "field_coverage_min": FIELD_COVERAGE_MIN,
        "period_gap_max": PERIOD_GAP_MAX,
        "sparse": sparse,
        "periods_comparable": comparable,
    }
    return {
        "verdict": verdict,
        "next": nxt,
        "repair_ready": ready,
        "parity": parity,
        "reconciliation": recon,
        "coverage": coverage,
        "by_group": groups,
        "by_date": dates,
    }
