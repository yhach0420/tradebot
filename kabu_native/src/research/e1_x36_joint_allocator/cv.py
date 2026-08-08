"""Nested CV: Outer A–D blind + inner LODO allocator selection."""
from __future__ import annotations

from typing import Any

import numpy as np

from . import HASH_SEEDS, HASH_SALT, MIN_INNER_DAYS, ORDER_ASC, ORDER_DESC, ORDER_HASH, OUTER_BLOCKS
from .metrics import summarize_replay
from .models import candidate_specs, fit_spec, score_fn_from_fit
from .replay import simulate_joint


def outer_train_test(block: str) -> tuple[set[str], set[str]]:
    test = set(OUTER_BLOCKS[block])
    train = set()
    for k, days in OUTER_BLOCKS.items():
        if k != block:
            train |= set(days)
    return train, test


def _score_holdout(sm: dict) -> float:
    """Primary inner score: total PnL yen; tie-break opp bps."""
    pnl = sm.get("total_pnl_yen")
    if pnl is None:
        return float("-inf")
    opp = sm.get("opp_bps_per_signal") or 0.0
    return float(pnl) + 0.01 * float(opp)


def inner_lodo_select(train: list[dict]) -> dict[str, Any]:
    days = sorted({e["date"] for e in train})
    specs = candidate_specs()
    survivors = []

    for spec in specs:
        day_scores = []
        for hold in days:
            tr = [e for e in train if e["date"] != hold]
            va = [e for e in train if e["date"] == hold]
            if len(tr) < 80 or len(va) < 10:
                continue
            fit = fit_spec(tr, spec)
            if fit.get("kind") == "fail":
                continue
            sfn = score_fn_from_fit(fit)
            if sfn is None and spec["family"] != "A0_ASC":
                continue
            if spec["family"] == "A0_ASC":
                sim = simulate_joint(va, order_mode=ORDER_ASC)
            else:
                sim = simulate_joint(va, score_fn=sfn)
            sm = summarize_replay(sim)
            day_scores.append({
                "day": hold,
                "pnl": sm.get("total_pnl_yen"),
                "opp": sm.get("opp_bps_per_signal"),
                "score": _score_holdout(sm),
            })
        if len(day_scores) < MIN_INNER_DAYS:
            continue
        pos = sum(1 for s in day_scores if (s["pnl"] or 0) > 0)
        mean_score = float(np.mean([s["score"] for s in day_scores]))
        mean_pnl = float(np.mean([s["pnl"] for s in day_scores]))
        # require majority positive PnL days on inner
        if pos <= len(day_scores) / 2.0:
            continue
        if mean_pnl <= 0:
            continue
        survivors.append({
            "spec": spec,
            "mean_score": mean_score,
            "mean_pnl": mean_pnl,
            "pos_days": pos,
            "n_eval": len(day_scores),
        })

    survivors.sort(key=lambda x: (-x["mean_score"], x["spec"]["family"], x["spec"]["feature_set"], x["spec"]["reg"]))
    chosen = survivors[0] if survivors else {
        "spec": {"family": "A0_ASC", "feature_set": "EXEC", "reg": 1.0},
        "mean_score": None,
        "mean_pnl": None,
        "pos_days": None,
        "n_eval": 0,
        "fallback": True,
    }
    return {"chosen": chosen, "n_survivors": len(survivors), "top": survivors[:8]}


