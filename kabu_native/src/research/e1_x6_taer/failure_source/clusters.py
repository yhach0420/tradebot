"""Overlap clusters and episode loading for FSA V2."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from research.e1_x6_taer.exit_joint_audit import PRIOR_STORE, load_entry_observations

from .precommit import MAX_HOLD_SEC


def _session_from_epoch(t: float) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ts = datetime.fromtimestamp(t, tz=ZoneInfo("Asia/Tokyo"))
    return "AM" if ts.hour < 12 else "PM"


def load_episodes() -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, dict]]:
    rows, meta = load_entry_observations()
    # Enrich with session + anchor join
    anchors: dict[str, dict] = {}
    ap = PRIOR_STORE / "anchors.jsonl"
    need = {r["episode_id"] for r in rows}
    with ap.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            d = json.loads(line)
            eid = d.get("episode_id")
            if eid in need:
                anchors[eid] = d

    out = []
    for r in rows:
        eid = r["episode_id"]
        a = anchors.get(eid) or {}
        session = a.get("session") or _session_from_epoch(float(r["entry_t"]))
        out.append({
            **r,
            "session": session,
            "anchor": a.get("anchor") or {},
            "setup_detail": a.get("setup") or {},
            "exhaustion": a.get("exhaustion") or {},
            "dynamic": a.get("dynamic") or {},
            "features_snapshot_anchor": a.get("features_snapshot") or {},
        })
    meta["anchors_joined"] = len(anchors)
    meta["anchors_missing"] = sorted(need - set(anchors))
    return out, meta, anchors


class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))
        self.r = [0] * n

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.r[ra] < self.r[rb]:
            self.p[ra] = rb
        elif self.r[ra] > self.r[rb]:
            self.p[rb] = ra
        else:
            self.p[rb] = ra
            self.r[ra] += 1


def build_overlap_clusters(episodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Assign overlap_cluster_id; primary method = CLUSTER_FIRST_EPISODE."""
    # group by day|session|symbol
    groups: dict[tuple, list[int]] = defaultdict(list)
    for i, e in enumerate(episodes):
        key = (e["day"], e["session"], e["symbol"])
        groups[key].append(i)

    cluster_root: dict[int, str] = {}
    cluster_members: dict[str, list[int]] = defaultdict(list)
    cid_counter = 0

    for key, idxs in sorted(groups.items()):
        idxs_sorted = sorted(idxs, key=lambda i: (float(episodes[i]["entry_t"]), episodes[i]["episode_id"]))
        n = len(idxs_sorted)
        uf = _UF(n)
        # intervals [entry, entry+300]
        intervals = [(float(episodes[idxs_sorted[j]]["entry_t"]),
                      float(episodes[idxs_sorted[j]]["entry_t"]) + MAX_HOLD_SEC) for j in range(n)]
        for j in range(n):
            for k in range(j + 1, n):
                if intervals[k][0] > intervals[j][1] + 1e-12:
                    break  # sorted by start; no further overlaps with j
                # overlap if start_k < end_j
                if intervals[k][0] <= intervals[j][1] + 1e-12:
                    uf.union(j, k)
        roots: dict[int, list[int]] = defaultdict(list)
        for j in range(n):
            roots[uf.find(j)].append(idxs_sorted[j])
        for members in roots.values():
            cid_counter += 1
            cid = f"OC|{key[0]}|{key[1]}|{key[2]}|{cid_counter:05d}"
            for mi in members:
                cluster_root[mi] = cid
            cluster_members[cid] = sorted(members, key=lambda i: (float(episodes[i]["entry_t"]), episodes[i]["episode_id"]))

    enriched = []
    for i, e in enumerate(episodes):
        cid = cluster_root[i]
        members = cluster_members[cid]
        rep_i = members[0]
        enriched.append({
            **e,
            "overlap_cluster_id": cid,
            "cluster_size": len(members),
            "is_cluster_representative": i == rep_i,
            "cluster_weight": 1.0 / len(members),
        })

    sizes = [len(v) for v in cluster_members.values()]
    by_setup = Counter()
    by_day = Counter()
    by_symbol = Counter()
    for cid, members in cluster_members.items():
        rep = episodes[members[0]]
        by_setup[rep["setup_type"]] += 1
        by_day[rep["day"]] += 1
        by_symbol[rep["symbol"]] += 1

    summary = {
        "raw_episode_n": len(episodes),
        "overlap_cluster_n": len(cluster_members),
        "cluster_size_hist": dict(Counter(sizes)),
        "max_cluster_size": max(sizes) if sizes else 0,
        "setup_cluster_n": dict(by_setup),
        "day_cluster_n": dict(by_day),
        "symbol_cluster_n": dict(sorted(by_symbol.items(), key=lambda x: -x[1])[:30]),
        "primary_weighting": "CLUSTER_FIRST_EPISODE",
        # sum of 1/cluster_size over raw episodes == cluster_n
        "cluster_weight_sum": sum(e["cluster_weight"] for e in enriched),
    }
    assert abs(summary["cluster_weight_sum"] - summary["overlap_cluster_n"]) < 1e-9
    return enriched, summary
