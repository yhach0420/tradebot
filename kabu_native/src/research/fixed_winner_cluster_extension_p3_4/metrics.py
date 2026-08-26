"""Extension-gate discrimination, incremental 600→750, mechanism labels. Descriptive only."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.canonical_fixed_pnl_source_p3_3.ledger import pnl
from research.fixed_winner_cluster_extension_p3_4 import (
    GATE_MIXED,
    GATE_NOT,
    GATE_SUPPORTED,
    MECH_INCR,
    MECH_LABEL,
    MECH_MIXED,
    MECH_NODISC,
    MECH_NONE,
    PREDECLARED_TOP3,
    REST11,
)


def _finite(xs: list[Any]) -> np.ndarray:
    out = []
    for x in xs:
        try:
            v = float(x)
        except (TypeError, ValueError):
            continue
        if v == v and v not in (float("inf"), float("-inf")):
            out.append(v)
    return np.asarray(out, dtype=float)


def ret_stats(xs: list[Any]) -> dict[str, Any]:
    a = _finite(xs)
    if a.size == 0:
        return {"n": 0, "mean": None, "median": None, "positive_rate": None}
    return {
        "n": int(a.size),
        "mean": float(np.mean(a)),
        "median": float(np.median(a)),
        "positive_rate": float(np.mean(a > 0)),
    }


def yen_stats(xs: list[Any]) -> dict[str, Any]:
    a = _finite(xs)
    if a.size == 0:
        return {"n": 0, "sum": None, "mean": None, "median": None}
    return {
        "n": int(a.size),
        "sum": float(np.sum(a)),
        "mean": float(np.mean(a)),
        "median": float(np.median(a)),
    }


def _slice_days(rows: list[dict[str, Any]], days) -> list[dict[str, Any]]:
    return [r for r in rows if str(r.get("date")) in set(days)]


def group_future(rows: list[dict[str, Any]], klass: str) -> dict[str, Any]:
    sel = [
        r
        for r in rows
        if r.get("canonical_class") == klass and r.get("outcome_evaluable")
    ]
    return {
        "n_canonical": sum(1 for r in rows if r.get("canonical_class") == klass),
        "n_evaluable": len(sel),
        "future_bid": ret_stats([r.get("bid_ret_600_750") for r in sel]),
        "future_mid": ret_stats([r.get("mid_ret_600_750") for r in sel]),
    }


def compare_extend_exit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "EXTEND_TO_750": group_future(rows, "EXTEND_TO_750"),
        "EXIT_AT_600": group_future(rows, "EXIT_AT_600"),
    }


def same_anchor(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Cells with both EXTEND and EXIT600 on same date + Fixed anchor."""
    by: dict[tuple[str, str], list] = defaultdict(list)
    for r in rows:
        if r.get("canonical_class") not in ("EXTEND_TO_750", "EXIT_AT_600"):
            continue
        if not r.get("outcome_evaluable"):
            continue
        by[(str(r.get("date")), str(r.get("anchor_time")))].append(r)
    better = worse = equal = 0
    diffs_mean = []
    diffs_med = []
    cells = []
    for (day, an), rs in sorted(by.items()):
        ext = [r for r in rs if r.get("canonical_class") == "EXTEND_TO_750"]
        exi = [r for r in rs if r.get("canonical_class") == "EXIT_AT_600"]
        if not ext or not exi:
            continue
        e_m = float(np.mean([float(r["bid_ret_600_750"]) for r in ext]))
        x_m = float(np.mean([float(r["bid_ret_600_750"]) for r in exi]))
        e_d = float(np.median([float(r["bid_ret_600_750"]) for r in ext]))
        x_d = float(np.median([float(r["bid_ret_600_750"]) for r in exi]))
        dmean = e_m - x_m
        dmed = e_d - x_d
        diffs_mean.append(dmean)
        diffs_med.append(dmed)
        if abs(dmean) <= 1e-15:
            tag = "equal"
            equal += 1
        elif dmean > 0:
            tag = "extend_better"
            better += 1
        else:
            tag = "extend_worse"
            worse += 1
        cells.append(
            {
                "date": day,
                "anchor_time": an,
                "n_extend": len(ext),
                "n_exit600": len(exi),
                "extend_mean": e_m,
                "exit600_mean": x_m,
                "mean_difference": dmean,
                "median_difference": dmed,
                "tag": tag,
            }
        )
    return {
        "n_cells": len(cells),
        "extend_better": better,
        "extend_worse": worse,
        "equal": equal,
        "mean_difference": float(np.mean(diffs_mean)) if diffs_mean else None,
        "median_difference": float(np.median(diffs_med)) if diffs_med else None,
        "cells": cells,
    }


def _gt(a: Optional[float], b: Optional[float]) -> bool:
    return a is not None and b is not None and float(a) > float(b)


