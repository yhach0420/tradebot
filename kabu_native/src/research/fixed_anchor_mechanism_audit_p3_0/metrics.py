"""P3-0 aggregations and precommitted clock / selection labels."""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import numpy as np

_NATIVE = Path(__file__).resolve().parents[3]
if str(_NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(_NATIVE / "scripts"))

from research.fixed_anchor_mechanism_audit_p3_0 import (
    CLOCK_EXACT_TIME_SUPPORTED,
    CLOCK_NOT_UNIQUELY_SPECIAL,
    CLOCK_TIMING_MIXED,
    MECH_BOTH,
    MECH_EXACT_CLOCK,
    MECH_MIXED,
    MECH_NONE,
    MECH_SELECTION,
    MECH_TOP3,
    SELECTION_MIXED,
    SELECTION_NOT_SUPPORTED,
    SELECTION_SUPPORTED,
)
from run_p0_3_exact_runtime_replay_20260820 import _maxdd, _pf


def pf_out(v: Any) -> Any:
    if v is None:
        return None
    if v == float("inf"):
        return "Infinity"
    return v


def pf_num(v: Any) -> float:
    if v is None:
        return float("nan")
    if v == "Infinity" or v == float("inf"):
        return float("inf")
    return float(v)


def trade_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(t.get("pnl_yen_100") or 0.0) for t in trades]
    w = sum(1 for p in pnls if p > 1e-9)
    l = sum(1 for p in pnls if p < -1e-9)
    d = len(pnls) - w - l
    gp = sum(p for p in pnls if p > 0)
    gl = sum(-p for p in pnls if p < 0)
    pnl = round(sum(pnls), 2)
    am = [t for t in trades if t.get("session") == "AM"]
    pm = [t for t in trades if t.get("session") == "PM"]
    return {
        "trades": len(trades),
        "win": w,
        "loss": l,
        "draw": d,
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "pnl": pnl,
        "PF": pf_out(_pf(pnls)),
        "avg_pnl": round(pnl / len(trades), 4) if trades else 0.0,
        "maxDD": _maxdd(trades),
        "AM_pnl": round(sum(float(t.get("pnl_yen_100") or 0.0) for t in am), 2),
        "PM_pnl": round(sum(float(t.get("pnl_yen_100") or 0.0) for t in pm), 2),
        "AM_trades": len(am),
        "PM_trades": len(pm),
    }


def clock_label(orig: dict[str, Any], shifts: list[dict[str, Any]]) -> str:
    any_beats = False
    orig_beats_all = True
    op = float(orig.get("pnl") or 0.0)
    o_pf = pf_num(orig.get("PF"))
    for s in shifts:
        sp = float(s.get("pnl") or 0.0)
        s_pf = pf_num(s.get("PF"))
        if sp > op and s_pf > o_pf:
            any_beats = True
        if not (op > sp and o_pf > s_pf):
            orig_beats_all = False
    if any_beats:
        return CLOCK_NOT_UNIQUELY_SPECIAL
    if orig_beats_all and shifts:
        return CLOCK_EXACT_TIME_SUPPORTED
    return CLOCK_TIMING_MIXED


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


def independent_group_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    fills = [r for r in rows if r.get("independent_filled")]
    pnls = [float(r["independent_pnl"]) for r in fills if r.get("independent_pnl") is not None]
    gp = sum(p for p in pnls if p > 0)
    gl = sum(-p for p in pnls if p < 0)
    w = sum(1 for p in pnls if p > 1e-9)
    return {
        "n": n,
        "fill_n": len(fills),
        "fill_rate": (len(fills) / n) if n else 0.0,
        "win_rate": (w / len(pnls)) if pnls else None,
        "mean_independent_pnl": None if not pnls else float(np.mean(pnls)),
        "median_independent_pnl": None if not pnls else float(np.median(pnls)),
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "PF": pf_out(_pf(pnls) if pnls else None),
    }


def selection_pair_result(sel: dict[str, Any], nosel: dict[str, Any]) -> str:
    better = sel["fill_rate"] > nosel["fill_rate"] and _mean_gt(
        sel.get("mean_independent_pnl"), nosel.get("mean_independent_pnl")
    )
    worse = nosel["fill_rate"] > sel["fill_rate"] and _mean_gt(
        nosel.get("mean_independent_pnl"), sel.get("mean_independent_pnl")
    )
    if better:
        return "BETTER"
    if worse:
        return "WORSE"
    return "MIXED"


def selection_result(*, all_pair: str, rest_pair: str) -> str:
    if all_pair == "BETTER" and rest_pair == "BETTER":
        return SELECTION_SUPPORTED
    if all_pair == "WORSE" and rest_pair == "WORSE":
        return SELECTION_NOT_SUPPORTED
    return SELECTION_MIXED


