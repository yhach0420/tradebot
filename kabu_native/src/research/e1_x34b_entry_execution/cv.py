"""Outer nested CV + inner LODO for routing rules."""
from __future__ import annotations

from typing import Any

import numpy as np

from . import MIN_INNER_DAYS_EVAL, OUTER_BLOCKS
from .features import apply_rule, build_catalog, fit_all_thresholds
from .metrics import routed_net, support_ok, summarize_decisions


def outer_train_test(block: str) -> tuple[set[str], set[str]]:
    test = set(OUTER_BLOCKS[block])
    train = set()
    for k, days in OUTER_BLOCKS.items():
        if k != block:
            train.update(days)
    return train, test


def _filter_days(rows: list[dict], days: set[str]) -> list[dict]:
    return [r for r in rows if r["date"] in days]


def decide_all(rows: list[dict], rule: dict, thr: dict) -> list[str]:
    return [apply_rule(r, rule, thr) for r in rows]


def score_inner_lodo(
    train_rows: list[dict],
    rule: dict,
) -> dict[str, Any]:
    """Leave-one-day-out on Outer Train; thresholds fit excluding holdout day."""
    days = sorted({r["date"] for r in train_rows})
    day_scores = []
    pooled_rows = []
    pooled_dec = []
    for hold in days:
        tr = [r for r in train_rows if r["date"] != hold]
        va = [r for r in train_rows if r["date"] == hold]
        if len(tr) < 80 or len(va) < 5:
            continue
        thr = fit_all_thresholds(tr)
        dec = decide_all(va, rule, thr)
        # day score = mean opp_w 600
        nets = [routed_net(r, d, 600) for r, d in zip(va, dec)]
        day_scores.append({
            "holdout_day": hold,
            "opp600": float(np.mean(nets)),
            "n": len(va),
            "selected": sum(1 for d in dec if d != "SKIP"),
        })
        pooled_rows.extend(va)
        pooled_dec.extend(dec)

    if len(day_scores) < MIN_INNER_DAYS_EVAL:
        return {
            "pass": False,
            "reason": "insufficient_inner_days",
            "n_eval_days": len(day_scores),
        }

    # support on full train (thresholds on full train)
    thr_full = fit_all_thresholds(train_rows)
    dec_full = decide_all(train_rows, rule, thr_full)
    supp = support_ok(train_rows, dec_full)
    if not supp["ok"]:
        return {
            "pass": False,
            "reason": supp["status"],
            "support": supp,
            "n_eval_days": len(day_scores),
        }

    pos_days = sum(1 for s in day_scores if s["opp600"] > 0)
    mean_opp = float(np.mean([s["opp600"] for s in day_scores]))
    # require majority positive inner days + mean > 0
    majority = pos_days > len(day_scores) / 2.0
    passed = majority and mean_opp > 0
    return {
        "pass": passed,
        "reason": "ok" if passed else "inner_not_robust",
        "n_eval_days": len(day_scores),
        "pos_days": pos_days,
        "mean_opp600": mean_opp,
        "day_scores": day_scores,
        "support": supp,
        "train_summary": summarize_decisions(train_rows, dec_full, label="inner_train"),
    }


def run_nested_cv(rows: list[dict]) -> dict[str, Any]:
    catalog = build_catalog()
    fold_results = {}
    cross_rows: list[dict] = []
    cross_dec: list[str] = []
    selected_per_fold = {}

    for block in ("A", "B", "C", "D"):
        train_days, test_days = outer_train_test(block)
        train_rows = _filter_days(rows, train_days)
        test_rows = _filter_days(rows, test_days)
        print(f"  Outer {block}: train={len(train_rows)} test={len(test_rows)} catalog={len(catalog)}", flush=True)

        survivors = []
        for rule in catalog:
            sc = score_inner_lodo(train_rows, rule)
            if sc.get("pass"):
                survivors.append({"rule": rule, "inner": sc})

        # pick best survivor by inner mean_opp600 (train-only)
        chosen = None
        if survivors:
            survivors.sort(key=lambda x: -float(x["inner"]["mean_opp600"]))
            chosen = survivors[0]

        if chosen is None:
            # no rule: SKIP all on test (blind)
            thr = {}
            rule = {"id": "NONE", "kind": "none"}
            dec_test = ["SKIP"] * len(test_rows)
            test_sum = summarize_decisions(test_rows, dec_test, label=f"outer_{block}_none")
            selected_per_fold[block] = None
        else:
            rule = chosen["rule"]
            thr = fit_all_thresholds(train_rows)  # Outer Test blind
            dec_test = decide_all(test_rows, rule, thr)
            test_sum = summarize_decisions(test_rows, dec_test, label=f"outer_{block}")
            selected_per_fold[block] = {
                "rule_id": rule["id"],
                "family": rule.get("family"),
                "inner_mean_opp600": chosen["inner"]["mean_opp600"],
                "support": chosen["inner"].get("support"),
            }

        fold_results[block] = {
            "train_days": sorted(train_days),
            "test_days": sorted(test_days),
            "n_survivors": len(survivors),
            "selected": selected_per_fold[block],
            "test": test_sum,
            "survivor_ids": [s["rule"]["id"] for s in survivors[:10]],
        }
        cross_rows.extend(test_rows)
        cross_dec.extend(dec_test)
        print(
            f"    survivors={len(survivors)} chosen={selected_per_fold[block]} "
            f"test_opp600={test_sum.get('opp_w_ret600')}",
            flush=True,
        )

    cross = summarize_decisions(cross_rows, cross_dec, label="cross_fitted")
    return {
        "folds": fold_results,
        "selected_per_fold": selected_per_fold,
        "cross_rows": cross_rows,
        "cross_decisions": cross_dec,
        "cross_fitted": cross,
        "catalog_size": len(catalog),
    }
