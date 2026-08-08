"""Family baseline freeze (pre-Evaluation; no PnL selection)."""
from __future__ import annotations

from typing import Any, Optional

from research.e1_x6_provisional.util import sha256_obj

from . import (
    CONTROL_BY_HORIZON,
    FAMILY_PROTECT,
    FAMILY_QUICK_FAST,
    FAMILY_QUICK_TRAIL,
    FAMILY_SPIKE,
    TIE_PRIORITY,
)


def _family_exit_for_tag(tag: str, horizon_sec: int) -> Optional[str]:
    if tag in FAMILY_PROTECT:
        return FAMILY_PROTECT[tag]
    if tag == "SPIKE_AND_GIVEBACK":
        return FAMILY_SPIKE
    if tag == "QUICK_MOVE":
        if int(horizon_sec) <= 300:
            return FAMILY_QUICK_FAST
        return FAMILY_QUICK_TRAIL
    return None


def _control_for_horizon(horizon_sec: int) -> str:
    h = int(horizon_sec)
    if h in CONTROL_BY_HORIZON:
        return CONTROL_BY_HORIZON[h]
    if h <= 300:
        return CONTROL_BY_HORIZON[300]
    if h <= 900:
        return CONTROL_BY_HORIZON[900]
    return CONTROL_BY_HORIZON[1800]


def resolve_family_baseline(
    *,
    tags: list[str],
    candidate_horizon_sec: int,
    x26a_exits: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """
    Section 7 mechanical rule — Discovery tags + candidate horizon only.
    No Evaluation PnL.
    """
    tags = [t for t in (tags or []) if t in set(TIE_PRIORITY)]
    candidates: list[tuple[str, str, float]] = []  # tag, exit_id, |max_hold - horizon|
    for t in tags:
        eid = _family_exit_for_tag(t, candidate_horizon_sec)
        if eid is None:
            continue
        mh = float((x26a_exits.get(eid) or {}).get("max_hold_sec") or candidate_horizon_sec)
        candidates.append((t, eid, abs(mh - float(candidate_horizon_sec))))

    if not candidates:
        cid = _control_for_horizon(candidate_horizon_sec)
        return {
            "primary_family_baseline_exit_id": cid,
            "family_baseline_reason": f"no_family_tag_{cid}",
            "family_baseline_source": "COMMON_CONTROL",
            "resolved_from_tags": [],
        }

    if len(candidates) == 1:
        t, eid, _ = candidates[0]
        return {
            "primary_family_baseline_exit_id": eid,
            "family_baseline_reason": f"single_family_{t}",
            "family_baseline_source": "FAMILY",
            "resolved_from_tags": [t],
        }

    # closest max_hold to candidate_horizon; tie → TIE_PRIORITY order
    min_diff = min(c[2] for c in candidates)
    tied = [c for c in candidates if abs(c[2] - min_diff) < 1e-9]
    if len(tied) == 1:
        t, eid, _ = tied[0]
        return {
            "primary_family_baseline_exit_id": eid,
            "family_baseline_reason": f"multi_closest_hold_{t}",
            "family_baseline_source": "FAMILY",
            "resolved_from_tags": [c[0] for c in candidates],
        }
    best = None
    best_rank = 999
    for t, eid, _ in tied:
        try:
            rank = TIE_PRIORITY.index(t)
        except ValueError:
            rank = 999
        if rank < best_rank:
            best_rank = rank
            best = (t, eid)
    assert best is not None
    t, eid = best
    return {
        "primary_family_baseline_exit_id": eid,
        "family_baseline_reason": f"multi_tiebreak_{t}",
        "family_baseline_source": "FAMILY",
        "resolved_from_tags": [c[0] for c in candidates],
    }


def freeze_family_baselines(
    assignments: list[dict[str, Any]],
    x26a_exits: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    import json
    rows = []
    for a in assignments:
        tags = a.get("discovery_family_tags") or []
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except Exception:
                tags = []
        horizon = int(a.get("candidate_horizon_sec") or 300)
        fb = resolve_family_baseline(tags=list(tags), candidate_horizon_sec=horizon, x26a_exits=x26a_exits)
        rows.append({
            "candidate_id": a["candidate_id"],
            "decision_mask_sha": a.get("decision_mask_sha256"),
            "candidate_horizon": horizon,
            "X25_path_tags": tags,
            "primary_family_baseline_exit_id": fb["primary_family_baseline_exit_id"],
            "family_baseline_reason": fb["family_baseline_reason"],
            "family_baseline_source": fb["family_baseline_source"],
            "resolved_from_tags": fb["resolved_from_tags"],
            # freeze metadata — no PnL fields
            "pnl_used_for_selection": False,
        })
    sha = sha256_obj([
        {"cid": r["candidate_id"], "fb": r["primary_family_baseline_exit_id"], "reason": r["family_baseline_reason"]}
        for r in rows
    ])
    return rows, sha
