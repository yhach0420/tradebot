"""Concentration formula + D1/D2/LOSO/LODO diagnostics (X36/X36R semantics)."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.e1_x36_joint_allocator import MAX_SYMBOL_CONTRIB, OUTER_BLOCKS
from research.e1_x36_joint_allocator.cv import outer_train_test
from research.e1_x36_joint_allocator.metrics import summarize_replay
from research.e1_x36_joint_allocator.models import fit_spec, score_fn_from_fit
from research.e1_x36_joint_allocator.replay import simulate_joint
from research.e1_x39b_universe_bridge import X36_SELECTED
from research.e1_x39b_universe_bridge.outer_replay import crossfit_fixed_specs

from . import DEP_MIN_OPP, DEP_MIN_PNL_FRAC, DEP_MIN_POS_DAYS


def concentration_definition() -> dict[str, Any]:
    """Exact X36 decide_verdict / summarize_replay definition — no speculation."""
    return {
        "metric_name": "max_symbol_contrib_share",
        "gate_name": "no_severe_symbol_conc",
        "source_files": [
            "src/research/e1_x36_joint_allocator/metrics.py:summarize_replay",
            "src/research/e1_x36_joint_allocator/verdict.py:_robust_ok",
            "src/research/e1_x36_joint_allocator/__init__.py:MAX_SYMBOL_CONTRIB",
        ],
        "numerator": (
            "sum of realized_pnl_yen over accepted fills for a symbol where pnl > 0 "
            "(gross positive PnL of that symbol)"
        ),
        "denominator": (
            "sum of realized_pnl_yen over ALL accepted fills where pnl > 0 "
            "(gross positive PnL across symbols; NOT net total PnL)"
        ),
        "threshold": MAX_SYMBOL_CONTRIB,
        "severe_if": "max_symbol_contrib_share > MAX_SYMBOL_CONTRIB (0.50)",
        "zero_negative_treatment": (
            "accepted fills with pnl <= 0 do not enter numerator or denominator; "
            "they do not reduce the share of the top winner"
        ),
        "gross_positive_not_net": True,
        "x36r_precedent": (
            "same formula documented in e1_x36r_freeze_integrity/concentration.py"
        ),
    }


def symbol_contribution_table(events: list[dict]) -> dict[str, Any]:
    by_pos: dict[str, float] = defaultdict(float)
    by_neg: dict[str, float] = defaultdict(float)
    by_net: dict[str, float] = defaultdict(float)
    by_fills: dict[str, int] = defaultdict(int)
    tot_pos = 0.0
    tot_net = 0.0
    for e in events:
        if not e.get("accepted"):
            continue
        s = e["symbol"]
        p = float(e.get("realized_pnl_yen") or 0.0)
        by_fills[s] += 1
        by_net[s] += p
        tot_net += p
        if p > 0:
            by_pos[s] += p
            tot_pos += p
        elif p < 0:
            by_neg[s] += p

    rows = []
    for s in sorted(set(by_fills) | set(by_net)):
        rows.append({
            "symbol": s,
            "fills": by_fills[s],
            "gross_positive_pnl": float(by_pos.get(s, 0.0)),
            "gross_negative_pnl": float(by_neg.get(s, 0.0)),
            "net_pnl": float(by_net.get(s, 0.0)),
            "share_of_gross_positive": (
                float(by_pos[s] / tot_pos) if tot_pos > 1e-12 and s in by_pos else 0.0
            ),
            "share_of_net_pnl": (
                float(by_net[s] / tot_net) if abs(tot_net) > 1e-12 else None
            ),
        })
    rows.sort(key=lambda r: (-r["share_of_gross_positive"], -r["net_pnl"], r["symbol"]))
    max_share = rows[0]["share_of_gross_positive"] if rows else None
    top = rows[0]["symbol"] if rows else None
    return {
        "definition": concentration_definition(),
        "gross_positive_pnl_yen": tot_pos,
        "total_net_pnl_yen": tot_net,
        "max_symbol_contrib_share": max_share,
        "top_contributor": top,
        "threshold": MAX_SYMBOL_CONTRIB,
        "margin_to_threshold": (
            None if max_share is None else float(max_share - MAX_SYMBOL_CONTRIB)
        ),
        "severe": bool(max_share is not None and max_share > MAX_SYMBOL_CONTRIB),
        "top10": rows[:10],
        "all_rows": rows,
    }


def d1_contribution_removal(events: list[dict], symbol: str) -> dict[str, Any]:
    """Zero top-symbol accepted PnL/ret; no re-ranking / re-admission."""
    rows = []
    for e in events:
        r = dict(e)
        if r.get("symbol") == symbol and r.get("accepted"):
            r["realized_pnl_yen"] = 0.0
            r["realized_ret_bps"] = 0.0
            r["canonical_exit_ret_bps"] = 0.0
        rows.append(r)
    fake = {
        "events": rows,
        "hard_cap_violations": 0,
        "max_open_plus_pending": 5,
        "occupied_slot_sec": 0.0,
        "max_concurrent_notional_yen": 0.0,
        "p95_concurrent_notional_yen": 0.0,
        "max_pending_reserved_notional_yen": 0.0,
    }
    sm = summarize_replay(fake)
    return {
        "mode": "D1_CONTRIBUTION_REMOVAL",
        "symbol": symbol,
        "no_retrain": True,
        "no_re_ranking": True,
        "no_re_admission": True,
        "no_slot_replacement": True,
        "remaining_total_pnl_yen": sm.get("total_pnl_yen"),
        "pf": sm.get("pf"),
        "positive_days": sm.get("positive_days"),
        "negative_days": sm.get("negative_days"),
        "opp_bps_per_signal": sm.get("opp_bps_per_signal"),
        "ss_balanced": sm.get("ss_balanced"),
        "day_balanced": sm.get("day_balanced"),
        "fills_unchanged": sm.get("fills"),
        "admitted_unchanged": sm.get("admitted"),
        "hard_cap_violations": sm.get("hard_cap_violations"),
    }


def _crossfit_test_filter(
    train_panel: list[dict],
    test_panel: list[dict],
    *,
    test_exclude_symbols: set[str] | None = None,
    test_exclude_symbol_days: set[tuple[str, str]] | None = None,
    label: str = "D2",
) -> dict[str, Any]:
    """Same frozen outer specs; filter TEST membership only."""
    folds = {}
    cross_events: list[dict] = []
    for block in ("A", "B", "C", "D"):
        train_days, test_days = outer_train_test(block)
        train = [e for e in train_panel if e["date"] in train_days]
        test = [e for e in test_panel if e["date"] in test_days]
        if test_exclude_symbols:
            test = [e for e in test if e["symbol"] not in test_exclude_symbols]
        if test_exclude_symbol_days:
            test = [
                e for e in test
                if (e["date"], e["symbol"]) not in test_exclude_symbol_days
            ]
        spec = dict(X36_SELECTED[block])
        fit = fit_spec(train, spec)
        sfn = score_fn_from_fit(fit)
        sim = simulate_joint(test, score_fn=sfn)
        sm = summarize_replay(sim)
        folds[block] = {
            "test": {
                "admitted": sm.get("admitted"),
                "fills": sm.get("fills"),
                "total_pnl_yen": sm.get("total_pnl_yen"),
                "pf": sm.get("pf"),
                "positive_days": sm.get("positive_days"),
                "hard_cap_violations": sm.get("hard_cap_violations"),
            },
            "test_n": len(test),
        }
        for e in sim["events"]:
            ee = dict(e)
            ee["_outer_block"] = block
            cross_events.append(ee)
    fake = {
        "events": cross_events,
        "hard_cap_violations": sum(folds[b]["test"].get("hard_cap_violations") or 0 for b in folds),
        "max_open_plus_pending": 5,
        "occupied_slot_sec": 0.0,
        "max_concurrent_notional_yen": 0.0,
        "p95_concurrent_notional_yen": 0.0,
        "max_pending_reserved_notional_yen": 0.0,
    }
    cross = summarize_replay(fake)
    return {
        "label": label,
        "folds": folds,
        "cross_fitted": cross,
        "cross_events": cross_events,
    }


def d2_candidate_removal(
    legacy_panel: list[dict],
    am_panel: list[dict],
    symbol: str,
    *,
    bridge_events: list[dict],
) -> dict[str, Any]:
    """Remove top symbol from TEST AM membership; slots go to next ranked."""
    out = _crossfit_test_filter(
        legacy_panel, am_panel,
        test_exclude_symbols={symbol},
        label="D2",
    )
    sm = out["cross_fitted"]
    # replacement: fills/pnl on symbols that were not accepted in bridge but are in D2
    bridge_acc = {
        (e["date"], e["symbol"], float(e["signal_time"]))
        for e in bridge_events if e.get("accepted")
    }
    repl = [
        e for e in out["cross_events"]
        if e.get("accepted")
        and (e["date"], e["symbol"], float(e["signal_time"])) not in bridge_acc
    ]
    return {
        "mode": "D2_CANDIDATE_REMOVAL_REPLAY",
        "symbol": symbol,
        "no_retrain": True,
        "universe_rule_unchanged": True,
        "admitted": sm.get("admitted"),
        "fills": sm.get("fills"),
        "total_pnl_yen": sm.get("total_pnl_yen"),
        "pf": sm.get("pf"),
        "positive_days": sm.get("positive_days"),
        "opp_bps_per_signal": sm.get("opp_bps_per_signal"),
        "ss_balanced": sm.get("ss_balanced"),
        "day_balanced": sm.get("day_balanced"),
        "hard_cap_violations": sm.get("hard_cap_violations"),
        "replacement_fills": len(repl),
        "replacement_pnl_yen": float(sum(float(e.get("realized_pnl_yen") or 0) for e in repl)),
        "outer": out["folds"],
        "cross_events": out["cross_events"],
    }


def top_symbol_day_breakdown(events: list[dict], symbol: str) -> dict[str, Any]:
    by_day: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "fills": 0, "gross_pos": 0.0, "gross_neg": 0.0, "net": 0.0,
    })
    fills = 0
    gpos = gneg = net = 0.0
    for e in events:
        if e["symbol"] != symbol or not e.get("accepted"):
            continue
        p = float(e.get("realized_pnl_yen") or 0.0)
        fills += 1
        net += p
        d = by_day[e["date"]]
        d["fills"] += 1
        d["net"] += p
        if p > 0:
            gpos += p
            d["gross_pos"] += p
        elif p < 0:
            gneg += p
            d["gross_neg"] += p
    return {
        "symbol": symbol,
        "fills": fills,
        "gross_positive": gpos,
        "gross_negative": gneg,
        "net_pnl": net,
        "day_breakdown": [
            {"date": d, **v} for d, v in sorted(by_day.items())
        ],
    }


def detail_0731(events: list[dict], symbol: str, d2_events: list[dict]) -> dict[str, Any]:
    day = "20260731"
    bridge_day = [
        e for e in events
        if e["date"] == day and e["symbol"] == symbol
    ]
    admitted = [e for e in bridge_day if e.get("admitted")]
    accepted = [e for e in bridge_day if e.get("accepted")]
    # displaced: at clocks where symbol admitted, who else was in bridge adm vs d2
    d2_adm = {
        (e["date"], float(e["signal_time"])): [
            x["symbol"] for x in d2_events
            if x["date"] == e["date"] and float(x["signal_time"]) == float(e["signal_time"]) and x.get("admitted")
        ]
        for e in admitted
    }
    return {
        "date": day,
        "symbol": symbol,
        "fills": len(accepted),
        "net_pnl": float(sum(float(e.get("realized_pnl_yen") or 0) for e in accepted)),
        "admitted_n": len(admitted),
        "ranks_sample": [
            {"signal_time": e.get("signal_time"), "alloc_score": e.get("alloc_score"),
             "admitted": e.get("admitted"), "accepted": e.get("accepted")}
            for e in sorted(bridge_day, key=lambda x: float(x["signal_time"]))[:16]
        ],
        "d2_replacement_at_admitted_clocks": [
            {"signal_time": st, "d2_admitted": syms}
            for (_, st), syms in sorted(d2_adm.items(), key=lambda x: x[0][1])[:16]
        ],
    }


def added_symbol_block_identity(
    legacy_panel: list[dict],
    am_panel: list[dict],
    added_symbol_days: list[dict[str, str]],
    *,
    x36_targets: dict[str, Any],
) -> dict[str, Any]:
    """Remove AM-only symbol-days from TEST → expect X36 legacy identity."""
    excl = {(r["date"], r["symbol"]) for r in added_symbol_days}
    out = _crossfit_test_filter(
        legacy_panel, am_panel,
        test_exclude_symbol_days=excl,
        label="ADDED_BLOCK",
    )
    sm = out["cross_fitted"]
    checks = {
        "admitted": int(sm.get("admitted") or 0) == int(x36_targets["admitted"]),
        "fills": int(sm.get("fills") or 0) == int(x36_targets["fills"]),
        "pnl": abs(float(sm.get("total_pnl_yen") or 0) - float(x36_targets["pnl"])) < 1.0,
        "pf": sm.get("pf") is not None and abs(float(sm["pf"]) - float(x36_targets["pf"])) < 1e-9,
        "positive_days": int(sm.get("positive_days") or 0) == int(x36_targets["pos_days"]),
        "hard_cap": int(sm.get("hard_cap_violations") or 0) == 0,
    }
    return {
        "mode": "LEGACY_POOL_IDENTITY_DIAGNOSTIC",
        "not_prospective_rule": True,
        "removed_symbol_days_n": len(excl),
        "observed": {
            "admitted": sm.get("admitted"),
            "fills": sm.get("fills"),
            "total_pnl_yen": sm.get("total_pnl_yen"),
            "pf": sm.get("pf"),
            "positive_days": sm.get("positive_days"),
            "hard_cap_violations": sm.get("hard_cap_violations"),
        },
        "expected_x36": x36_targets,
        "checks": checks,
        "pass": all(checks.values()),
    }


def loso_filled_symbols(
    legacy_panel: list[dict],
    am_panel: list[dict],
    bridge_events: list[dict],
) -> dict[str, Any]:
    """Leave-one-symbol-out on TEST for every symbol that filled in Bridge."""
    filled = sorted({e["symbol"] for e in bridge_events if e.get("accepted")})
    rows = []
    for i, sym in enumerate(filled):
        print(f"  LOSO {i+1}/{len(filled)} {sym}...", flush=True)
        out = _crossfit_test_filter(
            legacy_panel, am_panel,
            test_exclude_symbols={sym},
            label=f"LOSO_{sym}",
        )
        sm = out["cross_fitted"]
        rows.append({
            "symbol": sym,
            "total_pnl_yen": sm.get("total_pnl_yen"),
            "pf": sm.get("pf"),
            "positive_days": sm.get("positive_days"),
            "fills": sm.get("fills"),
            "hard_cap_violations": sm.get("hard_cap_violations"),
            "opp_bps_per_signal": sm.get("opp_bps_per_signal"),
        })
    # positive strategies: pnl>0 and pf>1 and pos_days>=9 and hard_cap=0
    pos_strat = [
        r for r in rows
        if (r.get("total_pnl_yen") or 0) > 0
        and r.get("pf") is not None and float(r["pf"]) > 1.0
        and int(r.get("positive_days") or 0) >= DEP_MIN_POS_DAYS
        and int(r.get("hard_cap_violations") or 0) == 0
    ]
    worst_pnl = min(rows, key=lambda r: float(r.get("total_pnl_yen") or 0)) if rows else None
    worst_pf = min(
        (r for r in rows if r.get("pf") is not None),
        key=lambda r: float(r["pf"]),
        default=None,
    )
    return {
        "n_symbols": len(filled),
        "rows": rows,
        "worst_pnl": worst_pnl,
        "worst_pf": worst_pf,
        "n_positive_strategies": len(pos_strat),
        "no_refit": True,
    }


def lodo_day_contribution(events: list[dict]) -> dict[str, Any]:
    """Remove one day at a time from cross-fitted event list; re-aggregate."""
    days = sorted({e["date"] for e in events})
    rows = []
    for hold in days:
        sub = [e for e in events if e["date"] != hold]
        fake = {
            "events": sub,
            "hard_cap_violations": 0,
            "max_open_plus_pending": 5,
            "occupied_slot_sec": 0.0,
            "max_concurrent_notional_yen": 0.0,
            "p95_concurrent_notional_yen": 0.0,
            "max_pending_reserved_notional_yen": 0.0,
        }
        sm = summarize_replay(fake)
        rows.append({
            "holdout_day": hold,
            "remaining_pnl": sm.get("total_pnl_yen"),
            "pf": sm.get("pf"),
            "positive_days": sm.get("positive_days"),
            "n_days": sm.get("n_days"),
            "ss_balanced": sm.get("ss_balanced"),
            "day_balanced": sm.get("day_balanced"),
            "fills": sm.get("fills"),
        })
    r731 = next((r for r in rows if r["holdout_day"] == "20260731"), None)
    return {"rows": rows, "day_20260731_removed": r731, "no_retrain": True}


def dep_collapse(d: dict, *, orig_pnl: float) -> bool:
    """X36R precedent collapse test — not a new invented gate."""
    pnl = d.get("total_pnl_yen") if "total_pnl_yen" in d else d.get("remaining_total_pnl_yen")
    opp = d.get("opp_bps_per_signal")
    pos = d.get("positive_days")
    if pnl is None:
        return True
    if float(pnl) < float(orig_pnl) * DEP_MIN_PNL_FRAC:
        return True
    if opp is not None and float(opp) <= DEP_MIN_OPP:
        return True
    if pos is not None and int(pos) < DEP_MIN_POS_DAYS:
        return True
    return False
