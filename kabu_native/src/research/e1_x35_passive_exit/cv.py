"""Nested CV for EXIT selection — Outer Test blind."""
from __future__ import annotations

from typing import Any

import numpy as np

from . import MIN_INNER_DAYS, OUTER_BLOCKS
from .exits import build_catalog
from .metrics import evaluate_spec


def outer_train_test(block: str) -> tuple[set[str], set[str]]:
    test = set(OUTER_BLOCKS[block])
    train = set()
    for k, days in OUTER_BLOCKS.items():
        if k != block:
            train |= set(days)
    return train, test


def score_inner_lodo(train_eps: list[dict], spec: dict) -> dict[str, Any]:
    days = sorted({e["date"] for e in train_eps})
    day_scores = []
    for hold in days:
        tr = [e for e in train_eps if e["date"] != hold]
        va = [e for e in train_eps if e["date"] == hold]
        if len(tr) < 15 or len(va) < 2:
            continue
        # thresholds already in spec from full-train catalog; evaluate on holdout day
        sm = evaluate_spec(va, spec)
        if not sm.get("ok") or (sm.get("n") or 0) < 2:
            continue
        day_scores.append({"day": hold, "mean_ret": sm["mean_ret_bps"], "n": sm["n"]})
    if len(day_scores) < MIN_INNER_DAYS:
        return {"pass": False, "reason": "insufficient_inner_days", "n_eval": len(day_scores)}
    pos = sum(1 for s in day_scores if (s["mean_ret"] or 0) > 0)
    mean_ret = float(np.mean([s["mean_ret"] for s in day_scores]))
    # also require full-train positive mean
    full = evaluate_spec(train_eps, spec)
    ok = (
        pos > len(day_scores) / 2.0
        and mean_ret > 0
        and (full.get("mean_ret_bps") or 0) > 0
        and (full.get("pf") or 0) > 1.0
    )
    return {
        "pass": ok,
        "reason": "ok" if ok else "inner_fail",
        "n_eval": len(day_scores),
        "pos_days": pos,
        "mean_holdout_ret": mean_ret,
        "train_mean": full.get("mean_ret_bps"),
        "train_pf": full.get("pf"),
        "train_hold_median": (full.get("hold_sec") or {}).get("median"),
    }


def run_nested_cv(eps: list[dict]) -> dict[str, Any]:
    folds = {}
    cross_rows: list[tuple] = []  # (ep, exit_result, spec_id)
    selected = {}

    for block in ("A", "B", "C", "D"):
        train_days, test_days = outer_train_test(block)
        train = [e for e in eps if e["date"] in train_days]
        test = [e for e in eps if e["date"] in test_days]
        catalog = build_catalog(train)
        survivors = []
        for spec in catalog:
            sc = score_inner_lodo(train, spec)
            if sc.get("pass"):
                survivors.append({"spec": spec, "inner": sc})
        survivors.sort(key=lambda x: (-float(x["inner"]["mean_holdout_ret"]), x["spec"]["id"]))
        chosen = survivors[0] if survivors else None

        if chosen is None:
            # fallback: FIXED600 as control on test (still report)
            spec = {"id": "E0_FIXED_600", "family": "E0_FIXED", "fixed_hold_sec": 600.0}
            selected[block] = None
        else:
            spec = chosen["spec"]
            selected[block] = {
                "spec_id": spec["id"],
                "family": spec["family"],
                "inner_mean_ret": chosen["inner"]["mean_holdout_ret"],
                "train_hold_median": chosen["inner"].get("train_hold_median"),
            }

        test_sm = evaluate_spec(test, spec)
        folds[block] = {
            "train_n": len(train),
            "test_n": len(test),
            "n_survivors": len(survivors),
            "selected": selected[block],
            "fallback_fixed600": chosen is None,
            "test": {k: v for k, v in test_sm.items() if k != "day_means"},
            "top_survivors": [s["spec"]["id"] for s in survivors[:8]],
        }
        # accumulate cross-fitted exits
        from .exits import run_spec
        for e in test:
            r = run_spec(e, spec)
            if r.get("ok"):
                cross_rows.append((e, r, spec["id"]))
        print(
            f"  Outer {block}: survivors={len(survivors)} chosen={selected[block]} "
            f"test_ret={test_sm.get('mean_ret_bps')} hold_med={(test_sm.get('hold_sec') or {}).get('median')}",
            flush=True,
        )

    # summarize cross-fitted
    rets = [float(r["exit_ret_bps"]) for _, r, _ in cross_rows]
    holds = [float(r["hold_sec"]) for _, r, _ in cross_rows]
    by_day: dict[str, list[float]] = {}
    by_ss: dict[tuple, list[float]] = {}
    for e, r, _ in cross_rows:
        by_day.setdefault(e["date"], []).append(float(r["exit_ret_bps"]))
        by_ss.setdefault((e["date"], e["symbol"], e["session"]), []).append(float(r["exit_ret_bps"]))
    day_means = {d: float(np.mean(v)) for d, v in by_day.items()}
    pos_c = sum(x for x in rets if x > 0)
    neg_c = sum(x for x in rets if x < 0)
    from .metrics import dist_stats
    hold_stats = dist_stats(holds)
    cross = {
        "n": len(rets),
        "mean_ret_bps": float(np.mean(rets)) if rets else None,
        "pf": float(pos_c / abs(neg_c)) if abs(neg_c) > 1e-12 else None,
        "positive_days": sum(1 for v in day_means.values() if v > 0),
        "negative_days": sum(1 for v in day_means.values() if v < 0),
        "n_days": len(day_means),
        "day_means": day_means,
        "ss_balanced": float(np.mean([np.mean(v) for v in by_ss.values()])) if by_ss else None,
        "day_balanced": float(np.mean(list(day_means.values()))) if day_means else None,
        "hold_sec": hold_stats,
        "hold_vs_proxy600": (
            None if hold_stats.get("median") is None
            else float(600.0 - hold_stats["median"])
        ),
    }
    return {
        "folds": folds,
        "selected_per_fold": selected,
        "cross_fitted": cross,
        "cross_rows": cross_rows,
    }
