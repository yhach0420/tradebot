"""P3-1 execution / directional / filled-outcome metrics. No new thresholds."""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import numpy as np

_NATIVE = Path(__file__).resolve().parents[3]
if str(_NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(_NATIVE / "scripts"))

from research.fixed_selection_diagnostic_reconcile_p3_0r.metrics import group_metrics
from research.fixed_selection_edge_decomposition_p3_1 import (
    DIR_MIXED,
    DIR_NOT,
    DIR_SUPPORTED,
    EDGE_BOTH,
    EDGE_DIR_DOM,
    EDGE_DIR_EXEC_MIXED,
    EDGE_EXEC_DIR_MIXED,
    EDGE_EXEC_DOM,
    EDGE_MIXED,
    EDGE_NONE,
    EXEC_MIXED,
    EXEC_NOT,
    EXEC_SUPPORTED,
    FILL_BUCKETS_MS,
    HORIZONS_SEC,
)
from small_paper.v1r_native_entry_live import FEATURE_ORDER


def _median(xs: list[float]) -> Optional[float]:
    vals = [float(x) for x in xs if x is not None and float(x) == float(x)]
    if not vals:
        return None
    return float(np.median(vals))


def _mean(xs: list[float]) -> Optional[float]:
    vals = [float(x) for x in xs if x is not None and float(x) == float(x)]
    if not vals:
        return None
    return float(np.mean(vals))


def _pct(xs: list[float], q: float) -> Optional[float]:
    vals = [float(x) for x in xs if x is not None and float(x) == float(x)]
    if not vals:
        return None
    return float(np.percentile(vals, q))


def execution_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    fills = [r for r in rows if r.get("independent_filled")]
    fill_n = len(fills)
    ttf = [float(r["time_to_fill_ms"]) for r in fills if r.get("time_to_fill_ms") is not None]
    first_bps = [r.get("first_ask_minus_limit_bps") for r in rows]
    min_bps = [r.get("min_ask_minus_limit_bps") for r in rows]
    buckets = {}
    for ms in FILL_BUCKETS_MS:
        c = sum(
            1
            for r in fills
            if r.get("time_to_fill_ms") is not None and float(r["time_to_fill_ms"]) <= ms + 1e-9
        )
        buckets[f"fill_n_le_{ms}ms"] = c
        buckets[f"fill_rate_le_{ms}ms"] = (c / n) if n else 0.0
    return {
        "n": n,
        "fill_n": fill_n,
        "fill_rate": (fill_n / n) if n else 0.0,
        "median_first_ask_minus_limit_bps": _median([x for x in first_bps if x is not None]),
        "median_min_ask_minus_limit_bps": _median([x for x in min_bps if x is not None]),
        "median_time_to_fill_ms": _median(ttf),
        **buckets,
    }


