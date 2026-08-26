"""P3-2 post-fill execution-advantage vs MID-direction metrics."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.fixed_selection_edge_decomposition_p3_1.metrics import _mean, _median, _mono, _pct
from research.post_fill_edge_decomposition_p3_2 import (
    DIR_MIXED,
    DIR_NOT,
    DIR_SUPPORTED,
    HORIZONS_SEC,
    MECH_ADVERSE,
    MECH_MIXED,
    MECH_NONE,
    MECH_PLUS_DIR,
    MECH_PURE_EXEC,
    PRIMARY_DIR_HORIZONS,
)


def dist_block(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    xs = [float(r[field]) for r in rows if r.get(field) is not None and float(r[field]) == float(r[field])]
    return {
        "n": len(rows),
        "n_evaluable": len(xs),
        "mean": _mean(xs),
        "median": _median(xs),
        "p25": _pct(xs, 25),
        "p75": _pct(xs, 75),
    }


def markout_block(rows: list[dict[str, Any]], horizon: int, field: str) -> dict[str, Any]:
    st = f"status_{horizon}"
    evals = [
        r
        for r in rows
        if r.get(st) == "OK" and r.get(field) is not None and float(r[field]) == float(r[field])
    ]
    xs = [float(r[field]) for r in evals]
    pos = sum(1 for x in xs if x > 0)
    return {
        "horizon_sec": horizon,
        "n": len(rows),
        "n_evaluable": len(evals),
        "n_session_incomplete": sum(1 for r in rows if r.get(st) == "SESSION_INCOMPLETE"),
        "n_not_evaluable": sum(1 for r in rows if r.get(st) == "NOT_EVALUABLE"),
        "mean": _mean(xs),
        "median": _median(xs),
        "positive_rate": (pos / len(xs)) if xs else None,
    }


def same_anchor_rows(rows: list[dict[str, Any]], horizon: int) -> list[dict[str, Any]]:
    field = f"mid_markout_{horizon}"
    st = f"status_{horizon}"
    by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[(str(r["date"]), str(r["anchor_time"]))].append(r)
    out = []
    for (day, an), group in sorted(by.items()):
        sel = [r for r in group if r.get("selected") and r.get(st) == "OK" and r.get(field) is not None]
        nos = [r for r in group if (not r.get("selected")) and r.get(st) == "OK" and r.get(field) is not None]
        if not sel or not nos:
            continue
        sm = _mean([float(r[field]) for r in sel])
        nm = _mean([float(r[field]) for r in nos])
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
                "selected_mean": sm,
                "not_selected_mean": nm,
                "difference": d,
                "side": side,
            }
        )
    return out


def same_anchor_summary(detail: list[dict[str, Any]]) -> dict[str, Any]:
    diffs = [float(r["difference"]) for r in detail]
    return {
        "selected_better": sum(1 for r in detail if r.get("side") == "selected_better"),
        "selected_worse": sum(1 for r in detail if r.get("side") == "selected_worse"),
        "equal": sum(1 for r in detail if r.get("side") == "equal"),
        "n_anchors": len(detail),
        "median_difference": _median(diffs),
        "mean_difference": _mean(diffs),
    }


def post_fill_direction_verdict(
    *,
    all_mid: dict[int, dict[str, Any]],
    rest_mid: dict[int, dict[str, Any]],
    rest_sa: dict[int, dict[str, Any]],
) -> str:
    rest_ok: list[int] = []
    for h in PRIMARY_DIR_HORIZONS:
        sm = rest_mid[h].get("SELECTED", {}).get("mean")
        nm = rest_mid[h].get("NOT_SELECTED", {}).get("mean")
        mean_ok = sm is not None and nm is not None and float(sm) > float(nm)
        sa = rest_sa[h]
        sa_ok = int(sa.get("selected_better") or 0) > int(sa.get("selected_worse") or 0)
        if mean_ok and sa_ok:
            rest_ok.append(int(h))
    all_aligned = []
    for h in rest_ok:
        sm = all_mid[h].get("SELECTED", {}).get("mean")
        nm = all_mid[h].get("NOT_SELECTED", {}).get("mean")
        if sm is not None and nm is not None and float(sm) > float(nm):
            all_aligned.append(h)
    if len(rest_ok) >= 2 and len(all_aligned) >= 2:
        return DIR_SUPPORTED

    rest_bad: list[int] = []
    for h in PRIMARY_DIR_HORIZONS:
        sm = rest_mid[h].get("SELECTED", {}).get("mean")
        nm = rest_mid[h].get("NOT_SELECTED", {}).get("mean")
        mean_bad = sm is not None and nm is not None and float(sm) < float(nm)
        sa = rest_sa[h]
        sa_bad = int(sa.get("selected_worse") or 0) > int(sa.get("selected_better") or 0)
        if mean_bad and sa_bad:
            rest_bad.append(int(h))
    all_bad = []
    for h in rest_bad:
        sm = all_mid[h].get("SELECTED", {}).get("mean")
        nm = all_mid[h].get("NOT_SELECTED", {}).get("mean")
        if sm is not None and nm is not None and float(sm) < float(nm):
            all_bad.append(h)
    if len(rest_bad) >= 2 and len(all_bad) >= 2:
        return DIR_NOT
    return DIR_MIXED


def exec_adv_selected_better(all_s: dict[str, Any], all_n: dict[str, Any], rest_s: dict[str, Any], rest_n: dict[str, Any]) -> bool:
    def _gt(a, b) -> bool:
        if a.get("median") is None or b.get("median") is None:
            return False
        return float(a["median"]) > float(b["median"])

    return _gt(all_s, all_n) and _gt(rest_s, rest_n)


def mechanism(
    *,
    exec_adv_better: bool,
    dir_v: str,
    rest_mid: dict[int, dict[str, Any]],
) -> str:
    if dir_v == DIR_SUPPORTED:
        return MECH_PLUS_DIR
    if dir_v == DIR_MIXED:
        return MECH_MIXED
    n_worse = 0
    for h in PRIMARY_DIR_HORIZONS:
        sm = rest_mid[h].get("SELECTED", {}).get("mean")
        nm = rest_mid[h].get("NOT_SELECTED", {}).get("mean")
        if sm is not None and nm is not None and float(sm) < float(nm):
            n_worse += 1
    if n_worse >= 2:
        return MECH_ADVERSE
    if exec_adv_better:
        return MECH_PURE_EXEC
    return MECH_NONE


def rank_strata(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str, str]:
    buckets: dict[str, list[dict[str, Any]]] = {f"Q{i}": [] for i in range(1, 6)}
    for r in rows:
        q = r.get("quintile")
        if q in buckets:
            buckets[q].append(r)
    out = []
    adv = []
    dir_means: dict[int, list[Optional[float]]] = {h: [] for h in PRIMARY_DIR_HORIZONS}
    for i in range(1, 6):
        b = buckets[f"Q{i}"]
        ea = dist_block(b, "execution_advantage_bps")
        rec: dict[str, Any] = {
            "quintile": f"Q{i}",
            "fill_n": len(b),
            "execution_advantage_mean": ea.get("mean"),
            "execution_advantage_median": ea.get("median"),
            "execution_advantage_n_evaluable": ea.get("n_evaluable"),
        }
        adv.append(ea.get("median"))
        for h in HORIZONS_SEC:
            m = markout_block(b, int(h), f"mid_markout_{h}")
            rec[f"mid_markout_{h}_mean"] = m.get("mean")
            rec[f"mid_markout_{h}_median"] = m.get("median")
            rec[f"mid_markout_{h}_n_evaluable"] = m.get("n_evaluable")
            if h in PRIMARY_DIR_HORIZONS:
                dir_means[h].append(m.get("mean"))
        out.append(rec)
    exec_m = _mono(adv, higher_is_better=True)
    flags = [_mono(dir_means[h], higher_is_better=True) for h in PRIMARY_DIR_HORIZONS]
    if all(x == "true" for x in flags):
        dir_m = "true"
    elif all(x == "false" for x in flags):
        dir_m = "false"
    else:
        dir_m = "mixed"
    return out, exec_m, dir_m
