"""All-tag routing (no cross-family score exclusion) + activation support."""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

import numpy as np

from . import DISCOVERY, FAMILY_TAGS


def all_tag_routing(
    handoff_rows: list[dict[str, Any]],
    *,
    descriptive_primary: Optional[dict[str, str]] = None,
    descriptive_secondary: Optional[dict[str, Optional[str]]] = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """
    Route every Discovery tag to its EXIT family. NO_CLEAR → controls only.
    """
    out = []
    tag_route_counts: Counter = Counter()
    for h in handoff_rows:
        cid = h["candidate_id"]
        tags = list(h.get("discovery_family_tags") or [])
        if "NO_CLEAR_PATH_EDGE" in tags:
            if len(tags) != 1:
                raise ValueError(f"NO_CLEAR mixed with other tags: {cid} {tags}")
            routed = []
        else:
            routed = [t for t in tags if t in FAMILY_TAGS]
            for t in routed:
                tag_route_counts[t] += 1
        out.append({
            "candidate_id": cid,
            "decision_mask_sha256": h.get("decision_mask_sha256"),
            "all_discovery_tags": tags,
            "descriptive_primary_family": (descriptive_primary or {}).get(cid),
            "descriptive_secondary_family": (descriptive_secondary or {}).get(cid),
            "routed_exit_families": routed,
            "controls_only": len(routed) == 0,
        })
    return out, dict(tag_route_counts)


def activation_support(
    *,
    activation_bps: float,
    member_mask_ids: list[str],
    unique_masks: dict[str, np.ndarray],
    metrics: dict[str, np.ndarray],
    dates: np.ndarray,
    symbols: np.ndarray,
    path_ok: np.ndarray,
) -> dict[str, Any]:
    """
    Among Discovery anchors selected by any family member mask, fraction whose
    session MFE (or 1800s MFE) reaches activation_bps.
    """
    disc = path_ok & np.isin(dates, list(DISCOVERY))
    selected = np.zeros(len(dates), dtype=bool)
    for mid in member_mask_ids:
        selected |= unique_masks[mid]
    pop = disc & selected
    # use MFE_session if fresh, else MFE_1800
    mfe = metrics["MFE_session_bps"].copy()
    bad = ~metrics["fresh_ok_session"] | ~np.isfinite(mfe)
    mfe[bad] = metrics["MFE_1800s_bps"][bad]
    ok = pop & np.isfinite(mfe)
    reached = ok & (mfe >= activation_bps - 1e-12)
    days = int(np.unique(dates[reached]).size) if reached.any() else 0
    syms = int(np.unique(symbols[reached]).size) if reached.any() else 0
    n_reached = int(reached.sum())
    n_elig = int(ok.sum())
    return {
        "activation_bps": activation_bps,
        "eligible_support": n_elig,
        "reached_anchors": n_reached,
        "activation_reach_rate": (n_reached / n_elig) if n_elig else None,
        "activation_reached_days": days,
        "activation_reached_symbols": syms,
        "technical_support_ok": n_reached >= 3 and days >= 2,
    }


def build_x27_routes(
    *,
    routing_rows: list[dict[str, Any]],
    family_to_canonical_ids: dict[str, list[str]],
    common_ids: list[str],
) -> dict[str, Any]:
    """All tagged family canonical EXITs + controls; dedupe per mask."""
    pairs = []
    raw_total = 0
    sem_total = 0
    dist: Counter = Counter()
    for r in routing_rows:
        raw = list(common_ids)
        for fam in r["routed_exit_families"]:
            raw.extend(family_to_canonical_ids.get(fam, []))
        raw_n = len(raw)
        # semantic dedupe preserve order
        seen = set()
        uniq = []
        for e in raw:
            if e not in seen:
                seen.add(e)
                uniq.append(e)
        pairs.append({
            "candidate_id": r["candidate_id"],
            "decision_mask_sha256": r.get("decision_mask_sha256"),
            "all_discovery_tags": r["all_discovery_tags"],
            "routed_exit_families": r["routed_exit_families"],
            "exit_ids": uniq,
            "raw_route_count": raw_n,
            "semantic_route_count": len(uniq),
        })
        raw_total += raw_n
        sem_total += len(uniq)
        dist[len(uniq)] += 1
    return {
        "unique_masks": len(pairs),
        "raw_family_route_count": raw_total,
        "semantic_deduplicated_route_count": sem_total,
        "routes_per_mask_distribution": dict(sorted(dist.items())),
        "pairs": pairs,
    }
