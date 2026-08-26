"""Decision-aligned gate, incremental value, mechanism. Descriptive only. No retune."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.canonical_fixed_pnl_source_p3_3.metrics import dist, spearman
from research.extension_decision_alignment_p3_4r import (
    GATE_MIXED,
    GATE_NOT,
    GATE_SUPPORTED,
    MECH_MIXED,
    MECH_NODISC,
    MECH_NONE,
    MECH_POST,
    MECH_PRE,
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
        return {"n": 0, "sum": None, "mean": None, "median": None, "positive_rate": None}
    return {
        "n": int(a.size),
        "sum": float(np.sum(a)),
        "mean": float(np.mean(a)),
        "median": float(np.median(a)),
        "positive_rate": float(np.mean(a > 0)),
    }


def delay_stats(xs: list[Any]) -> dict[str, Any]:
    a = _finite(xs)
    if a.size == 0:
        return {"n": 0, "mean": None, "median": None, "p90": None, "max": None}
    d = dist(list(a))
    return {
        "n": int(a.size),
        "mean": float(np.mean(a)),
        "median": float(np.median(a)),
        "p90": d.get("p90"),
        "max": float(np.max(a)),
    }


def _sel(rows: list[dict[str, Any]], klass: str, flag: str) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("canonical_class") == klass and r.get(flag)]


def group_metric(rows: list[dict[str, Any]], klass: str, flag: str, key: str) -> dict[str, Any]:
    sel = _sel(rows, klass, flag)
    return {
        "n_canonical": sum(1 for r in rows if r.get("canonical_class") == klass),
        "n_evaluable": len(sel),
        **ret_stats([r.get(key) for r in sel]),
    }


def compare_post(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def pack(klass: str) -> dict[str, Any]:
        prim = _sel(rows, klass, "primary_evaluable")
        plus = _sel(rows, klass, "plus150_evaluable")
        return {
            "n_canonical": sum(1 for r in rows if r.get("canonical_class") == klass),
            "n_evaluable": len(prim),
            "decision_to_750_bid": ret_stats([r.get("decision_to_750_bid_return") for r in prim]),
            "decision_to_750_mid": ret_stats([r.get("decision_to_750_mid_return") for r in prim]),
            "decision_plus150_bid": ret_stats([r.get("decision_plus150_bid_return") for r in plus]),
            "decision_plus150_mid": ret_stats([r.get("decision_plus150_mid_return") for r in plus]),
            "plus150_label": "STANDARDIZED_POST_DECISION_150S_DIAGNOSTIC",
        }

    return {"EXTEND_TO_750": pack("EXTEND_TO_750"), "EXIT_AT_600": pack("EXIT_AT_600")}


def compare_pre(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def pack(klass: str) -> dict[str, Any]:
        sel = [
            r
            for r in rows
            if r.get("canonical_class") == klass and r.get("predecision_bid_return") is not None
        ]
        return {"n": len(sel), **ret_stats([r.get("predecision_bid_return") for r in sel])}

    return {"EXTEND_TO_750": pack("EXTEND_TO_750"), "EXIT_AT_600": pack("EXIT_AT_600")}


def compare_old(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def pack(klass: str) -> dict[str, Any]:
        sel = [
            r
            for r in rows
            if r.get("canonical_class") == klass and r.get("old_bid_ret_600_750") is not None
        ]
        return {
            "n": len(sel),
            "label": "OLD_NOMINAL_600_BASED",
            **ret_stats([r.get("old_bid_ret_600_750") for r in sel]),
        }

    return {"EXTEND_TO_750": pack("EXTEND_TO_750"), "EXIT_AT_600": pack("EXIT_AT_600")}


def same_anchor(rows: list[dict[str, Any]], key: str = "decision_to_750_bid_return") -> dict[str, Any]:
    by: dict[tuple[str, str], list] = defaultdict(list)
    for r in rows:
        if r.get("canonical_class") not in ("EXTEND_TO_750", "EXIT_AT_600"):
            continue
        if not r.get("primary_evaluable") or r.get(key) is None:
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
        e_m = float(np.mean([float(r[key]) for r in ext]))
        x_m = float(np.mean([float(r[key]) for r in exi]))
        e_d = float(np.median([float(r[key]) for r in ext]))
        x_d = float(np.median([float(r[key]) for r in exi]))
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


def gate_verdict(all_cmp: dict, rest_cmp: dict, rest_sa: dict) -> dict[str, Any]:
    e_all = (all_cmp.get("EXTEND_TO_750") or {}).get("decision_to_750_bid") or {}
    x_all = (all_cmp.get("EXIT_AT_600") or {}).get("decision_to_750_bid") or {}
    e_r = (rest_cmp.get("EXTEND_TO_750") or {}).get("decision_to_750_bid") or {}
    x_r = (rest_cmp.get("EXIT_AT_600") or {}).get("decision_to_750_bid") or {}
    rest_mean = _gt(e_r.get("mean"), x_r.get("mean"))
    rest_med = _gt(e_r.get("median"), x_r.get("median"))
    rest_sa_ok = int(rest_sa.get("extend_better") or 0) > int(rest_sa.get("extend_worse") or 0)
    full_mean = _gt(e_all.get("mean"), x_all.get("mean"))
    full_med = _gt(e_all.get("median"), x_all.get("median"))
    full_dir = full_mean and full_med
    if rest_mean and rest_med and rest_sa_ok and full_dir:
        label = GATE_SUPPORTED
        why = (
            "REST11 mean+median Bid decision→750 EXTEND>EXIT600 and same-anchor better>worse; "
            "FULL14 same direction. Not inherited from P3-4."
        )
    elif rest_mean or rest_med or rest_sa_ok or full_mean or full_med:
        label = GATE_MIXED
        why = (
            "some decision→750 Bid comparisons favor EXTEND, but REST11 mean+median+same-anchor "
            "and FULL14 direction are not all met"
        )
    else:
        label = GATE_NOT
        why = "EXTEND decision→750 Bid return is not better than EXIT600 on REST11 / FULL14"
    return {
        "EXTENSION_GATE_REVISED": label,
        "why": why,
        "REST11_mean_extend_gt_exit": rest_mean,
        "REST11_median_extend_gt_exit": rest_med,
        "REST11_same_anchor_better_gt_worse": rest_sa_ok,
        "FULL14_mean_extend_gt_exit": full_mean,
        "FULL14_median_extend_gt_exit": full_med,
        "P3_4_verdict_inherited": False,
    }


def incremental_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ext = [r for r in rows if r.get("canonical_class") == "EXTEND_TO_750"]
    top = [r for r in ext if str(r.get("date")) in set(PREDECLARED_TOP3)]
    rest = [r for r in ext if str(r.get("date")) in set(REST11)]

    def pack(sel: list[dict[str, Any]]) -> dict[str, Any]:
        prim = [r for r in sel if r.get("primary_evaluable")]
        old = [r for r in sel if r.get("old_600_750_value_yen") is not None]
        pre = [r for r in sel if r.get("predecision_value_yen") is not None]
        return {
            "n": len(sel),
            "n_primary_evaluable": len(prim),
            "OLD_NOMINAL_600_BASED": {
                "label": "OLD_NOMINAL_600_BASED",
                **yen_stats([r.get("old_600_750_value_yen") for r in old]),
                "bid_return": ret_stats([r.get("old_bid_ret_600_750") for r in old]),
            },
            "PREDECISION_VALUE_YEN": yen_stats([r.get("predecision_value_yen") for r in pre]),
            "POST_DECISION_INCREMENTAL_VALUE_YEN": yen_stats([r.get("post_decision_value_yen") for r in prim]),
            "identity_n": sum(1 for r in sel if r.get("identity_pass") is not None),
            "identity_pass": sum(1 for r in sel if r.get("identity_pass") is True),
        }

    return {"ALL": pack(ext), "TOP3": pack(top), "REST11": pack(rest)}


def delay_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reached = [r for r in rows if r.get("reached_600") and r.get("decision_delay_sec") is not None]
    ext = [r for r in reached if r.get("canonical_class") == "EXTEND_TO_750"]
    exi = [r for r in reached if r.get("canonical_class") == "EXIT_AT_600"]

    def rho(sel: list[dict[str, Any]]) -> dict[str, Any]:
        prim = [r for r in sel if r.get("primary_evaluable") and r.get("decision_to_750_bid_return") is not None]
        return spearman(
            [r.get("decision_delay_sec") for r in prim],
            [r.get("decision_to_750_bid_return") for r in prim],
        )

    return {
        "ALL": delay_stats([r.get("decision_delay_sec") for r in reached]),
        "EXTEND_TO_750": delay_stats([r.get("decision_delay_sec") for r in ext]),
        "EXIT_AT_600": delay_stats([r.get("decision_delay_sec") for r in exi]),
        "spearman_delay_vs_decision_to_750_bid": {
            "ALL": rho(reached),
            "EXTEND_TO_750": rho(ext),
            "EXIT_AT_600": rho(exi),
            "note": "Descriptive only. No delay threshold created.",
        },
    }


def interpret_mechanism(incr: dict[str, Any], gate: str, pre: dict[str, Any], post_cmp: dict[str, Any]) -> dict[str, Any]:
    post_bid = ((post_cmp.get("EXTEND_TO_750") or {}).get("decision_to_750_bid") or {})
    pre_ext = pre.get("EXTEND_TO_750") or {}
    m_post = post_bid.get("median")
    pr_post = post_bid.get("positive_rate")
    m_pre = pre_ext.get("median")
    case = None
    if m_post is not None:
        if abs(float(m_post)) < 0.001 and m_pre is not None and float(m_pre) > 0.001:
            case = "PRE"
        elif float(m_post) > 0.001 and (pr_post is None or float(pr_post) >= 0.55):
            case = "POST"
        elif float(m_post) < 0:
            case = "MIXED"
        elif float(m_post) > 0:
            case = "POST"
        else:
            case = "PRE"
    if gate == GATE_NOT:
        mech = MECH_NODISC
    elif case == "PRE":
        mech = MECH_PRE
    elif case == "POST" and gate == GATE_SUPPORTED:
        mech = MECH_POST
    elif case == "POST" and gate == GATE_MIXED:
        mech = MECH_MIXED
    elif case == "MIXED":
        mech = MECH_MIXED
    elif gate == GATE_SUPPORTED:
        mech = MECH_POST if (m_post is not None and float(m_post) > 0) else MECH_MIXED
    else:
        mech = MECH_NONE
    return {
        "EXTENSION_MECHANISM_REVISED": mech,
        "case": case,
        "post_decision_yen": (incr.get("ALL") or {}).get("POST_DECISION_INCREMENTAL_VALUE_YEN"),
        "note": {
            "PRE": "decision→750 yen ≈ 0 while 600→decision move is material. Old 600→750 mixed pre-decision path.",
            "POST": "post-decision Bid/yen to 750 clearly positive after actual 600_DECISION.",
            "MIXED": "gate may still split groups, but post-decision incremental value is mixed/negative.",
        }.get(case or "", ""),
        "A_already_winner_at_600": m_pre is not None and float(m_pre) > 0,
        "B_identified_at_actual_decision": gate,
        "C_post_decision_to_750": case,
        "old_374300_is_not_C": True,
    }


def slice_days(rows: list[dict[str, Any]], days) -> list[dict[str, Any]]:
    return [r for r in rows if str(r.get("date")) in set(days)]
