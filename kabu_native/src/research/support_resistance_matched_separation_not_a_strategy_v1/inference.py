"""Two-way date×symbol pigeonhole bootstrap and Holm adjustment across A/B/C/D. Frozen metric."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.support_resistance_first_interaction_matched_causal_test_v1.stats import _cluster_boot, _rate
from research.support_resistance_matched_separation_not_a_strategy_v1 import BOOT_N, BOOT_SEED, PRIMARY_METRIC


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def pigeonhole_boot(rows: list[dict[str, Any]], *, key: str = PRIMARY_METRIC, n: int = BOOT_N, seed: int = BOOT_SEED) -> dict[str, Any]:
    matched = [r for r in rows if r.get("matched")]
    if len(matched) < 10:
        return {"n": len(matched), "ci95_lo": None, "ci95_hi": None, "p50": None, "method": "pigeonhole_date_symbol"}
    by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    dates: list[str] = []
    symbols: list[str] = []
    seen_d: set[str] = set()
    seen_s: set[str] = set()
    for r in matched:
        d, s = str(r.get("date") or ""), str(r.get("symbol") or "")
        by[(d, s)].append(r)
        if d and d not in seen_d:
            seen_d.add(d)
            dates.append(d)
        if s and s not in seen_s:
            seen_s.add(s)
            symbols.append(s)
    if not dates or not symbols:
        return {"n": len(matched), "ci95_lo": None, "ci95_hi": None, "p50": None, "method": "pigeonhole_date_symbol"}
    rng = np.random.default_rng(int(seed))
    diffs = []
    for _ in range(int(n)):
        d_draw = rng.choice(dates, size=len(dates), replace=True)
        s_draw = rng.choice(symbols, size=len(symbols), replace=True)
        sample = []
        for d in d_draw:
            for s in s_draw:
                sample.extend(by.get((str(d), str(s)), ()))
        if len(sample) < 8:
            continue
        tr = _rate(sample, "tr_" + key)
        ct = _rate(sample, "ct_" + key)
        if tr is None or ct is None:
            continue
        diffs.append(float(tr) - float(ct))
    if not diffs:
        return {"n": len(matched), "ci95_lo": None, "ci95_hi": None, "p50": None, "method": "pigeonhole_date_symbol"}
    arr = np.asarray(diffs, dtype=float)
    lo, hi = float(np.percentile(arr, 2.5)), float(np.percentile(arr, 97.5))
    p_le = float(np.mean(arr <= 0.0))
    p_ge = float(np.mean(arr >= 0.0))
    p_two = min(1.0, 2.0 * min(p_le, p_ge))
    return {
        "n": len(matched),
        "boot_n": int(n),
        "method": "pigeonhole_date_symbol",
        "p50": float(np.median(arr)),
        "ci95_lo": lo,
        "ci95_hi": hi,
        "excludes_zero": bool(lo > 0 or hi < 0),
        "positive": bool(np.median(arr) > 0 and lo > 0),
        "p_two_sided": p_two,
    }


def holm(pvals: dict[str, float | None]) -> dict[str, Any]:
    items = [(k, float(v)) for k, v in pvals.items() if v is not None]
    m = len(items)
    adj: dict[str, float | None] = {k: None for k in pvals}
    if not items:
        return {"raw": pvals, "holm": adj, "m": 0}
    items.sort(key=lambda kv: kv[1])
    running = 0.0
    for i, (k, p) in enumerate(items):
        val = min(1.0, p * (m - i))
        running = max(running, val)
        adj[k] = running
    return {"raw": pvals, "holm": adj, "m": m, "alpha": 0.05}


def joint_inference(qs: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    raw = {}
    detail = {}
    for qid, rows in qs.items():
        ph = pigeonhole_boot(rows)
        dt = _cluster_boot(rows, key=PRIMARY_METRIC, cluster="date", n=BOOT_N, seed=BOOT_SEED)
        sm = _cluster_boot(rows, key=PRIMARY_METRIC, cluster="symbol", n=BOOT_N, seed=BOOT_SEED + 1)
        detail[qid] = {"pigeonhole": ph, "date": dt, "symbol": sm, "selected_whichever_favorable": False}
        raw[qid] = ph.get("p_two_sided")
    adj = holm(raw)
    return {
        "by_question": detail,
        "multiple_testing": adj,
        "frozen_metric": PRIMARY_METRIC,
        "did_not_pick_favorable_ci": True,
    }
