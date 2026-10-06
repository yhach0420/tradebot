"""Load V1 frozen leader/target identities. Do not rerank."""
from __future__ import annotations

import json
from typing import Any

from research.causal_driver_pb1.cross_sectional_precommit_v1_1 import (
    EXPECTED_LEADER_PAIRS,
    EXPECTED_LEADER_SET_SHA256,
    EXPECTED_TARGET_N,
    EXPECTED_TARGET_SET_SHA256,
    SUPERSEDES_PRECOMMIT_SHA256,
)
from research.causal_driver_pb1.cross_sectional_precommit_v1_1.isolation import V1_OUT
from research.causal_driver_pb1.identity.ids import sha256_obj


def load_v1_frozen_identities() -> dict[str, Any]:
    doc = json.loads((V1_OUT / "report.json").read_text(encoding="utf-8"))
    a = doc.get("answers") or {}
    ev = doc.get("evaluation") or {}
    leaders = ev.get("leaders") or {}
    frozen = list(leaders.get("frozen_leaders") or [])
    targets = list(leaders.get("target_symbols") or [])
    pairs = tuple((str(r.get("sector_id")), str(r.get("leader_symbol"))) for r in frozen)
    recorded_leader_sha = a.get("leader_set_sha256") or ev.get("leader_set_sha256")
    recorded_target_sha = a.get("target_set_sha256") or ev.get("target_set_sha256")
    recomputed_leader_sha = sha256_obj(frozen)
    recomputed_target_sha = sha256_obj(targets)
    used = {r["leader_symbol"] for r in frozen}
    disjoint = used.isdisjoint(set(targets))
    blockers: list[str] = []
    if a.get("precommit_sha256") != SUPERSEDES_PRECOMMIT_SHA256:
        blockers.append("V1_PRECOMMIT_SHA_MISMATCH")
    if pairs != EXPECTED_LEADER_PAIRS:
        blockers.append("LEADER_PAIRS_CHANGED")
    if recorded_leader_sha != EXPECTED_LEADER_SET_SHA256:
        blockers.append("LEADER_SET_SHA_CHANGED")
    if recorded_target_sha != EXPECTED_TARGET_SET_SHA256:
        blockers.append("TARGET_SET_SHA_CHANGED")
    if len(targets) != EXPECTED_TARGET_N:
        blockers.append("TARGET_N_CHANGED")
    if not disjoint:
        blockers.append("LEADER_TARGET_NOT_DISJOINT")
    if a.get("LEADER_LAGGARD_OUTCOMES_OPENED") is True:
        blockers.append("V1_OUTCOMES_WERE_OPENED")
    return {
        "pass": not blockers,
        "blockers": blockers,
        "frozen_leaders": frozen,
        "leader_symbols": [r["leader_symbol"] for r in frozen],
        "leader_set_sha256": EXPECTED_LEADER_SET_SHA256,
        "leader_sha_recomputed": recomputed_leader_sha,
        "leader_serialization_roundtrip_ok": recomputed_leader_sha == EXPECTED_LEADER_SET_SHA256,
        "target_symbols": targets,
        "target_set_sha256": EXPECTED_TARGET_SET_SHA256,
        "target_sha_recomputed": recomputed_target_sha,
        "target_serialization_roundtrip_ok": recomputed_target_sha == EXPECTED_TARGET_SET_SHA256,
        "target_n": len(targets),
        "leader_n": len(frozen),
        "disjoint": disjoint,
        "eligibility_rows": list(leaders.get("eligibility_rows") or []),
        "sector_liquidity": list(leaders.get("sector_liquidity") or []),
        "LEADER_SET_FROZEN_BEFORE_OUTCOME": True,
        "v1_answers": {
            "precommit_sha256": a.get("precommit_sha256"),
            "eligible_day_sha256": a.get("eligible_day_sha256"),
            "fold_boundary_sha256": a.get("fold_boundary_sha256"),
            "permutation_sha256": a.get("permutation_sha256"),
            "eligible_dev_n": a.get("eligible_dev_n"),
            "eligible_c1_n": a.get("eligible_c1_n"),
            "LEADER_LAGGARD_OUTCOMES_OPENED": a.get("LEADER_LAGGARD_OUTCOMES_OPENED"),
            "C1_outcomes_opened": a.get("C1_outcomes_opened"),
        },
        "future_return_used": False,
        "reranked": False,
    }
