#!/usr/bin/env python3
"""Export frozen Phase545/545B/545C centroids for runtime entry cluster guard."""

from __future__ import annotations

import csv
import json
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KABU = Path(__file__).resolve().parents[1]


def _bootstrap() -> None:
    for p in (KABU / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _load_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def _float(v) -> float:
    if v in (None, ""):
        return 0.0
    if isinstance(v, bool):
        return float(v)
    if str(v).lower() in ("true", "false"):
        return 1.0 if str(v).lower() == "true" else 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _centroid(rows, feats: list[str]) -> list[float]:
    if not rows:
        return [0.0] * len(feats)
    return [round(statistics.median([_float(r.get(f)) for r in rows]), 6) for f in feats]


def main() -> int:
    _bootstrap()
    from research.phase545_entry_pattern_clustering import CLUSTER_FEATURES
    from research.phase545b_recursive_cluster_refinement import REFINEMENT_FEATURES
    from research.phase545c_feature_engineering_hidden_loss_cluster import ENGINEERED_FEATURES
    from research.phase547_reject_cluster_winner_rescue import _enrich_trades
    from research.structural_trade_normalize import resolve_reports_dir

    reports = resolve_reports_dir(KABU)
    trades = _enrich_trades(reports)
    cluster_feats = list(CLUSTER_FEATURES) + ["minutes_from_open"]
    global_med = _centroid(trades, cluster_feats)

    cluster_centroids: dict[str, list[float]] = {}
    for cid in range(6):
        items = [t for t in trades if int(t.get("cluster_id") or -1) == cid]
        cluster_centroids[str(cid)] = _centroid(items, cluster_feats) if items else list(global_med)

    sub_feats = list(REFINEMENT_FEATURES)
    sub_centroids: dict[str, list[float]] = {}
    c3 = [t for t in trades if int(t.get("cluster_id") or -1) == 3]
    for sid in (0, 1):
        items = [t for t in c3 if int(t.get("subcluster_id") or -1) == sid]
        sub_centroids[str(sid)] = _centroid(items, sub_feats) if items else [0.0] * len(sub_feats)

    csub_feats = list(ENGINEERED_FEATURES)
    csub_centroids: dict[str, list[float]] = {}
    sub1 = [t for t in c3 if int(t.get("subcluster_id") or -1) == 1]
    for sid in range(8):
        items = [t for t in sub1 if int(t.get("new_subcluster_id") or -1) == sid]
        if items:
            csub_centroids[str(sid)] = _centroid(items, csub_feats)

    out = {
        "version": 2,
        "cluster_features": cluster_feats,
        "global_feature_medians": dict(zip(cluster_feats, global_med)),
        "cluster_centroids": cluster_centroids,
        "subcluster_features": sub_feats,
        "subcluster_centroids": sub_centroids,
        "csub_features": csub_feats,
        "csub_centroids": csub_centroids,
        "reject_clusters": [5],
        "reject_csubs": [0, 2, 3, 5],
        "liquidity_burst_threshold_default": 0.052267,
    }
    dest = KABU / "configs" / "entry_cluster_guard_model.json"
    dest.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {dest} clusters={len(cluster_centroids)} csub={len(csub_centroids)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