def gate_verdict(all_cmp: dict, rest_cmp: dict, rest_sa: dict, full_sa: dict) -> dict[str, Any]:
    e_all = (all_cmp.get("EXTEND_TO_750") or {}).get("future_bid") or {}
    x_all = (all_cmp.get("EXIT_AT_600") or {}).get("future_bid") or {}
    e_r = (rest_cmp.get("EXTEND_TO_750") or {}).get("future_bid") or {}
    x_r = (rest_cmp.get("EXIT_AT_600") or {}).get("future_bid") or {}
    rest_mean = _gt(e_r.get("mean"), x_r.get("mean"))
    rest_med = _gt(e_r.get("median"), x_r.get("median"))
    rest_sa_ok = int(rest_sa.get("extend_better") or 0) > int(rest_sa.get("extend_worse") or 0)
    full_mean = _gt(e_all.get("mean"), x_all.get("mean"))
    full_med = _gt(e_all.get("median"), x_all.get("median"))
    full_dir = full_mean and full_med
    if rest_mean and rest_med and rest_sa_ok and full_dir:
        label = GATE_SUPPORTED
        why = "REST11 mean+median Bid 600→750 EXTEND>EXIT600 and same-anchor better>worse; FULL14 same direction"
    elif rest_mean or rest_med or rest_sa_ok or full_mean or full_med:
        label = GATE_MIXED
        why = "some 600→750 Bid comparisons favor EXTEND, but REST11 mean+median+same-anchor and FULL14 direction are not all met"
    else:
        label = GATE_NOT
        why = "EXTEND 600→750 Bid return is not better than EXIT600 on REST11 / FULL14"
    return {
        "EXTENSION_GATE": label,
        "why": why,
        "REST11_mean_extend_gt_exit": rest_mean,
        "REST11_median_extend_gt_exit": rest_med,
        "REST11_same_anchor_better_gt_worse": rest_sa_ok,
        "FULL14_mean_extend_gt_exit": full_mean,
        "FULL14_median_extend_gt_exit": full_med,
    }


def incremental_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ext = [r for r in rows if r.get("canonical_class") == "EXTEND_TO_750" and r.get("outcome_evaluable")]
    top = [r for r in ext if str(r.get("date")) in set(PREDECLARED_TOP3)]
    rest = [r for r in ext if str(r.get("date")) in set(REST11)]

    def pack(sel: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "n": len(sel),
            "ENTRY_TO_600": ret_stats([r.get("ret_entry_to_600") for r in sel]),
            "600_TO_750": ret_stats([r.get("ret_600_to_750") for r in sel]),
            "ENTRY_TO_750": ret_stats([r.get("ret_entry_to_750") for r in sel]),
            "EXECUTABLE_INCREMENTAL_VALUE_YEN": yen_stats([r.get("incremental_value_600_750_yen") for r in sel]),
            "label": "EXECUTABLE_INCREMENTAL_VALUE_DIAGNOSTIC",
        }

    return {"ALL": pack(ext), "TOP3": pack(top), "REST11": pack(rest)}


def interpret_incremental(incr: dict[str, Any], gate: str) -> dict[str, Any]:
    all_b = incr.get("ALL") or {}
    e600 = (all_b.get("ENTRY_TO_600") or {})
    e750 = (all_b.get("600_TO_750") or {})
    m0 = e600.get("median")
    m1 = e750.get("median")
    pr = e750.get("positive_rate")
    case = None
    if m0 is not None and m1 is not None:
        if float(m0) > 0.002 and abs(float(m1)) < 0.001:
            case = "A"
        elif float(m0) > 0 and float(m1) > 0.001 and (pr is None or float(pr) >= 0.55):
            case = "B"
        elif float(m0) > 0 and float(m1) < 0:
            case = "C"
        elif float(m0) > 0 and float(m1) > 0:
            case = "B"
        else:
            case = "A" if abs(float(m1 or 0)) <= abs(float(m0 or 0)) * 0.15 else "C"
    if gate == GATE_NOT:
        mech = MECH_NODISC
    elif case == "A":
        mech = MECH_LABEL
    elif case == "B":
        mech = MECH_INCR
    elif case == "C":
        mech = MECH_MIXED
    else:
        mech = MECH_NONE
    return {
        "EXTENSION_MECHANISM": mech,
        "case": case,
        "case_note": {
            "A": "ENTRY→600 already large; 600→750 ≈ 0. CONT_EXTEND mainly labels already-large winners.",
            "B": "ENTRY→600 positive and 600→750 clearly positive. Extra 150s contributed.",
            "C": "ENTRY→600 positive; 600→750 negative. Gate may identify winners but extra hold erodes.",
        }.get(case or "", ""),
        "A_final_pnl_high": True,
        "B_identified_at_600": gate,
        "C_incremental_600_750": case,
    }