def directional_block(rows: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
    key = f"ret_{horizon}"
    st = f"status_{horizon}"
    evals = [r for r in rows if r.get(st) == "OK" and r.get(key) is not None]
    rets = [float(r[key]) for r in evals]
    pos = sum(1 for x in rets if x > 0)
    return {
        "horizon_sec": horizon,
        "label": "FILL_INDEPENDENT_DIRECTIONAL_DIAGNOSTIC",
        "n": len(rows),
        "n_evaluable": len(evals),
        "n_session_incomplete": sum(1 for r in rows if r.get(st) == "SESSION_INCOMPLETE"),
        "n_missing_price": sum(1 for r in rows if r.get(st) == "MISSING_PRICE"),
        "mean_return": _mean(rets),
        "median_return": _median(rets),
        "positive_return_rate": (pos / len(rets)) if rets else None,
    }


def same_anchor_rows(rows: list[dict[str, Any]], horizon: int) -> list[dict[str, Any]]:
    key = f"ret_{horizon}"
    st = f"status_{horizon}"
    by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[(str(r["date"]), str(r["anchor_time"]))].append(r)
    out = []
    for (day, an), group in sorted(by.items()):
        sel = [r for r in group if r.get("selected") and r.get(st) == "OK" and r.get(key) is not None]
        nos = [r for r in group if (not r.get("selected")) and r.get(st) == "OK" and r.get(key) is not None]
        if not sel or not nos:
            continue
        sm = _mean([float(r[key]) for r in sel])
        nm = _mean([float(r[key]) for r in nos])
        if sm is None or nm is None:
            continue
        d = sm - nm
        if d > 1e-12:
            side = "selected_better"
        elif d < -1e-12:
            side = "selected_worse"
        else:
            side = "equal"
        out.append(
            {
                "date": day,
                "anchor_time": an,
                "horizon_sec": horizon,
                "selected_n_evaluable": len(sel),
                "not_selected_n_evaluable": len(nos),
                "selected_mean_return": sm,
                "not_selected_mean_return": nm,
                "difference": d,
                "side": side,
            }
        )
    return out


def same_anchor_summary(detail: list[dict[str, Any]]) -> dict[str, Any]:
    diffs = [float(r["difference"]) for r in detail]
    better = sum(1 for r in detail if r.get("side") == "selected_better")
    worse = sum(1 for r in detail if r.get("side") == "selected_worse")
    equal = sum(1 for r in detail if r.get("side") == "equal")
    return {
        "selected_better_anchor_n": better,
        "selected_worse_anchor_n": worse,
        "equal_anchor_n": equal,
        "n_anchors": len(detail),
        "median_difference": _median(diffs),
        "mean_difference": _mean(diffs),
    }


def execution_verdict(
    all_sel: dict[str, Any],
    all_nos: dict[str, Any],
    rest_sel: dict[str, Any],
    rest_nos: dict[str, Any],
) -> str:
    fill_all = all_sel["fill_rate"] > all_nos["fill_rate"]
    fill_rest = rest_sel["fill_rate"] > rest_nos["fill_rate"]
    ms = all_sel.get("median_min_ask_minus_limit_bps")
    mn = all_nos.get("median_min_ask_minus_limit_bps")
    bps_ok = ms is not None and mn is not None and float(ms) < float(mn)
    fill_all_worse = all_sel["fill_rate"] < all_nos["fill_rate"]
    fill_rest_worse = rest_sel["fill_rate"] < rest_nos["fill_rate"]
    bps_worse = ms is not None and mn is not None and float(ms) > float(mn)
    if fill_all and fill_rest and bps_ok:
        return EXEC_SUPPORTED
    if fill_all_worse and fill_rest_worse and bps_worse:
        return EXEC_NOT
    return EXEC_MIXED


def directional_verdict(
    *,
    all_dir: dict[int, dict[str, Any]],
    rest_dir: dict[int, dict[str, Any]],
    rest_sa: dict[int, dict[str, Any]],
) -> str:
    rest_ok: list[int] = []
    for h in HORIZONS_SEC:
        sm = rest_dir[h].get("SELECTED", {}).get("mean_return")
        nm = rest_dir[h].get("NOT_SELECTED", {}).get("mean_return")
        mean_ok = sm is not None and nm is not None and float(sm) > float(nm)
        sa = rest_sa[h]
        sa_ok = int(sa.get("selected_better_anchor_n") or 0) > int(sa.get("selected_worse_anchor_n") or 0)
        if mean_ok and sa_ok:
            rest_ok.append(int(h))
    all_aligned = []
    for h in rest_ok:
        sm = all_dir[h].get("SELECTED", {}).get("mean_return")
        nm = all_dir[h].get("NOT_SELECTED", {}).get("mean_return")
        if sm is not None and nm is not None and float(sm) > float(nm):
            all_aligned.append(h)
    if len(rest_ok) >= 2 and len(all_aligned) >= 2:
        return DIR_SUPPORTED

    rest_bad: list[int] = []
    for h in HORIZONS_SEC:
        sm = rest_dir[h].get("SELECTED", {}).get("mean_return")
        nm = rest_dir[h].get("NOT_SELECTED", {}).get("mean_return")
        mean_bad = sm is not None and nm is not None and float(sm) < float(nm)
        sa = rest_sa[h]
        sa_bad = int(sa.get("selected_worse_anchor_n") or 0) > int(sa.get("selected_better_anchor_n") or 0)
        if mean_bad and sa_bad:
            rest_bad.append(int(h))
    all_bad = []
    for h in rest_bad:
        sm = all_dir[h].get("SELECTED", {}).get("mean_return")
        nm = all_dir[h].get("NOT_SELECTED", {}).get("mean_return")
        if sm is not None and nm is not None and float(sm) < float(nm):
            all_bad.append(h)
    if len(rest_bad) >= 2 and len(all_bad) >= 2:
        return DIR_NOT
    return DIR_MIXED


def selection_edge(exec_v: str, dir_v: str) -> str:
    if exec_v == EXEC_SUPPORTED and dir_v == DIR_SUPPORTED:
        return EDGE_BOTH
    if exec_v == EXEC_SUPPORTED and dir_v == DIR_MIXED:
        return EDGE_EXEC_DIR_MIXED
    if exec_v == EXEC_SUPPORTED and dir_v == DIR_NOT:
        return EDGE_EXEC_DOM
    if dir_v == DIR_SUPPORTED and exec_v == EXEC_MIXED:
        return EDGE_DIR_EXEC_MIXED
    if dir_v == DIR_SUPPORTED and exec_v == EXEC_NOT:
        return EDGE_DIR_DOM
    if exec_v == EXEC_NOT and dir_v == DIR_NOT:
        return EDGE_NONE
    return EDGE_MIXED


def _mono(vals: list[Optional[float]], *, higher_is_better: bool) -> str:
    xs = []
    for v in vals:
        if v is None:
            return "mixed"
        xs.append(float(v))
    if len(xs) < 2:
        return "mixed"
    if higher_is_better:
        steps = all(xs[i] >= xs[i + 1] - 1e-15 for i in range(len(xs) - 1))
        ends = xs[0] > xs[-1]
        reverse = all(xs[i] <= xs[i + 1] + 1e-15 for i in range(len(xs) - 1)) and xs[0] < xs[-1]
    else:
        steps = all(xs[i] <= xs[i + 1] + 1e-15 for i in range(len(xs) - 1))
        ends = xs[0] < xs[-1]
        reverse = all(xs[i] >= xs[i + 1] - 1e-15 for i in range(len(xs) - 1)) and xs[0] > xs[-1]
    if steps and ends:
        return "true"
    if reverse:
        return "false"
    if ends:
        return "mixed"
    return "false"


def rank_buckets(rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
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
    return buckets


def rank_strata(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str, str]:
    buckets = rank_buckets(rows)
    out = []
    fill_rates = []
    mean_rets: dict[int, list[Optional[float]]] = {h: [] for h in HORIZONS_SEC}
    for q in range(5):
        b = buckets[q]
        ex = execution_block(b)
        filled = [r for r in b if r.get("independent_filled")]
        fo = group_metrics(filled) if filled else group_metrics([])
        rec: dict[str, Any] = {
            "quintile": f"Q{q + 1}",
            "n": ex["n"],
            "fill_n": ex["fill_n"],
            "fill_rate": ex["fill_rate"],
            "filled_n": fo["n"],
            "mean_pnl_per_filled": fo.get("mean_pnl_per_filled"),
            "PF": fo.get("PF"),
        }
        fill_rates.append(ex["fill_rate"])
        for h in HORIZONS_SEC:
            d = directional_block(b, int(h))
            rec[f"mean_ret_{h}"] = d["mean_return"]
            rec[f"median_ret_{h}"] = d["median_return"]
            rec[f"n_evaluable_{h}"] = d["n_evaluable"]
            mean_rets[h].append(d["mean_return"])
        out.append(rec)
    exec_m = _mono(fill_rates, higher_is_better=True)
    dir_flags = [_mono(mean_rets[h], higher_is_better=True) for h in HORIZONS_SEC]
    if all(x == "true" for x in dir_flags):
        dir_m = "true"
    elif all(x == "false" for x in dir_flags):
        dir_m = "false"
    else:
        dir_m = "mixed"
    return out, exec_m, dir_m


def feature_dist(rows: list[dict[str, Any]], group: str) -> list[dict[str, Any]]:
    out = []
    for f in FEATURE_ORDER:
        xs = [r.get(f) for r in rows]
        out.append(
            {
                "group": group,
                "feature": f,
                "n": len(rows),
                "n_finite": sum(1 for x in xs if x is not None and float(x) == float(x)),
                "p25": _pct([x for x in xs if x is not None], 25),
                "median": _median([x for x in xs if x is not None]),
                "p75": _pct([x for x in xs if x is not None], 75),
            }
        )
    return out


def filled_outcome(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fills = [r for r in rows if r.get("independent_filled")]
    m = group_metrics(fills)
    m["label"] = "INDEPENDENT_FILLED_ARCH_E_OUTCOME"
    m["note"] = (
        "Fill-conditional Arch E independent path. Not directional-edge primary. "
        "Not a strategy return. P3-0R canonical fill 267/267; independent exit "
        "pnl match 185/267, exit_time match 94/267."
    )
    return m
