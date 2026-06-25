"""
Runtime entry cluster classification (Phase545/545B/545C frozen centroids).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


def _float(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    if isinstance(v, bool):
        return float(v)
    if str(v).lower() in ("true", "false"):
        return 1.0 if str(v).lower() == "true" else 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dist(a: Sequence[float], b: Sequence[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b))


def compute_liquidity_burst(trade: Mapping[str, Any]) -> float:
    buf = _float(trade.get("board_update_frequency"))
    if buf is None:
        uc = _float(trade.get("update_count_before_entry"))
        mins = _float(trade.get("minutes_from_open"))
        if uc is not None and mins is not None and mins > 0:
            buf = uc / mins
    rel = _float(trade.get("relative_volume"))
    if rel is None:
        rel = _float(trade.get("volume_ratio"))
    if rel is None:
        rel = 1.0
    return round((buf or 0.0) * rel, 6)


def compute_entry_cluster_feature_fields(trade: Mapping[str, Any]) -> dict[str, Any]:
    """Attach cluster-classifier inputs from trade / shadow fields."""
    out: dict[str, Any] = {}
    if trade.get("entry_vwap_dev_pct") is not None and trade.get("vwap_distance_pct") in (None, ""):
        out["vwap_distance_pct"] = trade.get("entry_vwap_dev_pct")
    if trade.get("entry_near_day_high_pct") is not None and trade.get("day_high_distance_pct") in (None, ""):
        out["day_high_distance_pct"] = trade.get("entry_near_day_high_pct")
    if trade.get("entry_momentum_score") is not None and trade.get("momentum_score") in (None, ""):
        out["momentum_score"] = trade.get("entry_momentum_score")
    elif trade.get("momentum_continuation_score") is not None and trade.get("momentum_score") in (None, ""):
        out["momentum_score"] = trade.get("momentum_continuation_score")
    if trade.get("entry_expectancy_score_v2") is not None and trade.get("entry_score_v2") in (None, ""):
        out["entry_score_v2"] = trade.get("entry_expectancy_score_v2")
    out["liquidity_burst"] = compute_liquidity_burst({**trade, **out})
    out["relative_volume"] = _float(trade.get("relative_volume")) or _float(trade.get("volume_ratio")) or 1.0
    return out


@dataclass(frozen=True)
class EntryClusterModel:
    cluster_features: tuple[str, ...]
    global_medians: dict[str, float]
    cluster_centroids: dict[int, list[float]]
    subcluster_features: tuple[str, ...]
    subcluster_centroids: dict[int, list[float]]
    csub_features: tuple[str, ...]
    csub_centroids: dict[int, list[float]]
    reject_clusters: frozenset[int]
    reject_csubs: frozenset[int]

    @classmethod
    def load(cls, path: Path) -> EntryClusterModel:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            cluster_features=tuple(raw["cluster_features"]),
            global_medians={k: float(v) for k, v in (raw.get("global_feature_medians") or {}).items()},
            cluster_centroids={int(k): list(v) for k, v in (raw.get("cluster_centroids") or {}).items()},
            subcluster_features=tuple(raw.get("subcluster_features") or ()),
            subcluster_centroids={int(k): list(v) for k, v in (raw.get("subcluster_centroids") or {}).items()},
            csub_features=tuple(raw.get("csub_features") or ()),
            csub_centroids={int(k): list(v) for k, v in (raw.get("csub_centroids") or {}).items()},
            reject_clusters=frozenset(int(x) for x in (raw.get("reject_clusters") or [5])),
            reject_csubs=frozenset(int(x) for x in (raw.get("reject_csubs") or [0, 2, 3, 5])),
        )

    def _vec(self, trade: Mapping[str, Any], feats: Sequence[str]) -> list[float]:
        out: list[float] = []
        for f in feats:
            v = _float(trade.get(f))
            if v is None or math.isnan(v) or math.isinf(v):
                v = float(self.global_medians.get(f, 0.0))
            out.append(v)
        return out

    def classify(self, trade: Mapping[str, Any]) -> dict[str, Any]:
        merged = {**trade, **compute_entry_cluster_feature_fields(trade)}
        cvec = self._vec(merged, self.cluster_features)
        cluster_id = min(
            self.cluster_centroids.keys(),
            key=lambda cid: _dist(cvec, self.cluster_centroids[cid]),
        )
        subcluster_id = -1
        new_subcluster_id = -1
        if cluster_id == 3 and self.subcluster_centroids:
            svec = self._vec(merged, self.subcluster_features)
            subcluster_id = min(
                self.subcluster_centroids.keys(),
                key=lambda sid: _dist(svec, self.subcluster_centroids[sid]),
            )
            if subcluster_id == 1 and self.csub_centroids:
                eng = {**merged}
                for f in self.csub_features:
                    if eng.get(f) in (None, ""):
                        eng[f] = 0.0
                csub_vec = [float(_float(eng.get(f)) or 0.0) for f in self.csub_features]
                new_subcluster_id = min(
                    self.csub_centroids.keys(),
                    key=lambda sid: _dist(csub_vec, self.csub_centroids[sid]),
                )
        reject = cluster_id in self.reject_clusters or new_subcluster_id in self.reject_csubs
        return {
            "cluster_id": cluster_id,
            "subcluster_id": subcluster_id,
            "new_subcluster_id": new_subcluster_id,
            "liquidity_burst": merged.get("liquidity_burst"),
            "cluster_reject_candidate": reject,
        }


def default_model_path(*, repo_root: Path) -> Path:
    return repo_root / "configs" / "entry_cluster_guard_model.json"


def load_default_model(*, repo_root: Path) -> EntryClusterModel:
    return EntryClusterModel.load(default_model_path(repo_root=repo_root))
