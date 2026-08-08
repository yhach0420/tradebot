"""Summaries, 285A, sensitivity, verdict for capital diagnostic."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.e1_x36_joint_allocator.cv import outer_train_test
from research.e1_x36_joint_allocator.metrics import summarize_replay
from research.e1_x36_joint_allocator.models import fit_spec, score_fn_from_fit
from research.e1_x36_joint_allocator.replay import simulate_joint
from research.e1_x36r_freeze_integrity import OUTER_SPECS

from . import (
    INITIAL_CASH_PRIMARY,
    SYMBOL_285A,
    VERDICT_IDENTITY_FAIL,
    VERDICT_NEGATIVE,
    VERDICT_POSITIVE,
    VERDICT_WEAK,
    X36_CROSS,
)
from .capital_replay import simulate_joint_capital


def build_fold_scorers(panel: list[dict]) -> dict[str, Any]:
    """Fit frozen OUTER_SPECS once; map each date → score_fn (no re-selection)."""
    score_by_date: dict[str, Any] = {}
    fold_meta = {}
    for block in ("A", "B", "C", "D"):
        train_days, test_days = outer_train_test(block)
        train = [e for e in panel if e["date"] in train_days]
        spec = OUTER_SPECS[block]
        fit = fit_spec(train, spec)
        sfn = score_fn_from_fit(fit)
        fold_meta[block] = {"spec": spec, "train_n": len(train), "test_days": list(test_days)}
        for d in test_days:
            score_by_date[d] = sfn
    return {"score_by_date": score_by_date, "fold_meta": fold_meta}


def unlimited_identity(panel: list[dict], score_by_date: dict) -> dict[str, Any]:
    """
    X36-style: per outer fold independent replay (no cash), concatenate.
    Must match X36 cross-fitted SoT.
    """
    cross_events = []
    for block in ("A", "B", "C", "D"):
        _, test_days = outer_train_test(block)
        test = [e for e in panel if e["date"] in test_days]
        # use any day's scorer from this block (same fn)
        sfn = score_by_date[list(test_days)[0]]
        sim = simulate_joint(test, score_fn=sfn)
        cross_events.extend(sim["events"])
    sm = summarize_replay({
        "events": cross_events,
        "hard_cap_violations": 0,
        "max_open_plus_pending": 5,
        "occupied_slot_sec": 0.0,
        "max_concurrent_notional_yen": 0.0,
        "p95_concurrent_notional_yen": 0.0,
        "max_pending_reserved_notional_yen": 0.0,
    })
    checks = {
        "admitted": sm.get("admitted") == X36_CROSS["admitted"],
        "fills": sm.get("fills") == X36_CROSS["fills"],
        "positive_days": sm.get("positive_days") == X36_CROSS["positive_days"],
        "pnl": abs(float(sm.get("total_pnl_yen") or 0) - X36_CROSS["total_pnl_yen"]) < 1.0,
        "pf": abs(float(sm.get("pf") or 0) - X36_CROSS["pf"]) < 1e-6,
        "hard_cap": (sm.get("hard_cap_violations") or 0) == 0,
    }
    return {
        "summary": sm,
        "events": cross_events,
        "checks": checks,
        "pass": all(checks.values()),
        "observed": {
            "admitted": sm.get("admitted"),
            "fills": sm.get("fills"),
            "total_pnl_yen": sm.get("total_pnl_yen"),
            "pf": sm.get("pf"),
            "positive_days": sm.get("positive_days"),
        },
    }


def run_capital_continuous(
    panel: list[dict],
    score_by_date: dict,
    *,
    initial_cash: float | None,
) -> dict[str, Any]:
    """Chronological continuous replay with per-date cross-fitted scorers."""
    ordered = sorted(panel, key=lambda e: (e["date"], float(e["signal_time"]), str(e["symbol"])))
    sim = simulate_joint_capital(
        ordered,
        score_fn_by_date=score_by_date,
        initial_cash=initial_cash,
        continuous=True,
    )
    # economics summary akin to summarize_replay
    events = sim["events"]
    opp = [
        float(e["canonical_exit_ret_bps"])
        if e.get("accepted") and e.get("canonical_exit_ret_bps") is not None
        else 0.0
        for e in events
    ]
    by_day: dict[str, list[float]] = defaultdict(list)
    by_ss: dict[tuple, list[float]] = defaultdict(list)
    for e, v in zip(events, opp):
        by_day[e["date"]].append(v)
        by_ss[(e["date"], e["symbol"], e["session"])].append(v)
    day_means = {d: float(np.mean(v)) for d, v in by_day.items()}
    pos = sum(x for x in opp if x > 0)
    neg = sum(x for x in opp if x < 0)
    fill_rets = [
        float(e["canonical_exit_ret_bps"])
        for e in events
        if e.get("accepted") and e.get("canonical_exit_ret_bps") is not None
    ]
    econ = {
        "opp_bps_per_signal": float(np.mean(opp)) if opp else None,
        "bps_per_fill": float(np.mean(fill_rets)) if fill_rets else None,
        "pf": float(pos / abs(neg)) if abs(neg) > 1e-12 else None,
        "positive_days": sum(1 for v in day_means.values() if v > 0),
        "negative_days": sum(1 for v in day_means.values() if v < 0),
        "n_days": len(day_means),
        "ss_balanced": float(np.mean([np.mean(v) for v in by_ss.values()])) if by_ss else None,
        "day_balanced": float(np.mean(list(day_means.values()))) if day_means else None,
        "fill_rate_admitted": (
            float(sim["accepted_fills"] / sim["orders_admitted"])
            if sim["orders_admitted"] else None
        ),
    }
    blocked_req = sim.get("required_cash_blocked") or []
    req_dist = None
    if blocked_req:
        a = np.asarray(blocked_req, dtype=float)
        req_dist = {
            "n": int(a.size),
            "median": float(np.median(a)),
            "p75": float(np.quantile(a, 0.75)),
            "p90": float(np.quantile(a, 0.90)),
            "max": float(np.max(a)),
        }
    return {**sim, "economics": econ, "capital_blocked_required_cash": req_dist}


def symbol_285a_stats(events: list[dict]) -> dict[str, Any]:
    rows = [e for e in events if e["symbol"] == SYMBOL_285A]
    return {
        "symbol": SYMBOL_285A,
        "signals": len(rows),
        "admitted": sum(1 for e in rows if e.get("admitted")),
        "capital_blocked": sum(1 for e in rows if e.get("CAPITAL_BLOCKED")),
        "capacity_blocked": sum(1 for e in rows if e.get("CAPACITY_BLOCKED")),
        "fills": sum(1 for e in rows if e.get("accepted")),
        "net_pnl_yen": float(sum(float(e.get("realized_pnl_yen") or 0.0) for e in rows)),
        "required_cash_sample": [
            float(e["required_cash"]) for e in rows[:5] if e.get("required_cash") is not None
        ],
    }


def decide_verdict(*, identity_ok: bool, primary: dict) -> dict[str, Any]:
    if not identity_ok:
        return {
            "verdict": VERDICT_IDENTITY_FAIL,
            "reason": "unlimited X36 identity failed — STOP",
        }
    pnl = float(primary.get("total_pnl_yen_cash") or primary.get("total_pnl_yen_realized") or 0.0)
    orig = X36_CROSS["total_pnl_yen"]
    ratio = pnl / orig if orig else None
    pos = (primary.get("economics") or {}).get("positive_days") or 0
    pf = (primary.get("economics") or {}).get("pf")

    if pnl <= 0:
        return {"verdict": VERDICT_NEGATIVE, "reason": f"PnL={pnl}", "pnl_ratio": ratio}

    # weak if PnL < 25% of original OR pos days < 9 OR PF <= 1
    weak = (
        (ratio is not None and ratio < 0.25)
        or pos < 9
        or (pf is not None and pf <= 1.0)
    )
    if weak:
        return {
            "verdict": VERDICT_WEAK,
            "reason": f"positive but reduced: ratio={ratio} pos={pos} pf={pf}",
            "pnl_ratio": ratio,
        }
    return {
        "verdict": VERDICT_POSITIVE,
        "reason": f"capital-constrained still positive robust: ratio={ratio} pos={pos} pf={pf}",
        "pnl_ratio": ratio,
    }
