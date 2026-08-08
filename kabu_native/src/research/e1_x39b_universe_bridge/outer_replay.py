"""Outer cross-fit replay with frozen X36 specs (no inner re-selection)."""
from __future__ import annotations

from typing import Any

from research.e1_x36_joint_allocator import OUTER_BLOCKS, ORDER_ASC
from research.e1_x36_joint_allocator.cv import outer_train_test, run_baselines
from research.e1_x36_joint_allocator.metrics import (
    lodo_from_day_means,
    loso_sensitivity,
    summarize_replay,
)
from research.e1_x36_joint_allocator.models import fit_spec, score_fn_from_fit
from research.e1_x36_joint_allocator.replay import simulate_joint
from research.e1_x36_joint_allocator.verdict import decide_verdict

from . import (
    X36_ADMITTED,
    X36_FILLS,
    X36_HARD_CAP,
    X36_PF,
    X36_PNL,
    X36_POS_DAYS,
    X36_SELECTED,
)


def _aggregate(cross_events: list[dict], folds: dict) -> dict[str, Any]:
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
    return summarize_replay(fake_sim)


def crossfit_fixed_specs(
    train_panel: list[dict],
    test_panel: list[dict],
    *,
    label: str,
) -> dict[str, Any]:
    """
    Fit outer models on train_panel (legacy population),
    evaluate on test_panel days (legacy or AM).
    Training population must remain the legacy X36 population.
    """
    folds = {}
    cross_events: list[dict] = []
    selected = {}
    fits_meta = {}

    for block in ("A", "B", "C", "D"):
        train_days, test_days = outer_train_test(block)
        train = [e for e in train_panel if e["date"] in train_days]
        test = [e for e in test_panel if e["date"] in test_days]
        spec = dict(X36_SELECTED[block])
        print(
            f"  [{label}] Outer {block}: train_n={len(train)} test_n={len(test)} "
            f"spec={spec}",
            flush=True,
        )
        fit = fit_spec(train, spec)
        assert fit.get("kind") != "fail", (block, fit)
        sfn = score_fn_from_fit(fit)
        sim = simulate_joint(test, score_fn=sfn)
        sm = summarize_replay(sim)
        selected[block] = {
            **spec,
            "fallback": False,
            "n_survivors": None,
            "inner_mean_pnl": None,
            "source": "X36_FROZEN_SELECTED",
        }
        fits_meta[block] = {
            "kind": fit.get("kind"),
            "feature_set": spec["feature_set"],
            "reg": spec["reg"],
            "n_features": len(fit.get("features") or ()),
            "coef_norm": float(
                sum(abs(float(x)) for x in (fit["model"].coef_.reshape(-1) if fit.get("kind") == "fill" else []))
            ) if fit.get("kind") == "fill" else None,
        }
        folds[block] = {
            "train_n": len(train),
            "test_n": len(test),
            "test_days": sorted(test_days),
            "selected": selected[block],
            "test": {k: v for k, v in sm.items() if k not in ("day_means_opp", "day_pnls")},
            "test_day_pnls": sm.get("day_pnls"),
            "test_day_means": sm.get("day_means_opp"),
        }
        for e in sim["events"]:
            e = dict(e)
            e["_outer_block"] = block
            cross_events.append(e)
        print(
            f"    test_pnl={sm.get('total_pnl_yen')} fills={sm.get('fills')} "
            f"adm={sm.get('admitted')} pos={sm.get('positive_days')}",
            flush=True,
        )

    cross = _aggregate(cross_events, folds)
    return {
        "label": label,
        "folds": folds,
        "selected_per_fold": selected,
        "fits_meta": fits_meta,
        "cross_fitted": cross,
        "cross_events": cross_events,
    }