def slice_pair_to_label(pair: str) -> str:
    if pair == "BETTER":
        return SELECTION_SUPPORTED
    if pair == "WORSE":
        return SELECTION_NOT_SUPPORTED
    return SELECTION_MIXED


def rank_strata(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_anchor: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if not r.get("feature_evaluable"):
            continue
        if r.get("rank") is None:
            continue
        by_anchor[(str(r["date"]), str(r["anchor_time"]))].append(r)
    buckets: dict[int, list[dict[str, Any]]] = {i: [] for i in range(5)}
    for _k, group in by_anchor.items():
        n = len(group)
        if n <= 0:
            continue
        for r in group:
            q = min(4, int(int(r["rank"]) * 5 / n))
            buckets[q].append(r)
    out = []
    for q in range(5):
        m = independent_group_metrics(buckets[q])
        m["quintile"] = f"Q{q + 1}"
        m["rank_meaning"] = "highest_alloc_score" if q == 0 else ("lowest_alloc_score" if q == 4 else "")
        out.append(m)
    return out


def monotonicity(strata: list[dict[str, Any]], field: str) -> dict[str, Any]:
    vals = []
    for s in strata:
        v = s.get(field)
        if v is None:
            vals.append(None)
        else:
            vals.append(float(v) if v != "Infinity" else float("inf"))
    present = [(i, v) for i, v in enumerate(vals) if v is not None]
    non_increasing = True
    for i in range(1, len(present)):
        if present[i][1] > present[i - 1][1] + 1e-12:
            non_increasing = False
            break
    return {
        "field": field,
        "q1_to_q5": [s.get(field) for s in strata],
        "non_increasing": bool(non_increasing and len(present) >= 2),
    }


def within_anchor(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if not r.get("feature_evaluable"):
            continue
        by[(str(r["date"]), str(r["anchor_time"]))].append(r)
    diffs: list[float] = []
    better = worse = equal = 0
    detail = []
    for (day, an), group in sorted(by.items()):
        sel = [r for r in group if r.get("selected")]
        nos = [r for r in group if not r.get("selected")]
        if not sel or not nos:
            continue

        def _mean0(xs: list[dict[str, Any]]) -> float:
            vals = []
            for r in xs:
                if r.get("independent_filled") and r.get("independent_pnl") is not None:
                    vals.append(float(r["independent_pnl"]))
                else:
                    vals.append(0.0)
            return float(np.mean(vals)) if vals else 0.0

        d = _mean0(sel) - _mean0(nos)
        diffs.append(d)
        if d > 1e-9:
            better += 1
            side = "selected_better"
        elif d < -1e-9:
            worse += 1
            side = "selected_worse"
        else:
            equal += 1
            side = "equal"
        detail.append({"date": day, "anchor_time": an, "difference": d, "side": side})
    return {
        "selected_better_count": better,
        "selected_worse_count": worse,
        "equal": equal,
        "median_difference": None if not diffs else float(np.median(diffs)),
        "n_compared_anchors": len(diffs),
        "rows": detail,
    }


def mechanism_label(
    *,
    clock_all: str,
    sel_all: str,
    clock_top3: str,
    sel_top3: str,
    clock_rest: str,
    sel_rest: str,
) -> str:
    top3_dom = (
        clock_rest != CLOCK_EXACT_TIME_SUPPORTED
        and sel_rest != SELECTION_SUPPORTED
        and (clock_top3 == CLOCK_EXACT_TIME_SUPPORTED or sel_top3 == SELECTION_SUPPORTED)
    )
    if top3_dom:
        return MECH_TOP3
    if clock_all == CLOCK_EXACT_TIME_SUPPORTED and sel_all == SELECTION_SUPPORTED:
        return MECH_BOTH
    if clock_all == CLOCK_EXACT_TIME_SUPPORTED:
        return MECH_EXACT_CLOCK
    if sel_all == SELECTION_SUPPORTED:
        return MECH_SELECTION
    if clock_all == CLOCK_TIMING_MIXED or sel_all == SELECTION_MIXED:
        return MECH_MIXED
    return MECH_NONE


def filter_days(trades: list[dict[str, Any]], days: tuple[str, ...]) -> list[dict[str, Any]]:
    ds = set(days)
    return [t for t in trades if str(t.get("date")) in ds]


def filter_xs(rows: list[dict[str, Any]], days: Optional[tuple[str, ...]]) -> list[dict[str, Any]]:
    if days is None:
        return list(rows)
    ds = set(days)
    return [r for r in rows if str(r.get("date")) in ds]


def offset_key(off: int) -> str:
    if off == 0:
        return "original_common_support"
    sign = "+" if off > 0 else ""
    return f"{sign}{off}"
