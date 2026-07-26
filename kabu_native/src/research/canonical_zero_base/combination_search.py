"""Coarse combination search with hard caps (no fine grid, no legacy scores)."""
from __future__ import annotations

import random
from collections import defaultdict
from typing import Any, Optional

from research.canonical_zero_base.cap5_portfolio import CapTrade, replay_cap5
from research.canonical_zero_base.canonical_loader import Tick
from research.canonical_zero_base.constants import (
    OOS_CARRY_CAP,
    QUANTILE_LEVELS,
    RAW_COMBINATION_CAP,
    SEED,
    TRAIN_PASS_CAP,
    VAL_PASS_CAP,
)
from research.canonical_zero_base.dependency import dependency_metrics
from research.canonical_zero_base.matched_exit import simulate_exit
from research.canonical_zero_base.opportunity_labels import opportunity_from_path
from research.canonical_zero_base.strategies_core import TEMPLATES, pass_template, scan_triggers
from research.canonical_zero_base.strategy_contract import EntryRule


def _quantile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    i = int(max(0, min(len(ys) - 1, round((len(ys) - 1) * q))))
    return ys[i]


def train_thresholds(events_by_key: dict[str, list], rng: random.Random) -> list[dict[str, float]]:
    """Build coarse threshold sets from TRAIN feature distributions."""
    tops, vols, spreads, upticks = [], [], [], []
    for evs in events_by_key.values():
        for e in evs[:: max(1, len(evs) // 200)]:
            f = e.features
            if f.get("canonical_top_imbalance") is not None:
                tops.append(float(f["canonical_top_imbalance"]))
            if f.get("volume_vs_recent_baseline") is not None:
                vols.append(float(f["volume_vs_recent_baseline"]))
            if f.get("spread_bps") is not None:
                spreads.append(float(f["spread_bps"]))
            if f.get("uptick_ratio") is not None:
                upticks.append(float(f["uptick_ratio"]))
    sets = []
    for q in QUANTILE_LEVELS:
        sets.append({
            "top_imb": _quantile(tops, q) if tops else 0.52,
            "vol_base": _quantile(vols, q) if vols else 1.0,
            "vol_acc": 0.0,
            "uptick_ratio": _quantile(upticks, q) if upticks else 0.55,
            "consec_up": 2,
            "spread_bps_max": _quantile(spreads, min(0.8, q + 0.15)) if spreads else 40.0,
            "min_qty": 100.0,
        })
    # dedupe-ish
    rng.shuffle(sets)
    return sets[:5]


def build_rules(strategy_id: str, thr_sets: list[dict[str, float]], rng: random.Random) -> list[EntryRule]:
    rules = []
    for ti, thr in enumerate(thr_sets):
        for tmpl, groups in TEMPLATES.items():
            # require PRICE + >=2 other groups for T4+; allow T0-T3 as diagnostics
            n_g = 1 + len(groups)
            if tmpl in ("T0", "T1", "T2", "T3"):
                # keep but limited
                pass
            elif n_g < 3:
                continue
            rules.append(
                EntryRule(
                    rule_id=f"{strategy_id}_{tmpl}_q{ti}",
                    strategy_id=strategy_id,
                    template=tmpl,
                    groups=("PRICE",) + groups,
                    conditions=dict(thr),
                    n_conditions=n_g,
                )
            )
    rng.shuffle(rules)
    return rules[:RAW_COMBINATION_CAP]


def _streams_for_days(all_streams: dict[str, list[Tick]], days: list[str]) -> dict[str, list[Tick]]:
    return {k: v for k, v in all_streams.items() if k.split("|")[0] in days}


def evaluate_rule(
    rule: EntryRule,
    events_by_key: dict[str, list],
    ticks_by_key: dict[str, list[Tick]],
    *,
    exit_mode: str,
    portfolio_id: str,
) -> dict[str, Any]:
    trades: list[CapTrade] = []
    n_trig = 0
    opp_rows = []
    for key, evs in events_by_key.items():
        ticks = ticks_by_key.get(key) or []
        if not ticks:
            continue
        triggers = scan_triggers(evs, rule.strategy_id, thr=rule.conditions)
        for tr in triggers:
            if not pass_template(tr.groups_hit, rule.template):
                continue
            # skip pure diagnostic templates for final scoring weight — still count
            n_trig += 1
            idx = tr.event.tick.idx
            if idx >= len(ticks) - 1:
                continue
            opp = opportunity_from_path(ticks, idx, entry_ask=tr.entry_ask)
            opp_rows.append(opp)
            ex = simulate_exit(
                ticks,
                idx,
                entry_ask=tr.entry_ask,
                strategy_id=rule.strategy_id,
                exit_mode=exit_mode,
                entry_features=tr.event.features,
            )
            if not ex.get("evaluable"):
                continue
            hold = (ex["exit_time"] - tr.event.tick.ts).total_seconds()
            stop = ex["exit_reason"] == "hard_stop"
            trades.append(
                CapTrade(
                    day=tr.event.tick.day,
                    symbol=tr.event.tick.symbol,
                    episode_id=tr.episode_id,
                    entry_time=tr.event.tick.ts,
                    exit_time=ex["exit_time"],
                    entry_price=tr.entry_ask,
                    exit_price=float(ex["exit_bid"]),
                    pnl_5bps=float(ex["pnl_5bps"]),
                    exit_reason=str(ex["exit_reason"]),
                    strategy_id=rule.strategy_id,
                    setup_id=f"{rule.rule_id}:{tr.episode_id}",
                    session="AM" if tr.event.tick.ts.hour < 12 else "PM",
                    mfe=float(ex["mfe"]),
                    mae=float(ex["mae"]),
                    stop=stop,
                    early_stop=stop and hold <= 60,
                    no_progress=float(ex["mfe"]) < 0.3 and "session" in str(ex["exit_reason"]),
                    winner=float(ex["pnl_5bps"]) > 0 and float(ex["mfe"]) >= 0.8,
                )
            )
    cap = replay_cap5(trades, portfolio_id=portfolio_id)
    dep = dependency_metrics(trades)
    # opportunity summary
    n_opp = len(opp_rows)
    never = sum(1 for o in opp_rows if o.get("never_profitable")) / n_opp if n_opp else None
    composite = 0.0
    pf = cap.get("PF_5bps")
    try:
        pf_f = float(pf) if pf not in (None, float("inf")) else 0.0
    except Exception:
        pf_f = 0.0
    composite += (cap.get("pnl_5bps") or 0) / 10000.0
    composite += pf_f * 10
    composite += (cap.get("pos_days") or 0) - (cap.get("neg_days") or 0)
    composite -= rule.n_conditions * 0.5  # simplicity penalty
    if dep.get("DEPENDENCY_BLOCKED"):
        composite -= 20
    return {
        "rule_id": rule.rule_id,
        "template": rule.template,
        "n_conditions": rule.n_conditions,
        "n_triggers": n_trig,
        "never_profitable_rate": never,
        "cap": cap,
        "dependency": dep,
        "composite": composite,
        "exit_mode": exit_mode,
        "trades": trades,
    }


def select_best(
    results: list[dict[str, Any]],
    *,
    cap: int,
) -> list[dict[str, Any]]:
    # Prefer multi-group (T4+) strongly; T0–T3 only as fill if needed.
    def _rank(r: dict[str, Any]) -> tuple:
        tmpl = r.get("template", "")
        multi = 0 if tmpl in ("T4", "T5", "T6", "T7", "T8", "T9") else 1
        return (multi, -r["composite"], r["n_conditions"], r["rule_id"])

    scored = sorted(results, key=_rank)
    out: list[dict[str, Any]] = []
    weak_slots = max(1, cap // 5)
    weak_used = 0
    for r in scored:
        tmpl = r.get("template", "")
        if tmpl in ("T0", "T1", "T2", "T3"):
            if weak_used >= weak_slots and any(
                x.get("template") in ("T4", "T5", "T6", "T7", "T8", "T9") for x in scored
            ):
                continue
            weak_used += 1
        out.append(r)
        if len(out) >= cap:
            break
    return out


def run_lane(
    strategy_id: str,
    *,
    events_train: dict,
    events_val: dict,
    events_oos: dict,
    ticks_all: dict[str, list[Tick]],
    exit_modes: tuple[str, ...] = ("X0", "X4", "X6"),
) -> dict[str, Any]:
    rng = random.Random(SEED + sum(ord(c) for c in strategy_id))
    thr_sets = train_thresholds(events_train, rng)
    rules = build_rules(strategy_id, thr_sets, rng)
    # Prefer multi-group templates first for speed under cap
    rules = sorted(rules, key=lambda r: (0 if r.template in ("T4", "T5", "T6", "T7", "T8") else 1, r.rule_id))
    raw_n = len(rules)

    train_results = []
    for rule in rules:
        best = None
        for xm in exit_modes:
            r = evaluate_rule(rule, events_train, ticks_all, exit_mode=xm, portfolio_id=f"TR_{rule.rule_id}_{xm}")
            if best is None or r["composite"] > best["composite"]:
                best = r
                best["rule"] = rule
        if best:
            train_results.append(best)
    train_pass = select_best(train_results, cap=TRAIN_PASS_CAP)

    val_results = []
    for tr in train_pass:
        rule = tr["rule"]
        xm = tr["exit_mode"]
        r = evaluate_rule(rule, events_val, ticks_all, exit_mode=xm, portfolio_id=f"VA_{rule.rule_id}")
        r["rule"] = rule
        r["train_composite"] = tr["composite"]
        val_results.append(r)
    val_pass = select_best(val_results, cap=VAL_PASS_CAP)

    oos_carry = select_best(val_pass, cap=OOS_CARRY_CAP)
    oos_results = []
    for tr in oos_carry:
        rule = tr["rule"]
        xm = tr["exit_mode"]
        r = evaluate_rule(rule, events_oos, ticks_all, exit_mode=xm, portfolio_id=f"OOS_{rule.rule_id}")
        r["rule"] = {
            "rule_id": rule.rule_id,
            "template": rule.template,
            "groups": rule.groups,
            "conditions": rule.conditions,
            "n_conditions": rule.n_conditions,
        }
        r["exit_mode_final"] = xm
        # strip heavy trades from oos summary later
        oos_results.append(r)

    final = oos_results[0] if oos_results else None
    return {
        "strategy_id": strategy_id,
        "raw_combinations": raw_n,
        "train_pass": len(train_pass),
        "val_pass": len(val_pass),
        "oos_carry": len(oos_carry),
        "train_top": [
            {"rule_id": r["rule_id"], "template": r["template"], "composite": r["composite"], "pnl": r["cap"].get("pnl_5bps"), "PF": r["cap"].get("PF_5bps"), "exit": r["exit_mode"]}
            for r in train_pass[:10]
        ],
        "val_top": [
            {"rule_id": r["rule_id"], "template": r["template"], "composite": r["composite"], "pnl": r["cap"].get("pnl_5bps"), "PF": r["cap"].get("PF_5bps"), "exit": r["exit_mode"]}
            for r in val_pass[:10]
        ],
        "oos_results": [
            {
                "rule": r["rule"],
                "exit_mode": r["exit_mode_final"],
                "cap": {k: v for k, v in r["cap"].items() if k != "daily_pnl"},
                "dependency": r["dependency"],
                "never_profitable_rate": r.get("never_profitable_rate"),
                "n_triggers": r.get("n_triggers"),
            }
            for r in oos_results
        ],
        "final": (
            {
                "rule": final["rule"],
                "exit_mode": final["exit_mode_final"],
                "cap": final["cap"],
                "dependency": final["dependency"],
                "trades": final["trades"],
            }
            if final
            else None
        ),
    }
