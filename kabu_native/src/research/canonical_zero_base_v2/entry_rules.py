"""Strategy-distinct ENTRY rules from feature ranking + interactions (no T0–T9)."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from research.canonical_zero_base_v2.constants import ENTRY_CAND_CAP_PER_STRAT, SEED
from research.canonical_zero_base_v2.entry_features import compute_entry_features
from research.canonical_zero_base_v2.episodes import Episode
from research.canonical_zero_base_v2.interactions import feature_group
from research.canonical_zero_base_v2.loader import Tick
from research.canonical_zero_base_v2.outcome_labels import path_metrics


@dataclass
class EntryRule:
    rule_id: str
    strategy_id: str
    state_requirements: list[str]
    features: list[str]
    directions: list[int]  # +1 above thr, -1 below
    thresholds: dict[str, float]
    threshold_source: str
    confirmation_events: int
    expected_horizon_sec: int
    invalidation_premise: str
    complexity: int
    groups: list[str] = field(default_factory=list)


# Distinct feature preferences per strategy (not identical rule sets)
STRAT_PREF = {
    "Z1": {
        "states": ["RECLAIM_CONFIRMED", "ACTIVE"],
        "prefer": ["bounce_low_", "reclaim_", "uptick_ratio_", "ask_depletion_", "bid_replenish_", "pullback_", "signed_vol_"],
        "horizon": 120,
        "invalidation": "pullback_low_breach",
    },
    "Z2": {
        "states": ["BREAKOUT_CONFIRMED", "ACTIVE"],
        "prefer": ["dist_high_", "vol_rate_", "uptick_ratio_", "ask_depletion_", "range_", "breakout_", "trade_freq_"],
        "horizon": 60,
        "invalidation": "breakout_level_reentry",
    },
    "Z3": {
        "states": ["ABSORPTION_CONFIRMED", "ACTIVE"],
        "prefer": ["wall_", "absorption", "ask_qty", "ask_depletion_", "price_stable", "signed_vol_", "aggressive_buy"],
        "horizon": 30,
        "invalidation": "wall_reformation",
    },
    "Z4": {
        "states": ["EXPANSION_CONFIRMED", "ACTIVE"],
        "prefer": ["compression_", "range_", "vol_rate_", "rv_", "uptick_ratio_", "depth_imb_", "expansion"],
        "horizon": 60,
        "invalidation": "range_reentry",
    },
}


def _pick_features(ranked: Sequence[dict], strategy_id: str, rng: random.Random) -> list[list[str]]:
    pref = STRAT_PREF[strategy_id]["prefer"]
    scored = []
    for r in ranked[:80]:
        f = r["feature"]
        bonus = 2.0 if any(p in f for p in pref) else 0.0
        scored.append((-(r.get("score") or 0) - bonus, f))
    scored.sort()
    ordered = [f for _, f in scored]
    combos = []
    # build distinct multi-group sets
    for n in (3, 4, 5):
        for _ in range(40):
            pick = []
            groups = set()
            pool = list(ordered)
            rng.shuffle(pool)
            for f in pool:
                g = feature_group(f)
                if g == "OTHER":
                    continue
                if g in groups and len(pick) < n - 1:
                    continue
                pick.append(f)
                groups.add(g)
                if len(pick) >= n:
                    break
            # require PRICE + >=2 other groups + at least one dynamic/sequence-ish
            gs = {feature_group(f) for f in pick}
            has_price = any(feature_group(f) == "PRICE" or f.startswith("return_") or "range_" in f or "bounce_" in f or "dist_" in f or "compression" in f for f in pick)
            # also count reclaim/breakout as price
            has_price = has_price or any(x in "".join(pick) for x in ("reclaim", "breakout", "pullback", "impulse", "vwap"))
            others = len([g for g in gs if g not in ("PRICE", "OTHER")])
            if not has_price or others < 2:
                continue
            if len(pick) >= 3 and pick not in combos:
                combos.append(pick)
            if len(combos) >= ENTRY_CAND_CAP_PER_STRAT:
                break
        if len(combos) >= ENTRY_CAND_CAP_PER_STRAT:
            break
    return combos[:ENTRY_CAND_CAP_PER_STRAT]


def build_entry_rules(
    strategy_id: str,
    ranked: Sequence[dict[str, Any]],
    train_feature_rows: Sequence[dict[str, Any]],
) -> list[EntryRule]:
    rng = random.Random(SEED + sum(ord(c) for c in strategy_id) * 13)
    combos = _pick_features(ranked, strategy_id, rng)
    # train medians as thresholds
    med: dict[str, float] = {}
    for f in {x for c in combos for x in c}:
        vals = sorted(float(r["features"][f]) for r in train_feature_rows if r["features"].get(f) is not None)
        if vals:
            med[f] = vals[len(vals) // 2]
    rules = []
    meta = STRAT_PREF[strategy_id]
    for i, feats in enumerate(combos):
        thr = {f: med[f] for f in feats if f in med}
        if len(thr) < 3:
            continue
        dirs = []
        for f in feats:
            # direction from ranked score sign via d_winner_vs_never if present
            d = 1
            for r in ranked:
                if r["feature"] == f and r.get("d_winner_vs_never") is not None:
                    d = 1 if r["d_winner_vs_never"] > 0 else -1
                    break
            dirs.append(d)
        rules.append(EntryRule(
            rule_id=f"{strategy_id}_E{i:03d}",
            strategy_id=strategy_id,
            state_requirements=list(meta["states"]),
            features=feats,
            directions=dirs,
            thresholds=thr,
            threshold_source="TRAIN_median",
            confirmation_events=2,
            expected_horizon_sec=int(meta["horizon"]),
            invalidation_premise=str(meta["invalidation"]),
            complexity=len(feats),
            groups=sorted({feature_group(f) for f in feats}),
        ))
    return rules


def rule_fires(feats: dict[str, Any], rule: EntryRule) -> bool:
    for f, d in zip(rule.features, rule.directions):
        v = feats.get(f)
        thr = rule.thresholds.get(f)
        if v is None or thr is None:
            return False
        if d >= 0 and float(v) < thr:
            return False
        if d < 0 and float(v) > thr:
            return False
    return True


def collect_entries(
    strategy_id: str,
    rule: EntryRule,
    episodes: Sequence[Episode],
    streams: dict[str, list[Tick]],
) -> list[dict[str, Any]]:
    """One episode one entry; executable Ask required."""
    used_ep = set()
    out = []
    for ep in episodes:
        if ep.strategy_id != strategy_id or not ep.entry_ready or ep.entry_idx is None:
            continue
        if ep.episode_id in used_ep:
            continue
        key = f"{ep.day}|{ep.symbol}"
        ticks = streams.get(key) or []
        idx = ep.entry_idx
        if idx >= len(ticks):
            continue
        t = ticks[idx]
        ask = t.board.canonical_best_ask
        if ask is None or ask <= 0 or not t.board.canonical_quote_valid:
            continue
        feats = compute_entry_features(ticks, idx)
        if not rule_fires(feats, rule):
            continue
        used_ep.add(ep.episode_id)
        out.append({
            "day": ep.day,
            "symbol": ep.symbol,
            "episode_id": ep.episode_id,
            "entry_idx": idx,
            "entry_time": t.ts,
            "entry_ask": float(ask),
            "stream_key": key,
            "strategy_id": strategy_id,
            "rule_id": rule.rule_id,
            "features": feats,
            "levels": ep.levels,
        })
    return out


def opportunity_pf(entries: Sequence[dict], streams: dict[str, list[Tick]], *, horizon: float = 120.0) -> dict[str, Any]:
    wins = losses = 0.0
    pnls = []
    never = early = 0
    for e in entries:
        ticks = streams[e["stream_key"]]
        m = path_metrics(ticks, e["entry_idx"], e["entry_ask"], max_sec=horizon)
        if not m.get("evaluable"):
            continue
        yen = float(m.get("net_terminal_yen") or 0)
        pnls.append(yen)
        if yen >= 0:
            wins += yen
        else:
            losses += -yen
        if m.get("never_profitable"):
            never += 1
        if m.get("mae", 0) <= -0.8 and (m.get("time_to_mae") or 999) <= 30:
            early += 1
    n = len(pnls)
    pf = (wins / losses) if losses > 0 else (float("inf") if wins > 0 else None)
    return {
        "n": n,
        "pnl": sum(pnls),
        "pf": pf,
        "never_rate": never / n if n else None,
        "early_stop_rate": early / n if n else None,
        "winner_capture": sum(1 for p in pnls if p > 0) / n if n else 0.0,
        "mean": (sum(pnls) / n) if n else None,
    }