def run_nested_cv(panel: list[dict]) -> dict[str, Any]:
    folds = {}
    cross_events: list[dict] = []
    selected = {}

    for block in ("A", "B", "C", "D"):
        train_days, test_days = outer_train_test(block)
        train = [e for e in panel if e["date"] in train_days]
        test = [e for e in panel if e["date"] in test_days]
        print(f"  Outer {block}: train_n={len(train)} test_n={len(test)} inner select...", flush=True)
        inner = inner_lodo_select(train)
        spec = inner["chosen"]["spec"]
        fit = fit_spec(train, spec)
        sfn = score_fn_from_fit(fit)
        if spec["family"] == "A0_ASC" or sfn is None:
            sim = simulate_joint(test, order_mode=ORDER_ASC)
            used = "A0_ASC"
        else:
            sim = simulate_joint(test, score_fn=sfn)
            used = spec["family"]
        sm = summarize_replay(sim)
        selected[block] = {
            "family": used,
            "feature_set": spec["feature_set"],
            "reg": spec["reg"],
            "inner_mean_pnl": inner["chosen"].get("mean_pnl"),
            "n_survivors": inner["n_survivors"],
            "fallback": bool(inner["chosen"].get("fallback")),
        }
        folds[block] = {
            "train_n": len(train),
            "test_n": len(test),
            "selected": selected[block],
            "test": {k: v for k, v in sm.items() if k not in ("day_means_opp", "day_pnls")},
            "test_day_pnls": sm.get("day_pnls"),
            "test_day_means": sm.get("day_means_opp"),
        }
        # tag events for cross-fit (copy with realized fields)
        for e in sim["events"]:
            cross_events.append(e)
        print(
            f"    chosen={selected[block]} test_pnl={sm.get('total_pnl_yen')} "
            f"opp={sm.get('opp_bps_per_signal')} fills={sm.get('fills')} pos_days={sm.get('positive_days')}",
            flush=True,
        )

    # synthesize cross sim summary from cross_events
    fake_sim = {
        "events": cross_events,
        "hard_cap_violations": sum(folds[b]["test"].get("hard_cap_violations") or 0 for b in folds),
        "max_open_plus_pending": max(folds[b]["test"].get("max_open_plus_pending") or 0 for b in folds),
        "occupied_slot_sec": sum(folds[b]["test"].get("occupied_slot_sec") or 0 for b in folds),
        "max_concurrent_notional_yen": max(
            (folds[b]["test"].get("capital") or {}).get("max_concurrent_notional_yen") or 0 for b in folds
        ),
        "p95_concurrent_notional_yen": None,
        "max_pending_reserved_notional_yen": max(
            (folds[b]["test"].get("capital") or {}).get("max_pending_reserved_notional_yen") or 0 for b in folds
        ),
    }
    # recompute capital p95 from events if needed
    cross = summarize_replay(fake_sim)
    return {
        "folds": folds,
        "selected_per_fold": selected,
        "cross_fitted": cross,
        "cross_events": cross_events,
    }


def run_baselines(panel: list[dict]) -> dict[str, Any]:
    out = {}
    # B0 skip all — zero PnL
    out["B0_SKIP"] = {
        "total_pnl_yen": 0.0,
        "opp_bps_per_signal": 0.0,
        "fills": 0,
        "admitted": 0,
        "positive_days": 0,
        "pf": None,
        "ss_balanced": 0.0,
    }
    for name, mode, salt in (
        ("B1_ASC", ORDER_ASC, HASH_SALT),
        ("B2_DESC", ORDER_DESC, HASH_SALT),
        ("B3_HASH", ORDER_HASH, HASH_SALT),
    ):
        sim = simulate_joint(panel, order_mode=mode, hash_salt=salt)
        out[name] = summarize_replay(sim)
        out[name]["_sim_meta"] = {
            "hard_cap_violations": sim["hard_cap_violations"],
            "accepted_fills": sim["accepted_fills"],
        }
        print(f"  baseline {name}: pnl={out[name]['total_pnl_yen']:.0f} opp={out[name]['opp_bps_per_signal']:.4f} "
              f"fills={out[name]['fills']} pos={out[name]['positive_days']}", flush=True)

    hash_pnls = []
    hash_opps = []
    for seed in HASH_SEEDS:
        salt = f"{HASH_SALT}|seed{seed}"
        sim = simulate_joint(panel, order_mode=ORDER_HASH, hash_salt=salt)
        sm = summarize_replay(sim)
        hash_pnls.append(sm["total_pnl_yen"])
        hash_opps.append(sm["opp_bps_per_signal"])
    out["HASH_DIAG"] = {
        "seeds": list(HASH_SEEDS),
        "pnl_yen": hash_pnls,
        "opp_bps": hash_opps,
        "median_pnl_yen": float(np.median(hash_pnls)),
        "median_opp_bps": float(np.median(hash_opps)),
        "dispersion_pnl": float(np.std(hash_pnls)) if hash_pnls else None,
    }
    print(f"  HASH diag median_pnl={out['HASH_DIAG']['median_pnl_yen']:.0f} "
          f"median_opp={out['HASH_DIAG']['median_opp_bps']:.4f}", flush=True)
    return out