def check_x36_identity(cross: dict[str, Any]) -> dict[str, Any]:
    pnl = float(cross.get("total_pnl_yen") or 0.0)
    pf = cross.get("pf")
    checks = {
        "admitted": int(cross.get("admitted") or 0) == X36_ADMITTED,
        "fills": int(cross.get("fills") or 0) == X36_FILLS,
        "pnl": abs(pnl - X36_PNL) < 1.0,  # yen float noise
        "pf": pf is not None and abs(float(pf) - X36_PF) < 1e-9,
        "positive_days": int(cross.get("positive_days") or 0) == X36_POS_DAYS,
        "hard_cap": int(cross.get("hard_cap_violations") or 0) == X36_HARD_CAP,
    }
    return {
        "observed": {
            "admitted": cross.get("admitted"),
            "fills": cross.get("fills"),
            "total_pnl_yen": pnl,
            "pf": pf,
            "positive_days": cross.get("positive_days"),
            "hard_cap_violations": cross.get("hard_cap_violations"),
        },
        "expected": {
            "admitted": X36_ADMITTED,
            "fills": X36_FILLS,
            "total_pnl_yen": X36_PNL,
            "pf": X36_PF,
            "positive_days": X36_POS_DAYS,
            "hard_cap_violations": X36_HARD_CAP,
        },
        "checks": checks,
        "pass": all(checks.values()),
    }


def apply_x36_gate(
    *,
    cross: dict[str, Any],
    baselines: dict[str, Any],
    selected_per_fold: dict[str, Any],
    cross_events: list[dict],
) -> dict[str, Any]:
    """Reuse X36 decide_verdict / acceptance logic — no new thresholds."""
    lodo = lodo_from_day_means(cross.get("day_means_opp") or {})
    loso = loso_sensitivity(cross_events)
    decision = decide_verdict(
        cross=cross,
        baselines=baselines,
        selected_per_fold=selected_per_fold,
        lodo=lodo,
        loso=loso,
    )
    return {
        "decision": decision,
        "lodo": lodo,
        "loso": {k: loso[k] for k in ("n_folds", "positive_folds", "majority_positive") if k in loso},
        "gate_source": "research.e1_x36_joint_allocator.verdict.decide_verdict",
        "x36_pass_verdict": "E1_X36_FULL_STRATEGY_HISTORICALLY_SUPPORTED",
        "bridge_matches_x36_pass": decision.get("verdict") == "E1_X36_FULL_STRATEGY_HISTORICALLY_SUPPORTED",
    }


def day_compare(
    legacy_events: list[dict],
    bridge_events: list[dict],
    delta_daily: list[dict],
) -> list[dict[str, Any]]:
    def _by_day(evs: list[dict]) -> dict[str, dict]:
        from collections import defaultdict
        pnl = defaultdict(float)
        fills = defaultdict(int)
        adm = defaultdict(int)
        for e in evs:
            d = e["date"]
            pnl[d] += float(e.get("realized_pnl_yen") or 0.0)
            if e.get("accepted"):
                fills[d] += 1
            if e.get("admitted"):
                adm[d] += 1
        return {d: {"pnl": pnl[d], "fills": fills[d], "admitted": adm[d]} for d in set(pnl) | set(fills) | set(adm)}

    leg = _by_day(legacy_events)
    br = _by_day(bridge_events)
    delta_map = {r["date"]: r for r in delta_daily}
    days = sorted(set(leg) | set(br) | set(delta_map))
    out = []
    for d in days:
        o = leg.get(d, {"pnl": 0.0, "fills": 0, "admitted": 0})
        b = br.get(d, {"pnl": 0.0, "fills": 0, "admitted": 0})
        dd = delta_map.get(d, {})
        out.append({
            "date": d,
            "old_candidate_count": dd.get("old_candidate_count"),
            "am_universe_count": dd.get("am_universe_count"),
            "added_symbols": dd.get("added"),
            "old_pnl": o["pnl"],
            "bridge_pnl": b["pnl"],
            "pnl_delta": b["pnl"] - o["pnl"],
            "old_fills": o["fills"],
            "bridge_fills": b["fills"],
            "fill_delta": b["fills"] - o["fills"],
            "old_admitted": o["admitted"],
            "bridge_admitted": b["admitted"],
            "sign": (
                "positive" if b["pnl"] > 0 else ("negative" if b["pnl"] < 0 else "zero")
            ),
        })
    return out
