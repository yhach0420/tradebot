"""EXIT manifest freeze + SHA."""
from __future__ import annotations

import json
from typing import Any

from research.e1_x6_provisional.util import sha256_obj

from . import MANIFEST_ID, EXIT_PARAMETER_SOURCE, TOUCH_EPS, EVENT_PRIORITY


def build_manifest(
    *,
    exit_rows: list[dict[str, Any]],
    routing_rows: list[dict[str, Any]],
    candidate_registry_sha: str,
    x25_handoff_sha: str,
    path_sha: str,
    grids: dict[str, Any],
    pbv2: dict[str, Any],
) -> dict[str, Any]:
    body = {
        "manifest_id": MANIFEST_ID,
        "exit_parameter_source": EXIT_PARAMETER_SOURCE,
        "TOUCH_EPS": TOUCH_EPS,
        "event_priority": list(EVENT_PRIORITY),
        "parameter_grids": grids,
        "exits": exit_rows,
        "routing": [
            {
                "candidate_id": r["candidate_id"],
                "decision_mask_sha256": r["decision_mask_sha256"],
                "primary_path_family": r["primary_path_family"],
                "secondary_path_family": r.get("secondary_path_family"),
            }
            for r in routing_rows
        ],
        "candidate_registry_sha": candidate_registry_sha,
        "x25_handoff_sha": x25_handoff_sha,
        "path_sha": path_sha,
        "pbv2_control": pbv2,
        "evaluation_used_for_parameters": False,
        "stress_used_for_parameters": False,
        "consumed_20260804_used_for_parameters": False,
    }
    sha = sha256_obj(body)
    body["manifest_sha256"] = sha
    return body


def x27_handoff(
    *,
    routing_rows: list[dict[str, Any]],
    family_exits: dict[str, list[str]],
    common_exit_ids: list[str],
) -> dict[str, Any]:
    """
    Per ENTRY mask: common controls + primary family EXITs (<=2) + secondary (<=2).
    """
    pairs = []
    for r in routing_rows:
        exits = list(common_exit_ids)
        prim = r["primary_path_family"]
        if prim != "NO_CLEAR_PATH_EDGE":
            exits.extend(family_exits.get(prim, [])[:2])
        sec = r.get("secondary_path_family")
        if sec:
            exits.extend(family_exits.get(sec, [])[:2])
        # dedupe preserve order
        seen = set()
        uniq = []
        for e in exits:
            if e not in seen:
                seen.add(e)
                uniq.append(e)
        pairs.append({
            "candidate_id": r["candidate_id"],
            "decision_mask_sha256": r["decision_mask_sha256"],
            "primary_path_family": prim,
            "secondary_path_family": sec,
            "exit_ids": uniq,
            "n_exits": len(uniq),
        })
    return {
        "unique_masks": len(pairs),
        "routed_entry_exit_pair_count": int(sum(p["n_exits"] for p in pairs)),
        "max_family_exits_per_mask": 4,
        "note": "X27 must not change EXIT parameters; ask/bid evaluation deferred to X28",
        "pairs_sample": pairs[:20],
        "pairs": pairs,
    }
