"""Corrected selection metrics. Clock labels frozen from P3-0."""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

import numpy as np

_NATIVE = Path(__file__).resolve().parents[3]
if str(_NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(_NATIVE / "scripts"))

from research.fixed_selection_diagnostic_reconcile_p3_0r import (
    MECH_CLOCK_DOMINANT,
    MECH_MIXED,
    MECH_NONE,
    MECH_SEL_DOMINANT_CLOCK_TOP3,
    SELECTION_MIXED,
    SELECTION_NOT_SUPPORTED,
    SELECTION_SUPPORTED,
)
from run_p0_3_exact_runtime_replay_20260820 import _pf


def pf_out(v: Any) -> Any:
    if v is None:
        return None
    if v == float("inf"):
        return "Infinity"
    return v


def _mean_gt(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return False
    if fa != fa or fb != fb:
        return False
    return fa > fb


def group_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    fills = [r for r in rows if r.get("independent_filled")]
    pnls = [float(r["independent_pnl"]) for r in fills if r.get("independent_pnl") is not None]
    gp = sum(p for p in pnls if p > 0)
    gl = sum(-p for p in pnls if p < 0)
    w = sum(1 for p in pnls if p > 1e-9)
    l = sum(1 for p in pnls if p < -1e-9)
    d = len(pnls) - w - l
    eligible_pnls = []
    for r in rows:
        if r.get("independent_filled") and r.get("independent_pnl") is not None:
            eligible_pnls.append(float(r["independent_pnl"]))
        else:
            eligible_pnls.append(0.0)
    return {
        "n": n,
        "fill_n": len(fills),
        "fill_rate": (len(fills) / n) if n else 0.0,
        "win": w,
        "loss": l,
        "draw": d,
        "win_rate": (w / len(pnls)) if pnls else None,
        "mean_pnl_per_eligible": float(np.mean(eligible_pnls)) if eligible_pnls else None,
        "mean_pnl_per_filled": float(np.mean(pnls)) if pnls else None,
        "median_pnl_per_filled": float(np.median(pnls)) if pnls else None,
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "PF": pf_out(_pf(pnls) if pnls else None),
        "note_eligible_mean": "no-fill counted as 0 yen in MEAN_PNL_PER_ELIGIBLE_CANDIDATE",
        "note_filled_mean": "MEAN_PNL_PER_FILLED_TRADE uses filled trades only",
    }


def selection_pair(sel: dict[str, Any], nos: dict[str, Any]) -> str:
    better = sel["fill_rate"] > nos["fill_rate"] and _mean_gt(
        sel.get("mean_pnl_per_filled"), nos.get("mean_pnl_per_filled")
    )
    worse = nos["fill_rate"] > sel["fill_rate"] and _mean_gt(
        nos.get("mean_pnl_per_filled"), sel.get("mean_pnl_per_filled")
    )
    if better:
        return "BETTER"
    if worse:
        return "WORSE"
    return "MIXED"


def selection_verdict(all_pair: str, rest_pair: str) -> str:
    if all_pair == "BETTER" and rest_pair == "BETTER":
        return SELECTION_SUPPORTED
    if all_pair == "WORSE" and rest_pair == "WORSE":
        return SELECTION_NOT_SUPPORTED
    return SELECTION_MIXED


def slice_label(pair: str) -> str:
    if pair == "BETTER":
        return SELECTION_SUPPORTED
    if pair == "WORSE":
        return SELECTION_NOT_SUPPORTED
    return SELECTION_MIXED


def rank_strata(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if not r.get("feature_evaluable"):
            continue
        if r.get("rank") is None:
            continue
        by[(str(r["date"]), str(r["anchor_time"]))].append(r)
    buckets: dict[int, list[dict[str, Any]]] = {i: [] for i in range(5)}
    for group in by.values():
        n = len(group)
        if n <= 0:
            continue
        for r in group:
            q = min(4, int(int(float(r["rank"])) * 5 / n))
            buckets[q].append(r)
    out = []
    for q in range(5):
        m = group_metrics(buckets[q])
        m["quintile"] = f"Q{q + 1}"
        out.append(m)
    return out


def within_anchor(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if not r.get("feature_evaluable"):
            continue
        by[(str(r["date"]), str(r["anchor_time"]))].append(r)

    def _pack(groups: list[tuple], *, require_fill_both: bool) -> dict[str, Any]:
        diffs: list[float] = []
        better = worse = equal = 0
        used = 0
        for _k, group in groups:
            sel = [r for r in group if r.get("selected")]
            nos = [r for r in group if not r.get("selected")]
            if not sel or not nos:
                continue
            if require_fill_both:
                if not any(r.get("independent_filled") for r in sel):
                    continue
                if not any(r.get("independent_filled") for r in nos):
                    continue

            def mean0(xs: list[dict[str, Any]]) -> float:
                vals = [
                    float(r["independent_pnl"]) if r.get("independent_filled") and r.get("independent_pnl") is not None else 0.0
                    for r in xs
                ]
                return float(np.mean(vals)) if vals else 0.0

            d = mean0(sel) - mean0(nos)
            diffs.append(d)
            used += 1
            if d > 1e-9:
                better += 1
            elif d < -1e-9:
                worse += 1
            else:
                equal += 1
        return {
            "selected_better": better,
            "selected_worse": worse,
            "equal": equal,
            "median_difference": None if not diffs else float(np.median(diffs)),
            "n_anchors": used,
        }

    items = sorted(by.items())
    all_a = _pack(items, require_fill_both=False)
    both = _pack(items, require_fill_both=True)
    both["anchors_with_any_fill_both_groups"] = both["n_anchors"]
    return {"all_eligible_anchors": all_a, "fill_both_groups": both}


def mechanism(sel: str) -> str:
    """Clock uniqueness on REST11 is frozen as not special. Keep that in the label."""
    if sel == SELECTION_SUPPORTED:
        return MECH_SEL_DOMINANT_CLOCK_TOP3
    if sel == SELECTION_NOT_SUPPORTED:
        return MECH_CLOCK_DOMINANT
    if sel == SELECTION_MIXED:
        return MECH_MIXED
    return MECH_NONE


def mismatch_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    c = Counter(str(r.get("klass") or "OTHER") for r in rows)
    return dict(c)
