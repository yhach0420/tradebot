#!/usr/bin/env python3
"""
Phase225: Entry interaction discovery review (review only).

Discover 2-way and 3-way feature interactions that best explain stop_hit.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase225_entry_interaction_discovery_review.json"

MIN_CLUSTER_N = 20

FEATURES: tuple[tuple[str, str], ...] = (
    ("TV", "trading_value"),
    ("VWAP", "entry_vwap_dev_pct"),
    ("Board", "entry_order_book_imbalance"),
    ("Duration", "max_continuation_duration"),
    ("Momentum", "momentum_continuation_score"),
    ("TickRatio", "tick_ratio_pct"),
    ("Rise5m", "entry_rise_5min_pct"),
    ("RollingMFE", "rolling_mfe_pct"),
)


def _load_module(name: str, rel_path: str) -> Any:
    path = REPO / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    sys.path[:0] = [str(REPO), str(REPO / "kabu_native" / "src")]
    spec.loader.exec_module(mod)
    return mod


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _quantile(xs: list[float], q: float) -> float:
    ys = sorted(xs)
    pos = (len(ys) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ys[lo]
    return ys[lo] + (ys[hi] - ys[lo]) * (pos - lo)


def _pf(pnls: list[float]) -> float | None:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "n": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "avg_pnl_pct": None,
            "stop_hit_count": 0,
            "stop_hit_rate": None,
        }
    pnls = [float(r.get("pnl_pct") or 0) for r in rows]
    n = len(rows)
    stops = sum(1 for r in rows if r.get("stop_hit"))
    pf = _pf(pnls)
    return {
        "n": n,
        "profit_factor": pf if pf != float("inf") else pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / n, 4),
        "stop_hit_count": stops,
        "stop_hit_rate": round(stops / n, 4),
    }


def _reject_cluster_impact(cluster: list[dict[str, Any]], rest: list[dict[str, Any]]) -> dict[str, Any]:
    """Impact of rejecting cluster (keeping rest): winners/losers removed from cluster."""
    win_cl = [r for r in cluster if float(r.get("pnl_pct") or 0) > 0]
    lose_cl = [r for r in cluster if float(r.get("pnl_pct") or 0) < 0]
    stop_cl = [r for r in cluster if r.get("stop_hit")]
    return {
        "winner_missed_count": len(win_cl),
        "winner_missed_pnl_pct": round(sum(float(r.get("pnl_pct") or 0) for r in win_cl), 4),
        "loser_avoided_count": len(lose_cl),
        "loser_avoided_pnl_pct": round(sum(float(r.get("pnl_pct") or 0) for r in lose_cl), 4),
        "stop_avoided_count": len(stop_cl),
        "net_rejected_pnl_pct": round(sum(float(r.get("pnl_pct") or 0) for r in cluster), 4),
    }


def _build_tertiles(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    cuts: dict[str, dict[str, Any]] = {}
    for label, field in FEATURES:
        vals = [_float(r.get(field)) for r in rows]
        vals = [v for v in vals if v is not None]
        if len(vals) < 9:
            cuts[label] = {"field": field, "coverage_n": len(vals), "usable": False}
            continue
        q33 = _quantile(vals, 1.0 / 3.0)
        q66 = _quantile(vals, 2.0 / 3.0)
        cuts[label] = {
            "field": field,
            "coverage_n": len(vals),
            "coverage_pct": round(100.0 * len(vals) / len(rows), 2),
            "usable": True,
            "p33": round(q33, 6),
            "p66": round(q66, 6),
        }
    return cuts


def _bin_label(val: float, q33: float, q66: float) -> str:
    if val <= q33:
        return "low"
    if val <= q66:
        return "mid"
    return "high"


def _assign_bins(rows: list[dict[str, Any]], cuts: dict[str, dict[str, Any]]) -> None:
    for r in rows:
        bins: dict[str, str] = {}
        for label, info in cuts.items():
            if not info.get("usable"):
                continue
            v = _float(r.get(info["field"]))
            if v is None:
                continue
            bins[label] = _bin_label(v, info["p33"], info["p66"])
        r["_bins"] = bins


def _cluster_key(r: dict[str, Any], feat_labels: tuple[str, ...]) -> Optional[tuple[str, ...]]:
    bins = r.get("_bins") or {}
    if not all(lbl in bins for lbl in feat_labels):
        return None
    return tuple(f"{lbl}:{bins[lbl]}" for lbl in feat_labels)


def _cluster_label(key: tuple[str, ...]) -> str:
    return " & ".join(key)


def _analyze_combo(
    rows: list[dict[str, Any]],
    feat_labels: tuple[str, ...],
    baseline_stop: float,
) -> list[dict[str, Any]]:
    eligible = [r for r in rows if _cluster_key(r, feat_labels) is not None]
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for r in eligible:
        key = _cluster_key(r, feat_labels)
        assert key is not None
        groups[key].append(r)

    out: list[dict[str, Any]] = []
    for key, members in groups.items():
        m = _metrics(members)
        rest = [r for r in rows if r not in members]
        impact = _reject_cluster_impact(members, rest)
        stop_rate = m["stop_hit_rate"] or 0.0
        out.append(
            {
                "cluster": _cluster_label(key),
                "features": list(feat_labels),
                "bins": {p.split(":")[0]: p.split(":")[1] for p in key},
                "eligible_universe_n": len(eligible),
                **m,
                "stop_lift_vs_baseline": round(stop_rate - baseline_stop, 4),
                "if_cluster_rejected": impact,
            }
        )
    return out


def _rank(clusters: list[dict[str, Any]], min_n: int = MIN_CLUSTER_N) -> dict[str, list[dict[str, Any]]]:
    ok = [c for c in clusters if c["n"] >= min_n]

    def slim(c: dict[str, Any], rank_key: str, rank_val: float) -> dict[str, Any]:
        return {
            "rank_key": rank_key,
            "rank_value": rank_val,
            "cluster": c["cluster"],
            "features": c["features"],
            "n": c["n"],
            "profit_factor": c["profit_factor"],
            "total_pnl_pct": c["total_pnl_pct"],
            "stop_hit_rate": c["stop_hit_rate"],
            "stop_lift_vs_baseline": c["stop_lift_vs_baseline"],
            "if_cluster_rejected": {
                "winner_missed_count": c["if_cluster_rejected"]["winner_missed_count"],
                "loser_avoided_count": c["if_cluster_rejected"]["loser_avoided_count"],
                "stop_avoided_count": c["if_cluster_rejected"]["stop_avoided_count"],
            },
        }

    by_stop_rate = sorted(ok, key=lambda c: (c["stop_hit_rate"] or 0, c["n"]), reverse=True)
    by_stop_lift = sorted(ok, key=lambda c: (c["stop_lift_vs_baseline"], c["n"]), reverse=True)
    by_stop_count = sorted(ok, key=lambda c: (c["stop_hit_count"], c["stop_hit_rate"] or 0), reverse=True)
    by_low_stop = sorted(ok, key=lambda c: (c["stop_hit_rate"] or 0, -c["n"]))

    return {
        "by_stop_hit_rate_desc": [
            slim(c, "stop_hit_rate", c["stop_hit_rate"] or 0) for c in by_stop_rate[:25]
        ],
        "by_stop_lift_desc": [
            slim(c, "stop_lift_vs_baseline", c["stop_lift_vs_baseline"]) for c in by_stop_lift[:25]
        ],
        "by_stop_hit_count_desc": [
            slim(c, "stop_hit_count", float(c["stop_hit_count"])) for c in by_stop_count[:25]
        ],
        "by_stop_hit_rate_asc_protective": [
            slim(c, "stop_hit_rate", c["stop_hit_rate"] or 0) for c in by_low_stop[:15]
        ],
    }


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p217 = _load_module("phase217_loader_p225", "kabu_native/scripts/run_phase217_stop_hit_root_cause_review.py")
    p221 = _load_module("phase221_loader_p225", "kabu_native/scripts/run_phase221_early_momentum_discovery_review.py")
    mod = p217._load_phase213c_module()

    print("loading trades...", flush=True)
    rows = p217._build_all(mod)
    print("augmenting features...", flush=True)
    p221._augment_early_features(mod, rows)

    baseline = _metrics(rows)
    baseline_stop = baseline["stop_hit_rate"] or 0.0
    cuts = _build_tertiles(rows)
    _assign_bins(rows, cuts)

    feat_labels = [lbl for lbl, _ in FEATURES]
    two_way: dict[str, list[dict[str, Any]]] = {}
    three_way: dict[str, list[dict[str, Any]]] = {}

    print("scanning 2-way interactions...", flush=True)
    for combo in itertools.combinations(feat_labels, 2):
        if not all(cuts[f].get("usable") for f in combo):
            continue
        key = "+".join(combo)
        two_way[key] = _analyze_combo(rows, combo, baseline_stop)

    print("scanning 3-way interactions...", flush=True)
    for combo in itertools.combinations(feat_labels, 3):
        if not all(cuts[f].get("usable") for f in combo):
            continue
        key = "+".join(combo)
        three_way[key] = _analyze_combo(rows, combo, baseline_stop)

    all_2 = [c for clusters in two_way.values() for c in clusters]
    all_3 = [c for clusters in three_way.values() for c in clusters]

    rankings = {
        "two_way": _rank(all_2),
        "three_way": _rank(all_3),
        "combined_top_stop_risk": _rank(all_2 + all_3)["by_stop_lift_desc"][:30],
    }

    # Feature-pair presence in top stop-lift clusters
    top_lift = rankings["combined_top_stop_risk"][:15]
    pair_counts: dict[str, int] = defaultdict(int)
    for item in top_lift:
        feats = item["features"]
        for pair in itertools.combinations(sorted(feats), 2):
            pair_counts["+".join(pair)] += 1

    report = {
        "phase": 225,
        "mode": "entry_interaction_discovery_review",
        "constraints": {
            "review_only": True,
            "hard_reject_forbidden": True,
            "entry_change_forbidden": True,
            "production_yaml_changes_forbidden": True,
        },
        "goal": "Discover 2-way and 3-way entry feature interactions that explain stop_hit.",
        "population": {
            "total_trades": len(rows),
            "baseline": baseline,
            "baseline_stop_hit_rate": baseline_stop,
        },
        "features": {
            lbl: cuts[lbl]
            for lbl, _ in FEATURES
        },
        "method": {
            "binning": "tertile (low/mid/high) per feature on non-null values",
            "interactions": "all usable 2-way and 3-way crosses",
            "min_cluster_n_for_ranking": MIN_CLUSTER_N,
            "cluster_metrics": "n, PF, PnL, stop_rate on cluster members",
            "gate_impact": "if_cluster_rejected = impact of excluding cluster from universe",
        },
        "two_way_interactions": {
            k: {"combo": k, "cluster_count": len(v), "clusters": v}
            for k, v in sorted(two_way.items())
        },
        "three_way_interactions": {
            k: {"combo": k, "cluster_count": len(v), "clusters": v}
            for k, v in sorted(three_way.items())
        },
        "rankings": rankings,
        "top_lift_feature_pair_frequency": dict(
            sorted(pair_counts.items(), key=lambda x: x[1], reverse=True)
        ),
        "notes": [
            "Review only — clusters are descriptive, not production gates.",
            "Rise5m tertile coverage limited (~9%) — combos including Rise5m use smaller eligible universe.",
            "stop_lift = cluster stop_rate minus baseline stop_rate (4.23%).",
            "High stop_lift clusters = entry profiles enriched for stop_hit.",
        ],
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    top = rankings["combined_top_stop_risk"][0] if rankings["combined_top_stop_risk"] else {}
    print(
        f"wrote {OUT} n={len(rows)} top_lift={top.get('cluster')} "
        f"stop={top.get('stop_hit_rate')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
