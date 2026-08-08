"""V1 issue audit (recomputed; not hardcoded)."""
from __future__ import annotations

from typing import Any, Optional

from research.e1_x6_provisional.util import sha256_obj

from . import EVENT_PRIORITY, TOUCH_EPS


def locked_profit_bps(activation: Optional[float], giveback: Optional[float]) -> Optional[float]:
    if activation is None or giveback is None:
        return None
    return float(activation) - float(giveback)


def semantic_exit_key(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "stop_bps": p.get("stop_bps"),
        "target_bps": p.get("target_bps"),
        "trail_activation_bps": p.get("trail_activation_bps"),
        "giveback_bps": p.get("giveback_bps"),
        "giveback_mode": p.get("giveback_mode"),
        "no_progress_sec": p.get("no_progress_sec"),
        "no_progress_mfe_bps": p.get("no_progress_mfe_bps", 5.0 if p.get("no_progress_sec") else None),
        "no_progress_abs_ret_bps": p.get("no_progress_abs_ret_bps", 5.0 if p.get("no_progress_sec") else None),
        "max_hold_sec": p.get("max_hold_sec"),
        "event_priority": list(EVENT_PRIORITY),
        "TOUCH_EPS": TOUCH_EPS,
    }


def semantic_exit_sha(p: dict[str, Any]) -> str:
    return sha256_obj(semantic_exit_key(p))


def audit_v1(
    *,
    exit_params: dict[str, dict[str, Any]],
    primary_counts: dict[str, int],
    secondary_counts: dict[str, int],
    x25_family_tag_counts: dict[str, int],
    cand_calib: dict[str, Any],
    anch_calib: dict[str, Any],
) -> dict[str, Any]:
    locked_rows = []
    for eid, p in exit_params.items():
        act = p.get("trail_activation_bps")
        gb = p.get("giveback_bps")
        if act is None and gb is None:
            continue
        lp = locked_profit_bps(act, gb)
        locked_rows.append({
            "exit_id": eid,
            "trail_activation_bps": act,
            "giveback_bps": gb,
            "locked_profit_at_activation_bps": lp,
            "negative_locked_profit": lp is not None and lp < 0,
        })

    # semantic duplicates
    by_sha: dict[str, list[str]] = {}
    for eid, p in exit_params.items():
        full = {
            **p,
            "giveback_mode": "from_MFE" if p.get("giveback_bps") is not None else None,
            "no_progress_mfe_bps": 5.0 if p.get("no_progress_sec") else None,
            "no_progress_abs_ret_bps": 5.0 if p.get("no_progress_sec") else None,
        }
        sha = semantic_exit_sha(full)
        by_sha.setdefault(sha, []).append(eid)
    dups = {sha: ids for sha, ids in by_sha.items() if len(ids) > 1}

    # routing mismatch
    routing_audit = []
    for fam in ("QUICK_MOVE", "PULLBACK_THEN_RISE", "CONTINUATION", "DELAYED_MOVE", "SPIKE_AND_GIVEBACK", "NO_CLEAR_PATH_EDGE"):
        routing_audit.append({
            "family": fam,
            "x25_tag_count": x25_family_tag_counts.get(fam, 0),
            "v1_primary_count": primary_counts.get(fam, 0),
            "v1_secondary_count": secondary_counts.get(fam, 0),
            "note": "V1 used cross-family raw margin score ranking for primary/secondary exclusion",
        })

    # stop ceiling: CONTINUATION ROOM
    cont_aw = (anch_calib.get("CONTINUATION") or {})
    raw_stop = cont_aw.get("pre60_abs_q75")
    v1_stop = (exit_params.get("EXIT_CONTINUATION_ROOM_V1") or {}).get("stop_bps")
    stop_ceiling = {
        "exit_id": "EXIT_CONTINUATION_ROOM_V1",
        "anchor_weighted_stop_raw_bps": raw_stop,
        "v1_stop_bps": v1_stop,
        "rounded_below_required": (
            raw_stop is not None and v1_stop is not None and float(v1_stop) + 1e-12 < float(raw_stop)
        ),
        "v1_stop_grid_max": 100,
    }

    return {
        "negative_locked_profit": locked_rows,
        "negative_locked_count": sum(1 for r in locked_rows if r["negative_locked_profit"]),
        "semantic_duplicates": [{"semantic_sha": k, "exit_ids": v} for k, v in dups.items()],
        "semantic_duplicate_groups": len(dups),
        "routing_scale_mismatch": routing_audit,
        "stop_grid_ceiling": stop_ceiling,
        "cross_family_raw_score_ranking_used_in_v1": True,
    }
