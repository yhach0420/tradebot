"""Cross-group interaction candidates — TRAIN only, capped, not fixed-50."""
from __future__ import annotations

import itertools
import random
from typing import Any, Sequence

from research.canonical_zero_base_v2.constants import INTER_2_CAP, INTER_3_CAP, INTER_4_CAP, SEED
from research.canonical_zero_base_v2.entry_separation import WINNER_CLASSES, cohens_d


GROUP_OF = {
    "PRICE": ("return_", "slope_", "range_", "rv_", "dist_", "bounce_", "fall_", "hh_", "ll_", "accel_", "log_return_", "price_eff", "compression", "impulse", "pullback", "reclaim", "breakout", "vwap", "consec_"),
    "VOLUME": ("vol_rate_", "volume_", "vol_"),
    "FLOW": ("uptick_", "downtick_", "signed_", "aggressive_", "flow_"),
    "BOARD": ("imb", "bid_qty", "ask_qty", "spread", "micro", "book_", "depth_", "bid_dep", "ask_dep", "bid_rep", "ask_rep"),
    "WALL": ("wall_", "absorption"),
    "LIQUIDITY": ("exec_", "quote_age", "spread_to"),
}


def feature_group(name: str) -> str:
    for g, prefixes in GROUP_OF.items():
        if any(p in name for p in prefixes):
            return g
    return "OTHER"


def _cond_effect(rows: Sequence[dict], feats: tuple[str, ...], directions: tuple[int, ...]) -> dict[str, Any]:
    """Simple AND of above/below median gates."""
    med = {}
    for f in feats:
        vals = [r["features"].get(f) for r in rows if r["features"].get(f) is not None]
        if not vals:
            return {"n": 0}
        vals = sorted(float(v) for v in vals)
        med[f] = vals[len(vals) // 2]
    hit_w = hit_n = 0
    for r in rows:
        ok = True
        for f, d in zip(feats, directions):
            v = r["features"].get(f)
            if v is None:
                ok = False
                break
            if d >= 0 and float(v) < med[f]:
                ok = False
                break
            if d < 0 and float(v) > med[f]:
                ok = False
                break
        if not ok:
            continue
        if r["class_name"] in WINNER_CLASSES:
            hit_w += 1
        else:
            hit_n += 1
    n = hit_w + hit_n
    return {
        "n": n,
        "winner_rate": hit_w / n if n else None,
        "feats": list(feats),
        "complexity": len(feats),
    }


def generate_interactions(
    rows: Sequence[dict[str, Any]],
    ranked_features: Sequence[dict[str, Any]],
    *,
    top_pool: int = 40,
) -> dict[str, Any]:
    rng = random.Random(SEED + 7)
    pool = [r["feature"] for r in ranked_features[:top_pool]]
    # diversify groups
    by_g: dict[str, list[str]] = {}
    for f in pool:
        by_g.setdefault(feature_group(f), []).append(f)

    pairs = []
    groups = [g for g, fs in by_g.items() if fs and g != "OTHER"]
    for g1, g2 in itertools.combinations(groups, 2):
        for f1 in by_g[g1][:8]:
            for f2 in by_g[g2][:8]:
                pairs.append((f1, f2))
    rng.shuffle(pairs)
    pairs = pairs[:INTER_2_CAP]

    triples = []
    for g1, g2, g3 in itertools.combinations(groups, 3):
        for f1 in by_g[g1][:5]:
            for f2 in by_g[g2][:5]:
                for f3 in by_g[g3][:5]:
                    triples.append((f1, f2, f3))
    rng.shuffle(triples)
    triples = triples[:INTER_3_CAP]

    quads = []
    if len(groups) >= 4:
        for combo in itertools.combinations(groups, 4):
            for f1 in by_g[combo[0]][:3]:
                for f2 in by_g[combo[1]][:3]:
                    for f3 in by_g[combo[2]][:3]:
                        for f4 in by_g[combo[3]][:3]:
                            quads.append((f1, f2, f3, f4))
    rng.shuffle(quads)
    quads = quads[:INTER_4_CAP]

    scored = []
    # evaluate subset for speed
    for feats in pairs[:400] + triples[:300] + quads[:150]:
        # try both direction patterns on first feature from separation
        dirs = tuple(1 for _ in feats)
        eff = _cond_effect(rows, feats, dirs)
        if (eff.get("n") or 0) < 20:
            continue
        # incremental: compare to first feature alone
        base = _cond_effect(rows, (feats[0],), (1,))
        scored.append({
            "features": list(feats),
            "n": eff["n"],
            "winner_rate": eff["winner_rate"],
            "base_winner_rate": base.get("winner_rate"),
            "incremental": (eff["winner_rate"] or 0) - (base.get("winner_rate") or 0),
            "complexity": len(feats),
            "groups": sorted({feature_group(f) for f in feats}),
        })
    scored.sort(key=lambda r: (-(r["incremental"] or 0), - (r["winner_rate"] or 0), r["complexity"]))
    return {
        "n_2": len(pairs),
        "n_3": len(triples),
        "n_4": len(quads),
        "evaluated": len(scored),
        "top": scored[:100],
    }
